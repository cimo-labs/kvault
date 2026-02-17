# Changelog

All notable changes to `knowledgevault` are documented in this file.

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
