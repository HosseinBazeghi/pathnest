"""Configuration path resolution for PathNest.

Resolution priority (highest first):

1. Explicit ``--config`` CLI argument.
2. ``PATHNEST_CONFIG_FILE`` environment variable.
3. ``$XDG_CONFIG_HOME/pathnest/bookmarks.json`` (when XDG_CONFIG_HOME is set).
4. ``~/.config/pathnest/bookmarks.json``.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "pathnest"
CONFIG_FILE_NAME = "bookmarks.json"
CONFIG_FILE_ENV = "PATHNEST_CONFIG_FILE"
RESULT_FILE_ENV = "PATHNEST_RESULT_FILE"


class ConfigError(Exception):
    """Raised when a configuration path cannot be used."""


def expand_user_path(value: str | Path) -> Path:
    """Expand ``~`` and normalize a path without resolving symlinks."""
    text = str(value)
    if not text.strip():
        raise ConfigError("path must not be empty")
    if "\x00" in text:
        raise ConfigError("path must not contain NUL characters")
    try:
        return Path(os.path.abspath(os.path.expanduser(text)))
    except OSError as exc:
        raise ConfigError(f"cannot normalize path: {exc}") from exc


def default_config_path() -> Path:
    """Return the default configuration path (XDG-aware)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg and os.path.isabs(xdg):
        base = expand_user_path(xdg)
    else:
        base = Path.home() / ".config"
    return base / APP_NAME / CONFIG_FILE_NAME


def resolve_config_path(cli_value: str | None = None) -> Path:
    """Resolve the configuration file path from CLI argument and environment."""
    if cli_value is not None:
        path = expand_user_path(cli_value)
        if path.is_dir():
            raise ConfigError(f"--config points to a directory: {path}")
        return path
    env_value = os.environ.get(CONFIG_FILE_ENV, "")
    if env_value:
        path = expand_user_path(env_value)
        if path.is_dir():
            raise ConfigError(f"{CONFIG_FILE_ENV} points to a directory: {path}")
        return path
    return default_config_path()


def result_file_from_env() -> Path | None:
    """Return the path of the shell-wrapper result file, or None."""
    value = os.environ.get(RESULT_FILE_ENV, "")
    return expand_user_path(value) if value else None
