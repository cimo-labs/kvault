import json
import re
from dataclasses import dataclass
from datetime import date
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import click

from kvault import (
    EntityIndex,
    EntityResearcher,
    ObservabilityLogger,
    SimpleStorage,
    normalize_entity_id,
)
from kvault.cli.check import check_kb
from kvault.matching.domain import DEFAULT_GENERIC_DOMAINS


# -------------------------
# Helpers: extraction utils
# -------------------------


GENERIC_DOMAINS: Set[str] = set(DEFAULT_GENERIC_DOMAINS)


def _iter_files(root: Path, include_ext: Set[str]) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in include_ext:
            yield p


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _domain_to_org_name(domain: str) -> str:
    # Example: acme-corporation.com -> "Acme Corporation"
    base = domain.split(".")[0]
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", base).strip()
    words = [w for w in cleaned.split() if w]
    return " ".join(w.capitalize() for w in words) or domain


def _localpart_to_person_name(local: str) -> str:
    # john.smith -> John Smith; jdoe -> Jdoe
    local = local.replace("_", ".").replace("-", ".")
    parts = [p for p in local.split(".") if p]
    if len(parts) >= 2:
        return " ".join(p.capitalize() for p in parts[:3])
    return local.capitalize()


@dataclass
class ExtractedEntity:
    name: str
    entity_type: str  # "org" | "person"
    aliases: List[str]
    contacts: List[Dict]
    source_id: str


def extract_entities_from_text(text: str, source_id: str) -> List[ExtractedEntity]:
    entities: List[ExtractedEntity] = []

    emails = set(EMAIL_RE.findall(text))
    domains = set()
    for e in emails:
        local, domain = e.split("@", 1)
        if domain.lower() not in GENERIC_DOMAINS:
            domains.add(domain.lower())

        # Person candidate from email
        person_name = _localpart_to_person_name(local)
        entities.append(
            ExtractedEntity(
                name=person_name,
                entity_type="person",
                aliases=[e, person_name],
                contacts=[{"email": e}],
                source_id=source_id,
            )
        )

    # Org candidates from non-generic domains
    for d in domains:
        org_name = _domain_to_org_name(d)
        entities.append(
            ExtractedEntity(
                name=org_name,
                entity_type="org",
                aliases=[d, org_name],
                contacts=[],
                source_id=source_id,
            )
        )

    # Deduplicate by (name, type)
    dedup: Dict[Tuple[str, str], ExtractedEntity] = {}
    for ent in entities:
        key = (ent.name.lower(), ent.entity_type)
        if key not in dedup:
            dedup[key] = ent
        else:
            # Merge aliases/contacts
            existing = dedup[key]
            existing_aliases = set(existing.aliases)
            for a in ent.aliases:
                if a not in existing_aliases:
                    existing.aliases.append(a)
                    existing_aliases.add(a)
            existing.contacts.extend(ent.contacts)

    return list(dedup.values())


# -------------------------
# CLI
# -------------------------


@click.group()
def cli() -> None:
    """kvault CLI.

    Utilities for indexing, processing corpora, and viewing logs.
    """


cli.add_command(check_kb)


# ---- init command ----


def _load_template(name: str) -> str:
    """Load a template file from kvault.templates package."""
    return resource_files("kvault.templates").joinpath(name).read_text()


def _render(template: str, replacements: Dict[str, str]) -> str:
    """Replace {{PLACEHOLDER}} tokens in template."""
    result = template
    for key, value in replacements.items():
        result = result.replace("{{" + key + "}}", value)
    return result


