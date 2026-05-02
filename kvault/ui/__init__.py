"""kvault web UI — read-only browser for knowledge bases.

Requires optional dependencies: ``pip install 'knowledgevault[ui]'``
"""

try:
    from kvault.ui.app import create_app  # noqa: F401

    __all__ = ["create_app"]
except ImportError:
    __all__ = []
