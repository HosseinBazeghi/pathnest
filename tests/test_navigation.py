"""Tests for the shell-navigation protocol and the Bash/Zsh wrapper.

The wrapper is exercised with a stub `pn-bin` that simulates the TUI
selecting (or not selecting) a directory, so navigation can be verified
end-to-end without an interactive terminal.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from pathnest.cli import emit_selected_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PN_SH = PROJECT_ROOT / "shell" / "pn.sh"

STUB = """\
#!/usr/bin/env bash
# Stub pn-bin used only by the test-suite.
if [ "$1" = "--select" ]; then
    printf '%s\\n' "$2" > "$PATHNEST_RESULT_FILE"
fi
exit 0
"""


@pytest.fixture()
def shell_env(tmp_path):
    """A stub pn-bin on PATH plus an isolated TMPDIR."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "pn-bin"
    stub.write_text(STUB)
    stub.chmod(0o755)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    start = tmp_path / "start"
    start.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["TMPDIR"] = str(tmpdir)
    env.pop("PATHNEST_RESULT_FILE", None)
    env.pop("PATHNEST_CONFIG_FILE", None)
    return env, start, tmpdir


def _run_wrapper(env, start, body, shell="bash"):
    script = f'. "{PN_SH}"\ncd "{start}"\n{body}\nrc=$?\nprintf "RC:%s\\n" "$rc"\nprintf "CWD:%s\\n" "$PWD"\n'
    return subprocess.run(
        [shell, "-c", script], env=env, capture_output=True, text=True, timeout=30
    )


def test_wrapper_changes_directory_on_selection(shell_env):
    env, start, tmpdir = shell_env
    target = start.parent / "dir with spaces"
    target.mkdir()
    proc = _run_wrapper(env, start, f'pn --select "{target}"')
    assert proc.returncode == 0, proc.stderr
    assert f"CWD:{target}" in proc.stdout
    assert list(tmpdir.glob("pathnest.*")) == []  # no temp files left behind


def test_wrapper_leaves_cwd_when_cancelled(shell_env):
    env, start, tmpdir = shell_env
    proc = _run_wrapper(env, start, "pn --no-selection")
    assert proc.returncode == 0, proc.stderr
    assert f"CWD:{start}" in proc.stdout
    assert list(tmpdir.glob("pathnest.*")) == []


def test_wrapper_rejects_missing_directory(shell_env):
    env, start, tmpdir = shell_env
    gone = start.parent / "deleted dir"
    proc = _run_wrapper(env, start, f'pn --select "{gone}"')
    assert "RC:1" in proc.stdout  # wrapper reports failure...
    assert f"CWD:{start}" in proc.stdout  # ...and does not cd anywhere
    assert "no longer exists" in proc.stderr


def test_wrapper_never_evaluates_paths_as_shell_code(shell_env):
    env, start, tmpdir = shell_env
    target = start.parent / 'pwned $(rm -rf ~) "quoted"'
    target.mkdir()
    proc = _run_wrapper(env, start, f"pn --select '{target}'")
    assert proc.returncode == 0, proc.stderr
    assert f"CWD:{target}" in proc.stdout


def test_wrapper_cli_commands_do_not_change_directory(shell_env):
    env, start, tmpdir = shell_env
    proc = _run_wrapper(env, start, "pn add --group Research >/dev/null")
    assert proc.returncode == 0, proc.stderr
    assert f"CWD:{start}" in proc.stdout


def test_wrapper_works_in_zsh(shell_env):
    if shutil.which("zsh") is None:
        pytest.skip("zsh not installed")
    env, start, tmpdir = shell_env
    target = start.parent / "zsh target"
    target.mkdir()
    proc = _run_wrapper(env, start, f'pn --select "{target}"', shell="zsh")
    assert proc.returncode == 0, proc.stderr
    assert f"CWD:{target}" in proc.stdout


def test_emit_selected_path_writes_result_file(tmp_path, monkeypatch):
    result_file = tmp_path / "results" / "selected"
    monkeypatch.setenv("PATHNEST_RESULT_FILE", str(result_file))
    emit_selected_path("/home/user/dir with spaces")
    assert result_file.read_text() == "/home/user/dir with spaces\n"
    assert stat.S_IMODE(result_file.stat().st_mode) == 0o600


