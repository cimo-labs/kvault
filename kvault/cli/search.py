"""CLI command for structured node search."""

from pathlib import Path
from typing import Optional, Tuple

import click

from kvault.cli._helpers import (
    apply_common_options,
    apply_verbosity_options,
    common_options,
    finish_op,
    get_tier,
    output_json,
    resolve_kb_root,
    verbosity_options,
)
from kvault.cli.render import render_notes
from kvault.core import notes as nt
from kvault.core import operations as ops
from kvault.core.search import KINDS


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
@click.option(
    "--no-collapse",
    "no_collapse",
    is_flag=True,
    help="Keep ancestor hits that only repeat a descendant's match.",
)
@click.option(
    "--kind",
    "kinds",
    multiple=True,
    type=click.Choice(list(KINDS)),
    help="Only nodes of this kind (repeatable).",
)
@click.option("--path", "path_prefix", default=None, help="Only nodes at or under this path.")
@verbosity_options
@common_options
@click.pass_context
def search_nodes(
    ctx: click.Context,
    query: str,
    limit: int,
    include_content: bool,
    content_max_chars: int,
    max_total_chars: int,
    no_collapse: bool,
    kinds: Tuple[str, ...],
    path_prefix: Optional[str],
    kb_root: Optional[Path],
    as_json: bool,
    quiet: bool,
    explain: bool,
    trace: bool,
    strict: bool,
) -> None:
    """Search node summaries with structured lexical ranking."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    apply_verbosity_options(ctx, quiet=quiet, explain=explain, trace=trace, strict=strict)
    kb_root = resolve_kb_root(ctx)
    result = ops.search_nodes(
        kb_root,
        query=query,
        limit=limit,
        include_content=include_content,
        content_max_chars=content_max_chars,
        total_max_chars=max_total_chars,
        collapse=not no_collapse,
        kinds=list(kinds) or None,
        path_prefix=path_prefix,
    )
    if ctx.obj.get("as_json"):
        output_json(result)
        finish_op(ctx, result)
        return

    tier = get_tier(ctx)
    if not result["results"]:
        click.echo(f"No results for {query!r}.")
        render_notes(result, tier)
        finish_op(ctx, result)
        return

    for item in result["results"]:
        click.echo(f"{item['path']}  {item['title']}  {item['kind']}  score={item['score']}")
        if item.get("snippet"):
            click.echo(f"  {item['snippet']}")
        if tier >= nt.EXPLAIN:
            click.echo(f"  matched: {', '.join(item.get('matched_fields', []))}")
            # --include-content used to be a silent no-op in human mode.
            if item.get("content"):
                click.echo("  --- content ---")
                for line in item["content"].splitlines():
                    click.echo(f"  {line}")
                if item.get("content_truncated"):
                    click.echo(f"  [content truncated: {item.get('content_omitted_reason')}]")
    render_notes(result, tier)
    finish_op(ctx, result)
