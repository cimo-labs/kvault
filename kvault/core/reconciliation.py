"""Journal-first semantic reconciliation with policy gates and recovery."""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Set

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kvault.core.frontmatter import build_frontmatter, parse_frontmatter
from kvault.core.transactions import (
    FileTransaction,
    KBWriteLock,
    LockBusyError,
    TransactionError,
    atomic_write_bytes,
    atomic_write_text,
    file_revision,
)

DecisionOutcome = Literal["apply", "journal_only", "duplicate", "no_op"]
MutationOperation = Literal["create", "update", "summary", "move", "merge", "delete"]


class EventDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    outcome: DecisionOutcome
    reasoning: str = Field(min_length=1)
    target_paths: List[str] = Field(default_factory=list)


class Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: MutationOperation
    path: str
    content: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    expected_revision: Optional[str] = None
    source_path: Optional[str] = None
    expected_source_revision: Optional[str] = None

    @model_validator(mode="after")
    def validate_shape(self) -> "Mutation":
        if self.operation in {"create", "update", "summary", "merge"} and self.content is None:
            raise ValueError(f"{self.operation} mutation requires content")
        if self.operation in {"move", "merge"} and not self.source_path:
            raise ValueError(f"{self.operation} mutation requires source_path")
        if self.operation == "create" and self.expected_revision is not None:
            raise ValueError("create mutation expected_revision must be null")
        return self


class ReconciliationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    event_ids: List[str] = Field(min_length=1)
    decisions: List[EventDecision] = Field(min_length=1)
    mutations: List[Mutation] = Field(default_factory=list)
    reasoning: str = Field(min_length=1)
    requested_by: str = "agent"

    @model_validator(mode="after")
    def validate_events(self) -> "ReconciliationPlan":
        event_ids = set(self.event_ids)
        decision_ids = {decision.event_id for decision in self.decisions}
        if len(event_ids) != len(self.event_ids):
            raise ValueError("event_ids must be unique")
        if decision_ids != event_ids or len(decision_ids) != len(self.decisions):
            raise ValueError("decisions must cover every event exactly once")
        has_apply = any(decision.outcome == "apply" for decision in self.decisions)
        if has_apply != bool(self.mutations):
            raise ValueError(
                "apply decisions require mutations, and mutations require apply decisions"
            )
        return self


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    reconciliation_id: str
    status: Literal["applied", "needs_review", "failed", "recovered"]
    event_outcomes: Dict[str, str]
    changed_paths: List[str] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)
    validation: Dict[str, Any] = Field(default_factory=dict)
    rollback_performed: bool = False
    error: Optional[str] = None
    approved_by: Optional[str] = None


class ReconciliationError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_reconciliation_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"rec_{stamp}_{uuid.uuid4().hex[:12]}"


def _normalize_path(path: str) -> str:
    raw = path.strip().rstrip("/") or "."
    if raw.endswith("/_summary.md"):
        raw = raw[: -len("/_summary.md")]
    return raw


def _summary_relative(path: str) -> str:
    path = _normalize_path(path)
    return "_summary.md" if path == "." else f"{path}/_summary.md"


def _summary_path(root: Path, path: str) -> Path:
    from kvault.core.paths import resolve_within_root

    return resolve_within_root(
        root,
        _summary_relative(path),
        allow_root=False,
        reject_symlinks=True,
    )


def _ancestors(path: str) -> List[str]:
    path = _normalize_path(path)
    if path == ".":
        return []
    parts = Path(path).parts
    parents = [str(Path(*parts[:idx])) for idx in range(len(parts) - 1, 0, -1)]
    parents.append(".")
    return parents


def _required_summary_paths(mutations: Sequence[Mutation]) -> Set[str]:
    required: Set[str] = set()
    for mutation in mutations:
        if mutation.operation == "summary":
            continue
        for path in _ancestors(mutation.path):
            required.add(path)
        if mutation.source_path:
            for path in _ancestors(mutation.source_path):
                required.add(path)
    return required


def _validate_summary_coverage(plan: ReconciliationPlan) -> None:
    required = _required_summary_paths(plan.mutations)
    provided = {
        _normalize_path(item.path) for item in plan.mutations if item.operation == "summary"
    }
    missing = sorted(required - provided)
    extra = sorted(provided - required)
    if missing or extra:
        raise ReconciliationError(
            "invalid_summary_set",
            "Reconciliation must supply exactly every affected ancestor summary",
            details={"missing": missing, "extra": extra},
        )


