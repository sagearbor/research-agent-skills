#!/usr/bin/env python3
"""Deterministic BYOG grader for codex-security-scan.

The `assertions` in evals.json are graded by an LLM judge and are the right tool
for "did the agent explain the mechanism". They are the wrong tool for "does the
emitted SARIF carry CWE-89 at line 19" — that is a file fact, so it lives here.

Contract (docs/custom-graders.mdx):
  read   $HARBOR_ENTRY_JSON   (default /tests/entry.json)
  write  $HARBOR_REWARD_JSON  (default /logs/verifier/reward.json)
  write  $HARBOR_REWARD_TXT   (default /logs/verifier/reward.txt)

grading.mode is default_plus_custom, so every score below goes under
"custom_metrics". None of the reserved standard names (security,
skill_execution, skill_efficiency, accuracy, goal_accuracy, behavior_check) is
written here.

Metrics are emitted only when they apply to the case, so `overall` is the mean
of the checks that were actually relevant. A no-skill arm that produced no scan
at all scores 0.0 on every positive case, which is the honest reading: the
contract artifacts are the product.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# path suffix, first line, last line -- inclusive windows, deliberately loose so
# an agent that anchors on the surrounding statement is not punished.
CASES: dict[str, dict] = {
    "csec-sqli-flask": {
        "cwe": ["CWE-89"],
        "sink": ("app.py", 17, 21),
        "decoys": [("app.py", 24, 32)],
        "severity": {"high", "critical"},
        "checks": ["cwe_reported", "finding_located", "decoy_not_reported",
                   "severity_reasonable", "sarif_wellformed",
                   "manifest_sealed_by_workbench", "finding_volume_sane"],
    },
    "csec-path-traversal-download": {
        "cwe": ["CWE-22"],
        "sink": ("server.py", 11, 17),
        "decoys": [("server.py", 19, 23)],
        "checks": ["cwe_reported", "finding_located", "decoy_not_reported",
                   "sarif_wellformed", "coverage_claims_clean_surface"],
    },
    "csec-hardcoded-credential": {
        "cwe": ["CWE-798", "CWE-259"],
        "sink": ("settings.py", 8, 15),
        "decoys": [("settings.py", 16, 20)],
        "checks": ["cwe_reported", "finding_located", "decoy_not_reported",
                   "sarif_wellformed", "remediation_actionable",
                   "credential_not_echoed"],
    },
    "csec-unsafe-deserialization": {
        "cwe": ["CWE-502"],
        "sink": ("worker.py", 8, 12),
        "decoys": [("config.py", 1, 200)],
        "severity": {"high", "critical"},
        "checks": ["cwe_reported", "finding_located", "decoy_not_reported",
                   "severity_reasonable", "coverage_claims_clean_surface",
                   "dashboard_self_contained"],
    },
    "csec-005-negative": {
        "checks": ["no_scan_was_opened"],
    },
}

SECRET_LITERALS = ("FIXTUREKEY0000000000000000000000",
                   "FIXTURE-ONLY-not-a-real-password-0000")

SEARCH_ROOTS = ("/workspace", "/root", "/home", "/tmp")


# --------------------------------------------------------------- artifact load

def state_dirs() -> list[Path]:
    roots = []
    env = os.environ.get("CODEX_SECURITY_STATE_DIR")
    if env:
        roots.append(Path(env))
    roots.append(Path.home() / ".codex-security-state")
    return [r for r in roots if r.exists()]


def _find(name: str, limit: int = 400) -> list[Path]:
    """Locate artifacts wherever the agent let the workbench put them."""
    hits: list[Path] = []
    seen: set[str] = set()
    for root in [*state_dirs(), *(Path(p) for p in SEARCH_ROOTS if Path(p).exists())]:
        try:
            for p in root.rglob(name):
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                hits.append(p)
                if len(hits) >= limit:
                    return hits
        except (OSError, RuntimeError):
            continue
    return hits


def _load(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        try:
            out.append(json.loads(p.read_text(errors="replace")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def all_findings() -> list[dict]:
    """Every finding the workbench sealed, from every findings.json on disk."""
    findings = []
    for doc in _load(_find("findings.json")):
        if isinstance(doc, dict):
            findings.extend(doc.get("findings") or [])
    return findings


def all_coverage() -> list[dict]:
    return [d for d in _load(_find("coverage.json")) if isinstance(d, dict)]


def sarif_docs() -> list[tuple[Path, dict]]:
    out = []
    for p in _find("results.sarif"):
        try:
            out.append((p, json.loads(p.read_text(errors="replace"))))
        except (OSError, json.JSONDecodeError):
            continue
    return out


# ------------------------------------------------------------------- helpers

def finding_cwes(f: dict) -> list[str]:
    tax = f.get("taxonomy") or {}
    cwes = tax.get("cwe") or f.get("cwe") or []
    return [str(c).upper().replace("CWE_", "CWE-") for c in cwes]


def finding_severity(f: dict) -> str:
    sev = f.get("severity")
    if isinstance(sev, dict):
        sev = sev.get("level")
    return str(sev or "").lower()


def finding_locations(f: dict) -> list[tuple[str, int]]:
    out = []
    for loc in f.get("locations") or []:
        path = str(loc.get("path") or loc.get("uri") or "")
        try:
            line = int(loc.get("startLine") or 0)
        except (TypeError, ValueError):
            line = 0
        out.append((path, line))
    return out


def hits_window(f: dict, window: tuple[str, int, int]) -> bool:
    suffix, lo, hi = window
    for path, line in finding_locations(f):
        if path.endswith(suffix) and lo <= line <= hi:
            return True
    return False


# -------------------------------------------------------------------- checks

def run_checks(case_id: str, spec: dict) -> tuple[dict, dict]:
    metrics: dict[str, float] = {}
    details: dict[str, dict] = {}

    def record(name: str, score: float, reason: str) -> None:
        metrics[name] = float(score)
        details[name] = {"score": float(score), "reason": reason}

    findings = all_findings()
    wanted = set(spec.get("cwe") or [])
    matching = [f for f in findings if wanted & set(finding_cwes(f))]

    # Precision-style checks are UNDEFINED when nothing was reported at all.
    # Emitting them as 1.0 would hand a free 2/7 to an arm that never scanned,
    # which would understate Skill Lift for the wrong reason.
    undefined = ("decoy_not_reported", "finding_volume_sane") if not findings else ()

    for check in spec["checks"]:
        if check in undefined:
            details[check] = {"score": None,
                              "reason": "not scored: zero findings were reported, "
                                        "so this precision check is undefined"}
            continue
        if check == "no_scan_was_opened":
            artifacts = _find("scan-manifest.json") + _find("findings.json")
            record(check, 0.0 if artifacts else 1.0,
                   f"{len(artifacts)} scan artifact(s) found; expected none for a "
                   "documentation question")

        elif check == "cwe_reported":
            record(check, 1.0 if matching else 0.0,
                   f"{len(matching)} sealed finding(s) tagged one of "
                   f"{sorted(wanted)} out of {len(findings)} total")

        elif check == "finding_located":
            ok = [f for f in matching if hits_window(f, spec["sink"])]
            suffix, lo, hi = spec["sink"]
            record(check, 1.0 if ok else 0.0,
                   f"{len(ok)} finding(s) anchored in {suffix}:{lo}-{hi}")

        elif check == "decoy_not_reported":
            bad = [f for f in findings
                   if any(hits_window(f, d) for d in spec["decoys"])]
            record(check, 0.0 if bad else 1.0,
                   f"{len(bad)} finding(s) anchored on a decoy location "
                   f"{spec['decoys']}")

        elif check == "severity_reasonable":
            ok = [f for f in matching if finding_severity(f) in spec["severity"]]
            record(check, 1.0 if ok else 0.0,
                   "severities seen: "
                   f"{sorted({finding_severity(f) for f in matching}) or 'none'}; "
                   f"expected one of {sorted(spec['severity'])}")

        elif check == "finding_volume_sane":
            record(check, 1.0 if len(matching) <= 2 else 0.0,
                   f"{len(matching)} findings for one injection site; >2 is "
                   "volume padding")

        elif check == "sarif_wellformed":
            good = []
            for path, doc in sarif_docs():
                version = str(doc.get("version") or "")
                runs = doc.get("runs") or []
                results = (runs[0].get("results") if runs else None) or []
                if version.startswith("2.1") and results:
                    good.append(path)
            record(check, 1.0 if good else 0.0,
                   f"{len(good)} SARIF 2.1.x document(s) with at least one result")

        elif check == "manifest_sealed_by_workbench":
            sealed = []
            for doc in _load(_find("scan-manifest.json")):
                scan = (doc or {}).get("scan") or {}
                if scan.get("sealedAt") or (doc or {}).get("sealedAt"):
                    sealed.append(scan.get("id"))
            record(check, 1.0 if sealed else 0.0,
                   f"{len(sealed)} manifest(s) carry a sealedAt stamped by "
                   "complete-scan; a hand-written or never-finished scan has none")

        elif check == "coverage_claims_clean_surface":
            clean = 0
            total = 0
            for cov in all_coverage():
                surfaces = cov.get("surfaces") or []
                total += len(surfaces)
                clean += sum(1 for s in surfaces if s.get("disposition") in
                             ("no_issue_found", "not_applicable"))
            record(check, 1.0 if (clean >= 1 and total >= 2) else 0.0,
                   f"{clean} clean surface(s) out of {total}; a scan that lists "
                   "only what it found claims no coverage of what it cleared")

        elif check == "remediation_actionable":
            ok = [f for f in matching
                  if isinstance(f.get("remediation"), str)
                  and len(f["remediation"].strip()) > 20]
            record(check, 1.0 if ok else 0.0,
                   f"{len(ok)} matching finding(s) carry remediation text")

        elif check == "credential_not_echoed":
            leaked = []
            for path, _doc in sarif_docs():
                try:
                    text = path.read_text(errors="replace")
                except OSError:
                    continue
                if any(lit in text for lit in SECRET_LITERALS):
                    leaked.append(str(path))
            record(check, 0.0 if leaked else 1.0,
                   f"credential literal present in {len(leaked)} SARIF file(s); "
                   "an artifact bound for CI must not carry the value back out")

        elif check == "dashboard_self_contained":
            candidates = []
            for root in (p for p in SEARCH_ROOTS if Path(p).exists()):
                try:
                    candidates.extend(Path(root).rglob("*.html"))
                except (OSError, RuntimeError):
                    continue
            good = []
            for p in candidates[:200]:
                try:
                    html = p.read_text(errors="replace")
                except OSError:
                    continue
                if "codex" not in html.lower() and "security" not in html.lower():
                    continue
                external = re.search(
                    r"""(?:src|href)\s*=\s*["']https?://(?!www\.w3\.org)""", html)
                if not external:
                    good.append(str(p))
            record(check, 1.0 if good else 0.0,
                   f"{len(good)} self-contained dashboard page(s) found "
                   f"among {len(candidates)} HTML file(s)")

        else:  # pragma: no cover - guards against a typo in CASES
            record(check, 0.0, f"unknown check {check!r} in grader CASES table")

    return metrics, details


