#!/usr/bin/env python3
"""Regression tests for scripts/push_ledger.py + scripts/render_dashboard.py.

Fully deterministic, no live Azure — az_publish/az_download/az_upload_blob
are exercised only via a fake `az` stub on PATH (push side, where a
subprocess call is unavoidable to prove state only advances on success) or
not at all (render side's merge/render logic is pure directory-tree code,
tested directly; --local-only skips every az call by construction).

Usage: transport_suite.py --model <id> [--auto] | --report
"""
import argparse
import datetime
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
LEDGER = SKILL_DIR.parent / "telemetry" / "llm-usage-ledger-transport.jsonl"
AUTO_RUNS = 8

sys.path.insert(0, str(SKILL_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))


def version():
    try:
        return (SKILL_DIR / "VERSION").read_text().strip()
    except OSError:
        return "unversioned"


def _stub_az(tmp: Path, mode: str) -> Path:
    """A fake `az` on a fresh PATH. mode: 'publish-ok', 'publish-fail'."""
    bindir = tmp / f"stubbin_{mode}"
    bindir.mkdir(parents=True, exist_ok=True)
    az = bindir / "az"
    if mode == "publish-ok":
        az.write_text("#!/bin/sh\necho '{}' >&2\nexit 0\n")
    else:
        az.write_text("#!/bin/sh\necho 'simulated az failure' >&2\nexit 1\n")
    az.chmod(0o755)
    return bindir


