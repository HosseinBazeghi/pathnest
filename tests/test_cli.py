"""Tests for the non-interactive CLI commands."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pathnest.cli import main
from pathnest.storage import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("PATHNEST_CONFIG_FILE", raising=False)
    monkeypatch.delenv("PATHNEST_RESULT_FILE", raising=False)


def test_group_add_and_duplicate(tmp_path, capsys):
    cfg = tmp_path / "bookmarks.json"
    assert main(["--config", str(cfg), "group", "add", "Research"]) == 0
    assert Store(cfg).group_names() == ["Research"]
    assert main(["--config", str(cfg), "group", "add", "Research"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_group_rename_keeps_bookmarks(tmp_path):
    cfg = tmp_path / "bookmarks.json"
    main(["--config", str(cfg), "group", "add", "Research"])
    main(["--config", str(cfg), "add", "--group", "Research", "--name", "Main", str(tmp_path)])
    assert main(["--config", str(cfg), "group", "rename", "Research", "Science"]) == 0
    store = Store(cfg)
    assert store.group_names() == ["Science"]
    assert [b["name"] for b in store.bookmarks("Science")] == ["Main"]


def test_group_remove_requires_confirmation(tmp_path, capsys):
    cfg = tmp_path / "bookmarks.json"
    main(["--config", str(cfg), "group", "add", "Research"])
    rc = main(["--config", str(cfg), "group", "remove", "Research"])
    assert rc == 1  # no tty and no --yes -> declined
    assert "declined" in capsys.readouterr().err
    assert main(["--config", str(cfg), "group", "remove", "Research", "--yes"]) == 0
    assert Store(cfg).groups == []


def test_add_defaults_to_cwd(tmp_path, monkeypatch):
    cfg = tmp_path / "bookmarks.json"
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    assert main(["--config", str(cfg), "add"]) == 0
    store = Store(cfg)
    assert store.group_names() == ["Default"]
    assert store.bookmarks("Default") == [{"name": "proj", "path": str(project)}]


def test_add_with_group_and_name(tmp_path):
    cfg = tmp_path / "bookmarks.json"
    rc = main(
        ["--config", str(cfg), "add", "--group", "Research", "--name", "Experiments", str(tmp_path)]
    )
    assert rc == 0
    store = Store(cfg)
    assert store.group_names() == ["Research"]
    assert store.bookmarks("Research")[0]["path"] == str(tmp_path)


def test_add_rejects_missing_directory(tmp_path, capsys):
    cfg = tmp_path / "bookmarks.json"
    rc = main(["--config", str(cfg), "add", str(tmp_path / "nope")])
    assert rc == 1
    assert "not an existing directory" in capsys.readouterr().err


def test_add_failure_does_not_create_orphan_group(tmp_path):
    cfg = tmp_path / "bookmarks.json"
    rc = main(["--config", str(cfg), "add", str(tmp_path / "nope"), "--group", "NewGroup"])
    assert rc == 1
    assert Store(cfg).groups == []  # no half-created group left behind


def test_config_flag_accepted_after_subcommand(tmp_path):
    cfg = tmp_path / "after.json"
    assert main(["group", "add", "--config", str(cfg), "Research"]) == 0
    assert Store(cfg).group_names() == ["Research"]
    cfg2 = tmp_path / "after2.json"
    assert main(["--config", str(cfg2), "list"]) == 0  # global position still works
    assert cfg2.exists()


def test_tui_without_terminal_fails_cleanly(capsys):
    # pytest replaces stdin/stdout with non-tty objects, so the guard triggers
    assert main([]) == 1
    assert "terminal" in capsys.readouterr().err


def test_remove_bookmark(tmp_path, capsys):
    cfg = tmp_path / "bookmarks.json"
    main(["--config", str(cfg), "group", "add", "Research"])
    main(["--config", str(cfg), "add", "--group", "Research", "--name", "Main", str(tmp_path)])
    rc = main(["--config", str(cfg), "remove", "Research", "Main", "--yes"])
    assert rc == 0
    assert Store(cfg).bookmarks("Research") == []
    # the real directory still exists
    assert tmp_path.is_dir()


def test_list_output(tmp_path, capsys):
    cfg = tmp_path / "bookmarks.json"
    main(["--config", str(cfg), "group", "add", "Research"])
    main(["--config", str(cfg), "add", "--group", "Research", "--name", "Main", str(tmp_path)])
    assert main(["--config", str(cfg), "list"]) == 0
    out = capsys.readouterr().out
    assert "Research (1)" in out
    assert f"Main: {tmp_path}" in out
    assert main(["--config", str(cfg), "list", "Research"]) == 0
    assert "Main" in capsys.readouterr().out


def test_config_directory_rejected(tmp_path, capsys):
    assert main(["--config", str(tmp_path), "list"]) == 2
    assert "directory" in capsys.readouterr().err


def test_first_run_creates_config_in_default_location(tmp_path):
    # HOME points into tmp_path, so this does not touch the real home.
    assert main(["list"]) == 0
    expected = tmp_path / "home" / ".config" / "pathnest" / "bookmarks.json"
    assert expected.exists()
    assert json.loads(expected.read_text()) == {"groups": []}


def test_python_dash_m_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "pathnest", "--version"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert "pathnest" in result.stdout


# ---------------------------------------------------------------- go

@pytest.fixture()
def go_store(tmp_path):
    thesis = tmp_path / "thesis"
    thesis.mkdir()
    draft = tmp_path / "thesis-draft"
    draft.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("University")
    store.add_bookmark("University", "thesis", str(thesis))
    store.add_bookmark("University", "thesis-draft", str(draft))
    store.add_group("Personal")
    store.add_bookmark("Personal", "scripts", str(scripts))
    return store


def test_go_exact_jump_with_wrapper(go_store, tmp_path, monkeypatch, capsys):
    result_file = tmp_path / "result"
    monkeypatch.setenv("PATHNEST_RESULT_FILE", str(result_file))
    assert main(["--config", str(go_store.path), "go", "thesis"]) == 0
    assert result_file.read_text() == f"{go_store.bookmarks('University')[0]['path']}\n"
    assert "→" in capsys.readouterr().out


def test_go_exact_without_env_prints_path_only(go_store, monkeypatch, capsys):
    monkeypatch.delenv("PATHNEST_RESULT_FILE", raising=False)
    assert main(["--config", str(go_store.path), "go", "thesis"]) == 0
    out = capsys.readouterr().out
    assert out == f"{go_store.bookmarks('University')[0]['path']}\n"  # nothing extra


def test_go_same_name_in_two_groups_is_ambiguous(tmp_path, capsys):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("One")
    store.add_bookmark("One", "thesis", str(tmp_path / "a"))
    store.add_group("Two")
    store.add_bookmark("Two", "thesis", str(tmp_path / "b"))
    assert main(["--config", str(store.path), "go", "thesis"]) == 1
    err = capsys.readouterr().err
    assert "more than one group" in err
    assert "One" in err and "Two" in err


def test_go_close_match_confirmed(go_store, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.delenv("PATHNEST_RESULT_FILE", raising=False)
    assert main(["--config", str(go_store.path), "go", "thesi"]) == 0  # typo, one close match, confirmed
    assert "thesis\n" in capsys.readouterr().out


def test_go_close_match_declined(go_store, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.delenv("PATHNEST_RESULT_FILE", raising=False)
    assert main(["--config", str(go_store.path), "go", "thesi"]) == 1


def test_go_close_match_without_terminal_declines(go_store, capsys):
    assert main(["--config", str(go_store.path), "go", "thesi"]) == 1  # pytest stdin is not a tty
    assert 'use the exact name "thesis"' in capsys.readouterr().err


def test_go_multiple_close_matches_lists_them(go_store, capsys):
    assert main(["--config", str(go_store.path), "go", "thesis-"]) == 1  # thesis and thesis-draft
    err = capsys.readouterr().err
    assert "Did you mean" in err
    assert "thesis" in err and "thesis-draft" in err


def test_go_no_match(go_store, capsys):
    assert main(["--config", str(go_store.path), "go", "zzz"]) == 1
    assert "no bookmark matching" in capsys.readouterr().err


def test_go_missing_directory(tmp_path, capsys):
    gone = tmp_path / "gone"
    gone.mkdir()
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Personal")
    store.add_bookmark("Personal", "gone", str(gone))
    gone.rmdir()
    assert main(["--config", str(store.path), "go", "gone"]) == 1
    assert "no longer exists" in capsys.readouterr().err


@pytest.mark.parametrize('args', [['add', ''], ['add', '--group', ''], ['add', '--name', '']])
def test_explicit_empty_arguments_rejected(tmp_path, args):
    cfg = tmp_path / 'db.json'
    assert main(['--config', str(cfg), *args]) == 1
    assert Store(cfg).groups == []


def test_group_requires_action_before_loading_config(tmp_path):
    cfg = tmp_path / 'db.json'
    with pytest.raises(SystemExit) as exc:
        main(['--config', str(cfg), 'group'])
    assert exc.value.code == 2
    assert not cfg.exists()


def test_add_group_and_bookmark_use_one_write(tmp_path, monkeypatch):
    cfg = tmp_path / 'db.json'
    Store(cfg)
    from unittest.mock import patch
    import os
    with patch('pathnest.storage.os.replace', wraps=os.replace) as replace:
        assert main(['--config', str(cfg), 'add', str(tmp_path)]) == 0
    assert replace.call_count == 1


def test_add_handles_deleted_working_directory(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / 'db.json'
    gone = tmp_path / 'gone'
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    assert main(['--config', str(cfg), 'add']) == 1
    assert 'pn:' in capsys.readouterr().err


def test_non_tui_cli_does_not_import_textual():
    result = subprocess.run(
        [sys.executable, '-c', "import sys; from pathnest.cli import main; "
         "\ntry: main(['--version'])\nexcept SystemExit: pass\nassert 'textual' not in sys.modules"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_fuzzy_duplicate_names_are_listed_once_per_group(tmp_path, capsys):
    store = Store(tmp_path / 'db.json')
    for group in ('One', 'Two'):
        store.add_bookmark(group, 'thesis', str(tmp_path), create_group=True)
    assert main(['--config', str(store.path), 'go', 'thesi']) == 1
    err = capsys.readouterr().err
    assert err.count('(One)') == 1
    assert err.count('(Two)') == 1
