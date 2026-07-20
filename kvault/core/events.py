"""Optional capture journal: durable memory candidates as pending events.

An event is an immutable-ish markdown record under ``.kvault/events/YYYY-MM/``
holding the verbatim candidate text plus source metadata.  Capture is cheap
and happens at admission time; semantic promotion happens later (often in a
different agent/process) via ``kvault write --event``, which resolves the
event and stamps provenance on the node.  Events that never get resolved
surface in ``kvault check`` — that is the whole point: "archived" must never
be mistaken for "incorporated".

This is deliberately small: no reconciliation plans, no revision pinning, no
policy engine.  Files are scanned directly (a personal KB captures a few
events a day; directories stay tiny).
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from kvault.core.frontmatter import (
    FrontmatterError,
    build_frontmatter,
    parse_frontmatter_strict,
)
from kvault.core.locks import KBWriteLock, atomic_write_text
from kvault.core.validation import ErrorCode, error_response

EVENTS_DIR = "events"
STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"
OUTCOMES = ("promoted", "journal_only", "duplicate", "no_op", "rejected")


def _events_root(kg_root: Path) -> Path:
    return Path(kg_root) / ".kvault" / EVENTS_DIR


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_id(source: str, source_ref: Optional[str], body: str) -> str:
    digest = hashlib.sha256(f"{source}\n{source_ref or ''}\n{body}".encode()).hexdigest()
    return f"ev{digest[:12]}"


def _event_file(kg_root: Path, event_id: str, captured_at: str) -> Path:
    return _events_root(kg_root) / captured_at[:7] / f"{event_id}.md"


def _iter_event_files(kg_root: Path) -> List[Path]:
    root = _events_root(kg_root)
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.md"))


def _load_event_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        meta, body = parse_frontmatter_strict(path.read_text(encoding="utf-8"))
    except (OSError, FrontmatterError):
        return None
    if not meta.get("id"):
        return None
    meta["body"] = body
    meta["_file"] = path
    return meta


def _find_event(kg_root: Path, event_id: str) -> Optional[Dict[str, Any]]:
    for path in _iter_event_files(kg_root):
        if path.stem == event_id:
            return _load_event_file(path)
    return None


def _write_event(kg_root: Path, event: Dict[str, Any]) -> None:
    body = event.pop("body")
    path = event.pop("_file", None) or _event_file(kg_root, event["id"], event["captured_at"])
    meta = {k: v for k, v in event.items() if v not in (None, [])}
    atomic_write_text(path, build_frontmatter(meta) + body)


def _age_days(captured_at: str) -> Optional[int]:
    try:
        captured = datetime.strptime(captured_at[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    return max(0, (datetime.now() - captured).days)


def _snippet(body: str, limit: int = 100) -> str:
    for line in body.strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:limit]
    return body.strip()[:limit]


def _public(event: Dict[str, Any], include_body: bool = False) -> Dict[str, Any]:
    view = {
        "id": event.get("id"),
        "captured_at": event.get("captured_at"),
        "occurred_at": event.get("occurred_at"),
        "source": event.get("source"),
        "source_ref": event.get("source_ref"),
        "tags": event.get("tags", []),
        "sensitivity": event.get("sensitivity"),
        "status": event.get("status"),
        "resolution": event.get("resolution"),
        "age_days": _age_days(str(event.get("captured_at", ""))),
        "snippet": _snippet(event.get("body", "")),
    }
    if include_body:
        view["body"] = event.get("body", "")
    return {k: v for k, v in view.items() if v is not None}


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------


def capture_event(
    kg_root: Path,
    body: str,
    source: str,
    source_ref: Optional[str] = None,
    occurred_at: Optional[str] = None,
    sensitivity: Optional[str] = None,
    tags: Optional[List[str]] = None,
    captured_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a memory candidate as a pending event, idempotently.

    The same (source, source_ref, body) returns the existing event.  A reused
    source_ref with different content is a conflict, not a new event.
    """
    if not body.strip():
        return error_response(ErrorCode.VALIDATION_ERROR, "Event body must not be empty")
    if not source or not source.strip():
        return error_response(ErrorCode.VALIDATION_ERROR, "Event source is required")

    event_id = _event_id(source, source_ref, body)
    with KBWriteLock(kg_root):
        if source_ref:
            for path in _iter_event_files(kg_root):
                existing = _load_event_file(path)
                if existing is None:
                    continue
                if existing.get("source") == source and existing.get("source_ref") == source_ref:
                    if existing["id"] == event_id:
                        return {
                            "success": True,
                            "event_id": existing["id"],
                            "status": existing.get("status"),
                            "created": False,
                        }
                    return error_response(
                        ErrorCode.VALIDATION_ERROR,
                        f"source_ref '{source_ref}' already captured with different "
                        f"content (event {existing['id']})",
                        details={"existing_event_id": existing["id"]},
                    )
        else:
            existing = _find_event(kg_root, event_id)
            if existing is not None:
                return {
                    "success": True,
                    "event_id": existing["id"],
                    "status": existing.get("status"),
                    "created": False,
                }

        event = {
            "id": event_id,
            "captured_at": captured_at or _now_iso(),
            "occurred_at": occurred_at,
            "source": source.strip(),
            "source_ref": source_ref,
            "tags": list(tags or []),
            "sensitivity": sensitivity,
            "status": STATUS_PENDING,
            "body": body,
        }
        _write_event(kg_root, event)
    return {
        "success": True,
        "event_id": event_id,
        "status": STATUS_PENDING,
        "created": True,
    }


