"""kvault CLI — CLI-first knowledge base for AI agents."""

import sqlite3
from datetime import date
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Dict, Optional

import click

from kvault.cli._helpers import apply_common_options, common_options, output_json, resolve_kb_root
from kvault.cli.check import check_kb
from kvault.cli.entity import read_entity, write_entity, list_entities, delete_entity, move_entity
from kvault.cli.events import capture, events_group
from kvault.cli.journal import write_journal
from kvault.cli.search import search_nodes
from kvault.cli.summary import read_summary, write_summary, update_summaries, ancestors
from kvault.cli.validate import validate_kb
from kvault.core.daily_artifacts import generate_daily_artifact, parse_iso_date
from kvault.core.observability import ObservabilityLogger
from kvault.core import operations as ops
from kvault._version import __version__

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
@click.version_option(version=__version__, prog_name="kvault", message="%(prog)s %(version)s")
@click.option(
    "--kb-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Knowledge base root (auto-detected if not specified)",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("-q", "--quiet", is_flag=True, help="Receipt and warnings only")
@click.option("--explain", is_flag=True, help="Add reasoning and next steps")
@click.option("--trace", is_flag=True, help="Add cost/mechanics detail (implies --explain)")
@click.option("--strict", is_flag=True, help="Exit 3 if any warning-class note was emitted")
@click.pass_context
def cli(
    ctx: click.Context,
    kb_root: Optional[Path],
    as_json: bool,
    quiet: bool,
    explain: bool,
    trace: bool,
    strict: bool,
) -> None:
    """kvault — personal knowledge base for AI agents."""
    ctx.ensure_object(dict)
    ctx.obj["kb_root"] = kb_root
    ctx.obj["as_json"] = as_json
    ctx.obj["quiet"] = quiet
    ctx.obj["explain"] = explain
    ctx.obj["trace"] = trace
    ctx.obj["strict"] = strict


# Register commands
cli.add_command(check_kb)
cli.add_command(read_entity)
cli.add_command(write_entity)
cli.add_command(list_entities, "list")
cli.add_command(search_nodes)
cli.add_command(delete_entity, "delete")
cli.add_command(move_entity, "move")
cli.add_command(read_summary)
cli.add_command(write_summary)
cli.add_command(update_summaries)
cli.add_command(ancestors)
cli.add_command(write_journal)
cli.add_command(validate_kb, "validate")
cli.add_command(capture)
cli.add_command(events_group)


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
    # Ship the ignore rules with the KB. Both pre-existing live KBs had to
    # hand-roll this after accidentally staging a growing binary; `*.db*` also
    # covers sqlite sidecar files (-wal/-shm) should a future tool create them.
    (kvault_dir / ".gitignore").write_text("# kvault runtime state — never commit\n*.db*\nlock/\n")

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
        if counts["shown_nodes"] < counts["total_nodes"]:
            click.echo(
                f"(showing {counts['shown_nodes']} of {counts['total_nodes']} nodes — "
                "raise --depth or --max-children for the rest)"
            )


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


def _resolve_log_db(ctx: click.Context, db_path: Optional[Path]) -> Path:
    """Resolve the log DB path: explicit --db wins, else <kb-root>/.kvault/logs.db.

    ``log summary`` used to default to the CWD-RELATIVE ``.kvault/logs.db``
    and silently ignore ``--kb-root`` — the only KB-scoped command that did.
    An explicit ``--db`` that doesn't exist is a hard error; a missing default
    DB just means "nothing logged yet".
    """
    if db_path is not None:
        db_path = db_path.resolve()
        if not db_path.exists():
            raise click.ClickException(f"Log database does not exist: {db_path}")
        return db_path
    kb_root = resolve_kb_root(ctx)
    return kb_root / ".kvault" / "logs.db"


@log_group.command("summary")
@click.option(
    "--db",
    "db_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to the logs database (default: <kb-root>/.kvault/logs.db)",
)
@click.option(
    "--session-id",
    default=None,
    help="Optional legacy session id. Defaults to the latest session in the database.",
)
@common_options
@click.pass_context
def log_summary(
    ctx: click.Context,
    db_path: Optional[Path],
    session_id: Optional[str],
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Show operation counts and legacy phase-log stats for this KB."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    resolved_db = _resolve_log_db(ctx, db_path)

    from kvault.core.oplog import OpLog

    ops_summary = OpLog(kg_root=None, db_path=resolved_db).summary()
    empty_legacy: Dict[str, object] = {
        "session_id": None,
        "phase_counts": {},
        "action_counts": {},
        "error_count": 0,
        "total_logs": 0,
    }
    if resolved_db.exists():
        try:
            legacy = ObservabilityLogger(resolved_db).get_session_summary(session_id=session_id)
        except sqlite3.Error:
            # A corrupt/garbage logs.db must degrade like every other logging
            # failure — zeros, not a raw traceback. OpLog.summary() above
            # already returned zeros for the same reason.
            legacy = dict(empty_legacy)
    else:
        legacy = dict(empty_legacy)

    payload = {
        "db": str(resolved_db),
        "ops": ops_summary,
        # Legacy phase-log stats keep their original top-level keys.
        **legacy,
    }
    if ctx.obj.get("as_json"):
        output_json(payload)
        return

    click.echo(f"Log database: {resolved_db}")
    click.echo(
        f"Operations: {ops_summary['total_ops']} "
        f"({ops_summary['sessions']} sessions, {ops_summary['partial_count']} partial)"
    )
    for op_name, count in sorted(ops_summary["op_counts"].items()):
        click.echo(f"  - {op_name}: {count}")
    if legacy["total_logs"]:
        click.echo(
            f"Legacy phase logs: {legacy['total_logs']} rows, "
            f"{legacy['error_count']} errors (latest session: {legacy['session_id']})"
        )


@log_group.command("tail")
@click.option("--limit", default=20, show_default=True, type=int, help="Rows to show")
@click.option("--session", default=None, help="Filter to one session id")
@common_options
@click.pass_context
def log_tail(
    ctx: click.Context,
    limit: int,
    session: Optional[str],
    kb_root: Optional[Path],
    as_json: bool,
) -> None:
    """Show recent KB operations and the decisions they reported."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    root = resolve_kb_root(ctx)

    from kvault.core.oplog import OpLog

    rows = OpLog(root).tail(limit=limit, session=session)
    if ctx.obj.get("as_json"):
        output_json({"count": len(rows), "ops": rows})
        return
    if not rows:
        click.echo("No operations logged.")
        return
    for row in reversed(rows):  # oldest first, like tail(1)
        flags = ""
        if row.get("partial"):
            flags += " [partial]"
        if row.get("changed") is False:
            flags += " [unchanged]"
        codes = ",".join(n.get("code", "?") for n in row.get("notes") or [])
        note_part = f"  notes: {codes}" if codes else ""
        click.echo(
            f"{row['ts']}  {row['surface']}:{row['op']:<16} "
            f"{row.get('path') or '-'}{flags}{note_part}"
        )


if __name__ == "__main__":
    cli()
