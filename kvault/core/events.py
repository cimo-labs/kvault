"""Immutable temporal events and append-only reconciliation records.

The temporal journal is the evidence layer of a knowledge vault.  Capturing an
event never mutates the semantic tree, and reconciliation records never rewrite
the event that caused them.  Records are Markdown files with YAML frontmatter
and an exact, hash-verified body.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Mapping, Optional, Sequence, Tuple, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kvault.core.paths import PathSafetyError, resolve_within_root

EVENT_SCHEMA_VERSION = 1
RECONCILIATION_SCHEMA_VERSION = 1
_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_OUTCOMES = {
    "applied",
    "journal_only",
    "duplicate",
    "no_op",
    "legacy_archived_unknown",
}


class EventError(RuntimeError):
    """Base exception for temporal record failures."""


class EventFormatError(EventError):
    """Raised when a stored event or reconciliation record is malformed."""


class ImmutableRecordConflict(EventError):
    """Raised when code attempts to replace an existing immutable record."""


class Sensitivity(str, Enum):
    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"

    def __str__(self) -> str:
        return self.value


class EventStatus(str, Enum):
    PENDING = "pending"
    RECONCILING = "reconciling"
    NEEDS_REVIEW = "needs_review"
    RESOLVED = "resolved"

    def __str__(self) -> str:
        return self.value


class ReconciliationOutcome(str, Enum):
    APPLIED = "applied"
    JOURNAL_ONLY = "journal_only"
    DUPLICATE = "duplicate"
    NO_OP = "no_op"
    LEGACY_ARCHIVED_UNKNOWN = "legacy_archived_unknown"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class EventMetadata(BaseModel):
    """Versioned immutable metadata stored in an event's frontmatter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    event_id: str
    captured_at: datetime
    occurred_at: Optional[datetime] = None
    source: str = Field(min_length=1)
    source_ref: Optional[str] = None
    content_sha256: str
    tags: List[str] = Field(default_factory=list)
    sensitivity: Sensitivity = Sensitivity.PERSONAL

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        return _validate_record_id(value, "event_id")

    @field_validator("captured_at", "occurred_at")
    @classmethod
    def normalize_datetimes(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _as_utc(value) if value is not None else None

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source cannot be blank")
        return value

    @field_validator("source_ref")
    @classmethod
    def normalize_source_ref(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("content_sha256")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("content_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: List[str]) -> List[str]:
        result: List[str] = []
        seen = set()
        for raw in values:
            value = str(raw).strip()
            if value and value not in seen:
                result.append(value)
                seen.add(value)
        return result


class EventRecord(EventMetadata):
    """A loaded event, including its exact candidate text and storage path."""

    body: str
    path: Path

    @model_validator(mode="after")
    def verify_body_hash(self) -> "EventRecord":
        if _content_hash(self.body) != self.content_sha256:
            raise ValueError("event body does not match content_sha256")
        return self


class CaptureEventResult(BaseModel):
    """Structured result for a capture or idempotent replay."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    status: Literal["created", "existing", "source_ref_conflict", "dry_run"]
    event: Optional[EventRecord] = None
    event_id: Optional[str] = None
    created: bool = False
    duplicate: bool = False
    error_code: Optional[str] = None
    error: Optional[str] = None


class ReconciliationPaths(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    directory: Path
    plan_path: Path
    result_path: Path

    @property
    def plan(self) -> Path:
        """Compatibility alias used by workflow adapters."""
        return self.plan_path

    @property
    def result(self) -> Path:
        """Compatibility alias used by workflow adapters."""
        return self.result_path


class ReconciliationPlanMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    record_type: Literal["reconciliation_plan"] = "reconciliation_plan"
    reconciliation_id: str
    created_at: datetime
    event_ids: List[str] = Field(min_length=1)
    review_required: bool = False
    content_sha256: str

    @field_validator("reconciliation_id")
    @classmethod
    def validate_reconciliation_id(cls, value: str) -> str:
        return _validate_record_id(value, "reconciliation_id")

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @field_validator("event_ids")
    @classmethod
    def validate_event_ids(cls, values: List[str]) -> List[str]:
        result: List[str] = []
        seen = set()
        for value in values:
            value = _validate_record_id(value, "event_id")
            if value in seen:
                raise ValueError(f"duplicate event_id: {value}")
            seen.add(value)
            result.append(value)
        return result

    @field_validator("content_sha256")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("content_sha256 must be a lowercase SHA-256 hex digest")
        return value


class ReconciliationPlanRecord(ReconciliationPlanMetadata):
    plan: Dict[str, Any]
    payload: Dict[str, Any]
    path: Path


class ReconciliationResultMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    record_type: Literal["reconciliation_result"] = "reconciliation_result"
    reconciliation_id: str
    created_at: datetime
    event_ids: List[str] = Field(min_length=1)
    event_outcomes: Dict[str, ReconciliationOutcome]
    content_sha256: str

    @field_validator("reconciliation_id")
    @classmethod
    def validate_reconciliation_id(cls, value: str) -> str:
        return _validate_record_id(value, "reconciliation_id")

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @model_validator(mode="after")
    def validate_outcome_coverage(self) -> "ReconciliationResultMetadata":
        if set(self.event_ids) != set(self.event_outcomes):
            raise ValueError("event_outcomes must cover exactly every event_id")
        return self

    @field_validator("content_sha256")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        value = value.lower()
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("content_sha256 must be a lowercase SHA-256 hex digest")
        return value


class ReconciliationResultRecord(ReconciliationResultMetadata):
    result: Dict[str, Any]
    path: Path
    success: Optional[bool] = None
    status: Optional[str] = None


class EventStateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    state: EventStatus
    terminal_outcome: Optional[ReconciliationOutcome] = None
    reconciliation_id: Optional[str] = None
    updated_at: datetime


def _validate_record_id(value: str, field_name: str) -> str:
    value = str(value).strip()
    if not _RECORD_ID_RE.fullmatch(value):
        raise ValueError(
            f"{field_name} must be 1-192 safe characters: letters, numbers, '.', '_', or '-'"
        )
    return value


def _content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


def _render_markdown(metadata: BaseModel, body: str) -> str:
    yaml_text = yaml.safe_dump(
        metadata.model_dump(mode="json"),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    return f"---\n{yaml_text}---\n\n{body}"


def _parse_markdown(path: Path) -> Tuple[Dict[str, Any], str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EventFormatError(f"Could not read temporal record: {path}") from exc
    if not raw.startswith("---\n"):
        raise EventFormatError(f"Temporal record has no YAML frontmatter: {path}")
    boundary = raw.find("\n---\n", 4)
    if boundary < 0:
        raise EventFormatError(f"Temporal record has unclosed YAML frontmatter: {path}")
    yaml_text = raw[4:boundary]
    body_start = boundary + len("\n---\n")
    if raw[body_start : body_start + 1] == "\n":
        body_start += 1
    try:
        metadata = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise EventFormatError(f"Temporal record has invalid YAML: {path}") from exc
    if not isinstance(metadata, dict):
        raise EventFormatError(f"Temporal record frontmatter must be a mapping: {path}")
    return metadata, raw[body_start:]


def _root(root: Union[str, Path]) -> Path:
    resolved = Path(root).expanduser().resolve()
    if not resolved.is_dir():
        raise EventError(f"KB root is not a directory: {resolved}")
    return resolved


def _safe_path(root: Path, relative: Union[str, Path]) -> Path:
    try:
        return resolve_within_root(root, relative, allow_root=False, reject_symlinks=True)
    except PathSafetyError as exc:
        raise EventError(f"Unsafe temporal record path: {relative}") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_create_text(path: Path, content: str) -> bool:
    """Create an immutable file atomically; return False if it already exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            return False
        _fsync_directory(path.parent)
        return True
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _record_lock(root: Path, timeout: float = 10.0) -> Iterator[None]:
    """Serialize record discovery plus creation without nesting the KB write lock."""
    lock = _safe_path(root, ".kvault/locks/temporal-records.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                if age > 300:
                    lock.rmdir()
                    continue
            except (FileNotFoundError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise EventError("Timed out waiting for the temporal record lock")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except FileNotFoundError:
            pass


def _event_path(root: Path, event_id: str, captured_at: datetime) -> Path:
    event_id = _validate_record_id(event_id, "event_id")
    stamp = _as_utc(captured_at)
    return _safe_path(
        root,
        Path("journal") / "events" / f"{stamp:%Y}" / f"{stamp:%m}" / f"{event_id}.md",
    )


def _new_event_id(captured_at: datetime, content_sha256: str) -> str:
    stamp = _as_utc(captured_at).strftime("%Y%m%dT%H%M%S%fZ")
    return f"evt_{stamp}_{content_sha256[:8]}_{secrets.token_hex(3)}"


def _event_files(root: Path) -> Iterator[Path]:
    base = _safe_path(root, "journal/events")
    if not base.exists():
        return
    for candidate in base.rglob("*"):
        if candidate.is_symlink():
            raise EventFormatError(
                f"Temporal event storage contains a symlink: {candidate.relative_to(root)}"
            )
    for path in sorted(base.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")):
        if path.is_file() and not path.is_symlink():
            try:
                yield _safe_path(root, path.relative_to(root))
            except EventError as exc:
                raise EventFormatError(f"Unsafe temporal event path: {path}") from exc


def _find_event_path(root: Path, event_id: str) -> Optional[Path]:
    event_id = _validate_record_id(event_id, "event_id")
    matches = [path for path in _event_files(root) if path.stem == event_id]
    if len(matches) > 1:
        raise EventFormatError(f"Duplicate event_id on disk: {event_id}")
    return matches[0] if matches else None


def _load_event_path(path: Path) -> EventRecord:
    metadata, body = _parse_markdown(path)
    try:
        return EventRecord(**metadata, body=body, path=path)
    except ValueError as exc:
        raise EventFormatError(f"Invalid event record: {path}: {exc}") from exc


def capture_event(
    root: Union[str, Path],
    body: Optional[str] = None,
    *,
    content: Optional[str] = None,
    source: str,
    source_ref: Optional[str] = None,
    occurred_at: Optional[Union[datetime, str]] = None,
    captured_at: Optional[datetime] = None,
    tags: Optional[Sequence[str]] = None,
    sensitivity: Union[Sensitivity, str] = Sensitivity.PERSONAL,
    event_id: Optional[str] = None,
    dry_run: bool = False,
) -> CaptureEventResult:
    """Capture an immutable event or return its idempotent existing record.

    ``source + source_ref`` is a stable identity when ``source_ref`` is supplied.
    Replaying identical text returns the prior event; reusing the reference for
    different text returns ``source_ref_conflict`` without creating a record.
    """
    root_path = _root(root)
    if body is None:
        body = content
    elif content is not None:
        raise TypeError("pass either body or content, not both")
    if not isinstance(body, str):
        raise TypeError("body/content must be a string")
    timestamp = _as_utc(captured_at or _utc_now())
    digest = _content_hash(body)
    candidate_id = event_id or _new_event_id(timestamp, digest)
    metadata = EventMetadata(
        event_id=candidate_id,
        captured_at=timestamp,
        occurred_at=_coerce_datetime(occurred_at) if occurred_at is not None else None,
        source=source,
        source_ref=source_ref,
        content_sha256=digest,
        tags=list(tags or []),
        sensitivity=Sensitivity(sensitivity),
    )

    def discover_existing() -> Optional[CaptureEventResult]:
        if metadata.source_ref is not None:
            for record_path in _event_files(root_path):
                record = _load_event_path(record_path)
                if record.source == metadata.source and record.source_ref == metadata.source_ref:
                    if record.content_sha256 == digest:
                        return CaptureEventResult(
                            success=True,
                            status="existing",
                            event=record,
                            event_id=record.event_id,
                            duplicate=True,
                        )
                    return CaptureEventResult(
                        success=False,
                        status="source_ref_conflict",
                        event=record,
                        event_id=record.event_id,
                        error_code="source_ref_conflict",
                        error=(
                            f"{metadata.source}:{metadata.source_ref} is already captured "
                            "with different content"
                        ),
                    )
        existing_path = _find_event_path(root_path, metadata.event_id)
        if existing_path is not None:
            existing = _load_event_path(existing_path)
            if existing.content_sha256 == digest and existing.source == metadata.source:
                return CaptureEventResult(
                    success=True,
                    status="existing",
                    event=existing,
                    event_id=existing.event_id,
                    duplicate=True,
                )
            raise ImmutableRecordConflict(f"event_id already exists: {metadata.event_id}")
        return None

    if dry_run:
        existing = discover_existing()
        if existing is not None:
            return existing
        preview_path = _event_path(root_path, metadata.event_id, metadata.captured_at)
        preview = EventRecord(**metadata.model_dump(), body=body, path=preview_path)
        return CaptureEventResult(
            success=True,
            status="dry_run",
            event=preview,
            event_id=preview.event_id,
        )

    with _record_lock(root_path):
        existing = discover_existing()
        if existing is not None:
            return existing
        path = _event_path(root_path, metadata.event_id, metadata.captured_at)
        rendered = _render_markdown(metadata, body)
        if not _atomic_create_text(path, rendered):
            raise ImmutableRecordConflict(f"event_id already exists: {metadata.event_id}")
        record = EventRecord(**metadata.model_dump(), body=body, path=path)
        return CaptureEventResult(
            success=True,
            status="created",
            event=record,
            event_id=record.event_id,
            created=True,
        )


def get_event(root: Union[str, Path], event_id: str) -> Optional[EventRecord]:
    root_path = _root(root)
    path = _find_event_path(root_path, event_id)
    return _load_event_path(path) if path is not None else None


def list_events(
    root: Union[str, Path], status: Optional[Union[EventStatus, str]] = None
) -> List[EventRecord]:
    root_path = _root(root)
    records = [_load_event_path(path) for path in _event_files(root_path)]
    records.sort(key=lambda item: (item.captured_at, item.event_id))
    if status is None:
        return records
    wanted = EventStatus(status)
    states = derive_event_states(root_path)
    return [record for record in records if states[record.event_id].state == wanted]


def _reconciliation_directories(root: Path) -> Iterator[Path]:
    base = _safe_path(root, "journal/reconciliations")
    if not base.exists():
        return
    for candidate in base.rglob("*"):
        if candidate.is_symlink():
            raise EventFormatError(
                "Reconciliation storage contains a symlink: " f"{candidate.relative_to(root)}"
            )
    for path in sorted(base.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*")):
        if path.is_dir() and not path.is_symlink():
            try:
                yield _safe_path(root, path.relative_to(root))
            except EventError as exc:
                raise EventFormatError(f"Unsafe reconciliation path: {path}") from exc


def _find_reconciliation_directory(root: Path, reconciliation_id: str) -> Optional[Path]:
    reconciliation_id = _validate_record_id(reconciliation_id, "reconciliation_id")
    matches = [path for path in _reconciliation_directories(root) if path.name == reconciliation_id]
    if len(matches) > 1:
        raise EventFormatError(f"Duplicate reconciliation_id on disk: {reconciliation_id}")
    return matches[0] if matches else None


def reconciliation_paths(
    root: Union[str, Path],
    reconciliation_id: str,
    *,
    created_at: Optional[datetime] = None,
) -> ReconciliationPaths:
    root_path = _root(root)
    reconciliation_id = _validate_record_id(reconciliation_id, "reconciliation_id")
    existing = _find_reconciliation_directory(root_path, reconciliation_id)
    if existing is not None:
        directory = existing
    else:
        timestamp = _as_utc(created_at or _utc_now())
        directory = _safe_path(
            root_path,
            Path("journal")
            / "reconciliations"
            / f"{timestamp:%Y}"
            / f"{timestamp:%m}"
            / reconciliation_id,
        )
    return ReconciliationPaths(
        directory=directory,
        plan_path=directory / "plan.md",
        result_path=directory / "result.md",
    )


def _payload_dict(value: Union[BaseModel, Mapping[str, Any]]) -> Dict[str, Any]:
    payload = _jsonable(value)
    if not isinstance(payload, dict):
        raise TypeError("record payload must be a mapping or Pydantic model")
    return payload


def _payload_body(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _coerce_datetime(value: Any, default: Optional[datetime] = None) -> datetime:
    if value is None:
        return _as_utc(default or _utc_now())
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        raw = value.strip().replace("Z", "+00:00")
        try:
            return _as_utc(datetime.fromisoformat(raw))
        except ValueError as exc:
            raise ValueError(f"invalid ISO-8601 datetime: {value}") from exc
    raise TypeError("datetime must be an ISO-8601 string or datetime")


def _plan_metadata(
    reconciliation_id: str,
    payload: Dict[str, Any],
    body: str,
) -> ReconciliationPlanMetadata:
    nested_plan = payload.get("plan")
    event_ids = payload.get("event_ids")
    if event_ids is None and isinstance(nested_plan, dict):
        event_ids = nested_plan.get("event_ids")
    if not isinstance(event_ids, list) or not event_ids:
        raise ValueError("reconciliation plan requires a non-empty event_ids list")
    return ReconciliationPlanMetadata(
        reconciliation_id=reconciliation_id,
        created_at=_coerce_datetime(payload.get("created_at")),
        event_ids=event_ids,
        review_required=bool(payload.get("review_required", payload.get("requires_review", False))),
        content_sha256=_content_hash(body),
    )


def _load_plan_path(path: Path) -> ReconciliationPlanRecord:
    metadata, body = _parse_markdown(path)
    if metadata.get("content_sha256") != _content_hash(body):
        raise EventFormatError(f"Reconciliation plan body hash mismatch: {path}")
    try:
        payload = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise EventFormatError(f"Invalid reconciliation plan JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise EventFormatError(f"Reconciliation plan body must be a JSON object: {path}")
    actual_plan = payload.get("plan", payload)
    if not isinstance(actual_plan, dict):
        raise EventFormatError(f"Reconciliation plan payload must be a JSON object: {path}")
    try:
        return ReconciliationPlanRecord(**metadata, plan=actual_plan, payload=payload, path=path)
    except ValueError as exc:
        raise EventFormatError(f"Invalid reconciliation plan: {path}: {exc}") from exc


def write_reconciliation_plan(
    root: Union[str, Path],
    reconciliation_id: str,
    plan_dict: Union[BaseModel, Mapping[str, Any]],
) -> ReconciliationPlanRecord:
    """Create an immutable reconciliation plan, idempotently for equal payloads."""
    root_path = _root(root)
    payload = _payload_dict(plan_dict)
    body = _payload_body(payload)
    metadata = _plan_metadata(reconciliation_id, payload, body)
    for event_id in metadata.event_ids:
        if get_event(root_path, event_id) is None:
            raise EventError(f"Cannot plan reconciliation for missing event: {event_id}")
    with _record_lock(root_path):
        paths = reconciliation_paths(root_path, reconciliation_id, created_at=metadata.created_at)
        if paths.plan_path.exists():
            existing = _load_plan_path(paths.plan_path)
            if existing.payload == payload:
                return existing
            raise ImmutableRecordConflict(
                f"reconciliation plan already exists: {reconciliation_id}"
            )
        if paths.result_path.exists():
            raise EventFormatError(
                f"reconciliation result exists without a plan: {reconciliation_id}"
            )
        rendered = _render_markdown(metadata, body)
        if not _atomic_create_text(paths.plan_path, rendered):
            raise ImmutableRecordConflict(
                f"reconciliation plan already exists: {reconciliation_id}"
            )
        return ReconciliationPlanRecord(
            **metadata.model_dump(),
            plan=payload.get("plan", payload),
            payload=payload,
            path=paths.plan_path,
        )


def read_reconciliation_plan(
    root: Union[str, Path], reconciliation_id: str
) -> Optional[ReconciliationPlanRecord]:
    paths = reconciliation_paths(root, reconciliation_id)
    return _load_plan_path(paths.plan_path) if paths.plan_path.is_file() else None


def _normalize_outcome(value: Any) -> ReconciliationOutcome:
    raw = str(value).strip().lower()
    if raw == "apply":
        raw = "applied"
    if raw in {"recovered", "needs_review"}:
        raw = "failed"
    return ReconciliationOutcome(raw)


def _result_metadata(
    reconciliation_id: str,
    plan: ReconciliationPlanRecord,
    payload: Dict[str, Any],
    body: str,
) -> ReconciliationResultMetadata:
    raw_outcomes = payload.get("event_outcomes")
    outcomes: Dict[str, ReconciliationOutcome]
    if payload.get("success") is False or payload.get("status") in {"failed", "recovered"}:
        outcomes = {event_id: ReconciliationOutcome.FAILED for event_id in plan.event_ids}
    elif isinstance(raw_outcomes, dict) and raw_outcomes:
        outcomes = {str(key): _normalize_outcome(value) for key, value in raw_outcomes.items()}
    else:
        outcome_value = payload.get("outcome") or payload.get("status")
        if outcome_value is None:
            raise ValueError("reconciliation result requires outcome, status, or event_outcomes")
        outcome = _normalize_outcome(outcome_value)
        outcomes = {event_id: outcome for event_id in plan.event_ids}
    return ReconciliationResultMetadata(
        reconciliation_id=reconciliation_id,
        created_at=_coerce_datetime(payload.get("created_at")),
        event_ids=list(plan.event_ids),
        event_outcomes=outcomes,
        content_sha256=_content_hash(body),
    )


def _load_result_path(path: Path) -> ReconciliationResultRecord:
    metadata, body = _parse_markdown(path)
    if metadata.get("content_sha256") != _content_hash(body):
        raise EventFormatError(f"Reconciliation result body hash mismatch: {path}")
    try:
        payload = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise EventFormatError(f"Invalid reconciliation result JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise EventFormatError(f"Reconciliation result body must be a JSON object: {path}")
    try:
        return ReconciliationResultRecord(
            **metadata,
            result=payload,
            path=path,
            success=payload.get("success"),
            status=payload.get("status"),
        )
    except ValueError as exc:
        raise EventFormatError(f"Invalid reconciliation result: {path}: {exc}") from exc


def write_reconciliation_result(
    root: Union[str, Path],
    reconciliation_id: str,
    result_dict: Union[BaseModel, Mapping[str, Any]],
) -> ReconciliationResultRecord:
    """Create the single immutable result for an existing plan."""
    root_path = _root(root)
    payload = _payload_dict(result_dict)
    body = _payload_body(payload)
    with _record_lock(root_path):
        paths = reconciliation_paths(root_path, reconciliation_id)
        if not paths.plan_path.is_file():
            raise EventError(f"Reconciliation plan does not exist: {reconciliation_id}")
        plan = _load_plan_path(paths.plan_path)
        metadata = _result_metadata(reconciliation_id, plan, payload, body)
        if paths.result_path.exists():
            existing = _load_result_path(paths.result_path)
            if existing.result == payload:
                return existing
            raise ImmutableRecordConflict(
                f"reconciliation result already exists: {reconciliation_id}"
            )
        rendered = _render_markdown(metadata, body)
        if not _atomic_create_text(paths.result_path, rendered):
            raise ImmutableRecordConflict(
                f"reconciliation result already exists: {reconciliation_id}"
            )
        return ReconciliationResultRecord(
            **metadata.model_dump(),
            result=payload,
            path=paths.result_path,
            success=payload.get("success"),
            status=payload.get("status"),
        )


def read_reconciliation_result(
    root: Union[str, Path], reconciliation_id: str
) -> Optional[ReconciliationResultRecord]:
    paths = reconciliation_paths(root, reconciliation_id)
    return _load_result_path(paths.result_path) if paths.result_path.is_file() else None


def _all_reconciliations(
    root: Path,
) -> Iterator[Tuple[ReconciliationPlanRecord, Optional[ReconciliationResultRecord]]]:
    for directory in _reconciliation_directories(root):
        plan_path = directory / "plan.md"
        result_path = directory / "result.md"
        if not plan_path.is_file():
            if result_path.exists():
                raise EventFormatError(f"Reconciliation result exists without plan: {directory}")
            continue
        plan = _load_plan_path(plan_path)
        result = _load_result_path(result_path) if result_path.is_file() else None
        if result is not None and result.reconciliation_id != plan.reconciliation_id:
            raise EventFormatError(f"Plan/result reconciliation_id mismatch: {directory}")
        yield plan, result


def derive_event_states(root: Union[str, Path]) -> Dict[str, EventStateRecord]:
    """Derive every event's state solely from immutable journal records."""
    root_path = _root(root)
    events = list_events(root_path)
    states: Dict[str, EventStateRecord] = {
        event.event_id: EventStateRecord(
            event_id=event.event_id,
            state=EventStatus.PENDING,
            updated_at=event.captured_at,
        )
        for event in events
    }
    attempts: Dict[
        str, List[Tuple[datetime, ReconciliationPlanRecord, Optional[ReconciliationResultRecord]]]
    ] = {event.event_id: [] for event in events}
    for plan, result in _all_reconciliations(root_path):
        timestamp = result.created_at if result is not None else plan.created_at
        for event_id in plan.event_ids:
            if event_id not in states:
                raise EventFormatError(
                    f"Reconciliation {plan.reconciliation_id} references missing event {event_id}"
                )
            attempts[event_id].append((timestamp, plan, result))

    for event_id, records in attempts.items():
        if not records:
            continue
        _, plan, result = max(records, key=lambda item: (item[0], item[1].reconciliation_id))
        if result is None:
            state = EventStatus.NEEDS_REVIEW if plan.review_required else EventStatus.RECONCILING
            states[event_id] = EventStateRecord(
                event_id=event_id,
                state=state,
                reconciliation_id=plan.reconciliation_id,
                updated_at=plan.created_at,
            )
            continue
        outcome = result.event_outcomes[event_id]
        if outcome.value in _TERMINAL_OUTCOMES:
            states[event_id] = EventStateRecord(
                event_id=event_id,
                state=EventStatus.RESOLVED,
                terminal_outcome=outcome,
                reconciliation_id=plan.reconciliation_id,
                updated_at=result.created_at,
            )
        else:
            states[event_id] = EventStateRecord(
                event_id=event_id,
                state=EventStatus.PENDING,
                reconciliation_id=plan.reconciliation_id,
                updated_at=result.created_at,
            )
    return states


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "RECONCILIATION_SCHEMA_VERSION",
    "CaptureEventResult",
    "EventError",
    "EventFormatError",
    "EventMetadata",
    "EventRecord",
    "EventStateRecord",
    "EventStatus",
    "ImmutableRecordConflict",
    "ReconciliationOutcome",
    "ReconciliationPaths",
    "ReconciliationPlanMetadata",
    "ReconciliationPlanRecord",
    "ReconciliationResultMetadata",
    "ReconciliationResultRecord",
    "Sensitivity",
    "capture_event",
    "derive_event_states",
    "get_event",
    "list_events",
    "read_reconciliation_plan",
    "read_reconciliation_result",
    "reconciliation_paths",
    "write_reconciliation_plan",
    "write_reconciliation_result",
]
