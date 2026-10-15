"""Exercise the installer's shell setup without changing the user's shell files."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('shell', ['bash', 'zsh'])
def test_installer_quotes_paths_and_does_not_duplicate_rc(tmp_path, shell):
    if shutil.which(shell) is None:
        pytest.skip(f'{shell} not installed')
    root = Path(__file__).resolve().parents[1]
    source = (root / 'install.sh').read_text()
    helper = source[source.index('integrate_rc() {'):source.index('\ncase "$(basename')]
    data = tmp_path / 'data $(touch INJECTED) `touch INJECTED` "quote" \'雪'
    data.mkdir()
    (data / 'pn.sh').write_text('PATHNEST_TEST_LOADED=yes\n')
    rc = tmp_path / '.rc'
    rc.write_text('# existing config\n')
    env = dict(os.environ, DATA_DIR=str(data))
    script = f'set -eu\nsay() {{ :; }}\ndie() {{ exit 1; }}\n{helper}\nintegrate_rc "$1"\nintegrate_rc "$1"\n'
    result = subprocess.run(['bash', '-c', script, 'test', str(rc)], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert rc.read_text().startswith('# existing config\n')
    assert rc.read_text().count('# >>> pathnest shell integration >>>') == 1
    result = subprocess.run([shell, '-c', '. "$1"; [ "$PATHNEST_TEST_LOADED" = yes ]', 'test', str(rc)],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'INJECTED').exists()


def test_relative_xdg_data_home_uses_home_default(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = (root / 'install.sh').read_text()
    setup = source[source.index('case "${XDG_DATA_HOME:-}"'):source.index('\nsay()')]
    env = dict(os.environ, HOME=str(tmp_path), XDG_DATA_HOME='relative')
    result = subprocess.run(['bash', '-c', setup + '\nprintf "%s" "$DATA_DIR"'],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(tmp_path / '.local/share/pathnest')


def test_install_replaces_running_binary_atomically(tmp_path):
    root = Path(__file__).resolve().parents[1]
    install_dir = tmp_path / '.local/bin'
    install_dir.mkdir(parents=True)
    installed = install_dir / 'pn-bin'
    shutil.copy2(shutil.which('sleep'), installed)
    candidate = tmp_path / 'candidate'
    candidate.write_text('#!/bin/sh\nprintf "pathnest 9.0.0\\n"\n')
    candidate.chmod(0o755)
    env = dict(os.environ, HOME=str(tmp_path), XDG_DATA_HOME=str(tmp_path / 'data'), SHELL='/bin/bash')
    running = subprocess.Popen([str(installed), '30'])
    try:
        result = subprocess.run(['bash', str(root / 'install.sh'), str(candidate)],
                                env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert running.poll() is None
        assert subprocess.check_output([str(installed), '--version'], text=True).strip() == 'pathnest 9.0.0'
        assert not list(install_dir.glob('.pn-bin.*'))
    finally:
        running.terminate()
        running.wait(timeout=5)
