# EXECUTE Step Instructions (Hierarchy Mode)

## Purpose
Execute all planned actions from the ActionPlan, one by one.

## Execution Loop

For each action in the ActionPlan:

### 1. CREATE Action
```
1. Create directory: {path}/
2. Write _meta.json:
   {
     "created": "{today}",
     "last_updated": "{today}",
     "sources": ["{source}"],
     "aliases": ["{from action.content.meta or extract from name}"]
   }
3. Write _summary.md with action.content.summary
```

### 2. UPDATE Action
```
1. Read existing _meta.json
2. Append source to sources (deduplicate)
3. Update last_updated to today
4. Append new content to _summary.md (preserve existing)
```

### 3. DELETE Action
```
1. Remove directory and contents
2. Note: Use sparingly, prefer archiving
```

### 4. MOVE Action
```
1. Copy entity to target_path
2. Remove from original path
3. Update any references (if applicable)
```

## File Formats

### _meta.json
```json
{
  "created": "YYYY-MM-DD",
  "last_updated": "YYYY-MM-DD",
  "sources": ["source_id_1", "source_id_2"],
  "aliases": ["Alternative Name", "email@domain.com"]
}
```

### _summary.md
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
```

## Tracking Completion

After each action, report:
```
EXECUTE ACTION {N}/{TOTAL}: {action_type} at {path}
- Files written: _meta.json, _summary.md
```

After all actions complete:
```
EXECUTE COMPLETE: {N} actions executed
- Created: {list of created paths}
- Updated: {list of updated paths}
```

## Error Handling

If an action fails:
1. Log the error
2. Continue with remaining actions
3. Report partial completion

## Next Step
After completing EXECUTE, proceed to PROPAGATE.
