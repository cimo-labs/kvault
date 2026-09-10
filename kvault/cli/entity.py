"""CLI commands for entity operations: read, write, list, delete, move."""

import time
from typing import Optional

import click

from pathlib import Path

from kvault.cli._helpers import (
    apply_common_options,
    apply_verbosity_options,
    common_options,
    finish_op,
    get_tier,
    output_json,
    read_stdin,
    read_stdin_json,
    record_op,
    resolve_kb_root,
    verbosity_options,
)
from kvault.cli.render import render_notes
from kvault.core import operations as ops


@click.command("read")
@click.argument("path")
@click.option(
    "--parents",
    type=click.Choice(["none", "immediate", "all"]),
    default="none",
    show_default=True,
    help="Parent context to include (immediate = parent summary for sibling context).",
)
@common_options
@click.pass_context
def read_entity(
    ctx: click.Context,
    path: str,
    parents: str,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Read a node (add --parents immediate for the parent summary)."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    result = ops.read_node(kb_root, path, parents=parents)
    if result is None:
        if ctx.obj.get("as_json"):
            output_json({"success": False, "error": f"Node not found: {path}"})
            ctx.exit(1)
        else:
            raise click.ClickException(f"Node not found: {path}")
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        meta = result.get("meta", {})
        click.echo(f"Path: {result['path']}")
        click.echo(f"Kind: {result['kind']}")
        if meta.get("name"):
            click.echo(f"Name: {meta['name']}")
        if meta.get("aliases"):
            click.echo(f"Aliases: {', '.join(str(a) for a in meta['aliases'])}")
        if meta.get("source"):
            click.echo(f"Source: {meta['source']}")
        if result.get("parent"):
            parent = result["parent"]
            click.echo(f"Parent: {parent['path']}")
            click.echo()
            click.echo(f"Parent summary ({parent['path']}):")
            click.echo(parent.get("content", "").rstrip())
            click.echo()
            click.echo("Node content:")
        click.echo()
        click.echo(result.get("content", ""))


@click.command("write")
@click.argument("path")
@click.option("--create", is_flag=True, help="Create new entity (fail if exists)")
@click.option("--reasoning", default=None, help="Reasoning for auto-journal logging")
@click.option("--journal-source", default=None, help="Override source for journal entry")
@click.option(
    "--event",
    "event_ids",
    multiple=True,
    help="Captured event ID this write promotes (repeatable); "
    "stamps provenance and resolves the event",
)
@click.option(
    "--new-root",
    is_flag=True,
    help="Allow this create to add a new root category (refused otherwise)",
)
@click.option(
    "--allow-similar",
    is_flag=True,
    help="Create even though a sibling has the same words (refused otherwise)",
)
@verbosity_options
@common_options
@click.pass_context
def write_entity(
    ctx: click.Context,
    path: str,
    create: bool,
    reasoning: Optional[str],
    journal_source: Optional[str],
    event_ids: tuple,
    new_root: bool,
    allow_similar: bool,
    kb_root: Optional[Path],
    as_json: bool,
    quiet: bool,
    explain: bool,
    trace: bool,
    strict: bool,
) -> None:
    """Write a node from stdin (frontmatter + markdown body).

    Content is read from stdin. Include YAML frontmatter for metadata,
    or omit it to use defaults.
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    apply_verbosity_options(ctx, quiet=quiet, explain=explain, trace=trace, strict=strict)
    kb_root = resolve_kb_root(ctx)
    raw = read_stdin()

    # Parse frontmatter from stdin content
    from kvault.core.frontmatter import parse_frontmatter

    meta, body = parse_frontmatter(raw)

    started = time.monotonic()
    result = ops.write_node(
        kb_root,
        path,
        body if meta else raw,
        meta=meta if meta else None,
        create=create,
        reasoning=reasoning,
        journal_source=journal_source,
        event_ids=list(event_ids) or None,
        new_root=new_root,
        allow_similar=allow_similar,
    )
    record_op(kb_root, "write", result, started)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            if result.get("created"):
                action = "Created"
            elif result.get("changed", True):
                action = "Updated"
            else:
                # Saying "Updated" for a detected no-op was actively false.
                action = "Unchanged"
            click.echo(f"{action}: {result['path']}")
            render_notes(result, get_tier(ctx))
            if result.get("journal_logged"):
                click.echo(f"Journal: {result.get('journal_path')}")
            paths = result.get("ancestor_paths") or []
            if paths and result.get("changed", True):
                click.echo(f"Ancestors to update: {len(paths)}  ({', '.join(paths)})")
        else:
            raise click.ClickException(result.get("error", "Write failed"))
    finish_op(ctx, result)


@click.command("list")
@click.argument("path", required=False, default=".")
@click.option("--recursive", is_flag=True, help="List descendant nodes recursively.")
@common_options
@click.pass_context
def list_entities(
    ctx: click.Context,
    path: str,
    recursive: bool,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """List child nodes under a path."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    entities = ops.list_nodes(kb_root, path=path, recursive=recursive)
    if ctx.obj.get("as_json"):
        output_json(entities)
    else:
        if not entities:
            click.echo("No nodes found.")
            return
        for e in entities:
            click.echo(f"  {e['path']}  ({e['title']}, {e['kind']})")


def _confirmation_error(operation: str, detail: str) -> dict:
    return {
        "success": False,
        "error_code": "confirmation_required",
        "error": f"{operation} is destructive and requires explicit confirmation",
        "hint": f"Re-run with --confirm to {detail}",
    }


@click.command("delete")
@click.argument("path")
@click.option("--confirm", is_flag=True, help="Confirm this destructive operation")
@click.option("--force", is_flag=True, help="Deprecated alias for --confirm")
@verbosity_options
@common_options
@click.pass_context
def delete_entity(
    ctx: click.Context,
    path: str,
    confirm: bool,
    force: bool,
    kb_root: Optional[Path],
    as_json: bool,
    quiet: bool,
    explain: bool,
    trace: bool,
    strict: bool,
) -> None:
    """Delete an entity (requires --confirm, or answering an interactive prompt)."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    apply_verbosity_options(ctx, quiet=quiet, explain=explain, trace=trace, strict=strict)
    kb_root = resolve_kb_root(ctx)
    confirmed = confirm or force
    if not confirmed:
        if ctx.obj.get("as_json"):
            output_json(_confirmation_error("delete", f"delete '{path}' and its subtree"))
            ctx.exit(1)
        click.confirm(f"Delete entity '{path}'?", abort=True)
    started = time.monotonic()
    result = ops.delete_entity(kb_root, path)
    record_op(kb_root, "delete", result, started)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            click.echo(f"Deleted: {path}")
            render_notes(result, get_tier(ctx))
        else:
            raise click.ClickException(result.get("error", "Delete failed"))
    finish_op(ctx, result)


@click.command("move")
@click.argument("source", required=False, default=None)
@click.argument("target", required=False, default=None)
@click.option("--confirm", is_flag=True, help="Confirm this destructive operation")
@click.option(
    "--new-root",
    is_flag=True,
    help="Allow the destination to add a new root category (refused otherwise)",
)
@click.option(
    "--batch",
    is_flag=True,
    help='Read a JSON list of {"from", "to"} moves from stdin; one lock, one confirm',
)
@click.option("--dry-run", is_flag=True, help="With --batch: validate and report, move nothing")
@verbosity_options
@common_options
@click.pass_context
def move_entity(
    ctx: click.Context,
    source: Optional[str],
    target: Optional[str],
    confirm: bool,
    new_root: bool,
    batch: bool,
    dry_run: bool,
    kb_root: Optional[Path],
    as_json: bool,
    quiet: bool,
    explain: bool,
    trace: bool,
    strict: bool,
) -> None:
    """Move an entity (requires --confirm, or answering an interactive prompt).

    With --batch, stdin carries a JSON list of {"from", "to"} moves that run
    under one lock with one confirmation and one combined propagation list
    (the shape `kvault plan` emits).
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    apply_verbosity_options(ctx, quiet=quiet, explain=explain, trace=trace, strict=strict)
    kb_root = resolve_kb_root(ctx)
    if batch:
        _move_batch(ctx, kb_root, confirm=confirm, new_root=new_root, dry_run=dry_run)
        return
    if source is None or target is None:
        raise click.UsageError("SOURCE and TARGET are required unless --batch is given")
    if dry_run:
        raise click.UsageError("--dry-run only applies with --batch")
    if not confirm:
        if ctx.obj.get("as_json"):
            output_json(
                _confirmation_error("move", f"move '{source}' (and its subtree) to '{target}'")
            )
            ctx.exit(1)
        click.confirm(f"Move '{source}' to '{target}'?", abort=True)
    started = time.monotonic()
    result = ops.move_entity(kb_root, source, target, new_root=new_root)
    record_op(kb_root, "move", result, started)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            click.echo(f"Moved: {source} → {target}")
            render_notes(result, get_tier(ctx))
        else:
            raise click.ClickException(result.get("error", "Move failed"))
    finish_op(ctx, result)


def _move_batch(
    ctx: click.Context, kb_root: Path, confirm: bool, new_root: bool, dry_run: bool
) -> None:
    moves = read_stdin_json()
    if not isinstance(moves, list):
        raise click.ClickException("--batch expects a JSON list of {from, to} objects on stdin")
    if not dry_run and not confirm:
        if ctx.obj.get("as_json"):
            output_json(_confirmation_error("move --batch", f"move {len(moves)} nodes"))
            ctx.exit(1)
        # stdin carried the payload, so there is no stream left to prompt on.
        raise click.UsageError(
            f"--batch would move {len(moves)} nodes and cannot prompt (stdin is the payload); "
            "pass --confirm, or --dry-run to preview"
        )
    started = time.monotonic()
    result = ops.move_entities(kb_root, moves, new_root=new_root, dry_run=dry_run)
    if not dry_run:
        record_op(kb_root, "move-batch", result, started)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if not result.get("success"):
            detail = result.get("details", {}).get("errors") or []
            lines = [result.get("error", "Batch move failed")]
            lines.extend(f"  [{e.get('index')}] {e.get('error')}" for e in detail[:10])
            raise click.ClickException("\n".join(lines))
        if dry_run:
            click.echo(f"Dry run: {result['count']} moves valid")
            for mv in result["moves"]:
                click.echo(f"  {mv['from']} → {mv['to']}")
            if result.get("stubs"):
                click.echo(f"  would stub summaries: {', '.join(result['stubs'])}")
        else:
            click.echo(f"Moved: {result['count']} nodes")
            for mv in result["moved"]:
                click.echo(f"  {mv['from']} → {mv['to']}")
        render_notes(result, get_tier(ctx))
        paths = result.get("ancestor_paths") or []
        if paths:
            click.echo(f"Ancestors to update: {len(paths)}  ({', '.join(paths)})")
    finish_op(ctx, result)


@click.command("mark")
@click.argument("path")
@click.option(
    "--distinct-from",
    "distinct_from",
    multiple=True,
    help="Record that PATH and this node are different things (sibling name or KB path; repeatable)",
)
@click.option(
    "--max-children", type=int, default=None, help="This parent's own child ceiling (0 clears)"
)
@click.option(
    "--series-ok/--no-series-ok",
    "series_ok",
    default=None,
    help="This parent's dated children are an intentional chronology",
)
@click.option("--clear", is_flag=True, help="Drop all recorded decisions on PATH first")
@verbosity_options
@common_options
@click.pass_context
def mark_node(
    ctx: click.Context,
    path: str,
    distinct_from: tuple,
    max_children: Optional[int],
    series_ok: Optional[bool],
    clear: bool,
    kb_root: Optional[Path],
    as_json: bool,
    quiet: bool,
    explain: bool,
    trace: bool,
    strict: bool,
) -> None:
    """Record a structure decision on a node so check, plan, and the guards honor it.

    A correction that only lives in a conversation is re-proposed next week;
    one recorded here sticks: `distinct_from` silences the sibling-collision
    finding and the create guard for that pair, `max_children` sets the
    parent's own ceiling, `--series-ok` keeps a deliberate chronology.
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    apply_verbosity_options(ctx, quiet=quiet, explain=explain, trace=trace, strict=strict)
    kb_root = resolve_kb_root(ctx)
    started = time.monotonic()
    result = ops.mark_node(
        kb_root,
        path,
        distinct_from=list(distinct_from) or None,
        max_children=max_children,
        series_ok=series_ok,
        clear=clear,
    )
    record_op(kb_root, "mark", result, started)
    if ctx.obj.get("as_json"):
        output_json(result)
        if not result.get("success"):
            ctx.exit(1)
    else:
        if not result.get("success"):
            raise click.ClickException(result.get("error", "mark failed"))
        click.echo(result["did"].replace("marked ", "Marked: ", 1))
        render_notes(result, get_tier(ctx))
    finish_op(ctx, result)
