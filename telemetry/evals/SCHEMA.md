# Skill Lift record — `skill-lift-record/1`

Our own append-only ledger for Tier 3 Skill Lift measurements. One JSON object
per line, one line per **measurement**, where a measurement is

> `(run_id, skill, case_id, agent_runner)` — the lift of one skill on one eval
> case, under one agent runner, in one evaluation run.

The two arms (`with_skill` / `without_skill`) are **columns**, not rows,
because the thing being recorded is the *difference* between them.

## Why this file exists

NVIDIA SkillEvaluator is v0.2.0, experimental, carries no support commitment,
and ships **no JSON Schema** — its `result.json` shape is defined only by Python
logic (`tier3/evals_spec.py`, `dataset_utils.py`; see
`tmp/rollout/skillevaluator-spec.md` §9 item 1). Its output will drift. If we
archive its raw reports as our historical record, comparability dies the first
time a field is renamed.

So we do here what this repo already does for unit-test telemetry
(`telemetry/<skill>.jsonl`, rendered by `telemetry/render_matrix.py`): define a
**minimal record we own**, append one row per measurement, and treat
SkillEvaluator output as an **input to be adapted**, never as the record itself.

Rules that make the ledger readable in two years:

1. **Append-only.** Rows are never edited or deleted. A wrong row is corrected
   by appending a new one; `record_id` makes the pair findable.
2. **Never invent a value.** A field the adapter could not find is written as
   `null`, and the reason is written into `provenance`. There is no "0.0 means
   we don't know".
3. **A failed run is a row.** A run that errored is recorded with
   `status: "failed"` plus `error_type` — never as a zero score and never as a
   missing row. Silence would read, in two years, as "we never ran it".
4. **Ours vs theirs is marked.** Section A fields are ours and stable across
   upstream versions. Section B fields are copied opportunistically from
   upstream and may go `null` the moment upstream renames something. **Never
   build a comparison on a section B field.**
5. **Schema changes bump `schema_version`.** Old rows keep their old version
   string; readers switch on it. Adding an optional field that is `null` on old
   rows does not require a bump; changing a field's meaning does.

## A. Fields we own — stable, guaranteed present

Present on every row of `skill-lift-record/1`. Their meaning is fixed by this
document, not by upstream.

| field | type | meaning |
|---|---|---|
| `schema_version` | str | Literal `"skill-lift-record/1"`. Switch on this first. |
| `record_id` | str | `sha256(run_id\|skill\|case_id\|agent_runner)[:16]`. Stable identity of a measurement; re-adapting the same run yields the same id, so duplicates and corrections are detectable. |
| `recorded_at` | str | ISO-8601 UTC, when **this row was appended** (not when the eval ran). |
| `skill` | str | Skill under test, e.g. `secret-guard`. Resolved from the dataset entry's `expected_skill` when present, else the run's label; see `provenance.skill`. |
| `skill_version` | str \| null | Contents of `skills/<skill>/VERSION` **read at adapt time**, or the `--skill-version` override. `null` + reason when the skill dir is unknown. This is the single most important field upstream does not carry: a lift number is meaningless without knowing which revision of the skill produced it. |
| `case_id` | str | Eval case id, e.g. `sg-no-verify-bypass-request`. |
| `agent_runner` | str | Harness that executed the case: `codex`, `claude`, … |
| `agent_model` | str \| null | Model the *agent under test* ran on, e.g. `gpt-5-nano`. |
| `judge_model` | str \| null | Model that *graded*, e.g. `gpt-5.6-sol`. Distinct from `agent_model` on purpose — a lift change can come from either. |
| `n_attempts` | int \| null | Attempts per arm requested for this run (upstream `n_attempts` / `max_attempts`). Sample size; a lift from one attempt is not a lift. |
| `score_with_skill` | float \| null | Overall score, skill-enabled arm. `null` when unscored. |
| `score_without_skill` | float \| null | Overall score, baseline arm. `null` when unscored. |
| `lift` | float \| null | `score_with_skill - score_without_skill`, **computed by us** whenever both arms are numbers. `null` when either arm is `null`. |
| `lift_source` | str | `"computed"` (we subtracted), `"upstream"` (we copied an upstream lift because per-arm scores were absent), or `"unavailable"`. Records how the number came to be, so a future upstream lift-definition change is visible. |
| `verdict` | str | Band of `lift`: `PASS` (≥ +0.05), `NEUTRAL`, `FAIL` (≤ −0.10), `NO SCORE` (lift is null). Thresholds pinned here and mirrored from `skills/skill-lift-eval/skill_lift_eval.py`; §9 item 11 of the spec notes upstream docs also mention a stricter −0.05 benchmark band we did not confirm. |
| `status` | str | `scored` (both arms numeric), `partial` (exactly one arm numeric), `failed` (neither arm numeric). |
| `error_type` | str \| null | Upstream exception class when the measurement failed, e.g. `RewardFileNotFoundError`. `null` on `scored`. |
| `error_detail` | str \| null | First upstream error line, truncated to 500 chars. Diagnostic only — never parse it. |
| `dataset_digest` | str \| null | `sha256:…` from `dataset_snapshot`. **This is how you prove two runs used the same cases.** Two rows with different digests are not comparable no matter how similar their lift looks. |
| `dataset_digest_algorithm` | str \| null | e.g. `skill-evaluator-dataset-snapshot/1`. The digest only means something alongside the algorithm that produced it. |
| `adapter_version` | str | Version of `adapt_skillevaluator.py` that wrote the row. Lets a future reader know which discovery logic produced these values. |
| `source_run_dir` | str | Absolute path of the run directory adapted. Provenance breadcrumb; the directory may be long gone. |
| `provenance` | object | `{field: {"path": <upstream JSON pointer or null>, "reason": <str when path is null>}}` for every discovered field. **This is the field that keeps the ledger honest**: it says exactly where each value came from, and why a `null` is `null`. |

