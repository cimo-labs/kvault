"""References in node summaries, resolved against the tree (``DANGLING:``).

The 0.15 checks compare dates (``PROPAGATE``) and names (``SIBLINGS``);
nothing compared what a summary *says* with what is on disk. A parent that
still listed children that had been deleted or moved away stayed green
forever, because a child that no longer exists can never be newer than its
parent. On copies of two real KBs the first run found two dozen: links
broken when their node or its target moved, and rollups that copied a
child's relative link (``[deep_context/](deep_context/)``) one level up,
where it resolves to nothing.

A summary points at other nodes in four ways that can be checked without
reading prose for meaning:

- ``link``: a relative Markdown link, ``[x](foo/)`` or
  ``[x](../bar/_summary.md)``. Resolved from the node's own directory, as a
  Markdown renderer would, with the KB root as a fallback (agents write
  root-relative links too).
- ``code``: a code span holding a KB path, `` `a/b` `` or `` `a/b/` ``.
- ``path``: a bare path in prose with three or more components or an
  underscore. Two-part prose ("and/or", "family/startup") was all noise on
  real KBs, so it is never checked.
- ``list``: an entry of a child list, meaning a list item or heading whose
  first token is a node name (``- `weekly_reporting/` — …``, ``### foo_bar``),
  or a nested item or table row whose first token is a directory
  (``    - `foo_bar/` — …``, ``| `foo_bar/` | … |``; a bare one is as often a
  config key or a column name). An entry written as a link (``- [foo_bar](../foo_bar/)``) is
  judged by its link alone. Entries count only in a summary that
  demonstrably lists its children: at least one entry (a linked one too),
  link, or `` `name/` `` code span names a child that exists. A root
  category is not dangling (boilerplate that mentions the top-level
  ``tech/`` was most of the noise on a real KB; the price is that a removed
  child named like a root category goes unreported), and a `` `name/` ``
  in running prose is not an entry (it is as often a code directory).

``code`` and ``path`` references count only when their first component is a
directory of the KB (tried from the node, then from the root), so text that
merely looks like a path (``cimo-labs/kvault``) is never checked. A relative
link of more than one component needs that too only when both its first and
last components are plain words and the last names no node: a tracker
shortlink such as ``issue/123`` is not a child path, while
``models/bayes_routing/`` after ``models/`` moved, ``gone_child/spec/`` and
``deep_context/notes/`` are still checked.

A dangling reference lists the nodes elsewhere that share its target's name
(``moved_to``), as hints. It never says which one to write: after a rename
the name's remaining holder is a namesake, and after a delete it is only a
namesake, which nothing in the tree tells apart from a node that moved.
File-like targets (``notes.md``, ``chart.png``) are not node references.
A reference is *dangling* when it resolves inside the KB and nothing is
there. Fenced code blocks are skipped.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from kvault.core import structure as st
from kvault.core.frontmatter import parse_frontmatter

REF_KINDS: Tuple[str, ...] = ("link", "code", "path", "list")
#: Same-name nodes offered per dangling reference as "maybe it moved here".
MAX_MOVED_TO = 3

_COMP = r"[a-z0-9][a-z0-9_\-]*"
_MDLINK_RE = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_CODE_PATH_RE = re.compile(rf"(?:{_COMP}/)+{_COMP}/?")
_CODE_DIR_RE = re.compile(rf"({_COMP})/")
# A sentence may end right after a path ("… lives at a/b_c."); a dot that
# continues into a word ("plan.md", "example.com") means it is not a node path.
_BARE_PATH_RE = re.compile(rf"(?<![\w./:@`\-])((?:{_COMP}/)+{_COMP})/?(?![\w/@`\-]|\.[\w/])")
_ENTRY_NAME = r"[a-z0-9](?:[a-z0-9_\-]*[a-z0-9])?"  # never ends in _ or -: __bold__ closes
_ENTRY_RE = re.compile(
    rf"^(?:\s{{0,3}}(?:#{{1,6}}\s+|\|\s*)|\s*(?:[-*+]\s+|\d+[.)]\s+))"
    rf"(?:\*\*|__)?\[?`?(?P<name>{_ENTRY_NAME})(?P<slash>/)?`?\]?"
    r"(?P<link>\([^)\n]*\))?(?:\*\*|__)?"
    r"(?=\s*(?:[:—–(|\-]|\*\*|$))"
)
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)


@dataclass(frozen=True)
class Reference:
    """One resolved reference from a node's summary."""

    node: str
    kind: str
    raw: str
    target: str
    exists: bool
    moved_to: Tuple[str, ...] = field(default=())

    def as_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "kind": self.kind,
            "raw": self.raw,
            "target": self.target,
            "exists": self.exists,
            "moved_to": list(self.moved_to),
        }


