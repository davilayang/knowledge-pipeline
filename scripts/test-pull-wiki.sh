#!/usr/bin/env bash
# Verifier for `scripts/deploy-hcloud.sh pull-wiki`, which mirrors the hcloud
# entity wiki into a local Obsidian vault ($WIKI_VAULT_DIR) and newsletter-
# assistant's notes into $WIKI_VAULT_DIR/data/notes/ (the path wiki pages
# already link notes at).
#
# The script under test is COPIED into a throwaway project dir with its own
# .env.deploy, so the developer's real .env.deploy and real vault are never
# read or touched. Sources are local dirs given as PULL_WIKI_SRC /
# PULL_NOTES_SRC, so no server is needed. Exit 0 = every assertion holds.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PROJ="$TMP/proj"
mkdir -p "$PROJ/scripts"
cp "$REPO/scripts/deploy-hcloud.sh" "$PROJ/scripts/"
touch "$TMP/key"
write_env() {
    printf 'DEPLOY_TARGET=test@localhost\nIDENTITY_FILE=%s\n' "$TMP/key" > "$PROJ/.env.deploy"
}

SRC="$TMP/wiki"; NOTES="$TMP/notes"; VAULT="$TMP/vault"; EMPTY="$TMP/empty"
mkdir -p "$SRC" "$NOTES" "$EMPTY" "$VAULT/.obsidian" "$VAULT/.trash"
echo '{}' > "$VAULT/.obsidian/app.json"
echo '# Deleted in Obsidian' > "$VAULT/.trash/old.md"
echo '# Gone upstream' > "$VAULT/old_page-00000000.md"
echo '# Wiki Index' > "$SRC/index.md"
printf '# Agent harness\n\n*[My note](data/notes/n1.md) — my note*\n' > "$SRC/agent_harness-3ddfee78.md"
echo 'half-written' > "$SRC/agent_harness-3ddfee78.md.tmp"
echo '# n1' > "$NOTES/n1.md"
export PULL_WIKI_SRC="$SRC/" PULL_NOTES_SRC="$NOTES/"

fail=0
check() {
    if eval "$1"; then echo "ok   - $2"
    else echo "FAIL - $2"; sed 's/^/       | /' "$TMP/out" 2>/dev/null | tail -3; fail=1; fi
}
run() { bash "$PROJ/scripts/deploy-hcloud.sh" pull-wiki > "$TMP/out" 2>&1; }
pull() { WIKI_VAULT_DIR="$1" run; }

# --- refusals ----------------------------------------------------------------
write_env
check "! env -u WIKI_VAULT_DIR bash '$PROJ/scripts/deploy-hcloud.sh' pull-wiki > '$TMP/out' 2>&1 \
    && grep -q WIKI_VAULT_DIR '$TMP/out'" \
    "unset WIKI_VAULT_DIR exits non-zero and names the variable"

check "! (cd '$TMP' && pull vault) && grep -q WIKI_VAULT_DIR '$TMP/out'" \
    "relative WIKI_VAULT_DIR is refused"

mkdir -p "$TMP/not-a-vault"; echo 'precious' > "$TMP/not-a-vault/keep.txt"
check "! pull '$TMP/not-a-vault' && [ -f '$TMP/not-a-vault/keep.txt' ]" \
    "a non-empty folder that is not a vault is refused and left untouched"

check "! PULL_WIKI_SRC='$EMPTY/' pull '$VAULT' && [ -f '$VAULT/old_page-00000000.md' ]" \
    "an empty wiki source is refused and the vault left untouched"

# --- first pull into an existing vault ----------------------------------------
# .env.deploy is the documented config file; a value there must not override
# the vault the caller passed on the command line.
write_env; echo "WIKI_VAULT_DIR=$TMP/env-file-vault" >> "$PROJ/.env.deploy"
check "pull '$VAULT'" "pull-wiki exits 0"
check "[ ! -e '$TMP/env-file-vault' ]" "the caller's WIKI_VAULT_DIR wins over .env.deploy"
check "! grep -q 'cannot delete' '$TMP/out'" "no rsync deletion warning"
check "[ -f '$VAULT/agent_harness-3ddfee78.md' ] && [ -f '$VAULT/index.md' ]" "pages copied"
check "[ ! -e '$VAULT/agent_harness-3ddfee78.md.tmp' ]" "in-flight *.tmp files are not copied"
check "[ ! -e '$VAULT/old_page-00000000.md' ]" "page gone upstream is removed"
check "[ -f '$VAULT/data/notes/n1.md' ]" "note lands under data/notes/"
check "[ -f '$VAULT/.obsidian/app.json' ]" ".obsidian/ preserved"
check "[ -f '$VAULT/.trash/old.md' ]" ".trash/ preserved"

check "pull '$VAULT'" "second pull exits 0"
check "[ -f '$VAULT/data/notes/n1.md' ]" "second pull keeps the notes"
check "[ -f '$VAULT/.obsidian/app.json' ] && [ -f '$VAULT/.trash/old.md' ]" \
    "second pull keeps dot-folders"

# --- a fresh vault path (never opened in Obsidian yet) -------------------------
check "pull '$TMP/fresh/vault' && pull '$TMP/fresh/vault' \
    && [ -f '$TMP/fresh/vault/index.md' ]" \
    "a new vault path can be pulled into, and pulled into again"

check "uv run --quiet --project '$REPO' python '$REPO/scripts/check_wiki_links.py' '$VAULT'" \
    "every link in the vault resolves (incl. the note link)"

exit $fail
