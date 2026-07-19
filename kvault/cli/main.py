"""kvault CLI — CLI-first knowledge base for AI agents."""

import json
from datetime import date
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Dict, Optional

import click

from kvault import __version__
from kvault.cli._helpers import apply_common_options, common_options, output_json, resolve_kb_root
from kvault.cli.check import check_kb
from kvault.cli.entity import read_entity, list_entities
from kvault.cli.search import search_nodes
from kvault.cli.summary import read_summary, ancestors
from kvault.cli.validate import validate_kb
from kvault.cli.workflow import (
    capture_event_command,
    events_group,
    legacy_mutation_stub,
    migrate_command,
    reconcile_group,
    skill_group,
)
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


def _migration_success(result: object) -> bool:
    if isinstance(result, dict):
        return bool(result.get("success", False))
    return bool(getattr(result, "success", False))


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
@click.version_option(package_name="knowledgevault")
@click.pass_context
def cli(ctx: click.Context, kb_root: Optional[Path], as_json: bool) -> None:
    """kvault — personal knowledge base for AI agents."""
    ctx.ensure_object(dict)
    ctx.obj["kb_root"] = kb_root
    ctx.obj["as_json"] = as_json


# Register commands
cli.add_command(check_kb)
cli.add_command(read_entity)
cli.add_command(list_entities, "list")
cli.add_command(search_nodes)
cli.add_command(read_summary)
cli.add_command(ancestors)
cli.add_command(validate_kb, "validate")
cli.add_command(capture_event_command)
cli.add_command(events_group)
cli.add_command(reconcile_group)
cli.add_command(migrate_command)
cli.add_command(skill_group)
for _legacy_name in ("write", "delete", "move", "write-summary", "update-summaries", "journal"):
    cli.add_command(legacy_mutation_stub(_legacy_name))


@cli.command("init")
@click.argument("path", type=click.Path(path_type=Path), default=".")
@click.option("--name", default="My", help="Owner name for the knowledge base")
@click.pass_context
def init_kb(ctx: click.Context, path: Path, name: str) -> None:
    """Initialize a new kvault knowledge base."""
    path = path.resolve()
    allowed_error = ops.validate_allowed_root(path)
    if allowed_error:
        raise click.ClickException(allowed_error)
    if path.exists() and any(path.iterdir()):
        raise click.ClickException(
            f"Refusing to initialize nonempty directory: {path}. Choose an empty path."
        )
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
    agents_tpl = _load_template("AGENTS.md")

    (path / "_summary.md").write_text(_render(root_tpl, replacements))
    (path / "AGENTS.md").write_text(_render(agents_tpl, replacements))

    categories = {
        "people": (
            "People tracked in this knowledge base, organized into Family, Friends, and Contacts. "
            "Family is for relatives and household context, including relationship notes, important "
            "dates, preferences, current life state, and recurring obligations. Friends is for personal "
            "relationships, shared history, recent conversations, interests, plans, and follow-ups that "
            "help future agents preserve continuity. Contacts is for professional contacts, "
            "acquaintances, collaborators, vendors, customers, and other people who matter because of "
            "work, research, community, or logistics. As descendants are added, this parent summary "
            "should roll up the current state across all three branches so an agent can understand the "
            "whole people landscape before opening child files. Keep durable facts here: who matters, "
            "why they matter, what changed recently, what follow-up is pending, and which child branch "
            "contains the detailed evidence for future careful review."
        ),
        "people/family": "Close family members.",
        "people/friends": "Personal friends.",
        "people/contacts": "Professional contacts, acquaintances, and others.",
        "projects": "Active work and research initiatives.",
        "accomplishments": "Professional wins and quantifiable impacts.",
        "journal": "Immutable temporal evidence and reconciliation history.",
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

    kvault_dir = path / ".kvault"
    kvault_dir.mkdir(parents=True, exist_ok=True)
    ObservabilityLogger(kvault_dir / "logs.db")
    from kvault.core.migration import migrate

    migration = migrate(path, dry_run=False)
    if not _migration_success(migration):
        raise click.ClickException("Failed to initialize kvault 0.12 schema")

    click.echo(f"Initialized knowledge base at {path}")
    click.echo(f"Owner: {name}")
    click.echo()
    click.echo("Next: read AGENTS.md for agent workflow instructions.")
    click.echo("Use 'kvault --help' to see all commands.")


@cli.command("status")
@common_options
@click.pass_context
def status(ctx: click.Context, kb_root: Optional[Path], as_json: bool) -> None:
    """Show KB status: root, entity count, hierarchy, health."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    info = ops.get_kb_info(kb_root)
    health = {
        "root_summary_exists": (kb_root / "_summary.md").exists(),
        "kvault_dir_exists": (kb_root / ".kvault").exists(),
    }
    info["health"] = health
    info["version"] = __version__
    try:
        from kvault.core.migration import current_schema_version

        info["schema_version"] = current_schema_version(kb_root)
    except Exception:
        info["schema_version"] = None
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
@click.argument("path", default=".", required=False)
@click.option(
    "--depth", type=int, default=None, help="Levels to show below PATH (default: unlimited)"
)
@click.option(
    "--max-children",
    type=int,
    default=20,
    show_default=True,
    help="Children shown per node before eliding",
)
@click.option("--gist", is_flag=True, default=False, help="Append a one-line gist per node")
@common_options
@click.pass_context
def tree(
    ctx: click.Context,
    path: str,
    depth: Optional[int],
    max_children: int,
    gist: bool,
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Print an annotated outline of the KB node tree.

    Shows titles, child/descendant counts, and most-recent activity per
    node. Anything pruned by --depth or --max-children is called out with
    an explicit truncation marker.
    """
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)
    outline = ops.build_outline(
        kb_root, path=path, depth=depth, max_children=max_children, include_gist=gist
    )
    if outline is None:
        raise click.ClickException(f"Node not found: {path}")
    counts = ops.outline_counts(outline)
    if ctx.obj.get("as_json"):
        output_json(
            {
                "kg_root": str(kb_root),
                "path": outline["path"],
                "depth": depth,
                "max_children": max_children,
                "include_gist": gist,
                "total_nodes": counts["total_nodes"],
                "shown_nodes": counts["shown_nodes"],
                "outline": outline,
            }
        )
    else:
        click.echo(ops.render_outline_text(outline))


@cli.group("artifact")
def artifact_group() -> None:
    """Generate derivative artifacts from the KB."""


@artifact_group.command("daily")
@click.option(
    "--kb-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Knowledge base root (auto-detected if not specified)",
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
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def generate_daily(
    ctx: click.Context,
    kb_root: Optional[Path],
    artifact_date: Optional[str],
    force: bool,
    print_stdout: bool,
    as_json: bool,
) -> None:
    """Generate the daily artifact markdown file."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    kb_root = resolve_kb_root(ctx)

    try:
        parsed_date = parse_iso_date(artifact_date)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    result = generate_daily_artifact(kb_root, artifact_date=parsed_date, force=force)
    rel_path = result.path.relative_to(kb_root)
    if ctx.obj.get("as_json"):
        output_json(
            {
                "success": True,
                "kg_root": str(kb_root),
                "artifact_date": result.artifact_date.isoformat(),
                "path": str(result.path),
                "relative_path": str(rel_path),
                "written": result.written,
                "content": result.content,
            }
        )
        return

    status = "Generated" if result.written else "Reused existing"
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
