"""Tests for the JSON persistence layer and its safety guarantees."""

import json
import os

import pytest

from pathnest.storage import StorageError, Store, default_name, normalize_path


def test_first_run_initializes_empty_database(tmp_path):
    cfg = tmp_path / "sub" / "bookmarks.json"
    store = Store(cfg)
    assert store.groups == []
    assert cfg.exists()
    assert json.loads(cfg.read_text()) == {"groups": []}


def test_group_add_duplicate_and_rename(tmp_path):
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Research")
    store.add_group("University")
    with pytest.raises(StorageError):
        store.add_group("Research")
    with pytest.raises(StorageError):
        store.add_group("   ")
    store.rename_group("University", "Uni")
    assert store.group_names() == ["Research", "Uni"]
    with pytest.raises(StorageError):
        store.rename_group("Uni", "Research")


def test_rename_group_preserves_bookmarks(tmp_path):
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Research")
    store.add_bookmark("Research", "Main", str(tmp_path))
    store.add_bookmark("Research", "Results", str(tmp_path))
    store.rename_group("Research", "Science")
    assert store.group_names() == ["Science"]
    names = [b["name"] for b in store.bookmarks("Science")]
    assert names == ["Main", "Results"]


def test_remove_group_with_multiple_bookmarks(tmp_path):
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Research")
    for i in range(3):
        store.add_bookmark("Research", f"B{i}", str(tmp_path))
    removed = store.remove_group("Research")
    assert removed == 3
    assert store.groups == []
    with pytest.raises(StorageError):
        store.remove_group("Research")


def test_persistence_across_restart(tmp_path):
    cfg = tmp_path / "bookmarks.json"
    Store(cfg).add_group("Research")
    store = Store(cfg)
    assert store.group_names() == ["Research"]
    store.add_bookmark("Research", "Main", str(tmp_path))
    reloaded = Store(cfg)
    assert reloaded.bookmarks("Research") == [{"name": "Main", "path": str(tmp_path)}]


def test_invalid_json_is_preserved_not_destroyed(tmp_path):
    cfg = tmp_path / "bookmarks.json"
    cfg.write_text("{ this is not json", encoding="utf-8")
    store = Store(cfg)
    assert store.groups == []
    assert len(store.warnings) == 1
    backup = tmp_path / "bookmarks.json.invalid"
    assert backup.read_text() == "{ this is not json"
    assert json.loads(cfg.read_text()) == {"groups": []}


def test_wrong_schema_is_preserved_not_destroyed(tmp_path):
    cfg = tmp_path / "bookmarks.json"
    cfg.write_text(json.dumps({"nope": 42}), encoding="utf-8")
    store = Store(cfg)
    assert store.groups == []
    assert (tmp_path / "bookmarks.json.invalid").read_text() == json.dumps({"nope": 42})


def test_binary_garbage_config_is_preserved_not_destroyed(tmp_path):
    cfg = tmp_path / "bookmarks.json"
    cfg.write_bytes(b"\x00\x9c\xff\xfa\x01not utf8 at all")
    store = Store(cfg)
    assert store.groups == []
    assert store.warnings, "expected a warning about the unreadable file"
    assert (tmp_path / "bookmarks.json.invalid").read_bytes() == b"\x00\x9c\xff\xfa\x01not utf8 at all"
    assert json.loads(cfg.read_text()) == {"groups": []}


def test_unwritable_save_raises_friendly_error(tmp_path):
    cfg = tmp_path / "bookmarks.json"
    store = Store(cfg)
    os.chmod(tmp_path, 0o500)  # make the directory read-only
    try:
        with pytest.raises(StorageError):
            store.add_group("Research")
    finally:
        os.chmod(tmp_path, 0o700)
    # no temp files leaked by the failed save
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_bookmark_requires_existing_directory(tmp_path):
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Research")
    with pytest.raises(StorageError):
        store.add_bookmark("Research", "Missing", str(tmp_path / "does-not-exist"))
    with pytest.raises(StorageError):
        store.add_bookmark("Research", "File", __file__)