# -- the tree ----------------------------------------------------------------


@dataclass
class _Tree:
    root: Path
    ignore: Sequence[str]
    #: Every non-hidden directory (reserved and ignored ones included: a
    #: reference into deep_context/ or a tooling dir is still a real path).
    dirs: Set[str] = field(default_factory=set)
    by_name: Dict[str, List[str]] = field(default_factory=dict)
    children: Dict[str, Set[str]] = field(default_factory=dict)
    #: Directories that carry a summary and are not ignored: the nodes scanned.
    nodes: List[str] = field(default_factory=list)
    #: Their last components (journal months and deep_context/ entries are not).
    node_names: Set[str] = field(default_factory=set)
    #: Paths that count as KB directories though they are gone: what a move or
    #: delete just took away, so references into it still resolve to "nothing".
    anchors: Set[str] = field(default_factory=set)


def _walk(kg_root: Path, ignore: Sequence[str]) -> _Tree:
    root = Path(os.path.normpath(str(Path(kg_root).resolve())))
    tree = _Tree(root=root, ignore=ignore)
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        rel = st.rel(root, Path(current))
        tree.children[rel] = set(dirnames)
        if rel != ".":
            tree.dirs.add(rel)
            tree.by_name.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
        # journal/ and deep_context/ are history and background material: a
        # path written there was true when it was written, so their own
        # summaries are not scanned (references *into* them still resolve).
        if (
            st.SUMMARY_NAME in filenames
            and not st.is_ignored(rel, ignore)
            and not any(st.is_reserved_name(part) for part in rel.split("/") if rel != ".")
            and st.inside_root(Path(current) / st.SUMMARY_NAME, root)
        ):
            tree.nodes.append(rel)
            tree.node_names.add(rel.rsplit("/", 1)[-1])
    return tree


def _node_dir(tree: _Tree, node: str) -> Path:
    return tree.root if node == "." else tree.root / node


def _inside(tree: _Tree, path: Path) -> Optional[str]:
    """KB-relative form of a normalized absolute path, or None outside the KB."""
    if path == tree.root:
        return "."
    try:
        rel = path.relative_to(tree.root).as_posix()
    except ValueError:
        return None
    if rel.startswith("..") or any(part.startswith(".") for part in rel.split("/")):
        return None
    return rel


def _norm(path: Path) -> Path:
    return Path(os.path.normpath(str(path)))


def _exists(tree: _Tree, rel: str) -> bool:
    # os.path.exists never raises (a 260-byte name, a permission error)
    return rel == "." or rel in tree.dirs or os.path.exists(tree.root / rel)


def _file_like(target: str) -> bool:
    last = target.rsplit("/", 1)[-1]
    return "." in last and last not in (".", "..")


def _clean_link(raw: str) -> Optional[str]:
    target = raw.split("#", 1)[0].split("?", 1)[0].strip()
    if not target or target.startswith(("/", "~", "#")) or _SCHEME_RE.match(target):
        return None
    if target.endswith("/_summary.md"):
        target = target[: -len("/_summary.md")] or "."
    elif target == "_summary.md":
        return None
    target = target.rstrip("/")
    if not target or target == "." or _file_like(target):
        return None
    return target


# -- extraction --------------------------------------------------------------


def _unfenced(body: str) -> str:
    """*body* without fenced code blocks.

    A fence opens with 3+ backticks or tildes at any indent (list items
    indent them); it closes on a line of the same character, at least as
    long, with nothing after it. A backtick line whose info string holds a
    backtick ("``` x ```") is inline code, not a fence. An unclosed fence
    runs to the end, as in CommonMark.
    """
    out: List[str] = []
    fence: Optional[str] = None
    for line in body.splitlines():
        m = _FENCE_RE.match(line)
        if fence is None:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = m.group(1)
                continue
            out.append(line)
        elif (
            m
            and m.group(1)[0] == fence[0]
            and len(m.group(1)) >= len(fence)
            and not m.group(2).strip()
        ):
            fence = None
    return "\n".join(out)


