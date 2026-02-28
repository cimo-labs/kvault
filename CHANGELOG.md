# Changelog

All notable changes to `knowledgevault` are documented in this file.

## 0.7.1 - 2026-02-27

### Changed

- **Multi-tool compatibility**: Renamed `kvault/templates/CLAUDE.md` → `AGENTS.md`. `kvault init` now generates `AGENTS.md` instead of `CLAUDE.md`. Template language generalized for all AI coding agents (Claude Code, OpenAI Codex, Gemini CLI, Cursor, GitHub Copilot).
- **README**: Added multi-tool quickstart tips table; integrity hook section now shows CLI command first with tool-specific hook as an example; replaced Claude Code-specific tool references with generic language.

### Fixed

- **Click 8.2 compatibility**: Removed `mix_stderr` kwarg from `CliRunner()` in tests (removed in Click 8.2).

## 0.7.0 - 2026-02-25

### Added

- **CLI-first architecture**: All KB operations now available as CLI commands (`kvault read`, `kvault write`, `kvault list`, `kvault delete`, `kvault move`, `kvault read-summary`, `kvault write-summary`, `kvault update-summaries`, `kvault ancestors`, `kvault journal`, `kvault validate`, `kvault status`, `kvault tree`).
- **Shared operations layer** (`kvault/core/operations.py`): Stateless functions backing all CLI commands. All functions take `kg_root: Path` as first argument.
- **Validation moved to core** (`kvault/core/validation.py`): Business rules used by CLI and operations layer.
- **CLI helpers** (`kvault/cli/_helpers.py`): KB root auto-detection, stdin reading, JSON output.
- **Group-level options**: `--kb-root` and `--json` flags on the top-level `kvault` group, inherited by all subcommands.
- **Source tracking**: CLI uses `default_source="auto:cli"` to identify write origins.
- **New tests**: `test_operations.py` (26 tests), `test_cli_commands.py` (28 tests), `test_cli_write_workflow.py` (2 tests).

### Changed

- **`kvault init` output**: Changed "Next steps" from MCP config JSON to CLI usage instructions.
- **Templates**: `kvault/templates/CLAUDE.md` rewritten for CLI workflow (shell commands, not MCP tool calls). (Renamed to `AGENTS.md` in 0.7.1.)
- **Documentation**: README, CLAUDE.md, and CHANGELOG updated for CLI-first architecture.

### Removed

- **MCP server**: The `kvault/mcp/` package, `kvault-mcp` entry point, and `[mcp]` install extra have been removed. CLI commands are now the sole interface. Install with `pip install knowledgevault` (no extras needed).

## 0.6.3 - 2026-02-17

### Security

- Added optional KB-root pinning guard:
  - Init now enforces `KVAULT_ALLOWED_ROOTS` when configured.
  - Returns structured `validation_error` if requested `kg_root` is outside allowed roots.
- Status now reports configured `allowed_kg_roots` when root pinning is enabled.

### Compatibility & Docs

- Aligned README workflow language with staged flow (research -> decide -> execute -> propagate -> log -> rebuild/validate).
- Added packaging excludes for Python cache artifacts (`__pycache__`, `*.py[cod]`) across wheel + sdist (`pyproject.toml`, `MANIFEST.in`) to keep releases clean.

### Testing

- Added root guard coverage.

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
- Reconciled architecture and maintainer docs with current module layout.

## 0.6.1 - 2026-02-17

### Security

- Hardened path handling to prevent writes or moves that escape the configured KB root.
  - Summary writes now reject paths outside KB root.
  - Entity moves now validate source and target paths and enforce root containment for both.

### Testing

- Added path traversal regression coverage in `tests/test_e2e_workflows.py`:
  - summary write escape attempts
  - move source/target traversal attempts
  - batch summary update escape attempts
