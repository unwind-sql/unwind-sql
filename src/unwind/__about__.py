"""Single source of truth for the package version.

The version is declared in ``pyproject.toml`` and read back here from the
installed distribution metadata, so there is exactly one place to bump.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("unwind-sql")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled source tree
    __version__ = "0.0.0+unknown"
