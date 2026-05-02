"""In-memory entity search for the web UI."""

import time
from pathlib import Path
from typing import Dict, List, Tuple

from kvault.core.storage import EntityRecord, scan_entities

_SEARCH_CACHE_TTL_SECONDS = 3.0
_ENTITY_CACHE: Dict[str, Tuple[float, List[EntityRecord]]] = {}


def clear_search_cache() -> None:
    """Clear in-memory search index cache (used by tests)."""
    _ENTITY_CACHE.clear()


def _cached_entities(kg_root: Path) -> List[EntityRecord]:
    """Return entities with a short-lived in-memory cache."""
    root_key = str(Path(kg_root).resolve())
    now = time.monotonic()
    cached = _ENTITY_CACHE.get(root_key)
    if cached and (now - cached[0]) < _SEARCH_CACHE_TTL_SECONDS:
        return cached[1]

    entities = scan_entities(Path(root_key))
    _ENTITY_CACHE[root_key] = (now, entities)
    return entities


def search_entities(kg_root: Path, query: str, limit: int = 50) -> List[EntityRecord]:
    """Search entities by case-insensitive substring match.

    Matches against name, aliases, path, and content body.
    Returns at most *limit* results, sorted by relevance:
    name match > alias match > path match > content match.
    """
    if not query or not query.strip():
        return []

    q = query.strip().lower()
    entities = _cached_entities(kg_root)

    scored: List[Tuple[int, EntityRecord]] = []
    for entity in entities:
        score = _score(entity, q)
        if score > 0:
            scored.append((score, entity))

    # Deterministic tie-breakers help stable UI ordering.
    scored.sort(key=lambda pair: (-pair[0], pair[1].name.lower(), pair[1].path.lower()))
    return [entity for _, entity in scored[:limit]]


def _score(entity: EntityRecord, query: str) -> int:
    """Return relevance score (0 = no match)."""
    # Exact name match (highest priority)
    if query == entity.name.lower():
        return 100

    # Name contains query
    if query in entity.name.lower():
        return 80

    # Alias match
    for alias in entity.aliases:
        if query == alias.lower():
            return 70
        if query in alias.lower():
            return 60

    # Path match
    if query in entity.path.lower():
        return 40

    # Content match
    if query in entity.content.lower():
        return 20

    return 0
