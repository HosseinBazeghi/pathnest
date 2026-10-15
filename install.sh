#!/usr/bin/env bash
# Install a downloaded bundle, or pass a locally built binary as the first argument.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_SOURCE="${1:-$SCRIPT_DIR/pn-bin}"
INSTALL_DIR="$HOME/.local/bin"
case "${XDG_DATA_HOME:-}" in
    /*) DATA_DIR="$XDG_DATA_HOME/pathnest" ;;
    *) DATA_DIR="$HOME/.local/share/pathnest" ;;
esac
say() { printf '%s\n' "$*"; }
die() { printf 'pn: %s\n' "$*" >&2; exit 1; }
[ "$(uname -s)" = Linux ] || die "this installer supports Linux"
[ -x "$BIN_SOURCE" ] || die "executable not found: $BIN_SOURCE"
"$BIN_SOURCE" --version || die "binary cannot run on this system; use the source build instead"
mkdir -p "$INSTALL_DIR" "$DATA_DIR"
# Stage files on their destination filesystems. Renaming the binary also works
# while an older PathNest process is running; never truncate the live executable.
BIN_STAGE="$(mktemp "$INSTALL_DIR/.pn-bin.XXXXXXXX")"
SHELL_STAGE=""
trap 'rm -f -- "$BIN_STAGE" "${SHELL_STAGE:-}"' EXIT
SHELL_STAGE="$(mktemp "$DATA_DIR/.pn.sh.XXXXXXXX")"
install -m 0755 "$BIN_SOURCE" "$BIN_STAGE"
install -m 0644 "$SCRIPT_DIR/shell/pn.sh" "$SHELL_STAGE"
mv -f -- "$SHELL_STAGE" "$DATA_DIR/pn.sh"
mv -f -- "$BIN_STAGE" "$INSTALL_DIR/pn-bin"

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

case ":$PATH:" in
    *":$INSTALL_DIR:"*) ;;
    *) say 'Add to your shell configuration: export PATH="$HOME/.local/bin:$PATH"' ;;
esac
say 'Installed. Open a new Bash or Zsh terminal and run pn.'
