"""Layout conventions that more than one module must agree on.

``BACKGROUND_CHILD_DIRS`` names child directories that hold *supporting*
material for their parent rather than being nodes in their own right (the
``deep_context/`` convention: an entity keeps its long-form notes in one
child). Two rules key on it and must not drift apart:

- summary quality: a parent whose only summary-bearing children are
  background dirs is budgeted as a leaf, not as an index page;
- search collapse: a hit in a background child never justifies dropping its
  parent from the results — the parent *is* the canonical node.

It is a tuple, not a config knob, on purpose: a KB either uses the
convention or it does not, and both rules must see the same answer.
"""

from pathlib import Path
from typing import Iterable, Tuple

BACKGROUND_CHILD_DIRS: Tuple[str, ...] = ("deep_context",)


def is_background_child(name: str) -> bool:
    """True when *name* (a directory basename) is a background child."""
    return name in BACKGROUND_CHILD_DIRS


def only_background_children(child_dirs: Iterable[Path]) -> bool:
    """True when the node has children and every one is a background child."""
    names = [child.name for child in child_dirs]
    return bool(names) and all(is_background_child(name) for name in names)


__all__ = ["BACKGROUND_CHILD_DIRS", "is_background_child", "only_background_children"]
