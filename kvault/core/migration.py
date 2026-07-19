"""Schema migration and legacy Moss capture import helpers."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kvault.core.events import (
    EventError,
    EventStatus,
    ReconciliationOutcome,
    Sensitivity,
    capture_event,
    derive_event_states,
    write_reconciliation_plan,
    write_reconciliation_result,
)
from kvault.core.frontmatter import FrontmatterError, build_frontmatter, parse_frontmatter
from kvault.core.paths import PathSafetyError, resolve_within_root
from kvault.core.policy import ensure_policy, load_policy, policy_path
from kvault.core.transactions import (
    FileTransaction,
    KBWriteLock,
    TransactionError,
    atomic_write_json,
    atomic_write_text,
)

CURRENT_SCHEMA_VERSION = 1


class MigrationError(RuntimeError):
    """Raised for an invalid or unsupported KB schema."""


class MigrationRequiredError(MigrationError):
    """Raised when a semantic mutation is attempted before migration."""


class UnsupportedSchemaError(MigrationError):
    """Raised when a KB was written by a newer, unsupported schema."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SchemaState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    installed_at: datetime
    migrated_from: Optional[int] = None
    features: List[str] = Field(
        default_factory=lambda: [
            "journal_events_v1",
            "reconciliation_v1",
            "policy_v1",
        ]
    )

    @field_validator("installed_at")
    @classmethod
    def normalize_installed_at(cls, value: datetime) -> datetime:
        return _as_utc(value)


class DigestBackfillResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planned_paths: List[str] = Field(default_factory=list)
    updated_paths: List[str] = Field(default_factory=list)
    skipped_paths: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


DigestBackfillHook = Callable[[Path, bool], Union[DigestBackfillResult, Mapping[str, Any]]]


class MigrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    dry_run: bool
    migration_required: bool
    schema_before: Optional[int] = None
    schema_after: Optional[int] = None
    changes: List[str] = Field(default_factory=list)
    legacy_journal_paths: List[str] = Field(default_factory=list)
    digest_backfill: DigestBackfillResult = Field(default_factory=DigestBackfillResult)
    errors: List[str] = Field(default_factory=list)


class MossImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Literal["inbox", "processed"]
    source_record_id: str
    event_id: Optional[str] = None
    status: Literal[
        "created_pending",
        "created_legacy_archived_unknown",
        "existing",
        "source_ref_conflict",
        "dry_run",
        "error",
    ]
    error: Optional[str] = None


class MossImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    dry_run: bool
    total: int
    created: int = 0
    existing: int = 0
    conflicts: int = 0
    pending: int = 0
    legacy_archived_unknown: int = 0
    items: List[MossImportItem] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


def _root(root: Union[str, Path]) -> Path:
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise MigrationError(f"KB root is not a directory: {root_path}")
    return root_path


def schema_path(root: Union[str, Path]) -> Path:
    root_path = _root(root)
    try:
        return resolve_within_root(
            root_path,
            ".kvault/schema.json",
            allow_root=False,
            reject_symlinks=True,
        )
    except PathSafetyError as exc:
        raise MigrationError("Unsafe .kvault/schema.json path") from exc


def current_schema(root: Union[str, Path]) -> Optional[SchemaState]:
    """Load the current schema record, returning None only when it is absent."""
    path = schema_path(root)
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise MigrationError(".kvault/schema.json must be a regular file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise MigrationError(f"Could not parse schema state: {path}") from exc
    if not isinstance(raw, dict):
        raise MigrationError("Schema state must be a JSON object")
    raw_version = raw.get("schema_version")
    if isinstance(raw_version, int) and raw_version > CURRENT_SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"KB schema {raw_version} is newer than supported schema {CURRENT_SCHEMA_VERSION}"
        )
    try:
        return SchemaState.model_validate(raw)
    except ValueError as exc:
        raise MigrationError(f"Invalid schema state: {exc}") from exc


def current_schema_version(root: Union[str, Path]) -> Optional[int]:
    state = current_schema(root)
    return state.schema_version if state is not None else None