def _validate_mutation_conflicts(plan: ReconciliationPlan) -> None:
    destinations: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    for mutation in plan.mutations:
        path = _normalize_path(mutation.path)
        if path in destinations:
            raise ReconciliationError(
                "conflicting_mutations",
                f"Multiple mutations target the same node: {path}",
            )
        destinations[path] = mutation.operation
        if mutation.source_path is not None:
            source = _normalize_path(mutation.source_path)
            if source == path:
                raise ReconciliationError(
                    "conflicting_mutations",
                    f"Source and target must differ: {path}",
                )
            if source in sources:
                raise ReconciliationError(
                    "conflicting_mutations",
                    f"Multiple mutations consume the same source: {source}",
                )
            sources[source] = mutation.operation

    for source in sources:
        if source in destinations:
            raise ReconciliationError(
                "conflicting_mutations",
                f"A node cannot be both a source and a target: {source}",
            )


def _leaf_paths(mutation: Mutation) -> Set[str]:
    paths = {_normalize_path(mutation.path)}
    if mutation.source_path is not None:
        paths.add(_normalize_path(mutation.source_path))
    return paths


def _is_at_or_below(path: str, ancestor: str) -> bool:
    path = _normalize_path(path)
    ancestor = _normalize_path(ancestor)
    return ancestor == "." or path == ancestor or path.startswith(ancestor + "/")


def _mutation_event_ids(plan: ReconciliationPlan, mutation: Mutation) -> List[str]:
    """Return only the applied events whose declared targets affect a mutation."""
    relevant: List[str] = []
    for decision in plan.decisions:
        if decision.outcome != "apply":
            continue
        targets = {_normalize_path(path) for path in decision.target_paths}
        if mutation.operation == "summary":
            applies = any(_is_at_or_below(target, mutation.path) for target in targets)
        else:
            applies = bool(targets & _leaf_paths(mutation))
        if applies:
            relevant.append(decision.event_id)
    return relevant


def _validate_provenance_coverage(plan: ReconciliationPlan) -> None:
    leaves = [mutation for mutation in plan.mutations if mutation.operation != "summary"]
    touched = set().union(*(_leaf_paths(mutation) for mutation in leaves)) if leaves else set()
    for decision in plan.decisions:
        targets = {_normalize_path(path) for path in decision.target_paths}
        if decision.outcome == "apply":
            if not targets:
                raise ReconciliationError(
                    "invalid_provenance",
                    f"Apply decision has no target paths: {decision.event_id}",
                )
            unknown = sorted(targets - touched)
            if unknown:
                raise ReconciliationError(
                    "invalid_provenance",
                    f"Apply decision names paths with no leaf mutation: {decision.event_id}",
                    details={"unknown_target_paths": unknown},
                )
        elif targets:
            raise ReconciliationError(
                "invalid_provenance",
                f"Non-apply decision must not name target paths: {decision.event_id}",
            )

    for mutation in plan.mutations:
        if not _mutation_event_ids(plan, mutation):
            raise ReconciliationError(
                "invalid_provenance",
                f"Mutation is not backed by an applied event: {mutation.operation} {mutation.path}",
            )


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _load_event(root: Path, event_id: str) -> Dict[str, Any]:
    from kvault.core.events import get_event

    event = get_event(root, event_id)
    if event is None:
        raise ReconciliationError("event_not_found", f"Event not found: {event_id}")
    return _as_dict(event)


def _event_state(root: Path, event_id: str) -> str:
    from kvault.core.events import derive_event_states

    states = derive_event_states(root)
    raw = states.get(event_id, "pending")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return str(raw.get("state", "pending"))
    state = getattr(raw, "state", raw)
    return str(getattr(state, "value", state))


def _require_current_schema(root: Path) -> None:
    from kvault.core.migration import current_schema_version

    version = current_schema_version(root)
    if version != 1:
        raise ReconciliationError(
            "migration_required",
            "KB must be migrated before semantic mutation",
            details={"current_schema_version": version, "required_schema_version": 1},
        )


def _policy_dict(root: Path) -> Dict[str, Any]:
    from kvault.core.policy import load_policy

    return _as_dict(load_policy(root))


