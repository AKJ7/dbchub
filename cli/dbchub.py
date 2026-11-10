#!/usr/bin/env python3
"""dbchub — turn an upload/update into a GitHub Pull Request.

This is the "write API" client. Because the repo is the database and
contributions are reviewed, a write is three GitHub REST calls:

    1. create a branch off the base branch
    2. commit the file onto that branch   (PUT .../contents/<path>)
    3. open a pull request

Nothing here needs a server — it talks straight to api.github.com.

Usage:
    export GITHUB_TOKEN=ghp_...        # 'repo' (or 'public_repo' on a fork) scope
    python cli/dbchub.py upload engine.dbc \
        --repo youruser/dbchub --project powertrain --bus can

    python cli/dbchub.py update databases/powertrain/can/engine.dbc \
        --repo youruser/dbchub

For public contributions, point --repo at your own fork; the PR is opened from
the fork against the upstream default branch.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"


def api(method: str, path: str, token: str, body: dict | None = None):
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "dbchub-cli")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        detail = json.loads(raw) if raw else {}
        return exc.code, detail


def get_file_sha(repo, path, branch, token):
    """Return the blob sha of an existing file, or None if it does not exist."""
    status, data = api("GET", f"/repos/{repo}/contents/{path}?ref={branch}", token)
    if status == 200 and isinstance(data, dict):
        return data.get("sha")
    return None


def cmd_write(args, is_update: bool):
    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("error: set $GITHUB_TOKEN or pass --token")

    local = Path(args.file)
    if not local.is_file():
        sys.exit(f"error: no such file: {local}")

    # Target path inside the repo.
    if args.path:
        repo_path = args.path
    elif is_update:
        repo_path = str(local).replace("\\", "/")
    else:
        if not (args.project and args.bus):
            sys.exit("error: upload needs --project and --bus (or an explicit --path)")
        repo_path = f"databases/{args.project}/{args.bus}/{local.name}"

    # 1. resolve base branch head
    status, ref = api("GET", f"/repos/{args.repo}/git/ref/heads/{args.base}", token)
    if status != 200:
        sys.exit(f"error: cannot read base branch '{args.base}': {ref}")
    base_sha = ref["object"]["sha"]

    # 2. create working branch
    branch = args.branch or f"{'update' if is_update else 'add'}-{local.stem}-{int(time.time())}"
    status, res = api(
        "POST",
        f"/repos/{args.repo}/git/refs",
        token,
        {"ref": f"refs/heads/{branch}", "sha": base_sha},
    )
    if status not in (200, 201):
        sys.exit(f"error: cannot create branch '{branch}': {res}")

    # 3. commit the file onto the branch
    content_b64 = base64.b64encode(local.read_bytes()).decode()
    body = {
        "message": args.message or f"{'Update' if is_update else 'Add'} {repo_path}",
        "content": content_b64,
        "branch": branch,
    }
    existing = get_file_sha(args.repo, repo_path, branch, token)
    if existing:
        body["sha"] = existing
    status, res = api("PUT", f"/repos/{args.repo}/contents/{repo_path}", token, body)
    if status not in (200, 201):
        sys.exit(f"error: cannot commit file: {res}")

    # 4. open the pull request
    head = f"{args.repo.split('/')[0]}:{branch}" if args.upstream else branch
    pr_repo = args.upstream or args.repo
    status, pr = api(
        "POST",
        f"/repos/{pr_repo}/pulls",
        token,
        {
            "title": body["message"],
            "head": head,
            "base": args.base,
            "body": args.pr_body
            or f"Automated {'update' if is_update else 'upload'} of `{repo_path}` via dbchub CLI.",
        },
    )
    if status not in (200, 201):
        sys.exit(f"error: cannot open pull request: {pr}")

    print(f"✅ Pull request opened: {pr['html_url']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="dbchub write client (upload/update -> PR).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("upload", "update"):
        p = sub.add_parser(name, help=f"{name} a database file as a pull request")
        p.add_argument("file", help="Local database file.")
        p.add_argument("--repo", required=True, help="owner/repo you can push to (your fork for public contrib).")
        p.add_argument("--upstream", help="owner/repo to open the PR against (default: --repo).")
        p.add_argument("--base", default="main", help="Base branch (default: main).")
        p.add_argument("--project", help="Project folder (upload only).")
        p.add_argument("--bus", help="Bus folder, e.g. can/lin (upload only).")
        p.add_argument("--path", help="Explicit repo path (overrides project/bus).")
        p.add_argument("--branch", help="Branch name (default: auto).")
        p.add_argument("--message", help="Commit message.")
        p.add_argument("--pr-body", help="Pull-request body.")
        p.add_argument("--token", help="GitHub token (default: $GITHUB_TOKEN).")

    args = ap.parse_args()
    cmd_write(args, is_update=(args.cmd == "update"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
