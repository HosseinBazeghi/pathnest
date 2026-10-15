"""Updater integration tests use local release bytes and isolated installations."""
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
from urllib.error import HTTPError, URLError

import pytest

from pathnest import update
from pathnest.cli import main


def executable(path, version):
    path.write_text(f'#!/bin/sh\nprintf "pathnest {version}\\n"\n')
    path.chmod(0o755)
    return path


def archive_bytes(version='1.3.0', extra=None, omit=None):
    contents = {f'{update.BUNDLE}/pn-bin': f'#!/bin/sh\nprintf "pathnest {version}\\n"\n'.encode(),
                f'{update.BUNDLE}/shell/pn.sh': b'# updated wrapper\npn() { pn-bin "$@"; }\n'}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, data in contents.items():
            if name == omit:
                continue
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if extra:
            archive.addfile(extra)
    return output.getvalue()


@pytest.fixture
def release_setup(tmp_path, monkeypatch):
    binary = executable(tmp_path / 'pn-bin', '1.0.0')
    wrapper = tmp_path / 'pn.sh'
    wrapper.write_text('# old wrapper\n')
    metadata = {'tag_name': 'v1.3.0', 'draft': False, 'prerelease': False,
                'assets': [{'name': update.ASSET}, {'name': update.ASSET + '.sha256'}]}
    state = {'archive': archive_bytes(), 'metadata': metadata, 'requests': []}
    def fetch(url, destination, limit):
        state['requests'].append(url)
        if url == update.LATEST_URL:
            data = json.dumps(state['metadata']).encode()
        elif url.endswith('.sha256'):
            checksum = state.get('checksum', hashlib.sha256(state['archive']).hexdigest())
            data = f'{checksum}  {update.ASSET}\n'.encode()
        else:
            data = state['archive']
        assert len(data) <= limit
        destination.write_bytes(data)
        return hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(update, '_fetch', fetch)
    monkeypatch.setattr(update, '__version__', '1.0.0')
    monkeypatch.setattr(update, '_check_platform', lambda: None)
    monkeypatch.setattr(update, '_installation', lambda: (binary, wrapper))
    return state, binary, wrapper


def test_update_installs_verified_binary_and_wrapper_without_touching_config(release_setup, tmp_path):
    state, binary, wrapper = release_setup
    config = tmp_path / 'bookmarks.json'
    config.write_text('deliberately invalid database')
    assert main(['--config', str(config), 'update']) == 0
    assert update._binary_version(binary) == '1.3.0'
    assert wrapper.read_text().startswith('# updated wrapper')
    assert config.read_text() == 'deliberately invalid database'
    assert not list(tmp_path.glob('*.invalid*'))
    assert not list(tmp_path.glob('.pathnest-update-*'))
    assert all(url.startswith(('https://api.github.com/', f'{update.RELEASES_URL}/download/')) for url in state['requests'])


def test_update_check_does_not_download_or_require_installation(release_setup, monkeypatch, tmp_path, capsys):
    state, binary, wrapper = release_setup
    monkeypatch.setattr(update, '_installation', lambda: pytest.fail('check must not inspect installation'))
    config = tmp_path / 'does-not-exist.json'
    assert main(['update', '--check', '--config', str(config)]) == 0
    assert not config.exists()
    assert state['requests'] == [update.LATEST_URL]
    assert 'Update available' in capsys.readouterr().out
    assert update._binary_version(binary) == '1.0.0'


@pytest.mark.parametrize('tag', ['v1.0.0', 'v0.9.0'])
def test_update_never_downgrades(release_setup, tag):
    state, binary, _ = release_setup
    state['metadata']['tag_name'] = tag
    assert update.run_update() == 0
    assert state['requests'] == [update.LATEST_URL]
    assert update._binary_version(binary) == '1.0.0'


@pytest.mark.parametrize('failure', ['checksum', 'missing-asset', 'bad-version', 'missing-file', 'broken-binary', 'bad-shell'])
def test_invalid_release_preserves_installation(release_setup, failure):
    state, binary, wrapper = release_setup
    before = (binary.read_bytes(), wrapper.read_bytes())
    if failure == 'checksum':
        state['checksum'] = '0' * 64
    elif failure == 'missing-asset':
        state['metadata']['assets'] = []
    elif failure == 'bad-version':
        state['archive'] = archive_bytes('1.2.0')
    elif failure == 'missing-file':
        state['archive'] = archive_bytes(omit=f'{update.BUNDLE}/shell/pn.sh')
    elif failure == 'broken-binary':
        state['archive'] = archive_bytes('bad')
    elif failure == 'bad-shell':
        state['archive'] = state['archive'][:20]  # Valid checksum, truncated compressed stream.
    assert update.run_update() == 1
    assert (binary.read_bytes(), wrapper.read_bytes()) == before


@pytest.mark.parametrize('kind', ['traversal', 'absolute', 'symlink', 'hardlink', 'duplicate', 'oversized'])
def test_archive_rejects_unsafe_entries(release_setup, kind):
    state, binary, wrapper = release_setup
    member = tarfile.TarInfo('extra')
    if kind == 'traversal':
        member.name = '../outside'
    elif kind == 'absolute':
        member.name = '/tmp/outside'
    elif kind in ('symlink', 'hardlink'):
        member.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
        member.linkname = '/tmp/outside'
    elif kind == 'duplicate':
        member.name = f'{update.BUNDLE}/pn-bin'
    elif kind == 'oversized':
        member.size = update.MAX_DOWNLOAD * 3
    state['archive'] = (gzip.compress(member.tobuf() + b'\0' * 1024)
                        if kind == 'oversized' else archive_bytes(extra=member))
    before = (binary.read_bytes(), wrapper.read_bytes())
    assert update.run_update() == 1
    assert (binary.read_bytes(), wrapper.read_bytes()) == before


