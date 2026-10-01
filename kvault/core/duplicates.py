"""KB-wide duplicates: the same thing filed in two places (``DUPLICATE:``).

``SIBLINGS`` compares names under one parent. The duplicates that survive it
live in different folders, often under different names: one supplier filed
under ``manufacturing/`` and again under ``services/``, a topic under two
roots, a company under its full name and again under its acronym. Before
0.16 the only cross-folder rule (the same directory name at two depths) was
reported last under ``SIBLINGS``, where on a large KB the output cap hid it.

Signals, computed in one pass over the node summaries:

- ``same_name``: the same directory name at more than one place.
  Alphabetical buckets (``a_m``) and facet layouts (one name under sibling
  parents, ``customers/{key,standard}/oem``) are layouts, not twins, and so
  is a homonym: each title adds its own, different words to the name.
- ``same_title``: identical title words (stemmed), at least two of them.
- ``shared_alias``: an alias on both nodes. On its own it takes two shared
  aliases, a shared identifier (an email address or phone number), or mutual
  naming (each node's title is an alias of the other), and neither node may
  be a dated record: a review note *about* a customer carries the customer's
  names as aliases. Otherwise a shared alias is only supporting evidence.
- ``similar_body``: 5-word shingles compared through an index of the
  shingles that occur in at most ten nodes. Jaccard >= 0.5, or containment
  >= 0.8 (one body is mostly inside the other), for bodies of 40+ words.

Every reported pair also carries its *measured* overlap: shingle
``jaccard``/``containment`` and ``words``, the share of the smaller node's
content words found in the other (``None`` when a node is too short to
measure; before 0.16.1 an unmeasured value read 0.00). Model-written copies
are paraphrases, so shingles miss them while word overlap does not: on a
real KB, twins shared 83-100% of their words, while facets and homonyms
shared 18-37%. Word overlap is reported, not a signal: short templated
nodes about different companies reach 0.8.

The homonym rule ignores filler words that tools and agents append to
titles (``Category Summary``, ``Overview``, ``Project``,
``Architecture``): on a 1,000-node KB they made one subject's three nodes
look like three different things. A filler word still tells two same-name
nodes apart when their texts share under 40% of their words, since it is
sometimes the subject ("Search Project", a staffing plan, beside "Search
Architecture", a cluster design).

Never compared: a node with its own ancestors or descendants (a rollup
repeats its children by design), two members of one date series (``SERIES``
reports those), kvault's placeholder stubs, anything under ``journal/`` or
``deep_context/``, and pairs recorded as ``distinct_from``. A title or alias
shared by more than five nodes is a naming pattern, not a duplicate.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from kvault.core import decisions as dc
from kvault.core import structure as st
from kvault.core.frontmatter import parse_frontmatter

DUPLICATE_SIGNALS: Tuple[str, ...] = ("similar_body", "same_title", "shared_alias", "same_name")
MIN_BODY_WORDS = 40
BODY_JACCARD = 0.5
BODY_CONTAINMENT = 0.8
SHINGLE_WORDS = 5
MAX_SHINGLE_NODES = 10
MAX_GROUP = 5

#: Below this share of the smaller node's content words found in the other,
#: same-name nodes whose titles differ only by filler words are homonyms
#: (twins measured 0.83-1.0 on a real KB, facets and homonyms 0.18-0.37).
WORD_DIFFERENT = 0.4
#: A node with fewer distinct content words than this is too short to measure.
MIN_CONTENT_WORDS = 20
#: Title words that describe the kind of page, not the subject (stemmed);
#: the homonym rule ignores them.
FILLER_TITLE_WORDS = frozenset(
    {
        "category",
        "summary",
        "overview",
        "index",
        "hub",
        "note",
        "project",
        "architecture",
        "system",
        "program",
        "page",
        "home",
        "main",
        "info",
        "detail",
        "readme",
        "doc",
        "document",
    }
)
_CONTENT_STOP = frozenset(
    """the and for with that this from are was were been has have had not but all any can
    will would should could into onto over under about after before between during their
    there them they then than when where which while who whom whose what why how its our
    out off own per via etc also more most much many some such each every other only same
    very just like one two three new use used using based within without across upon both
    either neither nor yet still even ever once here these those your you his her him she
    may might must shall does did doing done being""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)