def extract_refs(body: str) -> Dict[str, List[str]]:
    """The raw reference strings in *body*, by kind (unresolved).

    ``list`` holds child-list entry names. ``linked`` (entries written as
    links) and ``dirs`` (single-directory code spans, `` `name/` ``) are
    only evidence that the summary lists children, never references
    themselves. Whether entries count depends on the tree (see the module
    docstring).
    """
    text = _unfenced(body)
    links = [m.group(1) for m in _MDLINK_RE.finditer(text)]
    code: List[str] = []
    dir_spans: List[str] = []
    for m in _CODE_RE.finditer(text):
        span = m.group(1).strip()
        if _CODE_DIR_RE.fullmatch(span):
            dir_spans.append(span[:-1])
        elif _CODE_PATH_RE.fullmatch(span):
            code.append(span.rstrip("/"))
    prose = _CODE_RE.sub(" ", _MDLINK_RE.sub(" ", text))
    paths = [
        p
        for p in (m.group(1) for m in _BARE_PATH_RE.finditer(prose))
        if p.count("/") >= 2 or "_" in p
    ]
    entries: List[str] = []
    linked: List[str] = []
    for line in text.splitlines():
        e = _ENTRY_RE.match(line)
        if e is None:
            continue
        # An entry written as a link is judged by its link (a Related list
        # links siblings; an external link is not a child). A table's first
        # cell or a nested item counts only when written as a directory: a
        # bare one is as often a column name or a config key.
        table = e.group(0).lstrip().startswith("|")
        nested = len(line) - len(line.lstrip()) > 3
        if e.group("link"):
            linked.append(e.group("name"))
        elif e.group("slash") or not (table or nested):
            entries.append(e.group("name"))
    return {
        "link": links,
        "code": code,
        "path": paths,
        "list": entries,
        "linked": linked,
        "dirs": dir_spans,
    }


# -- resolution --------------------------------------------------------------


def _resolve_link(tree: _Tree, node: str, raw: str) -> Optional[Tuple[str, bool]]:
    target = _clean_link(raw)
    if target is None:
        return None
    parts = target.split("/")
    if (
        len(parts) > 1
        and not target.startswith(".")
        and "_" not in parts[0]
        and not st.is_reserved_name(parts[0])
        and "_" not in parts[-1]
        and parts[-1] not in tree.node_names
    ):
        # Plain words at both ends, the last naming no node: a tracker or
        # service link (issue/4821), unless the first word is a KB directory
        # after all. models/bayes_routing/ after models/ moved, gone_child/spec/
        # and deep_context/notes/ are checked either way.
        firsts = [_node_dir(tree, node) / parts[0], tree.root / parts[0]]
        if not any(os.path.isdir(f) or _inside(tree, _norm(f)) in tree.anchors for f in firsts):
            return None
    local = _inside(tree, _norm(_node_dir(tree, node) / target))
    if local is None:
        return None
    if _exists(tree, local):
        return local, True
    if not target.startswith("."):
        alt = _inside(tree, _norm(tree.root / target))
        if alt is not None and _exists(tree, alt):
            return alt, True
    return local, False


def _resolve_anchored(tree: _Tree, node: str, raw: str) -> Optional[Tuple[str, bool]]:
    """A code span or bare path: KB-internal only when its first component is a directory."""
    target = raw.rstrip("/")
    if not target or _file_like(target):
        return None
    first = target.split("/", 1)[0]
    candidates: List[str] = []
    for base in (_node_dir(tree, node), tree.root):
        anchored = _inside(tree, _norm(base / first)) in tree.anchors
        if os.path.isdir(base / first) or anchored:
            rel = _inside(tree, _norm(base / target))
            if rel is not None and rel not in candidates:
                candidates.append(rel)
    if not candidates:
        return None
    for rel in candidates:
        if _exists(tree, rel):
            return rel, True
    if st.is_ignored(candidates[0], tree.ignore):
        return None
    return candidates[0], False


def _moved_to(tree: _Tree, name: str, target: str) -> Tuple[str, ...]:
    if st.is_reserved_name(name):
        return ()  # every node may keep a deep_context/; another one is not "here"
    homes = [
        p
        for p in tree.by_name.get(name, [])
        if p != target and (tree.root / p / st.SUMMARY_NAME).is_file()
    ]
    return tuple(homes[:MAX_MOVED_TO])


def _is_child(node: str, rel: str) -> bool:
    if node == ".":
        return rel != "." and "/" not in rel
    return rel.startswith(node + "/") and "/" not in rel[len(node) + 1 :]


