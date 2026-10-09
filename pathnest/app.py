"""Two-panel TUI for PathNest: groups on the left, bookmarks on the right.

Every destructive action only edits the saved JSON references; bookmarked
directories themselves are never modified.
"""

from __future__ import annotations

import os
from typing import Callable

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Select, Static

from .storage import Store, StorageError, default_name, normalize_path

SAFETY_NOTE = (
    "Only saved references will be removed.\n"
    "Your actual directories and files will NOT be deleted."
)


class InputScreen(ModalScreen[str]):
    """Single-field dialog.  Dismisses with the entered string or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(
        self,
        title: str,
        label: str,
        initial: str = "",
        validate: Callable[[str], str | None] | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._label = label
        self._initial = initial
        self._validate = validate

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg"):
            yield Static(self._title, id="dlg-title")
            yield Static(self._label, classes="dlg-label")
            yield Input(value=self._initial, id="dlg-input")
            yield Static("", id="dlg-error", markup=False)
            yield Static("Enter confirm · Esc cancel", classes="dlg-hint")

    def on_mount(self) -> None:
        self.query_one("#dlg-input", Input).focus()

    @on(Input.Submitted, "#dlg-input")
    def _submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        error = self._validate(value) if self._validate else None
        if error:
            self.query_one("#dlg-error", Static).update(error)
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss("")


class BookmarkFormScreen(ModalScreen[tuple[str, str, str]]):
    """Name / group / path form.  Dismisses with (name, group, path) or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(
        self,
        group_names: list[str],
        *,
        title: str,
        name: str = "",
        group: str = "",
        path: str = "",
        validate: Callable[[str, str, str], str | None] | None = None,
    ) -> None:
        super().__init__()
        self._group_names = group_names
        self._title = title
        self._name = name
        self._group = group
        self._path = path
        self._validate = validate

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg"):
            yield Static(self._title, id="dlg-title")
            yield Static("Name", classes="dlg-label")
            yield Input(value=self._name, id="f-name")
            yield Static("Group", classes="dlg-label")
            yield Select(
                [(Text(n), n) for n in self._group_names],
                value=self._group or self._group_names[0],
                allow_blank=False,
                id="f-group",
            )
            yield Static("Path", classes="dlg-label")
            yield Input(value=self._path, id="f-path")
            yield Static("", id="dlg-error", markup=False)
            yield Static("Enter confirm · Tab between fields · Esc cancel", classes="dlg-hint")

    def on_mount(self) -> None:
        self.query_one("#f-name", Input).focus()

    @on(Input.Submitted, "#f-name")
    @on(Input.Submitted, "#f-path")
    def _submitted(self, event: Input.Submitted) -> None:
        name = self.query_one("#f-name", Input).value.strip()
        path = self.query_one("#f-path", Input).value
        group = self.query_one("#f-group", Select).value
        try:
            error = self._validate(name, group, path) if self._validate else None
        except StorageError as exc:
            error = str(exc)
        if error:
            self.query_one("#dlg-error", Static).update(error)
            return
        self.dismiss((name, group, path))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """Confirmation dialog with Cancel as the safe default."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, message: str, confirm_label: str = "Remove") -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg"):
            yield Static(self._message, id="dlg-msg", markup=False)
            with Horizontal(id="dlg-buttons"):
                yield Button("Cancel", id="btn-cancel")
                yield Button(self._confirm_label, variant="error", id="btn-confirm")
            yield Static("Enter triggers the focused button · Esc cancels", classes="dlg-hint")

    def on_mount(self) -> None:
        self.query_one("#btn-cancel", Button).focus()  # Cancel is the default

    @on(Button.Pressed, "#btn-cancel")
    def _cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#btn-confirm")
    def _confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg"):
            yield Static("Keyboard shortcuts", id="dlg-title")
            yield Static(
                "  ↑ / ↓      Move selection\n"
                "  Tab        Switch panels\n"
                "  Enter      Left panel: open group\n"
                "             Right panel: cd into directory\n"
                "  a          Add group / bookmark\n"
                "  e          Edit selected group / bookmark\n"
                "  d          Remove selected group / bookmark\n"
                "  ?          Show this help\n"
                "  q          Quit (directory unchanged)\n"
                "  Esc        Cancel dialog",
                id="dlg-msg",
            )
            yield Static("Press Esc or q to close", classes="dlg-hint")

    def action_close(self) -> None:
        self.dismiss(None)


class PathNestApp(App):
    TITLE = "PATHNEST"
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    #panels {
        height: 1fr;
    }
    ListView {
        border: round $panel;
        padding: 0;
    }
    ListView:focus {
        border: round $accent;
    }
    #groups {
        width: 1fr;
    }
    #bookmarks {
        width: 3fr;
    }
    ListItem {
        layout: horizontal;
        height: auto;
        padding: 0 1;
    }
    ListItem > .bm-name {
        width: 1fr;
    }
    ListItem > .bm-path {
        width: 2fr;
        color: grey 55%;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    InputScreen, BookmarkFormScreen, ConfirmScreen, HelpScreen {
        align: center middle;
        background: black 45%;
    }
    #dlg {
        width: 64;
        max-width: 90%;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #dlg-title {
        text-style: bold;
        margin-bottom: 1;
    }
    .dlg-label {
        color: grey 70%;
        margin-top: 1;
    }
    #dlg-error {
        color: $error;
        margin-top: 1;
    }
    .dlg-hint {
        color: grey 50%;
        margin-top: 1;
    }
    #dlg-msg {
        margin-bottom: 1;
    }
    #dlg-buttons {
        height: auto;
        align-horizontal: center;
        margin-top: 1;
    }
    #dlg-buttons Button {
        margin: 0 2;
    }
    """

    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("e", "edit", "Edit"),
        Binding("d", "remove", "Remove"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+q", "quit", show=False),
    ]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.selected_path: str | None = None
        self.current_group: int | None = None
        self._syncing = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="panels"):
            yield ListView(id="groups")
            yield ListView(id="bookmarks")
        yield Footer()

    async def on_mount(self) -> None:
        groups = self.query_one("#groups", ListView)
        groups.border_title = "GROUPS"
        await self.refresh_lists(select=0 if self.store.groups else None)
        groups.focus()

    def _current_group(self) -> dict | None:
        if self.current_group is None or not self.store.groups:
            return None
        if self.current_group >= len(self.store.groups):
            self.current_group = len(self.store.groups) - 1
        return self.store.groups[self.current_group]

    def _current_group_name(self) -> str:
        group = self._current_group()
        return group["name"] if group else ""

    def _panel(self) -> str:
        return "bookmarks" if (self.focused is not None and self.focused.id == "bookmarks") else "groups"

    async def refresh_lists(self, select: int | None = None) -> None:
        groups_lv = self.query_one("#groups", ListView)
        bookmark_index = self.query_one("#bookmarks", ListView).index
        self._syncing = True
        try:
            await groups_lv.clear()
            await groups_lv.extend(ListItem(Label(name, markup=False)) for name in self.store.group_names())
            count = len(self.store.groups)
            if count == 0:
                self.current_group = None
            else:
                if select is not None:
                    self.current_group = select
                self.current_group = max(0, min(self.current_group or 0, count - 1))
                groups_lv.index = self.current_group
            await self._fill_bookmarks(bookmark_index)
        finally:
            self._syncing = False

    async def _fill_bookmarks(self, index: int | None = 0) -> None:
        bms_lv = self.query_one("#bookmarks", ListView)
        group = self._current_group()
        await bms_lv.clear()
        entries = group["bookmarks"] if group else []
        await bms_lv.extend(
            ListItem(
                Label(bm["name"], classes="bm-name", markup=False),
                Label(bm["path"], classes="bm-path", markup=False),
            )
            for bm in entries
        )
        bms_lv.border_title = f"BOOKMARKED DIRECTORIES ({len(entries)})"
        bms_lv.index = max(0, min(index or 0, len(entries) - 1)) if entries else None

    async def _on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if self._syncing or event.list_view.id != "groups":
            return
        new_index = event.list_view.index
        if new_index is None or new_index == self.current_group:
            return
        self.current_group = new_index
        await self._fill_bookmarks()

    async def _on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "groups":
            self.query_one("#bookmarks", ListView).focus()
        else:
            self._open_bookmark()

    def _open_bookmark(self) -> None:
        group = self._current_group()
        if not group:
            return
        index = self.query_one("#bookmarks", ListView).index
        if index is None or index >= len(group["bookmarks"]):
            return
        path = group["bookmarks"][index]["path"]
        if not os.path.isdir(path):
            self.notify(
                f"Directory no longer exists:\n{escape(path)}", title="Cannot navigate", severity="error"
            )
            return
        self.selected_path = path
        self.exit()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {"add", "edit", "remove", "help", "quit"} and isinstance(self.screen, ModalScreen):
            return False
        return True

    def action_add(self) -> None:
        if self._panel() == "groups":
            self.run_worker(self._add_group(), exclusive=True)
        else:
            self.run_worker(self._add_bookmark(), exclusive=True)

    async def _add_group(self) -> None:
        try:
            def validate(name: str) -> str | None:
                if not name:
                    return "Group name is required."
                if name in self.store.group_names():
                    return f'Group "{name}" already exists.'
                return None

            value = await self.push_screen(
                InputScreen("Add group", "Group name", validate=validate), wait_for_dismiss=True
            )
            if not value:
                return
            self.store.add_group(value)
            await self.refresh_lists(select=len(self.store.groups) - 1)
        except (StorageError, OSError) as exc:
            await self.refresh_lists()
            self.notify(escape(str(exc)), title="Add failed", severity="error")

    async def _add_bookmark(self) -> None:
        try:
            names = self.store.group_names()
            if not names:
                self.notify(
                    "Add a group first: focus the left panel and press a.",
                    title="No groups yet",
                    severity="warning",
                )
                return
            cwd = os.getcwd()

            def validate(name: str, group: str, path: str) -> str | None:
                if not name:
                    return "Name is required."
                if any(bm["name"] == name for bm in self.store.bookmarks(group)):
                    return f'A bookmark named "{name}" already exists in {group}.'
                normalized = normalize_path(path)
                if not os.path.isdir(normalized):
                    return f"Directory does not exist:\n{normalized}"
                return None

            result = await self.push_screen(
                BookmarkFormScreen(
                    names,
                    title="Add bookmark",
                    name=default_name(cwd),
                    group=self._current_group_name(),
                    path=cwd,
                    validate=validate,
                ),
                wait_for_dismiss=True,
            )
            if result is None:
                return
            name, group, path = result
            self.store.add_bookmark(group, name, path)
            await self.refresh_lists(select=self.store.group_names().index(group))
        except (StorageError, OSError) as exc:
            await self.refresh_lists()
            self.notify(escape(str(exc)), title="Add failed", severity="error")

    def action_edit(self) -> None:
        if self._panel() == "groups":
            self.run_worker(self._edit_group(), exclusive=True)
        else:
            self.run_worker(self._edit_bookmark(), exclusive=True)

    async def _edit_group(self) -> None:
        try:
            group = self._current_group()
            if group is None:
                self.notify("Nothing to edit: add a group first (a).", severity="warning")
                return
            old = group["name"]

            def validate(name: str) -> str | None:
                if not name:
                    return "Group name is required."
                if name != old and name in self.store.group_names():
                    return f'Group "{name}" already exists.'
                return None

            value = await self.push_screen(
                InputScreen("Edit group", "Group name", initial=old, validate=validate),
                wait_for_dismiss=True,
            )
            if not value:
                return
            self.store.rename_group(old, value)
            await self.refresh_lists()
        except (StorageError, OSError) as exc:
            await self.refresh_lists()
            self.notify(escape(str(exc)), title="Edit failed", severity="error")

    async def _edit_bookmark(self) -> None:
        try:
            group = self._current_group()
            if group is None or not group["bookmarks"]:
                self.notify("Nothing to edit: add a bookmark first (a).", severity="warning")
                return
            index = self.query_one("#bookmarks", ListView).index
            if index is None:
                return
            bm = group["bookmarks"][index]
            group_name = group["name"]

            def validate(name: str, target: str, path: str) -> str | None:
                if not name:
                    return "Name is required."
                for existing in self.store.bookmarks(target):
                    if existing is not bm and existing["name"] == name:
                        return f'A bookmark named "{name}" already exists in {target}.'
                normalized = normalize_path(path)
                if not os.path.isdir(normalized):
                    return f"Directory does not exist:\n{normalized}"
                return None

            result = await self.push_screen(
                BookmarkFormScreen(
                    self.store.group_names(),
                    title="Edit bookmark",
                    name=bm["name"],
                    group=group_name,
                    path=bm["path"],
                    validate=validate,
                ),
                wait_for_dismiss=True,
            )
            if result is None:
                return
            name, target, path = result
            self.store.update_bookmark(
                group_name, bm["name"], new_group=target, new_name=name, new_path=path
            )
            await self.refresh_lists(select=self.store.group_names().index(target))
        except (StorageError, OSError) as exc:
            await self.refresh_lists()
            self.notify(escape(str(exc)), title="Edit failed", severity="error")

    def action_remove(self) -> None:
        if self._panel() == "groups":
            self.run_worker(self._remove_group(), exclusive=True)
        else:
            self.run_worker(self._remove_bookmark(), exclusive=True)

    async def _remove_group(self) -> None:
        try:
            group = self._current_group()
            if group is None:
                self.notify("Nothing to remove.", severity="warning")
                return
            name = group["name"]
            count = len(group["bookmarks"])
            message = (
                f'Remove group "{name}" and its {count} bookmark'
                f'{"s" if count != 1 else ""}?\n\n{SAFETY_NOTE}'
            )
            confirmed = await self.push_screen(ConfirmScreen(message), wait_for_dismiss=True)
            if not confirmed:
                return
            self.store.remove_group(name)
            await self.refresh_lists()
        except (StorageError, OSError) as exc:
            await self.refresh_lists()
            self.notify(escape(str(exc)), title="Remove failed", severity="error")

    async def _remove_bookmark(self) -> None:
        try:
            group = self._current_group()
            if group is None or not group["bookmarks"]:
                self.notify("Nothing to remove.", severity="warning")
                return
            index = self.query_one("#bookmarks", ListView).index
            if index is None:
                return
            bm = group["bookmarks"][index]
            message = f'Remove bookmark "{bm["name"]}"?\n  {bm["path"]}\n\n{SAFETY_NOTE}'
            confirmed = await self.push_screen(ConfirmScreen(message), wait_for_dismiss=True)
            if not confirmed:
                return
            self.store.remove_bookmark(group["name"], bm["name"])
            await self.refresh_lists()
        except (StorageError, OSError) as exc:
            await self.refresh_lists()
            self.notify(escape(str(exc)), title="Remove failed", severity="error")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self.exit()
