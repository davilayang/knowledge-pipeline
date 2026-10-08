#!/usr/bin/env bash
# Verifier for the done-line "if I say 'check my knowledge graph about OKF', the
# agent can reach it and tell me what I have read": a fresh top-level Claude Code
# session (not a subagent: it must load the user's normal MCP servers, skills
# and instructions) must call the knowledge-os MCP `search_knowledge` tool and
# name at least one OKF article actually in queue.db, by a title phrase or by
# its source (author / handle), since a good answer cites sources by who wrote them.
#
# Usage: scripts/check_agent_recall.sh [extra claude flags...]
#   RECALL_CWD     directory the session starts in (default: this repo's root)
#   RECALL_TITLES  '|'-separated phrases identifying the OKF articles in queue.db
#                  (default below: distinctive title fragments + source handles;
#                  not "Google Cloud", which any OKF answer mentions unprompted, nor a
#                  bare first name like "Vincent", which proves nothing)
# Model output varies: rerun a red once before believing it.
# Exit 0 = tool called and a title named; 1 otherwise.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TITLES="${RECALL_TITLES:-Beyond RAG|OKF + RAG|Agentic Data Catalogs|More Than Markdown|secret-dev|ravishkhullar|Mokashi|vincent930093}"
OUT="$(mktemp)"
ERR="$(mktemp)"
trap 'rm -f "$OUT" "$ERR"' EXIT

cd "${RECALL_CWD:-$REPO}" || exit 1
claude -p "check my knowledge graph about OKF" \
    --output-format stream-json --verbose \
    --allowedTools "mcp__knowledge-os__search_knowledge" \
    "$@" > "$OUT" 2> "$ERR"

python3 - "$OUT" "$TITLES" "$ERR" <<'PY'
import json, sys

path, titles = sys.argv[1], sys.argv[2].split("|")
called, answer = False, ""
for line in open(path, encoding="utf-8"):
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    if event.get("type") == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "mcp__knowledge-os__search_knowledge":
                called = True
    if event.get("type") == "result":
        answer = event.get("result") or ""
named = [t for t in titles if t.lower() in answer.lower()]
print(f"search_knowledge called: {called}")
print(f"OKF articles identified: {named or 'none'}")
if not (called and named):
    print("--- answer ---")
    print(answer[:1500])
    # An empty answer usually means claude itself failed (auth, PATH, MCP server
    # down); show why, so the red is not mistaken for the model's choice.
    stderr = open(sys.argv[3], encoding="utf-8").read().strip()
    if stderr:
        print("--- claude stderr ---")
        print(stderr[-1500:])
sys.exit(0 if called and named else 1)
PY
