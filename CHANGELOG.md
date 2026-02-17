# Changelog

All notable changes to `knowledgevault` are documented in this file.

## 0.6.3 - 2026-02-17

### Security

- Added optional KB-root pinning guard for MCP init:
  - `kvault_init` now enforces `KVAULT_ALLOWED_ROOTS` when configured.
  - Returns structured `validation_error` if requested `kg_root` is outside allowed roots.
- `kvault_status` now reports configured `allowed_kg_roots` when root pinning is enabled.

### Compatibility & Docs

- Aligned README workflow language with staged MCP flow (research -> decide -> execute -> propagate -> log -> rebuild/validate).
- Added packaging excludes for Python cache artifacts (`__pycache__`, `*.py[cod]`) across wheel + sdist (`pyproject.toml`, `MANIFEST.in`) to keep releases clean.

### Testing

- Added MCP root guard coverage in `tests/test_mcp_pre_ui_hardening.py`.

## 0.6.2 - 2026-02-17

### Added

- Added shared research primitives in `kvault.core.research`:
  - `EntityResearcher`
  - `ResearchCandidate`
- Added `kvault log summary` CLI command for observability session summaries.
- Added tests for research primitives and log CLI behavior.

### Changed

- `ObservabilityLogger.get_session_summary()` now defaults to the latest logged session.
- Added `ObservabilityLogger.list_sessions()` helper.
- Refactored ProTec adapter to reuse `kvault.core.research.EntityResearcher` instead of local duplicate logic.
- Reconciled architecture and maintainer docs with the canonical MCP manifest and current module layout.

## 0.6.1 - 2026-02-17

### Security

- Hardened MCP path handling to prevent writes or moves that escape the configured KB root.
  - `handle_kvault_write_summary` now rejects paths outside KB root.
  - `handle_kvault_move_entity` now validates `source_path` and `target_path` and enforces root containment for both.

### Testing

- Added MCP path traversal regression coverage in `tests/test_e2e_workflows.py`:
  - summary write escape attempts
  - move source/target traversal attempts
  - batch summary update escape attempts

### Notes

- This is a patch release intended for immediate rollout on deployed moss/OpenClaw hosts using kvault MCP.
