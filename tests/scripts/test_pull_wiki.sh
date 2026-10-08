#!/usr/bin/env bash
# Verifier for `scripts/deploy-hcloud.sh pull-wiki`, which mirrors the hcloud
# entity wiki into a local Obsidian vault ($WIKI_VAULT_DIR) and newsletter-
# assistant's notes into $WIKI_VAULT_DIR/data/notes/ — the path wiki pages
# already link notes at. Runs against LOCAL source dirs via PULL_WIKI_SRC /
# PULL_NOTES_SRC (rsync sources that default to the hcloud paths), so it needs
# no server. Exit 0 = every assertion holds.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

SRC="$TMP/wiki"; NOTES="$TMP/notes"; VAULT="$TMP/vault"
mkdir -p "$SRC" "$NOTES" "$VAULT/.obsidian"
echo '{}' > "$VAULT/.obsidian/app.json"
echo '# Gone upstream' > "$VAULT/old_page-00000000.md"
printf '# Agent harness\n\n*[My note](data/notes/n1.md) — my note*\n' > "$SRC/agent_harness-3ddfee78.md"
echo '# n1' > "$NOTES/n1.md"

# The deploy script's pre-flight wants a target and a key; the pull under test
# never connects anywhere, so any values do.
export DEPLOY_TARGET=test@localhost IDENTITY_FILE=/dev/null
export PULL_WIKI_SRC="$SRC/" PULL_NOTES_SRC="$NOTES/"

fail=0
check() { if eval "$1"; then echo "ok   - $2"; else echo "FAIL - $2"; fail=1; fi; }
pull() { WIKI_VAULT_DIR="$VAULT" bash "$REPO/scripts/deploy-hcloud.sh" pull-wiki >/dev/null 2>&1; }

check "! env -u WIKI_VAULT_DIR bash '$REPO/scripts/deploy-hcloud.sh' pull-wiki >/dev/null 2>&1" \
    "unset WIKI_VAULT_DIR exits non-zero"

check "pull" "pull-wiki exits 0"
check "[ -f '$VAULT/agent_harness-3ddfee78.md' ]" "new page copied"
check "[ ! -e '$VAULT/old_page-00000000.md' ]" "page gone upstream is removed"
check "[ -f '$VAULT/data/notes/n1.md' ]" "note lands under data/notes/"
check "[ -f '$VAULT/.obsidian/app.json' ]" ".obsidian/ preserved"

check "pull" "second pull exits 0"
check "[ -f '$VAULT/data/notes/n1.md' ]" "second pull keeps the notes (wiki mirror does not delete them)"
check "[ -f '$VAULT/.obsidian/app.json' ]" "second pull keeps .obsidian/"

check "uv run --project '$REPO' python '$REPO/scripts/check_wiki_links.py' '$VAULT'" \
    "every link in the vault resolves (incl. the note link)"

exit $fail
