"""Tree-shape rules shared by the write guards, ``check``, and ``plan``.

Everything here is lexical and deterministic: it looks at directory *names*
and at whether a ``_summary.md`` exists, never at summary bodies. That is
deliberate. The failure these rules exist for (2026-09 audit of a 1,045-node
KB: 119 flat children under ``projects/``, ``infra/`` beside
``infrastructure/`` beside ``tech/infrastructure/``, 23 root categories,
summary-less directories invisible to every surface) was produced by agents
that never saw the sibling they were duplicating. A name comparison at write
time is cheap enough to run on every create, and the same comparison in
``check`` means the guard and the audit can never disagree about what counts
as a collision.

Three rules keyed on names:

- ``same_words``: the two names have identical stemmed token sets
  (``ai_overview`` / ``ai_overviews``). A create is refused without
  ``allow_similar``.
- ``prefix``: one name's tokens are a prefix of the other's
  (``pdp_prompts`` / ``pdp_prompts_concord``), or two single-token names
  share a 4+ character prefix (``infra`` / ``infrastructure``). Warned.
- ``overlap``: stemmed token sets overlap at Jaccard >= 0.5
  (``code_reviews`` / ``reviews``). Warned.

Purely semantic pairs (``people`` / ``team``) are out of scope on purpose;
they stay a human call surfaced by ``plan`` as a question, not a move.

``.kvaultignore`` lets a KB declare directories and files that are not
nodes and are fine (a ``scripts/`` dir, a ``requirements.txt``). One
fnmatch pattern per line against the KB-relative path; a pattern naming a
directory ignores everything beneath it.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from kvault.core.conventions import BACKGROUND_CHILD_DIRS

IGNORE_FILE = ".kvaultignore"
SUMMARY_NAME = "_summary.md"
LEGACY_META_NAME = "_meta.json"

#: Directories that are never nodes and never counted as children.
RESERVED_DIRS: Tuple[str, ...] = ("journal",) + BACKGROUND_CHILD_DIRS
#: Files a node directory may hold besides its summary.
NODE_FILES: Tuple[str, ...] = (SUMMARY_NAME, LEGACY_META_NAME)
#: Files the KB root may hold in addition to NODE_FILES.
ROOT_FILES: Tuple[str, ...] = ("AGENTS.md", "README.md", "CLAUDE.md", IGNORE_FILE)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_JOURNAL_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

COLLISION_KINDS: Tuple[str, ...] = ("same_words", "prefix", "overlap")
_KIND_RANK = {kind: i for i, kind in enumerate(COLLISION_KINDS)}

# Tokens that carry a date or a time of day rather than a topic. Stripping
# them from a name gives its *series key*: the 40 daily "source boundary"
# cards on a real KB all collapse to one key, which is how a chronology that
# has been written as nodes gets recognised (and kept out of the sibling
# collision rules, where it produced 60 false pairs).
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
_TIME = (
    "morning|midday|noon|afternoon|earlyafternoon|evening|night|late|early|am|pm|weekday|weekend"
)
_DATE_TOKEN_RE = re.compile(
    rf"^(?:\d{{1,4}}|(?:{_MONTHS})\d{{0,2}}|(?:{_DAYS})|(?:{_TIME})|q[1-4]|w\d{{1,2}}|h[12])$"
)
#: Alphabetical bucket names (``a_m``, ``n_z``) that AGENTS.md itself recommends
#: for splitting a fat parent; the same bucket name under two branches is the
#: convention working, not a duplicate.
_BUCKET_RE = re.compile(r"^[a-z]_[a-z]$")


# -- names -------------------------------------------------------------------


def name_tokens(name: str) -> List[str]:
    """Lowercase alphanumeric tokens of a directory name."""
    return _TOKEN_RE.findall(name.lower())


def stem(token: str) -> str:
    """Strip a plural/participle suffix. Crude on purpose: names, not prose."""
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    for suffix in ("ing", "ed"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def stem_set(name: str) -> Set[str]:
    return {stem(t) for t in name_tokens(name)}


@dataclass(frozen=True)
class Collision:
    """How ``name`` collides with the name it was compared against."""

    name: str
    kind: str
    score: float

    def as_dict(self) -> Dict[str, object]:
        return {"name": self.name, "kind": self.kind, "score": self.score}


def compare_names(a: str, b: str) -> Optional[Collision]:
    """Return how *b* collides with *a*, or ``None`` when the names are distinct."""
    if a == b:
        return None
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return None
    sa = {stem(t) for t in ta}
    sb = {stem(t) for t in tb}
    if sa == sb:
        return Collision(b, "same_words", 1.0)
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) < len(long_) and long_[: len(short)] == short:
        return Collision(b, "prefix", round(len(short) / len(long_), 2))
    if len(ta) == 1 and len(tb) == 1:
        x, y = ta[0], tb[0]
        if (x.startswith(y) or y.startswith(x)) and min(len(x), len(y)) >= 4:
            return Collision(b, "prefix", round(min(len(x), len(y)) / max(len(x), len(y)), 2))
    union = sa | sb
    jaccard = len(sa & sb) / len(union) if union else 0.0
    if jaccard >= 0.5:
        return Collision(b, "overlap", round(jaccard, 2))
    return None


def sibling_collisions(existing: Iterable[str], new_name: str, limit: int = 3) -> List[Collision]:
    """Existing sibling names that collide with *new_name*, strongest first."""
    found = [c for c in (compare_names(new_name, other) for other in existing) if c is not None]
    found.sort(key=lambda c: (_KIND_RANK[c.kind], -c.score, c.name))
    return found[:limit]


def sibling_pairs(names: Sequence[str], limit: Optional[int] = None) -> List[Tuple[str, Collision]]:
    """Every colliding pair among *names* as ``(name, collision)``, strongest first."""
    pairs: List[Tuple[str, Collision]] = []
    ordered = sorted(names)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            c = compare_names(a, b)
            if c is not None:
                pairs.append((a, c))
    pairs.sort(key=lambda p: (_KIND_RANK[p[1].kind], -p[1].score, p[0]))
    return pairs if limit is None else pairs[:limit]


def cluster_by_leading_token(
    names: Iterable[str], min_size: int = 3, skip_tokens: Optional[Iterable[str]] = None
) -> Tuple[List[Tuple[str, List[str]]], List[str]]:
    """Group names by their first token.

    Returns ``(groups, leftovers)``: groups of at least *min_size* members,
    largest first, and the names that fell into smaller groups. Dumb on
    purpose — the leading word is what agents reach for when they mint
    ``aio_reporting`` beside ``aio_scaling`` — and the merge of two adjacent
    groups (``aio`` with ``ai_overview``) is left to a person.
    """
    buckets: Dict[str, List[str]] = {}
    leftovers: List[str] = []
    skip = {stem(t) for t in (skip_tokens or ())}
    for name in names:
        # A date is never a topic: six meeting notes named 2026_02_13_* are
        # a chronology, not a cluster called "2026". And the parent's own
        # words are not a cluster either: twelve aio_* nodes under
        # projects/aio/ are that hub's contents, not a hub called aio/aio
        # (seen on a real KB after its first consolidation).
        tokens = [t for t in name_tokens(name) if not is_date_token(t) and stem(t) not in skip]
        if not tokens:
            leftovers.append(name)
            continue
        buckets.setdefault(tokens[0], []).append(name)
    groups: List[Tuple[str, List[str]]] = []
    for key, members in buckets.items():
        if len(members) >= min_size:
            groups.append((key, sorted(members)))
        else:
            leftovers.extend(members)
    groups.sort(key=lambda g: (-len(g[1]), g[0]))
    return groups, sorted(leftovers)


def is_date_token(token: str) -> bool:
    return bool(_DATE_TOKEN_RE.match(token))


def series_key(name: str) -> Tuple[str, bool]:
    """``(name with date/time tokens removed, whether any were removed)``."""
    tokens = name_tokens(name)
    kept = [t for t in tokens if not is_date_token(t)]
    return "_".join(kept), len(kept) < len(tokens)


def same_series(a: str, b: str) -> bool:
    """True when two names differ only by date/time tokens."""
    ka, da = series_key(a)
    kb, db = series_key(b)
    return da and db and ka == kb


def date_series(names: Iterable[str], min_size: int = 3) -> List[Tuple[str, List[str]]]:
    """Groups of names that differ only by date/time tokens, largest first."""
    groups: Dict[str, List[str]] = {}
    for name in names:
        key, dated = series_key(name)
        if dated:
            groups.setdefault(key, []).append(name)
    out = [(k, sorted(v)) for k, v in groups.items() if len(v) >= min_size]
    out.sort(key=lambda g: (-len(g[1]), g[0]))
    return out


def is_bucket_name(name: str) -> bool:
    return bool(_BUCKET_RE.match(name))


def is_facet_layout(paths: Sequence[str]) -> bool:
    """Same basename at one depth under sibling parents (``customers/{key,standard}/oem``)."""
    if len(paths) < 2:
        return False
    depths = {p.count("/") for p in paths}
    grandparents = {"/".join(p.split("/")[:-2]) for p in paths}
    return len(depths) == 1 and len(grandparents) == 1


# -- ignore file -------------------------------------------------------------


def load_ignore(kg_root: Path) -> List[str]:
    """Patterns from ``<root>/.kvaultignore`` (missing file = no patterns)."""
    path = Path(kg_root) / IGNORE_FILE
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return []
    patterns: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern = line.strip("/")
        # A catch-all ("*", "**") would hide every root category and
        # disarm the root guard; there is no honest reading of it.
        if pattern and set(pattern) <= set("*/"):
            continue
        if pattern:
            patterns.append(pattern)
    return patterns


def is_ignored(rel_path: str, patterns: Sequence[str]) -> bool:
    """True when *rel_path* (KB-relative, posix) or an ancestor matches a pattern."""
    rel = rel_path.strip("/")
    if not rel or rel == ".":
        return False
    for raw_pattern in patterns:
        pattern = raw_pattern.strip().strip("/")
        if not pattern:
            continue
        if fnmatch.fnmatchcase(rel, pattern):
            return True
        if rel.startswith(pattern + "/"):
            return True
        # A directory pattern ("sources") also ignores everything beneath it
        # when written with a wildcard ("sources/*").
        if fnmatch.fnmatchcase(rel, pattern + "/*"):
            return True
    return False


# -- walking -----------------------------------------------------------------


def is_reserved_name(name: str) -> bool:
    """Hidden, internal, or convention-reserved directory names."""
    return name.startswith(".") or name.startswith("_") or name in RESERVED_DIRS


def rel(kg_root: Path, path: Path) -> str:
    try:
        r = path.relative_to(kg_root).as_posix()
    except ValueError:
        return str(path)
    return "." if r in ("", ".") else r


def child_dirs(dir_path: Path, kg_root: Path, ignore: Sequence[str]) -> List[Path]:
    """Managed child directories of *dir_path*: not hidden, reserved, or ignored."""
    try:
        entries = sorted(dir_path.iterdir())
    except OSError:
        return []
    out: List[Path] = []
    for entry in entries:
        if not entry.is_dir() or is_reserved_name(entry.name):
            continue
        if is_ignored(rel(kg_root, entry), ignore):
            continue
        out.append(entry)
    return out


def walk_dirs(kg_root: Path, ignore: Sequence[str]) -> Iterator[Path]:
    """Every managed directory under the root, depth-first, root excluded."""
    stack = list(reversed(child_dirs(Path(kg_root), Path(kg_root), ignore)))
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(child_dirs(current, Path(kg_root), ignore)))


def has_summary(dir_path: Path) -> bool:
    return (dir_path / SUMMARY_NAME).is_file()


def is_ghost(dir_path: Path) -> bool:
    """A managed directory with neither a summary nor legacy metadata."""
    return not has_summary(dir_path) and not (dir_path / LEGACY_META_NAME).is_file()


def ghost_dirs(kg_root: Path, ignore: Sequence[str]) -> List[str]:
    """KB-relative paths of directories no surface can see."""
    root = Path(kg_root)
    return [rel(root, d) for d in walk_dirs(root, ignore) if is_ghost(d)]


def basename_matches(
    kg_root: Path, name: str, exclude: Optional[str], ignore: Sequence[str]
) -> List[str]:
    """Other managed directories anywhere in the tree with basename *name*."""
    root = Path(kg_root)
    out: List[str] = []
    for d in walk_dirs(root, ignore):
        if d.name != name:
            continue
        r = rel(root, d)
        if exclude is not None and r == exclude:
            continue
        out.append(r)
    return out


def basename_duplicates(kg_root: Path, ignore: Sequence[str]) -> Dict[str, List[str]]:
    """Basenames that occur at more than one place in the tree."""
    root = Path(kg_root)
    seen: Dict[str, List[str]] = {}
    for d in walk_dirs(root, ignore):
        seen.setdefault(d.name, []).append(rel(root, d))
    return {k: v for k, v in seen.items() if len(v) > 1}


def loose_files(kg_root: Path, ignore: Sequence[str]) -> List[str]:
    """Files that sit outside the node convention (not a summary, not in deep_context/)."""
    root = Path(kg_root)
    out: List[str] = []

    def _scan(dir_path: Path, allowed: Tuple[str, ...]) -> None:
        try:
            entries = sorted(dir_path.iterdir())
        except OSError:
            return
        for entry in entries:
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if entry.name in allowed:
                continue
            r = rel(root, entry)
            if is_ignored(r, ignore):
                continue
            out.append(r)

    _scan(root, NODE_FILES + ROOT_FILES)
    for d in walk_dirs(root, ignore):
        _scan(d, NODE_FILES)
    return out


def journal_layout_findings(kg_root: Path) -> List[Dict[str, str]]:
    """Files and directories under ``journal/`` that are off the canonical layout.

    Canonical: ``journal/YYYY-MM/log.md``. A ``_summary.md`` at ``journal/``
    or in a month directory is tolerated (some KBs treat months as nodes).
    ``journal/archive`` and ``archive/journal`` are flagged as competing
    histories.
    """
    root = Path(kg_root)
    findings: List[Dict[str, str]] = []
    journal = root / "journal"
    flagged_dirs: List[str] = []
    if journal.is_dir():
        for entry in sorted(journal.rglob("*")):
            if any(part.startswith(".") for part in entry.relative_to(root).parts):
                continue
            r = rel(root, entry)
            # One finding per stray subtree: the directory, not every file in it.
            if any(r.startswith(d + "/") for d in flagged_dirs):
                continue
            parts = entry.relative_to(journal).parts
            if entry.is_dir():
                if len(parts) == 1 and _JOURNAL_MONTH_RE.match(parts[0]):
                    continue
                if len(parts) == 1 and parts[0] == "archive":
                    findings.append({"path": r, "reason": "second history: journal/archive"})
                    flagged_dirs.append(r)
                    continue
                findings.append({"path": r, "reason": "directory off the journal/YYYY-MM layout"})
                flagged_dirs.append(r)
                continue
            if len(parts) == 1 and parts[0] == SUMMARY_NAME:
                continue
            if (
                len(parts) == 2
                and _JOURNAL_MONTH_RE.match(parts[0])
                and parts[1] in ("log.md", SUMMARY_NAME)
            ):
                continue
            findings.append({"path": r, "reason": "file off the journal/YYYY-MM/log.md layout"})
    if (root / "archive" / "journal").is_dir():
        findings.append({"path": "archive/journal", "reason": "second history: archive/journal"})
    return findings


__all__ = [
    "IGNORE_FILE",
    "RESERVED_DIRS",
    "NODE_FILES",
    "ROOT_FILES",
    "COLLISION_KINDS",
    "Collision",
    "name_tokens",
    "stem",
    "stem_set",
    "compare_names",
    "sibling_collisions",
    "sibling_pairs",
    "cluster_by_leading_token",
    "is_date_token",
    "series_key",
    "same_series",
    "date_series",
    "is_bucket_name",
    "is_facet_layout",
    "load_ignore",
    "is_ignored",
    "is_reserved_name",
    "rel",
    "child_dirs",
    "walk_dirs",
    "has_summary",
    "is_ghost",
    "ghost_dirs",
    "basename_matches",
    "basename_duplicates",
    "loose_files",
    "journal_layout_findings",
]
