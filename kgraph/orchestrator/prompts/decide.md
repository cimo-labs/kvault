# DECIDE Step Instructions

## Purpose
Based on research results, determine what action to take.

## Decision Matrix

| Condition | Action | When to Use |
|-----------|--------|-------------|
| **CREATE** | No match found | New entity with unique name/email |
| **UPDATE** | High-confidence match (≥0.9) | Add new information to existing entity |
| **SKIP** | Information not valuable | Generic or too vague to warrant an entity |
| **MERGE** | Multiple duplicates found | Consolidate redundant entities |

## Decision Criteria

### CREATE when:
- No matches found in index
- Name is specific enough to identify an individual/entity
- Source is reliable and information is substantive

### UPDATE when:
- Exact alias or email match exists
- New information adds value to existing entity
- High confidence the match is correct

### SKIP when:
- Name is too generic (e.g., "John", "Customer")
- Information is not substantive enough
- Entity type doesn't fit the knowledge graph scope

### MERGE when:
- Multiple entities refer to same real-world person/org
- Research reveals duplicates in the index
- Evidence supports consolidation

## Output Format

State your decision clearly:
```
DECIDE COMPLETE: [ACTION] - [reasoning]
Confidence: [0.0-1.0]
Target path: [path for UPDATE/MERGE, or new path for CREATE]
```

Example:
```
DECIDE COMPLETE: CREATE - No existing match for "Alice Smith", email domain "anthropic.com" is unique
Confidence: 0.95
Target path: people/alice_smith
```

## Next Step
- If CREATE/UPDATE/MERGE: proceed to WRITE
- If SKIP: proceed directly to LOG
