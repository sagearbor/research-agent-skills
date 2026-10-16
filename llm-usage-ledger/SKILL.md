---
name: llm-usage-ledger
description: Track EVERY LLM usage stream — cloud API calls, Claude Code/Codex subscription transcripts (ingest), and local models — in one append-only cross-repo token ledger with dated as-of price series, burst analysis, and interactive $-vs-tokens HTML dashboards. No local-model software required. Use whenever asked about LLM usage, token ledgers, spend/cost reporting, subscription-usage accounting, or price series in ANY repo.
metadata:
  author: Sage Arbor <sage.arbor@duke.edu>
---

# llm-usage-ledger

## Quick start (for a new user — 2 commands)

```
python3 <skill dir>/llm_usage_ledger.py install   # once: puts `ailedger` on your PATH (~/.local/bin)
ailedger                                           # import usage, refresh live prices, open the dashboard
```
`<skill dir>` is wherever the plugin put this skill, e.g.
`~/.claude/plugins/cache/research-agent-skills/research-skills/<version>/llm-usage-ledger`.
`ailedger -h` lists every option. The dashboard's **"Price local models as"** menu switches local
models between same-model cloud rental (default, most realistic) and Claude Haiku / Sonnet / Opus /
Fable replacement cost; the page title always names the choice, so screenshots explain themselves.

**Counted with no setup:** Claude Code sessions (`~/.claude/projects` transcripts) and LM Studio
**server** requests (LM Studio's own logs, `~/.lmstudio/server-logs`; requests your code already
logged are skipped, so nothing is double-counted). **Not counted unless your code calls
`log_usage()`:** direct cloud/Azure API calls, and LM Studio's chat window. Counts only — never
prompt or answer text.

**Rule 0 (telemetry):** once per session, before first use, run
`python3 ~/.claude/skills/llm-usage-ledger/tests/run_tests.py --auto --model <your-model-id>`
(no-ops after 8 recorded runs per model+version).

**Rule 0.5 (auto-capture):** the first time this skill is used in a session,
also run `python3 ~/.claude/skills/llm-usage-ledger/llm_usage_ledger.py ingest
claude-code` (idempotent, seconds). Zero-thought automation for a whole org:
ship this as a plugin with a SessionStart hook, or add to settings.json:

```json
"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command":
  "python3 ~/.claude/skills/llm-usage-ledger/llm_usage_ledger.py ingest claude-code >/dev/null 2>&1 &"}]}]}
```

Capture boundary (by design): usage is recorded where our tooling runs —
instrumented pipelines, skill calls, assistant-session transcripts. Someone
chatting directly in a vendor's own app is invisible to the ledger, and
that's accepted: no attribution we could trust exists there anyway.

## What this skill provides

`llm_usage_ledger.py` — stdlib-only Python (no pip installs), usable as a
module or CLI by any repo, any AI tool, or a human. Most org users have NO
local models: this skill stands alone — it needs no LM Studio or any other
local-model software. (Local-model server control lives in the separate
`lmstudio-panel` skill, which delegates all its accounting here.)

| Command | Purpose |
|---|---|
| `report [--by project\|repo\|model\|user\|machine\|project-model] [--windows] [--days N]` | cross-repo usage rollup + burst analysis |
| `report --html [PATH] [--open]` | self-contained interactive $-vs-tokens dashboard; prints a clickable `file://` URL, `--open` launches the browser |
| `report --html --all` / `--user NAME` | on a SHARED ledger dir the dashboard defaults to **your own rows only**; `--all` shows everyone |
| `prices update` / `prices list` | append dated hosted rates (LiteLLM table) for models seen in the ledger; reports join **as-of** each event's date — entries are never edited, so old reports stay reproducible |
| `ingest claude-code` / `ingest codex` | derive subscription-plan usage (flat-fee tools) from local transcripts into the ledger — idempotent, merged by message UUID |
| `ingest claude-code --refresh` | **opt-in migration.** Re-derives rows already in the ledger so fields added to the importer later (`repo`, `d1`) reach records imported before those fields existed. It REWRITES existing rows — the plain `ingest` never does. Confirm before running. |

## Two developers, one repo, one number
`detect_project()` returns the **local folder name**, so two people who clone the
same repo into differently-named directories would log different projects and
their spend would never add up. Every event therefore also carries:

| Field | What it is |
|---|---|
| `project` | human label — the repo folder name (may differ per developer) |
| `repo` | **the join key** — the *canonical* git remote (`host/org/name`), identical on every clone on every machine however it was cloned |
| `d1` | institutional project code, or absent — never invented |

`report --by repo` groups by that key. Group by `repo` for a number that
survives people naming folders differently.

**`repo` is canonical, not the raw remote URL.** `git@github.com:Org/Repo.git`
and `https://github.com/org/repo` are the same repository, so both canonicalise
to `github.com/org/repo`. Storing the raw URL (what this used to do) split the
join in half between the SSH people and the HTTPS people, with no error and two
totals that each looked complete.

**A directory with no remote gets `no-remote:<12 hex>`**, not a filesystem path.
A path would put `/home/USER/…` into a shared ledger and still join with
nobody; the marker is explicit about being unjoinable, and the digest keeps two
scratch directories apart without naming anyone.

`d1` comes from the registry agent-coach writes
(`~/.agent-coach/projects.json`), so tagging a repo once with
`agent_coach.py project D1-4471` labels it for BOTH ledgers. Untagged repos and
POCs simply have no `d1` — the field is omitted rather than guessed.

