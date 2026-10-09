"""Tests for configuration path resolution and priority rules."""

import pytest

from pathnest.config import (
    CONFIG_FILE_ENV,
    ConfigError,
    default_config_path,
    expand_user_path,
    resolve_config_path,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv(CONFIG_FILE_ENV, raising=False)


def test_default_uses_home_config():
    path = resolve_config_path()
    assert path == expand_user_path("~/.config/pathnest/bookmarks.json")


def test_xdg_config_home_respected(monkeypatch, tmp_path):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert resolve_config_path() == xdg / "pathnest" / "bookmarks.json"
    assert default_config_path() == xdg / "pathnest" / "bookmarks.json"


def test_env_var_beats_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    custom = tmp_path / "custom" / "pn.json"
    monkeypatch.setenv(CONFIG_FILE_ENV, str(custom))
    assert resolve_config_path() == custom


def test_cli_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_FILE_ENV, str(tmp_path / "env.json"))
    cli = str(tmp_path / "cli.json")
    assert resolve_config_path(cli) == expand_user_path(cli)


def test_env_var_tilde_expansion(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_FILE_ENV, "~/myconfigs/pathnest.json")
    resolved = resolve_config_path()
    assert resolved.name == "pathnest.json"
    assert str(resolved).startswith(str(tmp_path))


def test_cli_directory_rejected(tmp_path):
    with pytest.raises(ConfigError):
        resolve_config_path(str(tmp_path))


def test_empty_path_rejected():
    with pytest.raises(ConfigError):
        expand_user_path("   ")


@pytest.mark.parametrize('value', ['', ' ', 'bad\x00path'])
def test_invalid_explicit_config_rejected(value):
    with pytest.raises(ConfigError):
        resolve_config_path(value)


def test_relative_config_is_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_config_path('db.json') == tmp_path / 'db.json'


def test_config_preserves_trailing_spaces(tmp_path):
    assert resolve_config_path(str(tmp_path / 'db.json ')) == tmp_path / 'db.json '


def test_relative_xdg_config_home_is_ignored(monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', 'relative')
    assert default_config_path() == expand_user_path('~/.config/pathnest/bookmarks.json')
