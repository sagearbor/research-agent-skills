#!/bin/sh
# ---------------------------------------------------------------------------
# llm-usage-ledger Tier-3 eval fixture: lul-cache-tier-economics
# Self-contained + idempotent. Builds a ledger directory at $LED
# (default /workspace/ledger) and creates the output directory.
#
# The trap: these are subscription rows from Claude Code, where the cache
# token counts DWARF the plain input count. Anthropic prices cache reads at
# 0.1x input, 5-minute cache writes at 1.25x and 1-hour writes at 2.0x, so
# treating cache tokens as ordinary input tokens (or ignoring them) is off by
# orders of magnitude in opposite directions.
# ---------------------------------------------------------------------------
set -eu
LED="${LED:-/workspace/ledger}"
OUT="${OUT:-/workspace/output}"
rm -rf "$LED"
mkdir -p "$LED" "$OUT"

cat > "$LED/claude-code-dana-lab1.jsonl" <<'JEOF'
{"schema": 1, "src_id": "e1f2a3b4-0001-4000-8000-000000000001", "ts": "2026-07-20T13:04:11.000+00:00", "provider": "claude-code", "subscription": true, "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "claude-sonnet-5", "prompt_tokens": 2000, "completion_tokens": 500, "cache_read_tokens": 1000000, "cache_w5_tokens": 200000, "cache_w1h_tokens": 100000, "task_tag": "claude-code"}
{"schema": 1, "src_id": "e1f2a3b4-0001-4000-8000-000000000002", "ts": "2026-07-20T13:09:52.000+00:00", "provider": "claude-code", "subscription": true, "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "claude-sonnet-5", "prompt_tokens": 1000, "completion_tokens": 250, "cache_read_tokens": 600000, "cache_w5_tokens": 0, "cache_w1h_tokens": 0, "task_tag": "claude-code"}
JEOF

cat > "$LED/prices.jsonl" <<'PEOF'
{"ts": "2026-06-01", "model": "reference", "input_per_m": 2.5, "output_per_m": 10.0, "source": "builtin:2026-06"}
{"ts": "2026-06-01", "model": "claude-sonnet-5", "input_per_m": 3.0, "output_per_m": 15.0, "source": "litellm:anthropic/claude-sonnet-5"}
PEOF

echo "ready: ledger at $LED, output at $OUT"
