#!/usr/bin/env python3
"""Build the catalog data consumed by the Jekyll site.

Walks `databases/`, validates each file, and emits into the Jekyll source dir:
  * <site>/_data/catalog.json          — data file Liquid templates iterate over
  * <site>/index.json                  — same catalog, served as a machine-readable
                                         read-API endpoint (GET /index.json)
  * <site>/databases/<path>            — a copy of each file (served for download)
  * <site>/databases/<path>.json       — per-database metadata

Databases are organised by car manufacturer:
    databases/<manufacturer>/<model>/<bus>/<name>.<ext>

Jekyll then builds <site> into the published site (see .github/workflows/publish.yml).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

try:  # run as a file (python scripts/build_index.py)
    from validate import SUPPORTED_EXTS, validate_file
except ImportError:  # run as an installed entry point (uv run dbchub-build)
    from scripts.validate import SUPPORTED_EXTS, validate_file

REPO = Path(__file__).resolve().parent.parent
DB_ROOT = REPO / "databases"
SOURCES_FILE = DB_ROOT / "SOURCES.json"


def load_sources():
    """Return the provenance entries from databases/SOURCES.json (longest prefix
    wins), or an empty list if absent."""
    try:
        data = json.loads(SOURCES_FILE.read_text())
        entries = data.get("sources", [])
        return sorted(entries, key=lambda s: len(s.get("prefix", "")), reverse=True)
    except FileNotFoundError:
        return []
    except Exception as exc:  # malformed manifest shouldn't break the build
        print(f"warning: could not read {SOURCES_FILE}: {exc}")
        return []


def provenance_for(rel_posix: str, sources):
    """Match a file's path against the source prefixes; default to CC-BY-4.0 original."""
    for s in sources:
        if rel_posix.startswith(s.get("prefix", "\0")):
            return {
                "license": s.get("license", "CC-BY-4.0"),
                "source": s.get("name"),
                "source_url": s.get("url"),
                "source_commit": s.get("commit"),
                "attribution": s.get("attribution"),
            }
    return {
        "license": "CC-BY-4.0",
        "source": "Original contribution",
        "source_url": None,
        "source_commit": None,
        "attribution": None,
    }


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
    data_dir = out / "_data"
    files_dir = out / "databases"
    data_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)

    sources = load_sources()
    index = []
    for f in sorted(DB_ROOT.rglob("*")):
        if not (f.is_file() and f.suffix.lower() in SUPPORTED_EXTS):
            continue
        rel = f.relative_to(DB_ROOT)
        parts = rel.parts
        # databases/<manufacturer>/<model>/<bus>/<file>
        manufacturer = parts[0] if len(parts) > 1 else "unknown"
        model = parts[1] if len(parts) > 2 else ""
        bus = parts[2] if len(parts) > 3 else ""

        res = validate_file(f)
        sha, cdate = git_last_commit(f)
        record = {
            **res.metadata,
            "manufacturer": manufacturer,
            "model": model,
            "bus": bus,
            **provenance_for(rel.as_posix(), sources),
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

        dest = files_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        dest.with_suffix(".json").write_text(json.dumps(record, indent=2))

    index.sort(
        key=lambda r: (
            r["manufacturer"].lower(),
            (r.get("model") or "").lower(),
            r["name"].lower(),
        )
    )
    catalog = {
        "generated": date.today().isoformat(),
        "count": len(index),
        "databases": index,
    }
    payload = json.dumps(catalog, indent=2)
    (data_dir / "catalog.json").write_text(payload)  # for Liquid (site.data.catalog)
    (out / "index.json").write_text(payload)  # served read-API endpoint

    print(f"Built catalog with {len(index)} database(s) -> {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the dbchub catalog data for Jekyll.")
    ap.add_argument("--out", default="site", help="Jekyll source directory.")
    ap.add_argument(
        "--base-url",
        default="",
        help="Public base URL (e.g. https://user.github.io/dbchub) for download links.",
    )
    args = ap.parse_args()
    return build(Path(args.out), args.base_url)


if __name__ == "__main__":
    raise SystemExit(main())