def _node_refs(tree: _Tree, node: str, body: str) -> List[Reference]:
    raw = extract_refs(body)
    prefix = "" if node == "." else node + "/"
    everything = tree.children.get(node, set())
    # Only managed children show that a summary lists its children: the usual
    # "[deep_context/](deep_context/)" line or an ignored tooling dir must not
    # turn every snake_case bullet into a dangling child.
    children = {
        c
        for c in everything
        if not st.is_reserved_name(c) and not st.is_ignored(f"{prefix}{c}", tree.ignore)
    }
    own_names = set(node.split("/")) if node != "." else set()
    out: List[Reference] = []
    seen: Set[str] = set()

    def add(kind: str, text: str, resolved: Optional[Tuple[str, bool]]) -> None:
        if resolved is None:
            return
        target, exists = resolved
        if target in seen:
            return
        seen.add(target)
        name = target.rsplit("/", 1)[-1]
        moved = () if exists else _moved_to(tree, name, target)
        out.append(Reference(node, kind, text, target, exists, moved))

    link_children = False
    for text in raw["link"]:
        resolved = _resolve_link(tree, node, text)
        if (
            resolved
            and resolved[1]
            and _is_child(node, resolved[0])
            and resolved[0].rsplit("/", 1)[-1] in children
        ):
            link_children = True
        add("link", text, resolved)
    for text in raw["code"]:
        add("code", text, _resolve_anchored(tree, node, text))
    for text in raw["path"]:
        add("path", text, _resolve_anchored(tree, node, text))

    names = raw["list"]
    # A child that a move or delete just took away (an anchor) is evidence too:
    # otherwise a parent whose last child left keeps listing it unreported.
    listed = (
        link_children
        or any(n in children for n in names + raw["linked"] + raw["dirs"])
        or any(f"{prefix}{n}" in tree.anchors for n in names)
    )
    # Boilerplate names the top-level folders ("- tech — see there"). A
    # sibling stays a finding: a child that moved up a level is exactly what
    # this list should stop claiming.
    roots = tree.children.get(".", set())
    if listed:
        for name in names:
            # the node's own name or an ancestor's is a heading, not a child;
            # a root category is named, not listed as a child
            if (
                name in everything
                or name in own_names
                or name in roots
                or st.is_reserved_name(name)
            ):
                continue
            target = f"{prefix}{name}"
            if target in seen:
                continue
            # A rollup describes grandchildren too: a name that exists anywhere
            # under this node is not dangling.
            if any(p.startswith(prefix) for p in tree.by_name.get(name, [])):
                continue
            # Bare entry words ("- note: …") are not node names; a node name
            # has an underscore, or was written as a directory (`name/`).
            if "_" not in name and f"`{name}/`" not in body:
                continue
            seen.add(target)
            moved = _moved_to(tree, name, target)
            out.append(Reference(node, "list", name, target, False, moved))
    return out


def _read_body(tree: _Tree, node: str) -> Optional[str]:
    try:
        raw = (_node_dir(tree, node) / st.SUMMARY_NAME).read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    meta, body = parse_frontmatter(raw)
    return body if meta else raw


def scan_references(
    kg_root: Path,
    ignore: Optional[Sequence[str]] = None,
    contains: Optional[Iterable[str]] = None,
    under: Optional[Iterable[str]] = None,
    anchors: Optional[Iterable[str]] = None,
) -> List[Reference]:
    """Every KB-internal reference in the KB's summaries, resolved.

    With *contains* and/or *under* only some nodes are scanned: those whose
    summary text contains one of the *contains* strings, and those at or
    under one of the *under* paths. ``move`` and ``delete`` use this to look
    only where a reference to what they touched can be, and pass what they
    took away as *anchors*: a code span naming ``projects/hub`` after the
    ``projects`` root moved still counts as a KB path.
    """
    patterns = list(ignore) if ignore is not None else st.load_ignore(Path(kg_root))
    tree = _walk(Path(kg_root), patterns)
    tree.anchors = {a.strip("/") for a in (anchors or []) if a and a.strip("/")}
    needles = [c for c in (contains or []) if c]
    scopes = [u.strip("/") for u in (under or []) if u]
    filtered = contains is not None or under is not None
    out: List[Reference] = []
    for node in tree.nodes:
        body = _read_body(tree, node)
        if body is None:
            continue
        if filtered:
            in_scope = any(node == u or node.startswith(u + "/") for u in scopes)
            if not in_scope and not any(n in body for n in needles):
                continue
        out.extend(_node_refs(tree, node, body))
    return out


def dangling_references(
    kg_root: Path,
    ignore: Optional[Sequence[str]] = None,
    contains: Optional[Iterable[str]] = None,
    under: Optional[Iterable[str]] = None,
    anchors: Optional[Iterable[str]] = None,
) -> List[Reference]:
    """References that resolve inside the KB to nothing, sorted by node."""
    refs = [r for r in scan_references(kg_root, ignore, contains, under, anchors) if not r.exists]
    refs.sort(key=lambda r: (r.node, r.target))
    return refs


__all__ = [
    "REF_KINDS",
    "MAX_MOVED_TO",
    "Reference",
    "extract_refs",
    "scan_references",
    "dangling_references",
]
