"""Headless Textual pilot tests for the two-panel TUI."""

import asyncio
from pathlib import Path

import pytest

from pathnest.app import PathNestApp
from pathnest.storage import Store

pytest.importorskip("textual")

from textual.widgets import ListItem, ListView  # noqa: E402


def make_store(tmp_path: Path) -> Store:
    research = tmp_path / "research"
    research.mkdir()
    uni = tmp_path / "university"
    uni.mkdir()
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Research")
    store.add_bookmark("Research", "Main Project", str(research))
    store.add_group("University")
    store.add_bookmark("University", "Thesis", str(uni))
    store.add_bookmark("University", "Courses", str(uni))
    return store


def item_count(list_view: ListView) -> int:
    return sum(1 for child in list_view.children if isinstance(child, ListItem))


def run(coro):
    return asyncio.run(coro)


def test_panels_render_and_quit_leaves_no_selection(tmp_path):
    store = make_store(tmp_path)
    app = PathNestApp(store)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()  # let mount-time list refresh settle
            groups = app.query_one("#groups", ListView)
            bookmarks = app.query_one("#bookmarks", ListView)
            assert item_count(groups) == 2
            assert item_count(bookmarks) == 1  # first group selected by default
            assert app.focused is groups
            await pilot.press("q")

    run(scenario())
    assert app.selected_path is None


def test_highlighted_group_updates_bookmark_panel(tmp_path):
    store = make_store(tmp_path)
    app = PathNestApp(store)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("down")  # move from Research to University
            await pilot.pause()
            bookmarks = app.query_one("#bookmarks", ListView)
            assert item_count(bookmarks) == 2
            await pilot.press("q")

    run(scenario())


def test_enter_on_bookmark_selects_directory(tmp_path):
    store = make_store(tmp_path)
    app = PathNestApp(store)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("tab")  # switch to bookmarks panel
            await pilot.pause()
            assert app.focused is app.query_one("#bookmarks", ListView)
            await pilot.press("enter")

    run(scenario())
    assert app.selected_path == str(tmp_path / "research")


def test_add_group_dialog_flow(tmp_path):
    store = make_store(tmp_path)
    app = PathNestApp(store)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("a")  # groups panel focused -> Add group dialog
            await pilot.pause()
            for ch in "Personal":
                await pilot.press(ch)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("q")

    run(scenario())
    assert "Personal" in store.group_names()
    reloaded = Store(tmp_path / "bookmarks.json")
    assert "Personal" in reloaded.group_names()


def test_remove_group_confirmation_cancels_by_default(tmp_path):
    store = make_store(tmp_path)
    app = PathNestApp(store)

    async def scenario():
        from textual.widgets import Button

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("d")  # groups panel focused -> confirm removal
            await pilot.pause()
            focused = app.screen.focused
            assert isinstance(focused, Button)
            assert focused.id == "btn-cancel"  # Cancel is the safe default
            await pilot.press("enter")  # activates the focused Cancel button
            await pilot.pause()
            await pilot.press("q")

    run(scenario())
    assert "Research" in store.group_names()  # nothing removed


def test_remove_group_confirmed_only_removes_references(tmp_path):
    research = tmp_path / "research"
    store = make_store(tmp_path)
    app = PathNestApp(store)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("tab")  # focus moves from Cancel to Remove
            await pilot.pause()
            await pilot.press("enter")  # activate Remove
            await pilot.pause()
            await pilot.press("q")

    run(scenario())
    assert store.group_names() == ["University"]
    # the bookmarked directory itself is untouched
    assert research.is_dir()


def test_names_and_paths_are_literal_text(tmp_path):
    from textual.widgets import Input, Select
    target = tmp_path / '[/broken] '
    target.mkdir(parents=True)
    store = Store(tmp_path / 'db.json')
    store.add_bookmark('[/group]', '[/name]', str(target), create_group=True)
    app = PathNestApp(store)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert [str(label.render()) for label in app.query("ListItem Label")] == [
                "[/group]", "[/name]", str(target)
            ]
            await pilot.press('tab', 'e')
            await pilot.pause()
            assert app.screen.query_one('#f-path', Input).value == str(target)
            app.screen.query_one('#f-group', Select).focus()
            await pilot.press('enter', 'escape')
            app.screen.query_one('#f-path', Input).focus()
            await pilot.press('enter')
            await pilot.pause()
            await pilot.press('d')
            await pilot.pause()
            await pilot.press('escape', 'q')
    run(scenario())
    assert store.bookmarks('[/group]')[0]['path'] == str(target)


def test_empty_bookmark_path_keeps_form_open(tmp_path):
    from pathnest.app import BookmarkFormScreen
    from textual.widgets import Input
    store = make_store(tmp_path)
    app = PathNestApp(store)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press('tab', 'a')
            await pilot.pause()
            field = app.screen.query_one('#f-path', Input)
            field.value = ''
            field.focus()
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.screen, BookmarkFormScreen)
            assert len(store.bookmarks('Research')) == 1
            await pilot.press('escape', 'q')
    run(scenario())


def test_dialog_shortcuts_cannot_open_nested_dialogs(tmp_path):
    from pathnest.app import ConfirmScreen
    app = PathNestApp(make_store(tmp_path))
    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press('d')
            await pilot.pause()
            screen = app.screen
            for key in ('a', 'e', 'd', 'question_mark', 'q'):
                await pilot.press(key)
                assert app.screen is screen
                assert isinstance(app.screen, ConfirmScreen)
            await pilot.press('escape', 'q')
    run(scenario())


def test_refresh_preserves_bookmark_selection(tmp_path):
    app = PathNestApp(make_store(tmp_path))
    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press('down', 'tab', 'down')
            assert app.query_one('#bookmarks', ListView).index == 1
            await app.refresh_lists()
            await pilot.pause()
            assert app.query_one('#bookmarks', ListView).index == 1
            assert item_count(app.query_one('#bookmarks', ListView)) == 2
            await pilot.press('enter')
    run(scenario())
    assert app.selected_path == str(tmp_path / 'university')


def test_missing_bookmark_does_not_exit(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / 'research').rmdir()
    app = PathNestApp(store)
    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press('tab', 'enter')
            assert app.selected_path is None
            assert app.is_running
            await pilot.press('q')
    run(scenario())


def test_conflicting_edit_refreshes_tui_before_retry(tmp_path):
    from textual.widgets import Input
    store = make_store(tmp_path)
    app = PathNestApp(store)
    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press('e')
            await pilot.pause()
            Store(store.path).remove_group('Research')
            app.screen.query_one('#dlg-input', Input).value = 'Renamed'
            await pilot.press('enter')
            await pilot.pause()
            assert store.group_names() == ['University']
            assert item_count(app.query_one('#groups', ListView)) == 1
            assert item_count(app.query_one('#bookmarks', ListView)) == 2
            await pilot.press('tab', 'enter')
    run(scenario())
    assert app.selected_path == str(tmp_path / 'university')
