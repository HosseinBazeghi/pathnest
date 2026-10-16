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

# Remember the sourced wrapper even if XDG_DATA_HOME changes later.
if [ -n "${BASH_VERSION:-}" ]; then
    _PN_SHELL_FILE=${BASH_SOURCE[0]}
else
    _PN_SHELL_FILE=${(%):-%x}
fi
case "$_PN_SHELL_FILE" in
    /*) ;;
    *) _PN_SHELL_FILE="$PWD/$_PN_SHELL_FILE" ;;
esac

pn() {
    # These commands cannot navigate; avoid mktemp/rm and their subprocesses.
    case "${1:-}" in
        add|edit|group|remove|list|scan|update|--help|-h|--version)
            PATHNEST_RESULT_FILE= PATHNEST_SHELL_FILE="$_PN_SHELL_FILE" pn-bin "$@"
            return $?
            ;;
    esac
    local result rc dir
    result="$(mktemp "${TMPDIR:-/tmp}/pathnest.XXXXXXXXXX")" || return 1
    if PATHNEST_RESULT_FILE="$result" PATHNEST_SHELL_FILE="$_PN_SHELL_FILE" pn-bin "$@"; then
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

# Native completion keeps spaces in bookmark/group names intact. Directory
# arguments fall back to the shell's filename completion.
if [ -n "${BASH_VERSION:-}" ]; then
    _pn_complete() {
        local candidate
        COMPREPLY=()
        while IFS= read -r candidate; do
            COMPREPLY+=("$candidate")
        done < <(pn-bin _complete -- "${COMP_WORDS[@]:0:COMP_CWORD+1}" 2>/dev/null)
    }
    complete -o bashdefault -o default -F _pn_complete pn
elif [ -n "${ZSH_VERSION:-}" ]; then
    _pn_complete() {
        local -a candidates
        candidates=("${(@f)$(pn-bin _complete -- "${(@)words[1,CURRENT]}" 2>/dev/null)}")
        if (( ${#candidates} )) && [[ -n ${candidates[1]} ]]; then
            compadd -- "${candidates[@]}"
        else
            _files
        fi
    }
    # Define the completion function even before compinit, then register it
    # at the first prompt once the user's completion system is available.
    _pn_register_completion() {
        if (( $+functions[compdef] )); then
            compdef _pn_complete pn
            precmd_functions=(${precmd_functions:#_pn_register_completion})
        fi
    }
    if (( $+functions[compdef] )); then
        compdef _pn_complete pn
    elif [[ -o interactive ]]; then
        typeset -ga precmd_functions
        precmd_functions=(${precmd_functions:#_pn_register_completion} _pn_register_completion)
    fi
fi