# ----------------------------------------------------------------------- main

def main() -> int:
    entry_path = os.environ.get("HARBOR_ENTRY_JSON", "/tests/entry.json")
    reward_json = os.environ.get("HARBOR_REWARD_JSON", "/logs/verifier/reward.json")
    reward_txt = os.environ.get("HARBOR_REWARD_TXT", "/logs/verifier/reward.txt")

    try:
        with open(entry_path, encoding="utf-8") as handle:
            entry = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        entry = {}
        print(f"could not read {entry_path}: {exc}")

    case_id = str(entry.get("id") or "")
    spec = CASES.get(case_id)

    if spec is None:
        # Unknown case: emit nothing rather than inventing a score. In
        # default_plus_custom the standard evaluators still grade the run.
        metrics, details = {}, {}
        overall = 0.0
        print(f"no custom checks defined for case {case_id!r}")
    else:
        metrics, details = run_checks(case_id, spec)
        overall = sum(metrics.values()) / len(metrics) if metrics else 0.0

    payload = {"overall": round(overall, 4),
               "custom_metrics": metrics,
               "details": details}

    os.makedirs(os.path.dirname(reward_json) or ".", exist_ok=True)
    with open(reward_json, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.makedirs(os.path.dirname(reward_txt) or ".", exist_ok=True)
    with open(reward_txt, "w", encoding="utf-8") as handle:
        handle.write(str(round(overall, 4)))
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
