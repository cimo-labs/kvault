#!/usr/bin/env python3
"""Migrate entities from _meta.json to YAML frontmatter.

This script converts entities from the legacy format (separate _meta.json files)
to the new format (YAML frontmatter in _summary.md).

Usage:
    # Dry run (preview changes)
    python scripts/migrate_to_frontmatter.py --kg-root /path/to/kb

    # Actually migrate
    python scripts/migrate_to_frontmatter.py --kg-root /path/to/kb --execute

    # Migrate specific category
    python scripts/migrate_to_frontmatter.py --kg-root /path/to/kb --category people --execute
"""

import argparse
import json
import sys
from pathlib import Path

# Add kvault to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from kvault.core.frontmatter import build_frontmatter


def migrate_entity(entity_dir: Path, dry_run: bool = True) -> bool:
    """Migrate a single entity from _meta.json to frontmatter.

    Args:
        entity_dir: Path to entity directory
        dry_run: If True, only preview changes without modifying files

    Returns:
        True if migration was performed (or would be in dry run)
    """
    meta_path = entity_dir / "_meta.json"
    summary_path = entity_dir / "_summary.md"

    # Skip if no _meta.json exists
    if not meta_path.exists():
        return False

    # Skip if no _summary.md exists
    if not summary_path.exists():
        print(f"  Warning: {entity_dir.name} has _meta.json but no _summary.md")
        return False

    # Read existing files
    try:
        meta = json.load(open(meta_path))
        summary = summary_path.read_text()
    except (json.JSONDecodeError, OSError) as e:
        print(f"  Error reading {entity_dir.name}: {e}")
        return False

    # Check if already has frontmatter
    if summary.startswith("---"):
        print(f"  Skipping {entity_dir.name}: already has frontmatter")
        return False

    # Convert meta to frontmatter format
    frontmatter = {
        "created": meta.get("created"),
        "updated": meta.get("last_updated"),
        "source": meta.get("sources", ["unknown"])[0] if meta.get("sources") else "unknown",
        "aliases": meta.get("aliases", []),
    }

    # Remove None values
    frontmatter = {k: v for k, v in frontmatter.items() if v is not None}

    # Build new content
    new_content = build_frontmatter(frontmatter) + summary

    if dry_run:
        print(f"  Would migrate: {entity_dir.name}")
        print(f"    Frontmatter: {frontmatter}")
        return True

    # Write new summary with frontmatter
    summary_path.write_text(new_content)

    # Delete _meta.json
    meta_path.unlink()

    print(f"  Migrated: {entity_dir.name}")
    return True


def migrate_kb(kg_root: Path, category: str = None, dry_run: bool = True) -> int:
    """Migrate all entities in a knowledge base.

    Args:
        kg_root: Root path of knowledge base
        category: Optional category to filter (e.g., "people")
        dry_run: If True, only preview changes

    Returns:
        Count of entities migrated
    """
    kg_root = Path(kg_root)
    count = 0

    if dry_run:
        print(f"DRY RUN - No files will be modified\n")

    # Find all _meta.json files
    for meta_path in kg_root.rglob("_meta.json"):
        # Skip hidden directories
        if any(part.startswith(".") for part in meta_path.parts):
            continue

        entity_dir = meta_path.parent
        rel_path = entity_dir.relative_to(kg_root)

        # Filter by category if specified
        if category:
            parts = rel_path.parts
            if not parts or parts[0] != category:
                continue

        if migrate_entity(entity_dir, dry_run):
            count += 1

    return count


def main():
    parser = argparse.ArgumentParser(
        description="Migrate entities from _meta.json to YAML frontmatter"
    )
    parser.add_argument(
        "--kg-root",
        type=Path,
        required=True,
        help="Root path of knowledge base",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Only migrate entities in this category (e.g., 'people')",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform migration (default is dry run)",
    )

    args = parser.parse_args()

    if not args.kg_root.exists():
        print(f"Error: {args.kg_root} does not exist")
        sys.exit(1)

    dry_run = not args.execute

    print(f"Migrating entities in: {args.kg_root}")
    if args.category:
        print(f"Category filter: {args.category}")
    print()

    count = migrate_kb(args.kg_root, args.category, dry_run)

    print()
    if dry_run:
        print(f"Would migrate {count} entities. Run with --execute to apply.")
    else:
        print(f"Migrated {count} entities.")


if __name__ == "__main__":
    main()
