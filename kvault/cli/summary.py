"""CLI commands for summary operations: read-summary, write-summary, update-summaries, ancestors."""

from pathlib import Path
from typing import Optional

import click

from kvault.cli._helpers import (
    apply_common_options,
    common_options,
    output_json,
    read_stdin,
    read_stdin_json,
    resolve_kb_root,
)
from kvault.core import operations as ops


@click.command("read-summary")
@click.argument("path")
@common_options
@click.pass_context
def read_summary(ctx: click.Context, path: str, kb_root: Optional[Path], as_json: bool) -> None:
    """Read a summary file."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    result = ops.read_summary(kb_root, path)
    if result is None:
        if ctx.obj.get("as_json"):
            output_json({"success": False, "error": f"Summary not found: {path}"})
            ctx.exit(1)
        else:
            raise click.ClickException(f"Summary not found: {path}")
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        click.echo(f"Path: {result['path']}")
        click.echo()
        click.echo(result.get("content", ""))


@click.command("write-summary")
@click.argument("path")
@common_options
@click.pass_context
def write_summary(ctx: click.Context, path: str, kb_root: Optional[Path], as_json: bool) -> None:
    """Write a single summary file from stdin (frontmatter + body)."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    raw = read_stdin()

    from kvault.core.frontmatter import parse_frontmatter

    meta, body = parse_frontmatter(raw)
    result = ops.write_summary(kb_root, path, body if meta else raw, meta=meta if meta else None)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            click.echo(f"Updated summary: {path}")
        else:
            raise click.ClickException(result.get("error", "Write failed"))


@click.command("update-summaries")
@common_options
@click.pass_context
def update_summaries(ctx: click.Context, kb_root: Optional[Path], as_json: bool) -> None:
    """Batch-update summaries from stdin JSON array.

    Expects: [{"path": "...", "content": "..."}]
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    updates = read_stdin_json()
    if not isinstance(updates, list):
        raise click.ClickException("Expected a JSON array on stdin")
    result = ops.update_summaries(kb_root, updates)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            click.echo(
                f"Updated {result['count']} summaries: {', '.join(result.get('updated', []))}"
            )
        else:
            raise click.ClickException("Update failed")
        if result.get("errors"):
            for err in result["errors"]:
                click.echo(f"  Error: {err['path']}: {err['error']}", err=True)


@click.command("ancestors")
@click.argument("path")
@common_options
@click.pass_context
def ancestors(ctx: click.Context, path: str, kb_root: Optional[Path], as_json: bool) -> None:
    """Get ancestor summaries for propagation."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    result = ops.get_ancestors(kb_root, path)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("ancestors"):
            for a in result["ancestors"]:
                has_meta = "✓" if a.get("has_meta") else "✗"
                click.echo(f"  {a['path']}  (meta: {has_meta})")
        else:
            click.echo("No ancestors found.")
