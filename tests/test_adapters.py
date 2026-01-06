"""Tests for entity format adapters."""

import pytest
from kgraph.adapters import ProtecEntityAdapter
from kgraph.adapters.protec import ProtecItemAdapter
from kgraph.pipeline.agents.base import ExtractedEntity


class TestProtecEntityAdapterFromProtec:
    """Tests for converting ProTec → kgraph format."""

    def test_basic_conversion(self):
        """Test basic entity conversion."""
        protec = {
            "name": "Acme Corp",
            "type": "customer",
            "confidence": 0.85,
        }

        entity = ProtecEntityAdapter.from_protec(protec)

        assert entity.name == "Acme Corp"
        assert entity.entity_type == "customer"
        assert entity.confidence == 0.85

    def test_nested_attributes(self):
        """Test extracting tier/industry from nested attributes."""
        protec = {
            "name": "Acme Corp",
            "type": "customer",
            "attributes": {
                "tier": "strategic",
                "industry": "robotics",
            },
        }

        entity = ProtecEntityAdapter.from_protec(protec)

        assert entity.tier == "strategic"
        assert entity.industry == "robotics"

    def test_top_level_tier_industry(self):
        """Test tier/industry at top level."""
        protec = {
            "name": "Acme Corp",
            "type": "customer",
            "tier": "key",
            "industry": "automotive",
        }

        entity = ProtecEntityAdapter.from_protec(protec)

        assert entity.tier == "key"
        assert entity.industry == "automotive"

    def test_attributes_override_top_level(self):
        """Test that attributes take precedence over top-level."""
        protec = {
            "name": "Acme Corp",
            "type": "customer",
            "tier": "standard",
            "industry": "industrial",
            "attributes": {
                "tier": "strategic",
                "industry": "robotics",
            },
        }

        entity = ProtecEntityAdapter.from_protec(protec)

        # Attributes should win
        assert entity.tier == "strategic"
        assert entity.industry == "robotics"

    def test_source_list_to_source_id(self):
        """Test converting source list to single source_id."""
        protec = {
            "name": "Acme Corp",
            "type": "customer",
            "source": ["email_123", "email_456", "email_789"],
        }

        entity = ProtecEntityAdapter.from_protec(protec)

        assert entity.source_id == "email_123"
        assert entity.raw_data["all_sources"] == ["email_123", "email_456", "email_789"]

    def test_email_id_fallback(self):
        """Test email_id fallback when no source list."""
        protec = {
            "name": "Acme Corp",
            "type": "customer",
            "email_id": "12345",
        }

        entity = ProtecEntityAdapter.from_protec(protec)

        assert entity.source_id == "12345"
        assert entity.raw_data["email_id"] == "12345"

    def test_contacts_preserved(self):
        """Test contacts are preserved."""
        protec = {
            "name": "Acme Corp",
            "type": "customer",
            "contacts": [
                {"name": "John Doe", "email": "john@acme.com", "role": "buyer"},
                {"name": "Jane Smith", "email": "jane@acme.com"},
            ],
        }

        entity = ProtecEntityAdapter.from_protec(protec)

        assert len(entity.contacts) == 2
        assert entity.contacts[0]["name"] == "John Doe"
        assert entity.contacts[0]["email"] == "john@acme.com"

    def test_attribute_fields_to_raw_data(self):
        """Test that attribute fields go to raw_data."""
        protec = {
            "name": "Acme Corp",
            "type": "customer",
            "attributes": {
                "location": "New York",
                "website": "https://acme.com",
                "product_codes": ["ABC", "DEF"],
                "annual_revenue": "$500K",
            },
        }

        entity = ProtecEntityAdapter.from_protec(protec)

        assert entity.raw_data["location"] == "New York"
        assert entity.raw_data["website"] == "https://acme.com"
        assert entity.raw_data["product_codes"] == ["ABC", "DEF"]
        assert entity.raw_data["annual_revenue"] == "$500K"

    def test_is_new_preserved(self):
        """Test is_new flag is preserved in raw_data."""
        protec = {
            "name": "Acme Corp",
            "type": "customer",
            "is_new": False,
        }

        entity = ProtecEntityAdapter.from_protec(protec)

        assert entity.raw_data["is_new"] is False

    def test_default_values(self):
        """Test default values for missing fields."""
        protec = {"name": "Minimal"}

        entity = ProtecEntityAdapter.from_protec(protec)

        assert entity.name == "Minimal"
        assert entity.entity_type == "customer"
        assert entity.confidence == 0.5
        assert entity.tier is None
        assert entity.industry is None
        assert entity.contacts == []


