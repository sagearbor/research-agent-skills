#!/bin/sh
# ---------------------------------------------------------------------------
# llm-usage-ledger Tier-3 eval fixture: lul-shared-dir-rollup
# Self-contained + idempotent. Builds a SHARED ledger directory at $LED
# (default /workspace/ledger) holding three writers' files plus the price
# series, and creates the output directory.
#
# Two traps:
#   prices.jsonl is a dated PRICE SERIES sharing the directory and the .jsonl
#     extension. Its 12 rows parse cleanly and carry a `ts`, so any *.jsonl
#     sweep counts them as 12 zero-token calls.
#   ledger.jsonl was written by a different tool in the older
#     tokens_in/tokens_out schema and has NO `user` field at all -- not in the
#     rows and not in the filename. Its 6 rows belong to nobody in particular.
# ---------------------------------------------------------------------------
set -eu
LED="${LED:-/workspace/ledger}"
OUT="${OUT:-/workspace/output}"
rm -rf "$LED"
mkdir -p "$LED" "$OUT"

cat > "$LED/lmstudio-dana-lab1.jsonl" <<'JEOF'
{"schema": 1, "ts": "2026-07-20T09:00:00-04:00", "duration_s": 3.0, "provider": "azure", "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "gpt-5-mini", "prompt_tokens": 1000, "completion_tokens": 100, "reasoning_tokens": null, "task_tag": "qc"}
{"schema": 1, "ts": "2026-07-20T09:10:00-04:00", "duration_s": 3.0, "provider": "azure", "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "gpt-5-mini", "prompt_tokens": 1000, "completion_tokens": 100, "reasoning_tokens": null, "task_tag": "qc"}
{"schema": 1, "ts": "2026-07-21T09:00:00-04:00", "duration_s": 3.0, "provider": "azure", "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "gpt-5-mini", "prompt_tokens": 1000, "completion_tokens": 100, "reasoning_tokens": null, "task_tag": "qc"}
{"schema": 1, "ts": "2026-07-22T09:00:00-04:00", "duration_s": 3.0, "provider": "azure", "machine": "lab1", "user": "dana", "project": "assay-qc", "model": "gpt-5-mini", "prompt_tokens": 1000, "completion_tokens": 100, "reasoning_tokens": null, "task_tag": "qc"}
JEOF

cat > "$LED/lmstudio-ravi-lab2.jsonl" <<'JEOF'
{"schema": 1, "ts": "2026-07-20T11:00:00-04:00", "duration_s": 5.0, "provider": "azure", "machine": "lab2", "user": "ravi", "project": "intake", "model": "gpt-5-mini", "prompt_tokens": 2000, "completion_tokens": 200, "reasoning_tokens": null, "task_tag": "intake"}
{"schema": 1, "ts": "2026-07-21T11:00:00-04:00", "duration_s": 5.0, "provider": "azure", "machine": "lab2", "user": "ravi", "project": "intake", "model": "gpt-5-mini", "prompt_tokens": 2000, "completion_tokens": 200, "reasoning_tokens": null, "task_tag": "intake"}
{"schema": 1, "ts": "2026-07-22T11:00:00-04:00", "duration_s": 5.0, "provider": "azure", "machine": "lab2", "user": "ravi", "project": "intake", "model": "gpt-5-mini", "prompt_tokens": 2000, "completion_tokens": 200, "reasoning_tokens": null, "task_tag": "intake"}
JEOF

cat > "$LED/ledger.jsonl" <<'JEOF'
{"schema": 1, "ts": "2026-07-20T12:00:00-04:00", "provider": "azure", "model": "gpt-5-mini", "tokens_in": 50000, "tokens_out": 4000, "project": "judge-harness", "api_type": "chat", "endpoint_host": "example-aoai.openai.azure.com"}
{"schema": 1, "ts": "2026-07-20T12:30:00-04:00", "provider": "azure", "model": "gpt-5-mini", "tokens_in": 50000, "tokens_out": 4000, "project": "judge-harness", "api_type": "chat", "endpoint_host": "example-aoai.openai.azure.com"}
{"schema": 1, "ts": "2026-07-21T12:00:00-04:00", "provider": "azure", "model": "gpt-5-mini", "tokens_in": 50000, "tokens_out": 4000, "project": "judge-harness", "api_type": "chat", "endpoint_host": "example-aoai.openai.azure.com"}
{"schema": 1, "ts": "2026-07-21T12:30:00-04:00", "provider": "azure", "model": "gpt-5-mini", "tokens_in": 50000, "tokens_out": 4000, "project": "judge-harness", "api_type": "chat", "endpoint_host": "example-aoai.openai.azure.com"}
{"schema": 1, "ts": "2026-07-22T12:00:00-04:00", "provider": "azure", "model": "gpt-5-mini", "tokens_in": 50000, "tokens_out": 4000, "project": "judge-harness", "api_type": "chat", "endpoint_host": "example-aoai.openai.azure.com"}
{"schema": 1, "ts": "2026-07-22T12:30:00-04:00", "provider": "azure", "model": "gpt-5-mini", "tokens_in": 50000, "tokens_out": 4000, "project": "judge-harness", "api_type": "chat", "endpoint_host": "example-aoai.openai.azure.com"}
JEOF

cat > "$LED/prices.jsonl" <<'PEOF'
{"ts": "2026-06-01", "model": "reference", "input_per_m": 2.5, "output_per_m": 10.0, "source": "builtin:2026-06"}
{"ts": "2026-06-01", "model": "gpt-5-mini", "input_per_m": 0.25, "output_per_m": 2.0, "source": "litellm:azure/gpt-5-mini"}
{"ts": "2026-06-01", "model": "qwen3-32b", "input_per_m": 0.4, "output_per_m": 1.2, "source": "litellm:together/qwen3-32b"}
{"ts": "2026-06-15", "model": "reference", "input_per_m": 2.5, "output_per_m": 10.0, "source": "builtin:2026-06"}
{"ts": "2026-06-15", "model": "gpt-5-mini", "input_per_m": 0.25, "output_per_m": 2.0, "source": "litellm:azure/gpt-5-mini"}
{"ts": "2026-06-15", "model": "qwen3-32b", "input_per_m": 0.4, "output_per_m": 1.2, "source": "litellm:together/qwen3-32b"}
{"ts": "2026-07-01", "model": "reference", "input_per_m": 2.5, "output_per_m": 10.0, "source": "builtin:2026-07"}
{"ts": "2026-07-01", "model": "gpt-5-mini", "input_per_m": 0.25, "output_per_m": 2.0, "source": "litellm:azure/gpt-5-mini"}
{"ts": "2026-07-01", "model": "qwen3-32b", "input_per_m": 0.4, "output_per_m": 1.2, "source": "litellm:together/qwen3-32b"}
{"ts": "2026-07-15", "model": "reference", "input_per_m": 2.5, "output_per_m": 10.0, "source": "builtin:2026-07"}
{"ts": "2026-07-15", "model": "gpt-5-mini", "input_per_m": 0.25, "output_per_m": 2.0, "source": "litellm:azure/gpt-5-mini"}
{"ts": "2026-07-15", "model": "qwen3-32b", "input_per_m": 0.29, "output_per_m": 0.59, "source": "litellm:groq/qwen3-32b"}
PEOF

echo "ready: shared ledger at $LED (3 writers + price series), output at $OUT"