def run_suite():
    r = {}

    def check(name, cond, why=""):
        r[name] = "pass" if cond else f"FAIL: {why}"

    try:
        import push_ledger as pl
        import render_dashboard as rd
        check("modules_import", True)
    except Exception as e:
        check("modules_import", False, repr(e))
        return r

    # ---------------------------------------------------------- push_ledger
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "lmstudio-alice-mba.jsonl").write_text(
            '{"a":1}\n{"a":2}\n{"a":3}\n')
        (d / "claude-code-alice-mba.jsonl").write_text('{"b":1}\n')
        (d / "lmstudio-bob-desktop.jsonl").write_text('{"c":1}\n')  # not mine
        (d / "prices.jsonl").write_text('{"ts":"2026-01-01"}\n')     # excluded

        files = pl.own_files(d, "alice", "mba")
        check("own_files_scoped_to_identity",
              {f.name for f in files} == {"lmstudio-alice-mba.jsonl",
                                          "claude-code-alice-mba.jsonl"},
              f"got {[f.name for f in files]!r}")

        state = pl.load_state(d)
        check("load_state_default", state == {"files": {}, "seq": 0})

        deltas = pl.compute_deltas(files, state)
        check("full_file_is_delta_when_state_empty",
              deltas["lmstudio-alice-mba.jsonl"]["lines"] == ['{"a":1}', '{"a":2}', '{"a":3}']
              and deltas["lmstudio-alice-mba.jsonl"]["total"] == 3,
              f"got {deltas}")

        state["files"]["lmstudio-alice-mba.jsonl"] = 2
        deltas2 = pl.compute_deltas(files, state)
        check("partial_state_gives_tail_only",
              deltas2["lmstudio-alice-mba.jsonl"]["lines"] == ['{"a":3}']
              and deltas2["lmstudio-alice-mba.jsonl"]["total"] == 3,
              f"got {deltas2}")

        state["files"]["claude-code-alice-mba.jsonl"] = 1  # fully pushed
        deltas3 = pl.compute_deltas(files, state)
        check("no_delta_when_fully_pushed",
              "claude-code-alice-mba.jsonl" not in deltas3,
              f"got {deltas3}")

        # reset: state claims more lines than the file now has (hand-edited /
        # reinstalled) -> whole current file becomes the delta, total is the
        # CURRENT count, not old_pushed + len(delta)
        state_reset = {"files": {"lmstudio-alice-mba.jsonl": 99}, "seq": 0}
        deltas_reset = pl.compute_deltas(files, state_reset)
        check("reset_file_repushes_whole_file",
              deltas_reset["lmstudio-alice-mba.jsonl"]["lines"] == ['{"a":1}', '{"a":2}', '{"a":3}']
              and deltas_reset["lmstudio-alice-mba.jsonl"]["total"] == 3,
              f"got {deltas_reset}")

        with tempfile.TemporaryDirectory() as std:
            pl.stage_delta(Path(std), deltas)
            content = (Path(std) / "lmstudio-alice-mba.jsonl").read_text()
            check("stage_delta_writes_lines", content == '{"a":1}\n{"a":2}\n{"a":3}\n',
                  f"got {content!r}")

        pl.save_state(d, {"files": {"x": 5}, "seq": 3})
        check("state_roundtrip", pl.load_state(d) == {"files": {"x": 5}, "seq": 3})

    # main()-level integration: uses a SEPARATE temp dir + this machine's
    # REAL identity (own_files()/main() resolve whoami()/machine_name()
    # internally — a synthetic "alice-mba" filename would just be invisible
    # to main(), silently testing nothing).
    with tempfile.TemporaryDirectory() as td:
        d2 = Path(td)
        real_user, real_machine = pl.whoami(), pl.machine_name()
        fname = f"lmstudio-{real_user}-{real_machine}.jsonl"
        (d2 / fname).write_text('{"a":1}\n{"a":2}\n{"a":3}\n')

        real_path = os.environ.get("PATH", "")
        os.environ["LLM_TOKEN_LEDGER_DIR"] = str(d2)
        try:
            # dry-run: prints, touches NOTHING (no az on PATH at all —
            # proves it never shells out)
            os.environ["PATH"] = "/nonexistent-bin-dir"
            state_before = pl.load_state(d2)
            rc = pl.main(["--dry-run"])
            check("dry_run_exits_zero_and_does_not_touch_az", rc == 0)
            check("dry_run_does_not_advance_state",
                  pl.load_state(d2) == state_before,
                  f"state changed: {state_before} -> {pl.load_state(d2)}")

            # successful push: state advances, version increments
            stub_ok = _stub_az(Path(td), "publish-ok")
            os.environ["PATH"] = f"{stub_ok}{os.pathsep}{real_path}"
            state_before2 = pl.load_state(d2)
            rc2 = pl.main(["--org", "o", "--project", "p", "--feed", "f"])
            state_after2 = pl.load_state(d2)
            check("successful_push_advances_state",
                  rc2 == 0 and state_after2["seq"] == state_before2["seq"] + 1
                  and state_after2["files"][fname] == 3,
                  f"before={state_before2} after={state_after2}")

            # a second push with nothing new: no-op, state unchanged
            rc3 = pl.main(["--org", "o", "--project", "p", "--feed", "f"])
            check("nothing_new_is_a_noop",
                  rc3 == 0 and pl.load_state(d2) == state_after2,
                  f"state changed on a no-op push: {state_after2} -> {pl.load_state(d2)}")

            # new lines appended -> next push is JUST the new tail, version
            # bumps again (proves deltas never re-send already-published
            # content)
            with open(d2 / fname, "a") as f:
                f.write('{"a":4}\n')
            rc4 = pl.main(["--org", "o", "--project", "p", "--feed", "f"])
            state_after4 = pl.load_state(d2)
            check("second_delta_is_tail_only",
                  rc4 == 0 and state_after4["seq"] == state_after2["seq"] + 1
                  and state_after4["files"][fname] == 4,
                  f"got {state_after4}")

            # failing az publish: state must NOT advance (safe to retry)
            with open(d2 / fname, "a") as f:
                f.write('{"a":5}\n')
            stub_fail = _stub_az(Path(td), "publish-fail")
            os.environ["PATH"] = f"{stub_fail}{os.pathsep}{real_path}"
            state_before5 = pl.load_state(d2)
            try:
                pl.main(["--org", "o", "--project", "p", "--feed", "f"])
                failed_cleanly = False
            except SystemExit as e:
                failed_cleanly = e.code != 0
            check("failed_push_does_not_advance_state",
                  failed_cleanly and pl.load_state(d2) == state_before5,
                  f"before={state_before5} after={pl.load_state(d2)}")
        finally:
            os.environ["PATH"] = real_path
            os.environ.pop("LLM_TOKEN_LEDGER_DIR", None)

    # ------------------------------------------------------- render_dashboard
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "downloaded"
        merged = Path(td) / "merged"

        def ev(tag: str) -> str:
            # A real, minimal ledger event — NOT a placeholder dict — because
            # this same fixture tree is reused by the render()/main() tests
            # below, and read_ledgers() silently drops any line with no `ts`
            # (see llm_usage_ledger.py), which would make html_report() see
            # zero events and raise SystemExit("no usage events found").
            return json.dumps({"ts": "2026-08-01T00:00:00+00:00",
                               "provider": "lmstudio", "model": "m",
                               "prompt_tokens": 10, "completion_tokens": 5,
                               "task_tag": tag})

        def tags(path: Path) -> list:
            return [json.loads(l)["task_tag"] for l in path.read_text().splitlines()]

        # two versions of one machine's package -> concatenated in order
        v0 = root / "ledger-alice-mba" / "0.1.0"
        v1 = root / "ledger-alice-mba" / "0.1.1"
        v0.mkdir(parents=True)
        v1.mkdir(parents=True)
        (v0 / "lmstudio-alice-mba.jsonl").write_text(ev("a1") + "\n" + ev("a2") + "\n")
        (v1 / "lmstudio-alice-mba.jsonl").write_text(ev("a3") + "\n")
        # a second machine's package
        v0b = root / "ledger-bob-desktop" / "0.1.0"
        v0b.mkdir(parents=True)
        (v0b / "lmstudio-bob-desktop.jsonl").write_text(ev("b1") + "\n")

        rd.merge_versions(root, merged)
        got_alice = tags(merged / "lmstudio-alice-mba.jsonl")
        got_bob = tags(merged / "lmstudio-bob-desktop.jsonl")
        check("merge_concatenates_versions_in_order",
              got_alice == ["a1", "a2", "a3"], f"got {got_alice}")
        check("merge_keeps_machines_separate",
              got_bob == ["b1"], f"got {got_bob}")

        # a re-sent version (exact-duplicate lines) must not double-count
        v2 = root / "ledger-alice-mba" / "0.1.2"
        v2.mkdir(parents=True)
        (v2 / "lmstudio-alice-mba.jsonl").write_text(ev("a3") + "\n" + ev("a4") + "\n")  # a3 is a dup
        merged2 = Path(td) / "merged2"
        rd.merge_versions(root, merged2)
        got_dedup = tags(merged2 / "lmstudio-alice-mba.jsonl")
        check("merge_drops_exact_duplicate_lines",
              got_dedup == ["a1", "a2", "a3", "a4"], f"got {got_dedup}")

        # ledger-prices: latest version wins
        p0 = root / "ledger-prices" / "0.1.0"
        p1 = root / "ledger-prices" / "0.1.1"
        p0.mkdir(parents=True)
        p1.mkdir(parents=True)
        (p0 / "prices.jsonl").write_text('{"ts":"old"}\n')
        (p1 / "prices.jsonl").write_text('{"ts":"new"}\n')
        merged3 = Path(td) / "merged3"
        merged3.mkdir()
        rd.merge_prices(root, merged3)
        check("merge_prices_takes_latest_version",
              (merged3 / "prices.jsonl").read_text() == '{"ts":"new"}\n',
              f"got {(merged3 / 'prices.jsonl').read_text()!r}")

        no_prices = Path(td) / "merged_no_prices"
        no_prices.mkdir()
        rd.merge_prices(Path(td) / "does-not-exist", no_prices)
        check("merge_prices_missing_is_a_noop",
              not (no_prices / "prices.jsonl").exists())

        # render(): produces real html via the existing html_report()
        out = Path(td) / "dashboard.html"
        rd.render(merged, out)
        html = out.read_text()
        check("render_produces_html", out.is_file() and "<html" in html.lower(),
              f"file exists={out.is_file()}")

        # main() --local-only: end to end, zero az calls needed (no az on
        # PATH at all — proves it), never uploads
        out2 = Path(td) / "dashboard2.html"
        real_path = os.environ.get("PATH", "")
        os.environ["PATH"] = "/nonexistent-bin-dir"
        try:
            rc = rd.main(["--local-only", "--downloaded-root", str(root),
                         "--out", str(out2)])
        finally:
            os.environ["PATH"] = real_path
        check("local_only_end_to_end_without_az",
              rc == 0 and out2.is_file() and "<html" in out2.read_text().lower())

    return r


def record(model, results, dur):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps({
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "skill": "llm-usage-ledger-transport", "skill_version": version(),
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