def test_commit_failure_restores_wrapper(release_setup, monkeypatch):
    _, binary, wrapper = release_setup
    before = (binary.read_bytes(), wrapper.read_bytes())
    replace = os.replace
    def fail_binary(source, target):
        if Path(target) == binary and Path(source).name == 'new':
            raise OSError('simulated disk error')
        return replace(source, target)
    monkeypatch.setattr(update.os, 'replace', fail_binary)
    assert update.run_update() == 1
    assert (binary.read_bytes(), wrapper.read_bytes()) == before
    assert not list(binary.parent.glob('.pathnest-update-*'))


def test_concurrent_update_does_not_download(release_setup):
    state, binary, _ = release_setup
    with update._update_lock(binary):
        assert update.run_update() == 1
    assert state['requests'] == [update.LATEST_URL]


def test_stale_process_does_not_downgrade_new_installation(release_setup):
    state, binary, _ = release_setup
    executable(binary, '1.4.0')
    assert update.run_update() == 0
    assert state['requests'] == [update.LATEST_URL]
    assert update._binary_version(binary) == '1.4.0'


@pytest.mark.parametrize('version,expected', [('v1.10.0', (1, 10, 0)), ('1.2.3', (1, 2, 3))])
def test_numeric_versions(version, expected):
    assert update.version_tuple(version) == expected


@pytest.mark.parametrize('version', ['main', 'v1.2.3-beta', '../1.2.3', '', None])
def test_invalid_version_rejected(version):
    with pytest.raises(update.UpdateError):
        update.version_tuple(version)


@pytest.mark.parametrize('error,message', [
    (HTTPError(update.LATEST_URL, 404, 'missing', {}, None), 'no published release'),
    (HTTPError(update.LATEST_URL, 403, 'limited', {}, None), 'rate limit'),
    (URLError('offline'), 'connection'),
])
def test_network_errors_are_actionable(tmp_path, monkeypatch, error, message):
    class Opener:
        def open(self, *args, **kwargs):
            raise error
    monkeypatch.setattr(update, 'build_opener', lambda *args: Opener())
    with pytest.raises(update.UpdateError, match=message):
        update._fetch(update.LATEST_URL, tmp_path / 'response', 100)


def test_download_stream_is_bounded(tmp_path, monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            return io.BytesIO(b'x' * 100)
    monkeypatch.setattr(update, 'build_opener', lambda *args: Opener())
    with pytest.raises(update.UpdateError, match='size limit'):
        update._fetch(update.LATEST_URL, tmp_path / 'response', 50)


@pytest.mark.parametrize('url', ['http://github.com/file', 'https://example.com/file', 'https://github.com@evil.test/file'])
def test_untrusted_redirect_rejected(url):
    with pytest.raises(update.UpdateError):
        update._HTTPSRedirects().redirect_request(None, None, 302, '', {}, url)


def test_source_install_is_not_overwritten(monkeypatch):
    monkeypatch.delattr(update.sys, 'frozen', raising=False)
    with pytest.raises(update.UpdateError, match='standalone installation'):
        update._installation()


def test_child_resets_pyinstaller_environment(monkeypatch):
    monkeypatch.setenv('LD_LIBRARY_PATH', '/tmp/_MEI/lib')
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', '/system/lib')
    monkeypatch.setenv('PATHNEST_RESULT_FILE', '/tmp/selection')
    env = update._child_environment()
    assert env['LD_LIBRARY_PATH'] == '/system/lib'
    assert env['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert 'PATHNEST_RESULT_FILE' not in env


def test_installation_uses_wrapper_loaded_from_custom_data_directory(tmp_path, monkeypatch):
    binary = tmp_path / '.local/bin/pn-bin'
    binary.parent.mkdir(parents=True)
    executable(binary, '1.0.0')
    wrapper = tmp_path / 'custom data/pn.sh'
    wrapper.parent.mkdir()
    wrapper.write_text('# wrapper')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('PATHNEST_SHELL_FILE', str(wrapper))
    monkeypatch.setattr(update.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(update.sys, 'executable', str(binary))
    assert update._installation() == (binary, wrapper)
    wrapper.unlink()
    wrapper.symlink_to(binary)
    with pytest.raises(update.UpdateError, match='regular file'):
        update._installation()


@pytest.mark.parametrize('system,machine,libc', [
    ('Linux', 'aarch64', ('glibc', '2.35')),
    ('Linux', 'x86_64', ('musl', '1.2')),
    ('Linux', 'x86_64', ('glibc', '2.31')),
    ('Darwin', 'x86_64', ('', '')),
])
def test_platform_rejects_incompatible_prebuilt_bundle(monkeypatch, system, machine, libc):
    monkeypatch.setattr(update.platform, 'system', lambda: system)
    monkeypatch.setattr(update.platform, 'machine', lambda: machine)
    monkeypatch.setattr(update.platform, 'libc_ver', lambda: libc)
    with pytest.raises(update.UpdateError, match='build from source'):
        update._check_platform()