_PHONE_RE = re.compile(r"^\+?[\d\s().\-]{10,}$")
_NOT_PHONE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{1,3}(?:\.\d{1,3}){3}$|^\d{4}\s*[-–]\s*\d{4}$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def _norm(text: str) -> str:
    return " ".join(_tokens(text))


def _content_words(text: str) -> FrozenSet[str]:
    return frozenset(
        t for t in _tokens(text) if len(t) >= 3 and not t.isdigit() and t not in _CONTENT_STOP
    )


def _core(stems: FrozenSet[str]) -> FrozenSet[str]:
    """Title stems without the filler words that name the kind of page."""
    return frozenset(s for s in stems if s not in FILLER_TITLE_WORDS)


@dataclass
class _Doc:
    path: str
    title: str
    title_stems: FrozenSet[str]
    title_key: FrozenSet[str]
    title_norm: str
    aliases: Set[str] = field(default_factory=set)
    identifiers: Set[str] = field(default_factory=set)
    shingles: Set[int] = field(default_factory=set)
    words: int = 0
    content: FrozenSet[str] = frozenset()


def _title(meta: Dict[str, Any], body: str, name: str) -> str:
    for key in ("name", "title", "topic"):
        value = meta.get(key)
        if value:
            return str(value)
    match = _HEADING_RE.search(body)
    if match:
        return match.group(1).strip()
    return name.replace("_", " ")


def _identifier(alias: str) -> Optional[str]:
    alias = alias.strip()
    if _EMAIL_RE.match(alias):
        return alias.lower()
    if _PHONE_RE.match(alias) and not _NOT_PHONE_RE.match(alias):
        digits = re.sub(r"\D", "", alias)
        return digits if 10 <= len(digits) <= 15 else None
    return None


def _load(root: Path, ignore: Sequence[str]) -> Dict[str, _Doc]:
    docs: Dict[str, _Doc] = {}
    for d in st.walk_dirs(root, ignore):  # managed dirs: no reserved, hidden, or ignored subtrees
        if not st.has_summary(d) or not st.inside_root(d / st.SUMMARY_NAME, root):
            continue
        try:
            raw = (d / st.SUMMARY_NAME).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = parse_frontmatter(raw)
        meta = meta or {}
        if meta.get("source") == "kvault-stub":
            continue
        rel = st.rel(root, d)
        title = _title(meta, body if meta else raw, d.name)
        stems = frozenset(st.stem(t) for t in _tokens(title))
        # A title needs two words that are not dates to identify anything:
        # two notes titled "2026-05-13" are the same day, not the same thing.
        topical = [t for t in stems if not st.is_date_token(t)]
        doc = _Doc(
            path=rel,
            title=title,
            title_stems=stems,
            title_key=stems if len(topical) >= 2 else frozenset(),
            title_norm=_norm(title),
        )
        raw_aliases = meta.get("aliases")
        for alias in raw_aliases if isinstance(raw_aliases, list) else []:
            if alias is None:
                continue
            ident = _identifier(str(alias))
            if ident:
                doc.identifiers.add(ident)
                continue
            norm = _norm(str(alias))
            if len(norm) >= 3:
                doc.aliases.add(norm)
        text = body if meta else raw
        words = _tokens(text)
        doc.words = len(words)
        doc.content = _content_words(text)
        if doc.words >= MIN_BODY_WORDS:
            doc.shingles = {
                zlib.crc32(" ".join(words[i : i + SHINGLE_WORDS]).encode())
                for i in range(len(words) - SHINGLE_WORDS + 1)
            }
        docs[rel] = doc
    return docs


def _homonyms(a: _Doc, b: _Doc, filler: bool = False) -> bool:
    """Each title adds its own, different words to the shared name.

    Filler words count only with *filler*: "Search Project" and "Search
    Architecture" are one thing unless their texts say otherwise.
    """
    name = frozenset(st.stem(t) for t in _tokens(a.path.rsplit("/", 1)[-1]))
    words_a = a.title_stems if filler else _core(a.title_stems)
    words_b = b.title_stems if filler else _core(b.title_stems)
    extra_a, extra_b = words_a - name, words_b - name
    return bool(extra_a and extra_b and not extra_a & extra_b)


def _word_overlap(a: _Doc, b: _Doc) -> Optional[float]:
    """Share of the smaller node's content words found in the other, or None."""
    small = min(len(a.content), len(b.content))
    if small < MIN_CONTENT_WORDS:
        return None
    return round(len(a.content & b.content) / small, 2)