def test_bookmark_duplicate_name_rejected(tmp_path):
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Research")
    store.add_bookmark("Research", "Main", str(tmp_path))
    with pytest.raises(StorageError):
        store.add_bookmark("Research", "Main", str(tmp_path))


def test_paths_with_spaces_and_shell_metacharacters(tmp_path):
    weird = tmp_path / 'my project $(echo hi) & "stuff"'
    weird.mkdir()
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Research")
    entry = store.add_bookmark("Research", "Weird", str(weird))
    assert entry["path"] == str(weird)
    reloaded = Store(tmp_path / "bookmarks.json")
    assert reloaded.bookmarks("Research")[0]["path"] == str(weird)


def test_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "proj").mkdir()
    store = Store(tmp_path / "db.json")
    store.add_group("Personal")
    entry = store.add_bookmark("Personal", "Proj", "~/proj")
    assert entry["path"] == str(tmp_path / "proj")


def test_update_bookmark_rename_move_and_path(tmp_path):
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Research")
    store.add_group("University")
    store.add_bookmark("Research", "Main", str(tmp_path))
    new_dir = tmp_path / "elsewhere"
    new_dir.mkdir()

    moved = store.update_bookmark(
        "Research", "Main", new_group="University", new_name="Thesis", new_path=str(new_dir)
    )
    assert moved == {"name": "Thesis", "path": str(new_dir)}
    assert store.bookmarks("Research") == []
    assert store.bookmarks("University") == [moved]

    with pytest.raises(StorageError):
        store.update_bookmark("University", "Thesis", new_path=str(tmp_path / "missing"))
    with pytest.raises(StorageError):
        store.update_bookmark("University", "Nope")


def test_remove_never_deletes_real_directories(tmp_path):
    real_dir = tmp_path / "real project"
    real_dir.mkdir()
    important = real_dir / "important.txt"
    important.write_text("keep me", encoding="utf-8")

    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Research")
    store.add_bookmark("Research", "Real", str(real_dir))
    store.remove_bookmark("Research", "Real")
    store.remove_group("Research")

    assert real_dir.is_dir()
    assert important.read_text() == "keep me"


def test_atomic_save_leaves_valid_json(tmp_path):
    store = Store(tmp_path / "bookmarks.json")
    store.add_group("Research")
    store.add_bookmark("Research", "Main", str(tmp_path))
    data = json.loads((tmp_path / "bookmarks.json").read_text())
    assert data["groups"][0]["bookmarks"][0]["name"] == "Main"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_normalize_and_default_name():
    assert normalize_path("~/x/../y").endswith("/y")
    assert default_name("/home/user/research/main") == "main"
    assert default_name(str(os.sep)) == os.sep


@pytest.mark.parametrize('raw', ['', '   ', '\x00bad'])
def test_invalid_paths_do_not_save_current_directory(tmp_path, raw):
    store = Store(tmp_path / 'db.json')
    store.add_group('G')
    with pytest.raises(StorageError):
        store.add_bookmark('G', 'B', raw)
    assert store.bookmarks('G') == []


