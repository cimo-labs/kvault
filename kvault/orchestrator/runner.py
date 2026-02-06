"""
HeadlessOrchestrator - Main orchestrator for headless Claude Code execution.

Processes new information through the mandatory 6-step workflow:
1. RESEARCH - Query index for existing entities
2. DECIDE - Determine action (create/update/skip)
3. WRITE - Create/update entity files
4. PROPAGATE - Update ancestor summaries
5. LOG - Add journal entry
6. REBUILD - Rebuild index if entity created

Plus stochastic refactoring: Bernoulli(p=0.1) triggers cleanup.

Supports two execution modes:
1. Claude Agent SDK (preferred) - Full programmatic control with hooks
2. CLI subprocess - Falls back to `claude -p` if SDK not available
"""

import asyncio
import json
import random
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from kvault import EntityIndex, EntityResearcher, ObservabilityLogger, SimpleStorage
from kvault.orchestrator.context import (
    OrchestratorConfig,
    WorkflowContext,
    HierarchyInput,
    ActionPlan,
    PlannedAction,
)
from kvault.orchestrator.enforcer import WorkflowEnforcer
from kvault.orchestrator.state_machine import WorkflowStateMachine, WorkflowState

# Try to import Claude Agent SDK (optional dependency)
try:
    from claude_code_sdk import ClaudeCodeSDK, ClaudeCodeOptions, query

    CLAUDE_SDK_AVAILABLE = True
except ImportError:
    CLAUDE_SDK_AVAILABLE = False


