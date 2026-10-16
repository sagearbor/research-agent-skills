# llm-usage-ledger — Tier 3 eval set

Five positive cases and one negative case for NVIDIA SkillEvaluator Tier 3,
which runs the same prompt twice — once with the skill available, once without
— and scores the difference as **Skill Lift**. Every case here is built so that
a competent agent *without* llm-usage-ledger plausibly gets a **different
number**, because a case both arms answer identically produces no lift and is
not worth running.

## Layout

| Path | Role |
|---|---|
| `evals.json` | the dataset, `agentskills` format (`{skill_name, evals[]}`) |
| `config.yml` | run policy + `grading.mode: default_plus_custom` |
| `grader.py` | BYOG grader — owns the arithmetic, which no LLM judge should be asked to eyeball |
| `files/<case-id>/setup.sh` | one self-contained script per case, builds the ledger at `/workspace/ledger` |

## Why there is a custom grader

`assertions` in `evals.json` is a flat array of plain-English strings graded by
an LLM judge (`judge_behavior_check`). Every claim this skill makes is a claim
about a **number**: $1.8503 versus $5.7203 is the difference between correct
cache-tier pricing and treating cached reads as ordinary input tokens, and a
judge reading a trajectory cannot separate those with any confidence. So the
behavioural half lives in `assertions` and the arithmetic lives in `grader.py`,
which scores every positive case on three metrics:

| Metric | What it means |
|---|---|
| `answer_file_valid` | `/workspace/output/answer.json` exists, parses, and has the shape the prompt asked for |
| `accounting_correct` | the figure is right — and when it is wrong, the reason string names *which* wrong answer was produced (priced at the current rate, cache ignored, price rows counted as calls, …) |
| `ledger_integrity` | **anti-degenerate.** The fixture files that ARE the accounting record are byte-identical afterwards. A reporting tool that edits the books until the report comes out has not reported. |

The grader accepts `1.85`, `"1.85"` and `"$1.85"` as the same answer, and
matches model/person keys case-insensitively by substring, so it scores the
answer rather than its typography. None of the six reserved metric names is
written.

## The cases — and the specific way the no-skill arm fails each one

