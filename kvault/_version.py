"""Single source of the package version.

Importable from anywhere in the package without side effects: ``operations``
needs the version for ``status --json`` and cannot import ``kvault`` itself
(cycle). The fallback string is for an uninstalled source tree only and must
match ``pyproject.toml``.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

try:
    __version__ = _distribution_version("knowledgevault")
except PackageNotFoundError:
    # Uninstalled source tree only; keep in sync with pyproject.toml.
    __version__ = "0.15.0"

__all__ = ["__version__"]
