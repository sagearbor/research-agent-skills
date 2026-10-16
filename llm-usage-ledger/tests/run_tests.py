#!/usr/bin/env python3
"""Regression tests + telemetry for the llm-usage-ledger global skill.

Fully deterministic — no LLM server or network required. Exercises the
ledger write/read/aggregate path (the part other repos and the org depend
on), the price series, ingest, and the HTML report, all with a temp ledger
dir, plus CLI integrity.

Usage: run_tests.py --model <id> [--auto] | --report
"""
import argparse
import datetime
import json
import os
import statistics
import subprocess
import pathlib
import re
import sys
import tempfile
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
LEDGER = SKILL_DIR.parent / "telemetry" / "llm-usage-ledger.jsonl"
AUTO_RUNS = 8

sys.path.insert(0, str(SKILL_DIR))


def version():
    try:
        return (SKILL_DIR / "VERSION").read_text().strip()
    except OSError:
        return "unversioned"


def run_suite():
    r = {}

    def check(name, cond, why=""):
        r[name] = "pass" if cond else f"FAIL: {why}"

    # 1. module imports clean
    try:
        import llm_usage_ledger as lp
        check("module_imports", True)
    except Exception as e:
        check("module_imports", False, repr(e))
        return r

    with tempfile.TemporaryDirectory() as td:
        os.environ["LLM_TOKEN_LEDGER_DIR"] = td

        # 2. ledger write/read roundtrip with full schema. Registry-tag the
        # repo (fake HOME, like 9a2 below) so d1 is populated instead of
        # silently absent, and assert on `back[0]` — what actually survived
        # the write-to-disk + read_ledgers()/_normalize() trip — not on `ev`,
        # the in-memory dict log_usage() was about to write. Checking `ev`
        # would pass even if serialization or _normalize() dropped a field.
        fake_home_ru = Path(td) / "home-roundtrip"
        (fake_home_ru / ".agent-coach").mkdir(parents=True)
        repo_key = lp.repo_remote_key(".")
        (fake_home_ru / ".agent-coach" / "projects.json").write_text(
            json.dumps({repo_key: {"d1": "D1-TEST-CODE"}}))
        real_home_ru = Path.home
        Path.home = staticmethod(lambda: fake_home_ru)  # noqa
        try:
            usage = {"prompt_tokens": 100, "completion_tokens": 20,
                     "completion_tokens_details": {"reasoning_tokens": 7}}
            ev = lp.log_usage("test-model", usage, 1.5, task_tag="unit")
        finally:
            Path.home = real_home_ru
        back = lp.read_ledgers()
        check("ledger_roundtrip", len(back) == 1
              and back[0]["prompt_tokens"] == 100
              and back[0]["reasoning_tokens"] == 7
              and back[0]["repo"] == repo_key
              and back[0]["d1"] == "D1-TEST-CODE"
              and back[0]["task_tag"] == "unit",
              f"got {back!r}")
        # Every field log_usage()+read_ledgers() actually produce for a live
        # (non-ingested) event — SCHEMA.md's v1 set minus tokens_in/tokens_out
        # (a *different* writer's schema, exercised separately below) and
        # minus subscription/cache_*/src_id (ingest-only, see schema_complete_ingest).
        required = {"schema", "ts", "duration_s", "provider", "machine",
                    "user", "project", "repo", "d1", "model",
                    "prompt_tokens", "completion_tokens", "reasoning_tokens",
                    "task_tag"}
        check("schema_complete", required.issubset(back[0].keys()),
              f"missing {required - set(back[0].keys())}, "
              f"got {sorted(back[0].keys())}")

        # 2b. provider param recorded (default stays lmstudio for back-compat)
        ev_p = lp.log_usage("test-model", {"prompt_tokens": 1,
                                           "completion_tokens": 1},
                            0.1, provider="azure")
        check("provider_param", ev["provider"] == "lmstudio"
              and ev_p["provider"] == "azure"
              and not lp.is_local_event(ev_p))

        # 3. reasoning tokens absent -> null, not 0
        ev2 = lp.log_usage("test-model", {"prompt_tokens": 5,
                                          "completion_tokens": 2}, 0.1)
        check("reasoning_null_not_zero", ev2["reasoning_tokens"] is None)

        # 4. per-user filename (shared-dir collision safety)
        files = list(Path(td).glob("lmstudio-*.jsonl"))
        check("per_user_filename", len(files) == 1
              and lp.whoami() in files[0].name
              and lp.machine_name() in files[0].name)

        # 5. aggregation math on a known fixture
        lp.log_usage("model-b", {"prompt_tokens": 50, "completion_tokens": 10},
                     2.0, project="proj-x")
        agg = lp.aggregate(lp.read_ledgers(), by="model")
        check("aggregate_math",
              agg.get("test-model", {}).get("prompt") == 106
              and agg.get("model-b", {}).get("calls") == 1,
              f"got {agg!r}")

        # 6. hourly windows + burst ratio derivable from raw events
        w, mean, peak, burst = lp.hourly_windows(lp.read_ledgers())
        check("windows_derivable", len(w) >= 1 and mean > 0 and peak,
              f"got {w!r}")

        # 7. corrupt ledger line skipped, not fatal
        files[0].write_text(files[0].read_text() + "NOT JSON\n")
        check("corrupt_line_tolerated", len(lp.read_ledgers()) == 4)

        # 8. scope='all' merges foreign-writer schemas (tokens_in/tokens_out)
        (Path(td) / "ledger.jsonl").write_text(json.dumps(
            {"schema": 1, "ts": "2026-07-27T12:00:00-04:00",
             "provider": "local", "model": "other-writer-model",
             "tokens_in": 500, "tokens_out": 40,
             "project": "another-repo"}) + "\n")
        merged = lp.read_ledgers(scope="all")
        norm = [e for e in merged if e.get("model") == "other-writer-model"]
        check("foreign_schema_merged", len(merged) == 5 and len(norm) == 1
              and norm[0]["prompt_tokens"] == 500
              and lp.is_local_event(norm[0]),
              f"merged={len(merged)} norm={norm!r}")

        # 8a. D1 — identity is NEVER inferred from whoever runs the report.
        # ledger.jsonl carries no `user`/`machine` (22,229 live rows do not);
        # defaulting them to the reader silently credits one person with every
        # foreign writer's spend on a SHARED ledger dir. Unknown stays unknown.
        check("unattributed_not_reader_identity",
              len(norm) == 1
              and norm[0]["user"] == lp.UNATTRIBUTED
              and norm[0]["machine"] == lp.UNATTRIBUTED
              and norm[0]["user"] != lp.whoami()
              and norm[0]["machine"] != lp.machine_name(),
              f"got user={norm[0]['user']!r} machine={norm[0]['machine']!r}"
              if norm else "foreign-schema row missing")

        # 8b. model-name normalization for price matching
        check("normalize_names",
              lp.normalize_model_name("lmstudio-community/Qwen3-32B-MLX-4bit")
              == "qwen3-32b"
              and lp.normalize_model_name("google/gemma-4-e4b:2")
              == "gemma-4-e4b")

        # 8c. cheapest-hosted match + as-of price join (no network)
        table = {"together/qwen3-32b": {"mode": "chat",
                                        "input_cost_per_token": 4e-7,
                                        "output_cost_per_token": 1.2e-6},
                 "groq/qwen3-32b": {"mode": "chat",
                                    "input_cost_per_token": 2.9e-7,
                                    "output_cost_per_token": 5.9e-7}}
        hit = lp.match_price("Qwen3-32B-MLX-4bit", table)
        check("median_hosted_match", hit is not None
              and abs(hit[0] - 0.345) < 1e-6 and "median of 2" in hit[2],
              f"got {hit!r}")
        table["azure/qwen3-32b-fp8"] = {"mode": "chat",
                                        "input_cost_per_token": 1e-7,
                                        "output_cost_per_token": 2e-7}
        table["x/gpt-5-nano"] = {"mode": "chat",
                                 "input_cost_per_token": 5e-8,
                                 "output_cost_per_token": 4e-7}
        check("no_variant_overmatch",
              lp.match_price("gpt-5", table) is None  # nano is NOT gpt-5
              # fp8 listing accepted as the same model; it is the only Azure
              # listing, so Azure-preference picks it (0.1)
              and abs(lp.match_price("qwen3-32b", table)[0] - 0.1) < 1e-6,
              "prefix over-match not blocked or fp8 suffix not accepted")
        az = lp.match_price("qwen3-32b", {
            "together/qwen3-32b": {"mode": "chat", "input_cost_per_token": 4e-7, "output_cost_per_token": 1.2e-6},
            "groq/qwen3-32b": {"mode": "chat", "input_cost_per_token": 2.9e-7, "output_cost_per_token": 5.9e-7},
            "azure_ai/qwen3-32b": {"mode": "chat", "input_cost_per_token": 9e-7, "output_cost_per_token": 9e-7}})
        check("azure_listing_preferred", az is not None and abs(az[0] - 0.9) < 1e-6
              and az[2].startswith("azure"), f"got {az!r}")
        (Path(td) / "prices.jsonl").write_text(
            '{"ts":"2026-07-01","model":"qwen3-32b","input_per_m":0.40,"output_per_m":1.2,"source":"t"}\n'
            '{"ts":"2026-07-20","model":"qwen3-32b","input_per_m":0.29,"output_per_m":0.59,"source":"t"}\n'
            '{"ts":"2026-07-01","model":"reference","input_per_m":2.5,"output_per_m":10.0,"source":"t"}\n')
        s = lp.load_price_series()
        early = lp.as_of_price("qwen3-32b", "2026-07-10T12:00:00", s)
        late = lp.as_of_price("qwen3-32b", "2026-07-26T12:00:00", s)
        fall = lp.as_of_price("unknown-model", "2026-07-26T12:00:00", s)
        check("as_of_join", early[0] == 0.40 and late[0] == 0.29
              and fall[2] == "reference",
              f"early={early} late={late} fall={fall}")
        # back-fill: usage BEFORE a model's first price uses that first price,
        # never the frontier reference (was ~50x too high, 2026-10-16)
        before = lp.as_of_price("qwen3-32b", "2026-06-01T12:00:00", s)
        check("backfill_first_price", before[0] == 0.40 and before[2] == "model",
              f"before={before}")
        (Path(td) / "prices.jsonl").write_text(
            (Path(td) / "prices.jsonl").read_text()
            + '{"ts":"2026-10-16","model":"reference-open","input_per_m":0.2,"output_per_m":1.0,"source":"t"}\n')
        s = lp.load_price_series()
        loc = lp.as_of_price("unknown-local", "2026-07-26T12:00:00", s, local=True)
        cld = lp.as_of_price("unknown-cloud", "2026-07-26T12:00:00", s, local=False)
        check("unpriced_local_uses_open_reference",
              loc[0] == 0.2 and loc[2] == "reference-open"
              and cld[0] == 2.5 and cld[2] == "reference",
              f"local={loc} cloud={cld}")
        (Path(td) / "prices.jsonl").write_text(
            '{"ts":"2026-07-01","model":"qwen3-32b","input_per_m":0.40,"output_per_m":1.2,"source":"t"}\n'
            '{"ts":"2026-07-20","model":"qwen3-32b","input_per_m":0.29,"output_per_m":0.59,"source":"t"}\n'
            '{"ts":"2026-07-01","model":"reference","input_per_m":2.5,"output_per_m":10.0,"source":"t"}\n')

        lp.LOCAL_PRICING = "opus"
        fr = lp.as_of_price("qwen3-32b", "2026-07-26T12:00:00", s, local=True)
        lp.LOCAL_PRICING = "same-model"
        check("local_as_tier_without_price_falls_back", fr[2] == "reference" and fr[0] == 2.5, f"got {fr}")
        nt = lp.newest_claude({
            "claude-opus-5": {"input_cost_per_token": 5e-6, "output_cost_per_token": 2.5e-5},
            "claude-opus-5-5": {"input_cost_per_token": 4e-6, "output_cost_per_token": 2e-5},
            "claude-opus-4-8": {"input_cost_per_token": 5e-6, "output_cost_per_token": 2.5e-5},
            "claude-opus-4-20250514": {"input_cost_per_token": 1.5e-5, "output_cost_per_token": 7.5e-5},
            "us.anthropic.claude-opus-9": {"input_cost_per_token": 1e-6, "output_cost_per_token": 1e-6}}, "opus")
        check("newest_claude_tier", nt is not None and nt[2] == "claude-opus-5-5"
              and abs(nt[0] - 4.0) < 1e-6, f"got {nt}")
        check("pipeline_plan_calls_are_subscription",
              lp.is_subscription_event({"provider": "claude-cli", "endpoint_host": "subscription"})
              and lp.is_subscription_event({"provider": "codex-cli"})
              and not lp.is_subscription_event({"provider": "azure", "endpoint_host": "x.openai.azure.com"}),
              "claude-cli/codex-cli plan usage booked as cloud spend")

        # 8d. D2 — prices.jsonl lives in the ledger dir but is a PRICE series,
        # not usage. The scope="all" glob swept it in and counted every dated
        # price row (86 live) as a zero-token call, inflating the calls tile.
        after_prices = lp.read_ledgers(scope="all")
        n_price_rows = sum(1 for e in after_prices if "input_per_m" in e)
        check("prices_file_not_counted_as_usage",
              len(after_prices) == 5 and n_price_rows == 0,
              f"{len(after_prices)} events (expected 5), "
              f"{n_price_rows} price rows counted as usage")

        # 9. html report renders from the merged dir (interactive version:
        # data island + filter controls + static fallback table)
        out = Path(td) / "report.html"
        lp.html_report(out)
        html = out.read_text()
        check("html_report_renders",
              'type="application/json"' in html
              and "another-repo" in html          # data island has the project
              and 'data-c="local"' in html        # class toggle chips
              and 'data-m="usd"' in html          # $/tokens metric toggle
              and 'id="mlist"' in html            # collapsible model filter
              and "<table>" in html,              # JS-independent fallback
              "missing data island / filters / table")

        # 9a1. D1 (dashboard half) — records with no user get their OWN bucket
        # in the report, never folded into a person. The chart rows already
        # carry "U"; the page has to spend it.
        m_isl = re.search(r'<script id="d" type="application/json">(.*?)</script>',
                          html, re.S)
        island = json.loads(m_isl.group(1)) if m_isl else {"rows": []}
        folded = [r for r in island["rows"]
                  if r.get("m") == "other-writer-model"
                  and r.get("U") != lp.UNATTRIBUTED]
        check("dashboard_unattributed_bucket",
              'id="userbars"' in html
              and any(r.get("U") == lp.UNATTRIBUTED for r in island["rows"])
              and not folded,
              f"no per-person bucket in the page, or unattributed rows were "
              f"folded into a person: {folded!r}")

        # 9a2. claude-code ingest: parse fixture transcript, idempotent merge
        fake_home = Path(td) / "home"
        proj = fake_home / ".claude" / "projects" / "-Users-x-repo"
        proj.mkdir(parents=True)
        # A real (non-git) cwd, not "/Users/x/repo" (which doesn't exist on
        # disk): project_tag() only runs when Path(cwd).is_dir() is True, so
        # a fake path silently takes the d1=None fallback and d1 could never
        # round-trip through this test. unjoinable_repo_key() is used because
        # this dir has no git remote.
        fake_cwd = Path(td) / "fake-repo-cwd"
        fake_cwd.mkdir()
        cwd_key = lp.unjoinable_repo_key(str(fake_cwd))
        (fake_home / ".agent-coach").mkdir(parents=True)
        (fake_home / ".agent-coach" / "projects.json").write_text(
            json.dumps({cwd_key: {"d1": "D1-INGEST-TEST"}}))
        line = json.dumps({"uuid": "u-1", "timestamp": "2026-07-27T01:02:03Z",
                           "cwd": str(fake_cwd),
                           "message": {"model": "claude-sonnet-5", "usage": {
                               "input_tokens": 10,
                               "cache_creation_input_tokens": 90,
                               "cache_read_input_tokens": 1000,
                               "output_tokens": 20}}})
        (proj / "sess.jsonl").write_text(line + "\nnot json but no usage\n")
        real_home = Path.home
        Path.home = staticmethod(lambda: fake_home)  # noqa
        try:
            lp.ingest_claude_code()
            lp.ingest_claude_code()  # second run must not duplicate
        finally:
            Path.home = real_home
        derived = list(Path(td).glob("claude-code-*.jsonl"))
        ok = False
        if len(derived) == 1:
            recs = [json.loads(l) for l in
                    derived[0].read_text().splitlines()]
            ok = (len(recs) == 1 and recs[0]["prompt_tokens"] == 10
                  and recs[0]["cache_w5_tokens"] == 90
                  and recs[0]["cache_read_tokens"] == 1000
                  and recs[0]["subscription"] is True
                  and recs[0]["project"] == fake_cwd.name)
        check("claude_code_ingest", ok, f"derived={derived!r}")

        # 9a2b. full ingest schema survives read_ledgers()/_normalize(), not
        # just the raw on-disk write 9a2 checked above.
        back_ingest = [e for e in lp.read_ledgers(scope="all")
                       if e.get("provider") == "claude-code"]
        required_ingest = {"schema", "src_id", "ts", "provider",
                           "subscription", "machine", "user", "project",
                           "repo", "d1", "model", "prompt_tokens",
                           "completion_tokens", "cache_read_tokens",
                           "cache_w5_tokens", "cache_w1h_tokens", "task_tag"}
        check("schema_complete_ingest",
              len(back_ingest) == 1
              and required_ingest.issubset(back_ingest[0].keys())
              and back_ingest[0]["repo"] == cwd_key
              and back_ingest[0]["d1"] == "D1-INGEST-TEST",
              f"missing {required_ingest - set(back_ingest[0].keys()) if back_ingest else required_ingest}, "
              f"got {back_ingest!r}")

        # 9a3. subscription rows separate in aggregation + cache discount
        (Path(td) / "prices.jsonl").write_text(
            (Path(td) / "prices.jsonl").read_text()
            + '{"ts":"2026-07-01","model":"claude-sonnet-5",'
              '"input_per_m":3.0,"output_per_m":15.0,"source":"t"}\n')
        ev = lp._all_usage_events()
        sub = [e for e in ev if e["sub"]]
        rows2, _g = lp._agg_rows(ev, lp.load_price_series())
        subrow = [r for r in rows2 if r["u"]]
        # 10 in @$3 + 20 out @$15 + 1000 reads @10% + 90 5m-writes @1.25x
        expect = (10/1e6*3.0 + 20/1e6*15.0 + 1000/1e6*0.3
                  + 90/1e6*3.0*1.25)
        check("subscription_agg_and_cache_price",
              len(sub) == 1 and len(subrow) == 1
              and abs(subrow[0]["s"] - round(expect, 4)) < 1e-6,
              f"sub={sub!r} subrow={subrow!r} expect={expect}")

        # 9b. hostile model name cannot break out of the static table
        lp.log_usage("<script>alert(1)</script>", {"prompt_tokens": 1,
                                                   "completion_tokens": 1}, 0.1)
        lp.html_report(out)
        check("html_escapes_model_names",
              "<script>alert(1)</script>" not in out.read_text())

    # ---- D3/D4: ingest identity + error-log hygiene, in their own ledger dir
    # so the fixtures above (which assert exact event counts) stay untouched.
    with tempfile.TemporaryDirectory() as td2:
        os.environ["LLM_TOKEN_LEDGER_DIR"] = td2

        # D3a. SSH and HTTPS spellings of the SAME repo must canonicalize to
        # the SAME key. They did not, so the documented cross-developer join
        # split in half the moment two people cloned differently.
        forms = ("git@github.com:Org/Repo.git",
                 "https://github.com/org/repo.git",
                 "https://github.com/org/repo",
                 "https://tok:x@github.com/org/repo.git",
                 "ssh://git@github.com:22/org/repo.git")
        keys = {lp.canonical_repo_key(u) for u in forms}
        check("repo_key_canonical_across_url_forms",
              keys == {"github.com/org/repo"}, f"got {keys!r}")

        # D3b. No remote -> an explicit unjoinable marker. The old fallback was
        # "path:" + abspath, which leaks the local username into a shared
        # ledger and never joins with anybody anyway.
        scratch = Path(td2) / "scratch"
        scratch.mkdir()
        nk = lp.repo_remote_key(str(scratch))
        check("no_remote_marker_not_a_path",
              nk.startswith(lp.NO_REMOTE) and not nk.startswith("path:")
              and lp.whoami() not in nk and str(scratch) not in nk,
              f"got {nk!r}")

        # D3c. ingest must stamp `repo` (the join key) and `d1` from the git
        # remote of the RECORDED cwd. Both are empty on all 46,668 live rows.
        work = Path(td2) / "work"
        work.mkdir()
        for cmd in (["git", "init", "-q", str(work)],
                    ["git", "-C", str(work), "remote", "add", "origin",
                     "git@github.com:Org/Repo.git"]):
            subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        fake_home2 = Path(td2) / "home"
        proj2 = fake_home2 / ".claude" / "projects" / "-w"
        proj2.mkdir(parents=True)
        # registry keyed by the LEGACY (raw SSH url) spelling agent-coach
        # writes today — the d1 join must survive canonicalization.
        (fake_home2 / ".agent-coach").mkdir(parents=True)
        (fake_home2 / ".agent-coach" / "projects.json").write_text(json.dumps(
            {"git@github.com:org/repo": {"project": "Repo", "d1": "D1-4471",
                                         "tier": "project"}}))
        rec_line = json.dumps({
            "uuid": "w-1", "timestamp": "2026-08-01T01:02:03Z",
            "cwd": str(work),
            "message": {"model": "claude-sonnet-5",
                        "usage": {"input_tokens": 7, "output_tokens": 3}}})
        scratch_line = json.dumps({
            "uuid": "w-2", "timestamp": "2026-08-01T01:02:04Z",
            "cwd": str(scratch),
            "message": {"model": "claude-sonnet-5",
                        "usage": {"input_tokens": 1, "output_tokens": 1}}})
        # D4. An unparsed line must be logged by SHAPE only. 400 raw characters
        # of a transcript in a log the user is then told to email breaks the
        # counts-never-content invariant the whole ledger rests on.
        secret = "PARTICIPANT-12345-NEVER-LOG-THIS"
        broken = '{"usage": {"input_tokens": 1}, "note": "' + secret + '"'
        (proj2 / "sess.jsonl").write_text(
            rec_line + "\n" + scratch_line + "\n" + broken + "\n")

        real_home2 = Path.home
        Path.home = staticmethod(lambda: fake_home2)  # noqa
        try:
            lp.ingest_claude_code()
        finally:
            Path.home = real_home2
        drv = list(Path(td2).glob("claude-code-*.jsonl"))
        recs = {}
        if len(drv) == 1:
            for l in drv[0].read_text().splitlines():
                got = json.loads(l)
                recs[got["src_id"]] = got
        r1, r2 = recs.get("w-1", {}), recs.get("w-2", {})
        check("ingest_stamps_repo_join_key",
              r1.get("repo") == "github.com/org/repo"
              and r2.get("repo", "").startswith(lp.NO_REMOTE)
              and "cwd" not in r1,
              f"repo fields: {r1.get('repo')!r} / {r2.get('repo')!r}")
        check("ingest_d1_from_registry_legacy_key",
              r1.get("d1") == "D1-4471" and r2.get("d1") is None,
              f"d1 fields: {r1.get('d1')!r} / {r2.get('d1')!r}")

        elog = Path(td2) / "ingest-errors-claude-code.log"
        etxt = elog.read_text() if elog.exists() else ""
        check("ingest_error_logs_shape_not_content",
              elog.exists() and secret not in etxt
              and "PARTICIPANT" not in etxt
              and "len=" in etxt and "sha256=" in etxt and "field=" in etxt,
              f"error log was {etxt!r}")

        contact = lp.ingest_contact()
        # the default must name a ROLE, not a person (it was a live
        # deliverable address at an institution). "sage" alone is not a usable
        # probe -- it is a substring of "usage".
        check("ingest_contact_not_an_individual",
              "@duke.edu" not in contact.lower()
              and "sage.arbor" not in contact.lower()
              and "LLM_LEDGER_SUPPORT_CONTACT" in contact,
              f"got {contact!r}")
        os.environ["LLM_LEDGER_SUPPORT_CONTACT"] = "ops@example.org"
        try:
            check("ingest_contact_env_overridable",
                  lp.ingest_contact() == "ops@example.org",
                  f"got {lp.ingest_contact()!r}")
        finally:
            os.environ.pop("LLM_LEDGER_SUPPORT_CONTACT", None)

        # Backfilling `repo` onto ALREADY-ingested rows is a MIGRATION, so it
        # is opt-in (`ingest claude-code --refresh`) and never automatic — a
        # plain re-ingest must leave existing records exactly as they are.
        stale = dict(r1)
        stale.pop("repo", None)
        stale.pop("d1", None)
        drv[0].write_text(json.dumps(stale) + "\n")
        Path.home = staticmethod(lambda: fake_home2)  # noqa
        try:
            lp.ingest_claude_code()
            kept = [json.loads(l) for l in drv[0].read_text().splitlines()
                    if json.loads(l)["src_id"] == "w-1"][0]
            lp.ingest_claude_code(refresh=True)
            fixed = [json.loads(l) for l in drv[0].read_text().splitlines()
                     if json.loads(l)["src_id"] == "w-1"][0]
        finally:
            Path.home = real_home2
        check("repo_backfill_is_opt_in_migration",
              "repo" not in kept and fixed.get("repo") == "github.com/org/repo",
              f"plain re-ingest gave {kept.get('repo')!r}, "
              f"--refresh gave {fixed.get('repo')!r}")

    os.environ.pop("LLM_TOKEN_LEDGER_DIR", None)

    # 10. project auto-detect returns a non-empty string
    check("project_autodetect", isinstance(lp.detect_project(), str)
          and len(lp.detect_project()) > 0)

    # 11. CLI help exits 0
    p = subprocess.run([sys.executable,
                        str(SKILL_DIR / "llm_usage_ledger.py"),
                        "--help"], capture_output=True, timeout=20)
    # user must survive normalization — it was silently dropped, which made
    # any per-user filter on a SHARED ledger dir return zero rows
    evs = ac._all_usage_events() if False else None
    import llm_usage_ledger as _L
    ev = {"ts": "2026-07-09T10:00:00+00:00", "model": "m", "user": "alice",
          "project": "p", "prompt_tokens": 10, "completion_tokens": 2}
    norm = {"ts": ev["ts"], "user": ev.get("user") or "?"}
    check("normalizer_keeps_user_field",
          "\"user\": e.get(\"user\")" in open(
              str(pathlib.Path(_L.__file__))).read(),
          "user is not carried through _all_usage_events")
    check("agg_rows_carry_user",
          '"U": U' in open(str(pathlib.Path(_L.__file__))).read(),
          "chart rows do not carry a user, so per-user views are impossible")

    # reports must never land in $HOME or the cwd — they are byproducts, and
    # littering someone's home directory is how a tool gets uninstalled
    rp = _L.default_report_path()
    check("report_not_in_home", rp.parent != pathlib.Path.home(),
          f"default report path is directly in $HOME: {rp}")
    check("report_in_hidden_state_dir",
          ".llm_token_ledger" in str(rp) and "reports" in str(rp),
          f"got {rp}")
    check("report_path_absolute", rp.is_absolute(),
          "a relative default would litter whatever cwd you ran from")

    # cross-developer identity: the join key must not be the local folder name
    check("repo_key_is_not_folder_name",
          _L.repo_remote_key(".") != _L.detect_project("."),
          "repo key fell back to the folder name — two devs would not join")
    check("project_tag_has_repo_and_d1",
          set(("repo", "project", "d1")) <= set(_L.project_tag(".")),
          f"got {_L.project_tag('.')}")

    check("cli_help", p.returncode == 0)

    # LM Studio server-log importer: counts only, idempotent, dedupes against
    # calls another writer already logged, and never copies text (2026-10-16).
    with tempfile.TemporaryDirectory() as td3:
        os.environ["LLM_TOKEN_LEDGER_DIR"] = td3
        logs = Path(td3) / "lms"
        (logs / "2026-10").mkdir(parents=True)
        block = (lambda rid, tin, tout, hh: (
            f"[2026-10-16 {hh}:00:05][INFO][m] Generated prediction: {{\n"
            f'  "id": "{rid}",\n  "model": "qwen3.5-122b-a10b",\n'
            f'  "choices": [{{"message": {{"content": "PRIVATE TEXT"}}}}],\n'
            f'  "usage": {{"prompt_tokens": {tin}, "completion_tokens": {tout}}}\n}}\n'))
        (logs / "2026-10" / "a.log").write_text(
            "[2026-10-16 09:00:00][DEBUG] Received request: POST {\n  \"x\": 1\n}\n"
            + block("chatcmpl-1", 100, 10, "09") + block("chatcmpl-2", 200, 20, "10"))
        # chatcmpl-2's call was already logged by the user's own code
        when = _L.datetime.datetime(2026, 10, 16, 10, 0, 30).astimezone().isoformat()
        (Path(td3) / "own.jsonl").write_text(json.dumps({
            "ts": when, "provider": "local", "model": "qwen3.5-122b-a10b",
            "prompt_tokens": 200, "completion_tokens": 20}) + "\n")
        os.environ["LMSTUDIO_LOG_DIR"] = str(logs)
        _L.ingest_lmstudio(); _L.ingest_lmstudio()
        out = _L._ingest_derived_file(_L.LMSTUDIO_TAG)
        rows = [json.loads(x) for x in out.read_text().splitlines()]
        check("lmstudio_ingest_counts_dedupes_idempotent",
              [r["src_id"] for r in rows] == ["chatcmpl-1"] and rows[0]["prompt_tokens"] == 100
              and _L.is_local_event(rows[0]), f"got {rows}")
        check("lmstudio_ingest_never_copies_text", "PRIVATE" not in out.read_text())
        os.environ.pop("LMSTUDIO_LOG_DIR", None)
        b = Path(td3) / "bin"
        shim = _L.install_cli(b)
        check("install_writes_launcher", shim.exists() and os.access(shim, os.X_OK)
              and "ailedger launcher" in shim.read_text()
              and str(Path(_L.__file__).resolve().parent) in shim.read_text())
    os.environ.pop("LLM_TOKEN_LEDGER_DIR", None)
    return r


