#!/bin/sh
# ---------------------------------------------------------------------------
# llm-usage-ledger Tier-3 eval fixture: lul-reingest-no-double-count
# Self-contained + idempotent. Builds:
#   $LED  (default /workspace/ledger)     -- ledger dir, ALREADY holds 3 rows
#   $FHOME (default /workspace/fakehome)  -- a stand-in HOME holding transcripts
#
# The transcripts are under a FAKE home, never the container's real one, so the
# agent's own session transcript can never be swept in and the counts stay
# deterministic in both arms.
#
# Two traps, pulling in opposite directions:
#   m0 is in the ledger but NO LONGER in any transcript (the tool pruned it).
#      Rebuilding the ledger from the transcripts silently deletes it.
#   m1, m2 are in BOTH. Appending everything double-counts them.
# Only m3 is genuinely new.
#
# The pre-existing derived file must be named the way the importer names it
# for THIS user on THIS machine, or the importer would write a second file
# beside it and the "already ingested" state would not exist. That name is
# computed, not hardcoded.
# ---------------------------------------------------------------------------
set -eu
LED="${LED:-/workspace/ledger}"
FHOME="${FHOME:-/workspace/fakehome}"
OUT="${OUT:-/workspace/output}"
rm -rf "$LED" "$FHOME"
mkdir -p "$LED" "$OUT" "$FHOME/.claude/projects/-workspace-repo"

DERIVED="$LED/$(python3 -c 'import getpass, platform; print("claude-code-%s-%s.jsonl" % (getpass.getuser(), platform.node().split(".")[0]))')"

# already ingested: m0 (transcript since pruned), m1, m2
cat > "$DERIVED" <<'JEOF'
{"schema": 1, "src_id": "m0", "ts": "2026-07-18T09:00:00+00:00", "provider": "claude-code", "subscription": true, "machine": "lab1", "user": "dana", "project": "repo", "model": "claude-sonnet-5", "prompt_tokens": 100, "completion_tokens": 10, "cache_read_tokens": 0, "cache_w5_tokens": 0, "cache_w1h_tokens": 0, "task_tag": "claude-code"}
{"schema": 1, "src_id": "m1", "ts": "2026-07-19T09:00:00+00:00", "provider": "claude-code", "subscription": true, "machine": "lab1", "user": "dana", "project": "repo", "model": "claude-sonnet-5", "prompt_tokens": 200, "completion_tokens": 20, "cache_read_tokens": 0, "cache_w5_tokens": 0, "cache_w1h_tokens": 0, "task_tag": "claude-code"}
{"schema": 1, "src_id": "m2", "ts": "2026-07-19T09:05:00+00:00", "provider": "claude-code", "subscription": true, "machine": "lab1", "user": "dana", "project": "repo", "model": "claude-sonnet-5", "prompt_tokens": 300, "completion_tokens": 30, "cache_read_tokens": 0, "cache_w5_tokens": 0, "cache_w1h_tokens": 0, "task_tag": "claude-code"}
JEOF

# transcripts hold m1, m2 (already ingested) and m3 (new). m0 is gone.
cat > "$FHOME/.claude/projects/-workspace-repo/session-a.jsonl" <<'TEOF'
{"type": "user", "uuid": "u-a", "timestamp": "2026-07-19T08:59:00Z", "cwd": "/workspace/repo", "message": {"role": "user", "content": "run the qc pass"}}
{"uuid": "m1", "timestamp": "2026-07-19T09:00:00Z", "cwd": "/workspace/repo", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 200, "output_tokens": 20}}}
{"uuid": "m2", "timestamp": "2026-07-19T09:05:00Z", "cwd": "/workspace/repo", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 300, "output_tokens": 30}}}
TEOF
cat > "$FHOME/.claude/projects/-workspace-repo/session-b.jsonl" <<'TEOF'
{"uuid": "m3", "timestamp": "2026-07-21T15:20:00Z", "cwd": "/workspace/repo", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 400, "output_tokens": 40}}}
TEOF

cat > "$LED/prices.jsonl" <<'PEOF'
{"ts": "2026-06-01", "model": "reference", "input_per_m": 2.5, "output_per_m": 10.0, "source": "builtin:2026-06"}
{"ts": "2026-06-01", "model": "claude-sonnet-5", "input_per_m": 3.0, "output_per_m": 15.0, "source": "litellm:anthropic/claude-sonnet-5"}
PEOF

echo "ready: ledger at $LED (3 rows already imported), transcripts under $FHOME"