def require_schema(root: Union[str, Path]) -> SchemaState:
    state = current_schema(root)
    if state is None:
        raise MigrationRequiredError(
            "KB must be migrated before semantic mutation (run `kvault migrate`)"
        )
    if state.schema_version != CURRENT_SCHEMA_VERSION:
        raise MigrationRequiredError(
            f"KB schema {state.schema_version} must be migrated to {CURRENT_SCHEMA_VERSION}"
        )
    return state


def _legacy_journals(root: Path) -> List[str]:
    journal = root / "journal"
    if not journal.is_dir():
        return []
    result: List[str] = []
    for path in sorted(journal.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]/log.md")):
        if path.is_file():
            result.append(str(path.relative_to(root)))
    return result


def _coerce_digest_result(value: Any) -> DigestBackfillResult:
    if isinstance(value, DigestBackfillResult):
        return value
    if isinstance(value, Mapping):
        return DigestBackfillResult.model_validate(value)
    raise TypeError("digest backfill hook must return DigestBackfillResult or a mapping")


def _semantic_summaries(root: Path) -> List[Path]:
    """Return real summaries reachable through the semantic node tree."""
    summaries: List[Path] = []

    def visit(directory: Path) -> None:
        summary = directory / "_summary.md"
        if summary.is_file() and not summary.is_symlink():
            summaries.append(summary)
        try:
            children = list(directory.iterdir())
        except OSError as exc:
            raise MigrationError(f"Could not inspect node directory: {directory}") from exc
        for child in sorted(children):
            if not child.is_dir() or child.is_symlink() or child.name.startswith((".", "_")):
                continue
            child_summary = child / "_summary.md"
            if child_summary.is_file() and not child_summary.is_symlink():
                visit(child)

    visit(root)
    return summaries


def _has_direct_child_nodes(directory: Path) -> bool:
    return any(
        child.is_dir()
        and not child.is_symlink()
        and not child.name.startswith((".", "_"))
        and (child / "_summary.md").is_file()
        and not (child / "_summary.md").is_symlink()
        for child in directory.iterdir()
    )


def backfill_children_digests(root: Path, dry_run: bool) -> DigestBackfillResult:
    """Backfill exact immediate-child digests without changing Markdown bodies."""
    from kvault.core.validation import compute_children_digest

    root = Path(root).resolve()
    candidates: List[tuple[Path, str]] = []
    errors: List[str] = []
    summaries = sorted(
        _semantic_summaries(root),
        key=lambda item: len(item.relative_to(root).parts),
        reverse=True,
    )
    for summary in summaries:
        if not _has_direct_child_nodes(summary.parent):
            continue
        node_path = "." if summary.parent == root else str(summary.parent.relative_to(root))
        try:
            raw = summary.read_text(encoding="utf-8")
            meta, _body = parse_frontmatter(raw)
            digest = compute_children_digest(root, node_path)
        except (OSError, UnicodeError, FrontmatterError) as exc:
            errors.append(f"{summary.relative_to(root)}: {exc}")
            continue
        if meta.get("children_digest") != digest:
            candidates.append((summary, node_path))

    planned = [str(path.relative_to(root)) for path, _node_path in candidates]
    if dry_run or errors:
        return DigestBackfillResult(planned_paths=planned, errors=errors)

    updated: List[str] = []
    # Deepest-first is required because a changed child summary changes its
    # parent's exact digest.
    for summary, node_path in candidates:
        raw = summary.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        meta["children_digest"] = compute_children_digest(root, node_path)
        atomic_write_text(summary, build_frontmatter(meta) + body)
        updated.append(str(summary.relative_to(root)))
    return DigestBackfillResult(planned_paths=planned, updated_paths=updated)