def test_emit_selected_path_prints_to_stdout_without_env(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PATHNEST_RESULT_FILE", raising=False)
    assert emit_selected_path("/opt/projects") is True
    assert capsys.readouterr().out == "/opt/projects\n"


def test_emit_selected_path_survives_unwritable_result_file(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file", encoding="utf-8")
    monkeypatch.setenv("PATHNEST_RESULT_FILE", str(blocker / "result"))
    assert emit_selected_path("/opt/projects") is False
    captured = capsys.readouterr()
    assert captured.out == ""  # path must not leak to stdout in wrapper mode
    assert "cannot write" in captured.err


@pytest.mark.parametrize('shell', ['bash', 'zsh'])
@pytest.mark.parametrize('name', ['trailing\n\n', ' leading and trailing ', '雪\nproject', '$(touch INJECTED)`touch INJECTED`'])
def test_wrapper_preserves_unusual_paths(shell_env, shell, name):
    import shlex
    if shutil.which(shell) is None:
        pytest.skip(f'{shell} not installed')
    env, start, tmpdir = shell_env
    target = start.parent / name
    target.mkdir()
    body = f'pn --select {shlex.quote(str(target))}\n[ "$PWD" = {shlex.quote(str(target))} ]'
    proc = _run_wrapper(env, start, body, shell=shell)
    assert 'RC:0' in proc.stdout, proc.stderr
    assert not (start / 'INJECTED').exists()
    assert not list(tmpdir.iterdir())


@pytest.mark.parametrize('shell', ['bash', 'zsh'])
def test_wrapper_failed_command_does_not_cd_and_cleans_up(shell_env, shell):
    if shutil.which(shell) is None:
        pytest.skip(f'{shell} not installed')
    env, start, tmpdir = shell_env
    stub = Path(env['PATH'].split(os.pathsep)[0]) / 'pn-bin'
    stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$1" > "$PATHNEST_RESULT_FILE"\nexit 7\n')
    proc = _run_wrapper(env, start, f'pn "{start.parent}"', shell=shell)
    assert 'RC:7' in proc.stdout
    assert f'CWD:{start}' in proc.stdout
    assert not list(tmpdir.iterdir())
    script = f'. "{PN_SH}"\nset -e\npn "{start.parent}"\n'
    proc = subprocess.run([shell, '-c', script], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 7
    assert not list(tmpdir.iterdir())


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo'])
def test_result_file_rejects_unsafe_targets(tmp_path, monkeypatch, capsys, kind):
    target = tmp_path / 'important'
    target.write_text('keep me')
    result = tmp_path / 'result'
    if kind == 'symlink':
        result.symlink_to(target)
    elif kind == 'hardlink':
        os.link(target, result)
    else:
        os.mkfifo(result)
    monkeypatch.setenv('PATHNEST_RESULT_FILE', str(result))
    assert emit_selected_path('/tmp') is False
    assert target.read_text() == 'keep me'
    assert capsys.readouterr().out == ''


def test_invalid_result_path_does_not_fall_back_to_stdout(monkeypatch, capsys):
    monkeypatch.setenv('PATHNEST_RESULT_FILE', '   ')
    assert emit_selected_path('/tmp') is False
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('shell', ['bash', 'zsh'])
def test_wrapper_real_cli_relative_symlink_roundtrip(tmp_path, shell):
    import shlex
    import sys
    if shutil.which(shell) is None:
        pytest.skip(f'{shell} not installed')
    target = tmp_path / '目标 \n'
    target.mkdir()
    (tmp_path / 'alias').symlink_to(target, target_is_directory=True)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    binary = bin_dir / 'pn-bin'
    binary.write_text(f'#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} -m pathnest "$@"\n')
    binary.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               PYTHONPATH=str(PROJECT_ROOT), PATHNEST_CONFIG_FILE=str(tmp_path / 'db.json'))
    script = f'''cd {shlex.quote(str(tmp_path))}
. {shlex.quote(str(PN_SH))}
pn add --name Link alias || exit 1
cd /
pn go Link || exit 1
[ "$PWD" = {shlex.quote(str(tmp_path / 'alias'))} ]
'''
    proc = subprocess.run([shell, '-c', script], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
