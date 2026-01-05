# Entity Extraction Prompt

You are analyzing source data to extract entities for a knowledge graph.

## Your Task

For each item in the input, extract:
1. **Entities** - People, organizations, projects, or other key objects
2. **Relationships** - How entities relate to each other
3. **Attributes** - Key facts about each entity
4. **Signals** - Important indicators (priorities, risks, opportunities)

## Output Format

Return valid JSON with this structure:

```json
{
  "entities": [
    {
      "name": "Entity Name",
      "type": "entity_type",
      "tier": "high|medium|low",
      "confidence": 0.0-1.0,
      "is_new": true|false,
      "attributes": {
        "status": "active",
        "priority": 8,
        "description": "Brief description"
      },
      "contacts": [
        {"name": "Contact Name", "email": "email@domain.com", "role": "Role"}
      ],
      "source_refs": ["reference to source item"]
    }
  ],
  "relationships": [
    {
      "from": "Entity A",
      "to": "Entity B",
      "type": "relationship_type",
      "confidence": 0.0-1.0
    }
  ],
  "signals": [
    {
      "type": "opportunity|risk|action_item",
      "entity": "Related Entity",
      "description": "What was detected",
      "priority": 1-10
    }
  ]
}
```

## Guidelines

1. **Confidence Scoring**
   - 0.9+ : Explicit mention with clear context
   - 0.7-0.9 : Implied or partial information
   - 0.5-0.7 : Requires inference, may need verification
   - <0.5 : Low confidence, flag for review

2. **Entity Deduplication**
   - Set `is_new: false` if entity likely exists already
   - Include enough detail to match against existing entities
   - Use canonical names when known

3. **Tier Assignment**
   - High: Critical importance, high priority, frequent interaction
   - Medium: Regular importance, moderate priority
   - Low: Background, low priority, infrequent

## Input

{{INPUT}}
