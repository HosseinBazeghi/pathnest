"""Read-only shell completion; never initialize or repair the bookmark file."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path


def complete(config: Path, words: list[str]) -> int:
    if words and words[0] == "--":
        words = words[1:]
    # Words include pn itself and a possibly empty current word.
    words = words[1:] or [""]
    prefix, previous = words[-1], words[-2] if len(words) > 1 else ""
    # Respect --config anywhere in the command being completed.
    for index, word in enumerate(words[:-1]):
        if word == "--config" and index + 1 < len(words) - 1:
            config = Path(words[index + 1]).expanduser()
        elif word.startswith("--config="):
            config = Path(word.split("=", 1)[1]).expanduser()
    command_words = []
    skip = False
    for word in words[:-1]:
        if skip:
            skip = False
        elif word in ("--config", "--group", "-g", "--name", "-n", "--path", "--version"):
            skip = True
        elif not word.startswith("-"):
            command_words.append(word)
    command = command_words[0] if command_words else ""
    groups = None

    def saved_groups():
        nonlocal groups
        if groups is None:
            groups = _read_groups(config)
        return groups

    if previous in ("--config", "--name", "-n", "--path", "--version"):
        candidates = []
    elif previous in ("--group", "-g"):
        candidates = [g["name"] for g in saved_groups()]
    elif prefix.startswith("-"):
        candidates = ["--config", "--help"]
        if command in ("go", "add", "scan", "edit"):
            candidates += ["--group"]
        if command == "update":
            candidates += ["--check", "--version"]
        if command in ("add", "edit"):
            candidates += ["--name"]
        if command == "edit":
            candidates += ["--path"]
        if command == "list":
            candidates += ["--json"]
        if command in ("scan", "remove"):
            candidates += ["--yes"]
    elif not command:
        candidates = ["add", "edit", "go", "group", "list", "remove", "scan", "update"]
    elif command == "group" and len(command_words) == 1:
        candidates = ["add", "rename", "remove"]
    elif command == "go" or (command in ("remove", "edit") and len(command_words) == 2):
        group = command_words[1] if command in ("remove", "edit") else None
        if command == "go":
            for index, word in enumerate(words[:-1]):
                if word in ("--group", "-g") and index + 1 < len(words) - 1:
                    group = words[index + 1]
                elif word.startswith("--group="):
                    group = word.split("=", 1)[1]
        candidates = [bm["name"] for g in saved_groups() if group is None or g["name"] == group
                      for bm in g["bookmarks"]]
    elif command in ("list", "remove", "edit") or (command == "group" and len(command_words) == 2
                                            and command_words[1] in ("rename", "remove")):
        candidates = [g["name"] for g in saved_groups()]
    else:
        candidates = []
    for candidate in sorted(set(candidates)):
        matches = candidate.casefold().startswith(prefix.casefold()) if command == "go" else candidate.startswith(prefix)
        if matches and not any(c in candidate for c in "\n\r\t"):
            print(candidate)
    return 0


def _read_groups(config: Path) -> list[dict]:
    from .storage import Store, StorageError

    try:
        fd = os.open(config, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return []
            return Store._parse(json.loads(stream.read()))
    except (OSError, ValueError, UnicodeError, StorageError, RecursionError):
        return []
