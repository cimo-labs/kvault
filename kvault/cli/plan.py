"""``kvault plan`` — the ordered maintenance worklist."""

from pathlib import Path
from typing import Any, Dict, Optional

import click

from kvault.cli._helpers import (
    apply_common_options,
    common_options,
    output_json,
    resolve_kb_root,
)
from kvault.core.check import DEFAULT_MAX_CHILDREN
from kvault.core.plan import DEFAULT_LIMIT, build_plan

#: Commands printed per item in human mode; the JSON carries all of them.
MAX_COMMANDS_SHOWN = 6


def _echo_item(index: int, item: Dict[str, Any]) -> None:
    head = f"{index}. {item['kind']:<9}{item['path']}"
    if item["kind"] == "cluster":
        head += f" → {item['new_parent']}"
    click.echo(head)
    click.echo(f"     {item['why']}")
    commands = item.get("commands", [])
    shown = commands[:MAX_COMMANDS_SHOWN]
    for command in shown:
        for line in str(command).splitlines():
            click.echo(f"     {line}")
    if len(commands) > len(shown):
        click.echo(f"     … +{len(commands) - len(shown)} more commands (--json for all)")
    if item.get("then"):
        click.echo(f"     then: {item['then']}")


@click.command("plan")
@click.argument("path", required=False, default=None)
@click.option(
    "--limit",
    type=int,
    default=DEFAULT_LIMIT,
    show_default=True,
    help="Items to show, in leverage order (0 = all).",
)
@click.option(
    "--max-children",
    type=int,
    default=DEFAULT_MAX_CHILDREN,
    show_default=True,
    help="Direct-child ceiling that makes a parent a clustering candidate.",
)
@common_options
@click.pass_context
def plan(
    ctx: click.Context,
    path: Optional[str],
    limit: int,
    max_children: int,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """List maintenance work in leverage order, with the exact commands.

    Clusters over-fanout parents by leading word, then ghosts, sibling
    collisions, loose files, journal drift, and summary rewrites. Never
    applies anything; judgment calls come back as questions.
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    result = build_plan(kb_root, path=path, limit=limit, max_children=max_children)
    if ctx.obj.get("as_json"):
        output_json(result)
        if not result.get("success"):
            ctx.exit(1)
        return
    if not result.get("success"):
        raise click.ClickException(result.get("error", "plan failed"))
    total, count = result["total"], result["count"]
    if total == 0:
        click.echo(f"Plan for {result['path']}: nothing to do.")
        return
    suffix = f" (showing {count}; --limit 0 for all)" if count < total else ""
    click.echo(f"Plan for {result['path']}: {total} item{'s' if total != 1 else ''}{suffix}")
    for index, item in enumerate(result["items"], 1):
        _echo_item(index, item)
    if result.get("questions"):
        click.echo(
            "Decisions (each has a default; act on it, record it with kvault mark if it should stick):"
        )
        for question in result["questions"]:
            click.echo(f"  - {question}")
