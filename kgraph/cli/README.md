# CLI Module

Command-line interface for kgraph operations.

## Commands

| Command | Description |
|---------|-------------|
| `kgraph init <name>` | Initialize a new knowledge graph project |
| `kgraph process` | Process data through the pipeline |
| `kgraph resume` | Resume interrupted processing session |
| `kgraph review` | Interactive review of pending questions |
| `kgraph tree` | Display knowledge graph structure |
| `kgraph validate` | Validate graph integrity |
| `kgraph status` | Show pipeline status |

## Files

- `main.py` - Click command definitions and handlers
- `__init__.py` - Package exports

## Usage Examples

```bash
# Process with options
kgraph process --source emails --batch-size 100 --auto-apply

# Resume specific session
kgraph resume --session-id abc123

# Review with batch filter
kgraph review --batch batch_001

# Validate with strict mode
kgraph validate --strict
```

## Architecture

```
CLI Commands
    │
    ├── init      → Creates project structure
    ├── process   → Orchestrator.process()
    ├── resume    → Orchestrator.resume()
    ├── review    → QuestionQueue.get_pending()
    ├── tree      → FilesystemStorage.list_entities()
    ├── validate  → Schema validation
    └── status    → SessionManager.get_status()
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error |
| 2 | Invalid arguments |
| 3 | Interrupted (Ctrl+C) |
