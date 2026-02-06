# PROPAGATE Step Instructions

## Purpose
Update all ancestor `_summary.md` files to reflect the new/updated entity.

## Propagation Chain

For an entity at `people/alice_smith`:
1. Update `people/_summary.md` (parent category)
2. Update root `_summary.md` (if significant)

## Update Strategy

### Category Summary (e.g., `people/_summary.md`)

Add or update the entity reference:
```markdown
## People

### Active Collaborators
- [Alice Smith](alice_smith/) - Research collaborator, Anthropic (added 2024-01-15)
```

If the category summary has sections, place the entity in the appropriate one.

### Root Summary

Only update root `_summary.md` for significant additions:
- New key relationships
- New active projects
- Major accomplishments

## Actions

1. **Read ancestor summaries**
   ```
   {kg_root}/_summary.md
   {kg_root}/people/_summary.md
   ```

2. **Determine placement**
   - Which section does the entity belong in?
   - Is there an existing reference to update?

3. **Update summaries**
   - Add new reference if entity is new
   - Update existing reference if entity was updated
   - Add date stamp for new additions

## Output Format

```
PROPAGATE COMPLETE: Updated [N] ancestor summaries
- Updated: people/_summary.md (added Alice Smith reference)
- Updated: _summary.md (added to Active Collaborators)
```

Or if no propagation needed:
```
PROPAGATE COMPLETE: No ancestor updates needed (entity update was minor)
```

## Guidelines

- Keep ancestor summaries concise
- Use relative links to entities: `[Name](entity_path/)`
- Include brief context/description
- Add date stamps for new entries
- Don't duplicate full content from entity summaries

## Next Step
After completing PROPAGATE, proceed to LOG.
