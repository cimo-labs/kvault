"""
Extraction agent for entity extraction from raw data.

Uses Claude CLI in headless mode to extract structured entities
from unstructured data (emails, documents, etc.).
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from kgraph.core.config import KGraphConfig
from kgraph.pipeline.agents.base import ExtractedEntity, AgentContext
from kgraph.pipeline.audit import log_audit, log_error


class ExtractionAgent:
    """
    Agent that extracts entities from raw data using LLM.

    Uses Claude CLI in headless mode with structured output.
    """

    def __init__(self, config: KGraphConfig):
        """
        Initialize extraction agent.

        Args:
            config: KGraph configuration
        """
        self.config = config
        self.prompts_path = config.prompts_path

    @property
    def name(self) -> str:
        """Agent name for logging."""
        return "extraction"

    def extract(
        self,
        items: List[Dict[str, Any]],
        context: Optional[AgentContext] = None,
    ) -> List[ExtractedEntity]:
        """
        Extract entities from raw data items.

        Args:
            items: List of raw data items (e.g., emails, documents)
            context: Optional agent context

        Returns:
            List of extracted entities
        """
        if not items:
            return []

        prompt = self._build_prompt(items, context)

        log_audit(
            "agent",
            "invoke",
            {
                "agent": self.name,
                "items": len(items),
                "prompt_length": len(prompt),
            },
        )

        try:
            result = self._call_llm(prompt)
            entities = self._parse_response(result, items)

            log_audit(
                "agent",
                "complete",
                {
                    "agent": self.name,
                    "entities_extracted": len(entities),
                },
            )

            return entities

        except subprocess.TimeoutExpired:
            log_audit("agent", "timeout", {
                "agent": self.name,
                "timeout": self.config.agent.timeout,
            })
            return []

        except Exception as e:
            log_error(e, {"agent": self.name, "items": len(items)})
            return []

    def _build_prompt(
        self,
        items: List[Dict[str, Any]],
        context: Optional[AgentContext] = None,
    ) -> str:
        """Build extraction prompt from items."""
        # Try to load template
        template = self._load_template(context)

        # Format items
        items_text = "\n\n".join(
            f"--- ITEM {i + 1} (id: {item.get('id', i)}) ---\n{self._format_item(item)}"
            for i, item in enumerate(items)
        )

        # Build entity types reference
        entity_types = list(self.config.entity_types.keys())
        tiers = list(self.config.tiers.keys())

        return f"""{template}

## Entity Types
{', '.join(entity_types)}

## Tier Options
{', '.join(tiers)}

## Items to Process

{items_text}

## Output Format

Return ONLY valid JSON:
```json
{{
  "entities": [
    {{
      "name": "Company/Person Name",
      "entity_type": "{entity_types[0] if entity_types else 'entity'}",
      "tier": "{tiers[0] if tiers else 'standard'}",
      "industry": "robotics|automotive|medical|industrial|other",
      "contacts": [
        {{"name": "Contact Name", "email": "email@example.com", "role": "Title"}}
      ],
      "confidence": 0.85,
      "source_id": "item-id"
    }}
  ]
}}
```

Only extract entities with confidence >= 0.5.
Include source_id to link back to the source item.
Respond with JSON only, no explanation."""

    def _load_template(self, context: Optional[AgentContext] = None) -> str:
        """Load prompt template or use default."""
        # Try context first
        if context:
            template = context.get_prompt_template("extraction")
            if template:
                return template

        # Try config path
        if self.prompts_path:
            template_path = self.prompts_path / "extraction.md"
            if template_path.exists():
                return template_path.read_text()

        # Default template
        return self._default_template()

    def _default_template(self) -> str:
        """Default extraction prompt template."""
        return """# Entity Extraction

Extract business entities from the provided items.

For each entity found, determine:
1. **name**: Official company/organization/person name (normalized)
2. **entity_type**: Type from allowed list
3. **tier**: Tier classification based on apparent importance
4. **industry**: Primary industry vertical
5. **contacts**: List of {name, email, phone, role}
6. **confidence**: 0.0-1.0 how confident you are in this extraction

## Extraction Guidelines

- Normalize company names (e.g., "Acme Corp." → "Acme Corporation")
- Infer tier from context (strategic accounts mentioned prominently, prospects in inquiries)
- Include all contacts with at least name OR email
- Don't create duplicate entities from the same item
- Set confidence based on how clearly the entity is identified"""

    def _format_item(self, item: Dict[str, Any]) -> str:
        """Format a single item for the prompt."""
        # Handle different item types
        if "subject" in item and "body" in item:
            # Email format
            parts = []
            if item.get("from"):
                parts.append(f"From: {item['from']}")
            if item.get("to"):
                parts.append(f"To: {item['to']}")
            if item.get("date"):
                parts.append(f"Date: {item['date']}")
            parts.append(f"Subject: {item.get('subject', '')}")
            parts.append(f"\n{item.get('body', '')}")
            return "\n".join(parts)

        elif "content" in item:
            # Document format
            return item["content"]

        else:
            # Generic JSON
            return json.dumps(item, indent=2, default=str)

    def _call_llm(self, prompt: str) -> str:
        """Call LLM via Claude CLI."""
        result = subprocess.run(
            ["claude", "-p", "-"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.config.agent.timeout,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI failed: {result.stderr}")

        return result.stdout

    def _parse_response(
        self,
        response: str,
        source_items: List[Dict[str, Any]],
    ) -> List[ExtractedEntity]:
        """Parse LLM response into entities."""
        # Find JSON in response
        start = response.find("{")
        end = response.rfind("}") + 1

        if start < 0 or end <= start:
            log_audit("agent", "parse_failed", {
                "agent": self.name,
                "reason": "no_json_found",
                "response_preview": response[:200],
            })
            return []

        try:
            data = json.loads(response[start:end])
        except json.JSONDecodeError as e:
            log_audit("agent", "parse_failed", {
                "agent": self.name,
                "reason": "json_decode_error",
                "error": str(e),
            })
            return []

        entities = []
        for e_data in data.get("entities", []):
            # Skip low confidence
            confidence = e_data.get("confidence", 0.5)
            if confidence < 0.5:
                continue

            # Validate required fields
            name = e_data.get("name", "").strip()
            if not name:
                continue

            entity = ExtractedEntity(
                name=name,
                entity_type=e_data.get("entity_type", "entity"),
                tier=e_data.get("tier"),
                industry=e_data.get("industry"),
                contacts=e_data.get("contacts", []),
                confidence=confidence,
                source_id=e_data.get("source_id"),
                raw_data=e_data,
            )
            entities.append(entity)

        return entities


class MockExtractionAgent(ExtractionAgent):
    """
    Mock extraction agent for testing without LLM.

    Returns pre-defined entities instead of calling Claude.
    """

    def __init__(self, config: KGraphConfig, mock_entities: Optional[List[Dict]] = None):
        super().__init__(config)
        self.mock_entities = mock_entities or []

    def _call_llm(self, prompt: str) -> str:
        """Return mock response instead of calling LLM."""
        return json.dumps({"entities": self.mock_entities})
