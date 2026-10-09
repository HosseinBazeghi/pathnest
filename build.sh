#!/usr/bin/env bash
#
# PathNest — one-command build & install script.
#
#   chmod +x build.sh
#   ./build.sh
#
# What it does:
#   1. Detects the Linux environment (primarily Fedora).
#   2. Checks for Python 3.
#   3. Creates (and reuses) a build virtual environment.
#   4. Installs build dependencies (textual, pyinstaller, pytest).
#   5. Runs the automated test suite.
#   6. Builds a standalone `pn-bin` executable with PyInstaller.
#   7. Installs it to ~/.local/bin and installs the shell wrapper.
#   8. Verifies the installation against isolated temporary HOME/config.
#
# The script is idempotent: rerunning rebuilds and updates the program,
# never touches your bookmark database, and never duplicates shell config.
# No root privileges are required.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BIN_NAME="pn-bin"                # internal executable name (the shell wrapper owns `pn`)
INSTALL_DIR="$HOME/.local/bin"
case "${XDG_DATA_HOME:-}" in
    /*) DATA_DIR="$XDG_DATA_HOME/pathnest" ;;
    *) DATA_DIR="$HOME/.local/share/pathnest" ;;
esac
VENV_DIR="$SCRIPT_DIR/.venv-build"
PY="$VENV_DIR/bin/python"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Some environments (e.g. terminal hosts that inject LD_LIBRARY_PATH from an
# AppImage) break venv interpreter detection.  Routing every interpreter call
# through `env` with a sanitized environment is harmless on normal systems and
# fixes that case.
run_py() { env -u LD_LIBRARY_PATH "$@"; }

# ----------------------------------------------------------------------
# 1. Environment detection
# ----------------------------------------------------------------------
say "Detecting build environment"
[ "$(uname -s)" = "Linux" ] || die "unsupported OS: $(uname -s) — PathNest targets Linux"
if [ -r /etc/fedora-release ]; then
    say "Fedora detected: $(cat /etc/fedora-release)"
else
    warn "not Fedora; attempting the build anyway"
fi
[ "$(id -u)" -ne 0 ] || warn "running as root is unnecessary — everything installs into \$HOME"

# ----------------------------------------------------------------------
# 2. Python check
# ----------------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "python3 not found (Fedora: sudo dnf install python3)"
PY_OK="$(run_py python3 -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)' 2>/dev/null)" || die "python3 is broken"
[ "$PY_OK" = "1" ] || die "python3 >= 3.9 required, found $(python3 --version 2>&1)"
say "Using $(python3 --version 2>&1) at $(command -v python3)"

# ----------------------------------------------------------------------
# 3. Build virtual environment
# ----------------------------------------------------------------------
if [ ! -x "$PY" ]; then
    say "Creating build virtual environment: $VENV_DIR"
    if command -v uv >/dev/null 2>&1; then
        uv venv --python "$(command -v python3)" "$VENV_DIR" || die "uv venv failed"
    else
        run_py python3 -m venv "$VENV_DIR" || die "python3 -m venv failed (Fedora: sudo dnf install python3)"
    fi
fi
if ! run_py "$PY" -c 'import sys' >/dev/null 2>&1; then
    die "virtual environment is unusable; move $VENV_DIR aside and recreate it with python3 -m venv --copies"
fi
run_py "$PY" -c 'import sys; sys.exit(0 if sys.prefix == sys.argv[1] else 1)' "$VENV_DIR" \
    || die "virtual environment is broken (sys.prefix mismatch); try unsetting LD_LIBRARY_PATH and rerun"

# ----------------------------------------------------------------------
# 4. Build dependencies
# ----------------------------------------------------------------------
say "Installing build dependencies (textual, pyinstaller, pytest)"
if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$PY" 'textual>=1.0' 'pyinstaller>=6' 'pytest>=7' >/dev/null || die "dependency installation failed (uv)"
else
    run_py "$PY" -m pip --version >/dev/null 2>&1 \
        || die "pip unavailable in the venv (Fedora: sudo dnf install python3-pip, or install uv)"
    run_py "$PY" -m pip install --quiet 'textual>=1.0' 'pyinstaller>=6' 'pytest>=7' \
        || die "dependency installation failed (pip)"
fi

# ----------------------------------------------------------------------
# 5. Test suite
# ----------------------------------------------------------------------
say "Running automated tests"
run_py "$PY" -m pytest tests -q || die "tests failed — fix them before building"

# ----------------------------------------------------------------------
# 6. Build the standalone executable
# ----------------------------------------------------------------------
say "Building standalone executable with PyInstaller"
run_py "$PY" -m PyInstaller \
    --noconfirm --clean --onefile \
    --collect-all textual \
    --name "$BIN_NAME" \
    entry.py || die "PyInstaller build failed"
[ -x "dist/$BIN_NAME" ] || die "build finished but dist/$BIN_NAME is missing"

# ----------------------------------------------------------------------
# 7. Install executable + shell integration
# ----------------------------------------------------------------------
say "Installing to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
install -m 0755 "dist/$BIN_NAME" "$INSTALL_DIR/$BIN_NAME" || die "failed to install executable"

say "Installing shell integration to $DATA_DIR"
mkdir -p "$DATA_DIR"
install -m 0644 shell/pn.sh "$DATA_DIR/pn.sh" || die "failed to install shell wrapper"

integrate_rc() {
    local rcfile="$1"
    touch -- "$rcfile"  # create only when missing; never overwrites
    if grep -q 'pathnest shell integration' "$rcfile" 2>/dev/null; then
        say "Shell integration already present in $rcfile (no change)"
    else
        {
            printf '\n# >>> pathnest shell integration >>>\n'
            printf 'if [ -f %q ]; then\n  . %q\nfi\n' "$DATA_DIR/pn.sh" "$DATA_DIR/pn.sh"
            printf '# <<< pathnest shell integration <<<\n'
        } >> "$rcfile" || die "failed to update $rcfile"
        say "Added shell integration to $rcfile"
    fi
}

case "$(basename "${SHELL:-/bin/bash}")" in
    zsh) integrate_rc "${ZDOTDIR:-$HOME}/.zshrc" ;;
    *)   integrate_rc "$HOME/.bashrc" ;;
esac

# ----------------------------------------------------------------------
# 8. PATH check
# ----------------------------------------------------------------------
case ":$PATH:" in
    *":$INSTALL_DIR:"*) say "PATH check: $INSTALL_DIR is on PATH" ;;
    *) warn "$INSTALL_DIR is not in PATH. Add to your shell rc: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# ----------------------------------------------------------------------
# 9. Verification (isolated temporary HOME/config — user data untouched)
# ----------------------------------------------------------------------
say "Verifying installation"
VERIFY_HOME="$(mktemp -d)" || die "mktemp failed"
trap 'rm -rf -- "$VERIFY_HOME"' EXIT

"$INSTALL_DIR/$BIN_NAME" --version >/dev/null || die "installed executable failed to run"

CFG_ENV="$VERIFY_HOME/custom/bookmarks.json"
PATHNEST_CONFIG_FILE="$CFG_ENV" "$INSTALL_DIR/$BIN_NAME" list >/dev/null \
    || die "running with PATHNEST_CONFIG_FILE failed"
[ -s "$CFG_ENV" ] || die "PATHNEST_CONFIG_FILE location was not initialized"

CFG_CLI="$VERIFY_HOME/cli/bookmarks.json"
"$INSTALL_DIR/$BIN_NAME" --config "$CFG_CLI" list >/dev/null || die "running with --config failed"
[ -s "$CFG_CLI" ] || die "--config location was not initialized"

env -u PATHNEST_CONFIG_FILE -u XDG_CONFIG_HOME HOME="$VERIFY_HOME" \
    "$INSTALL_DIR/$BIN_NAME" list >/dev/null || die "running with isolated HOME failed"
[ -f "$VERIFY_HOME/.config/pathnest/bookmarks.json" ] \
    || die "default config location (~/.config/pathnest/bookmarks.json) was not created"

env -u PATHNEST_CONFIG_FILE HOME="$VERIFY_HOME" XDG_CONFIG_HOME="$VERIFY_HOME/xdg" \
    "$INSTALL_DIR/$BIN_NAME" list >/dev/null || die "running with XDG_CONFIG_HOME failed"
[ -f "$VERIFY_HOME/xdg/pathnest/bookmarks.json" ] || die "XDG_CONFIG_HOME was not respected"

SPACY="$VERIFY_HOME/dir with spaces & (chars)"
mkdir -p -- "$SPACY"
"$INSTALL_DIR/$BIN_NAME" --config "$CFG_ENV" group add Research >/dev/null || die "group add failed"
"$INSTALL_DIR/$BIN_NAME" --config "$CFG_ENV" add "$SPACY" --group Research --name Spacey >/dev/null \
    || die "bookmarking a path with spaces failed"
"$INSTALL_DIR/$BIN_NAME" --config "$CFG_ENV" list | grep -F -- "$SPACY" >/dev/null \
    || die "roundtrip of a path with spaces failed"

say "All verification checks passed"

# ----------------------------------------------------------------------
# 10. Success
# ----------------------------------------------------------------------
printf '\n'
say "PathNest installed successfully"
cat <<EOF

    Executable : $INSTALL_DIR/$BIN_NAME
    Wrapper    : $DATA_DIR/pn.sh  (defines the \`pn\` shell function)
    Bookmarks  : \$XDG_CONFIG_HOME/pathnest/bookmarks.json
                 (~/.config/pathnest/bookmarks.json by default)

    Start a new shell (or run: . "$DATA_DIR/pn.sh") and use:

        pn            interactive navigator (Enter = cd in your shell)
        pn add        bookmark the current directory
        pn group add  create a group
        pn --help     all commands

EOF
