# RESEARCH Step Instructions

## Purpose
Query the knowledge graph index to find existing entities that might match the new information.

## Required Actions

1. **Search the index database**
   ```bash
   sqlite3 {kg_root}/.kvault/index.db "SELECT path, name, aliases FROM entities WHERE name LIKE '%{name}%' OR aliases LIKE '%{name}%'"
   ```

2. **Search by email domain** (if email provided)
   ```bash
   sqlite3 {kg_root}/.kvault/index.db "SELECT path, name, aliases FROM entities WHERE aliases LIKE '%{email_domain}%'"
   ```

3. **Read existing entity summaries** for potential matches
   - If matches found, read their `_summary.md` to understand context

## Output Format

Report your findings:
```
RESEARCH COMPLETE: Found [N] potential matches
- [entity_path]: [name] (match reason: [alias|name|domain])
- ...
```

Or if no matches:
```
RESEARCH COMPLETE: No existing matches found for "{name}"
```

## Matching Criteria

- **High confidence (0.9+)**: Exact alias match or email domain match
- **Medium confidence (0.7-0.9)**: Fuzzy name match (similar spelling)
- **Low confidence (<0.7)**: Partial name overlap

## Next Step
After completing RESEARCH, proceed to DECIDE.
