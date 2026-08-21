#!/bin/sh
# ---------------------------------------------------------------------------
# llm-usage-ledger Tier-3 eval fixture: lul-two-clones-one-repo
# Self-contained + idempotent. Builds:
#   /workspace/dev-a/research-agent-skills  git clone, origin over SSH
#   /workspace/dev-b/ras                    the SAME repo, origin over HTTPS,
#                                           different capitalisation, different
#                                           folder name
#   /workspace/dev-c/scratch                a plain directory, no git at all
#   $FHOME (default /workspace/fakehome)    transcripts whose `cwd` points at
#                                           each of the three
#   $LED  (default /workspace/ledger)       empty ledger dir + price series
#
# The trap: the two clones are one repository. The folder names differ, and so
# do the remote URLs -- scheme, punctuation and case all differ -- so grouping
# by folder name OR by the raw remote string yields two repositories and two
# half-totals, neither of which is wrong-looking. The scratch directory has no
# remote at all and can never join to anything; recording its local path as the
# key publishes a filesystem path (and the local username) into a shared
# ledger while still joining with nobody.
#
# Everything git is set --local so the host's global config is never touched.
# ---------------------------------------------------------------------------
set -eu
WS="${WS:-/workspace}"
LED="${LED:-$WS/ledger}"
FHOME="${FHOME:-$WS/fakehome}"
OUT="${OUT:-$WS/output}"
rm -rf "$LED" "$FHOME" "$WS/dev-a" "$WS/dev-b" "$WS/dev-c"
mkdir -p "$LED" "$OUT" "$FHOME/.claude/projects/-shared"
mkdir -p "$WS/dev-a" "$WS/dev-b" "$WS/dev-c/scratch"

mkclone() {
  dir="$1"; url="$2"
  mkdir -p "$dir"
  git init -q "$dir"
  git -C "$dir" config --local user.email fixture@example.invalid
  git -C "$dir" config --local user.name "Eval Fixture"
  git -C "$dir" config --local commit.gpgsign false
  git -C "$dir" remote add origin "$url"
  echo "# fixture" > "$dir/README.md"
  git -C "$dir" add README.md
  git -C "$dir" commit -q -m "init"
}
mkclone "$WS/dev-a/research-agent-skills" "git@github.com:sagearbor/ras.git"
mkclone "$WS/dev-b/ras"                   "https://github.com/SageArbor/RAS.git"

cat > "$FHOME/.claude/projects/-shared/session.jsonl" <<TEOF
{"uuid": "c-1", "timestamp": "2026-07-20T09:00:00Z", "cwd": "$WS/dev-a/research-agent-skills", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 1000, "output_tokens": 100}}}
{"uuid": "c-2", "timestamp": "2026-07-20T09:30:00Z", "cwd": "$WS/dev-a/research-agent-skills", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 2000, "output_tokens": 200}}}
{"uuid": "c-3", "timestamp": "2026-07-21T10:00:00Z", "cwd": "$WS/dev-b/ras", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 4000, "output_tokens": 400}}}
{"uuid": "c-4", "timestamp": "2026-07-21T11:00:00Z", "cwd": "$WS/dev-c/scratch", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 500, "output_tokens": 50}}}
TEOF

cat > "$LED/prices.jsonl" <<'PEOF'
{"ts": "2026-06-01", "model": "reference", "input_per_m": 2.5, "output_per_m": 10.0, "source": "builtin:2026-06"}
{"ts": "2026-06-01", "model": "claude-sonnet-5", "input_per_m": 3.0, "output_per_m": 15.0, "source": "litellm:anthropic/claude-sonnet-5"}
PEOF

echo "ready: two clones of one repo + one scratch dir, transcripts under $FHOME"