def _shingle_overlap(a: _Doc, b: _Doc) -> Tuple[Optional[float], Optional[float]]:
    if not a.shingles or not b.shingles:
        return None, None
    shared = len(a.shingles & b.shingles)
    return (
        round(shared / len(a.shingles | b.shingles), 2),
        round(shared / min(len(a.shingles), len(b.shingles)), 2),
    )


def _related(a: str, b: str) -> bool:
    return a.startswith(b + "/") or b.startswith(a + "/")


@dataclass
class _Pair:
    same_name: bool = False
    same_title: bool = False
    shared_aliases: Set[str] = field(default_factory=set)
    identifier: bool = False
    jaccard: float = 0.0
    containment: float = 0.0


def _grouped(index: Dict[Any, List[str]]) -> Iterable[Tuple[Any, List[str]]]:
    for key, paths in index.items():
        unique = sorted(set(paths))
        if 2 <= len(unique) <= MAX_GROUP:
            yield key, unique


def duplicate_pairs(kg_root: Path, ignore: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Pairs of nodes that look like one thing filed twice, strongest first."""
    root = Path(kg_root)
    patterns = list(ignore) if ignore is not None else st.load_ignore(root)
    docs = _load(root, patterns)
    pairs: Dict[Tuple[str, str], _Pair] = {}

    def pair(a: str, b: str) -> _Pair:
        key = (a, b) if a < b else (b, a)
        return pairs.setdefault(key, _Pair())

    for name, paths in st.basename_duplicates(root, patterns).items():
        # Ghost directories and kvault's stubs are GHOST:/SUMMARY: work, not
        # twins: a pair needs two nodes an agent can read, mark, and move.
        paths = [p for p in paths if p in docs]
        if st.is_bucket_name(name) or len(paths) > MAX_GROUP:
            continue
        for a, b in combinations(sorted(paths), 2):
            # judged per pair: projects/models and tech/models are a facet
            # layout even when a third "models" sits one level deeper
            if not st.is_facet_layout([a, b]):
                pair(a, b).same_name = True

    by_title: Dict[FrozenSet[str], List[str]] = {}
    by_alias: Dict[str, List[str]] = {}
    by_ident: Dict[str, List[str]] = {}
    for doc in docs.values():
        if doc.title_key:
            by_title.setdefault(doc.title_key, []).append(doc.path)
        for alias in doc.aliases:
            by_alias.setdefault(alias, []).append(doc.path)
        for ident in doc.identifiers:
            by_ident.setdefault(ident, []).append(doc.path)
    for _, paths in _grouped(by_title):
        for a, b in combinations(paths, 2):
            pair(a, b).same_title = True
    for alias, paths in _grouped(by_alias):
        for a, b in combinations(paths, 2):
            pair(a, b).shared_aliases.add(alias)
    for _, paths in _grouped(by_ident):
        for a, b in combinations(paths, 2):
            pair(a, b).identifier = True

    index: Dict[int, List[str]] = {}
    for doc in docs.values():
        for shingle in doc.shingles:
            index.setdefault(shingle, []).append(doc.path)
    shared: Dict[Tuple[str, str], int] = {}
    for paths in index.values():
        if 2 <= len(paths) <= MAX_SHINGLE_NODES:
            for a, b in combinations(sorted(paths), 2):
                shared[(a, b)] = shared.get((a, b), 0) + 1
    for (a, b), count in shared.items():
        sa, sb = docs[a].shingles, docs[b].shingles
        jaccard = count / len(sa | sb)
        containment = count / min(len(sa), len(sb))
        if jaccard >= BODY_JACCARD or containment >= BODY_CONTAINMENT:
            p = pair(a, b)
            p.jaccard, p.containment = round(jaccard, 2), round(containment, 2)

    out: List[Dict[str, Any]] = []
    for (a, b), p in pairs.items():
        name_a, name_b = a.rsplit("/", 1)[-1], b.rsplit("/", 1)[-1]
        # Two members of one date series differ by their dates; the same name
        # twice (acme_meeting_2026_05_13 in two folders) is a twin, not a series.
        if _related(a, b) or (name_a != name_b and st.same_series(name_a, name_b)):
            continue
        da, db = docs.get(a), docs.get(b)
        mutual = bool(da and db and da.title_norm in db.aliases and db.title_norm in da.aliases)
        body = p.jaccard >= BODY_JACCARD or p.containment >= BODY_CONTAINMENT
        words = _word_overlap(da, db) if da and db else None
        # A dated record (meeting note, review, daily card) carries the names
        # of what it is about; sharing them is not evidence of being it.
        dated = st.series_key(a.rsplit("/", 1)[-1])[1] or st.series_key(b.rsplit("/", 1)[-1])[1]
        strong_alias = not dated and (p.identifier or len(p.shared_aliases) >= 2 or mutual)
        # One directory name for two things is a homonym when each title adds
        # its own words beyond the name and those words differ ("Standard
        # Customers" vs "Other Industrial Suppliers — Standard"). Identical or
        # bare titles ("Models" twice) stay: that is the split-brain case.
        # A name made only of dates (2026_05_13) names a day, not a thing.
        date_only = not st.series_key(name_a)[0]
        # Filler words ("Acme Project" / "Acme Architecture") make no homonym,
        # unless the texts share under 40% of their words: then the filler
        # word is the subject ("Search Project" vs "Search Architecture").
        differ = words is not None and words < WORD_DIFFERENT
        homonym = bool(
            da and db and (_homonyms(da, db) or (differ and _homonyms(da, db, filler=True)))
        )
        same_name = p.same_name and not date_only and not homonym
        signals: List[str] = []
        if body:
            signals.append("similar_body")
        if p.same_title:
            signals.append("same_title")
        if p.shared_aliases or p.identifier:
            signals.append("shared_alias")
        if same_name:
            signals.append("same_name")
        if not (body or p.same_title or same_name or strong_alias):
            continue  # shared names alone: supporting evidence only
        if dc.are_distinct(root, a, b):
            continue
        measured_j, measured_c = _shingle_overlap(da, db) if da and db else (None, None)
        strength = (
            3.0 * max(measured_j or 0.0, (measured_c or 0.0) * 0.9)
            + (2.0 if p.same_title else 0.0)
            + (1.5 if strong_alias else 0.5 if p.shared_aliases else 0.0)
            + (1.0 if same_name else 0.0)
            + (words or 0.0)
        )
        out.append(
            {
                "a": a,
                "b": b,
                "signals": signals,
                "titles": [da.title if da else "", db.title if db else ""],
                "shared_aliases": sorted(p.shared_aliases)[:5],
                "shared_identifier": p.identifier,
                # measured for every pair; None = too short to measure
                "jaccard": measured_j,
                "containment": measured_c,
                "words": words,
                "strength": round(strength, 2),
            }
        )
    out.sort(key=lambda d: (-d["strength"], d["a"], d["b"]))
    return out


def describe(pair: Dict[str, Any]) -> str:
    """One line of evidence: ``same title «X»; body 99% alike; 2 shared aliases``."""
    parts: List[str] = []
    ta, tb = pair["titles"]
    if "similar_body" in pair["signals"]:
        if (pair["jaccard"] or 0.0) >= BODY_JACCARD:
            parts.append(f"body {round(pair['jaccard'] * 100)}% alike")
        else:
            parts.append(
                f"{round((pair['containment'] or 0.0) * 100)}% of one body is in the other"
            )
    if "same_title" in pair["signals"]:
        if _norm(ta) == _norm(tb):
            parts.append(f"same title «{ta[:40]}»")
        else:
            parts.append(f"titles alike «{ta[:35]}» / «{tb[:35]}»")
    if "shared_alias" in pair["signals"]:
        if pair["shared_identifier"]:
            parts.append("shared identifier")
        aliases = pair["shared_aliases"]
        if len(aliases) == 1:
            parts.append(f"shared alias «{aliases[0][:40]}»")
        elif aliases:
            parts.append(f"{len(aliases)} shared aliases")
    if "same_name" in pair["signals"]:
        titles = f" («{ta[:30]}» / «{tb[:30]}»)" if ta or tb else ""
        parts.append(f"same name{titles}")
    if pair.get("words") is not None:
        parts.append(f"{round(pair['words'] * 100)}% of words shared")
    return "; ".join(parts)


__all__ = [
    "DUPLICATE_SIGNALS",
    "MIN_BODY_WORDS",
    "BODY_JACCARD",
    "BODY_CONTAINMENT",
    "MAX_GROUP",
    "WORD_DIFFERENT",
    "FILLER_TITLE_WORDS",
    "duplicate_pairs",
    "describe",
]