class HeadlessOrchestrator:
    """Headless Claude Code orchestrator for kvault's 6-step workflow.

    Usage:
        config = OrchestratorConfig(kg_root=Path("./knowledge_graph"))
        orchestrator = HeadlessOrchestrator(config)

        # Process single item
        result = await orchestrator.process({
            "name": "Alice Smith",
            "type": "person",
            "email": "alice@anthropic.com",
            "source": "email:12345"
        })

        # Process batch
        results = await orchestrator.process_batch([...])
    """

    def __init__(self, config: OrchestratorConfig):
        """Initialize orchestrator with configuration.

        Args:
            config: OrchestratorConfig with kg_root and other settings
        """
        self.config = config
        self.kg_root = Path(config.kg_root).resolve()

        # Initialize kvault infrastructure
        kvault_dir = self.kg_root / ".kvault"
        kvault_dir.mkdir(parents=True, exist_ok=True)

        self.logger = ObservabilityLogger(kvault_dir / "logs.db")
        self.index = EntityIndex(kvault_dir / "index.db")
        self.storage = SimpleStorage(self.kg_root)
        self.researcher = EntityResearcher(self.index)

        # State machine and enforcer (created per-workflow)
        self.state_machine: Optional[WorkflowStateMachine] = None
        self.enforcer: Optional[WorkflowEnforcer] = None

        # Load meta context
        self.meta_context = self._load_meta_context()

    def _load_meta_context(self) -> str:
        """Load CLAUDE.md or similar meta context."""
        if self.config.meta_context_path and self.config.meta_context_path.exists():
            return self.config.meta_context_path.read_text()

        # Check common locations
        for path in [
            self.kg_root / "CLAUDE.md",
            self.kg_root.parent / "CLAUDE.md",
            Path.home() / ".claude" / "CLAUDE.md",
        ]:
            if path.exists():
                return path.read_text()

        return ""

    def _load_last_k_updates(self, k: int = 10) -> List:
        """Load recent k updates from logs.db for context.

        Args:
            k: Number of recent updates to load

        Returns:
            List of recent LogEntry objects
        """
        try:
            return self.logger.get_decisions(limit=k)
        except Exception:
            return []

    def _load_root_summary(self) -> str:
        """Load root _summary.md content.

        Returns:
            Root summary content, or empty string if not found.
        """
        root_summary_path = self.kg_root / "_summary.md"
        if root_summary_path.exists():
            try:
                return root_summary_path.read_text()
            except Exception:
                return ""
        return ""

    def _build_hierarchy_tree(self, max_depth: int = 3) -> str:
        """Build a tree representation of the KB hierarchy.

        Args:
            max_depth: Maximum depth to traverse

        Returns:
            Tree string showing directory structure with _summary.md presence
        """
        lines = []

        def _walk(path: Path, prefix: str = "", depth: int = 0):
            if depth > max_depth:
                return

            # Skip hidden directories and files
            if path.name.startswith("."):
                return

            # Get subdirectories only
            try:
                subdirs = sorted([
                    p for p in path.iterdir()
                    if p.is_dir() and not p.name.startswith(".")
                ])
            except PermissionError:
                return

            for i, subdir in enumerate(subdirs):
                is_last = i == len(subdirs) - 1
                connector = "└── " if is_last else "├── "
                extension = "    " if is_last else "│   "

                # Check if directory has _summary.md
                has_summary = (subdir / "_summary.md").exists()
                marker = " ✓" if has_summary else ""

                lines.append(f"{prefix}{connector}{subdir.name}/{marker}")
                _walk(subdir, prefix + extension, depth + 1)

        # Start from kg_root
        has_root_summary = (self.kg_root / "_summary.md").exists()
        lines.append(f"./{' ✓' if has_root_summary else ''}")
        _walk(self.kg_root, "", 0)

        return "\n".join(lines)

    def _build_system_prompt(self, context: WorkflowContext) -> str:
        """Build system prompt with context and workflow instructions.

        Args:
            context: WorkflowContext for this run

        Returns:
            Complete system prompt string
        """
        last_updates_summary = "\n".join(
            [
                f"- {u.data.get('entity', 'unknown')}: {u.data.get('action', 'unknown')} "
                f"({u.data.get('reasoning', '')[:50]}...)"
                for u in context.last_k_updates[:5]
            ]
        )

        if context.is_hierarchy_mode:
            return self._build_hierarchy_system_prompt(context, last_updates_summary)
        else:
            return self._build_legacy_system_prompt(context, last_updates_summary)

    def _build_hierarchy_system_prompt(
        self, context: WorkflowContext, last_updates_summary: str
    ) -> str:
        """Build system prompt for hierarchy-based processing.

        Args:
            context: WorkflowContext with raw_input
            last_updates_summary: Formatted recent updates

        Returns:
            System prompt for hierarchy reasoning
        """
        return f"""You are a knowledge graph curator. You receive raw information and reason about what changes the knowledge hierarchy needs.

## Knowledge Base Structure
```
{context.hierarchy_tree}
```

## Root Summary (Executive View)
{context.root_summary[:2000] if context.root_summary else "(No root summary)"}

## Instructions
{self.meta_context[:3000]}

## Recent Activity
{last_updates_summary if last_updates_summary else "(No recent updates)"}

## MANDATORY WORKFLOW - Execute ALL steps in order

**CRITICAL: Output structured JSON for each step for observability.**

After completing each step, output the step marker AND a JSON block:
```
[STEP_NAME] COMPLETE: <brief summary>
```json
{{ "step": "STEP_NAME", ... step-specific fields ... }}
```
```

### 1. RESEARCH
**First, study the Knowledge Base Structure tree above.** Understand:
- What categories exist (people/family, people/contacts, projects, etc.)
- Where similar entities live
- The naming conventions used (lowercase_with_underscores)

**Detect the intent of this request:**
- **ADD/UPDATE:** New information about a person/project/event
- **CORRECT:** "Actually, X is wrong" or "X should be Y" → find and fix incorrect info
- **DELETE:** "Remove X" or "X doesn't exist" or "I don't know X" → delete entity
- **RESTRUCTURE:** "Move X to Y" or "X is actually family, not contact" → move entity

Then:
- Analyze the input content for entities, events, and relationships
- **Extract identifiers:** phone numbers (normalize to +1XXXXXXXXXX), email addresses, names
- Search .kvault/index.db for existing matches using extracted identifiers
- Read relevant _summary.md files (both the potential match AND its parent category summary)
- For DELETE/MOVE: verify entity exists and note its current path
- Determine the correct category path for any new entities

**Output format (REQUIRED):**
```
[RESEARCH] COMPLETE: Found X matches for [name]
```json
{{
  "step": "RESEARCH",
  "intent": "add|update|correct|delete|restructure",
  "identifiers_extracted": {{
    "names": ["John Doe"],
    "phones": ["+14155551234"],
    "emails": ["john@example.com"]
  }},
  "index_matches": [{{
    "path": "people/contacts/john_doe",
    "name": "John Doe",
    "matched_on": "phone:+14155551234",
    "confidence": 0.99
  }}],
  "files_read": ["people/contacts/john_doe/_summary.md"],
  "analysis": "Found exact phone match for John Doe"
}}
```

### 2. DECIDE
**Choose the correct action and path based on the Knowledge Base Structure.**

**Action types:**
- **create:** New entity that doesn't exist → choose correct category path
- **update:** Add info to existing entity → use matched path from RESEARCH
  - For CORRECTIONS: read existing content, identify the incorrect info, replace it with correct info
  - User says "X is wrong" about a field → update that field, preserve everything else
- **delete:** Remove entity (user says "remove X", "I don't know X", "X doesn't exist") → requires exact match
- **move:** Restructure (user says "move X to Y", "X is family not contact") → requires source and target paths
- **skip:** Input doesn't warrant any KB changes

**Path selection:**
- People go under `people/family/`, `people/contacts/`, or `people/collaborators/`
- Projects go under `projects/`
- Use existing category patterns (look at sibling entities)

**CRITICAL: Verify phone/email matches EXACTLY before claiming entity match.**
Do NOT assume entities are the same person unless identifiers match exactly.

**Output format (REQUIRED):**
```
[DECIDE] COMPLETE: CREATE/UPDATE/DELETE/MOVE/SKIP [entity] - [reasoning]
```json
{{
  "step": "DECIDE",
  "actions": [
    {{
      "action_type": "create|update|delete|move|skip",
      "path": "category/subcategory/entity_name",
      "target_path": "new/path (for move only)",
      "reasoning": "why this action and why this path",
      "confidence": 0.95,
      "identifier_verification": {{"input_phone": "+1...", "matched_phone": "+1...", "exact_match": true}},
      "content": {{"summary": "...", "meta": {{...}}}}
    }}
  ],
  "overall_reasoning": "High-level explanation including path rationale"
}}
```

**NEVER delete or move entities without exact identifier match or explicit user confirmation.**

### 3. EXECUTE
For each action in the plan, execute the appropriate operation:

**For CREATE actions:**
- Create directory: `mkdir -p <kg_root>/<path>`
- Write `_summary.md` with YAML frontmatter (NO separate _meta.json files)

**Entity File Format** (`_summary.md` with YAML frontmatter):
```markdown
---
created: YYYY-MM-DD
updated: YYYY-MM-DD
source: {{source_id}}
aliases: [names, emails, phones for matching]
phone: '+1XXXXXXXXXX' (if available, MUST be quoted)
email: user@example.com (if available)
relationship_type: family|friend|colleague|contact
context: how you know them
---

# Entity Name

**Relationship:** {{relationship_type}}
**Context:** {{context}}

## Background
{{content}}

## Interactions
- YYYY-MM-DD: {{event}}

## Follow-ups
- [ ] {{action items}}
```

**For UPDATE actions:**
1. Read existing `_summary.md` and parse existing frontmatter
2. Preserve existing frontmatter fields (don't overwrite unless correcting)
3. Update `updated` field to today's date
4. Merge new aliases (combine, don't replace)
5. Append new interactions to Interactions section

**For CORRECTION updates:**
1. Read existing content carefully
2. Identify the specific incorrect information
3. Replace ONLY the incorrect parts, preserve everything else
4. Add a note in Interactions: "YYYY-MM-DD: Corrected [field] from [old] to [new]"

**For DELETE actions:**
1. Verify the entity exists at the specified path
2. Remove the entire entity directory: `rm -rf <kg_root>/<path>`
3. Note: PROPAGATE step will clean up references in ancestor summaries

**For MOVE actions:**
1. Verify source entity exists
2. Create target directory if needed: `mkdir -p <kg_root>/<target_path>`
3. Move all files: `mv <kg_root>/<source_path>/* <kg_root>/<target_path>/`
4. Remove empty source directory: `rmdir <kg_root>/<source_path>`
5. Update frontmatter with new path context if category changed
6. Note: PROPAGATE step will update both old and new ancestor summaries

**Output format (REQUIRED):**
```
[EXECUTE] COMPLETE: Executed N actions
```json
{{
  "step": "EXECUTE",
  "actions_completed": [
    {{"action": "create", "path": "people/john_doe", "success": true}},
    {{"action": "delete", "path": "people/test_user", "success": true}},
    {{"action": "move", "source": "people/contacts/alice", "target": "people/family/alice", "success": true}}
  ]
}}
```

### 4. PROPAGATE
**Walk UP the tree from each affected path to the root.**

**For CREATE/UPDATE actions:**
- Walk from entity to root, updating each ancestor summary
- Example: `people/contacts/john_doe/` → update `people/contacts/_summary.md` → `people/_summary.md` → `_summary.md`

**For DELETE actions:**
- Walk from deleted entity's parent to root
- REMOVE references to the deleted entity from all ancestor summaries
- Update counts and lists to reflect removal

**For MOVE actions:**
- Walk BOTH the old path's ancestors AND the new path's ancestors
- REMOVE entity from old location summaries
- ADD entity to new location summaries

For each ancestor:
- Read the current _summary.md
- Decide if it needs updating to reflect the change
- Write the updated summary (semantic synthesis, not just appending)

**Output format (REQUIRED):**
```
[PROPAGATE] COMPLETE: Updated N ancestor summaries
```json
{{
  "step": "PROPAGATE",
  "paths_updated": ["people/contacts/_summary.md", "people/_summary.md", "_summary.md"],
  "changes": ["Added John Doe to contacts list", "Updated people count", "Updated root recent activity"]
}}
```

### 5. LOG
Add entry to journal/YYYY-MM/log.md covering all changes.

**Log all action types:**
- CREATE: "Created new entity at [path]"
- UPDATE: "Updated [path] with new information"
- DELETE: "Removed entity at [path] - [reason]"
- MOVE: "Moved [source] to [target] - [reason]"

**Output format (REQUIRED):**
```
[LOG] COMPLETE: Added journal entry
```json
{{
  "step": "LOG",
  "journal_path": "journal/2026-01/log.md",
  "entry_summary": "Brief description of what was logged"
}}
```

### 6. REBUILD
Rebuild the index if the entity set changed (creates OR deletes).

**Rebuild required when:**
- Any new entities were created
- Any entities were deleted
- Any entities were moved

**Output format (REQUIRED):**
```
[REBUILD] COMPLETE: Index rebuilt with N entities
```json
{{
  "step": "REBUILD",
  "rebuilt": true,
  "entity_count": 27
}}
```

## Knowledge Graph Root
{self.kg_root}

## Input to Process
```
{self._truncate_content(context.raw_input.content) if context.raw_input else ""}
```
Source: {context.raw_input.source if context.raw_input else "unknown"}

---

**IMPORTANT: You MUST output "[STEP] COMPLETE:" marker after EACH step, not just at the end.**

Begin with Step 1 (RESEARCH). Output "[RESEARCH] COMPLETE: ..." when done, then proceed to Step 2.
"""

    def _build_legacy_system_prompt(
        self, context: WorkflowContext, last_updates_summary: str
    ) -> str:
        """Build system prompt for legacy entity-centric processing.

        Args:
            context: WorkflowContext with new_info
            last_updates_summary: Formatted recent updates

        Returns:
            Legacy system prompt
        """
        return f"""You are a knowledge graph curator following a mandatory 6-step workflow.

## Meta Context
{self.meta_context[:3000]}

## Recent Updates (last {len(context.last_k_updates)} entries)
{last_updates_summary if last_updates_summary else "(No recent updates)"}

## MANDATORY WORKFLOW - Execute ALL steps in order

You MUST complete each step before proceeding to the next.
After completing each step, output: "[STEP_NAME] COMPLETE: [summary]"

### 1. RESEARCH
- Query .kvault/index.db for existing entities matching the input
- Search by name, aliases, and email domain
- Report matches found (or "no matches")

### 2. DECIDE
- Based on research, decide action:
  - CREATE: No existing match, create new entity
  - UPDATE: High-confidence match exists, update it
  - SKIP: Information not significant enough to add
  - MERGE: Multiple duplicates found, merge them
- State your decision with confidence and reasoning

### 3. WRITE
- Create or update entity files using YAML frontmatter (NO separate _meta.json)
- Entity file format: `_summary.md` with frontmatter containing:
  - created, updated, source, aliases (required)
  - phone, email, relationship_type, context (optional)
- For updates: preserve existing frontmatter, update `updated` field, merge aliases

### 4. PROPAGATE
- Update ALL ancestor _summary.md files
- This includes the parent category (e.g., people/_summary.md)
- And the root _summary.md if appropriate

### 5. LOG
- Add entry to journal/YYYY-MM/log.md
- Format: date, action taken, cross-reference to entity

### 6. REBUILD
- If new entity was created, run: python scripts/rebuild_index.py
- Report new index count

## Knowledge Graph Root
{self.kg_root}

## Current Input to Process
```json
{json.dumps(context.new_info, indent=2) if context.new_info else "{}"}
```

Begin with Step 1 (RESEARCH).
"""

    def _build_workflow_prompt(self, context: WorkflowContext) -> str:
        """Build the prompt that initiates the workflow.

        Args:
            context: WorkflowContext for this run

        Returns:
            Workflow initiation prompt
        """
        if context.is_hierarchy_mode:
            return f"""Process the following raw information into the knowledge hierarchy.

## Raw Input
{self._truncate_content(context.raw_input.content) if context.raw_input else ""}

Source: {context.raw_input.source if context.raw_input else "unknown"}

## Execute Mandatory Workflow

Start with Step 1 (RESEARCH):
- Analyze the input for entities, events, and relationships
- Search the index for related existing entities
- Read relevant _summary.md files

Then proceed through all remaining steps.
Output "[STEP] COMPLETE: summary" after each step.
For EXECUTE, output "ACTION N COMPLETE: [path]" for each action.
"""
        else:
            return f"""Process the following information into the knowledge graph.

## Information to Process
- Name: {context.new_info.get('name', 'Unknown') if context.new_info else 'Unknown'}
- Type: {context.new_info.get('type', 'unknown') if context.new_info else 'unknown'}
- Email: {context.new_info.get('email', 'N/A') if context.new_info else 'N/A'}
- Source: {context.new_info.get('source', 'manual') if context.new_info else 'manual'}
- Content: {context.new_info.get('content', 'No additional content') if context.new_info else 'No additional content'}

## Execute Mandatory Workflow

Start with Step 1 (RESEARCH):
- Search the index for existing entities matching this name or email
- Query: sqlite3 {self.kg_root}/.kvault/index.db "SELECT * FROM entities WHERE name LIKE '%{context.new_info.get('name', '') if context.new_info else ''}%' OR aliases LIKE '%{context.new_info.get('name', '') if context.new_info else ''}%'"

Then proceed through all remaining steps in order.
Output "[STEP] COMPLETE: summary" after each step.
"""

    def _truncate_content(self, content: str, max_chars: Optional[int] = None) -> str:
        """Truncate content to prevent context bloat.

        Truncates at a natural boundary (newline) when possible to avoid
        cutting mid-sentence or mid-line.

        Args:
            content: Raw content to truncate
            max_chars: Maximum characters (uses config default if not specified)

        Returns:
            Truncated content with indicator if truncated
        """
        if max_chars is None:
            max_chars = self.config.max_content_chars

        if len(content) <= max_chars:
            return content

        truncated = content[:max_chars]

        # Try to truncate at a natural boundary (newline)
        last_newline = truncated.rfind('\n')
        if last_newline > max_chars * 0.8:  # Only if we keep at least 80%
            truncated = truncated[:last_newline]

        # Handle multi-byte unicode: ensure we don't cut mid-character
        try:
            truncated.encode('utf-8')
        except UnicodeEncodeError:
            # Trim one char at a time until valid
            while truncated:
                try:
                    truncated[:-1].encode('utf-8')
                    truncated = truncated[:-1]
                    break
                except UnicodeEncodeError:
                    truncated = truncated[:-1]

        chars_removed = len(content) - len(truncated)
        return f"{truncated}\n\n[TRUNCATED: {chars_removed:,} chars removed, {len(content):,} total]"

    def _build_refactor_prompt(self, context: WorkflowContext) -> str:
        """Build prompt for stochastic refactoring step.

        Args:
            context: WorkflowContext for this run

        Returns:
            Refactor prompt
        """
        return f"""## Refactor Opportunity Check

A refactor check was triggered (probability: {context.refactor_probability}).

Review the knowledge graph for cleanup opportunities:

1. Look for similar entities that could be merged
2. Check for outdated information that should be updated
3. Identify missing cross-references
4. Find inconsistent naming patterns

Focus on entities related to: {context.new_info.get('name', 'recent additions') if context.new_info else 'recent additions'}

List opportunities found (if any) and execute the most impactful one.
Report: "REFACTOR COMPLETE: [actions taken]" or "REFACTOR COMPLETE: No opportunities found"
"""

    async def process(self, new_info: Dict[str, Any]) -> Dict[str, Any]:
        """Process new information through the 6-step workflow (legacy entity-centric mode).

        Args:
            new_info: Dictionary containing information to add to the knowledge graph
                Expected keys: name, type, email (optional), source, content (optional)

        Returns:
            Dictionary with processing results including:
            - session_id: Observability session ID
            - decision: The action taken (create/update/skip/merge)
            - entity_path: Path to the affected entity
            - propagated_paths: Paths of updated ancestors
            - refactored: Whether refactoring was performed
        """
        # Initialize context (legacy mode)
        context = WorkflowContext(
            new_info=new_info,
            meta_context=self.meta_context,
            last_k_updates=self._load_last_k_updates(self.config.last_k_updates),
            refactor_probability=self.config.refactor_probability,
        )

        return await self._execute_workflow(context, new_info)

    async def ingest(self, content: str, source: str, hints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Process raw content through hierarchy-based workflow.

        This is the new API that accepts unstructured input and lets the agent
        reason about what changes the hierarchy needs.

        Args:
            content: Raw content to process (any format)
            source: Source identifier (e.g., 'imessage:2024-01-15', 'manual')
            hints: Optional extraction hints

        Returns:
            Dictionary with processing results including:
            - session_id: Observability session ID
            - action_plan: The plan produced by DECIDE
            - executed_actions: Actions that were executed
            - created_paths: Paths where new entities were created
            - updated_paths: Paths where entities were updated
            - propagated_paths: Paths of updated ancestors
        """
        # Create hierarchy input
        raw_input = HierarchyInput(content=content, source=source, hints=hints)

        # Initialize context (hierarchy mode)
        context = WorkflowContext(
            raw_input=raw_input,
            meta_context=self.meta_context,
            root_summary=self._load_root_summary(),
            hierarchy_tree=self._build_hierarchy_tree(),
            last_k_updates=self._load_last_k_updates(self.config.last_k_updates),
            refactor_probability=self.config.refactor_probability,
        )

        return await self._execute_workflow(context, {"content": content, "source": source})

    async def _execute_workflow(
        self, context: WorkflowContext, input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute the workflow with the given context.

        Args:
            context: Initialized WorkflowContext
            input_data: Input data for logging

        Returns:
            Processing results dictionary
        """

        # Initialize state machine and enforcer
        self.state_machine = WorkflowStateMachine(context)
        self.enforcer = WorkflowEnforcer(
            self.state_machine,
            logger=self.logger,
            kg_root=str(self.kg_root),
        )

        # Start new observability session
        session_id = self.logger.new_session()
        context.session_id = session_id
        self.logger.log_input([input_data], source="orchestrator")

        try:
            # Execute workflow
            if CLAUDE_SDK_AVAILABLE:
                await self._execute_with_sdk(context)
            else:
                await self._execute_with_cli(context)

            # Stochastic refactor check
            context.should_refactor = random.random() < context.refactor_probability

            if context.should_refactor:
                await self._execute_refactor(context)

        except Exception as e:
            import traceback as tb

            entity_name = (
                input_data.get("name")
                if not context.is_hierarchy_mode
                else "hierarchy_ingest"
            )
            # Get full traceback string for debugging
            full_traceback = tb.format_exc()

            self.logger.log_error(
                error_type=type(e).__name__,
                entity=entity_name,
                details={
                    "message": str(e),
                    "traceback": full_traceback,
                    "session_id": context.session_id,
                    "is_hierarchy_mode": context.is_hierarchy_mode,
                    "input_preview": str(input_data)[:500] if input_data else None,
                },
                resolution="failed",
            )
            self.state_machine.force_transition(WorkflowState.ERROR, str(e))
            raise

        return context.to_dict()

    async def _execute_with_sdk(self, context: WorkflowContext) -> None:
        """Execute workflow using Claude Agent SDK.

        Args:
            context: WorkflowContext for this run
        """
        # This would use the full SDK with hooks
        # For now, fall back to CLI since SDK API may vary
        await self._execute_with_cli(context)

    def _find_claude_binary(self) -> str:
        """Find the correct claude binary path.

        Prefers ~/.claude/local/claude (newer) over /usr/local/bin/claude (older).

        Returns:
            Path to claude binary
        """
        import os
        import shutil

        # Check for newer claude in user's home directory first
        home_claude = Path.home() / ".claude" / "local" / "claude"
        if home_claude.exists():
            return str(home_claude)

        # Fall back to PATH lookup
        claude_path = shutil.which("claude")
        if claude_path:
            return claude_path

        # Default to just "claude" and hope for the best
        return "claude"

    async def _execute_with_cli(self, context: WorkflowContext) -> None:
        """Execute workflow using Claude CLI subprocess with retry logic.

        Implements exponential backoff retry for timeouts and subprocess errors.

        Args:
            context: WorkflowContext for this run
        """
        import traceback as tb

        # Build the full prompt
        system_prompt = self._build_system_prompt(context)
        workflow_prompt = self._build_workflow_prompt(context)

        full_prompt = f"{system_prompt}\n\n---\n\n{workflow_prompt}"

        # Find the correct claude binary
        claude_bin = self._find_claude_binary()

        # Build command with permission flags for headless execution
        cmd = [
            claude_bin,
            "-p",
            full_prompt,
            "--output-format",
            "text",
        ]

        # Add permission flags for headless file writes
        if self.config.dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        elif self.config.permission_mode:
            cmd.extend(["--permission-mode", self.config.permission_mode])

        # Verbose mode: show progress
        if self.config.verbose:
            content_preview = context.raw_input.content[:50] if context.raw_input else "entity"
            print(f"[ORCHESTRATOR] Starting workflow for: {content_preview}...")
            print(f"[ORCHESTRATOR] Timeout: {self.config.timeout_seconds}s, Max retries: {self.config.max_retries}")

        # Retry loop with exponential backoff
        output = ""
        result = None

        for attempt in range(self.config.max_retries):
            try:
                if attempt > 0 and self.config.verbose:
                    print(f"[ORCHESTRATOR] Retry attempt {attempt + 1}/{self.config.max_retries}")

                # Run claude -p with permissions
                result = subprocess.run(
                    cmd,
                    cwd=str(self.kg_root),
                    capture_output=True,
                    text=True,
                    timeout=self.config.timeout_seconds,
                )

                output = result.stdout + result.stderr

                # Success - break out of retry loop
                break

            except subprocess.TimeoutExpired as e:
                # Log timeout with details
                if self.logger:
                    self.logger.log(
                        "cli_timeout",
                        {
                            "type": "timeout",
                            "attempt": attempt + 1,
                            "max_retries": self.config.max_retries,
                            "timeout_seconds": self.config.timeout_seconds,
                            "partial_stdout": e.stdout[:2000] if e.stdout else None,
                            "partial_stderr": e.stderr[:2000] if e.stderr else None,
                        },
                    )
                if attempt < self.config.max_retries - 1:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                    if self.config.verbose:
                        print(f"[ORCHESTRATOR] Timeout after {self.config.timeout_seconds}s, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise

            except subprocess.SubprocessError as e:
                # Log subprocess error with traceback
                if self.logger:
                    self.logger.log(
                        "cli_subprocess_error",
                        {
                            "type": "subprocess_error",
                            "attempt": attempt + 1,
                            "max_retries": self.config.max_retries,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "traceback": tb.format_exc(),
                        },
                    )
                if attempt < self.config.max_retries - 1:
                    wait_time = 2 ** attempt
                    if self.config.verbose:
                        print(f"[ORCHESTRATOR] Subprocess error: {e}, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise

        # Verbose mode: show completion
        if self.config.verbose:
            return_code = result.returncode if result else -1
            print(f"[ORCHESTRATOR] Claude finished (exit code: {return_code})")
            print(f"[ORCHESTRATOR] Output length: {len(output)} chars")

        # Log the FULL raw output (no truncation) for debugging - even on success
        if self.logger:
            self.logger.log(
                "cli_raw",
                {
                    "type": "cli_output",
                    "output": output,  # Full output, no truncation
                    "output_length": len(output),
                    "return_code": result.returncode if result else -1,
                    "attempts": attempt + 1,
                    "prompt_length": len(full_prompt),
                },
            )

        # Parse step completions from output (also logs each step)
        self._parse_step_completions(output, context)

    def _extract_step_json(self, text: str, step_name: str) -> Optional[Dict[str, Any]]:
        """Extract JSON block following a step completion marker.

        Args:
            text: Text to search (usually the details after COMPLETE:)
            step_name: Name of the step (for logging)

        Returns:
            Parsed JSON dict or None if not found/invalid
        """
        import re

        # Look for JSON block in the text
        json_pattern = r'```json\s*(\{.*?\})\s*```'
        json_match = re.search(json_pattern, text, re.DOTALL)

        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError as e:
                if self.logger:
                    self.logger.log(
                        f"step_{step_name.lower()}_json_error",
                        {"step": step_name, "error": str(e), "raw": json_match.group(1)[:500]},
                    )
                return None
        return None

    def _parse_step_completions(self, output: str, context: WorkflowContext) -> None:
        """Parse step completion markers from Claude output.

        Looks for patterns like:
        - "RESEARCH COMPLETE: Found 2 matches"
        - "[DECIDE] COMPLETE: CREATE - no existing match"

        Extracts structured JSON from each step for observability.

        Args:
            output: Raw output from Claude
            context: WorkflowContext to update
        """
        import re

        # Pattern: [STEP] COMPLETE: summary or STEP COMPLETE: summary
        # Capture everything until the next step marker
        pattern = r"\[?(\w+)\]?\s*COMPLETE:\s*(.+?)(?=\n\[?\w+\]?\s*COMPLETE:|\Z)"
        matches = re.findall(pattern, output, re.IGNORECASE | re.DOTALL)

        for step_name, details in matches:
            step_name = step_name.upper()
            details = details.strip()

            # Verbose mode: print step completions as they're parsed
            if self.config.verbose:
                # Extract first line of details for summary
                summary_line = details.split('\n')[0][:80]
                print(f"[{step_name}] COMPLETE: {summary_line}")

            # Try to extract structured JSON from this step's output
            step_json = self._extract_step_json(details, step_name)

            # Log each step with both raw details and parsed JSON
            if self.logger:
                self.logger.log(
                    f"step_{step_name.lower()}",
                    {
                        "step": step_name,
                        "details": details[:2000],
                        "details_length": len(details),
                        "structured_output": step_json,  # Parsed JSON if available
                    },
                )

            # Extract relevant info based on step
            if step_name == "RESEARCH":
                research_data = step_json or {"matches": []}
                self.state_machine.store_output("RESEARCH", research_data)
                self.state_machine.transition("RESEARCH")

            elif step_name == "DECIDE":
                if context.is_hierarchy_mode:
                    # Use step_json already extracted from details, or try full output as fallback
                    plan_dict = step_json
                    if not plan_dict:
                        # Fallback: search full output for JSON
                        json_pattern = r'```json\s*(\{.*?\})\s*```'
                        json_match = re.search(json_pattern, output, re.DOTALL)
                        if json_match:
                            try:
                                plan_dict = json.loads(json_match.group(1))
                            except json.JSONDecodeError:
                                plan_dict = None

                    if plan_dict:
                        try:
                            # Handle both "actions" list and direct action dict
                            actions_list = plan_dict.get("actions", [])
                            if not actions_list and plan_dict.get("action_type"):
                                # Single action as direct dict
                                actions_list = [plan_dict]

                            context.action_plan = ActionPlan(
                                actions=[
                                    PlannedAction(
                                        action_type=a.get("action_type", a.get("action", "skip")),
                                        path=a.get("path", ""),
                                        reasoning=a.get("reasoning", ""),
                                        confidence=float(a.get("confidence", 1.0)),
                                    )
                                    for a in actions_list
                                ],
                                overall_reasoning=plan_dict.get("overall_reasoning", plan_dict.get("reasoning", "")),
                            )
                        except (TypeError, ValueError) as e:
                            context.action_plan = ActionPlan(
                                actions=[], overall_reasoning=f"Parse error: {e}"
                            )
                    else:
                        context.action_plan = ActionPlan(
                            actions=[], overall_reasoning="No JSON found in DECIDE output"
                        )
                    self.state_machine.store_output(
                        "DECIDE", {"action_plan": context.action_plan}
                    )
                    self.state_machine.transition("DECIDE")
                else:
                    # Legacy mode: keyword-based decision
                    decision = None
                    for action in ["create", "update", "skip", "merge"]:
                        if action.lower() in details.lower():
                            decision = action
                            break
                    if decision:
                        self.state_machine.store_output(
                            "DECIDE",
                            {"decision": decision, "reasoning": details},
                        )
                        self.state_machine.transition("DECIDE")

            elif step_name == "WRITE":
                # Extract entity path
                path_match = re.search(r"(?:path|wrote|created).*?([a-z_]+/[a-z_]+)", details, re.I)
                entity_path = path_match.group(1) if path_match else None
                if entity_path or context.decision in ("create", "update"):
                    self.state_machine.store_output(
                        "WRITE",
                        {"entity_path": entity_path or context.target_path},
                    )
                    self.state_machine.transition("WRITE")

            elif step_name == "PROPAGATE":
                self.state_machine.store_output("PROPAGATE", {"paths": []})
                self.state_machine.transition("PROPAGATE")

            elif step_name == "LOG":
                self.state_machine.store_output("LOG", {})
                self.state_machine.transition("LOG")

            elif step_name == "REBUILD":
                count_match = re.search(r"(\d+)", details)
                count = int(count_match.group(1)) if count_match else None
                self.state_machine.store_output("REBUILD", {"count": count})
                self.state_machine.transition("REBUILD")

        # Fallback: Parse created/updated paths from summary text
        # Claude often outputs a summary instead of per-step markers
        if context.is_hierarchy_mode and not context.created_paths:
            # Match various patterns Claude might use:
            # - **Entity created:** `people/contacts/foo/_summary.md`
            # - **Entity path:** `people/contacts/foo/`
            # - **Path:** `people/contacts/foo/`
            # - CREATE new contact at `people/contacts/foo/`
            # - Created `people/contacts/foo`
            # - mkdir -p projects/directional_surrogacy
            # - "path": "projects/foo"
            # - already exists at `projects/foo/`
            patterns = [
                r'CREATE[:\s].*?`([^`]+)`',  # Most common: CREATE ... `path`
                r'[Ee]ntity (?:created|path).*?`([^`]+)`',
                r'\*\*Path:\*\*\s*`([^`]+)`',
                r'[Cc]reated[:\s]+`([^`]+)`',
                r'[Cc]reated.*?entity.*?`([^`]+)`',
                r'mkdir -p\s+[^\s]*?([a-z_]+/[a-z_]+(?:/[a-z_]+)?)',  # mkdir commands
                r'"path":\s*"([a-z_]+/[a-z_]+(?:/[a-z_]+)?)"',  # JSON path field
                r'(?:exists|created).*?at\s+`([^`]+)`',  # "exists at" or "created at"
                r'new entity at\s+`([^`]+)`',  # "new entity at"
            ]
            created = []
            for pattern in patterns:
                matches = re.findall(pattern, output)
                created.extend(matches)
            # Normalize: remove /_summary.md suffix and trailing slashes
            created = [re.sub(r'/_summary\.md$', '', p).rstrip('/') for p in created]
            # Filter to valid-looking paths (category/entity or category/subcategory/entity)
            created = [p for p in created if re.match(r'^[a-z_]+/[a-z_]+(?:/[a-z_]+)?$', p)]
            context.created_paths = list(set(created))

        if context.is_hierarchy_mode and not context.updated_paths:
            # Match various patterns for updates
            patterns = [
                r'[Uu]pdated[:\s]+`([^`]+)`',
                r'\*\*Updated:\*\*\s*`([^`]+)`',
                r'UPDATE action.*?`([a-z_]+/[a-z_]+(?:/[a-z_]+)?)`',
            ]
            updated = []
            for pattern in patterns:
                matches = re.findall(pattern, output)
                updated.extend(matches)
            # Filter out paths that were created and normalize
            updated = [re.sub(r'/_summary\.md$', '', p) for p in updated]
            updated = [p for p in updated if p not in context.created_paths]
            context.updated_paths = list(set(updated))

        if context.is_hierarchy_mode and not context.deleted_paths:
            # Match various patterns for deletions
            # Claude outputs: "Removed directory `path`", "Deleted `path`", etc.
            patterns = [
                r'DELETE[:\s].*?`([^`]+)`',
                r'[Dd]eleted.*?`([^`]+)`',
                r'[Rr]emoved.*?`([^`]+)`',
                r'\*\*Deleted:\*\*\s*`([^`]+)`',
            ]
            deleted = []
            for pattern in patterns:
                matches = re.findall(pattern, output)
                deleted.extend(matches)
            # Normalize: remove /_summary.md suffix and trailing slashes
            deleted = [re.sub(r'/_summary\.md$', '', p).rstrip('/') for p in deleted]
            # Filter out summary files (we want entity paths, not summary paths)
            deleted = [p for p in deleted if not p.endswith('_summary.md')]
            context.deleted_paths = list(set(deleted))

        if context.is_hierarchy_mode and not context.moved_paths:
            # Match move patterns:
            # - "Moved entity X from `source` to `target`"
            # - "Moved `source` to `target`"
            # - "MOVE ... `source` → `target`"
            patterns = [
                r'[Mm]oved.*?from\s+`([^`]+)`\s+to\s+`([^`]+)`',
                r'[Mm]oved\s+`([^`]+)`\s+to\s+`([^`]+)`',
                r'MOVE[:\s].*?`([^`]+)`.*?(?:to|→)\s*`([^`]+)`',
            ]
            for pattern in patterns:
                matches = re.findall(pattern, output)
                for source, target in matches:
                    # Normalize: remove /_summary.md suffix and trailing slashes
                    source = re.sub(r'/_summary\.md$', '', source).rstrip('/')
                    target = re.sub(r'/_summary\.md$', '', target).rstrip('/')
                    context.moved_paths.append({"source": source, "target": target})

        if context.is_hierarchy_mode and not context.propagated_paths:
            # Match propagation mentions
            patterns = [
                r'[Pp]ropagated.*?(\d+)\s*ancestor',
                r'[Uu]pdated\s+(\d+)\s*(?:ancestor|summar)',
            ]
            for pattern in patterns:
                match = re.search(pattern, output)
                if match:
                    context.propagated_paths = [f"{match.group(1)} ancestors updated"]
                    break

        # Check if workflow completed
        if self.state_machine.current_state in (
            WorkflowState.REBUILD,
            WorkflowState.LOG,
        ):
            # Sample refactor probability
            context.should_refactor = random.random() < context.refactor_probability
            self.state_machine.store_output(
                "REFACTOR_CHECK",
                {"should_refactor": context.should_refactor},
            )
            self.state_machine.transition("REFACTOR_CHECK")

            if not context.should_refactor:
                self.state_machine.transition("COMPLETE")

    async def _execute_refactor(self, context: WorkflowContext) -> None:
        """Execute stochastic refactoring step with retry logic.

        Args:
            context: WorkflowContext for this run
        """
        import traceback as tb

        refactor_prompt = self._build_refactor_prompt(context)

        # Find the correct claude binary
        claude_bin = self._find_claude_binary()

        # Build command with permission flags
        cmd = [
            claude_bin,
            "-p",
            refactor_prompt,
            "--output-format",
            "text",
        ]

        if self.config.dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        elif self.config.permission_mode:
            cmd.extend(["--permission-mode", self.config.permission_mode])

        # Retry loop with exponential backoff
        output = ""
        result = None

        for attempt in range(self.config.max_retries):
            try:
                if attempt > 0 and self.config.verbose:
                    print(f"[ORCHESTRATOR] Refactor retry attempt {attempt + 1}/{self.config.max_retries}")

                result = subprocess.run(
                    cmd,
                    cwd=str(self.kg_root),
                    capture_output=True,
                    text=True,
                    timeout=self.config.timeout_seconds,
                )

                output = result.stdout + result.stderr
                break  # Success

            except subprocess.TimeoutExpired as e:
                if self.logger:
                    self.logger.log(
                        "refactor_timeout",
                        {
                            "type": "timeout",
                            "attempt": attempt + 1,
                            "max_retries": self.config.max_retries,
                            "timeout_seconds": self.config.timeout_seconds,
                        },
                    )
                if attempt < self.config.max_retries - 1:
                    wait_time = 2 ** attempt
                    if self.config.verbose:
                        print(f"[ORCHESTRATOR] Refactor timeout, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    # Log but don't fail the whole workflow for refactor timeout
                    context.refactor_results = [{"error": f"Timeout after {self.config.max_retries} attempts"}]
                    self.state_machine.transition("COMPLETE")
                    return

            except subprocess.SubprocessError as e:
                if self.logger:
                    self.logger.log(
                        "refactor_subprocess_error",
                        {
                            "type": "subprocess_error",
                            "attempt": attempt + 1,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "traceback": tb.format_exc(),
                        },
                    )
                if attempt < self.config.max_retries - 1:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                else:
                    context.refactor_results = [{"error": str(e)}]
                    self.state_machine.transition("COMPLETE")
                    return

        # Log refactor output
        if self.logger:
            self.logger.log(
                "refactor_raw",
                {
                    "type": "refactor_output",
                    "output": output,
                    "output_length": len(output),
                    "return_code": result.returncode if result else -1,
                },
            )

        context.refactor_results = [{"output": output[:2000]}]

        # Parse refactor completion
        if "REFACTOR COMPLETE" in output.upper():
            self.state_machine.store_output("EXEC_REFACTOR", {"results": context.refactor_results})
            self.state_machine.transition("EXEC_REFACTOR")

        self.state_machine.transition("COMPLETE")

    async def process_batch(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process multiple items through the workflow.

        Args:
            items: List of new_info dictionaries to process

        Returns:
            List of result dictionaries
        """
        results = []

        for item in items:
            try:
                result = await self.process(item)
                results.append(result)
            except Exception as e:
                results.append(
                    {
                        "error": str(e),
                        "item": item,
                        "session_id": self.logger.session_id if self.logger else None,
                    }
                )

        return results

    def process_sync(self, new_info: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous wrapper for process().

        Args:
            new_info: Information to process

        Returns:
            Processing result dictionary
        """
        return asyncio.run(self.process(new_info))

    def process_batch_sync(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Synchronous wrapper for process_batch().

        Args:
            items: List of items to process

        Returns:
            List of result dictionaries
        """
        return asyncio.run(self.process_batch(items))


def main():
    """CLI entry point for direct execution."""
    import argparse

    parser = argparse.ArgumentParser(description="kvault headless orchestrator")
    parser.add_argument("--kg-root", type=Path, required=True, help="Knowledge graph root")
    parser.add_argument("--name", required=True, help="Entity name")
    parser.add_argument("--type", dest="entity_type", default="person", help="Entity type")
    parser.add_argument("--email", default=None, help="Email address")
    parser.add_argument("--source", default="manual", help="Source identifier")
    parser.add_argument("--refactor-prob", type=float, default=0.1, help="Refactor probability")
    parser.add_argument("--content", default="", help="Additional content")

    args = parser.parse_args()

    config = OrchestratorConfig(
        kg_root=args.kg_root,
        refactor_probability=args.refactor_prob,
    )

    orchestrator = HeadlessOrchestrator(config)

    result = orchestrator.process_sync(
        {
            "name": args.name,
            "type": args.entity_type,
            "email": args.email,
            "source": args.source,
            "content": args.content,
        }
    )

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
