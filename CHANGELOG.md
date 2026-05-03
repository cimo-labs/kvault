# Changelog

All notable changes to `knowledgevault` are documented in this file.

## 0.10.0 - Unreleased

### Added

- **Strict MCP parent-summary updates**: Added `kvault_prepare_summary_update` and
  `kvault_write_parent_summary` so MCP clients can read all direct child summaries before writing
  a parent rollup.
- **Stateless child-summary digests**: Parent summary writes can now reject stale MCP update
  attempts when a direct child summary changed after preparation.
- **Hierarchy pressure hints**: Strict prepare calls return an advisory `hierarchy_hint` when a
  parent has more than 10 direct children.

### Compatibility

- Existing summary tools, including `kvault_update_summaries`, `kvault_write_summary`,
  `kvault_get_parent_summaries`, `kvault_get_ancestors`, and `kvault_propagate_all`, remain
  available unchanged.

## 0.9.0 - 2026-05-03

### Added

- **Node-first interface**: `kvault read`, `kvault write`, and `kvault list` now operate on any
  visible `_summary.md` node, including root, parent branches, and leaf entities.
- **Structured lexical search**: Added `kvault search`, Python `search_nodes(...)`, and MCP
  `kvault_search` for node-aware discovery across path, title, aliases, headings, and body text.
- **Node MCP tools**: Added `kvault_read_node`, `kvault_write_node`, and `kvault_list_nodes`.

### Changed

- **Read context**: Node reads return the full requested node plus immediate parent context by
  default, with options for no parents or full ancestry.
- **Write behavior**: Node writes preserve existing frontmatter when stdin omits frontmatter, and
  still return ancestor summaries for propagation.
- **CI dependencies**: Development validation now installs `[dev,mcp]` only.
- **Docs and fixtures**: Public examples and test fixtures now use neutral sample data; maintainer
  notes moved to a provider-neutral filename.
- **Packaging metadata**: Release builds use SPDX-style license metadata.

### Removed

- **Optional web UI**: Removed `kvault ui`, the `[ui]` extra, and the Starlette/Jinja/htmx UI
  package to keep kvault focused on files, CLI, MCP, and Python APIs.

### Compatibility

- Existing entity and summary CLI/MCP names remain available as compatibility aliases.

## 0.8.0 - 2026-02-27

### Added

- **Read-only web UI** (`kvault ui`): Browse your knowledge base in a local web browser. Starlette + htmx + Jinja2 — no npm/node required. Optional install: `pip install 'knowledgevault[ui]'`.
  - Dashboard with entity count, health status, and tree preview
  - Two-column tree browser with lazy-loaded navigation (htmx)
  - Entity detail with server-side Markdown rendering (mistune)
  - Live search with 300ms debounce (htmx)
  - Breadcrumb navigation, category summaries
  - Pico CSS (CDN) for responsive classless styling; htmx vendored (~50KB, no CDN dependency for JS)
  - All routes read-only with path traversal defense-in-depth
- **`[ui]` optional dependency group**: `starlette`, `uvicorn`, `jinja2`, `mistune`
- **Summary-quality audit**: `kvault check` now emits warn-only `SUMMARY:` findings for
  parent summaries that are too short, omit immediate child coverage, or contain placeholder
  redirect language.
- **Thin MCP compatibility server**: Restored `[mcp]` extra and `kvault-mcp` entry point with
  root-bound tools backed by `kvault.core.operations`.
- **`httpx`** added to `[dev]` dependencies for Starlette test client
- **New tests**: `test_ui.py` (integration), `test_ui_search.py` (unit)

### Changed

- **CLI option ordering**: Agent-facing commands accept `--json` and `--kb-root` before or after
  the subcommand.
- **Artifact CLI**: `kvault artifact daily` now honors top-level `--kb-root` and supports JSON
  output for machine-readable artifact generation.
- **Entity path validation**: Removed the old max-depth cap while keeping safe lowercase
  component validation and root-escape protection.
- **Init templates**: Freshly initialized KBs now start with parent summaries that satisfy the
  summary-quality audit.
- **Root pinning**: `KVAULT_ALLOWED_ROOTS` is enforced at CLI and MCP boundaries.
- **Public API**: `ObservabilityLogger` is exported from top-level `kvault`, and `__version__`
  is read from package metadata when installed.

### Fixed

- **Starlette/Jinja compatibility**: `TemplateResponse` calls now work across current and older
  Starlette signatures.
- **CI workflow**: Installs `[dev,ui,mcp]` so UI and MCP compatibility tests run where supported.

## 0.7.1 - 2026-02-27

### Changed

- **Multi-tool compatibility**: Renamed the tool-specific agent template to `AGENTS.md`. `kvault init` now generates `AGENTS.md`. Template language generalized for AI coding agents.
- **README**: Added multi-tool quickstart tips table; integrity hook section now shows CLI command first with generic tool language.

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
- **Templates**: The generated agent instructions were rewritten for CLI workflow (shell commands, not MCP tool calls). (Renamed to `AGENTS.md` in 0.7.1.)
- **Documentation**: README, generated agent instructions, and CHANGELOG updated for CLI-first architecture.

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
- Refactored downstream adapter integration to reuse `kvault.core.research.EntityResearcher` instead of local duplicate logic.
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