def test_relative_paths_and_store_location_survive_cwd_changes(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.chdir(tmp_path)
    store = Store('db.json')
    store.add_group('G')
    store.add_bookmark('G', 'B', 'project')
    monkeypatch.chdir(other)
    store.add_group('Other')
    assert store.bookmarks('G')[0]['path'] == str(project)
    assert not (other / 'db.json').exists()
    assert Store(tmp_path / 'db.json').group_names() == ['G', 'Other']


def test_paths_preserve_symlinks_and_trailing_spaces(tmp_path):
    target = tmp_path / '目标 '
    target.mkdir()
    link = tmp_path / 'link '
    link.symlink_to(target, target_is_directory=True)
    store = Store(tmp_path / 'db.json')
    store.add_group('G')
    assert store.add_bookmark('G', 'B', str(link))['path'] == str(link)
    target.rmdir()
    with pytest.raises(StorageError):
        store.add_bookmark('G', 'Broken', str(link))


def test_legacy_relative_paths_are_anchored_to_config_directory(tmp_path, monkeypatch):
    cfg = tmp_path / 'db.json'
    cfg.write_text(json.dumps({'groups': [{'name': 'G', 'bookmarks': [{'name': 'B', 'path': 'project'}]}]}))
    monkeypatch.chdir('/')
    assert Store(cfg).bookmarks('G')[0]['path'] == str(tmp_path / 'project')


def test_failed_save_rolls_back_all_edits(tmp_path, monkeypatch):
    store = Store(tmp_path / 'db.json')
    store.add_bookmark('G', 'B', str(tmp_path), create_group=True)
    original = store.path.read_bytes()

    def fail(*args):
        raise PermissionError('denied')

    monkeypatch.setattr(os, 'replace', fail)
    operations = [
        lambda: store.add_group('New'),
        lambda: store.rename_group('G', 'Renamed'),
        lambda: store.remove_group('G'),
        lambda: store.add_bookmark('New', 'B', str(tmp_path), create_group=True),
        lambda: store.update_bookmark('G', 'B', new_name='Renamed'),
        lambda: store.remove_bookmark('G', 'B'),
    ]
    for operation in operations:
        with pytest.raises(StorageError, match='denied'):
            operation()
        assert store.groups == json.loads(original)['groups']
        assert store.path.read_bytes() == original
        assert not list(tmp_path.glob('*.tmp'))


def test_stale_save_cannot_overwrite_other_process(tmp_path):
    first = Store(tmp_path / 'db.json')
    second = Store(first.path)
    first.add_group('First')
    with pytest.raises(StorageError, match='changed in another process'):
        second.add_group('Second')
    assert second.group_names() == ['First']
    second.add_group('Second')
    assert Store(first.path).group_names() == ['First', 'Second']


def test_external_corruption_is_not_overwritten_by_stale_save(tmp_path):
    store = Store(tmp_path / 'db.json')
    store.path.write_text('broken')
    with pytest.raises(StorageError, match='refusing to overwrite'):
        store.add_group('G')
    assert store.path.read_text() == 'broken'
    assert store.groups == []


def test_config_symlink_is_preserved(tmp_path):
    target = tmp_path / 'target.json'
    link = tmp_path / 'link.json'
    link.symlink_to(target)
    Store(link).add_group('G')
    assert link.is_symlink()
    assert Store(target).group_names() == ['G']


def test_backup_never_overwrites_existing_dangling_symlink(tmp_path):
    cfg = tmp_path / 'db.json'
    cfg.write_text('broken')
    existing = tmp_path / 'db.json.invalid'
    existing.symlink_to(tmp_path / 'missing')
    Store(cfg)
    assert existing.is_symlink()
    assert (tmp_path / 'db.json.invalid-2').read_text() == 'broken'


def test_failed_recovery_leaves_original_in_place(tmp_path, monkeypatch):
    cfg = tmp_path / 'db.json'
    cfg.write_text('broken')
    def fail(*args):
        raise PermissionError('denied')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(StorageError):
        Store(cfg)
    assert cfg.read_text() == 'broken'
    assert (tmp_path / 'db.json.invalid').read_text() == 'broken'


@pytest.mark.parametrize('groups', [
    [{'name': 'G'}, {'name': 'G'}],
    [{'name': ' '}],
    [{'name': 'G', 'bookmarks': [{'name': 'B', 'path': '/tmp'}, {'name': 'B', 'path': '/tmp'}]}],
    [{'name': 'G', 'bookmarks': [{'name': 'B', 'path': ''}]}],
])
def test_ambiguous_schema_is_backed_up(tmp_path, groups):
    cfg = tmp_path / 'db.json'
    original = json.dumps({'groups': groups})
    cfg.write_text(original)
    assert Store(cfg).warnings
    assert (tmp_path / 'db.json.invalid').read_text() == original


def test_storage_permissions(tmp_path):
    import stat
    store = Store(tmp_path / 'db.json')
    store.add_group('G')
    for path in (store.path, tmp_path / 'db.json.lock'):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_non_utf8_directory_roundtrip(tmp_path):
    target = tmp_path / os.fsdecode(b'project-\xff')
    target.mkdir()
    store = Store(tmp_path / 'db.json')
    store.add_bookmark('G', 'B', str(target), create_group=True)
    assert Store(store.path).bookmarks('G')[0]['path'] == str(target)


def test_fifo_config_rejected_without_blocking(tmp_path):
    fifo = tmp_path / 'db.json'
    os.mkfifo(fifo)
    with pytest.raises(StorageError, match='regular file'):
        Store(fifo)


def test_processes_serialize_conflicting_writes(tmp_path):
    import multiprocessing
    cfg = tmp_path / 'db.json'
    Store(cfg)
    ctx = multiprocessing.get_context('fork')
    def edit(connection, name):
        store = Store(cfg)
        connection.send('ready')
        connection.recv()
        try:
            store.add_group(name)
            connection.send('saved')
        except StorageError:
            connection.send('conflict')
        connection.close()
    processes = []
    parents = []
    try:
        for name in ('A', 'B'):
            parent, child = ctx.Pipe()
            process = ctx.Process(target=edit, args=(child, name))
            process.start()
            child.close()
            processes.append(process)
            parents.append(parent)
        for parent in parents:
            assert parent.poll(10)
            assert parent.recv() == 'ready'
        for parent in parents:
            parent.send('go')
        results = []
        for parent in parents:
            assert parent.poll(10)
            results.append(parent.recv())
        assert sorted(results) == ['conflict', 'saved']
        assert len(Store(cfg).groups) == 1
    finally:
        for parent in parents:
            parent.close()
        for process in processes:
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join()
            assert process.exitcode == 0


def test_fsync_failure_keeps_previous_database(tmp_path, monkeypatch):
    store = Store(tmp_path / 'db.json')
    original = store.path.read_bytes()
    def fail(fd):
        raise OSError('disk error')
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(StorageError, match='disk error'):
        store.add_group('G')
    assert store.path.read_bytes() == original
    assert store.groups == []
    assert not list(tmp_path.glob('*.tmp'))


def test_unreadable_config_is_not_recovered_or_replaced(tmp_path):
    cfg = tmp_path / 'db.json'
    cfg.write_text('private')
    cfg.chmod(0)
    try:
        with pytest.raises(StorageError, match='cannot read'):
            Store(cfg)
        assert not list(tmp_path.glob('*.invalid*'))
    finally:
        cfg.chmod(0o600)
    assert cfg.read_text() == 'private'


def test_directory_sync_failure_reports_committed_write(tmp_path, monkeypatch):
    import stat
    store = Store(tmp_path / 'db.json')
    fsync = os.fsync
    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError('directory sync failed')
        fsync(fd)
    monkeypatch.setattr(os, 'fsync', fail_directory)
    with pytest.raises(StorageError, match='saved .* could not sync'):
        store.add_group('G')
    assert store.group_names() == ['G']
    assert Store(store.path).group_names() == ['G']


def test_lock_symlink_does_not_modify_target(tmp_path):
    cfg = tmp_path / 'db.json'
    target = tmp_path / 'important'
    target.write_text('keep')
    (tmp_path / 'db.json.lock').symlink_to(target)
    with pytest.raises(StorageError):
        Store(cfg)
    assert target.read_text() == 'keep'
    assert not cfg.exists()
