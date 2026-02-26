"""CLI commands for entity operations: read, write, list, delete, move."""

from typing import Optional

import click

from kvault.cli._helpers import output_json, read_stdin, resolve_kb_root
from kvault.core import operations as ops


@click.command("read")
@click.argument("path")
@click.pass_context
def read_entity(ctx: click.Context, path: str) -> None:
    """Read an entity and its parent summary."""
    kb_root = resolve_kb_root(ctx)
    result = ops.read_entity(kb_root, path)
    if result is None:
        if ctx.obj.get("as_json"):
            output_json({"success": False, "error": f"Entity not found: {path}"})
            ctx.exit(1)
        else:
            raise click.ClickException(f"Entity not found: {path}")
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        meta = result.get("meta", {})
        click.echo(f"Path: {result['path']}")
        if meta.get("name"):
            click.echo(f"Name: {meta['name']}")
        if meta.get("aliases"):
            click.echo(f"Aliases: {', '.join(str(a) for a in meta['aliases'])}")
        if meta.get("source"):
            click.echo(f"Source: {meta['source']}")
        if result.get("parent_path"):
            click.echo(f"Parent: {result['parent_path']}")
        click.echo()
        click.echo(result.get("content", ""))


@click.command("write")
@click.argument("path")
@click.option("--create", is_flag=True, help="Create new entity (fail if exists)")
@click.option("--reasoning", default=None, help="Reasoning for auto-journal logging")
@click.option("--journal-source", default=None, help="Override source for journal entry")
@click.pass_context
def write_entity(
    ctx: click.Context,
    path: str,
    create: bool,
    reasoning: Optional[str],
    journal_source: Optional[str],
) -> None:
    """Write an entity from stdin (frontmatter + markdown body).

    Content is read from stdin. Include YAML frontmatter for metadata,
    or omit it to use defaults.
    """
    kb_root = resolve_kb_root(ctx)
    raw = read_stdin()

    # Parse frontmatter from stdin content
    from kvault.core.frontmatter import parse_frontmatter

    meta, body = parse_frontmatter(raw)

    result = ops.write_entity(
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
@click.argument("category", required=False, default=None)
@click.pass_context
def list_entities(ctx: click.Context, category: Optional[str]) -> None:
    """List entities, optionally filtered by category."""
    kb_root = resolve_kb_root(ctx)
    entities = ops.list_entities(kb_root, category=category)
    if ctx.obj.get("as_json"):
        output_json(entities)
    else:
        if not entities:
            click.echo("No entities found.")
            return
        for e in entities:
            click.echo(f"  {e['path']}  ({e['name']}, updated {e['last_updated']})")


@click.command("delete")
@click.argument("path")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def delete_entity(ctx: click.Context, path: str, force: bool) -> None:
    """Delete an entity."""
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
@click.pass_context
def move_entity(ctx: click.Context, source: str, target: str) -> None:
    """Move an entity to a new path."""
    kb_root = resolve_kb_root(ctx)
    result = ops.move_entity(kb_root, source, target)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            click.echo(f"Moved: {source} → {target}")
        else:
            raise click.ClickException(result.get("error", "Move failed"))
