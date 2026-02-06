# WRITE Step Instructions

## Purpose
Create or update entity files in the knowledge graph.

## Entity Structure

Each entity is a directory containing:
```
{category}/{entity_id}/
├── _meta.json     # Machine-readable metadata (4 required fields)
└── _summary.md    # Human-readable freeform content
```

## _meta.json Format

```json
{
  "created": "YYYY-MM-DD",
  "last_updated": "YYYY-MM-DD",
  "sources": ["source_id_1", "source_id_2"],
  "aliases": ["Alternative Name", "email@domain.com"]
}
```

**Required fields:**
- `created`: ISO date when entity was first created
- `last_updated`: ISO date of most recent update
- `sources`: List of source identifiers for traceability
- `aliases`: List of alternative names, emails, handles

## _summary.md Format

```markdown
# {Entity Name}

**Relationship:** {relationship type}
**Context:** {how you know them}

## Background
{Brief description}

## Interactions
- YYYY-MM-DD: {Interaction note}

## Follow-ups
- [ ] {Pending action item}

## Contact
- Email: {email}
- {other contact info}
```

## Actions

### For CREATE:
1. Create the directory: `{category}/{entity_id}/`
2. Write `_meta.json` with all 4 fields
3. Write `_summary.md` with initial content

### For UPDATE:
1. Read existing `_meta.json`
2. Merge new sources into existing sources (deduplicate)
3. Merge new aliases into existing aliases (deduplicate)
4. Update `last_updated` to today
5. Append new information to `_summary.md`

### For MERGE:
1. Combine information from all duplicate entities
2. Update primary entity with merged data
3. Delete duplicate entities (or mark as merged)
4. Update aliases to include all variants

## Output Format

```
WRITE COMPLETE: [action] at path: {entity_path}
- Created/Updated: _meta.json
- Created/Updated: _summary.md
```

## Next Step
After completing WRITE, proceed to PROPAGATE.