| # | Case | Skill knowledge under test | Named failure of the without-skill arm |
|---|---|---|---|
| 1 | `lul-as-of-price-across-change` | prices join **as-of the event's own date**, and a model with no price row falls back to the dated `reference` benchmark | qwen3-32b's rate dropped on 2026-07-15 and two of its four events predate the change. The natural move — total the tokens, multiply by the rate you found — gives **$1.8360** at the current rate (16% low) or **$2.8800** at the old one (32% high) against a true **$2.1840**. Both look like clean answers and neither prints a warning. Second failure: `mistral-small-3` has no price row, so it is silently **dropped from the bill** (or given qwen's rate) instead of being priced at the reference benchmark. |
| 2 | `lul-cache-tier-economics` | Anthropic cache economics: reads **×0.1**, 5-minute writes **×1.25**, 1-hour writes **×2.0** of the input rate | The rows carry 1.6M cache reads against 3,000 input tokens, so this field dominates the answer completely. Pricing cache tokens as ordinary input tokens gives **$5.7203** — 3.1× too high. Ignoring the `cache_*` fields, which is what happens if you only look for `prompt_tokens`/`completion_tokens`, gives **$0.0203** — 91× too low. True answer **$1.8503**. There is no way to guess the three multipliers, and getting them wrong does not look like an error. |
| 3 | `lul-reingest-no-double-count` | ingest merges by the transcript's own message UUID, into one derived file, so re-running is a no-op | Two opposite traps in one fixture. `m1`/`m2` are in both the transcripts and the existing ledger, so a hand-rolled importer that appends produces **6 records / 1500 tokens** — the flat-fee plan looks 50% more expensive than it was. `m0` is in the ledger but the tool has already **pruned its transcript**, so an importer that rebuilds the file from what it can see produces **3 records / 900 tokens**, silently deleting accounting history. Correct is 4 / 1000. The prompt says the job will run from a session-start hook, which is the requirement, not the method. |
| 4 | `lul-shared-dir-rollup` | non-usage files are excluded **by name**; a record with no user is bucketed as `unattributed` and never attributed to the reader | `prices.jsonl` shares the directory and the extension, and its 12 rows parse cleanly and carry a `ts` — so any `*.jsonl` sweep reports **25 calls instead of 13**. Then the request applies pressure ("every call has to land in a column"), and the 6 rows from the foreign-schema `ledger.jsonl` have no user in the row *or* the filename. Folding them into dana, into ravi, or into whoever ran the report is the accommodating answer and the wrong one; the grader fails a bucket keyed on the running account by name. |
| 5 | `lul-two-clones-one-repo` | the join key is the **canonicalised** git remote (`host/org/name`), and a directory with no remote gets an explicit not-joinable marker rather than its path | The two checkouts are one repository, cloned as `git@github.com:sagearbor/ras.git` and `https://github.com/SageArbor/RAS.git` — scheme, punctuation and capitalisation all differ. Grouping on the folder name (`research-agent-skills` vs `ras`) or on the raw remote string yields **two half-totals that both look complete**, which is the worst failure shape available. The scratch directory has no remote at all: keying it on `/workspace/dev-c/scratch` publishes a local path — and in real use the local username — into a shared ledger while still joining with nothing. |
| 6 | `lul-invoice-column-sum-negative` | — | **Negative case**, `expected_skill: null`. A pasted vendor invoice, a column to sum, and an explicit "don't touch or create any usage-tracking files". Squarely in the skill's advertised subject area (spend/cost reporting by model) with nothing to ingest and no ledger in play. Detects over-triggering. |

Cases 1–5 are **implicit**: none names the skill, because naming it measures
routing rather than lift.

## Cases deliberately NOT written, and why

Writing a case that both arms pass is worse than writing no case: it dilutes
the lift measurement and reads like evidence that the skill adds nothing.

| Rejected case | Why it would show no lift |
|---|---|
| **Error-log hygiene** — an unparsed transcript line must be logged by shape, never by content | The with-skill arm just runs the importer, and whether the without-skill arm's hand-written parser happens to log the offending line at all is luck, not skill. The failure cannot be reliably triggered, so the measurement would be noise. The invariant is covered by a unit test (`ingest_error_logs_shape_not_content`) instead, which is where a deterministic property belongs. |
| **Burst / hourly-window analysis** | Bucketing timestamps by hour and dividing peak by mean is ordinary data manipulation. An unaided agent does it correctly. No lift. |
| **`reasoning_tokens` is null, never 0** | The distinction is real and documented, but any answer that sums a column of nulls either crashes immediately or is fixed in the same turn. Too small to separate the arms. |
| **`prices update` against the live LiteLLM table** | Both arms can `curl` the same public URL, so the lift would be zero — and it would make the score depend on network access and on an upstream file that changes, which is a reproducibility problem, not a skill measurement. |
| **Grouping an existing ledger by `repo` where the rows already store raw remote URLs** | The skill canonicalises at **write** time, not at read time, so it would not help here either. Case 5 exercises the same knowledge at the point where the skill genuinely applies it (ingest). Building a read-time canonicaliser purely to make an eval pass would be writing the test's answer into the product. |

## Fixtures

Each case is one self-contained `setup.sh` rather than a tree of loose files,
because fixture staging into `/workspace/input/` does not guarantee that
subdirectories survive. Each script is idempotent (it removes and rebuilds its
directories) and:

- builds the ledger at `$LED` (default `/workspace/ledger`) and creates `$OUT`;
- puts transcripts under a **stand-in home** at `$FHOME` (default
  `/workspace/fakehome`), never the container's real `$HOME`. If the agent's own
  session transcript were reachable, the record counts would differ between
  arms and between runs, and the case would be unscoreable. The prompts say to
  point the import at that directory;
- in `lul-reingest-no-double-count`, **computes** the name of the
  already-ingested derived file (`claude-code-<user>-<machine>.jsonl`) rather
  than hardcoding it, because a fixture file the importer does not recognise as
  its own is not "already ingested" at all — the importer would write a second
  file beside it and the case's whole premise would evaporate;
- in `lul-two-clones-one-repo`, sets `user.email`, `user.name` and
  `commit.gpgsign` **`--local` only**, so it can never touch the host's global
  git config.

Run one by hand with `LED=/tmp/led OUT=/tmp/out FHOME=/tmp/fh WS=/tmp/ws sh files/<case-id>/setup.sh`.

## Expected values, and how they were obtained

Every figure in `grader.py` was produced by running the real skill against the
real fixture, not by hand arithmetic:

| Case | Expected | Verified by |
|---|---|---|
| 1 | qwen3-32b $2.1840, mistral-small-3 $3.5000, total $5.6840 | `as_of_price()` over the fixture ledger |
| 2 | $1.8503 | `_agg_rows()` over the fixture ledger |
| 3 | 4 records, 1000/100 tokens | `ingest claude-code` run twice |
| 4 | 13 calls — dana 4, ravi 3, unattributed 6 | `read_ledgers(scope="all")` |
| 5 | `github.com/sagearbor/ras` 3 calls / 7000 tokens; `no-remote:<digest>` 1 call | `ingest claude-code` over the two clones |

If a fixture changes, regenerate both the numbers and the `INTEGRITY` hashes;
the grader fails loudly (`unknown_case`, or an integrity miss naming the file)
rather than silently scoring against stale constants.

## Assumptions worth checking

- **The lift is knowledge + module, not the CLI alone.** `report --by model`
  prints tokens, not dollars, so cases 1 and 2 are answered by importing the
  skill as a module (`as_of_price`, `_agg_rows`) or by reading the rates the
  skill documents. Tier 3 stages `SKILL.md` and the skill's script, which is
  exactly what those cases need; nothing here depends on a hook being installed.
- **Case 5's lift is the weakest of the five.** A careful unaided agent *can*
  notice that two differently-spelled remotes are the same repository. The case
  is still worth running because the two other failure modes it grades —
  grouping on the folder name, and keying the scratch directory on its
  filesystem path — are the defaults, not the careful path. Expect a smaller
  gap here than in cases 1–3.
- **`git` must exist in both arms.** `pre_agent_setup` installs it if missing;
  if the base image is not Debian-family, change that line or case 5 fails for
  an environment reason and its lift number means nothing.
- **Nothing here needs network or pip.** The skill is stdlib-only by design, so
  the without-skill arm is never handicapped by a missing dependency.
