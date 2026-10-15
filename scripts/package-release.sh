#!/usr/bin/env bash
# Bundle a tested binary with everything needed for shell navigation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="pathnest-linux-x86_64"
STAGE="$(mktemp -d)"
trap 'rm -rf -- "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME/shell" "$ROOT/dist"
install -m 0755 "$ROOT/dist/pn-bin" "$STAGE/$NAME/pn-bin"
install -m 0755 "$ROOT/install.sh" "$STAGE/$NAME/install.sh"
install -m 0644 "$ROOT/shell/pn.sh" "$STAGE/$NAME/shell/pn.sh"
install -m 0644 "$ROOT/README.md" "$ROOT/LICENSE" "$STAGE/$NAME/"
tar -czf "$ROOT/dist/$NAME.tar.gz" -C "$STAGE" "$NAME"
(cd "$ROOT/dist" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
