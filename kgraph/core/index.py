"""
EntityIndex - SQLite-backed entity index with full-text search.

Provides fast lookup for entity deduplication and research.
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class IndexEntry:
    """An entry in the entity index."""

    path: str  # "people/collaborators/alice_smith"
    name: str  # "Alice Smith"
    aliases: List[str] = field(default_factory=list)  # ["Alice", "alice@anthropic.com"]
    category: str = ""  # "people"
    email_domains: List[str] = field(default_factory=list)  # ["anthropic.com"]
    last_updated: str = ""


class EntityIndex:
    """SQLite-backed entity index with full-text search."""

    def __init__(self, db_path: Path):
        """Initialize index with database path.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                -- Main entities table
                CREATE TABLE IF NOT EXISTS entities (
                    path TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    aliases TEXT,
                    category TEXT,
                    email_domains TEXT,
                    last_updated TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_category ON entities(category);
                CREATE INDEX IF NOT EXISTS idx_name ON entities(name);

                -- Full-text search table
                CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
                    name, aliases,
                    content='entities',
                    content_rowid='rowid'
                );

                -- Triggers to keep FTS in sync
                CREATE TRIGGER IF NOT EXISTS entities_ai AFTER INSERT ON entities BEGIN
                    INSERT INTO entities_fts(rowid, name, aliases)
                    VALUES (new.rowid, new.name, new.aliases);
                END;

                CREATE TRIGGER IF NOT EXISTS entities_ad AFTER DELETE ON entities BEGIN
                    INSERT INTO entities_fts(entities_fts, rowid, name, aliases)
                    VALUES ('delete', old.rowid, old.name, old.aliases);
                END;

                CREATE TRIGGER IF NOT EXISTS entities_au AFTER UPDATE ON entities BEGIN
                    INSERT INTO entities_fts(entities_fts, rowid, name, aliases)
                    VALUES ('delete', old.rowid, old.name, old.aliases);
                    INSERT INTO entities_fts(rowid, name, aliases)
                    VALUES (new.rowid, new.name, new.aliases);
                END;
            """)

    def _extract_email_domains(self, aliases: List[str]) -> List[str]:
        """Extract email domains from aliases."""
        domains = []
        for alias in aliases:
            if "@" in alias:
                domain = alias.split("@")[1].lower()
                if domain not in domains:
                    domains.append(domain)
        return domains

    def add(
        self,
        path: str,
        name: str,
        aliases: List[str] = None,
        category: str = "",
    ) -> None:
        """Add or update entity in index.

        Args:
            path: Entity path (e.g., "people/collaborators/alice_smith")
            name: Display name
            aliases: List of aliases (names, emails, etc.)
            category: Top-level category (e.g., "people", "projects")
        """
        aliases = aliases or []
        email_domains = self._extract_email_domains(aliases)
        last_updated = datetime.now().strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO entities (path, name, aliases, category, email_domains, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    path,
                    name,
                    json.dumps(aliases),
                    category,
                    json.dumps(email_domains),
                    last_updated,
                ),
            )

    def remove(self, path: str) -> None:
        """Remove entity from index.

        Args:
            path: Entity path to remove
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM entities WHERE path = ?", (path,))

    def get(self, path: str) -> Optional[IndexEntry]:
        """Get entity by path.

        Args:
            path: Entity path

        Returns:
            IndexEntry if found, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM entities WHERE path = ?", (path,)
            ).fetchone()

            if not row:
                return None

            return IndexEntry(
                path=row["path"],
                name=row["name"],
                aliases=json.loads(row["aliases"] or "[]"),
                category=row["category"] or "",
                email_domains=json.loads(row["email_domains"] or "[]"),
                last_updated=row["last_updated"] or "",
            )

    def _escape_fts_query(self, query: str) -> str:
        """Escape special characters for FTS5 query.

        FTS5 treats certain characters as special operators.
        Wrap in quotes to treat as literal string.
        """
        # Escape double quotes within the query
        escaped = query.replace('"', '""')
        return f'"{escaped}"'

    def search(
        self, query: str, category: str = None, limit: int = 10
    ) -> List[IndexEntry]:
        """Full-text search across names and aliases.

        Args:
            query: Search query
            category: Optional category filter
            limit: Maximum results to return

        Returns:
            List of matching IndexEntry objects
        """
        # Escape special FTS characters
        fts_query = self._escape_fts_query(query)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Build FTS query
            if category:
                rows = conn.execute(
                    """
                    SELECT e.* FROM entities e
                    JOIN entities_fts fts ON e.rowid = fts.rowid
                    WHERE entities_fts MATCH ? AND e.category = ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT e.* FROM entities e
                    JOIN entities_fts fts ON e.rowid = fts.rowid
                    WHERE entities_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()

            return [
                IndexEntry(
                    path=row["path"],
                    name=row["name"],
                    aliases=json.loads(row["aliases"] or "[]"),
                    category=row["category"] or "",
                    email_domains=json.loads(row["email_domains"] or "[]"),
                    last_updated=row["last_updated"] or "",
                )
                for row in rows
            ]

    def find_by_alias(self, alias: str) -> Optional[IndexEntry]:
        """Exact alias lookup (case-insensitive).

        Args:
            alias: Alias to search for

        Returns:
            IndexEntry if found, None otherwise
        """
        alias_lower = alias.lower()

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM entities").fetchall()

            for row in rows:
                aliases = json.loads(row["aliases"] or "[]")
                if any(a.lower() == alias_lower for a in aliases):
                    return IndexEntry(
                        path=row["path"],
                        name=row["name"],
                        aliases=aliases,
                        category=row["category"] or "",
                        email_domains=json.loads(row["email_domains"] or "[]"),
                        last_updated=row["last_updated"] or "",
                    )

            return None

    def find_by_email_domain(self, domain: str) -> List[IndexEntry]:
        """Find entities with matching email domain.

        Args:
            domain: Email domain to search for (e.g., "anthropic.com")

        Returns:
            List of matching IndexEntry objects
        """
        domain_lower = domain.lower()
        results = []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM entities").fetchall()

            for row in rows:
                email_domains = json.loads(row["email_domains"] or "[]")
                if domain_lower in [d.lower() for d in email_domains]:
                    results.append(
                        IndexEntry(
                            path=row["path"],
                            name=row["name"],
                            aliases=json.loads(row["aliases"] or "[]"),
                            category=row["category"] or "",
                            email_domains=email_domains,
                            last_updated=row["last_updated"] or "",
                        )
                    )

        return results

    def list_all(self, category: str = None) -> List[IndexEntry]:
        """List all entities, optionally filtered by category.

        Args:
            category: Optional category filter

        Returns:
            List of all IndexEntry objects
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if category:
                rows = conn.execute(
                    "SELECT * FROM entities WHERE category = ? ORDER BY name",
                    (category,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM entities ORDER BY name"
                ).fetchall()

            return [
                IndexEntry(
                    path=row["path"],
                    name=row["name"],
                    aliases=json.loads(row["aliases"] or "[]"),
                    category=row["category"] or "",
                    email_domains=json.loads(row["email_domains"] or "[]"),
                    last_updated=row["last_updated"] or "",
                )
                for row in rows
            ]

    def rebuild(self, kg_root: Path) -> int:
        """Rebuild index from filesystem.

        Scans kg_root for entities and indexes them. Supports both:
        - YAML frontmatter in _summary.md (preferred)
        - Separate _meta.json files (legacy fallback)

        Args:
            kg_root: Root path of knowledge graph

        Returns:
            Count of entities indexed
        """
        from kgraph.core.frontmatter import parse_frontmatter

        kg_root = Path(kg_root)
        count = 0

        # Clear existing index
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM entities")

        # Scan for _summary.md files (entity directories have these)
        for summary_path in kg_root.rglob("_summary.md"):
            # Skip hidden directories
            if any(part.startswith(".") for part in summary_path.parts):
                continue

            entity_dir = summary_path.parent
            rel_path = entity_dir.relative_to(kg_root)

            # Skip root and category-level summaries (need at least 2 parts: category/entity)
            if len(rel_path.parts) < 2:
                continue

            meta = None

            # Try frontmatter first (preferred format)
            try:
                content = summary_path.read_text()
                meta, _ = parse_frontmatter(content)
            except OSError:
                pass

            # Fall back to _meta.json (legacy format)
            if not meta:
                meta_path = entity_dir / "_meta.json"
                if meta_path.exists():
                    try:
                        with open(meta_path) as f:
                            meta = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        pass

            # Skip if no metadata found
            if not meta:
                continue

            # Extract category (top-level directory)
            parts = rel_path.parts
            category = parts[0] if parts else ""

            # Get name and aliases
            name = meta.get("name", meta.get("topic", entity_dir.name))
            aliases = list(meta.get("aliases", []))

            # Add phone/email to aliases for matching
            if meta.get("phone"):
                phone = meta["phone"]
                if phone not in aliases:
                    aliases.append(phone)
            if meta.get("email"):
                email = meta["email"]
                if email not in aliases:
                    aliases.append(email)

            self.add(
                path=str(rel_path),
                name=name,
                aliases=aliases,
                category=category,
            )
            count += 1

        return count

    def count(self, category: str = None) -> int:
        """Count entities in index.

        Args:
            category: Optional category filter

        Returns:
            Number of entities
        """
        with sqlite3.connect(self.db_path) as conn:
            if category:
                result = conn.execute(
                    "SELECT COUNT(*) FROM entities WHERE category = ?", (category,)
                ).fetchone()
            else:
                result = conn.execute("SELECT COUNT(*) FROM entities").fetchone()

            return result[0] if result else 0
