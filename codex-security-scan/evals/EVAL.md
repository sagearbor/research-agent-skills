# Tier 3 eval set — codex-security-scan

Five cases in the SkillEvaluator `agentskills` format: four positive, one
negative. Each positive case is a tiny, obviously synthetic, deliberately
vulnerable repo plus a prompt a real user would type. The agent runs each prompt
twice — with the skill and without — and the difference is Skill Lift.

```
evals/
├── evals.json                 5 cases, {skill_name, evals[]}
├── config.yml                 schema_version 1, grading.mode default_plus_custom
├── grader.py                  BYOG deterministic checks → custom_metrics
├── environment/Dockerfile     pre-warms the npm package (see "Network")
├── files/<case-id>/           fixture repos
└── results-20260828.json      first executed run (see "Executed" below)
```

**Status: executed once, 2026-08-28, outside SkillEvaluator.** See
[Executed: 2026-08-28](#executed-2026-08-28--30-subagent-runs-k3) at the bottom
for the numbers, the method, and two grader defects the run exposed. The Tier-3
Docker path below is still UNVERIFIED — it was not needed and was not used.

---

## Network: this skill `npm install`s at runtime. Verdict and options.

**As shipped, `codex-security-scan` cannot run in a network-isolated Tier 3
sandbox.** `csec.py bootstrap` — called implicitly by `start`, `finish`,
`doctor`, `list` and `dashboard` — runs:

```
npm install @openai/codex-security@<UPSTREAM_PIN> --no-audit --no-fund
```

against the public npm registry, into `$CODEX_SECURITY_SKILL_CACHE`. Upstream is
deliberately **not vendored**: the whole design is that OpenAI's methodology,
schemas, SQLite workbench and SARIF finalizer arrive from npm so their
improvements land without edits here. With no network and a cold cache the
`subprocess.run(..., check=True)` raises, and **every deterministic check in
`grader.py` fails for reasons that have nothing to do with the model**. The
Skill Lift you would compute measures npm reachability, not the skill.

Three options, in the order I would take them.

### Option 1 — pre-baked image (recommended; implemented here)

`bootstrap()` is idempotent and offline-safe once the cache exists:

```python
if force or not workbench().exists():
    ...  # npm install only runs when the cache is cold
```

So warm the cache at **image build time**, which has network, and the sandboxed
run needs none. That is exactly what `evals/environment/Dockerfile` does: it
installs `nodejs`/`npm`, creates `/opt/codex-security-cache`, runs the one
`npm install`, and asserts `workbench_db.py` landed. `CODEX_SECURITY_SKILL_CACHE`
is exported in the image so `csec.py` finds it.

Two caveats, both real:

* **The `FROM` line is unverified.** With `custom_dockerfile_mode: rebase` (set
  in `config.yml`) SkillEvaluator rewrites `FROM` onto its own agent base image,
  so `python:3.12-slim` is a placeholder and the `apt-get` package names must
  suit whatever base is substituted. Run
  `skillevaluator tier3 validate ./codex-security-scan --harbor-contract --json`
  and one throwaway `tier3 evaluate --harbor-keep-jobs` before trusting a score.
* **Pin drift.** `UPSTREAM_PIN` is `0.1.1` and the Dockerfile hardcodes the same
  version. `bootstrap()` checks only whether `workbench_db.py` *exists*, never
  which version it is, so a stale warm cache is silently used forever. Re-warm
  the image whenever `UPSTREAM_PIN` changes, and run
  `codex-security-scan/tests/contract_test.py` first.

`CODEX_SECURITY_STATE_DIR` is deliberately **not** set in the Dockerfile. The
default `~/.codex-security-state` is already per-container and every Harbor case
gets a fresh container, so cross-case finding leakage cannot happen. Do not
"improve" this by pointing all cases at a shared state dir — the workbench is
explicitly cross-repo and cross-session, which is the product, and a shared dir
would let case 1's findings satisfy case 3's checks.

### Option 2 — vendor upstream

Copy `_bundled_plugin/` into the skill and drop the npm path. This makes the
skill trivially Tier-3-evaluable and trivially air-gapped. It also destroys the
skill's central design claim — that upstream's improvements arrive on their own
— and takes on redistribution of an Apache-2.0 package the `NOTICE` currently
says is *not* redistributed. Cheap to do, expensive to own. Not recommended
unless air-gapped operation becomes a requirement in its own right.

### Option 3 — accept that it is not Tier-3-evaluable in a sealed sandbox

Legitimate, and better than a fabricated number. If you cannot pre-bake an image
and will not vendor, then **do not run these cases and do not report zeros**.
The LLM-judged `assertions` in `evals.json` are graded from the trajectory and
need no workbench, so they would still score — but `grader.py`'s
`custom_metrics` would be uniformly 0.0 in *both* arms, and lift would look
flat for a harness reason. Report the custom metrics as N/A rather than as a
result.

---

## Cases

| id | bucket | planted vulnerability | decoy that must NOT be reported | extra thing under test |
|---|---|---|---|---|
| `csec-sqli-flask` | explicit | CWE-89 SQL injection, `app.py:19` | parameterised `/customers/<id>` (`app.py:24-32`) | SARIF 2.1.x validates; manifest carries a `sealedAt` the workbench stamped, not a hand-written one (SKILL Rule 1) |
| `csec-path-traversal-download` | explicit | CWE-22 path traversal, `server.py:11-17` | fixed-filename `/logo` (`server.py:19-23`) | coverage surfaces include a clean surface; refusal to dismiss on assumed deployment (SKILL Rule 2) |
| `csec-hardcoded-credential` | explicit | CWE-798 / CWE-259, `settings.py:10` and `:14` | `REPORTING_API_TOKEN` from `os.environ` (`settings.py:17`) | remediation text present; the credential literal must not be echoed into the SARIF |
| `csec-unsafe-deserialization` | implicit | CWE-502 attacker-controlled `pickle.loads`, `worker.py:11` | `yaml.safe_load` in `config.py` | severity reflects reachability; cross-repo dashboard renders and is self-contained |
| `csec-005-negative` | **negative** | — | — | a SARIF *documentation* question. `expected_skill: null`, `files: []`. The skill must not fire, and no scan may be opened |

Line windows in `grader.py` are ±2 around the sink so an agent that anchors on
the surrounding statement is not penalised.

**The negative case matters more than it looks.** A security scanner is exactly
the kind of skill whose description ("find vulnerabilities, check for OWASP or
CWE issues, produce SARIF") over-triggers on any sentence containing the word
SARIF. `csec-005-negative` sets `expected_skill: null` **explicitly** — without
it, `_normalize_entry` injects the top-level `skill_name` and grades it as a
positive case, which is the most common way to author a broken negative.

## Why an agent without the skill plausibly does worse

Detection is the *weaker* half of the lift here — a competent agent finds
textbook SQLi and pickle-RCE unaided. Three things separate the arms:

1. **Artifacts.** Without the skill there is no workbench, no schema validation,
   no sealed manifest and no real SARIF. An agent asked for SARIF hand-writes a
   plausible-looking blob with invented rule metadata. Every deterministic check
   in `grader.py` targets this, and it is where the lift lives.
2. **False-positive and false-negative discipline.** Each fixture carries a
   decoy. Upstream's `validation/SKILL.md` forces "reportable vs rejected" into
   an explicit step; without it, agents both over-report (flagging
   `yaml.safe_load`, flagging `os.environ.get`) and under-report (dismissing the
   traversal because "it's internal", dismissing the credentials because the
   literals say `FIXTURE`).
3. **Coverage claims.** Surfaces are how a scan says what it *cleared*, not just
   what it found. Nothing prompts an unaided agent to produce that, so a
   zero-finding file is indistinguishable from an unread one.

## How the two grading halves split

| half | mechanism | what it is good for |
|---|---|---|
| `assertions` in `evals.json` | LLM judge (`judge_behavior_check` → the `behavior_check` metric) | "did the agent explain the mechanism", "did it refuse to dismiss on assumed deployment". Trajectory-text facts. Both arms can score here — this is the honest baseline. |
| `custom_metrics` from `grader.py` | deterministic file inspection | "does the sealed SARIF carry CWE-89 anchored at app.py:19". File facts. The no-skill arm scores near zero here by construction. |

`grader.py` never writes the reserved names `security`, `skill_execution`,
`skill_efficiency`, `accuracy`, `goal_accuracy`, `behavior_check` — a collision
there fails grading outright.

### `grader.py` metric inventory

| metric | cases | pass condition |
|---|---|---|
| `cwe_reported` | 1-4 | a sealed finding carries the expected CWE |
| `finding_located` | 1-4 | that finding is anchored inside the sink window |
| `decoy_not_reported` | 1-4 | no finding is anchored on the decoy |
| `severity_reasonable` | 1, 4 | severity is high or critical |
| `finding_volume_sane` | 1 | ≤2 findings for one injection site |
| `sarif_wellformed` | 1-3 | a SARIF 2.1.x document with ≥1 result exists |
| `manifest_sealed_by_workbench` | 1 | `scan-manifest.json` carries a `sealedAt` |
| `coverage_claims_clean_surface` | 2, 4 | ≥1 surface with `no_issue_found`/`not_applicable`, ≥2 surfaces total |
| `remediation_actionable` | 3 | the finding carries >20 chars of remediation |
| `credential_not_echoed` | 3 | neither fixture literal appears in the SARIF |
| `dashboard_self_contained` | 4 | a rendered HTML page with no external `src`/`href` |
| `no_scan_was_opened` | 5 (negative) | no `scan-manifest.json` or `findings.json` anywhere |

`decoy_not_reported` and `finding_volume_sane` are **not emitted at all** when
zero findings were reported — a precision check is undefined when nothing was
reported, and scoring them 1.0 would hand a free 2/7 to an arm that never
scanned. Verified: a fully correct scan scores `overall 1.0` across 7 metrics; a
no-scan arm scores `overall 0.0` across 5.

## Fixture staging — one thing to verify before the first run

`files` paths are relative to `evals/` and stage into `/workspace/input/`. The
one documented data point in the spec is a **file** ref
(`files/case-001/deploy.env` → `/workspace/input/deploy.env`), i.e. the case
prefix is stripped. These cases use **directory** refs
(`files/csec-sqli-flask`) because the fixtures are multi-file repos, and the
prompts therefore say "the repo in `/workspace/input`".

If directory refs turn out to nest under the case-id (`/workspace/input/csec-sqli-flask/`),
the prompts need one word changed each. Confirm with
`skillevaluator tier3 validate ./codex-security-scan --harbor-contract --json`
before spending on a run. The fallback that definitely works — omitting `files`
entirely to stage all of `evals/files/` — is **not** acceptable here: it would
put all four vulnerable repos in front of every case and let decoys leak across
cases.

## Fixtures

`files/<case-id>/` — 20-40 lines each. Every file carries a
`SYNTHETIC EVAL FIXTURE` header, every fabricated credential is a visible
placeholder that authenticates to nothing, and every README says do not deploy.
Legible at a glance by design: the eval measures process discipline, not
needle-finding.

## Known leakage and how it was handled

`SKILL.md` ships a worked `analysis.json` example whose finding is
**zip-extraction path traversal (CWE-22)**. Reusing that shape would let the
with-skill arm pattern-match the answer straight out of the skill text and
inflate lift. `csec-path-traversal-download` therefore uses a different traversal
shape — a caller-named download joined to a storage root — the same CWE reached
by different reasoning. No other case overlaps a SKILL.md example.

## Residual risks

* **Negative-case assertions are judge-graded.** "The agent did not read
  codex-security-scan/SKILL.md" is checked against the trajectory by an LLM. The
  deterministic half (`no_scan_was_opened`) is the reliable signal; treat the
  judged half as corroboration.
* **`dashboard_self_contained` scans `*.html` under `/workspace`, `/root`,
  `/home`, `/tmp`** and filters on the words "codex" or "security" appearing in
  the page, because the agent chooses the filename. It is the loosest check in
  the set. If the harness exposes the agent's working directory explicitly,
  tighten it to that path.
* **Rule 0 telemetry.** SKILL.md asks for `tests/run_tests.py --auto` once per
  session, which appends to `../telemetry/*.jsonl` in the repo. Inside a
  container that is harmless; if you ever run these cases against a live
  checkout, the ledger accumulates runs that are not real usage.
* **`n_attempts: 3` triples the bill.** Cost is `cases × agents × attempts ×
  arms` = 5 × agents × 3 × 2 = 30 agent runs per agent. Drop to `n_attempts: 1`
  for a first smoke run, then raise it once the contract is verified.

---

## Executed: 2026-08-28 — 30 subagent runs, k=3

First actual execution of this eval set. **SkillEvaluator was not used**, and
neither was `environment/Dockerfile`; both remain UNVERIFIED. The design above
was executed by hand with Claude Code subagents, which needs no sandbox, no npm
cache inside a container, and no `FROM`-line guess.

### Method

30 runs = 5 cases × 2 arms × k=3, one `general-purpose` subagent per run, each
scoped to its own directory under a per-run root.

| | with-skill arm ("primary") | no-skill arm ("baseline") |
|---|---|---|
| cases 1-4 | the `evals.json` prompt + "read `codex-security-scan/SKILL.md` and follow it" | **only** "review this repo for security vulnerabilities and report findings with file and line". No skill, no `csec.py`, no mention of SARIF or a workbench. |
| case 5 (negative) | skill *offered* by name + description + path, with "use it if and only if it applies" — the over-triggering question | the `evals.json` prompt alone |

Case 5 uses the case prompt in both arms because it has no repository; the
generic baseline sentence would have asked a different question.

Two deliberate deviations from the shipped instructions, applied to **both**
arms so they cannot move the delta: SKILL.md **Rule 0** (the `run_tests.py
--auto` telemetry append) was skipped, because these are not real usage and the
residual-risk note above says so; and cases 2-5 were told "static review, do not
create virtualenvs or run the app" after case 1's first primary run spent three
minutes building a Flask venv to execute a proof-of-concept. Case 1 primary
attempt 1 ran before that instruction existed and did execute a PoC.

Isolation: each run got its own `CODEX_SECURITY_STATE_DIR`, so no case's
findings could satisfy another's checks. The developer's real workbench at
`~/.codex-security-state` was byte-identical before and after (92 files).

### Scoring

`grader.py` was **not** modified and not reimplemented — the driver imports it
and calls `run_checks(case_id, spec)` unchanged. The only thing supplied is the
sandbox the grader assumes:

```python
import grader
os.environ["HOME"] = str(EMPTY_DIR)               # neutralise ~/.codex-security-state
os.environ["CODEX_SECURITY_STATE_DIR"] = str(RUN / "state")
grader.SEARCH_ROOTS = (str(RUN),)                 # this run's container-equivalent /workspace
metrics, details = grader.run_checks(case, grader.CASES[case])
```

`SEARCH_ROOTS` is `("/workspace","/root","/home","/tmp")` because in the
intended harness each case owns a container and those paths *are* that case's
filesystem. Outside a container they are shared by every run on the machine, so
they are repointed at the run directory — the container equivalent. The check
logic is untouched.

Sanity checks before the real runs: a hand-authored perfect scan scores
`overall 1.0` across 7 metrics on case 1; an empty directory scores `0.0` across
5 on case 1 and `1.0` on case 5. Both match this document's claims exactly.

### Result

`delta_pass_rate = primary.mean − baseline.mean = 0.990 − 0.240 = **+0.751**`

| case | primary (k=3) | baseline (k=3) | delta |
|---|---|---|---|
| `csec-sqli-flask` | 1.00, 1.00, 0.86 | 0.00, 0.00, 0.00 | **+0.952** |
| `csec-path-traversal-download` | 1.00, 1.00, 1.00 | 0.00, 0.00, 0.00 | **+1.000** |
| `csec-hardcoded-credential` | 1.00, 1.00, 1.00 | 0.20, 0.20, 0.20 | **+0.800** |
| `csec-unsafe-deserialization` | 1.00, 1.00, 1.00 | 0.00, 0.00, 0.00 | **+1.000** |
| `csec-005-negative` | 1.00, 1.00, 1.00 | 1.00, 1.00, 1.00 | **+0.000** |

Per-case deltas: mean +0.751, sd 0.428, n=5 cases. The noise band this document
quotes for the intended harness is roughly −0.10…+0.05; four of five cases sit
an order of magnitude outside it and the variance within an arm is near zero
(29 of 30 runs scored exactly 0.00, 0.20 or 1.00). n=5 cases is still small, so
the *aggregate* is not tightly bounded — but the per-case results are not
ambiguous.

**False positives on the negative case: zero.** All three with-skill attempts
answered the `partialFingerprints` documentation question directly and opened no
scan, so `no_scan_was_opened` = 1.0 in both arms. The skill's broad description
("produce SARIF for CI") did not over-trigger on a question that contains the
word SARIF.

Raw per-run metrics: `results-20260828.json`. Ledger rows (one per case):
`telemetry/evals/skill-lift.jsonl`, rendered by `telemetry/evals/render_lift.py`.

### What the number does and does not mean

**It measures artifacts, not detection.** Five of the seven case-1 metrics —
and every metric on cases 2-4 except `decoy_not_reported` and
`credential_not_echoed` — read a *sealed workbench finding*. Only the with-skill
arm can produce one. The baseline arm therefore scores near zero **by
construction**, whatever it finds, which is exactly what §"How the two grading
halves split" predicted. +0.751 is a faithful measurement of "did the agent
produce contract artifacts", and it is close to the ceiling that design allows.

**Detection was a tie.** Read from the arms' prose reports (not from
`grader.py`, and recorded here as an observation, not a score): the baseline
arm named the planted sink at the right file and line, with the right CWE, in
**12 of 12** positive runs, and explicitly declined to report the planted decoy
in **12 of 12**. The with-skill arm did the same in 12 of 12. On these fixtures
a competent unaided agent loses nothing on finding the bug — as this document
predicted. What it loses is the SARIF, the sealed manifest, the coverage claim,
and the cross-repo record.

### Two grader defects this run exposed

1. **`decoy_not_reported` is location-based, not class-based.** Case 1 primary
   attempt 3 scored 0.86 because it filed a second, *legitimate* CWE-306
   "both endpoints return PII with no authentication" finding whose location
   spans `app.py:24-31` — inside the decoy window `("app.py", 24, 32)`. It did
   not report the parameterised handler as SQL injection; it reported a
   different vulnerability class that happens to live on those lines. The check
   should require the offending finding to carry the case's own CWE, or the
   decoy window should be narrowed to the `conn.execute` call.
2. **`credential_not_echoed` hands the no-scan arm a free 1.0.** It is the
   entire reason `csec-hardcoded-credential` baseline scores 0.20 rather than
   0.00: with no SARIF on disk, no credential literal can appear in one. It is a
   precision check and belongs in the same `undefined` tuple as
   `decoy_not_reported` and `finding_volume_sane` when zero findings were
   reported. Left as-is here so the published number comes from the grader as
   the design shipped it; fixing it would move the delta from +0.751 to +0.791.
