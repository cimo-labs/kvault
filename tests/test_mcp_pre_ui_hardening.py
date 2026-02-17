"""Regression tests for pre-UI kvault hardening."""

from kvault.mcp.server import (
    handle_kvault_init,
    handle_kvault_log_phase,
    handle_kvault_read_entity,
    handle_kvault_status,
    handle_kvault_write_entity,
)


def test_write_entity_without_meta_autofills_safe_defaults(empty_kb):
    """Missing meta should no longer fail schema-level writes."""
    result = handle_kvault_write_entity(
        path="people/meta_free_user",
        meta=None,
        content="# Meta Free User\n\nCreated without explicit meta.\n",
        create=True,
    )
    assert result["success"] is True
    assert result.get("meta_autofilled", {}).get("source") is True
    assert result.get("meta_autofilled", {}).get("aliases") is True

    entity = handle_kvault_read_entity("people/meta_free_user")
    assert entity is not None
    assert entity["meta"]["source"] == "auto:mcp"
    assert entity["meta"]["aliases"] == ["Meta Free User"]


def test_write_entity_without_meta_reuses_existing_frontmatter(empty_kb):
    """Updates without meta should preserve existing source/aliases."""
    create_result = handle_kvault_write_entity(
        path="people/existing_user",
        meta={"source": "manual", "aliases": ["Existing User"]},
        content="# Existing User\n\nInitial version.\n",
        create=True,
    )
    assert create_result["success"] is True

    update_result = handle_kvault_write_entity(
        path="people/existing_user",
        meta=None,
        content="# Existing User\n\nUpdated content.\n",
        create=False,
    )
    assert update_result["success"] is True
    assert "meta_autofilled" not in update_result

    entity = handle_kvault_read_entity("people/existing_user")
    assert entity is not None
    assert entity["meta"]["source"] == "manual"
    assert entity["meta"]["aliases"] == ["Existing User"]


def test_status_exposes_manifest_and_prefixed_names(initialized_kb):
    """kvault_status should expose canonical manifest metadata for UI/config clients."""
    result = handle_kvault_status(tool_prefix="personal")

    assert result["initialized"] is True
    assert result["tool_manifest_version"]
    assert result["tool_count"] > 0
    assert len(result["tool_manifest"]) == result["tool_count"]
    names = [tool["name"] for tool in result["tool_manifest"]]
    assert "kvault_status" in names
    prefixed = [tool.get("prefixed_name") for tool in result["tool_manifest"]]
    assert "personal_kvault_status" in prefixed


def test_status_session_lookup_returns_full_session(initialized_kb):
    """kvault_status(session_id=...) should return detailed workflow state."""
    init_result = handle_kvault_init(str(initialized_kb))
    session_id = init_result["session_id"]

    result = handle_kvault_status(session_id=session_id)
    assert result["session"]["session_id"] == session_id
    assert result["session"]["current_step"] == "research"


def test_log_phase_accepts_valid_phase_and_rejects_invalid(initialized_kb):
    """kvault_log_phase should validate phase names and keep structured errors."""
    ok = handle_kvault_log_phase(
        phase="research",
        data={"query": "who to follow up with"},
    )
    assert ok["success"] is True
    assert ok["phase"] == "research"

    bad = handle_kvault_log_phase(phase="totally_unknown_phase", data={"x": 1})
    assert bad["success"] is False
    assert bad["error_code"] == "validation_error"