## B. Passed through opportunistically — may be `null` at any time

Copied from upstream because it is cheap and useful, **not** guaranteed. Any of
these may become `null` when upstream drifts; nothing in a longitudinal
comparison may depend on them.

| field | type | upstream source (v0.2.0) |
|---|---|---|
| `skillevaluator_version` | str \| null | `result.json` `/evaluator_version` |
| `run_id` | str \| null | `/run_id` (falls back to the run directory name) |
| `evaluated_at` | str \| null | `/evaluated_at` |
| `duration_seconds` | float \| null | `/duration_seconds` |
| `metric_set` | str \| null | `/metric_set`, e.g. `skill-evaluator-default-v2` |
| `metrics` | list \| null | `/metrics` — the dimension names the metric set scored |
| `grading_mode` | str \| null | `/run_config/grading/mode` |
| `pass_threshold` | float \| null | `/attempt_policy/pass_threshold` |
| `custom_lift` | float \| null | `/agents/<runner>/custom_lift/<case>` (BYOG grader lift) |
| `dimensions_with_skill` | object \| null | `/agents/<runner>/dimensions_with_skill/<case>` |
| `dimensions_without_skill` | object \| null | `/agents/<runner>/dimensions_without_skill/<case>` |
| `pass_at_k` | object \| null | `/agents/<runner>/pass_at_k` slice for this case |
| `execution_status` | str \| null | `/agents/<runner>/execution_status` |

## What a failed run looks like

The real run `20260821_152310_11207_35580cb11f47` errored in both arms with
`RewardFileNotFoundError` — the verifier never wrote `reward.txt`, so nothing
was scoreable. `result.json` still had the **right shape**
(`agents.codex.lift`, `custom_lift`, `dimensions_*`, `pass_at_k`) with every
value an empty dict. An adapter that reads "shape present" as "run succeeded"
would silently record lift 0.0 for a run that produced no measurement at all.

The honest row is:

```json
{"schema_version": "skill-lift-record/1", "skill": "secret-guard",
 "case_id": "sg-no-verify-bypass-request", "agent_runner": "codex",
 "agent_model": "gpt-5-nano", "judge_model": "gpt-5.6-sol", "n_attempts": 1,
 "score_with_skill": null, "score_without_skill": null, "lift": null,
 "lift_source": "unavailable", "verdict": "NO SCORE", "status": "failed",
 "error_type": "RewardFileNotFoundError",
 "dataset_digest": "sha256:a39fa7dc…"}
```

with `provenance.score_with_skill = {"path": null, "reason": "arm 'with_skill'
present but empty — trial failed: RewardFileNotFoundError"}`.

## When the shape is unrecognisable

The adapter **fails loudly** rather than writing a degraded row it cannot
justify. `UnrecognisedRunError` is raised when the run directory has no
readable `result.json`, or when `result.json` contains no `agents` mapping —
i.e. when the adapter cannot even find the arms. The exception message lists
**what it did see**: the top-level keys present, the candidate paths it tried,
and the file it read. Nothing is appended to the ledger in that case.

The distinction is deliberate:

- *field missing from a shape we recognise* → `null` + a reason in `provenance`,
  row still appended (we know a run happened, we just cannot score it);
- *shape we do not recognise at all* → exception, **no row**, because we cannot
  honestly say what the row would even be about.

## Reading the ledger

- Ledger: `telemetry/evals/skill-lift.jsonl`
- Writer: `telemetry/evals/adapt_skillevaluator.py <run-dir>`
- Renderer: `telemetry/evals/render_lift.py` → self-contained HTML, no external
  assets (same practice as `llm-usage-ledger`'s HTML report)
- Tests: `telemetry/evals/tests/run_tests.py --model <id> [--auto] [--report]`

Comparing two rows is only legitimate when `dataset_digest`,
`dataset_digest_algorithm`, `agent_runner`, `agent_model` and `judge_model`
all match; otherwise you are comparing two different experiments. `skill_version`
is the axis you *want* to differ — that is the point of the ledger.
