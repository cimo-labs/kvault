"""CLI command for journal writing."""

from typing import Optional

import click

from kvault.cli._helpers import output_json, read_stdin_json, resolve_kb_root
from kvault.core import operations as ops


@click.command("journal")
@click.option("--source", required=True, help="Source identifier for the journal entry")
@click.option("--date", "journal_date", default=None, help="Date (YYYY-MM-DD, defaults to today)")
@click.pass_context
def write_journal(ctx: click.Context, source: str, journal_date: Optional[str]) -> None:
    """Write a journal entry from stdin JSON actions array.

    Expects: [{"action_type": "create", "path": "...", "reasoning": "..."}]
    """
    kb_root = resolve_kb_root(ctx)
    actions = read_stdin_json()
    if not isinstance(actions, list):
        raise click.ClickException("Expected a JSON array of actions on stdin")
    result = ops.write_journal(kb_root, actions, source, date=journal_date)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result.get("success"):
            click.echo(f"Logged {result['actions_logged']} actions to {result['journal_path']}")
        else:
            raise click.ClickException("Journal write failed")