@cli.command("init")
@click.argument("path", type=click.Path(path_type=Path), default=".")
@click.option("--name", default="My", help="Owner name for the knowledge base")
def init_kb(path: Path, name: str) -> None:
    """Initialize a new kvault knowledge base.

    Creates the directory structure, templates, and databases needed
    for a personal knowledge base with Claude Code integration.
    """
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

    # Load templates
    root_tpl = _load_template("root_summary.md")
    cat_tpl = _load_template("category_summary.md")
    journal_tpl = _load_template("journal_entry.md")
    claude_tpl = _load_template("CLAUDE.md")

    # Root summary
    (path / "_summary.md").write_text(_render(root_tpl, replacements))

    # CLAUDE.md
    (path / "CLAUDE.md").write_text(_render(claude_tpl, replacements))

    # Category directories
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

    # Journal
    journal_dir = path / "journal" / today.strftime("%Y-%m")
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / "log.md").write_text(_render(journal_tpl, replacements))

    # Databases
    kvault_dir = path / ".kvault"
    kvault_dir.mkdir(parents=True, exist_ok=True)
    EntityIndex(kvault_dir / "index.db")
    ObservabilityLogger(kvault_dir / "logs.db")

    click.echo(f"Initialized knowledge base at {path}")
    click.echo(f"Owner: {name}")
    click.echo()
    click.echo("Next steps:")
    click.echo()
    click.echo("1. Add MCP server to .claude/settings.json:")
    click.echo()
    click.echo("   {")
    click.echo('     "mcpServers": {')
    click.echo('       "kvault": {')
    click.echo('         "command": "kvault-mcp",')
    click.echo('         "env": {}')
    click.echo("       }")
    click.echo("     }")
    click.echo("   }")
    click.echo()
    click.echo("2. Add integrity hook to .claude/settings.json:")
    click.echo()
    click.echo("   {")
    click.echo('     "hooks": {')
    click.echo('       "UserPromptSubmit": [')
    click.echo("         {")
    click.echo('           "type": "command",')
    click.echo(f'           "command": "kvault check --kb-root {path}"')
    click.echo("         }")
    click.echo("       ]")
    click.echo("     }")
    click.echo("   }")
    click.echo()
    click.echo("3. Customize CLAUDE.md with your personal details")
    click.echo()
    click.echo("4. Start adding entities!")


# ---- index commands ----


@cli.group()
def index() -> None:
    """Index operations (rebuild, search)."""


@index.command("rebuild")
@click.option("--kg-root", type=click.Path(path_type=Path), required=True, help="Knowledge graph root")
@click.option(
    "--db",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to index.db (defaults to <kg-root>/.kvault/index.db)",
)
def index_rebuild(kg_root: Path, db: Optional[Path]) -> None:
    kg_root = kg_root.resolve()
    db = db or (kg_root / ".kvault" / "index.db")
    db.parent.mkdir(parents=True, exist_ok=True)

    index = EntityIndex(db)
    count = index.rebuild(kg_root)
    click.echo(f"Rebuilt index with {count} entities at {db}")


@index.command("search")
@click.option("--db", type=click.Path(path_type=Path), required=True, help="Path to index.db")
@click.option("--query", "query", required=True, help="Search query")
@click.option("--category", default=None, help="Optional category filter (e.g., people, orgs)")
@click.option("--limit", default=10, show_default=True, type=int)
def index_search(db: Path, query: str, category: Optional[str], limit: int) -> None:
    index = EntityIndex(db)
    results = index.search(query, category=category, limit=limit)
    for r in results:
        click.echo(json.dumps({
            "path": r.path,
            "name": r.name,
            "aliases": r.aliases,
            "category": r.category,
            "email_domains": r.email_domains,
            "last_updated": r.last_updated,
        }))


# ---- log commands ----


@cli.group()
def log() -> None:
    """Observability logs (summary)."""


@log.command("summary")
@click.option("--db", type=click.Path(path_type=Path), required=True, help="Path to logs.db")
@click.option("--session", default=None, help="Session ID (defaults to most recent session in this run)")
def log_summary(db: Path, session: Optional[str]) -> None:
    logger = ObservabilityLogger(db)
    summary = logger.get_session_summary(session)
    click.echo(json.dumps(summary, indent=2))


# ---- process command ----


