# Entity Reconciliation Prompt

You are deciding how to handle a newly extracted entity that may match existing entities.

## Context

**Extracted Entity:**
{{EXTRACTED_ENTITY}}

**Candidate Matches:**
{{CANDIDATES}}

## Decision Options

1. **MERGE** - Same entity, combine data into the existing one
   - Use when: Names are variations of the same entity
   - Use when: Email domains match + similar names
   - Use when: Clear parent-child relationship

2. **UPDATE** - Different but related, add info to existing
   - Use when: New contact for existing organization
   - Use when: Additional information about known entity
   - Use when: Department/division of existing org

3. **CREATE** - Genuinely new entity
   - Use when: No good matches found
   - Use when: Clearly different despite name similarity
   - Use when: Different industry/type than candidates

## Output Format

```json
{
  "decision": "MERGE|UPDATE|CREATE",
  "target": "path/to/target/entity",  // for MERGE/UPDATE
  "confidence": 0.0-1.0,
  "reasoning": "Brief explanation of decision"
}
```

## Decision Guidelines

| Scenario | Decision |
|----------|----------|
| Alias exact match | MERGE |
| Fuzzy name > 0.95 | MERGE |
| Same email domain + fuzzy > 0.7 | MERGE |
| Same email domain, different division | UPDATE |
| Similar name but different industry | CREATE |
| No matches > 0.5 | CREATE |

Consider:
- Email domains are strong signals for organization identity
- Fuzzy name matches may be coincidences
- Parent-child relationships (e.g., "Acme Corp" and "Acme Labs" may be same)
- Geographic qualifiers may indicate different entities ("Acme US" vs "Acme EU")