def migrate(
    root: Union[str, Path],
    *,
    dry_run: bool = True,
    digest_backfill_hook: Optional[DigestBackfillHook] = None,
) -> MigrationResult:
    """Plan or apply the explicit schema-1 migration.

    The schema marker is written last.  If a digest hook fails, the KB remains
    migration-required and safe to retry.  Existing monthly journal files and
    semantic bodies are never touched by this module.
    """
    root_path = _root(root)
    before = current_schema(root_path)
    policy_file = policy_path(root_path)
    changes: List[str] = []
    if not policy_file.exists():
        changes.append("create .kvault/policy.yaml")
    if before is None:
        changes.append("create .kvault/schema.json")

    selected_hook = digest_backfill_hook or backfill_children_digests
    digest_result = DigestBackfillResult()
    if dry_run:
        try:
            digest_result = _coerce_digest_result(selected_hook(root_path, True))
        except Exception as exc:
            return MigrationResult(
                success=False,
                dry_run=dry_run,
                migration_required=before is None,
                schema_before=before.schema_version if before else None,
                schema_after=before.schema_version if before else None,
                changes=changes,
                legacy_journal_paths=_legacy_journals(root_path),
                errors=[f"digest backfill failed: {exc}"],
            )
        if digest_result.planned_paths:
            changes.append(
                f"backfill child digests for {len(digest_result.planned_paths)} summaries"
            )
        if digest_result.errors:
            return MigrationResult(
                success=False,
                dry_run=dry_run,
                migration_required=before is None,
                schema_before=before.schema_version if before else None,
                schema_after=before.schema_version if before else None,
                changes=changes,
                legacy_journal_paths=_legacy_journals(root_path),
                digest_backfill=digest_result,
                errors=list(digest_result.errors),
            )

    if dry_run:
        return MigrationResult(
            success=True,
            dry_run=True,
            migration_required=before is None,
            schema_before=before.schema_version if before else None,
            schema_after=CURRENT_SCHEMA_VERSION,
            changes=changes,
            legacy_journal_paths=_legacy_journals(root_path),
            digest_backfill=digest_result,
        )

    transaction: Optional[FileTransaction] = None
    try:
        with KBWriteLock(root_path, owner="kvault-migrate"):
            try:
                # Re-read durable state after acquiring the writer lock. A
                # concurrent migrator may have completed since the preview at
                # function entry.
                before = current_schema(root_path)
                changes = []
                if not policy_file.exists():
                    changes.append("create .kvault/policy.yaml")
                if before is None:
                    changes.append("create .kvault/schema.json")
                preview = _coerce_digest_result(selected_hook(root_path, True))
                if preview.errors:
                    raise MigrationError("; ".join(preview.errors))
                snapshot_paths = list(preview.planned_paths)
                snapshot_paths.extend([".kvault/policy.yaml", ".kvault/schema.json"])
                transaction = FileTransaction(
                    root_path,
                    f"migration-v1-{uuid.uuid4().hex[:12]}",
                )
                transaction.begin(snapshot_paths)
                transaction.mark_applying()

                digest_result = _coerce_digest_result(selected_hook(root_path, False))
                if digest_result.planned_paths:
                    changes.append(
                        "backfill child digests for "
                        f"{len(digest_result.planned_paths)} summaries"
                    )
                if digest_result.errors:
                    raise MigrationError("; ".join(digest_result.errors))

                from kvault.core.validation import audit_kb

                audit = audit_kb(root_path)
                if not audit["valid"]:
                    findings = [
                        f"{item['path']}: {item['message']}"
                        for item in audit["issues"]
                        if item["severity"] in {"error", "warning"}
                    ]
                    raise MigrationError("integrity validation failed: " + "; ".join(findings))
                # Validate an existing policy before using schema.json as the durable
                # marker that migration completed.
                if policy_file.exists():
                    load_policy(root_path)
                else:
                    ensure_policy(root_path)
                if before is None:
                    state = SchemaState(installed_at=_utc_now(), migrated_from=None)
                    atomic_write_json(schema_path(root_path), state.model_dump(mode="json"))
                else:
                    state = before
                transaction.commit()
            except Exception as exc:
                if transaction is not None and transaction.state.get("status") in {
                    "prepared",
                    "applying",
                }:
                    try:
                        # Keep the single-writer lock through restoration so no
                        # other process can observe a partially migrated tree.
                        transaction.rollback(str(exc))
                    except (OSError, TransactionError) as rollback_exc:
                        exc = MigrationError(f"{exc}; rollback failed: {rollback_exc}")
                raise exc
    except Exception as exc:
        return MigrationResult(
            success=False,
            dry_run=False,
            migration_required=current_schema(root_path) is None,
            schema_before=before.schema_version if before else None,
            schema_after=current_schema_version(root_path),
            changes=changes,
            legacy_journal_paths=_legacy_journals(root_path),
            digest_backfill=digest_result,
            errors=[str(exc)],
        )

    return MigrationResult(
        success=True,
        dry_run=False,
        migration_required=False,
        schema_before=before.schema_version if before else None,
        schema_after=state.schema_version,
        changes=changes,
        legacy_journal_paths=_legacy_journals(root_path),
        digest_backfill=digest_result,
    )