## Unknown identity stays unknown
Records written by a tool that recorded no `user` (the `tokens_in/tokens_out`
schema does not have the field) are bucketed as **`unattributed`** — a real
value in the report, with its own bar in the dashboard. They are NEVER
attributed to whoever runs the report. That default used to credit 22,244 live
rows to the reader, which is worse than no dashboard, because the number looks
authoritative. On a single-person view (`--user NAME`) unattributed rows are
excluded and the page says so; `--all` shows the bucket.

`prices.jsonl` shares the ledger directory but is a **price series, not usage**,
and is excluded from every read by name.

## Pricing rules that are easy to get wrong

| What | Rate | Why it matters |
|---|---|---|
| Prompt / completion tokens | the model's `input_per_m` / `output_per_m` | — |
| **Price changes** | join **as-of** the event's own date — the latest `prices.jsonl` entry dated `<=` the event | Pricing a whole period at today's rate silently rewrites history. `as_of_price(model, ts, series)` does this join. |
| Model with no price entry | the dated `reference` benchmark, then the builtin table | Never drop the model and never borrow another model's rate. |
| **Cache reads** (`cache_read_tokens`) | **0.1x the input rate** | Anthropic charges cached reads at 10%. They are usually the LARGEST token count on a Claude Code row — 21,435 reads against 2,409 input tokens is typical — so pricing them as ordinary input tokens overstates the total several-fold. |
| **5-minute cache writes** (`cache_w5_tokens`) | **1.25x the input rate** | Writes carry a premium, not a discount. |
| **1-hour cache writes** (`cache_w1h_tokens`) | **2.0x the input rate** | Keep the TTL split the transcript provides; older records with a flat `cache_creation_input_tokens` are booked at the cheaper 5-minute premium, which understates rather than inflates. |

## How local models are priced (2026-10-16)
`prices update` fetches the LiteLLM community table (the same live data `npx ccusage` uses) and appends dated rows.
For each model: **Azure's own listing if one exists**, else the **median across every host** serving that exact
model (`-it`/`-instruct`/quant suffixes count as the same model). Usage older than a model's first price row is
back-filled at that first row. A local model with no hosted listing uses `reference-open` (median of the local
open models priced that day) — never the frontier `reference` benchmark, which overstated local savings ~50x.

## Where reports are written
Never `$HOME`, never the cwd. `report --html` with no path writes to
`~/.llm_token_ledger/reports/llm-spend.html` and prints a clickable `file://`
URL; `--open` launches the browser. Reports stay LOCAL even when the ledger
itself is on a shared drive — a personal report should not auto-publish to
colleagues. Pass an explicit path to override.

## Shared drive: you see you, by default
Point `LLM_TOKEN_LEDGER_DIR` at a network path and every colleague's file lands
in one directory. But a person opening their dashboard should see **their own**
usage — not an unrequested league table of who spent what. So when that env var
is set, `report --html` filters to the current user automatically. `--all` opts
into the org roll-up, `--user NAME` looks at one person. On a local dir the
filter is a no-op, so nobody has to think about it.

## Rules

1. **Never make an LLM call without logging.** Call `log_usage()` right after
   any completion (see the 3-line recipe below). The ledger is the
   cost-justification evidence; silent calls destroy it.
2. **Ledger location:** `$LLM_TOKEN_LEDGER_DIR` (default `~/.llm_token_ledger/`).
   One file per (user, machine): `lmstudio-<user>-<machine>.jsonl` (prefix
   kept from this code's original home for back-compat of existing data) —
   safe to point at a shared location, no write collisions. Orgs: set the
   env var to the shared path; `report` aggregates every file it sees.
3. **reasoning_tokens is `null`** when a model doesn't report it — never
   fabricate zeros.
4. Events are raw + timestamped; derive new views (windows, bursts, per-dev)
   at report time — do not pre-aggregate into the ledger.
5. **Subscription events stay separate:** ingest records carry
   `subscription: true` and are priced at API rates as "what the plan
   absorbed" — never mixed into local savings or cloud spend.
6. **Counts, never content.** The ledger records how many tokens, never what
   was said. That invariant is what makes it installable org-wide, and it holds
   in the error paths too: an unparsed transcript line is logged by SHAPE
   (length, which field failed, a sha256 prefix) and never by excerpt. Set
   `LLM_LEDGER_SUPPORT_CONTACT` to route importer bug reports; the default
   names a role, never a person.
7. **Identity is recorded, never inferred.** No read path may substitute the
   reader's own `user`/`machine` for a missing one — see `unattributed` above.

## Instrumenting any repo's own LLM client (3 lines)

A repo does NOT need to route calls through this skill to be counted — hook
its client's success path once:

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path.home() / ".claude/skills/llm-usage-ledger"))
from llm_usage_ledger import log_usage
# right after each successful completion:
log_usage(model, response_usage_dict, duration_s, task_tag="my-pipeline",
          provider="azure")   # provider defaults to "lmstudio" (local)
```

Repos with their own writer (like llm-as-judge's `token_ledger.py`) are also
fine AS-IS: `report`/`report --html` read **every `*.jsonl` in the ledger
dir** and normalize both schemas (`prompt_tokens/completion_tokens` and
`tokens_in/tokens_out`). One drop-dir, any writer, one merged report — set
`LLM_TOKEN_LEDGER_DIR` to a shared location for org-wide rollups.
