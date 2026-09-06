"""CLI commands for the capture journal: capture, events list/show/resolve/import."""

import time
from pathlib import Path
from typing import Optional, Tuple

import click

from kvault.cli._helpers import (
    apply_common_options,
    common_options,
    finish_op,
    get_tier,
    output_json,
    read_stdin,
    record_op,
    resolve_kb_root,
)
from kvault.cli.render import render_notes
from kvault.core import events as ev


@click.command("capture")
@click.option("--source", required=True, help="Where the candidate came from (e.g. conversation)")
@click.option("--source-ref", default=None, help="Stable reference within the source, if known")
@click.option("--occurred-at", default=None, help="When the fact occurred (ISO), if known")
@click.option(
    "--sensitivity", default=None, help="Sensitivity classification, per the owning KB's rules"
)
@click.option("--tag", "tags", multiple=True, help="Topic tag (repeatable)")
@click.option(
    "--allow-suspicious",
    is_flag=True,
    help="Capture even if the body carries shell-mangling residue (,208.25 / .00 / /bin/zsh)",
)
@common_options
@click.pass_context
def capture(
    ctx: click.Context,
    source: str,
    source_ref: Optional[str],
    occurred_at: Optional[str],
    sensitivity: Optional[str],
    tags: Tuple[str, ...],
    allow_suspicious: bool,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Capture a memory candidate (body on stdin) as a pending event.

    Capture is cheap and idempotent — do it at admission time, then promote
    the event into the semantic tree later with `kvault write --event <id>`.
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    body = read_stdin()
    started = time.monotonic()
    result = ev.capture_event(
        kb_root,
        body=body,
        source=source,
        source_ref=source_ref,
        occurred_at=occurred_at,
        sensitivity=sensitivity,
        tags=list(tags),
        allow_suspicious=allow_suspicious,
    )
    if result.get("success") and "did" not in result:
        verb = "captured" if result.get("created") else "already captured"
        result["did"] = f"{verb} {result.get('event_id')} ({result.get('status')})"
    record_op(kb_root, "capture", result, started)
    if ctx.obj.get("as_json"):
        output_json(result)
        if not result.get("success"):
            ctx.exit(1)
    elif result.get("success"):
        verb = "Captured" if result.get("created") else "Already captured"
        click.echo(f"{verb}: {result['event_id']} ({result['status']})")
        render_notes(result, get_tier(ctx))
    else:
        raise click.ClickException(result.get("error", "Capture failed"))
    finish_op(ctx, result)


@click.group("events")
def events_group() -> None:
    """Inspect and resolve captured events."""


@events_group.command("list")
@click.option(
    "--status",
    type=click.Choice(["pending", "resolved", "retracted"]),
    default=None,
    help="Filter by lifecycle status (retracted = resolved with outcome retracted)",
)
@common_options
@click.pass_context
def list_events_cmd(
    ctx: click.Context,
    status: Optional[str],
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """List captured events, newest first."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    result = ev.list_events(kb_root, status=status)
    if ctx.obj.get("as_json"):
        output_json(result)
        return
    if not result["events"]:
        click.echo("No events.")
        return
    for event in result["events"]:
        age = f"{event.get('age_days', '?')}d"
        outcome = (event.get("resolution") or {}).get("outcome", "")
        state = f"{event['status']}{f'/{outcome}' if outcome else ''}"
        click.echo(f"  {event['id']}  {state:<22} {age:>4}  {event.get('snippet', '')}")


@events_group.command("show")
@click.argument("event_id")
@common_options
@click.pass_context
def show_event_cmd(
    ctx: click.Context, event_id: str, kb_root: Optional[Path], as_json: bool
) -> None:
    """Show one event with its full body."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    result = ev.get_event(kb_root, event_id)
    if ctx.obj.get("as_json"):
        output_json(result)
        if not result.get("success"):
            ctx.exit(1)
        return
    if not result.get("success"):
        raise click.ClickException(result.get("error", "Event not found"))
    event = result["event"]
    for key in ("id", "status", "captured_at", "occurred_at", "source", "source_ref"):
        if event.get(key):
            click.echo(f"{key}: {event[key]}")
    if event.get("tags"):
        click.echo(f"tags: {', '.join(event['tags'])}")
    if event.get("resolution"):
        click.echo(f"resolution: {event['resolution']}")
    click.echo()
    click.echo(event.get("body", ""))


@events_group.command("resolve")
@click.argument("event_id")
@click.option(
    "--outcome",
    required=True,
    type=click.Choice(list(ev.OUTCOMES)),
    help="Why this event does not need (further) semantic writes",
)
@click.option("--note", default=None, help="Short explanation for the journal")
@common_options
@click.pass_context
def resolve_event_cmd(
    ctx: click.Context,
    event_id: str,
    outcome: str,
    note: Optional[str],
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Resolve a pending event without a node write.

    Promotion into the tree should use `kvault write --event <id>` instead —
    it stamps provenance and resolves the event in one step.
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    started = time.monotonic()
    result = ev.resolve_event(kb_root, event_id, outcome=outcome, note=note)
    if result.get("success") and "did" not in result:
        result["did"] = f"resolved {event_id}: {outcome}"
    record_op(kb_root, "events-resolve", result, started)
    if ctx.obj.get("as_json"):
        output_json(result)
        if not result.get("success"):
            ctx.exit(1)
    elif result.get("success"):
        click.echo(f"Resolved {event_id}: {outcome}")
        render_notes(result, get_tier(ctx))
    else:
        raise click.ClickException(result.get("error", "Resolve failed"))
    finish_op(ctx, result)


@events_group.command("retract")
@click.argument("event_id")
@click.option("--reason", required=True, help="Why the captured text is wrong evidence")
@click.option(
    "--superseded-by",
    default=None,
    help="Event id carrying the corrected text (clears the RETRACTED: finding once promoted)",
)
@common_options
@click.pass_context
def retract_event_cmd(
    ctx: click.Context,
    event_id: str,
    reason: str,
    superseded_by: Optional[str],
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Retract an event whose captured text is wrong (works on resolved events too).

    Nodes that still cite the event in `source_refs` show up in `kvault check`
    as RETRACTED: findings until they are rewritten and re-linked with
    `kvault write <node> --event <superseding-id>`.
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    started = time.monotonic()
    result = ev.retract_event(kb_root, event_id, reason=reason, superseded_by=superseded_by)
    if result.get("success") and "did" not in result:
        result["did"] = f"retracted {event_id}"
    record_op(kb_root, "events-retract", result, started)
    if ctx.obj.get("as_json"):
        output_json(result)
        if not result.get("success"):
            ctx.exit(1)
    elif result.get("success"):
        click.echo(f"Retracted {event_id}: {reason}")
        render_notes(result, get_tier(ctx))
    else:
        raise click.ClickException(result.get("error", "Retract failed"))
    finish_op(ctx, result)


@events_group.command("import")
@click.option(
    "--format",
    "import_format",
    type=click.Choice(["moss-capture"]),
    required=True,
    help="Legacy queue format",
)
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Active queue JSONL",
)
@click.option(
    "--processed",
    "processed_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Processed/archived queue JSONL",
)
@click.option("--dry-run", is_flag=True, help="Count records without importing")
@common_options
@click.pass_context
def import_events_cmd(
    ctx: click.Context,
    import_format: str,
    input_path: Path,
    processed_path: Optional[Path],
    dry_run: bool,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Import a legacy capture queue, repeat-safely.

    Open records become pending events. Archived records become
    resolved/journal_only — the old archive flag proved queue disposition,
    not semantic incorporation.
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    try:
        result = ev.import_moss_capture(
            kb_root,
            input_path=input_path,
            processed_path=processed_path,
            dry_run=dry_run,
        )
    except ValueError as exc:
        if ctx.obj.get("as_json"):
            output_json({"success": False, "error": str(exc)})
            ctx.exit(1)
        raise click.ClickException(str(exc))
    if ctx.obj.get("as_json"):
        output_json(result)
        return
    counts = result["counts"]
    mode = "Would import" if dry_run else "Imported"
    click.echo(
        f"{mode}: {counts['open']} open, {counts['archived']} archived "
        f"({counts['duplicate']} already present, {counts['invalid']} invalid, "
        f"{counts['conflict']} conflicts)"
    )
