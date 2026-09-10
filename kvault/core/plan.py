"""``kvault plan``: a bounded, ordered maintenance worklist derived from ``check``.

The 0.14 maintenance loop was a table in AGENTS.md: split fat parents, merge
near-duplicates, rewrite flagged summaries. No cadence, no ordering, no
deterministic input, and ``move`` took one node per call. The audited work
KB had nightly and weekly LLM jobs with maintenance prompts, and they
drifted anyway. ``plan`` is the engine those prompts lacked: it turns
findings into concrete commands, in leverage order, so an agent on any
runtime executes the same moves.

It never applies anything. Clustering is by leading token and nothing
smarter — the judgment calls (are ``aio`` and ``ai_overview`` the same
initiative? are ``people`` and ``team``?) come back as ``questions``.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

from kvault._version import __version__
from kvault.core import notes as nt
from kvault.core import structure as st
from kvault.core.check import DEFAULT_MAX_CHILDREN, run_checks
from kvault.core.frontmatter import parse_frontmatter

#: Members listed with a gist per cluster item; past this the count stands in.
MAX_MEMBER_GISTS = 12
#: Adopt commands per directory in the JSON; the human renderer bounds further.
MAX_ADOPT_COMMANDS = 25


def _rm_command(root: Path) -> str:
    return "git rm -q" if (root / ".git").exists() else "rm"


def _gist_of(root: Path, rel: str, limit: int = 80) -> Optional[str]:
    """First body line of a node summary, so an agent can refine a cluster
    without reading every member."""
    try:
        _, body = parse_frontmatter((root / rel / "_summary.md").read_text(encoding="utf-8"))
    except OSError:
        return None
    for line in body.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return None


DEFAULT_LIMIT = 10
DEFAULT_MIN_CLUSTER = 3

#: Leverage order. Clusters first: one batch collapses the most fan-out and
#: is where an agent's write-time collisions come from. Ghosts next because
#: an invisible branch is what makes split-brain trees happen.
PRIORITY = {
    "cluster": 1,
    "ghost": 2,
    "series": 3,
    "siblings": 4,
    "loose": 5,
    "journal": 6,
    "summary": 7,
}


def _join(parent: str, name: str) -> str:
    return name if parent == "." else f"{parent}/{name}"


def _in_scope(path: str, scope: Optional[str]) -> bool:
    if scope is None or scope == ".":
        return True
    return path == scope or path.startswith(scope + "/")


def _batch_command(root: Path, moves: List[Dict[str, str]], new_root: bool = False) -> str:
    payload = json.dumps(moves)
    flag = " --new-root" if new_root else ""
    return (
        f"kvault move --batch --confirm{flag} --kb-root {shlex.quote(str(root))} <<'EOF'\n"
        f"{payload}\nEOF"
    )


def build_plan(
    kg_root: Path,
    path: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    max_children: int = DEFAULT_MAX_CHILDREN,
    min_cluster: int = DEFAULT_MIN_CLUSTER,
) -> Dict[str, Any]:
    """Build the worklist. *path* scopes it to a subtree; *limit* bounds it."""
    root = Path(kg_root)
    q = shlex.quote(str(root))
    ignore = st.load_ignore(root)
    scope = None
    if path is not None:
        scope = path.strip("/")
        while scope.startswith("./"):
            scope = scope[2:]
        scope = scope or "."
        if scope != "." and not (root / scope).is_dir():
            return {
                "success": False,
                "error_code": "not_found",
                "error": f"Path not found: {scope}",
            }
    doc = run_checks(root, max_children=max_children, max_findings=0)

    items: List[Dict[str, Any]] = []
    questions: List[str] = []
    # Collapsed groups: one item per parent for sibling pairs, one per
    # directory for loose files. On a real KB the uncollapsed form was 131
    # items, which is a wall, not a worklist.
    sibling_groups: Dict[str, List[Dict[str, Any]]] = {}
    loose_groups: Dict[str, List[Dict[str, Any]]] = {}

    for finding in doc["findings"]:
        fpath = finding["path"]
        code = finding["code"]
        if code == "BRANCH":
            if not _in_scope(fpath, scope):
                continue
            parent_dir = root if fpath == "." else root / fpath
            names = [d.name for d in st.child_dirs(parent_dir, root, ignore)]
            parent_words = st.name_tokens(fpath.rsplit("/", 1)[-1]) if fpath != "." else []
            groups, leftovers = st.cluster_by_leading_token(
                names, min_cluster, skip_tokens=parent_words
            )
            count = finding["detail"].get("child_count", len(names))
            for token, members in groups:
                if st.is_reserved_name(token):
                    questions.append(
                        f"{fpath}: {len(members)} children share the reserved word "
                        f"'{token}'; default: name a hub for them by hand"
                    )
                    continue
                non_members = [n for n in names if n not in members]
                twin = next(
                    (
                        c.name
                        for c in st.sibling_collisions(non_members, token)
                        if c.kind == "same_words"
                    ),
                    None,
                )
                hub_name = twin or token
                hub = _join(fpath, hub_name)
                hub_exists = hub_name in members or twin is not None
                moves = [
                    {"from": _join(fpath, m), "to": f"{hub}/{m}"} for m in members if m != hub_name
                ]
                if not moves:
                    continue
                member_gists = [
                    {"name": m, "gist": _gist_of(root, _join(fpath, m))}
                    for m in members[:MAX_MEMBER_GISTS]
                ]
                items.append(
                    {
                        "kind": "cluster",
                        "priority": PRIORITY["cluster"],
                        "path": fpath,
                        "why": (
                            f"{fpath} has {count} direct children (ceiling {max_children}); "
                            f"{len(members)} share the leading word '{token}'"
                        ),
                        "new_parent": hub,
                        "new_parent_exists": hub_exists,
                        # The hub is named by the leading word. That is a
                        # placeholder: rename it in the `to` paths before the
                        # batch runs ('projects/ai' is not a name).
                        "hub_name_is_placeholder": not hub_exists,
                        "members": member_gists,
                        "members_total": len(members),
                        "moves": moves,
                        "commands": [
                            # A root cluster's hub is a new root category; the
                            # batch must say so or the guard refuses it.
                            _batch_command(root, moves, new_root=(fpath == "." and not hub_exists)),
                            f"kvault update-summaries --kb-root {q}  "
                            f"# rewrite {hub}, then {fpath}, then .",
                            f"# or, to keep {fpath} flat on purpose: kvault mark {shlex.quote(fpath)} "
                            f"--max-children {count} --kb-root {q}",
                        ],
                        "then": (
                            f"rewrite {hub} as a rollup of its {len(members)} children"
                            + (
                                " (it was a leaf; it is a parent now)"
                                if hub_exists
                                else f"; '{token}' is the leading word, not a name — rename "
                                "the hub in the `to` paths before running the batch"
                            )
                        ),
                    }
                )
            if leftovers:
                sample = ", ".join(leftovers[:8]) + (" …" if len(leftovers) > 8 else "")
                questions.append(
                    f"{fpath}: {len(leftovers)} children share no leading word with "
                    f"{min_cluster - 1}+ siblings and stay in place: {sample} — default: leave them"
                )
            if len(groups) >= 2:
                questions.append(
                    f"{fpath}: are any of these groups one initiative? "
                    + ", ".join(t for t, _ in groups[:10])
                    + " — default: keep them separate hubs (a merge is one later move)"
                )
            continue

        if code not in ("GHOST", "SERIES", "SIBLINGS", "LOOSE", "JOURNAL", "SUMMARY"):
            continue
        anchor = fpath
        if code == "SUMMARY":
            anchor = fpath[: -len("/_summary.md")] if fpath.endswith("/_summary.md") else "."
        if code == "SIBLINGS" and finding["detail"].get("kind") == "same_name_elsewhere":
            anchor = finding["detail"]["paths"][0]
        if code == "LOOSE":
            anchor = finding["detail"].get("parent", ".")
        if not _in_scope(anchor, scope):
            continue

        if code == "SERIES":
            d = finding["detail"]
            key = d["key"]
            parent_dir = root if fpath == "." else root / fpath
            names = [x.name for x in st.child_dirs(parent_dir, root, ignore)]
            members = next((m for k, m in st.date_series(names) if k == key), d.get("members", []))
            if not members:
                continue
            parent_leaf = fpath.rsplit("/", 1)[-1] if fpath != "." else "."
            if not key:
                # Children named by date alone: they are the chronology of the
                # parent itself; the parent is the current-state node.
                questions.append(
                    f"{fpath}: {len(members)} children are named by date alone "
                    f"({members[0]}, …) — they are the timeline of {fpath} itself; "
                    f"default: fold them into {fpath}/deep_context/ and keep {fpath} as the "
                    "current state"
                )
                continue
            if st.is_reserved_name(key):
                questions.append(
                    f"{fpath}: the series key '{key}' is a reserved name; default: pick a hub "
                    "name and fold by hand"
                )
                continue
            existing = [n for n in names if n not in members]
            if fpath != "." and (parent_leaf == key or st.series_key(parent_leaf)[0] == key):
                # The parent is already the current-state node for this series.
                hub, hub_exists = fpath, True
            else:
                twin = next(
                    (
                        c.name
                        for c in st.sibling_collisions(existing, key)
                        if c.kind == "same_words"
                    ),
                    None,
                )
                hub_name = twin or key
                hub, hub_exists = _join(fpath, hub_name), hub_name in existing
            # The fold that worked on a real KB: the dated nodes become
            # background material of one current-state node. Nothing is
            # deleted, search still reaches every card, and a new hub is a
            # stub until the agent writes the current state from them.
            moves = [{"from": _join(fpath, m), "to": f"{hub}/deep_context/{m}"} for m in members]
            then = (
                f"{hub} already exists; update it to reflect the {len(members)} dated nodes "
                "now under its deep_context/ (do not recreate it)"
                if hub_exists
                else f"{hub} is a stub after the batch; write it as the current state of "
                f"'{key}' from the {len(members)} dated nodes now under its deep_context/"
            )
            item: Dict[str, Any] = {
                "kind": "series",
                "priority": PRIORITY["series"],
                "path": fpath,
                "why": finding["message"],
                "new_parent": hub,
                "new_parent_exists": hub_exists,
                "moves": moves,
                "members": members[:MAX_MEMBER_GISTS],
                "members_total": len(members),
                "commands": [
                    _batch_command(root, moves, new_root=(fpath == "." and not hub_exists)),
                    f"kvault write {shlex.quote(hub)} --kb-root {q} <<'EOF' … (the current "
                    "state, written from deep_context/) EOF",
                    f"kvault update-summaries --kb-root {q}  # then {fpath}, then up",
                    f"# or, if this chronology is intentional: kvault mark {shlex.quote(fpath)} "
                    f"--series-ok --kb-root {q}",
                ],
                "then": then,
            }
            if fpath != "." and st.series_key(parent_leaf)[1] and hub != fpath:
                item["_question"] = (
                    f"{fpath} is itself a dated name; is the whole subtree one chronology "
                    "that should fold one level up? — default: run this fold; fold up later if so"
                )
            items.append(item)
            continue
        if code == "SIBLINGS" and finding["detail"].get("kind") not in (
            "same_name_elsewhere",
            "more_pairs",
        ):
            sibling_groups.setdefault(fpath, []).append(finding)
            continue
        if code == "LOOSE":
            loose_groups.setdefault(anchor, []).append(finding)
            continue

        if code == "GHOST":
            items.append(
                {
                    "kind": "ghost",
                    "priority": PRIORITY["ghost"],
                    "path": fpath,
                    "why": finding["message"],
                    "commands": [
                        f"kvault write {fpath} --create --kb-root {q} "
                        "<<'EOF' … (frontmatter + a rollup of what is inside) EOF",
                        f"# or, if it is tooling and not knowledge: "
                        f"echo '{fpath}/' >> {q}/{st.IGNORE_FILE}",
                    ],
                }
            )
        elif code == "SIBLINGS":
            detail = finding["detail"]
            if detail.get("kind") == "more_pairs":
                continue
            if detail.get("kind") == "same_name_elsewhere":
                questions.append(
                    f"'{fpath}' lives at {len(detail['paths'])} places "
                    f"({', '.join(detail['paths'][:4])}): which one is home? — default: read both; "
                    "different things → kvault mark <one> --distinct-from <other>"
                )
                items.append(
                    {
                        "kind": "siblings",
                        "priority": PRIORITY["siblings"],
                        "path": detail["paths"][0],
                        "why": finding["message"],
                        "commands": [f"kvault read {p} --kb-root {q}" for p in detail["paths"][:4]]
                        + ["# then: kvault move --confirm <loser> <winner>/<name>, or delete"],
                    }
                )
            else:
                a, b = detail.get("a"), detail.get("b")
                items.append(
                    {
                        "kind": "siblings",
                        "priority": PRIORITY["siblings"],
                        "path": fpath,
                        "why": finding["message"],
                        "commands": [
                            f"kvault read {_join(fpath, a)} --kb-root {q}",
                            f"kvault read {_join(fpath, b)} --kb-root {q}",
                            "# same thing → merge and delete one; subtopic → "
                            f"kvault move --confirm {_join(fpath, b)} {_join(fpath, a)}/{b}",
                            f"# different things → kvault mark {_join(fpath, a)} --distinct-from {b} "
                            f"--kb-root {q}  (the finding stops)",
                        ],
                    }
                )
        elif code == "JOURNAL":
            items.append(
                {
                    "kind": "journal",
                    "priority": PRIORITY["journal"],
                    "path": fpath,
                    "why": finding["message"],
                    "commands": [
                        "# fold its entries into journal/YYYY-MM/log.md with kvault journal, "
                        "then remove it",
                    ],
                }
            )
        elif code == "SUMMARY":
            items.append(
                {
                    "kind": "summary",
                    "priority": PRIORITY["summary"],
                    "path": anchor,
                    "why": finding["message"],
                    "commands": [
                        f"kvault read-summary {anchor} --kb-root {q}",
                        f"kvault list {anchor} --kb-root {q}",
                        f"kvault write-summary {anchor} --kb-root {q} "
                        "<<'EOF' … (the rewritten rollup) EOF",
                    ],
                }
            )

    # A plan is one snapshot, and any batch changes the tree the next item
    # was computed from. Nested chronologies produce a series item per
    # level, and a cluster can move the parent of a series. Only the
    # outermost structural item survives; the rest would fail with
    # "source doesn't exist" once the outer batch ran (seen on a real KB).
    structural = [i for i in items if i["kind"] in ("cluster", "series")]

    def _under_another_batch(item: Dict[str, Any]) -> bool:
        for other in structural:
            if other is item:
                continue
            for mv in other.get("moves", []):
                src = mv["from"]
                if item["path"] == src or item["path"].startswith(src + "/"):
                    return True
        return False

    items = [
        i for i in items if not (i["kind"] in ("cluster", "series") and _under_another_batch(i))
    ]
    for item in items:
        question = item.pop("_question", None)
        if question:
            questions.append(question)

    for parent, group in sibling_groups.items():
        top = group[0]["detail"]
        items.append(
            {
                "kind": "siblings",
                "priority": PRIORITY["siblings"],
                "path": parent,
                "why": f"{len(group)} colliding sibling pair(s), e.g. {group[0]['message']}",
                "pairs": [
                    {
                        "a": g["detail"].get("a"),
                        "b": g["detail"].get("b"),
                        "kind": g["detail"].get("kind"),
                    }
                    for g in group[:8]
                ],
                "commands": [
                    f"kvault read {_join(parent, top.get('a'))} --kb-root {q}",
                    f"kvault read {_join(parent, top.get('b'))} --kb-root {q}",
                    "# same thing → merge and delete one; subtopic → "
                    f"kvault move --confirm {_join(parent, top.get('b'))} "
                    f"{_join(parent, top.get('a'))}/{top.get('b')}",
                    f"# different things → kvault mark {_join(parent, top.get('a'))} "
                    f"--distinct-from {top.get('b')} --kb-root {q}  (the finding stops)",
                ],
            }
        )
    for parent, group in loose_groups.items():
        kinds = {
            k: sum(1 for g in group if g["detail"].get("kind") == k)
            for k in ("legacy_node_file", "supporting_doc", "artifact")
        }
        legacy = [g for g in group if g["detail"].get("kind") == "legacy_node_file"]
        home = "deep_context" if parent == "." else f"{parent}/deep_context"
        commands: List[str] = []
        rm = _rm_command(root)
        # Adopt THROUGH kvault: the write validates and autofills frontmatter,
        # runs the create guards, and lands in the ops log. A raw git mv did
        # none of that (and left WRITE: findings behind on a real KB).
        for g in legacy[:MAX_ADOPT_COMMANDS]:
            node = g["path"][: -len(".md")]
            file_q = shlex.quote(str(root / g["path"]))
            commands.append(
                f"kvault write {shlex.quote(node)} --create --kb-root {q} < {file_q} "
                f"&& {rm} {file_q}"
            )
        if len(legacy) > MAX_ADOPT_COMMANDS:
            commands.append(
                f"# … +{len(legacy) - MAX_ADOPT_COMMANDS} more legacy node files under {parent}"
            )
        if legacy:
            commands.append(
                f"kvault update-summaries --kb-root {q}  # rewrite {parent} to cover the adopted nodes"
            )
        others = [g for g in group if g["detail"].get("kind") != "legacy_node_file"]
        if others:
            mv = "git mv" if (root / ".git").exists() else "mv"
            home_q = shlex.quote(str(root / home))
            commands.append(
                f"mkdir -p {home_q} && {mv} "
                + " ".join(shlex.quote(str(root / g["path"])) for g in others[:6])
                + f" {home_q}/"
            )
            if len(others) > 6:
                commands.append(f"# … +{len(others) - 6} more files under {parent}")
            commands.append(f"# or list tooling/generated files in {q}/{st.IGNORE_FILE}")
        items.append(
            {
                "kind": "loose",
                "priority": PRIORITY["loose"],
                "path": parent,
                "why": (
                    f"{len(group)} loose file(s): {kinds['legacy_node_file']} legacy node "
                    f"file(s), {kinds['supporting_doc']} supporting doc(s), {kinds['artifact']} artifact(s)"
                ),
                "files": [g["path"] for g in group[:12]],
                "commands": commands,
            }
        )

    items.sort(key=lambda i: (i["priority"], i["path"]))
    total = len(items)
    shown = items[:limit] if limit and limit > 0 else items
    notes: List[Dict[str, Any]] = []
    if len(shown) < total:
        notes.append(
            nt.note(
                "truncated",
                f"showing {len(shown)} of {total} items",
                detail={"shown": len(shown), "total": total},
                next_step=f"kvault plan {scope or '.'} --limit {total} --kb-root {q}",
            )
        )
    return {
        "success": True,
        "version": __version__,
        "path": scope or ".",
        "total": total,
        "count": len(shown),
        "limit": limit,
        "did": f"planned {len(shown)} of {total} maintenance items",
        "notes": notes,
        "items": shown,
        "questions": questions,
    }


__all__ = ["DEFAULT_LIMIT", "DEFAULT_MIN_CLUSTER", "PRIORITY", "build_plan"]
