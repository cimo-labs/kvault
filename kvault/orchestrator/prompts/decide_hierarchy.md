# DECIDE Step Instructions (Hierarchy Mode)

## Purpose
Analyze the raw input and produce an ActionPlan with 0..N changes to the knowledge hierarchy.

## Input Analysis

You receive raw, unstructured input. Your job is to:
1. Extract meaningful information from the content
2. Determine what entities/nodes in the hierarchy are affected
3. Plan specific actions for each affected path

## ActionPlan Output Format

Output a JSON ActionPlan:

```json
{
  "overall_reasoning": "Summary of what this input means and what changes are needed",
  "actions": [
    {
      "action_type": "create|update|delete|move|skip",
      "path": "category/entity_name",
      "reasoning": "Why this action is needed",
      "confidence": 0.95,
      "content": {
        "summary": "Content for _summary.md (create/update only)",
        "meta": {"key": "value for _meta.json (create only)"}
      },
      "target_path": "new/path (move only)"
    }
  ]
}
```

## Action Types

| Action | When to Use |
|--------|-------------|
| **create** | New entity that doesn't exist in hierarchy |
| **update** | Add information to existing entity |
| **delete** | Remove entity (rarely used) |
| **move** | Relocate entity to different path |
| **skip** | Explicitly note something not acted on |

## Decision Guidelines

### When to CREATE
- Entity is significant enough to warrant its own node
- Name is specific (not generic like "John" or "meeting")
- Information is substantive

### When to UPDATE
- Entity already exists in hierarchy
- New information adds value
- High confidence the match is correct (≥0.9)

### When to produce EMPTY plan
- Input is noise or not actionable
- Information is too vague to structure
- Content doesn't fit the knowledge graph scope

## Example: Single Entity Input

Input: "Coffee with Sarah Chen from Anthropic, discussed AI safety research"

```json
{
  "overall_reasoning": "Interaction with Sarah Chen at Anthropic, relevant for contact network",
  "actions": [
    {
      "action_type": "create",
      "path": "people/sarah_chen",
      "reasoning": "New contact, specific name and affiliation",
      "confidence": 0.95,
      "content": {
        "summary": "# Sarah Chen\n\n**Affiliation:** Anthropic\n**Context:** AI safety research\n\n## Interactions\n- 2026-01-23: Coffee meeting, discussed AI safety research",
        "meta": {"created": "2026-01-23", "sources": ["manual:2026-01-23"], "aliases": ["Sarah Chen"]}
      }
    }
  ]
}
```

## Example: Multi-Entity Input

Input: "Team call with Mike and Bryan. Mike shared CJE paper feedback. Bryan mentioned new startup idea."

```json
{
  "overall_reasoning": "Team call involving two people, both with substantive updates",
  "actions": [
    {
      "action_type": "update",
      "path": "people/mike_duboe",
      "reasoning": "Existing contact, add CJE feedback interaction",
      "confidence": 0.95,
      "content": {
        "summary": "## Interactions\n- 2026-01-23: Team call, shared CJE paper feedback"
      }
    },
    {
      "action_type": "update",
      "path": "people/bryan_bischof",
      "reasoning": "Existing contact, add startup idea interaction",
      "confidence": 0.95,
      "content": {
        "summary": "## Interactions\n- 2026-01-23: Team call, mentioned new startup idea"
      }
    }
  ]
}
```

## Example: Empty Plan

Input: "Nice weather today"

```json
{
  "overall_reasoning": "Input is not actionable - contains no structured information",
  "actions": []
}
```

## Next Step
- If actions.length > 0: proceed to EXECUTE
- If actions.length == 0: proceed to LOG (record the skip)
