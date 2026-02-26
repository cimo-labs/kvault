"""kvault CLI — CLI-first knowledge base for AI agents."""

import json
from datetime import date
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Dict, Optional

import click

from kvault.cli._helpers import find_kb_root, output_json, resolve_kb_root
from kvault.cli.check import check_kb
from kvault.cli.entity import read_entity, write_entity, list_entities, delete_entity, move_entity
from kvault.cli.journal import write_journal
from kvault.cli.summary import read_summary, write_summary, update_summaries, ancestors
from kvault.cli.validate import validate_kb
from kvault.core.daily_artifacts import generate_daily_artifact, parse_iso_date
from kvault.core.observability import ObservabilityLogger
from kvault.core import operations as ops

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
@click.option(
    "--kb-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Knowledge base root (auto-detected if not specified)",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def cli(ctx: click.Context, kb_root: Optional[Path], as_json: bool) -> None:
    """kvault — personal knowledge base for AI agents."""
    ctx.ensure_object(dict)
    ctx.obj["kb_root"] = kb_root
    ctx.obj["as_json"] = as_json


# Register commands
cli.add_command(check_kb)
cli.add_command(read_entity)
cli.add_command(write_entity)
cli.add_command(list_entities, "list")
cli.add_command(delete_entity, "delete")
cli.add_command(move_entity, "move")
cli.add_command(read_summary)
cli.add_command(write_summary)
cli.add_command(update_summaries)
cli.add_command(ancestors)
cli.add_command(write_journal)
cli.add_command(validate_kb, "validate")


@cli.command("init")
@click.argument("path", type=click.Path(path_type=Path), default=".")
@click.option("--name", default="My", help="Owner name for the knowledge base")
@click.pass_context
def init_kb(ctx: click.Context, path: Path, name: str) -> None:
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
    click.echo("Next: read CLAUDE.md for agent workflow instructions.")
    click.echo("Use 'kvault --help' to see all commands.")


@cli.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show KB status: root, entity count, hierarchy, health."""
    kb_root = resolve_kb_root(ctx)
    info = ops.get_kb_info(kb_root)
    health = {
        "root_summary_exists": (kb_root / "_summary.md").exists(),
        "kvault_dir_exists": (kb_root / ".kvault").exists(),
    }
    info["health"] = health
    if ctx.obj.get("as_json"):
        output_json(info)
    else:
        click.echo(f"KB root: {info['kg_root']}")
        click.echo(f"Entities: {info['entity_count']}")
        click.echo(f"Root summary: {'✓' if health['root_summary_exists'] else '✗'}")
        click.echo(f".kvault dir: {'✓' if health['kvault_dir_exists'] else '✗'}")
        click.echo()
        click.echo(info["hierarchy"])


@cli.command("tree")
@click.option("--depth", type=int, default=3, help="Max depth to display")
@click.pass_context
def tree(ctx: click.Context, depth: int) -> None:
    """Print KB hierarchy tree."""
    kb_root = resolve_kb_root(ctx)
    click.echo(ops.build_hierarchy_tree(kb_root, max_depth=depth))


@cli.group("artifact")
def artifact_group() -> None:
    """Generate derivative artifacts from the KB."""


@artifact_group.command("daily")
@click.option(
    "--kb-root",
    type=click.Path(path_type=Path),
    default=".",
    show_default=True,
    help="Knowledge base root directory",
)
@click.option(
    "--date",
    "artifact_date",
    default=None,
    help="Artifact date (YYYY-MM-DD). Defaults to today.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing artifact for the given date.",
)
@click.option(
    "--stdout",
    "print_stdout",
    is_flag=True,
    help="Print generated artifact markdown to stdout.",
)
def generate_daily(
    kb_root: Path, artifact_date: Optional[str], force: bool, print_stdout: bool
) -> None:
    """Generate the daily artifact markdown file."""
    kb_root = kb_root.resolve()
    if not kb_root.exists():
        raise click.ClickException(f"Knowledge base root does not exist: {kb_root}")

    try:
        parsed_date = parse_iso_date(artifact_date)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    result = generate_daily_artifact(kb_root, artifact_date=parsed_date, force=force)
    status = "Generated" if result.written else "Reused existing"
    rel_path = result.path.relative_to(kb_root)
    click.echo(f"{status} daily artifact: {rel_path}")

    if print_stdout:
        click.echo()
        click.echo(result.content)


@cli.group("log")
def log_group() -> None:
    """Inspect kvault observability logs."""


@log_group.command("summary")
@click.option(
    "--db",
    "db_path",
    type=click.Path(path_type=Path),
    default=Path(".kvault/logs.db"),
    show_default=True,
    help="Path to observability SQLite database",
)
@click.option(
    "--session-id",
    default=None,
    help="Optional session id. Defaults to the latest session in the database.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print summary as JSON.",
)
def log_summary(db_path: Path, session_id: Optional[str], as_json: bool) -> None:
    """Show high-level stats for an observability session."""
    db_path = db_path.resolve()
    if not db_path.exists():
        raise click.ClickException(f"Log database does not exist: {db_path}")

    logger = ObservabilityLogger(db_path)
    summary = logger.get_session_summary(session_id=session_id)

    if as_json:
        click.echo(json.dumps(summary, indent=2, sort_keys=True))
        return

    click.echo(f"Session: {summary['session_id']}")
    click.echo(f"Total logs: {summary['total_logs']}")
    click.echo(f"Errors: {summary['error_count']}")

    click.echo("Phase counts:")
    if summary["phase_counts"]:
        for phase, count in sorted(summary["phase_counts"].items()):
            click.echo(f"  - {phase}: {count}")
    else:
        click.echo("  - (none)")

    click.echo("Action counts:")
    if summary["action_counts"]:
        for action, count in sorted(summary["action_counts"].items()):
            click.echo(f"  - {action}: {count}")
    else:
        click.echo("  - (none)")


if __name__ == "__main__":
    cli()