def _plan_review_reasons(
    root: Path, plan: ReconciliationPlan, events: Dict[str, Dict[str, Any]]
) -> List[str]:
    policy = _policy_dict(root)
    reasons: List[str] = []
    auto_operations = set(policy.get("auto_apply_operations", ["create", "update", "summary"]))
    auto_outcomes = set(policy.get("auto_resolve_outcomes", ["duplicate", "no_op", "journal_only"]))
    review_operations = set(
        policy.get("review_operations", ["move", "merge", "delete", "restructure"])
    )
    review_sensitivities = set(policy.get("review_sensitivities", ["sensitive", "restricted"]))

    for event_id, event in events.items():
        sensitivity = str(event.get("sensitivity", "personal"))
        if sensitivity in review_sensitivities:
            reasons.append(f"event {event_id} sensitivity is {sensitivity}")

    for decision in plan.decisions:
        if decision.outcome != "apply" and decision.outcome not in auto_outcomes:
            reasons.append(f"outcome requires review: {decision.outcome}")

    for mutation in plan.mutations:
        if mutation.operation in review_operations or mutation.operation not in auto_operations:
            reasons.append(f"operation requires review: {mutation.operation} {mutation.path}")
        if mutation.operation == "update" and policy.get("additive_updates_only", True):
            current = _summary_path(root, mutation.path)
            if current.exists() and mutation.content is not None:
                meta, body = parse_frontmatter(current.read_text(encoding="utf-8"))
                old_lines = {line.rstrip() for line in body.splitlines() if line.strip()}
                new_lines = {
                    line.rstrip() for line in mutation.content.splitlines() if line.strip()
                }
                removed = sorted(old_lines - new_lines)
                if removed:
                    reasons.append(f"update removes or replaces existing text: {mutation.path}")
                for key, old_value in meta.items():
                    if key in {"created", "updated", "children_digest", "source_refs"}:
                        continue
                    if key in mutation.meta and mutation.meta[key] != old_value:
                        if key != "aliases" or not set(old_value or []).issubset(
                            set(mutation.meta[key] or [])
                        ):
                            reasons.append(f"update replaces metadata field {key}: {mutation.path}")
    return sorted(set(reasons))


def prepare_reconciliation(
    root: Path, event_ids: Sequence[str], paths: Optional[Sequence[str]] = None
) -> Dict[str, Any]:
    """Return immutable events, policy, bounded orientation, and requested revisions."""
    _require_current_schema(root)
    from kvault.core import operations as ops

    events = []
    states: Dict[str, str] = {}
    for event_id in event_ids:
        events.append(_load_event(root, event_id))
        states[event_id] = _event_state(root, event_id)
    outline = ops.build_outline(root, path=".", depth=2, max_children=20, include_gist=False)
    revisions: Dict[str, Optional[str]] = {}
    for path in paths or []:
        normalized = _normalize_path(path)
        revisions[normalized] = file_revision(_summary_path(root, normalized))
    return {
        "success": True,
        "events": events,
        "event_states": states,
        "policy": _policy_dict(root),
        "orientation": outline,
        "revisions": revisions,
    }


