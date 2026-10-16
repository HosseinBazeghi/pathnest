# Changelog

## 1.3.1 — 2026-10-16

- Fix HTTPS updates in standalone Linux builds by discovering system CA bundles
  and including Mozilla roots as a fallback when no CA file is available.
- Respect explicit `SSL_CERT_FILE` and `SSL_CERT_DIR` settings, including company
  proxy certificates, without disabling certificate or hostname verification.
- Distinguish certificate verification and TLS errors from network failures.
- Test packaged update checks in clean Ubuntu and Fedora containers before release.

## 1.3.0 — 2026-10-16

- Jump with case-insensitive names and unique prefixes; prefer exact matches
  and list ambiguous matches without navigating.
- Add `pn edit GROUP NAME` to rename bookmarks, repair paths and move references
  between existing groups in one atomic save.
- Add `pn list --json` with optional group filtering for scripts and integrations.
- Add `pn update --version X.Y.Z`, including check-only previews and explicit
  downgrades to published stable releases. Default updates never downgrade.
- Extend Bash/Zsh completion for the new commands and case-insensitive navigation.

## 1.2.0 — 2026-10-15

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