def migrate_kb(
    root: Union[str, Path],
    *,
    dry_run: bool = True,
    digest_backfill_hook: Optional[DigestBackfillHook] = None,
) -> MigrationResult:
    """Compatibility alias for :func:`migrate`; new integrations should use migrate."""
    return migrate(
        root,
        dry_run=dry_run,
        digest_backfill_hook=digest_backfill_hook,
    )


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MigrationError(f"Could not read Moss capture file: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (ValueError, TypeError) as exc:
            raise MigrationError(f"Invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise MigrationError(f"Moss JSONL record must be an object at {path}:{line_number}")
        records.append(value)
    return records


def _first(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _record_body(record: Mapping[str, Any]) -> str:
    value = _first(record, ("content", "text", "body", "memory", "fact", "candidate"))
    if not isinstance(value, str):
        raise ValueError("record has no string content/text/body/memory/fact/candidate field")
    return value


def _record_id(record: Mapping[str, Any]) -> str:
    value = _first(record, ("id", "event_id", "capture_id", "uuid"))
    if value is None or not str(value).strip():
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        return "hash_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return str(value).strip()


def _moss_event_id(source: str, record_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", record_id).strip("._-") or "record"
    stable_identity = f"{source}\0{record_id}"
    digest = hashlib.sha256(stable_identity.encode("utf-8")).hexdigest()[:10]
    return f"moss_{slug[:150]}_{digest}"


def _parse_optional_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, (int, float)):
        # Moss/OpenClaw timestamps may be Unix seconds or milliseconds.
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    raw = str(value).strip().replace("Z", "+00:00")
    return _as_utc(datetime.fromisoformat(raw))


def _record_tags(record: Mapping[str, Any]) -> List[str]:
    value = record.get("tags", [])
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _record_sensitivity(record: Mapping[str, Any]) -> Sensitivity:
    try:
        return Sensitivity(str(record.get("sensitivity", "personal")))
    except ValueError:
        return Sensitivity.PERSONAL


def _import_one_moss_record(
    root: Path,
    record: Mapping[str, Any],
    *,
    origin: Literal["inbox", "processed"],
    dry_run: bool,
) -> MossImportItem:
    status: Literal[
        "created_pending",
        "created_legacy_archived_unknown",
        "existing",
        "dry_run",
    ]
    source_id = _record_id(record)
    source = str(record.get("source") or "moss-openclaw")
    occurred_at = _parse_optional_datetime(
        _first(record, ("occurred_at", "timestamp", "created_at", "captured_at", "ts"))
    )
    captured_at = _parse_optional_datetime(
        _first(record, ("captured_at", "created_at", "timestamp", "ts"))
    )
    capture = capture_event(
        root,
        _record_body(record),
        source=source,
        source_ref=source_id,
        occurred_at=occurred_at,
        captured_at=captured_at,
        tags=_record_tags(record),
        sensitivity=_record_sensitivity(record),
        event_id=_moss_event_id(source, source_id),
        dry_run=dry_run,
    )
    if not capture.success:
        return MossImportItem(
            origin=origin,
            source_record_id=source_id,
            event_id=capture.event_id,
            status="source_ref_conflict",
            error=capture.error,
        )
    assert capture.event is not None

    if origin == "processed" and not dry_run:
        event_id = capture.event.event_id
        reconciliation_id = (
            "rec_moss_import_" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:20]
        )
        created_at = capture.event.captured_at.isoformat().replace("+00:00", "Z")
        plan = {
            "schema_version": 1,
            "created_at": created_at,
            "event_ids": [event_id],
            "review_required": False,
            "decisions": [
                {
                    "event_id": event_id,
                    "outcome": ReconciliationOutcome.LEGACY_ARCHIVED_UNKNOWN.value,
                    "reasoning": (
                        "Imported from Moss processed capture history; prior semantic promotion "
                        "cannot be proven."
                    ),
                }
            ],
            "mutations": [],
            "reasoning": "Legacy Moss capture import",
            "requested_by": "moss-capture-import",
        }
        result = {
            "success": True,
            "created_at": created_at,
            "status": ReconciliationOutcome.LEGACY_ARCHIVED_UNKNOWN.value,
            "event_outcomes": {event_id: ReconciliationOutcome.LEGACY_ARCHIVED_UNKNOWN.value},
            "changed_paths": [],
        }
        write_reconciliation_plan(root, reconciliation_id, plan)
        write_reconciliation_result(root, reconciliation_id, result)
        status = "created_legacy_archived_unknown" if capture.created else "existing"
    elif dry_run:
        status = "dry_run"
    elif capture.created:
        status = "created_pending"
    else:
        status = "existing"

    return MossImportItem(
        origin=origin,
        source_record_id=source_id,
        event_id=capture.event.event_id,
        status=status,
    )


def import_moss_capture(
    root: Union[str, Path],
    inbox_path: Union[str, Path],
    processed_path: Optional[Union[str, Path]] = None,
    *,
    dry_run: bool = True,
) -> MossImportResult:
    """Import legacy Moss JSONL without mutating either input file.

    Inbox records remain pending.  Processed records receive the deliberately
    conservative terminal outcome ``legacy_archived_unknown`` rather than an
    unsupported claim that they were promoted into the semantic tree.
    """
    root_path = _root(root)
    sources: List[tuple[Literal["inbox", "processed"], Dict[str, Any]]] = [
        ("inbox", item) for item in _load_jsonl(Path(inbox_path).expanduser())
    ]
    if processed_path is not None:
        sources.extend(
            ("processed", item) for item in _load_jsonl(Path(processed_path).expanduser())
        )

    items: List[MossImportItem] = []
    errors: List[str] = []
    for origin, record in sources:
        try:
            item = _import_one_moss_record(root_path, record, origin=origin, dry_run=dry_run)
        except (EventError, MigrationError, OSError, TypeError, ValueError) as exc:
            source_id = _record_id(record)
            item = MossImportItem(
                origin=origin,
                source_record_id=source_id,
                status="error",
                error=str(exc),
            )
            errors.append(f"{origin}:{source_id}: {exc}")
        items.append(item)

    created = sum(item.status.startswith("created_") for item in items)
    existing = sum(item.status == "existing" for item in items)
    conflicts = sum(item.status == "source_ref_conflict" for item in items)
    usable_items = [
        item
        for item in items
        if item.event_id is not None and item.status not in {"error", "source_ref_conflict"}
    ]
    processed_event_ids = {item.event_id for item in usable_items if item.origin == "processed"}
    inbox_event_ids = {item.event_id for item in usable_items if item.origin == "inbox"}
    archived = len(processed_event_ids)
    if dry_run:
        # A record present in both legacy queues is already terminal in the
        # processed queue and must not also be reported as pending.
        pending = len(inbox_event_ids - processed_event_ids)
    else:
        states = derive_event_states(root_path)
        pending = sum(
            states[event_id].state == EventStatus.PENDING
            for event_id in inbox_event_ids
            if event_id in states
        )
    return MossImportResult(
        success=not errors and conflicts == 0,
        dry_run=dry_run,
        total=len(items),
        created=created,
        existing=existing,
        conflicts=conflicts,
        pending=pending,
        legacy_archived_unknown=archived,
        items=items,
        errors=errors,
    )


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DigestBackfillHook",
    "DigestBackfillResult",
    "MigrationError",
    "MigrationRequiredError",
    "MigrationResult",
    "MossImportItem",
    "MossImportResult",
    "SchemaState",
    "UnsupportedSchemaError",
    "backfill_children_digests",
    "current_schema",
    "current_schema_version",
    "import_moss_capture",
    "migrate",
    "migrate_kb",
    "require_schema",
    "schema_path",
]
