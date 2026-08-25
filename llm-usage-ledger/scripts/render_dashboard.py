#!/usr/bin/env python3
"""Merge every machine's pushed ledger deltas and render the shared dashboard.

Runs in the scheduled DevOps Pipeline (see ../pipelines/ledger-dashboard.yml),
NOT on a laptop. For each `ledger-*` package in the Artifacts feed (one
package per machine, written by push_ledger.py), downloads every VERSION
— each version is one delta push — and concatenates them in version order to
reconstruct that machine's full history. This is the read side of the
delta-push design: a push is cheap because it never re-sends data already on
the feed, and reconstruction stays correct because versions are downloaded
and replayed in the same order they were published.

Also pulls the latest `ledger-prices` package (if any machine has pushed one
via `llm_usage_ledger.py prices update` + a manual push) so the dashboard
prices as-of each event's date, same as a local report.

Two halves, kept separate so the merge/render logic is testable without a
live feed or `az` on PATH:
  - az_* functions: thin subprocess/REST wrappers, the only place that talks
    to Azure.
  - merge_versions() / render(): pure(ish) — operate on a directory tree
    already on disk.

Usage (in the pipeline):
    render_dashboard.py --org ORG --project PROJECT --feed FEED \
        --storage-account ACCOUNT --out /tmp/llm-spend.html

Usage (local test / dry run, no Azure at all):
    render_dashboard.py --local-only --downloaded-root ./fixture --out ./out.html
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

PACKAGE_PREFIX = "ledger-"
PRICES_PACKAGE = "ledger-prices"


# ------------------------------------------------------------------- az I/O
def az_list_packages(org: str, project: str, feed: str) -> list[dict]:
    """[{name, versions: [str, ...]}] for every package in the feed, oldest
    version first. Uses the Packaging REST API directly (via `az rest`)
    rather than an extension-specific list command, since the exact
    az-devops-extension surface for "list all versions of every package"
    has moved between versions; the REST endpoint is stable."""
    url = (f"https://feeds.dev.azure.com/{org}/{project}/_apis/packaging/"
           f"Feeds/{feed}/packages?includeAllVersions=true&api-version=7.1")
    r = subprocess.run(["az", "rest", "--method", "get", "--url", url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"az rest (list packages) failed: {r.stderr.strip()}")
    body = json.loads(r.stdout)
    out = []
    for pkg in body.get("value", []):
        versions = sorted((v["version"] for v in pkg.get("versions", [])),
                          key=_version_key)
        out.append({"name": pkg["name"], "versions": versions})
    return out


def _version_key(v: str) -> tuple:
    # "0.1.7" -> (0, 1, 7); publish always uses this scheme (see
    # push_ledger.py) so a plain numeric sort replays deltas in push order.
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return (v,)


def az_download_version(org: str, project: str, feed: str, name: str,
                        version: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["az", "artifacts", "universal", "download",
           "--organization", f"https://dev.azure.com/{org}",
           "--project", project, "--scope", "project", "--feed", feed,
           "--name", name, "--version", version, "--path", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"az download {name} v{version} failed: {r.stderr.strip()}")


def az_upload_blob(account: str, file_path: Path, blob_name: str = "index.html") -> None:
    cmd = ["az", "storage", "blob", "upload",
           "--account-name", account, "--container-name", "$web",
           "--name", blob_name, "--file", str(file_path),
           "--auth-mode", "login", "--overwrite"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"blob upload failed: {r.stderr.strip()}")


# ------------------------------------------------------------- pure merge
def merge_versions(downloaded_root: Path, merged_dir: Path) -> None:
    """downloaded_root/<package>/<version>/<original filenames...> -> one
    file per original filename in merged_dir, concatenated in ascending
    version order. Exact-duplicate lines are dropped (belt-and-suspenders
    for a push that was re-sent after a failed prior attempt whose local
    state didn't advance — see push_ledger.py's failure path)."""
    merged_dir.mkdir(parents=True, exist_ok=True)
    for pkg_dir in sorted(downloaded_root.iterdir()):
        if not pkg_dir.is_dir() or not pkg_dir.name.startswith(PACKAGE_PREFIX):
            continue
        if pkg_dir.name == PRICES_PACKAGE:
            continue  # handled separately by merge_prices()
        version_dirs = sorted((p for p in pkg_dir.iterdir() if p.is_dir()),
                              key=lambda p: _version_key(p.name))
        seen_by_file: dict[str, set] = {}
        for vdir in version_dirs:
            for f in sorted(vdir.glob("*.jsonl")):
                out = merged_dir / f.name
                seen = seen_by_file.setdefault(f.name, set())
                new_lines = [ln for ln in f.read_text().splitlines()
                            if ln.strip() and ln not in seen]
                seen.update(new_lines)
                if new_lines:
                    with open(out, "a") as fh:
                        fh.write("\n".join(new_lines) + "\n")


def merge_prices(downloaded_root: Path, merged_dir: Path) -> None:
    """Latest ledger-prices version -> merged_dir/prices.jsonl, if present."""
    pkg_dir = downloaded_root / PRICES_PACKAGE
    if not pkg_dir.is_dir():
        return
    version_dirs = sorted((p for p in pkg_dir.iterdir() if p.is_dir()),
                          key=lambda p: _version_key(p.name))
    if not version_dirs:
        return
    src = version_dirs[-1] / "prices.jsonl"
    if src.is_file():
        (merged_dir / "prices.jsonl").write_text(src.read_text())


def render(merged_dir: Path, out_html: Path) -> None:
    os.environ["LLM_TOKEN_LEDGER_DIR"] = str(merged_dir)
    import llm_usage_ledger as lp
    lp.html_report(out_html)


# ------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", default=os.environ.get("DEVOPS_ORG"))
    ap.add_argument("--project", default=os.environ.get("DEVOPS_PROJECT"))
    ap.add_argument("--feed", default=os.environ.get("DEVOPS_FEED"))
    ap.add_argument("--storage-account", default=os.environ.get("STORAGE_ACCOUNT"))
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--local-only", action="store_true",
                     help="skip every az call; render from --downloaded-root "
                          "as-is (for tests, or previewing before the feed "
                          "exists). Never uploads.")
    ap.add_argument("--downloaded-root", type=Path,
                     help="with --local-only: a pre-populated "
                          "<package>/<version>/*.jsonl tree to render from")
    ap.add_argument("--no-upload", action="store_true",
                     help="render but skip the blob upload step")
    a = ap.parse_args(argv)

    with tempfile.TemporaryDirectory() as td:
        merged = Path(td) / "merged"
        if a.local_only:
            if not a.downloaded_root:
                sys.exit("--local-only needs --downloaded-root")
            root = a.downloaded_root
        else:
            missing = [k for k in ("org", "project", "feed")
                      if not getattr(a, k)]
            if missing:
                sys.exit(f"missing --{missing[0]} (or its DEVOPS_* env var)")
            root = Path(td) / "downloaded"
            packages = az_list_packages(a.org, a.project, a.feed)
            print(f"render_dashboard: {len(packages)} package(s) on the feed")
            for pkg in packages:
                for version in pkg["versions"]:
                    dest = root / pkg["name"] / version
                    az_download_version(a.org, a.project, a.feed,
                                        pkg["name"], version, dest)

        merge_versions(root, merged)
        merge_prices(root, merged)
        render(merged, a.out)
    print(f"render_dashboard: wrote {a.out}")

    if not a.local_only and not a.no_upload:
        if not a.storage_account:
            sys.exit("missing --storage-account (or STORAGE_ACCOUNT) for upload; "
                      "pass --no-upload to render without publishing")
        az_upload_blob(a.storage_account, a.out)
        print(f"render_dashboard: uploaded to {a.storage_account}/$web/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
