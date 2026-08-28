#!/usr/bin/env python3
"""Regression tests + per-model telemetry for the Skill Lift ledger tooling
(`adapt_skillevaluator.py`, `render_lift.py`, `SCHEMA.md`).

Deterministic, stdlib only: no network, no API key, no Docker, no
SkillEvaluator install.  Three fixtures:

  fixtures/real-failed-run/      a REAL SkillEvaluator 0.2.0 Tier 3 run whose
                                 two arms both died with RewardFileNotFoundError.
                                 result.json has the right SHAPE
                                 (agents.codex.lift, custom_lift, dimensions_*,
                                 pass_at_k) and every value is empty.  The whole
                                 point of the suite: this must record as an
                                 explicit failed run, never as lift 0.0 and
                                 never as a missing row.
  fixtures/synthetic-scored-run/ a hand-built successful run exercising scalar
                                 scores, dict-shaped scores, a partial arm, an
                                 upstream-only lift, and a skill with no
                                 VERSION file in this repo.
  fixtures/mangled-*/            shapes the adapter must REFUSE, loudly, having
                                 written nothing.

Usage (same contract as every skill's tests in this repo):
    run_tests.py --model <model-id>              # run tests + record to ledger
    run_tests.py --model <model-id> --auto       # run+record ONLY if this
                                                 #   (model, version) has < 8 runs
    run_tests.py --model <model-id> --no-record  # run without touching the ledger
    run_tests.py --report                        # print the matrix (mean +/- SD)

Ledger: telemetry/evals/tests-telemetry.jsonl — one JSON object per run, same
row shape as telemetry/<skill>.jsonl.  It lives in this subdirectory on purpose:
telemetry/render_matrix.py globs telemetry/*.jsonl to build the SKILL matrix,
and this is repo infrastructure, not a skill.
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
EVALS_DIR = TESTS_DIR.parent
REPO = EVALS_DIR.parent.parent
FIXTURES = TESTS_DIR / "fixtures"
LEDGER = EVALS_DIR / "tests-telemetry.jsonl"
AUTO_RUNS_PER_MODEL = 8

sys.path.insert(0, str(EVALS_DIR))
import adapt_skillevaluator as ad          # noqa: E402
import render_lift as rl                   # noqa: E402

REAL = FIXTURES / "real-failed-run"
SYNTH = FIXTURES / "synthetic-scored-run"

SECTION_A = (
    "schema_version", "record_id", "recorded_at", "skill", "skill_version",
    "case_id", "agent_runner", "agent_model", "judge_model", "n_attempts",
    "score_with_skill", "score_without_skill", "lift", "lift_source",
    "verdict", "status", "error_type", "error_detail", "dataset_digest",
    "dataset_digest_algorithm", "adapter_version", "source_run_dir",
    "provenance",
)


def tool_version() -> str:
    return ad.ADAPTER_VERSION


# ---------------------------------------------------------------------------
# suite
# ---------------------------------------------------------------------------


def run_suite() -> dict:
    results: dict[str, str] = {}

    def check(name, cond, msg=""):
        results[name] = "pass" if cond else f"FAIL: {msg}"

    def by_case(rows):
        return {r["case_id"]: r for r in rows}

    def raises(path, **kw):
        try:
            ad.adapt_run(FIXTURES / path, repo=REPO, **kw)
        except ad.UnrecognisedRunError as e:
            return str(e)
        except Exception as e:                          # noqa: BLE001
            return f"__WRONG_EXCEPTION__ {type(e).__name__}: {e}"
        return None

    # -- the real failed run ------------------------------------------------
    real = ad.adapt_run(REAL, repo=REPO)
    check("real_one_row_per_measurement", len(real) == 1,
          f"expected 1 row (1 case x 1 runner), got {len(real)}")
    r = real[0]
    check("real_schema_version",
          r["schema_version"] == "skill-lift-record/1", r["schema_version"])
    check("real_section_a_complete",
          all(k in r for k in SECTION_A),
          f"missing {[k for k in SECTION_A if k not in r]}")
    check("real_status_failed", r["status"] == "failed", r["status"])
    check("real_scores_are_null",
          r["score_with_skill"] is None and r["score_without_skill"] is None,
          f"{r['score_with_skill']!r}/{r['score_without_skill']!r} — a failed "
          "run must never be recorded as a score")
    check("real_lift_null_not_zero",
          r["lift"] is None and r["lift_source"] == "unavailable",
          f"lift={r['lift']!r} source={r['lift_source']!r}")
    check("real_verdict_no_score", r["verdict"] == "NO SCORE", r["verdict"])
    check("real_no_zero_anywhere",
          not any(r[k] == 0.0 for k in
                  ("score_with_skill", "score_without_skill", "lift")),
          "an empty upstream arm was turned into a 0.0")
    check("real_error_type",
          r["error_type"] == "RewardFileNotFoundError", repr(r["error_type"]))
    check("real_error_detail_present",
          isinstance(r["error_detail"], str) and "reward" in r["error_detail"].lower(),
          repr(r["error_detail"])[:120])
    check("real_error_detail_truncated",
          len(r["error_detail"]) <= 500, len(r["error_detail"]))
    check("real_error_provenance_is_failure_json",
          "failure.json/error/type" in
          (r["provenance"]["error_type"].get("path") or ""),
          r["provenance"]["error_type"])
    for f in ("score_with_skill", "score_without_skill"):
        p = r["provenance"][f]
        check(f"real_{f}_has_reason",
              p["path"] is None and "empty" in (p.get("reason") or ""),
              f"{f}: {p}")
    check("real_null_lift_has_reason",
          r["provenance"]["lift"]["path"] is None
          and bool(r["provenance"]["lift"].get("reason")),
          r["provenance"]["lift"])
    check("real_dataset_digest",
          r["dataset_digest"] ==
          "sha256:a39fa7dc7d0e8f9737f77d99109d9ddf146fada5e15bda7aa4a8370d294e8689",
          repr(r["dataset_digest"]))
    check("real_digest_algorithm",
          r["dataset_digest_algorithm"] == "skill-evaluator-dataset-snapshot/1",
          repr(r["dataset_digest_algorithm"]))
    check("real_skill_from_expected_skill",
          r["skill"] == "secret-guard",
          f"{r['skill']!r} — must come from the dataset entry's "
          "expected_skill, not the run label 'sg1'")
    check("real_skill_version_from_repo",
          r["skill_version"] == (REPO / "skills" / "secret-guard"
                                 / "VERSION").read_text().strip(),
          repr(r["skill_version"]))
    check("real_agent_model", r["agent_model"] == "gpt-5-nano",
          repr(r["agent_model"]))
    check("real_judge_model_distinct",
          r["judge_model"] == "gpt-5.6-sol"
          and r["judge_model"] != r["agent_model"],
          f"judge={r['judge_model']!r} agent={r['agent_model']!r}")
    check("real_agent_runner", r["agent_runner"] == "codex", r["agent_runner"])
    check("real_case_id", r["case_id"] == "sg-no-verify-bypass-request",
          r["case_id"])
    check("real_n_attempts", r["n_attempts"] == 1, repr(r["n_attempts"]))
    check("real_skillevaluator_version",
          r["skillevaluator_version"] == "0.2.0",
          repr(r["skillevaluator_version"]))
    check("real_record_id_stable",
          ad.adapt_run(REAL, repo=REPO)[0]["record_id"] == r["record_id"],
          "record_id is not reproducible across adapts")
    check("real_skill_version_override",
          ad.adapt_run(REAL, repo=REPO,
                       skill_version_override="9.9.9")[0]["skill_version"]
          == "9.9.9", "--skill-version override ignored")

    # -- the synthetic scored run -------------------------------------------
    syn = ad.adapt_run(SYNTH, repo=REPO)
    s = by_case(syn)
    check("synth_row_count", len(syn) == 5, f"got {len(syn)}: {sorted(s)}")
    a = s.get("sk-case-alpha", {})
    check("synth_lift_computed",
          a.get("lift") == 0.44 and a.get("lift_source") == "computed",
          f"{a.get('lift')!r}/{a.get('lift_source')!r}")
    check("synth_scored_status", a.get("status") == "scored", a.get("status"))
    check("synth_verdict_pass", a.get("verdict") == "PASS", a.get("verdict"))
    check("synth_lift_provenance_says_computed",
          "computed" in (a.get("provenance", {}).get("lift", {}).get("path") or ""),
          a.get("provenance", {}).get("lift"))
    b = s.get("sk-case-beta", {})
    check("synth_dict_shaped_score_coerced",
          b.get("score_with_skill") == 0.5 and b.get("score_without_skill") == 0.55,
          f"{b.get('score_with_skill')!r}/{b.get('score_without_skill')!r} — "
          "a per-case dict carrying 'overall' was not read")
    check("synth_verdict_neutral",
          b.get("verdict") == "NEUTRAL" and abs(b.get("lift") + 0.05) < 1e-9,
          f"{b.get('verdict')!r} lift={b.get('lift')!r}")
    g = s.get("sk-case-gamma", {})
    check("synth_partial_one_arm",
          g.get("status") == "partial" and g.get("score_with_skill") == 0.7
          and g.get("score_without_skill") is None,
          f"{g.get('status')!r} {g.get('score_with_skill')!r}/"
          f"{g.get('score_without_skill')!r}")
    check("synth_partial_lift_null",
          g.get("lift") is None and g.get("verdict") == "NO SCORE",
          f"lift={g.get('lift')!r} verdict={g.get('verdict')!r}")
    check("synth_partial_carries_error",
          g.get("error_type") == "JudgeTimeoutError", repr(g.get("error_type")))
    e = s.get("sk-case-epsilon", {})
    check("synth_upstream_lift_marked",
          e.get("lift") == 0.22 and e.get("lift_source") == "upstream",
          f"{e.get('lift')!r}/{e.get('lift_source')!r}")
    check("synth_upstream_lift_not_scored",
          e.get("status") == "partial",
          f"{e.get('status')!r} — an upstream lift with no per-arm scores is "
          "not a fully scored measurement")
    d = s.get("sk-case-delta", {})
    check("synth_unknown_skill_version_null",
          d.get("skill") == "ghost-skill-not-in-repo"
          and d.get("skill_version") is None,
          f"{d.get('skill')!r}/{d.get('skill_version')!r}")
    check("synth_unknown_skill_version_reason",
          "VERSION" in (d.get("provenance", {})
                        .get("skill_version", {}).get("reason") or ""),
          d.get("provenance", {}).get("skill_version"))
    check("synth_passthrough_dimensions",
          isinstance(a.get("dimensions_with_skill"), dict)
          and a["dimensions_with_skill"].get("security") == 0.9,
          repr(a.get("dimensions_with_skill")))
    check("synth_passthrough_custom_lift",
          a.get("custom_lift") == 0.4, repr(a.get("custom_lift")))
    check("synth_grading_mode",
          a.get("grading_mode") == "default_plus_custom", repr(a.get("grading_mode")))
    check("synth_distinct_record_ids",
          len({row["record_id"] for row in syn}) == 5,
          "record_ids collide across cases in one run")
    ptrs = [p.get("path") for row in syn for p in row["provenance"].values()
            if p.get("path")]
    check("synth_provenance_paths_are_locatable",
          all(p.startswith(("/", "file:", "computed:", "override:")) for p in ptrs),
          f"unlocatable provenance paths: "
          f"{[p for p in ptrs if not p.startswith(('/', 'file:', 'computed:', 'override:'))][:3]}")

    # -- shapes that must be refused ----------------------------------------
    m1 = raises("mangled-no-agents")
    check("mangled_no_agents_raises", m1 is not None and "__WRONG" not in m1,
          repr(m1)[:160])
    check("mangled_no_agents_reports_what_it_saw",
          bool(m1) and "runners" in m1 and "Top-level keys seen" in m1,
          "exception must list the keys it actually found")
    check("mangled_no_agents_reports_tried",
          bool(m1) and "with_skill" in m1 and "Tried" in m1,
          "exception must say which paths it tried")
    m2 = raises("mangled-shape-drift")
    check("mangled_no_case_ids_raises", m2 is not None and "__WRONG" not in m2,
          repr(m2)[:160])
    check("mangled_no_case_ids_lists_sources",
          bool(m2) and "trial_failures" in m2 and "dataset_snapshot" in m2,
          "exception must name the places it looked for case ids")
    m3 = raises("mangled-unparseable")
    check("mangled_unparseable_raises", m3 is not None and "__WRONG" not in m3,
          repr(m3)[:160])
    m4 = raises("does-not-exist-at-all")
    check("mangled_missing_dir_raises", m4 is not None and "__WRONG" not in m4,
          repr(m4)[:160])

    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "l.jsonl"
        for path in ("mangled-no-agents", "mangled-shape-drift",
                     "mangled-unparseable"):
            try:
                ad.append_rows(ad.adapt_run(FIXTURES / path, repo=REPO), led)
            except ad.UnrecognisedRunError:
                pass
        check("mangled_writes_no_rows", not led.exists(),
              "an unrecognised run wrote to the ledger")

        # -- ledger round trip ---------------------------------------------
        led2 = Path(td) / "l2.jsonl"
        ad.append_rows(real, led2)
        ad.append_rows(syn, led2)
        back = ad.load_ledger(led2)
        check("ledger_roundtrip", len(back) == 6 and back[0] == real[0],
              f"{len(back)} rows back")
        with open(led2, "a") as f:
            f.write("{not json at all\n\n")
        check("ledger_survives_corrupt_line",
              len(ad.load_ledger(led2)) == 6,
              "one bad line took the whole ledger down")
        check("ledger_is_append_only_text",
              led2.read_text().count("\n") >= 7,
              "rows were not appended as one JSON object per line")

        # -- renderer -------------------------------------------------------
        one = rl.render([real[0]], str(led2))
        check("render_one_row_works", "<svg" in one and "sg-no-verify" in one,
              "single-row render produced no chart")
        check("render_one_row_says_not_a_trend",
              "not a trend" in one, "single-row render implied a trend")
        check("render_failed_run_visible",
              "FAILED RUN" in one and "RewardFileNotFoundError" in one,
              "a failed run was hidden in the HTML")
        check("render_failed_not_zero",
              "no score" in one and "+0.000" not in one,
              "a failed run was drawn as lift 0.000")
        check("render_shows_null_reason",
              "no case was scored in this run" in one,
              "the reason a field is null did not reach the HTML")
        # a real series: the same case, same digest, three skill versions,
        # the middle one a failed run.  The line must connect the two scored
        # points AND the failure must still be drawn.
        v1 = dict(a, recorded_at="2026-01-01T00:00:00", skill_version="0.1.0",
                  record_id="aaaa0001")
        v2 = dict(a, recorded_at="2026-02-01T00:00:00", skill_version="0.2.0",
                  record_id="aaaa0002", status="failed", lift=None,
                  lift_source="unavailable", verdict="NO SCORE",
                  score_with_skill=None, score_without_skill=None,
                  error_type="RewardFileNotFoundError")
        v3 = dict(a, recorded_at="2026-03-01T00:00:00", skill_version="0.3.0",
                  record_id="aaaa0003", lift=0.61)
        series = rl.render([v1, v2, v3], str(led2))
        check("render_series_connects_scored_points",
              series.count("<polyline") == 1, "scored points were not joined")
        check("render_series_keeps_failure_visible",
              "FAILED RUN" in series and series.count("no score") >= 1,
              "the failed run in the middle of a series was hidden")
        check("render_series_labels_skill_versions",
              "v0.1.0" in series and "v0.2.0" in series and "v0.3.0" in series,
              "the x axis does not say which skill version each point is")
        full = rl.render(real + syn, str(led2))
        check("render_multi_row_groups",
              "skill/case/model series" in full and full.count('class="grp"') == 6,
              f"expected 6 series, got {full.count('class=\"grp\"')}")
        check("render_mixed_digest_warning",
              "NOT\ncomparable" in full or "NOT comparable" in full,
              "two dataset digests rendered without a comparability warning")
        check("render_no_external_assets",
              ("http://" not in full and "https://" not in full
               and "<script" not in full and "<link" not in full
               and "src=" not in full),
              "HTML references something outside the file")
        check("render_theme_aware",
              "prefers-color-scheme" in full and 'data-theme="dark"' in full,
              "HTML is not theme-aware")
        check("render_empty_ledger",
              "Ledger is empty" in rl.render([], str(led2)),
              "empty ledger rendered without saying so")
        nasty = dict(real[0])
        nasty["skill"] = '<img src=x onerror=alert(1)>'
        check("render_escapes_html",
              "<img src=x" not in rl.render([nasty], str(led2)),
              "ledger content was injected into the page unescaped")

    # -- pinned constants + doc coverage ------------------------------------
    check("verdict_bands_pinned",
          (ad.LIFT_PASS_THRESHOLD, ad.LIFT_FAIL_THRESHOLD) == (0.05, -0.10),
          f"{ad.LIFT_PASS_THRESHOLD}/{ad.LIFT_FAIL_THRESHOLD} drifted from "
          "skills/skill-lift-eval/skill_lift_eval.py")
    check("verdict_band_edges",
          (ad.lift_verdict(0.05), ad.lift_verdict(0.049),
           ad.lift_verdict(-0.10), ad.lift_verdict(None))
          == ("PASS", "NEUTRAL", "FAIL", "NO SCORE"),
          "verdict banding is wrong at the edges")
    check("score_coercion_rejects_bools",
          ad.coerce_score(True) is None and ad.coerce_score({"overall": True}) is None,
          "a boolean was accepted as a score")
    check("score_coercion_rejects_garbage",
          ad.coerce_score({"note": "n/a"}) is None and ad.coerce_score([]) is None
          and ad.coerce_score("n/a") is None,
          "unreadable per-case values were coerced into a number")
    schema = (EVALS_DIR / "SCHEMA.md").read_text()
    undocumented = [k for k in real[0] if f"`{k}`" not in schema]
    check("schema_documents_every_field", not undocumented,
          f"fields in the row but not in SCHEMA.md: {undocumented}")
    check("schema_states_ours_vs_upstream",
          "Fields we own" in schema and "opportunistically" in schema,
          "SCHEMA.md does not separate our fields from passthrough")
    check("schema_version_matches_adapter",
          f"`{ad.SCHEMA_VERSION}`" in schema, ad.SCHEMA_VERSION)

    return results


# ---------------------------------------------------------------------------
# telemetry (same contract as every skill's tests in this repo)
# ---------------------------------------------------------------------------


def record(model: str, results: dict, duration: float) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "skill": "telemetry/evals",
        "skill_version": tool_version(),
        "model": model,
        "skillevaluator": "not required (fixtures only)",
        "n_pass": sum(1 for v in results.values() if v == "pass"),
        "n_fail": sum(1 for v in results.values() if v.startswith("FAIL")),
        "duration_s": round(duration, 2),
        "results": results,
    }
    with open(LEDGER, "a") as f:
        f.write(json.dumps(entry) + "\n")


def load_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]


def runs_for(model: str) -> int:
    v = tool_version()
    return sum(1 for e in load_ledger()
               if e.get("model") == model and e.get("skill_version") == v)


def report() -> None:
    rows: dict[tuple, list[dict]] = {}
    for e in load_ledger():
        rows.setdefault((e["skill_version"], e["model"]), []).append(e)
    if not rows:
        print("ledger empty — no recorded runs yet")
        return
    print(f"{'version':16s} {'model':26s} {'runs':>4s} {'pass_rate':>9s} "
          f"{'dur_s mean±SD':>14s}")
    for (ver, model), es in sorted(rows.items()):
        total = sum(e["n_pass"] + e["n_fail"] for e in es)
        passed = sum(e["n_pass"] for e in es)
        durs = [e["duration_s"] for e in es]
        sd = statistics.stdev(durs) if len(durs) > 1 else 0.0
        print(f"{ver:16s} {model:26s} {len(es):>4d} {passed/total:>8.0%} "
              f"{statistics.mean(durs):>7.2f}±{sd:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="model id of the calling assistant")
    ap.add_argument("--auto", action="store_true",
                    help=f"only run+record if this (model, version) has "
                         f"< {AUTO_RUNS_PER_MODEL} runs")
    ap.add_argument("--no-record", action="store_true",
                    help="run the suite without writing to the shared ledger")
    ap.add_argument("--report", action="store_true",
                    help="print the model x version matrix")
    args = ap.parse_args()

    if args.report:
        report()
        return
    if not args.model:
        sys.exit("--model required (or use --report)")
    if args.auto and runs_for(args.model) >= AUTO_RUNS_PER_MODEL:
        print(f"telemetry: {AUTO_RUNS_PER_MODEL} runs already recorded for "
              f"{args.model} @ {tool_version()} — skipping")
        return

    t0 = time.time()
    results = run_suite()
    duration = time.time() - t0
    if not args.no_record:
        record(args.model, results, duration)

    n_fail = sum(1 for v in results.values() if v.startswith("FAIL"))
    for k, v in results.items():
        print(f"  {k}: {v}")
    print(f"{len(results)} tests — "
          f"{'ALL PASS' if n_fail == 0 else f'{n_fail} FAILURES'} "
          f"({duration:.1f}s)")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