@cli.command("process")
@click.option("--corpus", type=click.Path(path_type=Path), required=True, help="Input corpus root")
@click.option("--kg-root", type=click.Path(path_type=Path), required=True, help="Knowledge graph root")
@click.option(
    "--include-ext",
    default=".txt,.md",
    show_default=True,
    help="Comma-separated list of file extensions to include",
)
@click.option("--apply/--dry-run", default=False, help="Apply changes to KG or just print plan")
@click.option("--min-update-score", default=0.9, show_default=True, type=float, help="Min score for update action")
@click.option("--limit-files", default=0, show_default=True, type=int, help="Process at most N files (0 = no limit)")
def process_corpus(
    corpus: Path,
    kg_root: Path,
    include_ext: str,
    apply: bool,
    min_update_score: float,
    limit_files: int,
) -> None:
    """Process a text corpus into the knowledge graph (no web).

    Extract entities (people/orgs) via simple heuristics, research/dedup against the index,
    and create or update entities in the filesystem storage with observability logs.
    """
    corpus = corpus.resolve()
    kg_root = kg_root.resolve()
    kg_root.mkdir(parents=True, exist_ok=True)

    kvault_dir = kg_root / ".kvault"
    kvault_dir.mkdir(parents=True, exist_ok=True)
    index_db = kvault_dir / "index.db"
    logs_db = kvault_dir / "logs.db"

    storage = SimpleStorage(kg_root)
    index = EntityIndex(index_db)
    logger = ObservabilityLogger(logs_db)
    researcher = EntityResearcher(index)

    include_set = {ext.strip().lower() for ext in include_ext.split(",") if ext.strip()}
    files = list(_iter_files(corpus, include_set))
    if limit_files and len(files) > limit_files:
        files = files[:limit_files]

    logger.log_input([
        {"path": str(p), "size": p.stat().st_size} for p in files
    ], source="corpus")

    planned_ops: List[Dict] = []

    for f in files:
        text = f.read_text(errors="ignore")
        source_id = str(f.relative_to(corpus)) if f.is_relative_to(corpus) else str(f)
        entities = extract_entities_from_text(text, source_id)

        for ent in entities:
            # Research
            matches = researcher.research(
                ent.name,
                aliases=ent.aliases,
                email=(ent.contacts[0]["email"] if ent.contacts else None),
            )
            action, target_path, confidence = researcher.suggest_action(
                ent.name,
                aliases=ent.aliases,
                email=(ent.contacts[0]["email"] if ent.contacts else None),
            )

            logger.log_research(
                ent.name,
                ent.name.lower(),
                [m.__dict__ for m in matches],
                action,
            )
            logger.log_decide(
                ent.name,
                action,
                reasoning=(
                    "high-confidence match" if action == "update" and confidence >= min_update_score
                    else "no match" if action == "create" else "ambiguous"
                ),
                confidence=confidence,
            )

            # Determine category and entity path
            category = "people" if ent.entity_type == "person" else "orgs"
            entity_id = normalize_entity_id(ent.name)
            default_path = f"{category}/{entity_id}"
            final_path = target_path or default_path

            op = {
                "name": ent.name,
                "type": ent.entity_type,
                "action": action if confidence >= min_update_score or action == "create" else "review",
                "target_path": final_path,
                "confidence": confidence,
                "source": source_id,
            }
            planned_ops.append(op)

            if not apply:
                continue

            # Apply write/update
            aliases = list({*ent.aliases})
            meta_update = {
                "sources": [source_id],
                "aliases": aliases,
            }

            if action == "create" or (action == "update" and not target_path):
                summary = (
                    f"# {ent.name}\n\n"
                    f"Created from source: {source_id}\n\n"
                    + (f"Emails: {', '.join([c['email'] for c in ent.contacts])}\n" if ent.contacts else "")
                )
                storage.create_entity(final_path, meta_update, summary)
                logger.log_write(final_path, "create", "Created new entity")
            elif action == "update" and target_path:
                # Merge list fields manually to avoid overwriting
                existing = storage.read_meta(final_path) or {}
                merged_sources = list({*(existing.get("sources", []) or []), source_id})
                merged_aliases = list({*(existing.get("aliases", []) or []), *aliases})
                storage.update_entity(final_path, meta={"sources": merged_sources, "aliases": merged_aliases})
                logger.log_write(final_path, "update", "Updated entity metadata")
            else:
                # review - skip write, only log
                logger.log_decide(ent.name, "review", "Deferred for human review", confidence)
                continue

            # Update index and propagate
            index.add(final_path, ent.name, aliases=aliases, category=category)
            ancestors = storage.get_ancestors(final_path)
            logger.log_propagate(final_path, ancestors, reasoning="Updated parent summaries (placeholder)")

    # Output plan
    click.echo(json.dumps({
        "session": logger.session_id,
        "apply": apply,
        "planned_ops": planned_ops,
        "files_processed": len(files),
    }, indent=2))