def list_events(kg_root: Path, status: Optional[str] = None) -> Dict[str, Any]:
    """List events, newest first, optionally filtered by status."""
    events = []
    for path in _iter_event_files(kg_root):
        event = _load_event_file(path)
        if event is None:
            continue
        if status and event.get("status") != status:
            continue
        events.append(_public(event))
    events.sort(key=lambda e: str(e.get("captured_at", "")), reverse=True)
    return {"success": True, "count": len(events), "events": events}


def get_event(kg_root: Path, event_id: str) -> Dict[str, Any]:
    """Return one event with its full body."""
    event = _find_event(kg_root, event_id)
    if event is None:
        return error_response(ErrorCode.NOT_FOUND, f"Event not found: {event_id}")
    return {"success": True, "event": _public(event, include_body=True)}


def resolve_event(
    kg_root: Path,
    event_id: str,
    outcome: str,
    note: Optional[str] = None,
    target_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Resolve a pending event with an explicit outcome."""
    if outcome not in OUTCOMES:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            f"Unknown outcome: {outcome}",
            hint=f"Use one of: {', '.join(OUTCOMES)}",
        )
    with KBWriteLock(kg_root):
        event = _find_event(kg_root, event_id)
        if event is None:
            return error_response(ErrorCode.NOT_FOUND, f"Event not found: {event_id}")
        if event.get("status") == STATUS_RESOLVED:
            return error_response(
                ErrorCode.WORKFLOW_ERROR,
                f"Event {event_id} is already resolved",
                details={"resolution": event.get("resolution")},
            )
        event["status"] = STATUS_RESOLVED
        event["resolution"] = {
            k: v
            for k, v in {
                "outcome": outcome,
                "note": note,
                "target_paths": sorted(set(target_paths or [])),
                "resolved_at": _now_iso(),
            }.items()
            if v not in (None, [])
        }
        _write_event(kg_root, event)
    return {"success": True, "event_id": event_id, "resolution": event["resolution"]}


def check_events_promotable(kg_root: Path, event_ids: List[str]) -> Dict[str, Any]:
    """Verify every event exists and is pending or already promoted.

    Called BEFORE a node write so a doomed promotion fails fast without
    mutating the semantic tree (e.g. concurrent double-pickup by two
    subagents, or an event that was resolved as duplicate/rejected).
    """
    for event_id in event_ids:
        event = _find_event(kg_root, event_id)
        if event is None:
            return error_response(ErrorCode.NOT_FOUND, f"Event not found: {event_id}")
        if event.get("status") == STATUS_RESOLVED:
            outcome = (event.get("resolution") or {}).get("outcome")
            if outcome != "promoted":
                return error_response(
                    ErrorCode.WORKFLOW_ERROR,
                    f"Event {event_id} was already resolved as {outcome}",
                    details={"resolution": event.get("resolution")},
                )
    return {"success": True}


def promote_events(kg_root: Path, event_ids: List[str], target_path: str) -> Dict[str, Any]:
    """Mark events promoted to *target_path* after a successful node write.

    A pending event resolves as promoted; a promoted event gains the target
    (idempotent subagent retries, one event feeding several nodes).
    """
    promoted = []
    with KBWriteLock(kg_root):
        precheck = check_events_promotable(kg_root, event_ids)
        if not precheck.get("success"):
            return precheck
        for event_id in event_ids:
            event = _find_event(kg_root, event_id)
            assert event is not None  # checked above, under the same lock
            resolution = event.get("resolution") or {}
            targets = set(resolution.get("target_paths", []))
            targets.add(target_path)
            event["status"] = STATUS_RESOLVED
            event["resolution"] = {
                "outcome": "promoted",
                "target_paths": sorted(targets),
                "resolved_at": resolution.get("resolved_at", _now_iso()),
            }
            _write_event(kg_root, event)
            promoted.append(event_id)
    return {"success": True, "promoted": promoted, "target_path": target_path}


def pending_event_findings(kg_root: Path, max_age_days: int = 7) -> List[Dict[str, Any]]:
    """Events pending longer than *max_age_days*, for ``kvault check``."""
    findings = []
    for event in list_events(kg_root, status=STATUS_PENDING)["events"]:
        age = event.get("age_days")
        if age is not None and age > max_age_days:
            findings.append(
                {
                    "type": "pending_event",
                    "event_id": event["id"],
                    "captured_at": event.get("captured_at"),
                    "age_days": age,
                    "source": event.get("source"),
                    "snippet": event.get("snippet"),
                }
            )
    findings.sort(key=lambda f: f.get("age_days", 0), reverse=True)
    return findings


# ---------------------------------------------------------------------------
# Legacy Moss inbox import
# ---------------------------------------------------------------------------


def import_moss_capture(
    kg_root: Path,
    input_path: Path,
    processed_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Import the OpenClaw kb-inbox JSONL queue, repeat-safely.

    Records look like ``{"id", "ts", "source", "tags", "text", "status"}``
    with ``archived_ts`` on processed entries.  Open records become pending
    events.  Archived records become resolved/journal_only with a
    ``legacy_archived_unknown`` note — the archive flag proved queue
    disposition, not semantic incorporation.  Idempotency key: the record id
    as source_ref.  When a record id appears in both files, the processed
    (archived) copy wins.
    """
    import json as _json

    records: Dict[str, Dict[str, Any]] = {}
    counts = {"open": 0, "archived": 0, "invalid": 0, "duplicate": 0, "conflict": 0}

    def _read_jsonl(path: Path, archived_default: bool) -> None:
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"Cannot read {path}: {exc}") from exc
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = _json.loads(line)
            except ValueError:
                counts["invalid"] += 1
                continue
            record_id = str(record.get("id", "")).strip()
            text = str(record.get("text", "")).strip()
            if not record_id or not text:
                counts["invalid"] += 1
                continue
            archived = archived_default or record.get("status") == "archived"
            existing = records.get(record_id)
            if existing is None or (archived and not existing["archived"]):
                records[record_id] = {"record": record, "archived": archived}

    _read_jsonl(input_path, archived_default=False)
    if processed_path is not None:
        _read_jsonl(processed_path, archived_default=True)

    imported = []
    for record_id, item in sorted(records.items()):
        record, archived = item["record"], item["archived"]
        counts["archived" if archived else "open"] += 1
        if dry_run:
            continue
        result = capture_event(
            kg_root,
            body=str(record.get("text", "")),
            source=str(record.get("source") or "moss-inbox"),
            source_ref=f"moss-inbox:{record_id}",
            occurred_at=str(record["ts"]) if record.get("ts") else None,
            tags=[str(t) for t in record.get("tags") or []],
        )
        if not result.get("success"):
            counts["conflict"] += 1
            continue
        if not result.get("created"):
            counts["duplicate"] += 1
            continue
        if archived:
            resolve_event(
                kg_root,
                result["event_id"],
                outcome="journal_only",
                note="legacy_archived_unknown: archived in the old queue; "
                "semantic incorporation was never verified",
            )
        imported.append({"record_id": record_id, "event_id": result["event_id"]})

    return {
        "success": True,
        "dry_run": dry_run,
        "counts": counts,
        "imported": imported,
    }


__all__ = [
    "OUTCOMES",
    "capture_event",
    "check_events_promotable",
    "get_event",
    "import_moss_capture",
    "list_events",
    "pending_event_findings",
    "promote_events",
    "resolve_event",
]
