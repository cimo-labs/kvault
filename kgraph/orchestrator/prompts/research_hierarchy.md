# RESEARCH Step Instructions (Hierarchy Mode)

## Purpose
Analyze the raw input and search for related entities in the knowledge hierarchy.

## Research Process

### 1. Extract Key Terms
From the raw input, identify:
- **People names** (full names, nicknames)
- **Organizations** (companies, institutions)
- **Projects** (codenames, product names)
- **Key topics** (for context matching)

### 2. Search the Index

For each extracted term:
```bash
sqlite3 {kg_root}/.kgraph/index.db "
SELECT path, name, aliases
FROM entities
WHERE name LIKE '%{term}%'
   OR aliases LIKE '%{term}%'
"
```

### 3. Read Relevant Summaries

For high-confidence matches, read their `_summary.md` to:
- Confirm identity (is this the same person/entity?)
- Understand existing context
- Identify what new information would be valuable

### 4. Check Hierarchy Structure

Review the hierarchy tree to understand:
- Where similar entities are organized
- Whether subdirectories exist for the category
- Naming conventions in use

## Output Format

Report your findings:
```
RESEARCH COMPLETE: Analyzed input, found [N] related entities

Extracted terms: [list]

Matches:
- [path]: [name] (confidence: [0.0-1.0], reason: [why matched])
- ...

Context gathered:
- [path]: [relevant summary excerpt]
- ...

Hierarchy notes:
- [observations about where new entities should go]
```

Or if no matches:
```
RESEARCH COMPLETE: No existing matches found

Extracted terms: [list]
Recommended paths for new entities:
- [term] → [suggested_path]
```

## Matching Confidence

- **High (0.9+)**: Exact name or alias match
- **Medium (0.7-0.9)**: Partial name match, same organization
- **Low (<0.7)**: Topical relation only

## Next Step
After completing RESEARCH, proceed to DECIDE.
