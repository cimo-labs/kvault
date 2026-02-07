"""
Filesystem-based entity search — no SQLite, no index to rebuild.

Walks the KB directory, parses YAML frontmatter, and scores matches
using fuzzy string matching + keyword overlap. At typical KB sizes
(< 1000 entities) this is fast enough (< 200ms) and eliminates the
stale-index problem entirely.

Replaces: EntityIndex (core/index.py) + find_by_alias + find_by_email_domain
"""

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

from kvault.core.frontmatter import parse_frontmatter


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """A single search result."""
    path: str           # relative to kg_root, e.g. "people/friends/alice_smith"
    name: str           # display name
    aliases: list       # all aliases (strings)
    category: str       # top-level directory
    email_domains: list # extracted from aliases
    score: float = 0.0  # relevance score (0-1)
    match_reason: str = ""  # why this matched


@dataclass
class EntityRecord:
    """Parsed entity from disk — cheap to build, cached per search call."""
    path: str
    name: str
    aliases: List[str]
    category: str
    email_domains: List[str]
    content: str  # raw markdown body (after frontmatter)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_entities(kg_root: Path) -> List[EntityRecord]:
    """Walk the KB and parse every entity.

    An entity is a directory containing _summary.md with YAML frontmatter,
    at depth >= 2 from kg_root (i.e. category/entity at minimum).

    Returns list of EntityRecord. Cheap at < 1000 entities.
    """
    kg_root = Path(kg_root)
    entities: List[EntityRecord] = []

    for summary_path in kg_root.rglob("_summary.md"):
        entity_dir = summary_path.parent
        rel_path = entity_dir.relative_to(kg_root)

        # Skip hidden dirs (check relative path parts, not absolute)
        if any(part.startswith(".") for part in rel_path.parts):
            continue

        # Must be at least 2 levels deep (category/entity)
        if len(rel_path.parts) < 2:
            continue

        try:
            content = summary_path.read_text()
        except OSError:
            continue

        meta, body = parse_frontmatter(content)
        if not meta:
            # Check for legacy _meta.json
            import json
            meta_path = entity_dir / "_meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
            else:
                continue

        # Extract aliases (coerce non-strings)
        aliases = [str(a) for a in meta.get("aliases", []) if a is not None]

        # Add phone/email from dedicated fields
        for extra_field in ("phone", "email"):
            val = meta.get(extra_field)
            if val and str(val) not in aliases:
                aliases.append(str(val))

        # Derive display name
        name = meta.get("name") or meta.get("topic")
        if not name and aliases:
            for a in aliases:
                if isinstance(a, str) and "@" not in a and not a.startswith("+") and not a.isdigit():
                    name = a
                    break
            if not name:
                name = str(aliases[0])
        if not name:
            name = entity_dir.name

        # Extract email domains
        email_domains = []
        for a in aliases:
            if "@" in a:
                domain = a.split("@")[1].lower()
                if domain not in email_domains:
                    email_domains.append(domain)

        category = rel_path.parts[0]

        entities.append(EntityRecord(
            path=str(rel_path),
            name=name,
            aliases=aliases,
            category=category,
            email_domains=email_domains,
            content=body,
        ))

    return entities


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9@.\s]", "", text)
    return " ".join(text.split())


def _tokenize(text: str) -> set:
    """Tokenize normalized text, stripping trailing punctuation from each token."""
    return {t.strip(".") for t in _normalize(text).split() if t.strip(".")}


def _fuzzy_score(a: str, b: str) -> float:
    """SequenceMatcher ratio on normalized strings."""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _is_email(q: str) -> bool:
    """True for real emails like alice@acme.com, not @acme.com."""
    if q.startswith("@"):
        return False
    return "@" in q and "." in q.split("@")[-1]


def _is_domain_query(q: str) -> bool:
    """Matches @domain.com or bare domain.com patterns."""
    return q.startswith("@") or (
        "." in q and "@" not in q and not " " in q and len(q.split(".")) >= 2
    )


# ---------------------------------------------------------------------------
# Unified search
# ---------------------------------------------------------------------------

GENERIC_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "mail.com", "protonmail.com", "live.com", "msn.com",
    "ymail.com",
}


def search(
    kg_root: Path,
    query: str,
    category: Optional[str] = None,
    limit: int = 10,
    *,
    _entities: Optional[List[EntityRecord]] = None,
) -> List[SearchResult]:
    """Unified search: auto-detects query type and fans out.

    Query types (auto-detected):
      - Email address  → exact alias match + domain match
      - @domain.com    → domain match
      - Anything else  → fuzzy name/alias match + content keyword match

    Args:
        kg_root: Root of the knowledge base.
        query: Free-form query string.
        category: Optional category filter (e.g. "people").
        limit: Max results to return.
        _entities: Pre-scanned entities (for testing / batch calls).

    Returns:
        List of SearchResult sorted by score descending.
    """
    entities = _entities or scan_entities(kg_root)

    if category:
        entities = [e for e in entities if e.category == category]

    query = query.strip()
    if not query:
        return []

    scored: List[SearchResult] = []

    if _is_email(query):
        scored = _search_email(entities, query)
    elif _is_domain_query(query):
        domain = query.lstrip("@").lower()
        scored = _search_domain(entities, domain)
    else:
        scored = _search_text(entities, query)

    # Sort by score desc, take top-k
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:limit]


