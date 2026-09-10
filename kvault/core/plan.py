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
from pathlib import Path
from typing import Any, Dict, List, Optional

from kvault._version import __version__
from kvault.core import notes as nt
from kvault.core import structure as st
from kvault.core.check import DEFAULT_MAX_CHILDREN, run_checks

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


def _batch_command(root: Path, moves: List[Dict[str, str]]) -> str:
    payload = json.dumps(moves)
    return f"kvault move --batch --confirm --kb-root {root} <<'EOF'\n{payload}\nEOF"


def build_plan(
    kg_root: Path,
    path: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    max_children: int = DEFAULT_MAX_CHILDREN,
    min_cluster: int = DEFAULT_MIN_CLUSTER,
) -> Dict[str, Any]:
    """Build the worklist. *path* scopes it to a subtree; *limit* bounds it."""
    root = Path(kg_root)
    ignore = st.load_ignore(root)
    scope = None
    if path is not None:
        scope = path.strip("/") or "."
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
            groups, leftovers = st.cluster_by_leading_token(names, min_cluster)
            count = finding["detail"].get("child_count", len(names))
            for token, members in groups:
                hub = _join(fpath, token)
                hub_exists = token in members
                moves = [
                    {"from": _join(fpath, m), "to": f"{hub}/{m}"} for m in members if m != token
                ]
                if not moves:
                    continue
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
                        "moves": moves,
                        "commands": [
                            _batch_command(root, moves),
                            f"kvault update-summaries --kb-root {root}  "
                            f"# rewrite {hub}, then {fpath}, then .",
                        ],
                        "then": (
                            f"rewrite {hub} as a rollup of its {len(members)} children"
                            + (" (it was a leaf; it is a parent now)" if hub_exists else "")
                        ),
                    }
                )
            if leftovers:
                sample = ", ".join(leftovers[:8]) + (" …" if len(leftovers) > 8 else "")
                questions.append(
                    f"{fpath}: {len(leftovers)} children share no leading word with "
                    f"{min_cluster - 1}+ siblings and stay in place: {sample}"
                )
            if len(groups) >= 2:
                questions.append(
                    f"{fpath}: are any of these groups one initiative? "
                    + ", ".join(t for t, _ in groups[:10])
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
            items.append(
                {
                    "kind": "series",
                    "priority": PRIORITY["series"],
                    "path": fpath,
                    "why": finding["message"],
                    "commands": [
                        f"kvault tree {fpath} --gist --kb-root {root}",
                        f"# write one current-state node: kvault write {fpath}/{d['key']} "
                        "--create <<'EOF' … EOF",
                        "# move the timeline into journal/ (kvault journal), then delete the "
                        "dated nodes (kvault delete --confirm)",
                    ],
                    "members": d.get("members", []),
                }
            )
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
                        f"kvault write {fpath} --create --kb-root {root} "
                        "<<'EOF' … (frontmatter + a rollup of what is inside) EOF",
                        f"# or, if it is tooling and not knowledge: "
                        f"echo '{fpath}/' >> {root}/{st.IGNORE_FILE}",
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
                    f"({', '.join(detail['paths'][:4])}): which one is home?"
                )
                items.append(
                    {
                        "kind": "siblings",
                        "priority": PRIORITY["siblings"],
                        "path": detail["paths"][0],
                        "why": finding["message"],
                        "commands": [
                            f"kvault read {p} --kb-root {root}" for p in detail["paths"][:4]
                        ]
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
                            f"kvault read {_join(fpath, a)} --kb-root {root}",
                            f"kvault read {_join(fpath, b)} --kb-root {root}",
                            "# same thing → merge and delete one; subtopic → "
                            f"kvault move --confirm {_join(fpath, b)} {_join(fpath, a)}/{b}",
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
                        f"kvault read-summary {anchor} --kb-root {root}",
                        f"kvault list {anchor} --kb-root {root}",
                        f"kvault write-summary {anchor} --kb-root {root} "
                        "<<'EOF' … (the rewritten rollup) EOF",
                    ],
                }
            )

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
                    f"kvault read {_join(parent, top.get('a'))} --kb-root {root}",
                    f"kvault read {_join(parent, top.get('b'))} --kb-root {root}",
                    "# same thing → merge and delete one; subtopic → "
                    f"kvault move --confirm {_join(parent, top.get('b'))} "
                    f"{_join(parent, top.get('a'))}/{top.get('b')}",
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
        for g in legacy[:8]:
            node = g["path"][: -len(".md")]
            commands.append(
                f"mkdir -p {root}/{node} && git mv {root}/{g['path']} {root}/{node}/_summary.md"
            )
        if len(legacy) > 8:
            commands.append(f"# … +{len(legacy) - 8} more legacy node files under {parent}")
        if legacy:
            commands.append(
                f"kvault update-summaries --kb-root {root}  # rewrite {parent} to cover the adopted nodes"
            )
        others = [g for g in group if g["detail"].get("kind") != "legacy_node_file"]
        if others:
            commands.append(
                f"mkdir -p {root}/{home} && git mv "
                + " ".join(f"{root}/{g['path']}" for g in others[:6])
                + f" {root}/{home}/"
            )
            if len(others) > 6:
                commands.append(f"# … +{len(others) - 6} more files under {parent}")
            commands.append(f"# or list tooling/generated files in {root}/{st.IGNORE_FILE}")
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
                next_step=f"kvault plan --limit {total}",
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