class TestProtecEntityAdapterToProtec:
    """Tests for converting kgraph → ProTec format."""

    def test_basic_conversion(self):
        """Test basic entity conversion."""
        entity = ExtractedEntity(
            name="Acme Corp",
            entity_type="customer",
            confidence=0.85,
        )

        protec = ProtecEntityAdapter.to_protec(entity)

        assert protec["name"] == "Acme Corp"
        assert protec["type"] == "customer"
        assert protec["confidence"] == 0.85

    def test_tier_industry_in_both_places(self):
        """Test tier/industry appear in both top-level and attributes."""
        entity = ExtractedEntity(
            name="Acme Corp",
            entity_type="customer",
            tier="strategic",
            industry="robotics",
        )

        protec = ProtecEntityAdapter.to_protec(entity)

        # Top-level
        assert protec["tier"] == "strategic"
        assert protec["industry"] == "robotics"
        # Also in attributes
        assert protec["attributes"]["tier"] == "strategic"
        assert protec["attributes"]["industry"] == "robotics"

    def test_source_from_raw_data(self):
        """Test source list from raw_data.all_sources."""
        entity = ExtractedEntity(
            name="Acme Corp",
            entity_type="customer",
            source_id="email_123",
            raw_data={"all_sources": ["email_123", "email_456"]},
        )

        protec = ProtecEntityAdapter.to_protec(entity)

        assert protec["source"] == ["email_123", "email_456"]

    def test_source_from_source_id_fallback(self):
        """Test source falls back to source_id."""
        entity = ExtractedEntity(
            name="Acme Corp",
            entity_type="customer",
            source_id="email_999",
        )

        protec = ProtecEntityAdapter.to_protec(entity)

        assert protec["source"] == ["email_999"]

    def test_contacts_preserved(self):
        """Test contacts are preserved."""
        entity = ExtractedEntity(
            name="Acme Corp",
            entity_type="customer",
            contacts=[
                {"name": "John", "email": "john@acme.com"},
            ],
        )

        protec = ProtecEntityAdapter.to_protec(entity)

        assert len(protec["contacts"]) == 1
        assert protec["contacts"][0]["email"] == "john@acme.com"

    def test_raw_data_to_attributes(self):
        """Test raw_data fields go to attributes."""
        entity = ExtractedEntity(
            name="Acme Corp",
            entity_type="customer",
            raw_data={
                "location": "New York",
                "website": "https://acme.com",
                "product_codes": ["ABC"],
            },
        )

        protec = ProtecEntityAdapter.to_protec(entity)

        assert protec["attributes"]["location"] == "New York"
        assert protec["attributes"]["website"] == "https://acme.com"
        assert protec["attributes"]["product_codes"] == ["ABC"]

    def test_email_id_from_raw_data(self):
        """Test email_id is extracted from raw_data."""
        entity = ExtractedEntity(
            name="Acme Corp",
            entity_type="customer",
            raw_data={"email_id": "12345"},
        )

        protec = ProtecEntityAdapter.to_protec(entity)

        assert protec["email_id"] == "12345"

    def test_is_new_default(self):
        """Test is_new defaults to True."""
        entity = ExtractedEntity(
            name="Acme Corp",
            entity_type="customer",
        )

        protec = ProtecEntityAdapter.to_protec(entity)

        assert protec["is_new"] is True


