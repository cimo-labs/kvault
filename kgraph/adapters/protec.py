"""
ProTec entity format adapter.

Bridges the structural differences between ProTec's entity format and
kgraph's ExtractedEntity format.

Format Differences:
    | Aspect           | ProTec                    | kgraph                |
    |------------------|---------------------------|-----------------------|
    | Type field       | `type`                    | `entity_type`         |
    | Tier/Industry    | Nested in `attributes`    | Top-level fields      |
    | Source tracking  | `source: list[str]`       | `source_id: str`      |
    | Optional attrs   | `attributes.{...}`        | `raw_data`            |

Example:
    >>> from kgraph.adapters import ProtecEntityAdapter
    >>>
    >>> # Convert ProTec entity to kgraph
    >>> protec_entity = {
    ...     "name": "Acme Corp",
    ...     "type": "customer",
    ...     "attributes": {"tier": "strategic", "industry": "robotics"},
    ...     "contacts": [{"email": "john@acme.com"}],
    ...     "source": ["email_123", "email_456"],
    ... }
    >>> kgraph_entity = ProtecEntityAdapter.from_protec(protec_entity)
    >>>
    >>> # Convert back to ProTec format
    >>> protec_again = ProtecEntityAdapter.to_protec(kgraph_entity)
"""

from dataclasses import asdict
from typing import Any, Dict, List, Optional

from kgraph.pipeline.agents.base import ExtractedEntity


