"""CLI command for structured node search."""

from pathlib import Path
from typing import Optional

import click

from kvault.cli._helpers import apply_common_options, common_options, output_json, resolve_kb_root
from kvault.core import operations as ops


@click.command("search")
@click.argument("query")
@click.option("--limit", default=10, show_default=True, type=int, help="Maximum results.")
@click.option(
    "--include-content",
    is_flag=True,
    help="Include matching node content with truncation safeguards.",
)
@click.option(
    "--content-max-chars",
    default=6000,
    show_default=True,
    type=int,
    help="Maximum content characters per result.",
)
@click.option(
    "--max-total-chars",
    default=20000,
    show_default=True,
    type=int,
    help="Maximum total content characters across results.",
)
@common_options
@click.pass_context
def search_nodes(
    ctx: click.Context,
    query: str,
    limit: int,
    include_content: bool,
    content_max_chars: int,
    max_total_chars: int,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Search node summaries with structured lexical ranking."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    result = ops.search_nodes(
        kb_root,
        query=query,
        limit=limit,
        include_content=include_content,
        content_max_chars=content_max_chars,
        total_max_chars=max_total_chars,
    )
    if ctx.obj.get("as_json"):
        output_json(result)
        return

    if not result["results"]:
        click.echo(f"No results for {query!r}.")
        return

    for item in result["results"]:
        click.echo(f"{item['path']}  {item['title']}  {item['kind']}  score={item['score']}")
        if item.get("snippet"):
            click.echo(f"  {item['snippet']}")
