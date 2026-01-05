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
"""

import shutil
from pathlib import Path
from typing import Optional

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
@click.option("--batch-size", "-b", type=int, help="Emails per batch")
@click.option("--max-batches", "-m", type=int, help="Maximum batches to process")
@click.pass_context
def process(ctx, config: str, batch_size: int, max_batches: int):
    """Process source data into knowledge graph."""
    config_path = Path(config) if config else None
    cfg = _load_config_or_exit(config_path)

    click.echo(f"Project: {cfg.project_name}")
    click.echo(f"Data path: {cfg.data_path}")
    click.echo(f"KG path: {cfg.kg_path}")
    click.echo()

    # TODO: Implement processing pipeline
    click.echo("Processing pipeline not yet implemented.")
    click.echo("This will integrate with the hybrid processor from protec_knowledge_base.")


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.pass_context
def resume(ctx, config: str):
    """Resume interrupted processing."""
    config_path = Path(config) if config else None
    cfg = _load_config_or_exit(config_path)

    # TODO: Implement resume
    click.echo("Resume not yet implemented.")


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.pass_context
def review(ctx, config: str):
    """Review pending questions from processing."""
    config_path = Path(config) if config else None
    cfg = _load_config_or_exit(config_path)

    # TODO: Implement review
    click.echo("Review not yet implemented.")


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

    # TODO: Implement coverage
    click.echo("Coverage not yet implemented.")


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
