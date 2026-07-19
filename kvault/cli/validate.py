"""CLI command for KB validation."""

import click

from pathlib import Path
from typing import Optional

from kvault.cli._helpers import apply_common_options, common_options, output_json, resolve_kb_root
from kvault.core.validation import audit_kb


@click.command("validate")
@common_options
@click.pass_context
def validate_kb(ctx: click.Context, kb_root: Optional[Path], as_json: bool) -> None:
    """Validate KB integrity (incomplete entities, missing frontmatter)."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    result = audit_kb(kb_root)
    payload = {"success": result["valid"], **result}
    if ctx.obj.get("as_json"):
        output_json(payload)
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
    if not result["valid"]:
        ctx.exit(1)