class ProtecEntityAdapter:
    """
    Bidirectional adapter between ProTec and kgraph entity formats.

    ProTec Format:
        {
            "name": "Acme Corp",
            "type": "customer",
            "tier": "strategic",          # Optional top-level
            "industry": "robotics",       # Optional top-level
            "confidence": 0.85,
            "is_new": True,
            "email_id": "123",
            "source": ["email_123"],
            "contacts": [...],
            "attributes": {
                "tier": "strategic",
                "industry": "robotics",
                "location": "New York",
                "website": "https://acme.com",
                "product_codes": ["ABC", "DEF"],
                "annual_revenue": "$500K",
            }
        }

    kgraph ExtractedEntity:
        {
            "name": "Acme Corp",
            "entity_type": "customer",
            "tier": "strategic",
            "industry": "robotics",
            "confidence": 0.85,
            "source_id": "email_123",
            "contacts": [...],
            "raw_data": {
                "all_sources": ["email_123"],
                "email_id": "123",
                "is_new": True,
                "location": "New York",
                "website": "https://acme.com",
                "product_codes": ["ABC", "DEF"],
                "annual_revenue": "$500K",
            }
        }
    """

    # Fields that map directly from attributes to raw_data
    ATTRIBUTE_FIELDS = {
        "location",
        "website",
        "product_codes",
        "annual_revenue",
        "products",
        "status",
        "description",
        "notes",
    }

    # Fields that are top-level in kgraph (not in raw_data)
    TOP_LEVEL_FIELDS = {"tier", "industry"}

    @classmethod
    def from_protec(cls, entity: Dict[str, Any]) -> ExtractedEntity:
        """
        Convert a ProTec entity dictionary to a kgraph ExtractedEntity.

        Args:
            entity: ProTec format entity dictionary

        Returns:
            kgraph ExtractedEntity instance
        """
        attrs = entity.get("attributes", {})

        # Extract tier (check attributes first, then top-level)
        tier = attrs.get("tier") or entity.get("tier")

        # Extract industry (check attributes first, then top-level)
        industry = attrs.get("industry") or entity.get("industry")

        # Handle source - ProTec uses list, kgraph uses single ID
        sources = entity.get("source", [])
        if isinstance(sources, str):
            sources = [sources]
        source_id = sources[0] if sources else entity.get("email_id")
        if source_id and not isinstance(source_id, str):
            source_id = str(source_id)

        # Build raw_data from attributes and other fields
        raw_data: Dict[str, Any] = {
            "all_sources": sources,
            "is_new": entity.get("is_new", True),
        }

        # Copy email_id if present
        if entity.get("email_id"):
            raw_data["email_id"] = entity["email_id"]

        # Copy attribute fields to raw_data
        for field in cls.ATTRIBUTE_FIELDS:
            if attrs.get(field) is not None:
                raw_data[field] = attrs[field]

        # Copy any extra attributes (not tier/industry/known fields)
        for key, value in attrs.items():
            if key not in cls.TOP_LEVEL_FIELDS and key not in cls.ATTRIBUTE_FIELDS:
                raw_data[key] = value

        # Copy any extra top-level fields
        for key, value in entity.items():
            if key not in {
                "name",
                "type",
                "tier",
                "industry",
                "confidence",
                "is_new",
                "email_id",
                "source",
                "contacts",
                "attributes",
            }:
                raw_data[key] = value

        return ExtractedEntity(
            name=entity.get("name", ""),
            entity_type=entity.get("type", "customer"),
            tier=tier,
            industry=industry,
            contacts=entity.get("contacts", []),
            confidence=entity.get("confidence", 0.5),
            source_id=source_id,
            raw_data=raw_data,
        )

    @classmethod
    def to_protec(cls, entity: ExtractedEntity) -> Dict[str, Any]:
        """
        Convert a kgraph ExtractedEntity to ProTec format.

        Args:
            entity: kgraph ExtractedEntity instance

        Returns:
            ProTec format entity dictionary
        """
        raw = entity.raw_data or {}

        # Build source list
        sources = raw.get("all_sources", [])
        if not sources and entity.source_id:
            sources = [entity.source_id]

        # Build attributes dict
        attributes: Dict[str, Any] = {}

        # Always include tier/industry in attributes (ProTec expects this)
        if entity.tier:
            attributes["tier"] = entity.tier
        if entity.industry:
            attributes["industry"] = entity.industry

        # Copy attribute fields from raw_data
        for field in cls.ATTRIBUTE_FIELDS:
            if raw.get(field) is not None:
                attributes[field] = raw[field]

        result: Dict[str, Any] = {
            "name": entity.name,
            "type": entity.entity_type,
            "confidence": entity.confidence,
            "is_new": raw.get("is_new", True),
            "source": sources,
            "contacts": entity.contacts,
            "attributes": attributes,
        }

        # Add tier/industry at top level too (ProTec sometimes uses both)
        if entity.tier:
            result["tier"] = entity.tier
        if entity.industry:
            result["industry"] = entity.industry

        # Add email_id if present
        if raw.get("email_id"):
            result["email_id"] = raw["email_id"]

        return result

    @classmethod
    def from_protec_batch(
        cls, entities: List[Dict[str, Any]]
    ) -> List[ExtractedEntity]:
        """
        Convert a batch of ProTec entities.

        Args:
            entities: List of ProTec format entities

        Returns:
            List of kgraph ExtractedEntity instances
        """
        return [cls.from_protec(e) for e in entities]

    @classmethod
    def to_protec_batch(
        cls, entities: List[ExtractedEntity]
    ) -> List[Dict[str, Any]]:
        """
        Convert a batch of kgraph entities to ProTec format.

        Args:
            entities: List of kgraph ExtractedEntity instances

        Returns:
            List of ProTec format entity dictionaries
        """
        return [cls.to_protec(e) for e in entities]


class ProtecItemAdapter:
    """
    Adapter for converting ProTec email items to kgraph processing items.

    ProTec emails are converted to the generic "item" format expected
    by kgraph's Orchestrator.process() method.
    """

    @classmethod
    def from_email(cls, email: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a ProTec email record to a kgraph item.

        Args:
            email: Email record from ProTec database with keys:
                   id, subject, from_email, from_name, body_text, date_sent

        Returns:
            kgraph item dictionary
        """
        email_id = email.get("id")

        return {
            "id": f"email_{email_id}" if email_id else None,
            "source_type": "email",
            "content": email.get("body_text", ""),
            "metadata": {
                "subject": email.get("subject", ""),
                "from_email": email.get("from_email", ""),
                "from_name": email.get("from_name", ""),
                "date": email.get("date_sent", ""),
                "email_id": email_id,
            },
        }

    @classmethod
    def from_email_batch(
        cls, emails: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert a batch of emails to kgraph items.

        Args:
            emails: List of email records

        Returns:
            List of kgraph items
        """
        return [cls.from_email(e) for e in emails]
