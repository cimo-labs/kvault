"""kvault CLI — init and check commands."""

import json
from datetime import date
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Dict, Optional

import click

from kvault.cli.check import check_kb
from kvault.core.observability import ObservabilityLogger

# -------------------------
# Helpers
# -------------------------


def _load_template(name: str) -> str:
    return resource_files("kvault.templates").joinpath(name).read_text()


def _render(template: str, replacements: Dict[str, str]) -> str:
    result = template
    for key, value in replacements.items():
        result = result.replace("{{" + key + "}}", value)
    return result


# -------------------------
# CLI
# -------------------------


@click.group()
def cli() -> None:
    """kvault — personal knowledge base for AI agents."""


cli.add_command(check_kb)


@cli.command("init")
@click.argument("path", type=click.Path(path_type=Path), default=".")
@click.option("--name", default="My", help="Owner name for the knowledge base")
def init_kb(path: Path, name: str) -> None:
    """Initialize a new kvault knowledge base."""
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)

    if (path / ".kvault").exists():
        raise click.ClickException(
            f"KB already exists at {path} (.kvault/ directory found). "
            "Delete it first if you want to reinitialize."
        )

    today = date.today()
    replacements = {
        "OWNER_NAME": name,
        "DATE": today.isoformat(),
        "MONTH_YEAR": today.strftime("%Y-%m"),
    }

    root_tpl = _load_template("root_summary.md")
    cat_tpl = _load_template("category_summary.md")
    journal_tpl = _load_template("journal_entry.md")
    claude_tpl = _load_template("CLAUDE.md")

    (path / "_summary.md").write_text(_render(root_tpl, replacements))
    (path / "CLAUDE.md").write_text(_render(claude_tpl, replacements))

    categories = {
        "people": "People tracked in this knowledge base.",
        "people/family": "Close family members.",
        "people/friends": "Personal friends.",
        "people/contacts": "Professional contacts, acquaintances, and others.",
        "projects": "Active work and research initiatives.",
        "accomplishments": "Professional wins and quantifiable impacts.",
    }

    for cat_path, description in categories.items():
        cat_dir = path / cat_path
        cat_dir.mkdir(parents=True, exist_ok=True)
        cat_replacements = {
            **replacements,
            "CATEGORY_NAME": cat_path.split("/")[-1].replace("_", " ").title(),
            "DESCRIPTION": description,
        }
        (cat_dir / "_summary.md").write_text(_render(cat_tpl, cat_replacements))

    journal_dir = path / "journal" / today.strftime("%Y-%m")
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / "log.md").write_text(_render(journal_tpl, replacements))

    kvault_dir = path / ".kvault"
    kvault_dir.mkdir(parents=True, exist_ok=True)
    ObservabilityLogger(kvault_dir / "logs.db")

    click.echo(f"Initialized knowledge base at {path}")
    click.echo(f"Owner: {name}")
    click.echo()
    click.echo("Next: add MCP server to your AI tool config:")
    click.echo()
    click.echo('  { "mcpServers": { "kvault": { "command": "kvault-mcp" } } }')
    click.echo()
    click.echo("Then customize CLAUDE.md and start adding entities.")


if __name__ == "__main__":
    cli()
