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

from kgraph import EntityIndex, EntityResearcher, ObservabilityLogger, SimpleStorage
from kgraph.orchestrator.context import OrchestratorConfig, WorkflowContext
from kgraph.orchestrator.enforcer import WorkflowEnforcer
from kgraph.orchestrator.state_machine import WorkflowStateMachine, WorkflowState

# Try to import Claude Agent SDK (optional dependency)
try:
    from claude_code_sdk import ClaudeCodeSDK, ClaudeCodeOptions, query

    CLAUDE_SDK_AVAILABLE = True
except ImportError:
    CLAUDE_SDK_AVAILABLE = False


class HeadlessOrchestrator:
    """Headless Claude Code orchestrator for kgraph's 6-step workflow.

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

        # Initialize kgraph infrastructure
        kgraph_dir = self.kg_root / ".kgraph"
        kgraph_dir.mkdir(parents=True, exist_ok=True)

        self.logger = ObservabilityLogger(kgraph_dir / "logs.db")
        self.index = EntityIndex(kgraph_dir / "index.db")
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

        return f"""You are a knowledge graph curator following a mandatory 6-step workflow.

## Meta Context
{self.meta_context[:3000]}

## Recent Updates (last {len(context.last_k_updates)} entries)
{last_updates_summary if last_updates_summary else "(No recent updates)"}

## MANDATORY WORKFLOW - Execute ALL steps in order

You MUST complete each step before proceeding to the next.
After completing each step, output: "[STEP_NAME] COMPLETE: [summary]"

### 1. RESEARCH
- Query .kgraph/index.db for existing entities matching the input
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
- Create or update entity files:
  - _meta.json: created, last_updated, sources, aliases
  - _summary.md: Freeform markdown content
- Follow the entity format conventions

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
{json.dumps(context.new_info, indent=2)}
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
        return f"""Process the following information into the knowledge graph.

## Information to Process
- Name: {context.new_info.get('name', 'Unknown')}
- Type: {context.new_info.get('type', 'unknown')}
- Email: {context.new_info.get('email', 'N/A')}
- Source: {context.new_info.get('source', 'manual')}
- Content: {context.new_info.get('content', 'No additional content')}

## Execute Mandatory Workflow

Start with Step 1 (RESEARCH):
- Search the index for existing entities matching this name or email
- Query: sqlite3 {self.kg_root}/.kgraph/index.db "SELECT * FROM entities WHERE name LIKE '%{context.new_info.get('name', '')}%' OR aliases LIKE '%{context.new_info.get('name', '')}%'"

Then proceed through all remaining steps in order.
Output "[STEP] COMPLETE: summary" after each step.
"""

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

Focus on entities related to: {context.new_info.get('name', 'recent additions')}

List opportunities found (if any) and execute the most impactful one.
Report: "REFACTOR COMPLETE: [actions taken]" or "REFACTOR COMPLETE: No opportunities found"
"""

    async def process(self, new_info: Dict[str, Any]) -> Dict[str, Any]:
        """Process new information through the 6-step workflow.

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
        # Initialize context
        context = WorkflowContext(
            new_info=new_info,
            meta_context=self.meta_context,
            last_k_updates=self._load_last_k_updates(self.config.last_k_updates),
            refactor_probability=self.config.refactor_probability,
        )

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
        self.logger.log_input([new_info], source="orchestrator")

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
            self.logger.log_error(
                error_type=type(e).__name__,
                entity=new_info.get("name"),
                details={"message": str(e), "traceback": str(e.__traceback__)},
                resolution="failed",
            )
            self.state_machine.force_transition(WorkflowState.ERROR, str(e))
            raise

        return {
            "session_id": session_id,
            "decision": context.decision,
            "entity_path": context.entity_path,
            "entity_created": context.entity_created,
            "propagated_paths": context.propagated_paths,
            "index_rebuilt": context.index_rebuilt,
            "refactored": context.should_refactor,
            "refactor_results": context.refactor_results,
            "workflow_complete": self.state_machine.is_complete(),
            "workflow_history": self.state_machine.get_history(),
        }

    async def _execute_with_sdk(self, context: WorkflowContext) -> None:
        """Execute workflow using Claude Agent SDK.

        Args:
            context: WorkflowContext for this run
        """
        # This would use the full SDK with hooks
        # For now, fall back to CLI since SDK API may vary
        await self._execute_with_cli(context)

    async def _execute_with_cli(self, context: WorkflowContext) -> None:
        """Execute workflow using Claude CLI subprocess.

        Args:
            context: WorkflowContext for this run
        """
        # Build the full prompt
        system_prompt = self._build_system_prompt(context)
        workflow_prompt = self._build_workflow_prompt(context)

        full_prompt = f"{system_prompt}\n\n---\n\n{workflow_prompt}"

        # Run claude -p
        result = subprocess.run(
            [
                "claude",
                "-p",
                full_prompt,
                "--output-format",
                "text",
            ],
            cwd=str(self.kg_root),
            capture_output=True,
            text=True,
            timeout=self.config.timeout_seconds,
        )

        output = result.stdout + result.stderr

        # Parse step completions from output
        self._parse_step_completions(output, context)

        # Log the raw output for debugging
        if self.logger:
            self.logger.log(
                "input",  # Using input phase for raw output storage
                {
                    "type": "cli_output",
                    "output": output[:5000],  # Truncate if too long
                    "return_code": result.returncode,
                },
            )

    def _parse_step_completions(self, output: str, context: WorkflowContext) -> None:
        """Parse step completion markers from Claude output.

        Looks for patterns like:
        - "RESEARCH COMPLETE: Found 2 matches"
        - "[DECIDE] COMPLETE: CREATE - no existing match"

        Args:
            output: Raw output from Claude
            context: WorkflowContext to update
        """
        import re

        # Pattern: [STEP] COMPLETE: summary or STEP COMPLETE: summary
        pattern = r"\[?(\w+)\]?\s*COMPLETE:\s*(.+?)(?=\n\[?\w+\]?\s*COMPLETE:|\Z)"
        matches = re.findall(pattern, output, re.IGNORECASE | re.DOTALL)

        for step_name, details in matches:
            step_name = step_name.upper()
            details = details.strip()

            # Extract relevant info based on step
            if step_name == "RESEARCH":
                # Store that research was done
                self.state_machine.store_output("RESEARCH", {"matches": []})
                self.state_machine.transition("RESEARCH")

            elif step_name == "DECIDE":
                # Extract decision
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
        """Execute stochastic refactoring step.

        Args:
            context: WorkflowContext for this run
        """
        refactor_prompt = self._build_refactor_prompt(context)

        result = subprocess.run(
            [
                "claude",
                "-p",
                refactor_prompt,
                "--output-format",
                "text",
            ],
            cwd=str(self.kg_root),
            capture_output=True,
            text=True,
            timeout=self.config.timeout_seconds,
        )

        output = result.stdout + result.stderr
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

    parser = argparse.ArgumentParser(description="kgraph headless orchestrator")
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