def find_by_alias(
    kg_root: Path,
    alias: str,
    *,
    _entities: Optional[List[EntityRecord]] = None,
) -> Optional[SearchResult]:
    """Exact alias lookup (case-insensitive). Convenience wrapper.

    Kept as a thin helper because callers sometimes want a definitive
    yes/no rather than a ranked list.
    """
    entities = _entities or scan_entities(kg_root)
    alias_lower = _normalize(alias)

    for e in entities:
        for a in e.aliases:
            if _normalize(a) == alias_lower:
                return _to_result(e, 1.0, "exact_alias")
        if _normalize(e.name) == alias_lower:
            return _to_result(e, 1.0, "exact_name")

    return None


def find_by_email_domain(
    kg_root: Path,
    domain: str,
    *,
    _entities: Optional[List[EntityRecord]] = None,
) -> List[SearchResult]:
    """Find all entities with a given email domain. Convenience wrapper."""
    entities = _entities or scan_entities(kg_root)
    return _search_domain(entities, domain.lower())


def count_entities(
    kg_root: Path,
    category: Optional[str] = None,
    *,
    _entities: Optional[List[EntityRecord]] = None,
) -> int:
    """Count entities, optionally filtered by category."""
    entities = _entities or scan_entities(kg_root)
    if category:
        entities = [e for e in entities if e.category == category]
    return len(entities)


def list_entities(
    kg_root: Path,
    category: Optional[str] = None,
    *,
    _entities: Optional[List[EntityRecord]] = None,
) -> List[SearchResult]:
    """List all entities (optionally filtered). Returns them sorted by name."""
    entities = _entities or scan_entities(kg_root)
    if category:
        entities = [e for e in entities if e.category == category]
    results = [_to_result(e, 0.0, "list") for e in entities]
    results.sort(key=lambda r: r.name.lower())
    return results


# ---------------------------------------------------------------------------
# Internal scoring
# ---------------------------------------------------------------------------

def _to_result(e: EntityRecord, score: float, reason: str) -> SearchResult:
    return SearchResult(
        path=e.path,
        name=e.name,
        aliases=e.aliases,
        category=e.category,
        email_domains=e.email_domains,
        score=score,
        match_reason=reason,
    )


def _search_email(entities: List[EntityRecord], email: str) -> List[SearchResult]:
    """Search by email: exact alias match first, then domain match."""
    results = []
    email_lower = email.lower()
    domain = email.split("@")[1].lower()

    for e in entities:
        # Exact alias match
        if any(a.lower() == email_lower for a in e.aliases):
            results.append(_to_result(e, 1.0, f"exact_alias:{email}"))
            continue

        # Domain match (lower score)
        if domain not in GENERIC_DOMAINS and domain in e.email_domains:
            results.append(_to_result(e, 0.7, f"email_domain:{domain}"))

    return results


def _search_domain(entities: List[EntityRecord], domain: str) -> List[SearchResult]:
    """Search by email domain."""
    results = []
    domain = domain.lower()

    for e in entities:
        if domain in e.email_domains:
            results.append(_to_result(e, 0.9, f"email_domain:{domain}"))

    return results


def _search_text(entities: List[EntityRecord], query: str) -> List[SearchResult]:
    """Fuzzy name/alias match + content keyword search."""
    results = []
    query_norm = _normalize(query)
    query_tokens = _tokenize(query)

    # Fuzzy matching threshold — higher bar avoids noise like "OpenAI" → "OpenClaw"
    FUZZY_THRESHOLD = 0.75

    for e in entities:
        best_score = 0.0
        best_reason = ""

        # 1. Exact substring in name/aliases (high confidence, check first)
        if query_norm in _normalize(e.name):
            sub_score = 0.85
            if sub_score > best_score:
                best_score = sub_score
                best_reason = "name_substring"

        for alias in e.aliases:
            if query_norm in _normalize(alias):
                sub_score = 0.85
                if sub_score > best_score:
                    best_score = sub_score
                    best_reason = f"alias_substring:{alias}"

        # 2. Fuzzy match against name (only if above threshold)
        name_score = _fuzzy_score(query, e.name)
        if name_score >= FUZZY_THRESHOLD and name_score > best_score:
            best_score = name_score
            best_reason = "name_fuzzy"

        # 3. Fuzzy match against each alias
        for alias in e.aliases:
            alias_score = _fuzzy_score(query, alias)
            if alias_score >= FUZZY_THRESHOLD and alias_score > best_score:
                best_score = alias_score
                best_reason = f"alias_fuzzy:{alias}"

        # 4. Content keyword overlap (weaker signal, only if nothing better)
        if best_score < 0.5:
            content_tokens = _tokenize(e.content)
            overlap = query_tokens & content_tokens
            if overlap:
                content_score = 0.3 + 0.2 * (len(overlap) / len(query_tokens))
                if content_score > best_score:
                    best_score = content_score
                    best_reason = f"content_keyword:{','.join(overlap)}"

        # Threshold
        if best_score >= 0.3:
            results.append(_to_result(e, best_score, best_reason))

    return results