def record(model, results, dur):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps({
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "skill": "llm-usage-ledger", "skill_version": version(),
            "model": model,
            "n_pass": sum(1 for v in results.values() if v == "pass"),
            "n_fail": sum(1 for v in results.values() if v.startswith("FAIL")),
            "duration_s": round(dur, 2), "results": results}) + "\n")


def entries():
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        rows = {}
        for e in entries():
            rows.setdefault((e["skill_version"], e["model"]), []).append(e)
        for (v, m), es in sorted(rows.items()):
            tot = sum(e["n_pass"] + e["n_fail"] for e in es)
            ok = sum(e["n_pass"] for e in es)
            d = [e["duration_s"] for e in es]
            sd = statistics.stdev(d) if len(d) > 1 else 0.0
            print(f"{v:16s} {m:26s} runs={len(es)} pass={ok/tot:.0%} "
                  f"dur={statistics.mean(d):.2f}±{sd:.2f}s")
        return
    if not a.model:
        sys.exit("--model required (or --report)")
    n = sum(1 for e in entries()
            if e["model"] == a.model and e["skill_version"] == version())
    if a.auto and n >= AUTO_RUNS:
        print(f"telemetry: {AUTO_RUNS} runs recorded — skipping")
        return
    t0 = time.time()
    res = run_suite()
    dur = time.time() - t0
    record(a.model, res, dur)
    fails = sum(1 for v in res.values() if v.startswith("FAIL"))
    for k, v in res.items():
        print(f"  {k}: {v}")
    print("ALL PASS" if not fails else f"{fails} FAILURES")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