# ---- orchestrate commands ----


@cli.group()
def orchestrate() -> None:
    """Headless orchestrator for agent-driven workflows.

    DEPRECATION NOTICE: The CLI orchestrator will be deprecated in favor of
    the MCP server. For better reliability and debugging, use the kvault MCP
    server with Claude Code instead:

        pip install 'kvault[mcp]'
        kvault-mcp  # Start MCP server

    See CLAUDE.md for MCP configuration instructions.
    """
    import warnings
    warnings.warn(
        "CLI orchestrator is deprecated. Use kvault MCP server instead. "
        "See 'kvault orchestrate --help' for details.",
        DeprecationWarning,
        stacklevel=2,
    )


@orchestrate.command("process")
@click.option("--kg-root", type=click.Path(path_type=Path), required=True, help="Knowledge graph root")
@click.option("--name", required=True, help="Entity name")
@click.option("--type", "entity_type", default="person", show_default=True, help="Entity type")
@click.option("--email", default=None, help="Email address for matching")
@click.option("--source", default="manual", show_default=True, help="Source identifier")
@click.option("--content", default="", help="Additional content/context")
@click.option(
    "--refactor-prob",
    default=0.1,
    show_default=True,
    type=float,
    help="Probability of triggering refactor step (0.0-1.0)",
)
def orchestrate_process(
    kg_root: Path,
    name: str,
    entity_type: str,
    email: Optional[str],
    source: str,
    content: str,
    refactor_prob: float,
) -> None:
    """Process a single entity through the 6-step workflow.

    Uses headless Claude Code to execute the mandatory workflow:
    RESEARCH → DECIDE → WRITE → PROPAGATE → LOG → REBUILD

    Plus stochastic refactoring with configurable probability.
    """
    import asyncio

    from kvault.orchestrator import HeadlessOrchestrator, OrchestratorConfig

    config = OrchestratorConfig(
        kg_root=kg_root.resolve(),
        refactor_probability=refactor_prob,
    )

    orchestrator = HeadlessOrchestrator(config)

    result = asyncio.run(
        orchestrator.process(
            {
                "name": name,
                "type": entity_type,
                "email": email,
                "source": source,
                "content": content,
            }
        )
    )

    click.echo(json.dumps(result, indent=2, default=str))


@orchestrate.command("batch")
@click.option("--kg-root", type=click.Path(path_type=Path), required=True, help="Knowledge graph root")
@click.option(
    "--input",
    "input_file",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="JSONL file with items to process",
)
@click.option(
    "--refactor-prob",
    default=0.1,
    show_default=True,
    type=float,
    help="Probability of triggering refactor step",
)
@click.option(
    "--limit",
    default=0,
    show_default=True,
    type=int,
    help="Maximum items to process (0 = no limit)",
)
def orchestrate_batch(
    kg_root: Path,
    input_file: Path,
    refactor_prob: float,
    limit: int,
) -> None:
    """Process a batch of entities from a JSONL file.

    Each line in the input file should be a JSON object with:
    - name: Entity name (required)
    - type: Entity type (default: "person")
    - email: Email address (optional)
    - source: Source identifier (default: "batch")
    - content: Additional content (optional)
    """
    import asyncio

    from kvault.orchestrator import HeadlessOrchestrator, OrchestratorConfig

    # Load items from JSONL
    items = []
    with open(input_file) as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    if limit and len(items) > limit:
        items = items[:limit]

    click.echo(f"Processing {len(items)} items from {input_file}...")

    config = OrchestratorConfig(
        kg_root=kg_root.resolve(),
        refactor_probability=refactor_prob,
    )

    orchestrator = HeadlessOrchestrator(config)
    results = asyncio.run(orchestrator.process_batch(items))

    # Output results
    success_count = sum(1 for r in results if r.get("workflow_complete"))
    error_count = sum(1 for r in results if r.get("error"))

    click.echo(f"\nCompleted: {success_count}/{len(items)} successful, {error_count} errors")

    for i, result in enumerate(results):
        item = items[i]
        status = "OK" if result.get("workflow_complete") else "ERROR"
        decision = result.get("decision", result.get("error", "unknown"))
        click.echo(f"  [{i+1}] {item.get('name', 'unknown')}: {status} - {decision}")