def _validate_revisions(root: Path, mutations: Sequence[Mutation]) -> None:
    from kvault.core.paths import resolve_node_path, validate_node_target
    from kvault.core.validation import validate_entity_path

    for mutation in mutations:
        path = _normalize_path(mutation.path)
        if mutation.operation == "create":
            valid, message = validate_entity_path(path)
            if not valid:
                raise ReconciliationError("invalid_path", message or f"Invalid path: {path}")
            target_dir = resolve_node_path(root, path, allow_root=False)
            if target_dir.exists():
                raise ReconciliationError("already_exists", f"Target already exists: {path}")
            parent = target_dir.parent
            if parent != root and not (parent / "_summary.md").is_file():
                raise ReconciliationError(
                    "missing_parent_node",
                    f"Immediate parent is not a node: {parent.relative_to(root)}",
                )
            continue

        if mutation.operation in {"update", "summary"}:
            if path == ".":
                summary = _summary_path(root, path)
            else:
                node = resolve_node_path(root, path, allow_root=False, must_exist=True)
                summary = node / "_summary.md"
            if not summary.is_file() or summary.is_symlink():
                raise ReconciliationError("not_found", f"Node summary does not exist: {path}")
            if not mutation.expected_revision:
                raise ReconciliationError(
                    "expected_revision_required", f"Expected revision is required: {path}"
                )
            actual = file_revision(summary)
            if actual != mutation.expected_revision:
                raise ReconciliationError(
                    "stale_plan",
                    f"Revision changed for {path}",
                    details={
                        "path": path,
                        "expected": mutation.expected_revision,
                        "actual": actual,
                    },
                )
            continue

        if mutation.operation == "delete":
            target = validate_node_target(root, path, require_exists=True)
            if not mutation.expected_revision:
                raise ReconciliationError(
                    "expected_revision_required", f"Expected revision is required: {path}"
                )
            actual = file_revision(target / "_summary.md")
            if actual != mutation.expected_revision:
                raise ReconciliationError(
                    "stale_plan",
                    f"Revision changed for {path}",
                    details={
                        "path": path,
                        "expected": mutation.expected_revision,
                        "actual": actual,
                    },
                )
            continue

        if mutation.operation in {"move", "merge"}:
            assert mutation.source_path is not None
            source_path = _normalize_path(mutation.source_path)
            source = validate_node_target(root, source_path, require_exists=True)
            if mutation.operation == "merge":
                extra_entries = sorted(
                    str(item.relative_to(source))
                    for item in source.rglob("*")
                    if item.relative_to(source) != Path("_summary.md")
                )
                if extra_entries:
                    raise ReconciliationError(
                        "merge_source_not_leaf",
                        "Merge source must contain only its _summary.md; move or reconcile "
                        "descendants and attachments explicitly first",
                        details={"source_path": source_path, "entries": extra_entries[:20]},
                    )
            if not mutation.expected_source_revision:
                raise ReconciliationError(
                    "expected_revision_required",
                    f"Expected source revision is required: {source_path}",
                )
            actual_source = file_revision(source / "_summary.md")
            if actual_source != mutation.expected_source_revision:
                raise ReconciliationError(
                    "stale_plan",
                    f"Revision changed for {source_path}",
                    details={
                        "path": source_path,
                        "expected": mutation.expected_source_revision,
                        "actual": actual_source,
                    },
                )
            target = resolve_node_path(root, path, allow_root=False)
            if mutation.operation == "move":
                if target.exists():
                    raise ReconciliationError("already_exists", f"Move target exists: {path}")
                if target.parent != root and not (target.parent / "_summary.md").is_file():
                    raise ReconciliationError(
                        "missing_parent_node",
                        f"Move target parent is not a node: {target.parent.relative_to(root)}",
                    )
            else:
                validate_node_target(root, path, require_exists=True)
                if not mutation.expected_revision:
                    raise ReconciliationError(
                        "expected_revision_required",
                        f"Expected target revision is required: {path}",
                    )
                actual_target = file_revision(target / "_summary.md")
                if actual_target != mutation.expected_revision:
                    raise ReconciliationError(
                        "stale_plan",
                        f"Revision changed for {path}",
                        details={
                            "path": path,
                            "expected": mutation.expected_revision,
                            "actual": actual_target,
                        },
                    )
            if mutation.operation == "move":
                try:
                    target.relative_to(source)
                except ValueError:
                    pass
                else:
                    raise ReconciliationError(
                        "invalid_move",
                        "A node cannot be moved inside its own subtree",
                        details={"source": source_path, "target": path},
                    )


def _snapshot_paths(mutations: Sequence[Mutation]) -> List[str]:
    paths: Set[str] = set()
    for mutation in mutations:
        path = _normalize_path(mutation.path)
        if mutation.operation == "create":
            paths.add(path)
        elif mutation.operation in {"update", "summary"}:
            paths.add(_summary_relative(path))
        elif mutation.operation == "delete":
            paths.add(path)
        elif mutation.operation == "move":
            assert mutation.source_path
            paths.add(_normalize_path(mutation.source_path))
            paths.add(path)
        elif mutation.operation == "merge":
            assert mutation.source_path
            paths.add(_normalize_path(mutation.source_path))
            paths.add(_summary_relative(path))
    return sorted(paths)


def _clone_semantic_tree(root: Path, destination: Path) -> None:
    """Copy the complete semantic-node tree, excluding control/event storage."""
    destination.mkdir(parents=True)

    def clone_node(source: Path, target: Path) -> None:
        for item in sorted(source.iterdir()):
            if item.name.startswith((".", "_")) and item.name != "_summary.md":
                continue
            if item.is_symlink():
                raise ReconciliationError(
                    "unsafe_symlink",
                    f"Semantic node contains a symlink: {item.relative_to(root)}",
                )
            if item.is_file():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target / item.name)
                continue
            if item.is_dir():
                child_summary = item / "_summary.md"
                if child_summary.is_file() and not child_summary.is_symlink():
                    child_target = target / item.name
                    child_target.mkdir(parents=True)
                    clone_node(item, child_target)

    clone_node(root, destination)


def _stage_reconciliation(
    root: Path,
    transaction: FileTransaction,
    plan: ReconciliationPlan,
) -> tuple[Path, List[str], Dict[str, Any]]:
    stage_root = transaction.stage_dir / "tree"
    _clone_semantic_tree(root, stage_root)
    changed: List[str] = []
    leaves = [item for item in plan.mutations if item.operation != "summary"]
    summaries = sorted(
        (item for item in plan.mutations if item.operation == "summary"),
        key=lambda item: (0 if _normalize_path(item.path) == "." else item.path.count("/") + 1),
        reverse=True,
    )
    for mutation in leaves:
        changed.extend(
            _apply_leaf_mutation(stage_root, mutation, _mutation_event_ids(plan, mutation))
        )
    for mutation in summaries:
        changed.append(
            _write_node_content(
                stage_root,
                mutation,
                _mutation_event_ids(plan, mutation),
                create=False,
                summary=True,
            )
        )
    validation = _integrity_payload(stage_root)
    if not validation.get("valid", False):
        raise ReconciliationError(
            "integrity_failed",
            "Staged reconciliation failed KB integrity validation",
            details={"validation": validation},
        )
    transaction.mark_staged(changed)
    return stage_root, changed, validation


