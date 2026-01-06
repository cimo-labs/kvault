# Agents Module

LLM-powered agents for entity extraction and reconciliation.

## Overview

Agents handle the intelligent processing phases:

| Agent | Input | Output | Purpose |
|-------|-------|--------|---------|
| **ExtractionAgent** | Raw items | ExtractedEntity[] | Extract entities from text |
| **ResearchAgent** | ExtractedEntity | MatchCandidate[] | Find existing matches |
| **DecisionAgent** | Entity + Candidates | ReconcileDecision | Decide action |

## Files

```
agents/
├── __init__.py      # Exports all agents and data models
├── base.py          # Data models: ExtractedEntity, ReconcileDecision
├── extraction.py    # LLM entity extraction
├── research.py      # Matching strategy integration
└── decision.py      # Auto-decide + LLM reconciliation
```

## Data Models

### ExtractedEntity

```python
@dataclass
class ExtractedEntity:
    name: str                    # "Acme Corporation"
    entity_type: str             # "customer"
    tier: Optional[str]          # "strategic"
    industry: Optional[str]      # "robotics"
    contacts: list[dict]         # [{name, email, role}]
    confidence: float            # 0.0-1.0
    source_id: Optional[str]     # "email_001"
    raw_data: dict               # Original extraction data
```

### ReconcileDecision

```python
@dataclass
class ReconcileDecision:
    entity_name: str             # Entity being reconciled
    action: str                  # "merge" | "update" | "create"
    target_path: Optional[str]   # Target for merge/update
    confidence: float            # Decision confidence
    reasoning: str               # Explanation
    needs_review: bool           # Queue for human review?
    source_entity: ExtractedEntity
    candidates: list[MatchCandidate]
```

## ExtractionAgent

Extracts structured entities from unstructured text using LLM:

```python
from kgraph.pipeline.agents import ExtractionAgent

agent = ExtractionAgent(config)
entities = agent.extract([
    {"id": "email_001", "body": "Hi, I'm John from Acme Corp..."}
])

for entity in entities:
    print(f"{entity.name} ({entity.entity_type})")
```

### MockExtractionAgent

For testing without LLM:

```python
from kgraph.pipeline.agents import MockExtractionAgent

agent = MockExtractionAgent(config, mock_entities=[
    {"name": "Test Corp", "entity_type": "customer"}
])
```

## ResearchAgent

Finds potential matches using configured strategies:

```python
from kgraph.pipeline.agents import ResearchAgent

agent = ResearchAgent(config, storage)

entity = ExtractedEntity(name="Acme Corp", entity_type="customer")
candidates = agent.research(entity)

for candidate in candidates:
    print(f"{candidate.candidate_name}: {candidate.match_score}")
```

## DecisionAgent

Makes reconciliation decisions based on confidence thresholds:

```python
from kgraph.pipeline.agents import DecisionAgent

agent = DecisionAgent(config)

decision = agent.decide(entity, candidates, use_llm=True)
print(f"Action: {decision.action}")
print(f"Confidence: {decision.confidence}")
print(f"Needs review: {decision.needs_review}")
```

### Auto-Decide Rules

| Condition | Action | Review |
|-----------|--------|--------|
| Alias match (1.0) | MERGE | No |
| Score >= 0.95 | MERGE | No |
| Email domain >= 0.90 | UPDATE | No |
| Score < 0.50 | CREATE | No |
| Score 0.50-0.95 | LLM decides | Maybe |

## AgentContext

Shared context for agent invocations:

```python
@dataclass
class AgentContext:
    session_id: str
    batch_id: str
    config: KGraphConfig
    prompts_path: Optional[Path]
```
