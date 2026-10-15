"""Update standalone user installations from verified GitHub release assets.

This module is imported only by `pn update`. It never opens the bookmark store
or runs downloaded install scripts. Both replacement files are staged locally;
if committing the binary fails, the original shell wrapper is restored.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__

REPOSITORY = "HosseinBazeghi/pathnest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
BUNDLE = "pathnest-linux-x86_64"
ASSET = BUNDLE + ".tar.gz"
MAX_DOWNLOAD = 128 * 1024 * 1024
TIMEOUT = 30


class UpdateError(Exception):
    """An update could not be completed safely."""


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        raise UpdateError("release version must have the form vMAJOR.MINOR.PATCH")
    return tuple(int(part) for part in value.lstrip("v").split("."))


class _HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.hostname not in {"github.com", "api.github.com",
                                           "release-assets.githubusercontent.com",
                                           "objects.githubusercontent.com"}):
            raise UpdateError("refusing an unexpected release download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str, destination: Path, limit: int) -> str:
    """Stream a bounded HTTPS response to disk and return its SHA-256 digest."""
    request = Request(url, headers={"User-Agent": f"PathNest/{__version__}",
                                   "Accept": "application/vnd.github+json" if url == LATEST_URL else "application/octet-stream"})
    digest = hashlib.sha256()
    total = 0
    try:
        with build_opener(_HTTPSRedirects()).open(request, timeout=TIMEOUT) as response:
            with destination.open("wb") as output:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise UpdateError("release download exceeds its size limit")
                    output.write(chunk)
                    digest.update(chunk)
    except HTTPError as exc:
        if exc.code == 404 and url == LATEST_URL:
            raise UpdateError(f"no published release is available yet; see {RELEASES_URL}") from exc
        if exc.code in (403, 429):
            raise UpdateError("GitHub denied the request or its rate limit was reached; try again later") from exc
        raise UpdateError(f"GitHub download failed (HTTP {exc.code})") from exc
    except (URLError, TimeoutError, HTTPException) as exc:
        raise UpdateError("cannot reach GitHub; check your connection and try again") from exc
    return digest.hexdigest()


def _latest(directory: Path) -> dict:
    target = directory / "release.json"
    _fetch(LATEST_URL, target, 1024 * 1024)
    try:
        release = json.loads(target.read_bytes())
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            raise ValueError
        version_tuple(release.get("tag_name"))
        if not isinstance(release.get("assets"), list):
            raise ValueError
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise UpdateError("GitHub returned invalid release metadata") from exc
    return release


def _check_platform() -> None:
    libc, version = platform.libc_ver()
    try:
        compatible = libc == "glibc" and tuple(map(int, version.split(".")[:2])) >= (2, 35)
    except ValueError:
        compatible = False
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64") or not compatible:
        raise UpdateError("automatic updates require Linux x86_64 with glibc 2.35+; build from source on this platform")


def _installation() -> tuple[Path, Path]:
    binary = Path.home() / ".local/bin/pn-bin"
    if not getattr(sys, "frozen", False) or Path(sys.executable).absolute() != binary:
        raise UpdateError("run pn update from a standalone installation in ~/.local/bin; install this build with install.sh first")
    shell_path = os.environ.get("PATHNEST_SHELL_FILE")
    data_home = Path(os.environ.get("XDG_DATA_HOME", ""))
    if not data_home.is_absolute():
        data_home = Path.home() / ".local/share"
    shell = Path(shell_path) if shell_path else data_home / "pathnest/pn.sh"
    if not shell.is_absolute() or shell.name != "pn.sh":
        raise UpdateError("cannot locate the installed shell wrapper; rerun install.sh")
    for target in (binary, shell):
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise UpdateError(f"update target must be a regular file owned by you: {target}")
    return binary, shell


@contextmanager
def _update_lock(binary: Path):
    fd = os.open(binary.parent / ".pathnest-update.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "rb") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise UpdateError("update lock is not a regular file")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError("another PathNest update is running; try again when it finishes") from exc
        yield


def _download_release(release: dict, directory: Path) -> tuple[Path, Path]:
    tag = release["tag_name"]
    version_tuple(tag)
    names = [asset.get("name") for asset in release["assets"] if isinstance(asset, dict)]
    if names.count(ASSET) != 1 or names.count(ASSET + ".sha256") != 1:
        raise UpdateError(f"release {tag} has no complete {ASSET} bundle and checksum; see {RELEASES_URL}")
    # Construct URLs from our fixed repository, validated tag and fixed filenames.
    base = f"{RELEASES_URL}/download/{tag}/"
    checksum_file = directory / "checksum"
    _fetch(base + ASSET + ".sha256", checksum_file, 4096)
    try:
        fields = checksum_file.read_text(encoding="ascii").split()
    except UnicodeError as exc:
        raise UpdateError("invalid release checksum file") from exc
    if len(fields) != 2 or not re.fullmatch(r"[a-fA-F0-9]{64}", fields[0]) or fields[1].lstrip("*") != ASSET:
        raise UpdateError("invalid release checksum file")
    archive = directory / ASSET
    digest = _fetch(base + ASSET, archive, MAX_DOWNLOAD)
    if digest != fields[0].lower():
        raise UpdateError("release checksum mismatch; the installed version was not changed")
    expected = {f"{BUNDLE}/pn-bin": (directory / "pn-bin", MAX_DOWNLOAD),
                f"{BUNDLE}/shell/pn.sh": (directory / "pn.sh", 256 * 1024)}
    found = set()
    total_size = 0
    # Copy only the two known regular files; never extract archive-controlled paths.
    with tarfile.open(archive, "r|gz") as bundle:
        for count, member in enumerate(bundle, 1):
            path = PurePosixPath(member.name)
            total_size += member.size
            if (count > 100 or total_size > MAX_DOWNLOAD * 2 or member.size < 0
                    or path.is_absolute() or ".." in path.parts
                    or not (member.isfile() or member.isdir())):
                raise UpdateError("release archive contains unsafe or oversized entries")
            if member.name not in expected:
                continue
            target, limit = expected[member.name]
            if member.name in found or not member.isfile() or member.size > limit:
                raise UpdateError("release archive contains duplicate or invalid program files")
            found.add(member.name)
            with bundle.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, 64 * 1024)
    if found != set(expected):
        raise UpdateError("release archive is missing the binary or shell wrapper")
    binary, shell = directory / "pn-bin", directory / "pn.sh"
    binary.chmod(0o755)
    shell.chmod(0o644)
    return binary, shell


def _child_environment() -> dict[str, str]:
    env = dict(os.environ)
    # A frozen parent modifies its library path; the candidate must start fresh.
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is None:
        env.pop("LD_LIBRARY_PATH", None)
    else:
        env["LD_LIBRARY_PATH"] = original
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.pop("PATHNEST_RESULT_FILE", None)
    return env


def _binary_version(binary: Path) -> str:
    try:
        result = subprocess.run([str(binary), "--version"], env=_child_environment(),
                                capture_output=True, text=True, timeout=15, check=True)
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise UpdateError("the binary cannot run on this system; installation was not changed") from exc
    value = result.stdout.strip().removeprefix("pathnest ")
    version_tuple(value)
    return value


def _commit(binary: Path, shell: Path, target_binary: Path, target_shell: Path) -> None:
    """Stage on each destination filesystem; replace wrapper then binary."""
    staged = []
    keep_backups = False
    try:
        for source, target, mode in ((shell, target_shell, 0o644), (binary, target_binary, 0o755)):
            directory = Path(tempfile.mkdtemp(prefix=".pathnest-update-", dir=target.parent))
            staged.append((directory, target))
            shutil.copyfile(target, directory / "previous")
            (directory / "previous").chmod(stat.S_IMODE(target.stat().st_mode))
            with source.open("rb") as input_file, (directory / "new").open("wb") as output:
                shutil.copyfileobj(input_file, output, 64 * 1024)
                output.flush()
                os.fsync(output.fileno())
            (directory / "new").chmod(mode)
        replaced = []
        try:
            for directory, target in staged:
                os.replace(directory / "new", target)
                replaced.append((directory, target))
        except BaseException:
            failures = []
            for directory, target in reversed(replaced):
                try:
                    os.replace(directory / "previous", target)
                except OSError:
                    failures.append(f"{target} (recovery copy: {directory / 'previous'})")
            if failures:
                keep_backups = True
                raise UpdateError("could not restore " + "; ".join(failures))
            raise
    finally:
        if not keep_backups:
            for directory, _ in staged:
                shutil.rmtree(directory)


def run_update(*, check: bool = False) -> int:
    try:
        print("Checking the latest PathNest release on GitHub…", flush=True)
        with tempfile.TemporaryDirectory(prefix="pathnest-update-") as temp:
            directory = Path(temp)
            release = _latest(directory)
            latest = release["tag_name"]
            if version_tuple(latest) <= version_tuple(__version__):
                print(f"PathNest {__version__} is up to date (latest release: {latest}).")
                return 0
            if check:
                print(f"Update available: {__version__} → {latest}. Run pn update to install it.")
                return 0
            _check_platform()
            target_binary, target_shell = _installation()
            with _update_lock(target_binary):
                if version_tuple(_binary_version(target_binary)) >= version_tuple(latest):
                    print("The installed binary is already up to date. Open a new terminal.")
                    return 0
                print(f"Downloading {latest}…", flush=True)
                binary, shell = _download_release(release, directory)
                if version_tuple(_binary_version(binary)) != version_tuple(latest):
                    raise UpdateError("downloaded binary version does not match the release tag")
                for interpreter in ("bash", "zsh"):
                    command = shutil.which(interpreter)
                    if command:
                        subprocess.run([command, "-n", str(shell)], check=True,
                                       capture_output=True, timeout=10, env=_child_environment())
                _commit(binary, shell, target_binary, target_shell)
        print(f"Updated PathNest to {latest}. Bookmarks are unchanged. Open a new terminal to load updated shell integration.")
        return 0
    except (UpdateError, OSError, tarfile.TarError, EOFError, subprocess.SubprocessError) as exc:
        print(f"pn: update failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\npn: update cancelled", file=sys.stderr)
        return 130
