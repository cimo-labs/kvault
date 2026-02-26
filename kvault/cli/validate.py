"""CLI command for KB validation."""

import click

from kvault.cli._helpers import output_json, resolve_kb_root
from kvault.core import operations as ops


@click.command("validate")
@click.pass_context
def validate_kb(ctx: click.Context) -> None:
    """Validate KB integrity (incomplete entities, missing frontmatter)."""
    kb_root = resolve_kb_root(ctx)
    result = ops.validate_kb(kb_root)
    if ctx.obj.get("as_json"):
        output_json(result)
    else:
        if result["valid"]:
            click.echo("KB is valid.")
        else:
            click.echo(f"Issues found: {result['issue_count']}")
        for issue in result.get("issues", []):
            severity = issue["severity"].upper()
            click.echo(f"  [{severity}] {issue['path']}: {issue['message']}")
        if result["issue_count"] == 0:
            click.echo("No issues found.")
