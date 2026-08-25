#!/usr/bin/env python3
"""Push this machine's NEW ledger lines to an Azure DevOps Artifacts feed.

Why a feed and not a git repo: the ledger needs real deletion (a scrub of one
sensitive row must actually stop propagating, not just be un-reachable from
HEAD while every clone still has it in history). A git repo can't do that
cleanly; an Artifacts feed can drop a specific package version.

Why "delta push" and not "push my whole file every time": each push becomes
one immutable feed package VERSION containing only the lines written since
the last successful push. The render side (render_dashboard.py) downloads
every version of every machine's package and concatenates them in version
order to reconstruct full history — so a push is cheap (usually a handful of
lines) and no push ever duplicates data already on the feed.

This machine's own identity files only: `<tool>-<user>-<machine>.jsonl` in
the ledger dir (lmstudio-, claude-code-, codex-, ...). Never another
machine's file that happens to be visible in a shared LLM_TOKEN_LEDGER_DIR —
each machine pushes only what IT wrote.

Usage:
    push_ledger.py --org ORG --project PROJECT --feed FEED [--dry-run]

Reads DEVOPS_ORG / DEVOPS_PROJECT / DEVOPS_FEED from the environment as
defaults so a cron job or shell alias doesn't need to repeat them.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))
from llm_usage_ledger import (  # noqa: E402
    ledger_dir, whoami, machine_name, NON_USAGE_FILES,
)

STATE_FILE = ".push_state.json"


def load_state(d: Path) -> dict:
    p = d / STATE_FILE
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"files": {}, "seq": 0}


def save_state(d: Path, state: dict) -> None:
    (d / STATE_FILE).write_text(json.dumps(state))


def own_files(d: Path, user: str, machine: str) -> list[Path]:
    """This machine's own writer files — never another machine's, even if
    visible in a shared ledger dir."""
    suffix = f"-{user}-{machine}.jsonl"
    return sorted(p for p in d.glob(f"*{suffix}")
                  if p.name not in NON_USAGE_FILES)


def compute_deltas(files: list[Path], state: dict) -> dict[str, dict]:
    """{filename: {"lines": [new lines], "total": new total line count}} for
    files with unpushed content. Pure — no I/O beyond reading the files
    themselves; does not touch `state`.

    `total` (not just len(lines)) is returned because a file whose line count
    is now LOWER than what state recorded means it was reset (hand-edited, or
    a fresh install reusing the filename) — the whole current file becomes
    the delta, and the caller must set state to `total`, NOT
    `old_pushed_count + len(lines)`, or a reset silently under-counts forever.
    """
    deltas = {}
    for f in files:
        lines = f.read_text().splitlines()
        pushed = state["files"].get(f.name, 0)
        start = pushed if pushed <= len(lines) else 0
        new = lines[start:]
        if new:
            deltas[f.name] = {"lines": new, "total": len(lines)}
    return deltas


def stage_delta(tmpdir: Path, deltas: dict[str, dict]) -> None:
    for name, info in deltas.items():
        (tmpdir / name).write_text("\n".join(info["lines"]) + "\n")


def az_publish(org: str, project: str, feed: str, name: str, version: str,
               path: Path, description: str) -> subprocess.CompletedProcess:
    cmd = ["az", "artifacts", "universal", "publish",
           "--organization", f"https://dev.azure.com/{org}",
           "--project", project, "--scope", "project", "--feed", feed,
           "--name", name, "--version", version, "--path", str(path),
           "--description", description]
    return subprocess.run(cmd, capture_output=True, text=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", default=os.environ.get("DEVOPS_ORG"))
    ap.add_argument("--project", default=os.environ.get("DEVOPS_PROJECT"))
    ap.add_argument("--feed", default=os.environ.get("DEVOPS_FEED"))
    ap.add_argument("--dry-run", action="store_true",
                     help="show what would be pushed; touch nothing on the "
                          "feed and do not advance local push state")
    a = ap.parse_args(argv)

    d = ledger_dir()
    user, machine = whoami(), machine_name()
    files = own_files(d, user, machine)
    if not files:
        print(f"push_ledger: no ledger files for {user}@{machine} in {d}")
        return 0

    state = load_state(d)
    deltas = compute_deltas(files, state)
    if not deltas:
        print("push_ledger: nothing new to push")
        return 0

    n_lines = sum(len(info["lines"]) for info in deltas.values())
    pkg_name = f"ledger-{user}-{machine}"
    version = f"0.1.{state['seq']}"
    desc = (f"delta push {datetime.datetime.now().astimezone().isoformat(timespec='seconds')}"
            f" — {len(deltas)} file(s), {n_lines} line(s)")

    if a.dry_run:
        print(f"push_ledger: DRY RUN — would publish {pkg_name} v{version} "
              f"to {a.org}/{a.project}/{a.feed}:")
        for name, info in deltas.items():
            print(f"  {name}: +{len(info['lines'])} line(s)")
        return 0

    missing = [k for k in ("org", "project", "feed") if not getattr(a, k)]
    if missing:
        sys.exit(f"push_ledger: missing --{missing[0]} (or its DEVOPS_* env var)")

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        stage_delta(tdp, deltas)
        r = az_publish(a.org, a.project, a.feed, pkg_name, version, tdp, desc)
    if r.returncode != 0:
        sys.exit(f"push_ledger: az publish failed (exit {r.returncode}):\n"
                  f"{r.stderr.strip()}\nLocal state NOT advanced — the next "
                  f"run will retry the same delta.")

    state["seq"] += 1
    for name, info in deltas.items():
        state["files"][name] = info["total"]
    save_state(d, state)
    print(f"push_ledger: published {pkg_name} v{version} — {n_lines} line(s) "
          f"across {len(deltas)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
