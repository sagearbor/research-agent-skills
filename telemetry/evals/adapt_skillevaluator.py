#!/usr/bin/env python3
"""Adapt one NVIDIA SkillEvaluator Tier 3 run directory into our own
append-only Skill Lift ledger (`skill-lift-record/1`, see SCHEMA.md).

SkillEvaluator is v0.2.0, experimental, and ships NO JSON Schema — its output
shape is Python logic only and WILL drift.  So we never archive its reports as
our record: we adapt them into a minimal record we own, one JSON line per
measurement, and mark which fields are ours and which are opportunistic
passthrough.

Discovery is deliberately defensive.  Every field records WHERE it was found
(a JSON pointer) or, when it was not found, an explicit null plus the reason.
Nothing is ever invented, and a failed run is recorded as a failure — never as
a zero score, never as a missing row.  If the shape is unrecognisable the
adapter raises UnrecognisedRunError describing what it actually saw, and
writes nothing.

Usage:
    adapt_skillevaluator.py <run-dir> [<run-dir> ...]
        [--ledger PATH]        # default: telemetry/evals/skill-lift.jsonl
        [--skill NAME]         # override skill resolution
        [--skill-version V]    # override skills/<skill>/VERSION lookup
        [--repo PATH]          # repo root holding skills/<skill>/VERSION
        [--stdout]             # print rows instead of appending
        [--quiet]

Stdlib only.  No network, no SkillEvaluator install required.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

ADAPTER_VERSION = "0.1.0"
SCHEMA_VERSION = "skill-lift-record/1"

# Mirrored from skills/skill-lift-eval/skill_lift_eval.py so the bands in the
# ledger and the bands the skill reports cannot silently diverge.
LIFT_PASS_THRESHOLD = 0.05
LIFT_FAIL_THRESHOLD = -0.10

HERE = Path(__file__).resolve().parent
DEFAULT_LEDGER = HERE / "skill-lift.jsonl"
DEFAULT_REPO = HERE.parent.parent

# Upstream arm names we know about, mapped to our two stable arm slots.
ARM_ALIASES = {
    "with_skill": ("with_skill", "with-skill", "with", "skill", "treatment"),
    "without_skill": ("without_skill", "without-skill", "without", "baseline",
                      "control", "no_skill"),
}
# Keys that might hold a scalar overall score inside a per-case dict.
SCORE_KEYS = ("overall", "overall_score", "score", "mean", "value")


class UnrecognisedRunError(Exception):
    """The run directory does not look like a SkillEvaluator run at all.

    Raised only when the adapter cannot find the arms.  A recognised shape with
    missing fields degrades to nulls-with-reasons instead; this is for the case
    where we cannot honestly say what a row would even be about.
    """


# ---------------------------------------------------------------------------
# provenance-tracking discovery helpers
# ---------------------------------------------------------------------------


class Prov:
    """Accumulates {field: {"path": pointer|null, "reason": str}}."""

    def __init__(self) -> None:
        self.d: dict[str, dict] = {}

    def hit(self, field: str, path: str):
        self.d[field] = {"path": path}

    def miss(self, field: str, reason: str, tried=()):
        rec = {"path": None, "reason": reason}
        if tried:
            rec["tried"] = list(tried)
        self.d[field] = rec

    def dump(self) -> dict:
        return dict(sorted(self.d.items()))


def dig(obj, *keys):
    """Walk a nested mapping, returning (value, pointer) or (None, None)."""
    cur, ptr = obj, ""
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None, None
        cur = cur[k]
        ptr += "/" + str(k)
    return cur, ptr


def first_of(obj, field, prov, *paths, transform=None):
    """Take the first path that resolves to a non-None value, recording it."""
    for p in paths:
        val, ptr = dig(obj, *p)
        if val is not None:
            if transform is not None:
                val = transform(val)
                if val is None:
                    continue
            prov.hit(field, ptr)
            return val
    prov.miss(field, "not found in this run's result.json",
              tried=["/" + "/".join(str(k) for k in p) for p in paths])
    return None


def coerce_score(val):
    """Pull a float out of whatever upstream put in an arm's per-case slot.

    Upstream's per-case shape is undocumented (spec section 9 lists it among
    the unconfirmed items), so accept a bare number, or a dict carrying one
    under any of the plausible keys.  Anything else -> None, and the caller
    writes a reason.
    """
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        for k in SCORE_KEYS:
            v = val.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            return None
    return None


def describe_shape(val) -> str:
    if isinstance(val, dict):
        keys = list(val)[:6]
        return f"dict with keys {keys}" if keys else "empty dict"
    if isinstance(val, list):
        return f"list of {len(val)}"
    return f"{type(val).__name__}={val!r}"[:80]


def lift_verdict(value):
    if value is None:
        return "NO SCORE"
    if value >= LIFT_PASS_THRESHOLD:
        return "PASS"
    if value <= LIFT_FAIL_THRESHOLD:
        return "FAIL"
    return "NEUTRAL"


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# arm / case / error discovery
# ---------------------------------------------------------------------------


def find_arm(agent: dict, slot: str, prefix: str):
    """Locate an arm dict under whichever alias upstream used."""
    for alias in ARM_ALIASES[slot]:
        if alias in agent:
            return agent[alias], f"{prefix}/{alias}", alias
    return None, None, None


def discover_cases(agent: dict, dataset: list, agent_key: str) -> list[str]:
    """Union of every case id we can see, from every place one might hide."""
    cases: list[str] = []

    def add(c):
        if isinstance(c, str) and c and c not in cases:
            cases.append(c)

    for entry in dataset:
        if isinstance(entry, dict):
            add(entry.get("id") or entry.get("case_id") or entry.get("name"))
    for slot in ("with_skill", "without_skill"):
        arm, _, _ = find_arm(agent, slot, "")
        if isinstance(arm, dict):
            for c in arm:
                add(c)
    for key in ("lift", "custom_lift", "dimensions_with_skill",
                "dimensions_without_skill", "custom_with_skill",
                "custom_without_skill"):
        v = agent.get(key)
        if isinstance(v, dict):
            for c in v:
                add(c)
    # Last resort: trial names look like "<case-id>__<random>".
    tf = agent.get("trial_failures")
    if isinstance(tf, dict):
        for arm_list in tf.values():
            if isinstance(arm_list, list):
                for t in arm_list:
                    name = t.get("trial") if isinstance(t, dict) else None
                    if isinstance(name, str):
                        add(name.rsplit("__", 1)[0])
    return cases


def error_for(agent: dict, agent_key: str, case: str, run_dir: Path, prov: Prov):
    """Find the error class + first message for a case, without guessing.

    Preference order: the per-trial failure.json (upstream states the class
    explicitly) -> trial_failures[].reason ("ClassName: message") ->
    conditions.<arm>.execution_errors[0] (no class available).
    """
    prefix = f"/agents/{agent_key}"

    # 1. per-trial failure.json on disk
    for armdir in ("with-skill", "without-skill", "with_skill", "without_skill"):
        tdir = run_dir / agent_key / armdir / "trials"
        if not tdir.is_dir():
            continue
        for trial in sorted(tdir.iterdir()):
            if not trial.name.startswith(case):
                continue
            fj = load_json(trial / "failure.json")
            if isinstance(fj, dict) and isinstance(fj.get("error"), dict):
                et = fj["error"].get("type")
                msg = fj["error"].get("message")
                if et:
                    prov.hit("error_type",
                             f"file:{agent_key}/{armdir}/trials/{trial.name}"
                             f"/failure.json/error/type")
                    return str(et), (str(msg)[:500] if msg else None)

    # 2. trial_failures reason, formatted "ClassName: message"
    tf = agent.get("trial_failures")
    if isinstance(tf, dict):
        for arm_name, arm_list in tf.items():
            if not isinstance(arm_list, list):
                continue
            for t in arm_list:
                if not isinstance(t, dict):
                    continue
                if not str(t.get("trial", "")).startswith(case):
                    continue
                reason = t.get("reason")
                if isinstance(reason, str) and reason:
                    head, _, tail = reason.partition(":")
                    looks_like_class = (head and " " not in head.strip()
                                        and head.strip()[:1].isupper())
                    prov.hit("error_type",
                             f"{prefix}/trial_failures/{arm_name}[]/reason")
                    return ((head.strip() if looks_like_class else "unknown"),
                            (tail.strip() or reason)[:500])

    # 3. condition-level execution errors — no class name available
    conds = agent.get("conditions")
    if isinstance(conds, dict):
        for arm_name, cond in conds.items():
            if not isinstance(cond, dict):
                continue
            errs = cond.get("execution_errors")
            if isinstance(errs, list) and errs:
                prov.miss("error_type",
                          "only condition-level execution_errors present; "
                          "upstream gave no exception class",
                          tried=[f"{prefix}/conditions/{arm_name}/execution_errors"])
                return None, str(errs[0])[:500]

    prov.miss("error_type", "no failure record found for this case")
    return None, None


def resolve_skill(dataset: list, case: str, result: dict, prov: Prov,
                  override):
    if override:
        prov.hit("skill", "override:--skill")
        return override
    for entry in dataset:
        if isinstance(entry, dict) and entry.get("id") == case:
            es = entry.get("expected_skill")
            if isinstance(es, str) and es:
                prov.hit("skill", "/dataset_snapshot/dataset[]/expected_skill")
                return es
            prov.miss("skill",
                      "dataset entry found but expected_skill is "
                      f"{es!r} (negative case?) — falling back to run label")
            break
    label = result.get("skill_name")
    if isinstance(label, str) and label:
        if "skill" not in prov.d:
            prov.hit("skill", "/skill_name")
        else:
            prov.d["skill"]["fallback_path"] = "/skill_name"
        return label
    prov.miss("skill", "no expected_skill in dataset and no /skill_name")
    return "unknown"


def resolve_skill_version(skill: str, repo: Path, prov: Prov, override):
    if override:
        prov.hit("skill_version", "override:--skill-version")
        return override
    vf = repo / "skills" / skill / "VERSION"
    try:
        v = vf.read_text().strip()
    except OSError:
        prov.miss("skill_version",
                  f"no VERSION file at skills/{skill}/VERSION (skill not in "
                  "this repo, or renamed); pass --skill-version to pin it")
        return None
    if not v:
        prov.miss("skill_version", f"skills/{skill}/VERSION is empty")
        return None
    prov.hit("skill_version", f"file:skills/{skill}/VERSION")
    return v


# ---------------------------------------------------------------------------
# main adaptation
# ---------------------------------------------------------------------------


def adapt_run(run_dir, repo=None, skill_override=None,
              skill_version_override=None, now=None) -> list[dict]:
    """Read one run directory, return a list of skill-lift-record/1 rows."""
    run_dir = Path(run_dir).resolve()
    repo = Path(repo) if repo else DEFAULT_REPO
    now = now or datetime.datetime.now(datetime.timezone.utc)

    rpath = run_dir / "result.json"
    if not rpath.is_file():
        raise UnrecognisedRunError(
            f"{run_dir}: no result.json. Saw entries: "
            f"{sorted(p.name for p in run_dir.iterdir())[:12] if run_dir.is_dir() else 'directory does not exist'}")
    result = load_json(rpath)
    if not isinstance(result, dict):
        raise UnrecognisedRunError(
            f"{rpath}: not a JSON object (parsed as "
            f"{type(result).__name__}). SkillEvaluator's result.json is "
            f"expected to be a mapping with an 'agents' key.")

    agents = result.get("agents")
    if not isinstance(agents, dict) or not agents:
        raise UnrecognisedRunError(
            f"{rpath}: no usable 'agents' mapping — cannot find the "
            f"with-skill/without-skill arms, so no measurement can be named. "
            f"Top-level keys seen: {sorted(result)}. "
            f"agents = {describe_shape(agents)}. "
            f"Tried: /agents/<runner>/{{with_skill,without_skill}} "
            f"(aliases {ARM_ALIASES}). "
            f"Nothing was written to the ledger.")

    ds = result.get("dataset_snapshot")
    if not isinstance(ds, dict):
        ds = load_json(run_dir / "dataset_snapshot.json") or {}
    dataset = ds.get("dataset") if isinstance(ds.get("dataset"), list) else []

    run_cfg = result.get("run_config")
    if not isinstance(run_cfg, dict):
        run_cfg = load_json(run_dir / "run_config.json") or {}
    policy = result.get("attempt_policy")
    if not isinstance(policy, dict):
        policy = load_json(run_dir / "attempt_policy.json") or {}

    rows: list[dict] = []
    for agent_key, agent in sorted(agents.items()):
        if not isinstance(agent, dict):
            continue
        prefix = f"/agents/{agent_key}"
        cases = discover_cases(agent, dataset, agent_key)
        if not cases:
            raise UnrecognisedRunError(
                f"{rpath}: agent {agent_key!r} has an arm structure but no "
                f"case ids anywhere. Looked in the arms, lift/custom_lift, "
                f"dimensions_*, dataset_snapshot.dataset[].id and "
                f"trial_failures[].trial. Agent keys seen: {sorted(agent)}. "
                f"Nothing was written to the ledger.")

        for case in cases:
            prov = Prov()
            scores = {}
            for slot in ("with_skill", "without_skill"):
                field = "score_" + slot          # score_with_skill / score_without_skill
                arm, armptr, alias = find_arm(agent, slot, prefix)
                if arm is None:
                    prov.miss(field, f"no {slot} arm under any known alias",
                              tried=[f"{prefix}/{a}" for a in ARM_ALIASES[slot]])
                    scores[slot] = None
                    continue
                if not isinstance(arm, dict):
                    prov.miss(field,
                              f"arm {alias!r} is {describe_shape(arm)}, "
                              f"expected a mapping of case id -> score")
                    scores[slot] = None
                    continue
                if case not in arm:
                    prov.miss(field,
                              (f"arm {alias!r} present but empty — no case was "
                               f"scored in this run"
                               if not arm else
                               f"case {case!r} absent from arm {alias!r} "
                               f"(arm holds {sorted(arm)[:6]})"),
                              tried=[f"{armptr}/{case}"])
                    scores[slot] = None
                    continue
                val = coerce_score(arm[case])
                if val is None:
                    prov.miss(field,
                              f"case slot present but unreadable as a score: "
                              f"{describe_shape(arm[case])}; tried keys "
                              f"{list(SCORE_KEYS)}",
                              tried=[f"{armptr}/{case}"])
                else:
                    prov.hit(field, f"{armptr}/{case}")
                scores[slot] = val

            sw, so = scores["with_skill"], scores["without_skill"]
            if sw is not None and so is not None:
                lift, lift_source, status = round(sw - so, 6), "computed", "scored"
                prov.hit("lift", "computed: score_with_skill - score_without_skill")
            else:
                upstream_lift, ptr = dig(agent, "lift", case)
                ul = coerce_score(upstream_lift) if upstream_lift is not None else None
                if ul is not None:
                    lift, lift_source = ul, "upstream"
                    prov.hit("lift", ptr)
                    status = "partial"
                else:
                    lift, lift_source = None, "unavailable"
                    prov.miss("lift",
                              "at least one arm is unscored and upstream "
                              f"{prefix}/lift/{case} is absent or empty")
                    status = "partial" if (sw is not None or so is not None) else "failed"

            if status == "failed":
                error_type, error_detail = error_for(agent, agent_key, case,
                                                     run_dir, prov)
            else:
                error_type, error_detail = None, None
                if status == "partial":
                    error_type, error_detail = error_for(agent, agent_key,
                                                         case, run_dir, prov)

            skill = resolve_skill(dataset, case, result, prov, skill_override)
            skill_version = resolve_skill_version(skill, repo, prov,
                                                  skill_version_override)

            run_id = result.get("run_id")
            if isinstance(run_id, str) and run_id:
                prov.hit("run_id", "/run_id")
            else:
                run_id = run_dir.name
                prov.miss("run_id",
                          "no /run_id — using the run directory name instead")

            agent_model = first_of(
                agent, "agent_model", prov, ("model",),
                ("model_resolution", "model"))
            if agent_model is None:
                v, ptr = dig(run_cfg, "agents", agent_key, "model")
                if v is not None:
                    prov.hit("agent_model", f"/run_config/agents/{agent_key}/model")
                    agent_model = v
            judge_model = first_of(run_cfg, "judge_model", prov,
                                   ("judge", "model"), ("provider", "model"))
            n_attempts = first_of(policy, "n_attempts", prov, ("max_attempts",))
            if n_attempts is None:
                v, _ = dig(run_cfg, "harbor", "n_attempts")
                if v is not None:
                    prov.hit("n_attempts", "/run_config/harbor/n_attempts")
                    n_attempts = v

            digest = first_of(ds, "dataset_digest", prov, ("dataset_digest",))
            digest_algo, _ = dig(ds, "dataset_digest_algorithm")

            rid = hashlib.sha256(
                f"{run_id}|{skill}|{case}|{agent_key}".encode()).hexdigest()[:16]

            def opt(container, *keys):
                v, _ = dig(container, *keys)
                return v

            row = {
                # --- A. ours, stable -------------------------------------
                "schema_version": SCHEMA_VERSION,
                "record_id": rid,
                "recorded_at": now.replace(microsecond=0).isoformat(),
                "skill": skill,
                "skill_version": skill_version,
                "case_id": case,
                "agent_runner": agent_key,
                "agent_model": agent_model,
                "judge_model": judge_model,
                "n_attempts": n_attempts,
                "score_with_skill": sw,
                "score_without_skill": so,
                "lift": lift,
                "lift_source": lift_source,
                "verdict": lift_verdict(lift),
                "status": status,
                "error_type": error_type,
                "error_detail": error_detail,
                "dataset_digest": digest,
                "dataset_digest_algorithm": digest_algo,
                "adapter_version": ADAPTER_VERSION,
                "source_run_dir": str(run_dir),
                # --- B. opportunistic passthrough ------------------------
                "skillevaluator_version": result.get("evaluator_version"),
                "run_id": run_id,
                "evaluated_at": result.get("evaluated_at"),
                "duration_seconds": result.get("duration_seconds"),
                "metric_set": result.get("metric_set"),
                "metrics": result.get("metrics"),
                "grading_mode": opt(run_cfg, "grading", "mode"),
                "pass_threshold": policy.get("pass_threshold"),
                "custom_lift": coerce_score(opt(agent, "custom_lift", case)),
                "dimensions_with_skill": opt(agent, "dimensions_with_skill", case),
                "dimensions_without_skill": opt(agent, "dimensions_without_skill", case),
                "pass_at_k": opt(agent, "pass_at_k"),
                "execution_status": agent.get("execution_status"),
                # --- C. how we know ---------------------------------------
                "provenance": prov.dump(),
            }
            rows.append(row)
    return rows


def append_rows(rows, ledger=DEFAULT_LEDGER) -> int:
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return len(rows)


def load_ledger(ledger=DEFAULT_LEDGER) -> list[dict]:
    p = Path(ledger)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a corrupt line never takes the whole ledger down
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", nargs="+")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--skill")
    ap.add_argument("--skill-version")
    ap.add_argument("--stdout", action="store_true",
                    help="print rows instead of appending to the ledger")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    total = 0
    for d in a.run_dir:
        try:
            rows = adapt_run(d, repo=a.repo, skill_override=a.skill,
                             skill_version_override=a.skill_version)
        except UnrecognisedRunError as e:
            print(f"UNRECOGNISED RUN — nothing written.\n{e}", file=sys.stderr)
            return 2
        if a.stdout:
            for r in rows:
                print(json.dumps(r, indent=2))
        else:
            append_rows(rows, a.ledger)
        total += len(rows)
        if not a.quiet:
            for r in rows:
                bits = (f"{r['skill']}@{r['skill_version']} {r['case_id']} "
                        f"[{r['agent_runner']}/{r['agent_model']}] "
                        f"status={r['status']} verdict={r['verdict']}")
                if r["error_type"]:
                    bits += f" error={r['error_type']}"
                print(bits)
    if not a.quiet:
        dest = "stdout" if a.stdout else a.ledger
        print(f"{total} row(s) -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
