"""CLI command for journal writing."""

import time
from pathlib import Path
from typing import Optional

import click

from kvault.cli._helpers import (
    apply_common_options,
    apply_verbosity_options,
    common_options,
    finish_op,
    get_tier,
    output_json,
    read_stdin_json,
    record_op,
    resolve_kb_root,
    verbosity_options,
)
from kvault.cli.render import render_notes
from kvault.core import operations as ops


@click.command("journal")
@click.option("--source", required=True, help="Source identifier for the journal entry")
@click.option("--date", "journal_date", default=None, help="Date (YYYY-MM-DD, defaults to today)")
@verbosity_options
@common_options
@click.pass_context
def write_journal(
    ctx: click.Context,
    source: str,
    journal_date: Optional[str],
    kb_root: Optional[Path],
    as_json: bool,
    quiet: bool,
    explain: bool,
    trace: bool,
    strict: bool,
) -> None:
    """Write a journal entry from stdin JSON actions array.

    Expects: [{"action_type": "create", "path": "...", "reasoning": "..."}]
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    apply_verbosity_options(ctx, quiet=quiet, explain=explain, trace=trace, strict=strict)
    kb_root = resolve_kb_root(ctx)
    actions = read_stdin_json()
    if not isinstance(actions, list):
        raise click.ClickException("Expected a JSON array of actions on stdin")
    started = time.monotonic()
    result = ops.write_journal(kb_root, actions, source, date=journal_date)
    record_op(kb_root, "journal", result, started)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            click.echo(f"Logged {result['actions_logged']} actions to {result['journal_path']}")
            render_notes(result, get_tier(ctx))
        else:
            raise click.ClickException("Journal write failed")
    finish_op(ctx, result)