class TestProtecEntityAdapterRoundtrip:
    """Tests for roundtrip conversion (ProTec → kgraph → ProTec)."""

    def test_full_entity_roundtrip(self):
        """Test full entity survives roundtrip."""
        original = {
            "name": "Acme Corporation",
            "type": "customer",
            "tier": "strategic",
            "industry": "robotics",
            "confidence": 0.92,
            "is_new": False,
            "email_id": "12345",
            "source": ["email_123", "email_456"],
            "contacts": [
                {"name": "John Doe", "email": "john@acme.com", "role": "VP"},
            ],
            "attributes": {
                "tier": "strategic",
                "industry": "robotics",
                "location": "Boston",
                "website": "https://acme.com",
                "product_codes": ["ABC", "DEF"],
                "annual_revenue": "$1M",
            },
        }

        # Convert to kgraph
        kgraph = ProtecEntityAdapter.from_protec(original)

        # Convert back to ProTec
        restored = ProtecEntityAdapter.to_protec(kgraph)

        # Verify key fields preserved
        assert restored["name"] == original["name"]
        assert restored["type"] == original["type"]
        assert restored["tier"] == original["tier"]
        assert restored["industry"] == original["industry"]
        assert restored["confidence"] == original["confidence"]
        assert restored["is_new"] == original["is_new"]
        assert restored["source"] == original["source"]
        assert restored["contacts"] == original["contacts"]

        # Attributes preserved
        assert restored["attributes"]["tier"] == original["attributes"]["tier"]
        assert restored["attributes"]["industry"] == original["attributes"]["industry"]
        assert restored["attributes"]["location"] == original["attributes"]["location"]
        assert restored["attributes"]["website"] == original["attributes"]["website"]
        assert restored["attributes"]["product_codes"] == original["attributes"]["product_codes"]

    def test_minimal_entity_roundtrip(self):
        """Test minimal entity survives roundtrip."""
        original = {
            "name": "Minimal Corp",
            "type": "supplier",
        }

        kgraph = ProtecEntityAdapter.from_protec(original)
        restored = ProtecEntityAdapter.to_protec(kgraph)

        assert restored["name"] == original["name"]
        assert restored["type"] == original["type"]

    def test_batch_conversion(self):
        """Test batch conversion methods."""
        protec_batch = [
            {"name": "Acme Corp", "type": "customer", "tier": "strategic"},
            {"name": "Beta Inc", "type": "supplier", "industry": "automotive"},
        ]

        # Convert batch to kgraph
        kgraph_batch = ProtecEntityAdapter.from_protec_batch(protec_batch)
        assert len(kgraph_batch) == 2
        assert kgraph_batch[0].name == "Acme Corp"
        assert kgraph_batch[1].name == "Beta Inc"

        # Convert batch back
        restored_batch = ProtecEntityAdapter.to_protec_batch(kgraph_batch)
        assert len(restored_batch) == 2
        assert restored_batch[0]["name"] == "Acme Corp"
        assert restored_batch[1]["name"] == "Beta Inc"


class TestProtecItemAdapter:
    """Tests for email item adapter."""

    def test_basic_email_conversion(self):
        """Test basic email to item conversion."""
        email = {
            "id": 12345,
            "subject": "Order Inquiry",
            "from_email": "john@acme.com",
            "from_name": "John Doe",
            "body_text": "We need to order more parts...",
            "date_sent": "2024-01-15T10:30:00",
        }

        item = ProtecItemAdapter.from_email(email)

        assert item["id"] == "email_12345"
        assert item["source_type"] == "email"
        assert item["content"] == "We need to order more parts..."
        assert item["metadata"]["subject"] == "Order Inquiry"
        assert item["metadata"]["from_email"] == "john@acme.com"
        assert item["metadata"]["from_name"] == "John Doe"
        assert item["metadata"]["date"] == "2024-01-15T10:30:00"
        assert item["metadata"]["email_id"] == 12345

    def test_missing_fields(self):
        """Test handling missing email fields."""
        email = {"id": 1}

        item = ProtecItemAdapter.from_email(email)

        assert item["id"] == "email_1"
        assert item["content"] == ""
        assert item["metadata"]["subject"] == ""
        assert item["metadata"]["from_email"] == ""

    def test_batch_conversion(self):
        """Test batch email conversion."""
        emails = [
            {"id": 1, "subject": "First", "body_text": "Body 1"},
            {"id": 2, "subject": "Second", "body_text": "Body 2"},
        ]

        items = ProtecItemAdapter.from_email_batch(emails)

        assert len(items) == 2
        assert items[0]["id"] == "email_1"
        assert items[1]["id"] == "email_2"
        assert items[0]["content"] == "Body 1"
        assert items[1]["content"] == "Body 2"