def _atomic_install_directory(staged: Path, destination: Path) -> None:
    if destination.exists():
        raise ReconciliationError("already_exists", f"Target already exists: {destination}")
    os.replace(staged, destination)
    try:
        descriptor = os.open(destination.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _move_to_transaction_trash(
    root: Path,
    transaction: FileTransaction,
    relative_path: str,
) -> None:
    from kvault.core.paths import validate_node_target

    source = validate_node_target(root, relative_path, require_exists=True)
    trash = transaction.trash_dir / relative_path
    trash.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, trash)


def _apply_staged_to_live(
    root: Path,
    stage_root: Path,
    transaction: FileTransaction,
    plan: ReconciliationPlan,
) -> List[str]:
    from kvault.core.paths import resolve_node_path

    changed: List[str] = []
    leaves = [item for item in plan.mutations if item.operation != "summary"]
    summaries = sorted(
        (item for item in plan.mutations if item.operation == "summary"),
        key=lambda item: (0 if _normalize_path(item.path) == "." else item.path.count("/") + 1),
        reverse=True,
    )
    for mutation in leaves:
        path = _normalize_path(mutation.path)
        if mutation.operation == "create":
            destination = resolve_node_path(root, path)
            _atomic_install_directory(
                resolve_node_path(stage_root, path, must_exist=True), destination
            )
            applied = [_summary_relative(path)]
        elif mutation.operation == "update":
            atomic_write_bytes(
                _summary_path(root, path), _summary_path(stage_root, path).read_bytes()
            )
            applied = [_summary_relative(path)]
        elif mutation.operation == "delete":
            _move_to_transaction_trash(root, transaction, path)
            applied = [path]
        elif mutation.operation == "move":
            assert mutation.source_path is not None
            source_path = _normalize_path(mutation.source_path)
            _move_to_transaction_trash(root, transaction, source_path)
            destination = resolve_node_path(root, path)
            _atomic_install_directory(
                resolve_node_path(stage_root, path, must_exist=True), destination
            )
            applied = [source_path, path]
        elif mutation.operation == "merge":
            assert mutation.source_path is not None
            atomic_write_bytes(
                _summary_path(root, path), _summary_path(stage_root, path).read_bytes()
            )
            source_path = _normalize_path(mutation.source_path)
            _move_to_transaction_trash(root, transaction, source_path)
            applied = [_summary_relative(path), source_path]
        else:  # pragma: no cover - exhaustive Literal guard
            raise ReconciliationError(
                "invalid_operation", f"Unknown operation: {mutation.operation}"
            )
        changed.extend(applied)
        for relative in applied:
            transaction.mark_applied(relative)

    for mutation in summaries:
        path = _normalize_path(mutation.path)
        relative = _summary_relative(path)
        atomic_write_bytes(_summary_path(root, path), _summary_path(stage_root, path).read_bytes())
        changed.append(relative)
        transaction.mark_applied(relative)
    return changed


def _read_existing(root: Path, path: str) -> tuple[Dict[str, Any], str]:
    summary = _summary_path(root, path)
    if not summary.exists():
        return {}, ""
    return parse_frontmatter(summary.read_text(encoding="utf-8"))


def _resolved_meta(
    root: Path,
    path: str,
    incoming: Dict[str, Any],
    event_ids: Sequence[str],
    *,
    create: bool,
    summary: bool = False,
) -> Dict[str, Any]:
    existing, _ = _read_existing(root, path)
    result = dict(existing)
    if create:
        result.update(incoming)
    else:
        for key, value in incoming.items():
            if key == "aliases":
                result[key] = sorted(
                    {str(item) for item in result.get(key, []) + list(value or [])}
                )
            elif key == "source_refs":
                continue
            elif key not in {"created", "updated", "children_digest"}:
                result[key] = value

    refs = {str(item) for item in result.get("source_refs", [])}
    refs.update(f"journal:{event_id}" for event_id in event_ids)
    result["source_refs"] = sorted(refs)
    if not result.get("source"):
        result["source"] = f"journal:{event_ids[0]}"
    aliases = result.get("aliases", [])
    if not isinstance(aliases, list):
        raise ReconciliationError("invalid_metadata", f"aliases must be a list: {path}")
    result["aliases"] = aliases
    today = datetime.now().date().isoformat()
    if create:
        result["created"] = today
    result.setdefault("created", today)
    result["updated"] = today
    if summary:
        from kvault.core.validation import compute_children_digest

        result["children_digest"] = compute_children_digest(root, path)
    return result


