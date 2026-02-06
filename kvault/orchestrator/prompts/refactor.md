# REFACTOR Step Instructions

## Purpose
This step is triggered stochastically (Bernoulli p=0.1 by default) to maintain knowledge graph quality over time.

## Refactor Opportunities

### 1. Duplicate Detection
- Look for entities that might be the same person/org
- Signs of duplicates:
  - Similar names with different formatting
  - Same email domain but different entity paths
  - Overlapping aliases

### 2. Stale Information
- Entities with `last_updated` older than 6 months
- Information that may be outdated
- Missing context or relationships

### 3. Missing Cross-References
- Related entities that should link to each other
- Projects missing team member references
- People missing organization connections

### 4. Naming Inconsistencies
- Entity IDs that don't follow conventions
- Aliases that should be canonical names
- Typos in entity names

### 5. Orphaned Entities
- Entities with no sources
- Entities with empty summaries
- Categories with only one entity (might merge)

## Search Patterns

```bash
# Find potential duplicates
sqlite3 .kvault/index.db "SELECT name, COUNT(*) c FROM entities GROUP BY LOWER(name) HAVING c > 1"

# Find stale entities
find . -name "_meta.json" -exec sh -c 'grep -l "last_updated.*2023" "$1"' _ {} \;

# Find sparse summaries
find . -name "_summary.md" -size -200c
```

## Refactor Actions

### Merge Duplicates
1. Identify canonical entity (most complete)
2. Merge aliases from all duplicates
3. Merge sources from all duplicates
4. Combine summary content
5. Delete duplicate directories
6. Update references in ancestor summaries

### Update Stale Info
1. Flag entity for review
2. Add note to summary about staleness
3. Create follow-up task in journal

### Add Cross-References
1. Identify related entities
2. Add "See also" section to summaries
3. Update ancestor summaries with relationships

## Output Format

```
REFACTOR COMPLETE: [summary of actions]
- Merged: [entity_a] + [entity_b] → [canonical_path]
- Flagged: [stale_entity] for review
- Added cross-reference: [entity_x] ↔ [entity_y]
```

Or if no opportunities:
```
REFACTOR COMPLETE: No refactor opportunities identified
- Scanned [N] entities
- All entities appear well-maintained
```

## Guidelines

- Execute at most ONE refactor action per trigger
- Prefer safe actions (add references) over destructive (merge/delete)
- Log all changes for auditability
- When uncertain, flag for human review instead of acting

## Frequency

This step is triggered approximately 10% of the time (configurable).
The goal is to amortize maintenance work over regular operations.
