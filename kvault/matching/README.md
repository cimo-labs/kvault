# Matching Module

Entity matching strategies for deduplication.

## Overview

Matching strategies find potential duplicates when processing new entities. Each strategy produces candidates with confidence scores used for reconciliation decisions.

## Available Strategies

| Strategy | Score Range | Use Case |
|----------|-------------|----------|
| `alias` | 1.0 | Exact match against known aliases |
| `fuzzy_name` | 0.85-0.99 | Fuzzy string matching on names |
| `email_domain` | 0.85-0.95 | Match by shared email domains |

## Usage

```python
from kvault.matching import load_strategies, MatchCandidate, EntityIndexEntry

# Load strategies from config
strategies = load_strategies(['alias', 'fuzzy_name', 'email_domain'],
                             threshold=0.85)

# Build entity index
index = {
    "acme_corp": EntityIndexEntry(
        id="acme_corp",
        name="Acme Corporation",
        entity_type="customer",
        aliases=["Acme Corp", "ACME"],
        email_domains=["acme.com", "acmecorp.com"],
    )
}

# Find matches
entity = {"name": "ACME Corp", "contacts": [{"email": "john@acme.com"}]}
for strategy in strategies:
    candidates = strategy.find_matches(entity, index, threshold=0.85)
    for c in candidates:
        print(f"{c.candidate_name}: {c.match_score} ({c.match_type})")
```

## Files

```
matching/
├── __init__.py   # Exports and strategy registration
├── base.py       # Abstract base class, MatchCandidate, EntityIndexEntry
├── alias.py      # Exact alias matching
├── fuzzy.py      # Fuzzy string matching (SequenceMatcher)
└── domain.py     # Email domain matching
```

## Strategy Interface

```python
from kvault.matching import MatchStrategy, register_strategy

@register_strategy("custom")
class CustomMatchStrategy(MatchStrategy):
    @property
    def name(self) -> str:
        return "custom"

    @property
    def score_range(self) -> tuple[float, float]:
        return (0.7, 0.95)

    def find_matches(self, entity, index, threshold=0.0) -> list[MatchCandidate]:
        candidates = []
        for entry_id, entry in index.items():
            score = self._compute_score(entity, entry)
            if score >= threshold:
                candidates.append(MatchCandidate(
                    candidate_id=entry_id,
                    candidate_name=entry.name,
                    candidate_path=entry.path,
                    match_type=self.name,
                    match_score=score,
                ))
        return sorted(candidates, key=lambda c: c.match_score, reverse=True)
```

## Data Models

### MatchCandidate

```python
@dataclass
class MatchCandidate:
    candidate_id: str       # Entity ID
    candidate_name: str     # Display name
    candidate_path: str     # Path in knowledge graph
    match_type: str         # Strategy name
    match_score: float      # 0.0 to 1.0
    match_details: dict     # Strategy-specific details
```

### EntityIndexEntry

```python
@dataclass
class EntityIndexEntry:
    id: str                 # Normalized entity ID
    name: str               # Display name
    entity_type: str        # customer, supplier, etc.
    tier: Optional[str]     # strategic, key, standard, prospect
    path: str               # Full path
    aliases: list[str]      # Known aliases
    email_domains: list[str]  # Contact email domains
    industry: Optional[str]
    contacts: list[dict]
    extra: dict             # Strategy-specific additional data
```

## Normalization

The fuzzy matcher normalizes names before comparison:

```python
"Acme Corporation"  → "acme"
"R&L Carriers Inc." → "rl carriers"
"Universal Robots A/S" → "universal robots as"
```
