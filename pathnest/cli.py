"""Command-line interface for PathNest.

Running with no command launches the interactive TUI; otherwise the
subcommands manage groups and bookmarks non-interactively.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    # Shared option so --config works both before and after the subcommand
    # (SUPPRESS keeps the subparser from clobbering a global value).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        metavar="FILE",
        help="path to the bookmark database (default: ~/.config/pathnest/bookmarks.json)",
        default=argparse.SUPPRESS,
    )

    parser = argparse.ArgumentParser(
        prog="pn",
        description=(
            "PathNest — grouped directory bookmarks. "
            "Run without a command to open the interactive navigator."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="path to the bookmark database (default: ~/.config/pathnest/bookmarks.json)",
    )
    parser.add_argument("--version", action="version", version=f"pathnest {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_add = sub.add_parser("add", parents=[common], help="bookmark a directory (defaults to the current directory)")
    p_add.add_argument("path", nargs="?", help="directory to bookmark (default: current directory)")
    p_add.add_argument("-g", "--group", metavar="GROUP", help='destination group (default "Default")')
    p_add.add_argument("-n", "--name", metavar="NAME", help="bookmark name (default: directory name)")

    p_go = sub.add_parser("go", parents=[common], help="jump to a bookmark by (partial) name")
    p_go.add_argument("name", metavar="NAME", help="bookmark name; a partial name offers close matches")

    p_rm = sub.add_parser("remove", parents=[common], help="remove a bookmark (never deletes any files)")
    p_rm.add_argument("group", metavar="GROUP", help="group containing the bookmark")
    p_rm.add_argument("name", metavar="NAME", help="bookmark to remove")
    p_rm.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")

    p_group = sub.add_parser("group", parents=[common], help="manage groups")
    g_sub = p_group.add_subparsers(dest="group_command", metavar="ACTION", required=True)

    g_add = g_sub.add_parser("add", parents=[common], help="create a group")
    g_add.add_argument("name", metavar="NAME")

    g_rename = g_sub.add_parser("rename", parents=[common], help="rename a group (bookmarks are kept)")
    g_rename.add_argument("old", metavar="OLD")
    g_rename.add_argument("new", metavar="NEW")

    g_remove = g_sub.add_parser("remove", parents=[common], help="remove a group and its bookmarks (never deletes files)")
    g_remove.add_argument("name", metavar="NAME")
    g_remove.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")

    p_list = sub.add_parser("list", parents=[common], help="list groups and bookmarks")
    p_list.add_argument("group", nargs="?", metavar="GROUP", help="show only this group")

    return parser


def emit_selected_path(path: str) -> bool:
    """Hand the chosen directory to the calling shell.

    Writes one line to $PATHNEST_RESULT_FILE when set (the shell wrapper
    reads it and runs ``cd``); otherwise prints the path to stdout, so
    ``cd "$(pn-bin)"`` also works without the wrapper.  Returns True on
    success.
    """
    from .config import RESULT_FILE_ENV, ConfigError, result_file_from_env

    try:
        result_file = result_file_from_env()
    except ConfigError as exc:
        print(f"pn: invalid {RESULT_FILE_ENV}: {exc}", file=sys.stderr)
        return False
    if result_file is None:
        print(path)
        return True
    try:
        result_file.parent.mkdir(parents=True, exist_ok=True)
        import stat

        fd = os.open(result_file, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("result target must be a regular file with one link")
            os.fchmod(fh.fileno(), 0o600)
            fh.truncate(0)
            fh.write(path + "\n")
    except OSError as exc:
        print(f"pn: cannot write {RESULT_FILE_ENV} target {result_file}: {exc}", file=sys.stderr)
        return False
    return True


def run_tui(store) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("pn: the interactive navigator must be run in a terminal", file=sys.stderr)
        return 1
    from .app import PathNestApp

    app = PathNestApp(store)
    app.run()
    if app.selected_path and not emit_selected_path(app.selected_path):
        return 1
    return 0


def _confirm(prompt: str, skip: bool) -> bool:
    if skip:
        return True
    if not sys.stdin.isatty():
        print(
            "pn: declined (not a terminal; pass --yes to skip confirmation)", file=sys.stderr
        )
        return False
    try:
        answer = input(f"{prompt} [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in ("y", "yes")


def cmd_add(store, args: argparse.Namespace) -> int:
    from .storage import default_name

    raw_path = args.path if args.path is not None else os.getcwd()
    group = args.group if args.group is not None else "Default"
    name = args.name if args.name is not None else default_name(raw_path)
    entry = store.add_bookmark(group, name, raw_path, create_group=True)
    print(f'Added "{entry["name"]}" -> {entry["path"]} (group "{group}")')
    return 0


def _jump(path: str) -> int:
    """Validate a bookmark target and hand it to the calling shell."""
    if not os.path.isdir(path):
        print(f"pn: directory no longer exists: {path}", file=sys.stderr)
        return 1
    if not emit_selected_path(path):
        return 1
    if os.environ.get("PATHNEST_RESULT_FILE", "").strip():
        print(f"→ {path}")  # friendly confirmation; the wrapper ignores stdout
    return 0


def cmd_go(store, args: argparse.Namespace) -> int:
    import difflib

    name = args.name.strip()
    entries = [(g["name"], bm) for g in store.groups for bm in g["bookmarks"]]

    exact = [(g, bm) for g, bm in entries if bm["name"] == name]
    if len(exact) == 1:
        return _jump(exact[0][1]["path"])
    if len(exact) > 1:
        print(f'pn: "{name}" exists in more than one group:', file=sys.stderr)
        for g, bm in exact:
            print(f"  {g}: {bm['path']}", file=sys.stderr)
        return 1

    close = difflib.get_close_matches(name, {bm["name"] for _, bm in entries}, n=3, cutoff=0.6)
    if not close:
        print(f'pn: no bookmark matching "{name}"', file=sys.stderr)
        return 1
    candidates = [(g, bm) for g, bm in entries if bm["name"] == close[0]]
    if len(close) == 1 and len(candidates) == 1:
        if not sys.stdin.isatty():
            print(f'pn: not a terminal; use the exact name "{close[0]}"', file=sys.stderr)
            return 1
        try:
            answer = input(f'Did you mean "{close[0]}"? [y/N] ')
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if answer.strip().lower() not in ("y", "yes"):
            return 1
        return _jump(candidates[0][1]["path"])

    print("pn: no exact match. Did you mean:", file=sys.stderr)
    for close_name in close:
        for g, bm in entries:
            if bm["name"] == close_name:
                print(f"  {bm['name']}  ({g}): {bm['path']}", file=sys.stderr)
    return 1


def cmd_remove(store, args: argparse.Namespace) -> int:
    bookmark = store.bookmarks(args.group)
    target = next((b for b in bookmark if b["name"] == args.name), None)
    if target is None:
        print(f'pn: no bookmark "{args.name}" in group "{args.group}"', file=sys.stderr)
        return 1
    prompt = (
        f'Remove bookmark "{args.name}"?\n'
        f"  {target['path']}\n"
        "Only the saved reference is removed; no files are deleted."
    )
    if not _confirm(prompt, args.yes):
        return 1
    store.remove_bookmark(args.group, args.name)
    print(f'Removed bookmark "{args.name}" from group "{args.group}"')
    return 0


def cmd_group(store, args: argparse.Namespace) -> int:
    if args.group_command == "add":
        store.add_group(args.name)
        print(f'Added group "{args.name}"')
    elif args.group_command == "rename":
        store.rename_group(args.old, args.new)
        print(f'Renamed group "{args.old}" -> "{args.new}" (bookmarks kept)')
    elif args.group_command == "remove":
        count = len(store.bookmarks(args.name))
        prompt = (
            f'Remove group "{args.name}" and its {count} bookmark'
            f'{"s" if count != 1 else ""}?\n'
            "Only saved references are removed; no files are deleted."
        )
        if not _confirm(prompt, args.yes):
            return 1
        store.remove_group(args.name)
        print(f'Removed group "{args.name}" ({count} bookmarks)')
    return 0


def cmd_list(store, args: argparse.Namespace) -> int:
    if args.group:
        bookmarks = store.bookmarks(args.group)
        print(f"{args.group}:")
        for bm in bookmarks:
            print(f'  {bm["name"]}: {bm["path"]}')
        if not bookmarks:
            print("  (no bookmarks)")
        return 0
    if not store.groups:
        print("No groups yet. Add one with: pn group add NAME")
        return 0
    for group in store.groups:
        print(f'{group["name"]} ({len(group["bookmarks"])})')
        for bm in group["bookmarks"]:
            print(f'  {bm["name"]}: {bm["path"]}')
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    from .config import ConfigError, resolve_config_path
    from .storage import StorageError, Store

    try:
        config_path = resolve_config_path(args.config)
    except (ConfigError, OSError) as exc:
        print(f"pn: {exc}", file=sys.stderr)
        return 2

    try:
        store = Store(config_path)
    except (StorageError, OSError) as exc:
        print(f"pn: {exc}", file=sys.stderr)
        return 1
    for warning in store.warnings:
        print(f"pn: warning: {warning}", file=sys.stderr)

    handlers = {
        "add": cmd_add,
        "go": cmd_go,
        "remove": cmd_remove,
        "group": cmd_group,
        "list": cmd_list,
    }
    try:
        if args.command is None:
            return run_tui(store)
        return handlers[args.command](store, args)
    except (StorageError, OSError) as exc:
        print(f"pn: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
