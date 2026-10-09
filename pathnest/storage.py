"""JSON persistence and CRUD for PathNest groups and bookmarks.

Safety contract: this module only ever writes to the application's own
configuration file.  It never creates, moves, renames, or deletes any
bookmarked directory, and it performs no filesystem deletion of user data.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import ConfigError, expand_user_path


class StorageError(Exception):
    """User-facing error for group/bookmark operations."""


def normalize_path(raw: str) -> str:
    """Expand ``~`` and normalize a directory path (symlinks are kept)."""
    try:
        return str(expand_user_path(raw))
    except ConfigError as exc:
        raise StorageError(str(exc)) from exc


def default_name(raw: str) -> str:
    """Derive a bookmark name from a path (its last component)."""
    path = Path(normalize_path(raw))
    return path.name or str(path)


class Store:
    """Bookmark database persisted as JSON."""

    def __init__(self, path: Path | str):
        try:
            self.path = expand_user_path(path).resolve()
        except (ConfigError, OSError, RuntimeError) as exc:
            raise StorageError(f"cannot use configuration path: {exc}") from exc
        self.groups: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self._raw: bytes | None = None
        self.load()

    def _read(self) -> bytes | None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as fh:
                if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                    raise StorageError(f"configuration must be a regular file: {self.path}")
                return fh.read()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StorageError(f"cannot read {self.path}: {exc}") from exc

    @contextmanager
    def _locked(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "rb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield
        except OSError as exc:
            raise StorageError(f"cannot write {self.path}: {exc}") from exc

    def _decode(self, raw: bytes) -> list[dict[str, Any]]:
        groups = self._parse(json.loads(raw))
        relative_paths = False
        for group in groups:
            for bm in group["bookmarks"]:
                path = os.path.expanduser(bm["path"])
                if not os.path.isabs(path):
                    relative_paths = True
                    path = str(self.path.parent / path)
                bm["path"] = normalize_path(path)
        warning = f"legacy relative bookmark paths are based on {self.path.parent}; edit any saved from another directory"
        if relative_paths and warning not in self.warnings:
            self.warnings.append(warning)
        return groups

    def load(self) -> None:
        self.warnings = []
        raw = self._read()
        if raw is not None:
            try:
                self.groups = self._decode(raw)
                self._raw = raw
                return
            except (ValueError, UnicodeError, StorageError, RecursionError):
                pass
        with self._locked():
            raw = self._read()
            self._raw = raw
            if raw is None:
                self.groups = []
            else:
                try:
                    self.groups = self._decode(raw)
                    return
                except (ValueError, UnicodeError, StorageError, RecursionError) as exc:
                    backup = self._backup_broken_file(raw)
                    self.groups = []
                    self.warnings.append(
                        f"configuration file was invalid ({exc}); the original was preserved "
                        f"as {backup} and a fresh empty database was started"
                    )
            self._write()

    @staticmethod
    def _parse(data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
            raise StorageError('expected a JSON object like {"groups": [...]}')
        groups: list[dict[str, Any]] = []
        group_names: set[str] = set()
        for item in data["groups"]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise StorageError("invalid group entry: every group needs a string name")
            if not item["name"].strip() or item["name"] in group_names:
                raise StorageError("group names must be nonempty and unique")
            group_names.add(item["name"])
            bookmarks = item.get("bookmarks", [])
            if not isinstance(bookmarks, list):
                raise StorageError(f'group "{item.get("name")}" has an invalid bookmarks list')
            cleaned = []
            bookmark_names: set[str] = set()
            for bm in bookmarks:
                if (
                    not isinstance(bm, dict)
                    or not isinstance(bm.get("name"), str)
                    or not isinstance(bm.get("path"), str)
                ):
                    raise StorageError(
                        f'invalid bookmark in group "{item["name"]}": '
                        "every bookmark needs string name and path"
                    )
                if not bm["name"].strip() or bm["name"] in bookmark_names:
                    raise StorageError(f'bookmark names in "{item["name"]}" must be nonempty and unique')
                if not bm["path"].strip() or "\x00" in bm["path"]:
                    raise StorageError("bookmark paths must be nonempty and contain no NUL characters")
                bookmark_names.add(bm["name"])
                cleaned.append({"name": bm["name"], "path": bm["path"]})
            groups.append({"name": item["name"], "bookmarks": cleaned})
        return groups

    def _backup_broken_file(self, raw: bytes) -> Path:
        for counter in range(1, 100):
            suffix = ".invalid" if counter == 1 else f".invalid-{counter}"
            candidate = self.path.with_name(self.path.name + suffix)
            try:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            except OSError as exc:
                raise StorageError(f"cannot back up invalid configuration {self.path}: {exc}") from exc
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            return candidate
        raise StorageError(f"cannot back up invalid configuration {self.path}: backup names exhausted")

    def save(self) -> None:
        """Reject stale edits and roll back changes that could not be saved."""
        previous = self._raw
        try:
            with self._locked():
                current = self._read()
                if current != previous:
                    try:
                        self.groups = self._decode(current) if current is not None else []
                    except (ValueError, UnicodeError, StorageError, RecursionError) as exc:
                        raise StorageError(f"configuration changed and is invalid; refusing to overwrite: {exc}") from exc
                    self._raw = current
                    raise StorageError("configuration changed in another process; reloaded it, please retry")
                self._write()
        except BaseException:
            if self._raw == previous:
                self.groups = self._decode(previous) if previous is not None else []
            raise

    def _write(self) -> None:
        payload = (json.dumps({"groups": self.groups}, indent=2, ensure_ascii=False) + "\n").encode(
            "utf-8", errors="backslashreplace"
        )
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
            self._raw = payload
            try:
                dir_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError as exc:
                raise StorageError(f"saved {self.path}, but could not sync its directory: {exc}") from exc
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    def group_names(self) -> list[str]:
        return [group["name"] for group in self.groups]

    def _find_group(self, name: str) -> dict[str, Any] | None:
        for group in self.groups:
            if group["name"] == name:
                return group
        return None

    def _require_group(self, name: str) -> dict[str, Any]:
        group = self._find_group(name)
        if group is None:
            raise StorageError(f'unknown group "{name}"')
        return group

    def add_group(self, name: str) -> None:
        name = name.strip()
        if not name:
            raise StorageError("group name must not be empty")
        if self._find_group(name) is not None:
            raise StorageError(f'group "{name}" already exists')
        self.groups.append({"name": name, "bookmarks": []})
        self.save()

    def rename_group(self, old: str, new: str) -> None:
        old = old.strip()
        new = new.strip()
        group = self._require_group(old)
        if not new:
            raise StorageError("group name must not be empty")
        if new != old:
            if self._find_group(new) is not None:
                raise StorageError(f'group "{new}" already exists')
            group["name"] = new  # bookmarks are kept untouched
            self.save()

    def remove_group(self, name: str) -> int:
        """Remove a group entry.  Returns how many bookmarks it held."""
        group = self._require_group(name)
        count = len(group["bookmarks"])
        self.groups = [g for g in self.groups if g is not group]
        self.save()
        return count

    def bookmarks(self, group: str) -> list[dict[str, Any]]:
        return self._require_group(group)["bookmarks"]

    def add_bookmark(
        self, group: str, name: str, raw_path: str, *, create_group: bool = False
    ) -> dict[str, Any]:
        group = group.strip()
        if not group:
            raise StorageError("group name must not be empty")
        owner = self._find_group(group) if create_group else self._require_group(group)
        name = name.strip()
        if not name:
            raise StorageError("bookmark name must not be empty")
        if owner is not None and any(bm["name"] == name for bm in owner["bookmarks"]):
            raise StorageError(f'bookmark "{name}" already exists in group "{group}"')
        path = normalize_path(raw_path)
        if not os.path.isdir(path):
            raise StorageError(f"not an existing directory: {path}")
        if owner is None:
            owner = {"name": group, "bookmarks": []}
            self.groups.append(owner)
        entry = {"name": name, "path": path}
        owner["bookmarks"].append(entry)
        self.save()
        return entry

    def update_bookmark(
        self,
        group: str,
        name: str,
        *,
        new_group: str | None = None,
        new_name: str | None = None,
        new_path: str | None = None,
    ) -> dict[str, Any]:
        """Rename a bookmark, change its path, and/or move it to another group."""
        source = self._require_group(group)
        bm = next((b for b in source["bookmarks"] if b["name"] == name), None)
        if bm is None:
            raise StorageError(f'no bookmark "{name}" in group "{group}"')
        target_name = (new_name if new_name is not None else name).strip()
        if not target_name:
            raise StorageError("bookmark name must not be empty")
        target_group = (new_group if new_group is not None else group).strip()
        target = self._require_group(target_group)
        if any(b is not bm and b["name"] == target_name for b in target["bookmarks"]):
            raise StorageError(f'bookmark "{target_name}" already exists in group "{target_group}"')
        if new_path is not None:
            path = normalize_path(new_path)
            if not os.path.isdir(path):
                raise StorageError(f"not an existing directory: {path}")
            bm["path"] = path
        bm["name"] = target_name
        if target is not source:
            source["bookmarks"] = [b for b in source["bookmarks"] if b is not bm]
            target["bookmarks"].append(bm)
        self.save()
        return bm

    def remove_bookmark(self, group: str, name: str) -> dict[str, Any]:
        """Remove a saved reference.  The directory itself is never touched."""
        owner = self._require_group(group)
        bm = next((b for b in owner["bookmarks"] if b["name"] == name), None)
        if bm is None:
            raise StorageError(f'no bookmark "{name}" in group "{group}"')
        owner["bookmarks"] = [b for b in owner["bookmarks"] if b is not bm]
        self.save()
        return bm


__all__ = [
    "StorageError",
    "Store",
    "default_name",
    "expand_user_path",
    "normalize_path",
]
