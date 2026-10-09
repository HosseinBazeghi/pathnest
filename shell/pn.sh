# PathNest shell integration for Bash and Zsh.
#
# Defines the `pn` wrapper around the `pn-bin` executable.  When the
# interactive navigator exits after a bookmark was selected, the selected
# path is read from a private result file and the *current* shell changes
# into that directory.  Paths are never evaluated as shell code; they are
# passed to `cd` as a single quoted argument.
#
# Activate by sourcing this file from ~/.bashrc or ~/.zshrc:
#   . "$HOME/.local/share/pathnest/pn.sh"

pn() {
    local result rc dir
    result="$(mktemp "${TMPDIR:-/tmp}/pathnest.XXXXXXXXXX")" || return 1
    if PATHNEST_RESULT_FILE="$result" pn-bin "$@"; then
        rc=0
    else
        rc=$?
    fi
    if [ "$rc" -eq 0 ] && [ -s "$result" ]; then
        IFS= read -r -d '' dir < "$result" || :
        dir=${dir%$'\n'}
        rm -f -- "$result"
        if [ -d "$dir" ]; then
            builtin cd -- "$dir" || rc=1
        else
            printf 'pn: saved directory no longer exists: %s\n' "$dir" >&2
            rc=1
        fi
    else
        rm -f -- "$result"
    fi
    return "$rc"
}
