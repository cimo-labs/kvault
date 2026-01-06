"""
Command-line interface for kgraph.

Usage:
    kgraph init <project-name>    Initialize a new project
    kgraph process                Process data into knowledge graph
    kgraph resume                 Resume interrupted processing
    kgraph review                 Review pending questions
    kgraph validate               Validate knowledge graph
    kgraph coverage               Show processing coverage
    kgraph tree                   Display knowledge graph structure
    kgraph status                 Show pipeline status
"""

import json
import shutil
from pathlib import Path
from typing import List, Optional

import click
from pydantic import ValidationError

from kgraph import __version__
from kgraph.core.config import load_config, KGraphConfig


def _load_config_or_exit(config_path: Optional[Path]) -> KGraphConfig:
    """Load config with user-friendly Pydantic validation errors."""
    try:
        return load_config(config_path)
    except ValidationError as e:
        click.echo("Configuration errors:", err=True)
        for error in e.errors():
            loc = " -> ".join(str(x) for x in error["loc"])
            click.echo(f"  {loc}: {error['msg']}", err=True)
        raise SystemExit(1)


@click.group()
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx):
    """kgraph - Config-driven knowledge graph framework."""
    ctx.ensure_object(dict)


@cli.command()
@click.argument("project_name")
@click.option("--template", default="default", help="Template to use (default, email, crm)")
def init(project_name: str, template: str):
    """Initialize a new kgraph project."""
    project_path = Path(project_name)

    if project_path.exists():
        click.echo(f"Error: Directory '{project_name}' already exists", err=True)
        raise SystemExit(1)

    # Create project structure
    project_path.mkdir(parents=True)
    (project_path / "data").mkdir()
    (project_path / "knowledge_graph").mkdir()
    (project_path / "prompts").mkdir()
    (project_path / "entity_types").mkdir()

    # Copy templates
    templates_dir = Path(__file__).parent.parent / "templates"

    # Create config.yaml
    config_template = templates_dir / "config.yaml"
    if config_template.exists():
        config_content = config_template.read_text()
        config_content = config_content.replace("{{PROJECT_NAME}}", project_name)
        (project_path / "kgraph.yaml").write_text(config_content)
    else:
        # Write default config
        default_config = f"""project:
  name: "{project_name}"
  data_path: "./data"
  kg_path: "./knowledge_graph"

entity_types:
  entity:
    directory: "entities"
    tier_field: "tier"

tiers:
  high:
    criteria:
      priority_min: 8
    storage_type: directory
  medium:
    criteria:
      priority_min: 4
      priority_max: 8
    storage_type: directory
  low:
    criteria:
      priority_max: 4
    storage_type: jsonl

processing:
  batch_size: 500
  objective_interval: 5

confidence:
  auto_merge: 0.95
  auto_update: 0.90
  auto_create: 0.50
  llm_required: [0.50, 0.95]

matching:
  strategies:
    - alias
    - fuzzy_name
    - email_domain
  fuzzy_threshold: 0.85

agent:
  provider: claude
"""
        (project_path / "kgraph.yaml").write_text(default_config)

    # Create .gitignore
    gitignore = """# kgraph
data/
*.db
*.db-journal

# Python
__pycache__/
*.py[cod]
.venv/
venv/

# Editor
.vscode/
.idea/
*.swp
"""
    (project_path / ".gitignore").write_text(gitignore)

    # Create README
    readme = f"""# {project_name}

Knowledge graph built with [kgraph](https://github.com/eddiel/kgraph).

## Quick Start

```bash
# Process data
kgraph process

# Review pending questions
kgraph review

# View knowledge graph
kgraph tree
```

## Configuration

Edit `kgraph.yaml` to customize:
- Entity types and fields
- Tier definitions
- Matching strategies
- LLM provider
"""
    (project_path / "README.md").write_text(readme)

    click.echo(f"Created project: {project_name}/")
    click.echo(f"  kgraph.yaml      - Configuration")
    click.echo(f"  data/            - Source data")
    click.echo(f"  knowledge_graph/ - Output knowledge graph")
    click.echo(f"  prompts/         - LLM prompts")
    click.echo(f"  entity_types/    - Entity type schemas")
    click.echo()
    click.echo(f"Next steps:")
    click.echo(f"  cd {project_name}")
    click.echo(f"  # Add your data to data/")
    click.echo(f"  # Edit kgraph.yaml to configure entity types")
    click.echo(f"  kgraph process")


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option("--batch-size", "-b", type=int, default=10, help="Items per extraction batch")
@click.option("--max-batches", "-m", type=int, help="Maximum batches to process")
@click.option("--auto-apply", is_flag=True, help="Automatically apply ready operations")
@click.option("--no-llm", is_flag=True, help="Disable LLM for ambiguous decisions")
@click.option("--input", "-i", "input_file", type=click.Path(exists=True), help="Input file (JSON/JSONL)")
@click.pass_context
def process(ctx, config: str, batch_size: int, max_batches: int, auto_apply: bool, no_llm: bool, input_file: str):
    """Process source data into knowledge graph."""
    config_path = Path(config) if config else None
    cfg = _load_config_or_exit(config_path)

    click.echo(f"Project: {cfg.project_name}")
    click.echo(f"Data path: {cfg.data_path}")
    click.echo(f"KG path: {cfg.kg_path}")
    click.echo()

    # Load input data
    items: List[dict] = []

    if input_file:
        input_path = Path(input_file)
        if input_path.suffix == ".jsonl":
            with open(input_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        items.append(json.loads(line))
        elif input_path.suffix == ".json":
            with open(input_path) as f:
                data = json.load(f)
                if isinstance(data, list):
                    items = data
                else:
                    items = [data]
        else:
            click.echo(f"Error: Unsupported input format: {input_path.suffix}", err=True)
            raise SystemExit(1)

        click.echo(f"Loaded {len(items)} items from {input_file}")
    else:
        # Look for data files in data_path
        data_path = cfg.data_path
        if data_path.exists():
            for data_file in data_path.glob("*.jsonl"):
                with open(data_file) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            items.append(json.loads(line))
            for data_file in data_path.glob("*.json"):
                with open(data_file) as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        items.extend(data)
                    else:
                        items.append(data)

        if not items:
            click.echo("No input data found. Use --input or add files to data/", err=True)
            raise SystemExit(1)

        click.echo(f"Loaded {len(items)} items from {data_path}")

    # Apply max batches limit
    if max_batches:
        max_items = max_batches * batch_size
        if len(items) > max_items:
            items = items[:max_items]
            click.echo(f"Limited to {len(items)} items ({max_batches} batches)")

    # Initialize orchestrator
    from kgraph.pipeline import Orchestrator

    orchestrator = Orchestrator(cfg, cfg.kg_path)

    # Process
    click.echo()
    click.echo("Processing...")

    result = orchestrator.process(
        items=items,
        source_name=input_file,
        auto_apply=auto_apply,
        use_llm=not no_llm,
        batch_size=batch_size,
    )

    # Display results
    click.echo()
    click.echo("Results:")
    click.echo(f"  Items processed:    {result.items_processed}")
    click.echo(f"  Entities extracted: {result.entities_extracted}")
    click.echo(f"  Operations staged:  {result.operations_staged}")
    click.echo(f"  Operations applied: {result.operations_applied}")
    click.echo(f"  Operations failed:  {result.operations_failed}")
    click.echo(f"  Questions pending:  {result.questions_created}")

    if result.errors:
        click.echo()
        click.echo("Errors:")
        for error in result.errors[:5]:
            click.echo(f"  - {error}")
        if len(result.errors) > 5:
            click.echo(f"  ... and {len(result.errors) - 5} more")

    if result.questions_created > 0:
        click.echo()
        click.echo(f"Run 'kgraph review' to answer {result.questions_created} pending questions.")


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option("--session", "-s", "session_id", help="Session ID to resume")
@click.option("--auto-apply", is_flag=True, help="Automatically apply ready operations")
@click.pass_context
def resume(ctx, config: str, session_id: str, auto_apply: bool):
    """Resume interrupted processing."""
    config_path = Path(config) if config else None
    cfg = _load_config_or_exit(config_path)

    from kgraph.pipeline import Orchestrator, SessionManager

    orchestrator = Orchestrator(cfg, cfg.kg_path)

    # If no session specified, show resumable sessions
    if not session_id:
        sessions = orchestrator.session_manager.get_resumable_sessions()

        if not sessions:
            click.echo("No resumable sessions found.")
            return

        click.echo("Resumable sessions:")
        for s in sessions:
            click.echo(f"  {s['session_id']}")
            click.echo(f"    State: {s['state']}")
            click.echo(f"    Created: {s['created_at']}")
            click.echo(f"    Entities: {s['entities_extracted']}")
            click.echo()

        click.echo("Use --session <id> to resume a specific session.")
        return

    # Resume session
    click.echo(f"Resuming session: {session_id}")

    result = orchestrator.resume(session_id, auto_apply=auto_apply)

    if not result:
        click.echo(f"Session not found: {session_id}", err=True)
        raise SystemExit(1)

    # Display results
    click.echo()
    click.echo("Results:")
    click.echo(f"  Operations applied: {result.operations_applied}")
    click.echo(f"  Operations failed:  {result.operations_failed}")

    if result.errors:
        click.echo()
        click.echo("Errors:")
        for error in result.errors[:5]:
            click.echo(f"  - {error}")


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option("--batch", "-b", "batch_id", help="Filter by batch ID")
@click.option("--auto", is_flag=True, help="Accept suggested answers automatically")
@click.pass_context
def review(ctx, config: str, batch_id: str, auto: bool):
    """Review pending questions from processing."""
    config_path = Path(config) if config else None
    cfg = _load_config_or_exit(config_path)

    from kgraph.pipeline import Orchestrator

    orchestrator = Orchestrator(cfg, cfg.kg_path)

    # Count pending questions
    pending_count = orchestrator.question_queue.count_pending(batch_id)

    if pending_count == 0:
        click.echo("No pending questions.")
        return

    click.echo(f"Pending questions: {pending_count}")
    click.echo()

    answered = 0
    skipped = 0

    while True:
        # Get next question
        question_data = orchestrator.review_next(batch_id)

        if not question_data:
            break

        # Display question
        click.echo("-" * 60)
        click.echo(f"Question {answered + skipped + 1}/{pending_count}")
        click.echo(f"Type: {question_data['type']}")
        click.echo(f"Confidence: {question_data['confidence']:.2f}")
        click.echo()
        click.echo(question_data['text'])
        click.echo()

        if question_data.get('suggested'):
            click.echo(f"Suggested: {question_data['suggested']}")
            click.echo()

        if auto and question_data.get('suggested'):
            # Auto-accept suggestion
            answer = question_data['suggested']
            click.echo(f"Auto-accepting: {answer}")
        else:
            # Prompt for answer
            answer = click.prompt(
                "Your answer (or 'skip' to skip, 'quit' to exit)",
                default=question_data.get('suggested', ''),
            )

        if answer.lower() == 'quit':
            break

        if answer.lower() == 'skip':
            orchestrator.question_queue.skip(question_data['question_id'])
            skipped += 1
            continue

        # Submit answer
        success = orchestrator.answer_question(question_data['question_id'], answer)

        if success:
            answered += 1
            click.echo(f"Recorded answer: {answer}")
        else:
            click.echo("Failed to record answer", err=True)

        click.echo()

    click.echo("-" * 60)
    click.echo(f"Answered: {answered}")
    click.echo(f"Skipped: {skipped}")
    click.echo(f"Remaining: {orchestrator.question_queue.count_pending(batch_id)}")

    if answered > 0:
        click.echo()
        click.echo("Run 'kgraph resume --auto-apply' to apply the approved operations.")


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option("--strict", is_flag=True, help="Strict validation mode")
@click.pass_context
def validate(ctx, config: str, strict: bool):
    """Validate knowledge graph structure."""
    config_path = Path(config) if config else None
    cfg = _load_config_or_exit(config_path)

    if not cfg.kg_path.exists():
        click.echo(f"Error: Knowledge graph not found at {cfg.kg_path}", err=True)
        raise SystemExit(1)

    from kgraph.core.storage import FilesystemStorage

    storage = FilesystemStorage(cfg.kg_path, cfg)

    errors = []
    warnings = []

    # Check each entity type
    for et_name, et_config in cfg.entity_types.items():
        entities = storage.list_entities(et_name)
        click.echo(f"Checking {et_name}: {len(entities)} entities")

        for entity in entities:
            entity_data = storage.read_entity(et_name, entity["id"], entity.get("tier"))
            if entity_data:
                # Check required fields
                for field in et_config.required_fields:
                    if field not in entity_data:
                        errors.append(f"{et_name}/{entity['id']}: missing required field '{field}'")

    if errors:
        click.echo()
        click.echo("Errors:")
        for error in errors:
            click.echo(f"  - {error}")
        raise SystemExit(1)

    if warnings:
        click.echo()
        click.echo("Warnings:")
        for warning in warnings:
            click.echo(f"  - {warning}")

    click.echo()
    click.echo("Validation passed!")


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.pass_context
def coverage(ctx, config: str):
    """Show processing coverage statistics."""
    config_path = Path(config) if config else None
    cfg = _load_config_or_exit(config_path)

    from kgraph.pipeline import Orchestrator

    orchestrator = Orchestrator(cfg, cfg.kg_path)
    status = orchestrator.get_status()

    click.echo("Pipeline Status")
    click.echo("=" * 40)
    click.echo()

    # Session info
    session = status.get("session", {})
    if session.get("id"):
        click.echo("Current Session:")
        click.echo(f"  ID:       {session['id']}")
        click.echo(f"  State:    {session['state']}")
        click.echo(f"  Batches:  {session['batches']}")
        click.echo(f"  Entities: {session['entities_extracted']}")
    else:
        click.echo("No active session")
    click.echo()

    # Staging stats
    staging = status.get("staging", {})
    if staging:
        click.echo("Staging Database:")
        for stat, count in staging.items():
            click.echo(f"  {stat}: {count}")
    else:
        click.echo("Staging: empty")
    click.echo()

    # Questions stats
    questions = status.get("questions", {})
    if questions:
        click.echo("Question Queue:")
        for stat, count in questions.items():
            click.echo(f"  {stat}: {count}")
    else:
        click.echo("Question Queue: empty")
    click.echo()

    # Index size
    click.echo(f"Research Index: {status.get('index_size', 0)} entities")


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.pass_context
def status(ctx, config: str):
    """Show pipeline status."""
    # Delegate to coverage for now (same functionality)
    ctx.invoke(coverage, config=config)


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option("--depth", "-d", type=int, default=3, help="Maximum depth to display")
@click.pass_context
def tree(ctx, config: str, depth: int):
    """Display knowledge graph structure."""
    config_path = Path(config) if config else None
    cfg = _load_config_or_exit(config_path)

    if not cfg.kg_path.exists():
        click.echo(f"Error: Knowledge graph not found at {cfg.kg_path}", err=True)
        raise SystemExit(1)

    from kgraph.core.storage import FilesystemStorage

    storage = FilesystemStorage(cfg.kg_path, cfg)

    click.echo(f"{cfg.project_name}")
    click.echo(f"{'=' * len(cfg.project_name)}")
    click.echo()

    for et_name, et_config in cfg.entity_types.items():
        click.echo(f"{et_config.directory}/")

        if cfg.tiers:
            for tier_name in cfg.tiers:
                entities = storage.list_entities(et_name, tier_name)
                click.echo(f"  {tier_name}/ ({len(entities)} entities)")

                if depth > 1:
                    for entity in entities[:5]:  # Show first 5
                        click.echo(f"    - {entity['name']}")
                    if len(entities) > 5:
                        click.echo(f"    ... and {len(entities) - 5} more")
        else:
            entities = storage.list_entities(et_name)
            click.echo(f"  ({len(entities)} entities)")

        click.echo()


if __name__ == "__main__":
    cli()