def _write_node_content(
    root: Path,
    mutation: Mutation,
    event_ids: Sequence[str],
    *,
    create: bool,
    summary: bool = False,
) -> str:
    path = _normalize_path(mutation.path)
    meta = _resolved_meta(root, path, mutation.meta, event_ids, create=create, summary=summary)
    destination = _summary_path(root, path)
    if create:
        destination.parent.mkdir(parents=True, exist_ok=False)
    elif not destination.parent.is_dir():
        raise ReconciliationError("not_found", f"Node does not exist: {path}")
    atomic_write_text(destination, build_frontmatter(meta) + (mutation.content or "").lstrip("\n"))
    return _summary_relative(path)


def _apply_leaf_mutation(root: Path, mutation: Mutation, event_ids: Sequence[str]) -> List[str]:
    from kvault.core.paths import resolve_node_path, validate_node_target

    path = _normalize_path(mutation.path)
    if mutation.operation == "create":
        return [_write_node_content(root, mutation, event_ids, create=True)]
    if mutation.operation == "update":
        return [_write_node_content(root, mutation, event_ids, create=False)]
    if mutation.operation == "delete":
        target = validate_node_target(root, path, require_exists=True)
        shutil.rmtree(target)
        return [path]
    if mutation.operation == "move":
        assert mutation.source_path
        source = validate_node_target(root, mutation.source_path, require_exists=True)
        target = resolve_node_path(root, path, allow_root=False)
        shutil.move(str(source), str(target))
        meta, body = _read_existing(root, path)
        moved = Mutation(operation="update", path=path, content=body, meta=meta)
        _write_node_content(root, moved, event_ids, create=False)
        return [_normalize_path(mutation.source_path), path]
    if mutation.operation == "merge":
        assert mutation.source_path
        changed = [_write_node_content(root, mutation, event_ids, create=False)]
        source = validate_node_target(root, mutation.source_path, require_exists=True)
        shutil.rmtree(source)
        changed.append(_normalize_path(mutation.source_path))
        return changed
    raise ReconciliationError("invalid_operation", f"Not a leaf mutation: {mutation.operation}")


def _event_outcomes(plan: ReconciliationPlan) -> Dict[str, str]:
    return {
        decision.event_id: ("applied" if decision.outcome == "apply" else decision.outcome)
        for decision in plan.decisions
    }


def _persist_plan(
    root: Path,
    reconciliation_id: str,
    plan: ReconciliationPlan,
    *,
    review_required: bool,
    review_reasons: Sequence[str],
) -> None:
    from kvault.core.events import write_reconciliation_plan

    payload = plan.model_dump(mode="json")
    payload.update(
        {
            "reconciliation_id": reconciliation_id,
            "created_at": _now(),
            "review_required": review_required,
            "review_reasons": list(review_reasons),
        }
    )
    write_reconciliation_plan(root, reconciliation_id, payload)


def _persist_result(root: Path, result: ReconciliationResult) -> None:
    from kvault.core.events import write_reconciliation_result

    write_reconciliation_result(root, result.reconciliation_id, result.model_dump(mode="json"))


def _integrity_payload(root: Path) -> Dict[str, Any]:
    from kvault.core.validation import audit_kb

    raw = audit_kb(root, check_journal=False)
    payload = _as_dict(raw)
    if "valid" not in payload:
        errors = payload.get("errors", [])
        issues = payload.get("issues", [])
        payload["valid"] = not errors and not any(
            issue.get("severity") == "error" for issue in issues if isinstance(issue, dict)
        )
    return payload


