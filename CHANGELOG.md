# Changelog

## 1.2.0 — unreleased

- Add `pn update` and `pn update --check` for stable GitHub releases, with SHA-256
  verification, compatibility checks, an update lock and rollback on failed replacement.
- Keep update operations independent of the bookmark database.
- Skip temporary navigation files for management commands and avoid bookmark
  reads when completing command names and options.
- Preserve search selection when results stay the same and bound search result
  storage to the first 100 matches.
- Load fuzzy matching only when needed and reuse the bookmark-name lookup.
- Skip directory names that would collide after whitespace normalization during scanning.

## 1.1.0

- Add global TUI search, first-run bookmarking and unavailable-directory indicators.
- Add group-specific jumps, Bash/Zsh completion and shallow directory scanning.
- Add release archives, a shared installer and updated documentation/demo.
