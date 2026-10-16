"""User-facing regression coverage for search, onboarding, scanning and completion."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest
from textual.widgets import Input, ListView

from pathnest.app import BookmarkFormScreen, PathNestApp, SearchScreen
from pathnest.cli import main
from pathnest.storage import Store, StorageError


def test_group_jump_resolves_duplicate_name(tmp_path, monkeypatch, capsys):
    store = Store(tmp_path / 'bookmarks.json')
    for group in ('Work', 'Personal'):
        path = tmp_path / group
        path.mkdir()
        store.add_bookmark(group, 'api', str(path), create_group=True)
    monkeypatch.delenv('PATHNEST_RESULT_FILE', raising=False)
    assert main(['--config', str(store.path), 'go', 'api', '--group', 'Work']) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / 'Work')


def test_scan_selection_is_shallow_skips_hidden_and_existing(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'projects'
    for name in ('Alpha', 'Beta', '.hidden', 'Alpha/nested'):
        (root / name).mkdir(parents=True, exist_ok=True)
    store = Store(tmp_path / 'db.json')
    store.add_bookmark('Work', 'Existing', str(root / 'Alpha'), create_group=True)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: '1')
    assert main(['scan', str(root), '--group', 'Work', '--config', str(store.path)]) == 0
    assert [b['name'] for b in Store(store.path).bookmarks('Work')] == ['Existing', 'Beta']
    assert 'nested' not in capsys.readouterr().out


@pytest.mark.parametrize('answer', ['', '0', '100', 'no'])
def test_scan_cancel_or_invalid_selection_does_not_add(tmp_path, monkeypatch, answer):
    (tmp_path / 'project').mkdir()
    store = Store(tmp_path / 'db.json')
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: answer)
    main(['scan', str(tmp_path), '--config', str(store.path)])
    assert Store(store.path).groups == []


def test_scan_noninteractive_preview_and_yes(tmp_path):
    (tmp_path / 'project').mkdir()
    config = tmp_path / 'db.json'
    assert main(['scan', str(tmp_path), '--config', str(config)]) == 1
    assert Store(config).groups == []
    assert main(['scan', str(tmp_path), '--config', str(config), '--yes']) == 0
    assert len(Store(config).bookmarks('Default')) == 1


def test_batch_is_one_write_and_rolls_back_on_validation_failure(tmp_path):
    store = Store(tmp_path / 'db.json')
    with patch('pathnest.storage.os.replace', wraps=os.replace) as replace:
        store.add_bookmarks('Work', [('One', str(tmp_path)), ('Two', str(tmp_path))])
        assert replace.call_count == 1
    with pytest.raises(StorageError):
        store.add_bookmarks('New', [('Good', str(tmp_path)), ('Bad', str(tmp_path / 'gone'))])
    assert store.group_names() == ['Work']
    assert Store(store.path).groups == store.groups


def test_completion_read_only_and_group_filtered(tmp_path, capsys):
    config = tmp_path / 'db.json'
    assert main(['--config', str(config), '_complete', '--', 'pn', 'g']) == 0
    assert capsys.readouterr().out.splitlines() == ['go', 'group']
    assert not config.exists()
    store = Store(config)
    store.add_bookmark('Work', 'API service', str(tmp_path), create_group=True)
    store.add_bookmark('Personal', 'Another', str(tmp_path), create_group=True)
    assert main(['_complete', '--', 'pn', '--config', str(config), 'go', '--group', 'Work', 'A']) == 0
    assert capsys.readouterr().out.splitlines() == ['API service']
    config.write_text('broken')
    assert main(['--config', str(config), '_complete', '--', 'pn', 'go', '']) == 0
    assert config.read_text() == 'broken'
    assert not list(tmp_path.glob('*.invalid*'))


@pytest.mark.parametrize('shell', ['bash', 'zsh'])
def test_native_completion_preserves_spaces(tmp_path, shell):
    if not shutil.which(shell):
        pytest.skip('shell unavailable')
    root = Path(__file__).resolve().parents[1]
    store = Store(tmp_path / 'db.json')
    store.add_bookmark('Work', 'API service', str(tmp_path), create_group=True)
    executable = tmp_path / 'pn-bin'
    executable.write_text('#!/bin/sh\nexec "$PN_TEST_PYTHON" -m pathnest "$@"\n')
    executable.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path) + ':' + os.environ['PATH'],
               PATHNEST_CONFIG_FILE=str(store.path), PN_TEST_PYTHON=sys.executable,
               PYTHONPATH=str(root))
    if shell == 'bash':
        script = '. "$1"; COMP_WORDS=(pn go A); COMP_CWORD=2; _pn_complete; test "${#COMPREPLY[@]}" = 1 && test "${COMPREPLY[0]}" = "API service"'
    else:
        script = 'compdef() { :; }; . "$1"; words=(pn go api); CURRENT=3; compadd() { test "$1" = -U || return 1; shift 2; test "$#" = 1 && test "$1" = "API service"; }; _pn_complete'
    result = subprocess.run([shell, '-c', script, 'test', str(root / 'shell/pn.sh')], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_first_bookmark_and_cancel(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = Store(tmp_path / 'db.json')
    app = PathNestApp(store)
    async def scenario():
        async with app.run_test() as pilot:
            assert app.focused.id == 'save-current'
            await pilot.press('enter', 'escape')
            await app.workers.wait_for_complete()
            assert store.groups == []
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.screen, BookmarkFormScreen)
            await pilot.press('enter')
            await app.workers.wait_for_complete()
            assert store.bookmarks('Default')[0]['path'] == str(tmp_path)
            assert not app.query_one('#save-current').display
            assert app.focused.id == 'bookmarks'
    asyncio.run(scenario())


def test_search_across_groups_and_restore_selection(tmp_path):
    store = Store(tmp_path / 'db.json')
    store.add_bookmark('Work', 'API', str(tmp_path), create_group=True)
    store.add_bookmark('Research', 'Thesis', str(tmp_path), create_group=True)
    app = PathNestApp(store)
    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('down', 'tab', 'slash')
            await pilot.pause()
            assert isinstance(app.screen, SearchScreen)
            app.screen.query_one(Input).value = 'WORK api'
            await pilot.pause()
            assert len(app.screen.matches) == 1
            await pilot.press('escape')
            assert app.current_group == 1
            assert app.focused.id == 'bookmarks'
            await pilot.press('slash')
            app.screen.query_one(Input).value = 'thesis'
            await pilot.pause()
            await pilot.press('enter')
    asyncio.run(scenario())
    assert app.selected_path == str(tmp_path)


def test_search_no_match_missing_target_and_arrow_keys(tmp_path):
    store = Store(tmp_path / 'db.json')
    gone = tmp_path / 'gone'
    gone.mkdir()
    store.add_bookmark('Work', 'Gone', str(gone), create_group=True)
    store.add_bookmark('Work', 'Here', str(tmp_path), create_group=True)
    gone.rmdir()
    app = PathNestApp(store)
    async def scenario():
        async with app.run_test() as pilot:
            assert len(app.query('ListItem.unavailable')) == 1
            await pilot.press('slash', 'enter')
            assert isinstance(app.screen, SearchScreen)
            app.screen.query_one(Input).value = 'no match'
            await pilot.pause()
            await pilot.press('enter')
            assert app.selected_path is None
            app.screen.query_one(Input).value = ''
            await pilot.pause()
            await pilot.press('down', 'enter')
    asyncio.run(scenario())
    assert app.selected_path == str(tmp_path)


def test_completion_fifo_does_not_block(tmp_path):
    config = tmp_path / 'fifo'
    os.mkfifo(config)
    result = subprocess.run([sys.executable, '-m', 'pathnest', '--config', str(config),
                             '_complete', '--', 'pn', 'go', ''],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert result.stdout == ''


def test_batch_conflict_preserves_other_process_changes(tmp_path):
    stale = Store(tmp_path / 'db.json')
    current = Store(stale.path)
    current.add_bookmark('Work', 'Other', str(tmp_path), create_group=True)
    with pytest.raises(StorageError, match='another process'):
        stale.add_bookmarks('Work', [('Mine', str(tmp_path))])
    assert [bm['name'] for bm in Store(stale.path).bookmarks('Work')] == ['Other']


def test_search_limits_rendering_and_matches_unicode_without_stat(tmp_path):
    store = Store(tmp_path / 'db.json')
    store.add_bookmarks('Research', [(f'Thèse {i}', str(tmp_path)) for i in range(150)])
    app = PathNestApp(store)
    async def scenario():
        async with app.run_test() as pilot:
            with patch('pathnest.app.os.path.isdir', side_effect=AssertionError('search must not scan disk')):
                await pilot.press('slash')
                await pilot.pause()
                assert len(app.screen.query_one(ListView).children) == 100
                app.screen.query_one(Input).value = 'THÈSE 149'
                await pilot.pause()
                assert len(app.screen.matches) == 1
                await pilot.press('escape')
    asyncio.run(scenario())


def test_static_completion_never_reads_bookmarks(tmp_path, monkeypatch, capsys):
    from pathnest import completion
    monkeypatch.setattr(completion, '_read_groups', lambda _: pytest.fail('unneeded database read'))
    completion.complete(tmp_path / 'db.json', ['pn', 'u'])
    assert capsys.readouterr().out == 'update\n'
    completion.complete(tmp_path / 'db.json', ['pn', 'update', '--c'])
    assert capsys.readouterr().out.splitlines() == ['--check', '--config']


def test_scan_handles_normalized_name_collisions(tmp_path, capsys):
    for name in ('api', ' api ', ' '):
        (tmp_path / name).mkdir()
    config = tmp_path / 'db.json'
    assert main(['scan', str(tmp_path), '--config', str(config), '--yes']) == 0
    assert Store(config).bookmarks('Default') == [{'name': 'api', 'path': str(tmp_path / 'api')}]
    assert 'Skipped 2' in capsys.readouterr().out


def test_equivalent_search_results_keep_selection_and_widgets(tmp_path):
    store = Store(tmp_path / 'db.json')
    store.add_bookmarks('Work', [('API', str(tmp_path)), ('Website', str(tmp_path))])
    app = PathNestApp(store)
    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press('slash', 'down')
            results = app.screen.query_one(ListView)
            original = list(results.children)
            app.screen.query_one(Input).value = 'work'
            await pilot.pause()
            assert list(results.children) == original
            assert results.index == 1
            app.screen.query_one(Input).value = '  WORK  '
            await pilot.pause()
            assert list(results.children) == original
            await pilot.press('enter')
    asyncio.run(scenario())
    assert app.selected_path == str(tmp_path)


@pytest.mark.parametrize('words,expected', [
    (['pn', 'e'], ['edit']),
    (['pn', 'edit', ''], ['Personal', 'Work']),
    (['pn', 'edit', 'Work', ''], ['API service']),
    (['pn', 'edit', '--group', 'Personal', 'Work', 'A'], ['API service']),
    (['pn', 'edit', 'Work', 'API service', '--group', 'P'], ['Personal']),
    (['pn', 'edit', 'Work', 'API service', '--p'], ['--path']),
    (['pn', 'list', '--j'], ['--json']),
    (['pn', 'update', '--v'], ['--version']),
    (['pn', 'update', '--version', ''], []),
    (['pn', 'go', 'api'], ['API service']),
])
def test_completion_for_new_commands(tmp_path, capsys, words, expected):
    from pathnest.completion import complete
    store = Store(tmp_path / 'db.json')
    store.add_bookmark('Work', 'API service', str(tmp_path), create_group=True)
    store.add_bookmark('Personal', 'Another', str(tmp_path), create_group=True)
    assert complete(store.path, words) == 0
    assert capsys.readouterr().out.splitlines() == expected