def apply_reconciliation(
    root: Path,
    plan: ReconciliationPlan | Dict[str, Any],
    *,
    reconciliation_id: Optional[str] = None,
    approved_by: Optional[str] = None,
    persist_plan: bool = True,
) -> ReconciliationResult:
    """Validate and apply a complete event-backed semantic reconciliation."""
    root = Path(root).resolve()
    _require_current_schema(root)
    parsed = (
        plan if isinstance(plan, ReconciliationPlan) else ReconciliationPlan.model_validate(plan)
    )
    reconciliation_id = reconciliation_id or _new_reconciliation_id()
    _validate_mutation_conflicts(parsed)
    _validate_summary_coverage(parsed)
    _validate_provenance_coverage(parsed)
    transaction = FileTransaction(root, reconciliation_id)
    changed: List[str] = []
    durable_result: Optional[ReconciliationResult] = None
    outcomes = _event_outcomes(parsed)

    try:
        with KBWriteLock(root, reconciliation_id):
            # State discovery and plan creation are part of the same serialized
            # critical section as the semantic write. Otherwise two writers can
            # both observe ``pending`` and leave contradictory latest outcomes.
            events: Dict[str, Dict[str, Any]] = {}
            from kvault.core.events import EventStatus, derive_event_states

            states = derive_event_states(root)
            for event_id in parsed.event_ids:
                events[event_id] = _load_event(root, event_id)
                state = states[event_id]
                if persist_plan:
                    if state.state != EventStatus.PENDING:
                        raise ReconciliationError(
                            "event_not_pending",
                            f"Event is not pending: {event_id} ({state.state.value})",
                        )
                elif not (
                    state.state == EventStatus.NEEDS_REVIEW
                    and state.reconciliation_id == reconciliation_id
                ):
                    raise ReconciliationError(
                        "approval_state_invalid",
                        f"Reconciliation is not awaiting review for event: {event_id}",
                    )

            review_reasons = _plan_review_reasons(root, parsed, events)
            if persist_plan:
                _persist_plan(
                    root,
                    reconciliation_id,
                    parsed,
                    review_required=bool(review_reasons),
                    review_reasons=review_reasons,
                )

            if review_reasons and not approved_by:
                return ReconciliationResult(
                    success=False,
                    reconciliation_id=reconciliation_id,
                    status="needs_review",
                    event_outcomes=outcomes,
                    review_reasons=review_reasons,
                )

            try:
                # A terminal no-mutation result still closes the captured events.
                if not parsed.mutations:
                    result = ReconciliationResult(
                        success=True,
                        reconciliation_id=reconciliation_id,
                        status="applied",
                        event_outcomes=outcomes,
                        approved_by=approved_by,
                        validation={"valid": True, "issues": []},
                    )
                    _persist_result(root, result)
                    return result

                _validate_revisions(root, parsed.mutations)
                transaction.begin(_snapshot_paths(parsed.mutations))
                stage_root, _staged_paths, validation = _stage_reconciliation(
                    root,
                    transaction,
                    parsed,
                )
                transaction.mark_applying()
                changed.extend(
                    _apply_staged_to_live(
                        root,
                        stage_root,
                        transaction,
                        parsed,
                    )
                )

                validation = _integrity_payload(root)
                if not validation.get("valid", False):
                    raise ReconciliationError(
                        "integrity_failed",
                        "Staged reconciliation failed KB integrity validation",
                        details={"validation": validation},
                    )

                result = ReconciliationResult(
                    success=True,
                    reconciliation_id=reconciliation_id,
                    status="applied",
                    event_outcomes=outcomes,
                    changed_paths=sorted(set(changed)),
                    validation=validation,
                    approved_by=approved_by,
                )
                _persist_result(root, result)
                # result.md is the durable commit decision. If transaction-state
                # cleanup fails after this point, recovery must finish the commit;
                # it must never roll back a tree whose immutable result says applied.
                durable_result = result
                try:
                    transaction.commit()
                except (OSError, TransactionError) as exc:
                    result.validation = dict(result.validation)
                    result.validation["transaction_finalization_pending"] = True
                    result.validation["transaction_finalization_error"] = str(exc)
                return result
            except (ReconciliationError, TransactionError, OSError, ValueError) as exc:
                if durable_result is not None:
                    durable_result.validation = dict(durable_result.validation)
                    durable_result.validation["transaction_finalization_pending"] = True
                    durable_result.validation["transaction_finalization_error"] = str(exc)
                    return durable_result
                rolled_back = False
                if transaction.state.get("status") in {"prepared", "staged", "applying"}:
                    try:
                        # Rollback deliberately happens while the KB write lock is
                        # still held; no other writer can observe partial live state.
                        transaction.rollback(str(exc))
                        rolled_back = True
                    except Exception as rollback_exc:  # pragma: no cover
                        exc = ReconciliationError(
                            "rollback_failed",
                            f"{exc}; rollback also failed: {rollback_exc}",
                        )
                code = exc.code if isinstance(exc, ReconciliationError) else "apply_failed"
                details = exc.details if isinstance(exc, ReconciliationError) else {}
                result = ReconciliationResult(
                    success=False,
                    reconciliation_id=reconciliation_id,
                    status="failed",
                    event_outcomes=outcomes,
                    changed_paths=sorted(set(changed)),
                    validation=details.get("validation", {}),
                    rollback_performed=rolled_back,
                    error=f"{code}: {exc}",
                    approved_by=approved_by,
                )
                _persist_result(root, result)
                return result
    except LockBusyError as exc:
        raise ReconciliationError("lock_busy", str(exc)) from exc


