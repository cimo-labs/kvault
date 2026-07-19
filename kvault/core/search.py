"""Structured lexical search for kvault nodes.

The default backend is intentionally stateless and file-native: scan visible
``_summary.md`` files, score fielded lexical matches, and return node hits.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from kvault.core.frontmatter import parse_frontmatter
from kvault.core.paths import PathSafetyError, resolve_within_root

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_H_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_SNIPPET_MAX_CHARS = 440


@dataclass(frozen=True)
class SearchDocument:
    """A searchable kvault node summary."""

    path: str
    kind: str
    title: str
    aliases: List[str]
    headings: List[str]
    content: str
    summary_path: str
    last_updated: str


@dataclass(frozen=True)
class SearchResult:
    """A ranked search hit."""

    path: str
    kind: str
    title: str
    score: float
    matched_fields: List[str]
    snippet: str
    summary_path: str
    last_updated: str
    content: Optional[str] = None
    content_truncated: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "path": self.path,
            "kind": self.kind,
            "title": self.title,
            "score": self.score,
            "matched_fields": self.matched_fields,
            "snippet": self.snippet,
            "summary_path": self.summary_path,
            "last_updated": self.last_updated,
        }
        if self.content is not None:
            data["content"] = self.content
            data["content_truncated"] = bool(self.content_truncated)
        return data


def search_nodes(
    kg_root: Path,
    query: str,
    limit: int = 10,
    include_content: bool = False,
    content_max_chars: int = 6000,
    total_max_chars: int = 20000,
) -> Dict[str, Any]:
    """Search visible kvault nodes and return ranked results."""
    query = query.strip()
    if not query:
        return {"query": query, "count": 0, "results": []}

    documents = scan_search_documents(kg_root)
    query_tokens = _tokens(query)
    if not query_tokens:
        return {"query": query, "count": 0, "results": []}

    idf = _idf(documents, query_tokens)
    scored: List[Tuple[float, SearchDocument, Set[str]]] = []
    for doc in documents:
        score, matched_fields = _score_document(doc, query, query_tokens, idf)
        if score > 0:
            scored.append((score, doc, matched_fields))

    scored.sort(key=lambda item: (-item[0], item[1].path.count("/"), item[1].path))
    results: List[SearchResult] = []
    remaining_total = max(0, total_max_chars)
    for score, doc, matched_fields in scored[: max(limit, 0)]:
        content: Optional[str] = None
        truncated: Optional[bool] = None
        if include_content:
            cap = min(max(0, content_max_chars), remaining_total)
            content, truncated = _truncate(doc.content, cap)
            remaining_total -= len(content)
        results.append(
            SearchResult(
                path=doc.path,
                kind=doc.kind,
                title=doc.title,
                score=round(score, 3),
                matched_fields=sorted(matched_fields),
                snippet=_snippet(doc, query, query_tokens),
                summary_path=doc.summary_path,
                last_updated=doc.last_updated,
                content=content,
                content_truncated=truncated,
            )
        )

    return {
        "query": query,
        "count": len(results),
        "results": [result.to_dict() for result in results],
    }


def scan_search_documents(kg_root: Path) -> List[SearchDocument]:
    """Return searchable documents for every visible ``_summary.md`` node."""
    kg_root = Path(kg_root).resolve()
    documents: List[SearchDocument] = []
    for summary_path in sorted(kg_root.rglob("_summary.md")):
        try:
            rel_summary = summary_path.relative_to(kg_root)
            summary_path = resolve_within_root(
                kg_root,
                rel_summary,
                allow_root=False,
                must_exist=True,
                reject_symlinks=True,
            )
        except (PathSafetyError, ValueError):
            continue
        if _is_hidden(rel_summary.parts) or not summary_path.is_file() or summary_path.is_symlink():
            continue

        node_path = (
            "." if summary_path.parent == kg_root else str(summary_path.parent.relative_to(kg_root))
        )
        try:
            raw = summary_path.read_text()
        except OSError:
            continue
        meta, body = parse_frontmatter(raw)
        content = body if meta else raw
        title = _title(node_path, meta, content)
        documents.append(
            SearchDocument(
                path=node_path,
                kind=_kind(kg_root, node_path),
                title=title,
                aliases=[str(alias) for alias in meta.get("aliases", []) if alias is not None],
                headings=_headings(content),
                content=content,
                summary_path=str(rel_summary),
                last_updated=_mtime_date(summary_path),
            )
        )
    return documents


def _score_document(
    doc: SearchDocument,
    query: str,
    query_tokens: Sequence[str],
    idf: Dict[str, float],
) -> Tuple[float, Set[str]]:
    query_norm = _normalize_text(query)
    path_norm = _normalize_text(doc.path.replace("/", " ").replace("_", " "))
    title_norm = _normalize_text(doc.title)
    aliases_norm = _normalize_text(" ".join(doc.aliases))
    headings_norm = _normalize_text(" ".join(doc.headings))
    body_norm = _normalize_text(doc.content)

    score = 0.0
    matched_fields: Set[str] = set()

    score += _phrase_score(query_norm, path_norm, "path", matched_fields, exact=80.0, contains=45.0)
    score += _phrase_score(
        query_norm, title_norm, "title", matched_fields, exact=75.0, contains=40.0
    )
    score += _phrase_score(
        query_norm, aliases_norm, "aliases", matched_fields, exact=70.0, contains=36.0
    )
    score += _phrase_score(
        query_norm, headings_norm, "headings", matched_fields, exact=32.0, contains=24.0
    )
    score += _phrase_score(query_norm, body_norm, "body", matched_fields, exact=0.0, contains=18.0)

    fields = {
        "path": (path_norm, 8.0),
        "title": (title_norm, 6.0),
        "aliases": (aliases_norm, 6.0),
        "headings": (headings_norm, 4.0),
        "body": (body_norm, 1.0),
    }
    for token in query_tokens:
        token_idf = idf.get(token, 1.0)
        for field_name, (field_text, weight) in fields.items():
            count = _tokens(field_text).count(token)
            if count:
                matched_fields.add(field_name)
                score += token_idf * weight * (count / (count + 1.2))

    return score, matched_fields


def _phrase_score(
    query: str,
    field_text: str,
    field_name: str,
    matched_fields: Set[str],
    exact: float,
    contains: float,
) -> float:
    if not query or not field_text:
        return 0.0
    if query == field_text and exact:
        matched_fields.add(field_name)
        return exact
    if query in field_text:
        matched_fields.add(field_name)
        return contains
    return 0.0


def _idf(documents: Sequence[SearchDocument], query_tokens: Sequence[str]) -> Dict[str, float]:
    n = max(len(documents), 1)
    values: Dict[str, float] = {}
    for token in set(query_tokens):
        df = 0
        for doc in documents:
            corpus = " ".join(
                [doc.path, doc.title, " ".join(doc.aliases), " ".join(doc.headings), doc.content]
            )
            if token in set(_tokens(corpus)):
                df += 1
        values[token] = math.log((n + 1) / (df + 1)) + 1.0
    return values


def _snippet(
    doc: SearchDocument,
    query: str,
    query_tokens: Sequence[str],
    max_chars: int = _SNIPPET_MAX_CHARS,
) -> str:
    text = re.sub(r"\s+", " ", doc.content).strip()
    if not text:
        return doc.title
    haystack = text.lower()
    needle = query.lower().strip()
    idx = haystack.find(needle) if needle else -1
    if idx < 0:
        token_positions = [
            haystack.find(token) for token in query_tokens if haystack.find(token) >= 0
        ]
        idx = min(token_positions) if token_positions else 0

    start = max(0, idx - max_chars // 3)
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet += "..."
    return snippet


def _truncate(text: str, max_chars: int) -> Tuple[str, bool]:
    if max_chars <= 0:
        return "", bool(text)
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _tokens(value: str) -> List[str]:
    return _TOKEN_RE.findall(value.lower())


def _normalize_text(value: str) -> str:
    return " ".join(_tokens(value))


def _headings(markdown: str) -> List[str]:
    return [match.group(1).strip() for match in _H_RE.finditer(markdown)]


def _title(path: str, meta: Dict[str, Any], content: str) -> str:
    for key in ("name", "title", "topic"):
        value = meta.get(key)
        if value:
            return str(value)
    headings = _headings(content)
    if headings:
        return headings[0]
    if path == ".":
        return "Root"
    return path.split("/")[-1].replace("_", " ").title()


def _kind(kg_root: Path, path: str) -> str:
    if path == ".":
        return "root"
    parts = Path(path).parts
    node_dir = kg_root / path
    has_child_nodes = any(
        child.is_dir() and not child.name.startswith(".") and (child / "_summary.md").exists()
        for child in _safe_iterdir(node_dir)
    )
    if len(parts) < 2 or has_child_nodes:
        return "category"
    return "entity"


def _safe_iterdir(path: Path) -> Iterable[Path]:
    try:
        return list(path.iterdir())
    except OSError:
        return []


def _is_hidden(parts: Sequence[str]) -> bool:
    return any(part.startswith(".") for part in parts)


def _mtime_date(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return ""


__all__ = [
    "SearchDocument",
    "SearchResult",
    "scan_search_documents",
    "search_nodes",
]
