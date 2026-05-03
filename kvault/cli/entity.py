"""CLI commands for entity operations: read, write, list, delete, move."""

from typing import Optional

import click

from pathlib import Path

from kvault.cli._helpers import (
    apply_common_options,
    common_options,
    output_json,
    read_stdin,
    resolve_kb_root,
)
from kvault.core import operations as ops


@click.command("read")
@click.argument("path")
@click.option(
    "--parents",
    type=click.Choice(["none", "immediate", "all"]),
    default="immediate",
    show_default=True,
    help="Parent context to include.",
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
    """Read a node and its parent summary."""
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
@common_options
@click.pass_context
def write_entity(
    ctx: click.Context,
    path: str,
    create: bool,
    reasoning: Optional[str],
    journal_source: Optional[str],
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Write a node from stdin (frontmatter + markdown body).

    Content is read from stdin. Include YAML frontmatter for metadata,
    or omit it to use defaults.
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    raw = read_stdin()

    # Parse frontmatter from stdin content
    from kvault.core.frontmatter import parse_frontmatter

    meta, body = parse_frontmatter(raw)

    result = ops.write_node(
        kb_root,
        path,
        body if meta else raw,
        meta=meta if meta else None,
        create=create,
        reasoning=reasoning,
        journal_source=journal_source,
    )
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            action = "Created" if result.get("created") else "Updated"
            click.echo(f"{action}: {result['path']}")
            if result.get("journal_logged"):
                click.echo(f"Journal: {result.get('journal_path')}")
            n = len(result.get("ancestors", []))
            if n:
                click.echo(f"Ancestors to update: {n}")
        else:
            raise click.ClickException(result.get("error", "Write failed"))


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


@click.command("delete")
@click.argument("path")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@common_options
@click.pass_context
def delete_entity(
    ctx: click.Context, path: str, force: bool, kb_root: Optional[Path], as_json: bool
) -> None:
    """Delete an entity."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    if not force and not ctx.obj.get("as_json"):
        click.confirm(f"Delete entity '{path}'?", abort=True)
    result = ops.delete_entity(kb_root, path)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            click.echo(f"Deleted: {path}")
        else:
            raise click.ClickException(result.get("error", "Delete failed"))


@click.command("move")
@click.argument("source")
@click.argument("target")
@common_options
@click.pass_context
def move_entity(
    ctx: click.Context, source: str, target: str, kb_root: Optional[Path], as_json: bool
) -> None:
    """Move an entity to a new path."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    result = ops.move_entity(kb_root, source, target)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            click.echo(f"Moved: {source} → {target}")
        else:
            raise click.ClickException(result.get("error", "Move failed"))
