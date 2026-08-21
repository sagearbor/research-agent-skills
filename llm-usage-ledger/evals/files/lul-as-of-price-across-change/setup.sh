#!/bin/sh
# ---------------------------------------------------------------------------
# llm-usage-ledger Tier-3 eval fixture: lul-as-of-price-across-change
# Self-contained + idempotent. Builds a ledger directory at $LED
# (default /workspace/ledger) and creates the output directory.
#
# The trap: qwen3-32b's hosted rate DROPPED on 2026-07-15. Two of the four
# qwen events predate the change. Pricing the period at one rate is wrong in
# either direction. mistral-small-3 has no price row at all and must fall back
# to the dated `reference` benchmark, not be dropped and not borrow a rate.
# ---------------------------------------------------------------------------
set -eu
LED="${LED:-/workspace/ledger}"
OUT="${OUT:-/workspace/output}"
rm -rf "$LED"
mkdir -p "$LED" "$OUT"

cat > "$LED/lmstudio-dana-lab1.jsonl" <<'JEOF'
{"schema": 1, "ts": "2026-07-01T10:00:00-04:00", "duration_s": 41.0, "provider": "azure", "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "qwen3-32b", "prompt_tokens": 1000000, "completion_tokens": 200000, "reasoning_tokens": null, "task_tag": "batch"}
{"schema": 1, "ts": "2026-07-05T09:30:00-04:00", "duration_s": 22.0, "provider": "azure", "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "qwen3-32b", "prompt_tokens": 500000, "completion_tokens": 100000, "reasoning_tokens": null, "task_tag": "batch"}
{"schema": 1, "ts": "2026-07-20T11:15:00-04:00", "duration_s": 39.0, "provider": "azure", "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "qwen3-32b", "prompt_tokens": 1000000, "completion_tokens": 200000, "reasoning_tokens": null, "task_tag": "batch"}
{"schema": 1, "ts": "2026-07-25T14:45:00-04:00", "duration_s": 77.0, "provider": "azure", "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "qwen3-32b", "prompt_tokens": 2000000, "completion_tokens": 400000, "reasoning_tokens": null, "task_tag": "batch"}
{"schema": 1, "ts": "2026-07-22T08:05:00-04:00", "duration_s": 18.0, "provider": "azure", "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "mistral-small-3", "prompt_tokens": 1000000, "completion_tokens": 100000, "reasoning_tokens": null, "task_tag": "batch"}
JEOF

cat > "$LED/prices.jsonl" <<'PEOF'
{"ts": "2026-06-01", "model": "reference", "input_per_m": 2.5, "output_per_m": 10.0, "source": "builtin:2026-06"}
{"ts": "2026-06-01", "model": "qwen3-32b", "input_per_m": 0.4, "output_per_m": 1.2, "source": "litellm:together/qwen3-32b"}
{"ts": "2026-07-15", "model": "qwen3-32b", "input_per_m": 0.29, "output_per_m": 0.59, "source": "litellm:groq/qwen3-32b"}
{"ts": "2026-08-01", "model": "reference", "input_per_m": 2.5, "output_per_m": 10.0, "source": "builtin:2026-08"}
PEOF

echo "ready: ledger at $LED, output at $OUT"