@orchestrate.command("ingest")
@click.option("--kg-root", type=click.Path(path_type=Path), required=True, help="Knowledge graph root")
@click.option("--content", required=True, help="Raw content to process (or '-' for stdin)")
@click.option("--source", default="manual", show_default=True, help="Source identifier")
@click.option("--hint", multiple=True, help="Optional hints (key=value format)")
@click.option(
    "--refactor-prob",
    default=0.1,
    show_default=True,
    type=float,
    help="Probability of triggering refactor step (0.0-1.0)",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Print step completions as they happen",
)
def orchestrate_ingest(
    kg_root: Path,
    content: str,
    source: str,
    hint: Tuple[str, ...],
    refactor_prob: float,
    verbose: bool,
) -> None:
    """Ingest raw content through hierarchy-based workflow.

    Unlike 'process' which takes structured entity info, 'ingest' accepts
    unstructured content and lets the agent reason about what changes
    the knowledge hierarchy needs.

    The agent can produce 0..N actions (create, update, delete, move)
    based on its analysis of the input.

    Examples:
        kvault orchestrate ingest --kg-root . --content "Coffee with Sarah from Anthropic"
        kvault orchestrate ingest --kg-root . --content - --source "email:12345" < email.txt
        kvault orchestrate ingest --kg-root . --content "Meeting notes" --hint "people=Mike,Bryan"
    """
    import asyncio
    import sys

    from kvault.orchestrator import HeadlessOrchestrator, OrchestratorConfig

    # Read from stdin if content is '-'
    if content == "-":
        content = sys.stdin.read()

    # Parse hints into dict
    hints: Optional[Dict[str, str]] = None
    if hint:
        hints = {}
        for h in hint:
            if "=" in h:
                key, value = h.split("=", 1)
                hints[key.strip()] = value.strip()

    config = OrchestratorConfig(
        kg_root=kg_root.resolve(),
        refactor_probability=refactor_prob,
        verbose=verbose,
    )

    orchestrator = HeadlessOrchestrator(config)

    result = asyncio.run(
        orchestrator.ingest(
            content=content,
            source=source,
            hints=hints,
        )
    )

    click.echo(json.dumps(result, indent=2, default=str))


@orchestrate.command("status")
@click.option("--kg-root", type=click.Path(path_type=Path), required=True, help="Knowledge graph root")
@click.option("--session", default=None, help="Session ID to inspect (defaults to latest)")
def orchestrate_status(kg_root: Path, session: Optional[str]) -> None:
    """Show status of recent orchestrator sessions."""
    kg_root = kg_root.resolve()
    logs_db = kg_root / ".kvault" / "logs.db"

    if not logs_db.exists():
        click.echo("No logs.db found. Run an orchestrator process first.")
        return

    logger = ObservabilityLogger(logs_db)
    summary = logger.get_session_summary(session)

    click.echo(f"Session: {summary['session_id']}")
    click.echo(f"Total logs: {summary['total_logs']}")
    click.echo(f"Errors: {summary['error_count']}")
    click.echo("\nPhase counts:")
    for phase, count in summary.get("phase_counts", {}).items():
        click.echo(f"  {phase}: {count}")
    click.echo("\nAction counts:")
    for action, count in summary.get("action_counts", {}).items():
        click.echo(f"  {action}: {count}")


if __name__ == "__main__":
    cli()

