"""Journal-first event, reconciliation, migration, and skill commands."""

from __future__ import annotations

import shutil
import sysconfig
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import click

from kvault.cli._helpers import (
    apply_common_options,
    common_options,
    output_json,
    read_stdin,
    read_stdin_json,
    resolve_kb_root,
)
from kvault.core.reconciliation import (
    ReconciliationError,
    ReconciliationPlan,
    apply_reconciliation,
    approve_reconciliation,
    prepare_reconciliation,
    reconciliation_status,
    recover_reconciliations,
)


def _dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _emit(ctx: click.Context, payload: Dict[str, Any], *, failure: bool = False) -> None:
    if ctx.obj.get("as_json"):
        output_json(payload)
    else:
        if failure:
            click.echo(payload.get("error", "Operation failed"), err=True)
        else:
            click.echo(payload.get("message") or payload)
    if failure:
        ctx.exit(1)


def _error_payload(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, ReconciliationError):
        return {
            "success": False,
            "error_code": exc.code,
            "error": str(exc),
            "details": exc.details,
        }
    return {"success": False, "error_code": "workflow_error", "error": str(exc)}


@click.command("capture")
@click.option("--source", required=True, help="Source system or actor")
@click.option("--source-ref", default=None, help="Stable source record identifier")
@click.option("--occurred-at", default=None, help="Source occurrence time (RFC 3339)")
@click.option("--tag", "tags", multiple=True, help="Repeatable event tag")
@click.option(
    "--sensitivity",
    type=click.Choice(["public", "personal", "sensitive", "restricted"]),
    default="personal",
    show_default=True,
)
@common_options
@click.pass_context
def capture_event_command(
    ctx: click.Context,
    source: str,
    source_ref: Optional[str],
    occurred_at: Optional[str],
    tags: Sequence[str],
    sensitivity: str,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Capture an immutable memory candidate from stdin before semantic routing."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)
    content = read_stdin()
    try:
        from kvault.core.events import capture_event

        result = capture_event(
            root,
            content,
            source=source,
            source_ref=source_ref,
            occurred_at=occurred_at,
            tags=list(tags),
            sensitivity=sensitivity,
        )
        payload = _dict(result)
        payload.setdefault("success", True)
        _emit(ctx, payload)
    except Exception as exc:
        _emit(ctx, _error_payload(exc), failure=True)


@click.group("events")
def events_group() -> None:
    """Inspect and import immutable temporal events."""


@events_group.command("list")
@click.option(
    "--status",
    type=click.Choice(["pending", "reconciling", "needs_review", "resolved"]),
    default=None,
)
@common_options
@click.pass_context
def list_events_command(
    ctx: click.Context, status: Optional[str], kb_root: Optional[Path], as_json: bool
) -> None:
    """List events, optionally filtered by derived lifecycle state."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)
    try:
        from kvault.core.events import list_events

        records = list_events(root, status=status)
        payload = {
            "success": True,
            "count": len(records),
            "events": [_dict(record) for record in records],
        }
        _emit(ctx, payload)
    except Exception as exc:
        _emit(ctx, _error_payload(exc), failure=True)


@events_group.command("show")
@click.argument("event_id")
@common_options
@click.pass_context
def show_event_command(
    ctx: click.Context, event_id: str, kb_root: Optional[Path], as_json: bool
) -> None:
    """Show one captured event and its derived lifecycle state."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)
    try:
        from kvault.core.events import derive_event_states, get_event

        record = get_event(root, event_id)
        if record is None:
            raise ReconciliationError("event_not_found", f"Event not found: {event_id}")
        states = derive_event_states(root)
        payload = _dict(record)
        payload["state"] = (
            _dict(states[event_id]) if not isinstance(states[event_id], str) else states[event_id]
        )
        _emit(ctx, {"success": True, "event": payload})
    except Exception as exc:
        _emit(ctx, _error_payload(exc), failure=True)


@events_group.command("import")
@click.option("--format", "input_format", type=click.Choice(["moss-capture"]), required=True)
@click.option("--input", "inbox_path", type=click.Path(path_type=Path), required=True)
@click.option("--processed", "processed_path", type=click.Path(path_type=Path), default=None)
@click.option("--dry-run", is_flag=True)
@common_options
@click.pass_context
def import_events_command(
    ctx: click.Context,
    input_format: str,
    inbox_path: Path,
    processed_path: Optional[Path],
    dry_run: bool,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Import legacy capture files without inventing semantic outcomes."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)
    try:
        from kvault.core.migration import import_moss_capture

        result = import_moss_capture(
            root,
            inbox_path=inbox_path,
            processed_path=processed_path,
            dry_run=dry_run,
        )
        _emit(ctx, _dict(result))
    except Exception as exc:
        _emit(ctx, _error_payload(exc), failure=True)


@click.group("reconcile")
def reconcile_group() -> None:
    """Prepare, apply, approve, inspect, and recover semantic reconciliations."""


@reconcile_group.command("prepare")
@click.argument("event_ids", nargs=-1, required=True)
@click.option("--path", "paths", multiple=True, help="Include a target node revision")
@common_options
@click.pass_context
def prepare_reconciliation_command(
    ctx: click.Context,
    event_ids: Sequence[str],
    paths: Sequence[str],
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Load captured evidence, policy, bounded orientation, and requested revisions."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)
    try:
        _emit(ctx, prepare_reconciliation(root, event_ids, paths=paths))
    except Exception as exc:
        _emit(ctx, _error_payload(exc), failure=True)


@reconcile_group.command("apply")
@common_options
@click.pass_context
def apply_reconciliation_command(
    ctx: click.Context, kb_root: Optional[Path], as_json: bool
) -> None:
    """Validate and apply a complete reconciliation plan from stdin JSON."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)
    try:
        plan = ReconciliationPlan.model_validate(read_stdin_json())
        result = apply_reconciliation(root, plan)
        payload = result.model_dump(mode="json")
        _emit(ctx, payload, failure=not result.success)
    except Exception as exc:
        _emit(ctx, _error_payload(exc), failure=True)


@reconcile_group.command("approve")
@click.argument("reconciliation_id")
@click.option("--actor", required=True, help="Human approval identity")
@common_options
@click.pass_context
def approve_reconciliation_command(
    ctx: click.Context,
    reconciliation_id: str,
    actor: str,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Apply an unchanged review-gated plan after explicit approval."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)
    try:
        result = approve_reconciliation(root, reconciliation_id, actor)
        _emit(ctx, result.model_dump(mode="json"), failure=not result.success)
    except Exception as exc:
        _emit(ctx, _error_payload(exc), failure=True)


@reconcile_group.command("status")
@click.argument("reconciliation_id")
@common_options
@click.pass_context
def reconciliation_status_command(
    ctx: click.Context,
    reconciliation_id: str,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Show immutable plan/result and operational transaction state."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)
    try:
        _emit(ctx, reconciliation_status(root, reconciliation_id))
    except Exception as exc:
        _emit(ctx, _error_payload(exc), failure=True)


@reconcile_group.command("recover")
@common_options
@click.pass_context
def recover_reconciliation_command(
    ctx: click.Context, kb_root: Optional[Path], as_json: bool
) -> None:
    """Recover interrupted transactions while refusing to clear a live lock."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)
    try:
        _emit(ctx, recover_reconciliations(root))
    except Exception as exc:
        _emit(ctx, _error_payload(exc), failure=True)


@click.command("migrate")
@click.option("--dry-run", is_flag=True)
@common_options
@click.pass_context
def migrate_command(
    ctx: click.Context, dry_run: bool, kb_root: Optional[Path], as_json: bool
) -> None:
    """Explicitly migrate an existing KB to the 0.12 schema."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)
    try:
        from kvault.core.migration import migrate

        result = migrate(root, dry_run=dry_run)
        _emit(ctx, _dict(result), failure=not result.success)
    except Exception as exc:
        _emit(ctx, _error_payload(exc), failure=True)


def bundled_skill_path() -> Path:
    """Locate the canonical bundled skill in a checkout or installed wheel."""
    checkout = Path(__file__).resolve().parents[2] / "skills" / "kvault"
    if (checkout / "SKILL.md").is_file():
        return checkout
    try:
        distribution = metadata.distribution("knowledgevault")
        for item in distribution.files or []:
            normalized = str(item).replace("\\", "/")
            if normalized.endswith("skills/kvault/SKILL.md"):
                located = Path(distribution.locate_file(item)).resolve().parent
                if (located / "SKILL.md").is_file():
                    return located
    except metadata.PackageNotFoundError:
        pass
    shared = Path(sysconfig.get_path("data")) / "share" / "knowledgevault" / "skills" / "kvault"
    if (shared / "SKILL.md").is_file():
        return shared
    raise FileNotFoundError("Bundled kvault skill was not found")


@click.group("skill")
def skill_group() -> None:
    """Locate or install the bundled agent skill."""


@skill_group.command("path")
@click.option("--json", "as_json", is_flag=True)
def skill_path_command(as_json: bool) -> None:
    """Print the bundled skill directory."""
    path = bundled_skill_path()
    if as_json:
        output_json({"success": True, "path": str(path)})
    else:
        click.echo(path)


@skill_group.command("install")
@click.argument("destination", type=click.Path(path_type=Path))
@click.option("--force", is_flag=True, help="Replace an existing destination")
@click.option("--json", "as_json", is_flag=True)
def skill_install_command(destination: Path, force: bool, as_json: bool) -> None:
    """Install the bundled skill at an agent runtime's discovery path."""
    source = bundled_skill_path()
    destination = destination.expanduser().absolute()
    if destination.is_symlink():
        raise click.ClickException(f"Refusing to replace a symlink destination: {destination}")
    destination = destination.resolve(strict=False)
    if destination == source:
        raise click.ClickException("Refusing to replace the bundled source skill")
    if destination.exists():
        if not force:
            raise click.ClickException(f"Destination already exists: {destination}")
        if destination == Path.home() or destination == destination.parent:
            raise click.ClickException("Refusing to replace an unsafe destination")
        if not destination.is_dir():
            raise click.ClickException(f"Destination is not a directory: {destination}")
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    payload = {"success": True, "source": str(source), "destination": str(destination)}
    if as_json:
        output_json(payload)
    else:
        click.echo(f"Installed kvault skill at {destination}")


def legacy_mutation_stub(name: str) -> click.Command:
    """Create a fail-closed compatibility command for removed direct mutations."""

    @click.command(
        name,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    )
    @click.argument("legacy_args", nargs=-1, type=click.UNPROCESSED)
    @click.option("--json", "as_json", is_flag=True)
    @click.pass_context
    def command(ctx: click.Context, legacy_args: Sequence[str], as_json: bool) -> None:
        payload = {
            "success": False,
            "error_code": "workflow_required",
            "error": f"Direct '{name}' mutation was removed in kvault 0.12",
            "hint": "Capture the memory candidate, then use reconcile prepare/apply.",
        }
        if as_json or ctx.obj.get("as_json"):
            output_json(payload)
        else:
            click.echo(payload["error"], err=True)
            click.echo(payload["hint"], err=True)
        ctx.exit(1)

    return command