def approve_reconciliation(root: Path, reconciliation_id: str, actor: str) -> ReconciliationResult:
    """Apply an immutable review-gated plan after explicit human approval."""
    if not actor.strip():
        raise ReconciliationError("actor_required", "Approval actor is required")
    from kvault.core.events import read_reconciliation_plan

    raw = read_reconciliation_plan(root, reconciliation_id)
    if raw is None:
        raise ReconciliationError(
            "reconciliation_not_found", f"Reconciliation not found: {reconciliation_id}"
        )
    plan_payload = dict(raw.plan)
    for field in ("reconciliation_id", "created_at", "review_required", "review_reasons"):
        plan_payload.pop(field, None)
    return apply_reconciliation(
        root,
        ReconciliationPlan.model_validate(plan_payload),
        reconciliation_id=reconciliation_id,
        approved_by=actor,
        persist_plan=False,
    )


def reconciliation_status(root: Path, reconciliation_id: str) -> Dict[str, Any]:
    from kvault.core.events import read_reconciliation_plan, reconciliation_paths

    plan = read_reconciliation_plan(root, reconciliation_id)
    if plan is None:
        raise ReconciliationError(
            "reconciliation_not_found", f"Reconciliation not found: {reconciliation_id}"
        )
    paths = reconciliation_paths(root, reconciliation_id)
    result_payload: Optional[Dict[str, Any]] = None
    result_path = Path(paths["result_path"] if isinstance(paths, dict) else paths.result_path)
    if result_path.exists():
        from kvault.core.events import read_reconciliation_result

        result = read_reconciliation_result(root, reconciliation_id)
        result_payload = _as_dict(result) if result is not None else None
    tx = FileTransaction(root, reconciliation_id)
    return {
        "success": True,
        "reconciliation_id": reconciliation_id,
        "plan": _as_dict(plan),
        "result": result_payload,
        "transaction": tx.state or None,
    }


def recover_reconciliations(root: Path) -> Dict[str, Any]:
    """Resolve interrupted transactions without discarding a live owner's lock."""
    root = Path(root).resolve()
    lock = KBWriteLock(root, "recovery")
    if lock.lock_dir.exists():
        if not lock.is_stale():
            raise ReconciliationError(
                "lock_active", "Cannot recover while another process owns the KB write lock"
            )
        shutil.rmtree(lock.lock_dir, ignore_errors=True)

    recovered: List[Dict[str, Any]] = []
    with lock:
        for transaction in FileTransaction.active(root):
            from kvault.core.events import (
                read_reconciliation_plan,
                read_reconciliation_result,
            )

            raw_result = read_reconciliation_result(root, transaction.transaction_id)
            result = dict(raw_result.result) if raw_result is not None else None
            if result and result.get("success") and result.get("status") == "applied":
                transaction.commit()
                action = "finalized"
            else:
                transaction.rollback("automatic recovery")
                action = "rolled_back"
                if raw_result is None:
                    plan = read_reconciliation_plan(root, transaction.transaction_id)
                    if plan is not None:
                        _persist_result(
                            root,
                            ReconciliationResult(
                                success=False,
                                reconciliation_id=transaction.transaction_id,
                                status="recovered",
                                event_outcomes={event_id: "failed" for event_id in plan.event_ids},
                                rollback_performed=True,
                                error="Interrupted reconciliation rolled back during recovery",
                            ),
                        )
            recovered.append({"reconciliation_id": transaction.transaction_id, "action": action})

        # A process can stop after its immutable plan is created but before its
        # transaction directory exists. Such a non-review plan is not actionable
        # recovery state and would otherwise leave every event "reconciling"
        # forever. Record a failed attempt so the evidence becomes pending again.
        from kvault.core.events import EventStatus, derive_event_states, read_reconciliation_plan

        stalled_ids = {
            state.reconciliation_id
            for state in derive_event_states(root).values()
            if state.state == EventStatus.RECONCILING and state.reconciliation_id is not None
        }
        for reconciliation_id in sorted(stalled_ids):
            plan = read_reconciliation_plan(root, reconciliation_id)
            if plan is None:
                continue
            _persist_result(
                root,
                ReconciliationResult(
                    success=False,
                    reconciliation_id=reconciliation_id,
                    status="recovered",
                    event_outcomes={event_id: "failed" for event_id in plan.event_ids},
                    error="Incomplete plan released during recovery before a transaction began",
                ),
            )
            recovered.append(
                {"reconciliation_id": reconciliation_id, "action": "released_pending_plan"}
            )
    return {"success": True, "recovered": recovered, "count": len(recovered)}
