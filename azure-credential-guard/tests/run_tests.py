#!/usr/bin/env python3
"""Regression tests + telemetry for the azure-credential-guard skill.

Stdlib-only and fully offline: azure-identity is stubbed, so these run on any
machine with no Azure account, no network, and no credentials. That matters —
a test for "this must not hang" cannot be allowed to hang.

The three properties worth pinning, each of which HAS been got wrong in real
code:

1. Off Azure, managed identity is EXCLUDED (the hang).
2. On Azure, managed identity is ENABLED (the reverse bug — fixing the laptop
   by breaking production, which is harder to notice).
3. ``bounded()`` actually returns early, which the obvious ThreadPoolExecutor
   implementation does not.

Usage: run_tests.py --model <id> [--auto] | --report
"""

import argparse
import datetime
import importlib.util
import json
import os
import statistics
import sys
import time
import types
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
LEDGER = SKILL_DIR.parent / "telemetry" / "azure-credential-guard.jsonl"
AUTO_RUNS = 8
REFERENCE = SKILL_DIR / "reference" / "azure_credential.py"


def version():
    try:
        return (SKILL_DIR / "VERSION").read_text().strip()
    except OSError:
        return "unversioned"


def load_reference():
    """Import the reference module with azure-identity stubbed out.

    The stub records the kwargs it was constructed with — which is the entire
    behaviour under test, and requires no Azure package to be installed.
    """
    calls = []

    class _FakeCredential:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    fake_identity = types.ModuleType("azure.identity")
    fake_identity.DefaultAzureCredential = _FakeCredential
    fake_azure = types.ModuleType("azure")
    fake_azure.identity = fake_identity
    sys.modules["azure"] = fake_azure
    sys.modules["azure.identity"] = fake_identity

    spec = importlib.util.spec_from_file_location("_acg_reference", REFERENCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


def run_suite():
    r = {}

    def check(name, cond, why=""):
        r[name] = "pass" if cond else f"FAIL: {why}"

    check("skill_md_exists", (SKILL_DIR / "SKILL.md").is_file())
    check("reference_exists", REFERENCE.is_file(), "SKILL.md points at reference/azure_credential.py")
    if not REFERENCE.is_file():
        return r

    saved = {k: os.environ.get(k) for k in ("IDENTITY_ENDPOINT", "MSI_ENDPOINT", "AZURE_CLIENT_ID")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        mod, calls = load_reference()

        # 1. Off Azure: the IMDS probe must be excluded, or it hangs.
        check("off_azure_detected", mod.managed_identity_available() is False)
        calls.clear()
        mod.build_credential()
        check(
            "off_azure_excludes_managed_identity",
            calls and calls[0].get("exclude_managed_identity_credential") is True,
            f"got {calls!r} — the IMDS probe is what hangs off Azure",
        )
        check(
            "process_timeout_is_bounded",
            calls and isinstance(calls[0].get("process_timeout"), int),
            "the CLI credentials shell out and need a bound",
        )

        # 2. On Azure: it must NOT be excluded, or production loses its only
        #    working credential. This is the regression people ship while
        #    fixing (1).
        os.environ["IDENTITY_ENDPOINT"] = "http://localhost:42/msi/token"
        check("on_azure_detected", mod.managed_identity_available() is True)
        calls.clear()
        mod.build_credential()
        check(
            "on_azure_keeps_managed_identity",
            calls and calls[0].get("exclude_managed_identity_credential") is False,
            "excluding MI on Azure breaks the only credential that works there",
        )
        os.environ.pop("IDENTITY_ENDPOINT")

        # 3. bounded() must actually return early. The ThreadPoolExecutor
        #    version passes a naive read of the code and still blocks, because
        #    shutdown(wait=True) re-joins the timed-out thread.
        started = time.time()
        out = mod.bounded(lambda: time.sleep(30), 0.5)
        elapsed = time.time() - started
        check("bounded_returns_none_on_timeout", out is None)
        check(
            "bounded_actually_returns_early",
            elapsed < 5,
            f"took {elapsed:.1f}s — this is the ThreadPoolExecutor trap",
        )
        check("bounded_passes_through_a_fast_result", mod.bounded(lambda: 7, 5) == 7)

        # 4. The marker list must stay in sync between doc and code: a host
        #    missing from one of them is exactly bug (2) waiting to happen.
        skill_md = (SKILL_DIR / "SKILL.md").read_text()
        check(
            "markers_documented",
            all(m in skill_md for m in mod.AZURE_IDENTITY_MARKERS),
            "every marker the code checks must be named in SKILL.md",
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return r


def record(model, results, dur):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                    "skill": "azure-credential-guard",
                    "skill_version": version(),
                    "model": model,
                    "n_pass": sum(1 for v in results.values() if v == "pass"),
                    "n_fail": sum(1 for v in results.values() if v.startswith("FAIL")),
                    "duration_s": round(dur, 2),
                    "results": results,
                }
            )
            + "\n"
        )


def entries():
    if not LEDGER.exists():
        return []
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]


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
            print(
                f"{v:16s} {m:26s} runs={len(es)} pass={ok / tot:.0%} "
                f"dur={statistics.mean(d):.2f}±{sd:.2f}s"
            )
        return
    if not a.model:
        sys.exit("--model required (or --report)")
    n = sum(1 for e in entries() if e["model"] == a.model and e["skill_version"] == version())
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
