#!/usr/bin/env python3
"""Build the static catalog and assemble the publishable site.

Walks `databases/`, validates each file, emits:
  * <out>/index.json                       — the catalog (all databases)
  * <out>/databases/<path>.json            — per-database metadata
  * <out>/databases/<path>.<ext>           — a copy of the file itself
  * <out>/<site files>                     — the static browse UI

The result in <out> (default `_site`) is what GitHub Pages deploys.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

from validate import SUPPORTED_EXTS, validate_file

REPO = Path(__file__).resolve().parent.parent
DB_ROOT = REPO / "databases"
SITE = REPO / "site"


def git_last_commit(path: Path):
    """Return (short_sha, YYYY-MM-DD) of the last commit touching `path`."""
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%h|%cs", "--", str(path)],
            cwd=REPO,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            sha, cdate = out.split("|", 1)
            return sha, cdate
    except Exception:
        pass
    return None, None


def build(out: Path, base_url: str) -> int:
    cat = out / "databases"
    cat.mkdir(parents=True, exist_ok=True)
    index = []

    for f in sorted(DB_ROOT.rglob("*")):
        if not (f.is_file() and f.suffix.lower() in SUPPORTED_EXTS):
            continue
        rel = f.relative_to(DB_ROOT)
        parts = rel.parts
        project = parts[0] if len(parts) > 1 else "uncategorized"
        bus = parts[1] if len(parts) > 2 else ""

        res = validate_file(f)
        sha, cdate = git_last_commit(f)
        record = {
            **res.metadata,
            "project": project,
            "bus": bus,
            "path": rel.as_posix(),
            "valid": res.ok,
            "warnings": res.warnings,
            "errors": res.errors,
            "commit": sha,
            "updated": cdate,
            "download": f"{base_url.rstrip('/')}/databases/{rel.as_posix()}",
            "metadata_url": f"{base_url.rstrip('/')}/databases/{rel.with_suffix('.json').as_posix()}",
        }
        index.append(record)

        dest = cat / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        dest.with_suffix(".json").write_text(json.dumps(record, indent=2))

    (out / "index.json").write_text(
        json.dumps(
            {
                "generated": date.today().isoformat(),
                "count": len(index),
                "databases": index,
            },
            indent=2,
        )
    )

    if SITE.exists():
        for item in SITE.iterdir():
            target = out / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)

    print(f"Built catalog with {len(index)} database(s) -> {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the dbchub catalog + site.")
    ap.add_argument("--out", default="_site", help="Output directory.")
    ap.add_argument(
        "--base-url",
        default="",
        help="Public base URL (e.g. https://user.github.io/dbchub) for download links.",
    )
    args = ap.parse_args()
    return build(Path(args.out), args.base_url)


if __name__ == "__main__":
    raise SystemExit(main())
