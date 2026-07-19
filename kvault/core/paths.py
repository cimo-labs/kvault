"""Canonical, symlink-aware filesystem path safety helpers.

All mutation entry points should resolve user-supplied paths through this
module before touching the filesystem.  Keeping containment rules here avoids
small differences between CLI, MCP, and legacy storage adapters.
"""

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


class PathSafetyError(ValueError):
    """Raised when a requested path is not safe for a KB operation."""


def _relative_to(path: Path, root: Path) -> bool:
    """Return whether *path* is *root* or one of its descendants."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_within_root(
    root: PathLike,
    relative_path: PathLike,
    *,
    allow_root: bool = True,
    must_exist: bool = False,
    reject_symlinks: bool = False,
) -> Path:
    """Resolve a relative KB path and prove that it remains below *root*.

    Traversal components and absolute paths are rejected even when they would
    happen to normalize back inside the root.  Existing symlinks are followed
    for the containment check.  Mutating callers should set
    ``reject_symlinks=True`` to avoid aliasing a different in-root node.
    """
    root_path = Path(root).expanduser().resolve()
    candidate = Path(relative_path)

    if "\x00" in str(relative_path):
        raise PathSafetyError("Path contains a NUL byte")
    if candidate.is_absolute():
        raise PathSafetyError("Path must be relative to the KB root")
    if ".." in candidate.parts:
        raise PathSafetyError("Path traversal is not allowed")

    lexical = root_path.joinpath(candidate)
    if reject_symlinks:
        current = root_path
        for part in candidate.parts:
            if part in ("", "."):
                continue
            current = current / part
            if current.is_symlink():
                raise PathSafetyError(f"Symlink path components are not allowed: {part}")

    resolved = lexical.resolve(strict=False)
    if not _relative_to(resolved, root_path):
        raise PathSafetyError("Path escapes the KB root")
    if resolved == root_path and not allow_root:
        raise PathSafetyError("The KB root is not a valid target")
    if must_exist and not resolved.exists():
        raise PathSafetyError(f"Path does not exist: {relative_path}")
    return resolved


def validate_node_target(
    root: PathLike,
    relative_path: PathLike,
    *,
    require_exists: bool = True,
) -> Path:
    """Return a deletion-safe canonical node directory.

    A deletable node must be a visible directory below the KB root containing
    a real ``_summary.md`` file.  The temporal journal and internal control
    directories are never semantic deletion targets.
    """
    resolved = resolve_node_path(
        root,
        relative_path,
        allow_root=False,
        must_exist=require_exists,
        reject_symlinks=True,
    )
    if require_exists:
        if not resolved.is_dir():
            raise PathSafetyError("Deletion target is not a node directory")
        summary = resolved / "_summary.md"
        if not summary.is_file() or summary.is_symlink():
            raise PathSafetyError("Deletion target is not a semantic node")
    return resolved


def resolve_node_path(
    root: PathLike,
    relative_path: PathLike,
    *,
    allow_root: bool = False,
    must_exist: bool = False,
    reject_symlinks: bool = True,
) -> Path:
    """Resolve a semantic-node path, excluding reserved KB namespaces."""
    candidate = Path(relative_path)
    parts = [part for part in candidate.parts if part not in ("", ".")]
    if not parts and not allow_root:
        raise PathSafetyError("The KB root is not a valid node target")
    if any(part.startswith((".", "_")) for part in parts):
        raise PathSafetyError("Hidden and internal paths are reserved")
    if parts and parts[0] == "journal":
        raise PathSafetyError("Journal evidence is not a semantic node")
    return resolve_within_root(
        root,
        candidate,
        allow_root=allow_root,
        must_exist=must_exist,
        reject_symlinks=reject_symlinks,
    )


__all__ = [
    "PathSafetyError",
    "resolve_node_path",
    "resolve_within_root",
    "validate_node_target",
]
