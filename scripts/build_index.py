#!/usr/bin/env python3
"""Build the catalog data consumed by the Jekyll site.

Walks `databases/`, validates each file, and emits into the Jekyll source dir:
  * <site>/_data/catalog.json            — data file Liquid templates iterate over
  * <site>/index.json                    — same catalog, served as a machine-readable
                                           read-API endpoint (GET /index.json)
  * <site>/browse.json                   — slim catalog for the browse page
  * <site>/search-index.json             — every message/signal of every database,
                                           for the cross-database search page
  * <site>/_data/recent.json             — home page: recent activity + counts
  * <site>/databases/<path>              — a copy of each file (served for download)
  * <site>/databases/<name>.json         — per-database metadata incl. all lint findings
  * <site>/databases/<name>.structure.json — nodes / messages / signals
  * <site>/databases/<name>.history.json   — committed versions + what each changed
  * <site>/databases/<name>.export/…     — the database converted to other formats

Databases are organised by car manufacturer:
    databases/<manufacturer>/<model>/<bus>/<name>.<ext>
with optional databases/<manufacturer>/manufacturer.yml and
databases/<manufacturer>/<model>/model.yml (see scripts/meta.py).

Jekyll then builds <site> into the published site (see .github/workflows/publish.yml).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

try:  # run as a file (python scripts/build_index.py)
    from diffdb import file_history
    from exports import export_all
    from meta import load_meta
    from validate import SUPPORTED_EXTS, validate_file
except ImportError:  # run as an installed entry point (uv run dbchub-build)
    from scripts.diffdb import file_history
    from scripts.exports import export_all
    from scripts.meta import load_meta
    from scripts.validate import SUPPORTED_EXTS, validate_file

REPO = Path(__file__).resolve().parent.parent
DB_ROOT = REPO / "databases"
SOURCES_FILE = DB_ROOT / "SOURCES.json"
HISTORY_LIMIT = 20  # versions per file in <name>.history.json
RECENT_LIMIT = 6


def load_sources():
    """Return (prefix, source) pairs from databases/SOURCES.json, longest prefix
    first, or an empty list if absent. Each entry of a source's `files` is an exact
    path; legacy `prefix` (string) / `prefixes` (list) are still honoured."""
    try:
        data = json.loads(SOURCES_FILE.read_text())
    except FileNotFoundError:
        return []
    except Exception as exc:  # malformed manifest shouldn't break the build
        print(f"warning: could not read {SOURCES_FILE}: {exc}")
        return []
    pairs = []
    for s in data.get("sources", []):
        prefixes = s.get("prefixes") or ([s["prefix"]] if s.get("prefix") else [])
        prefixes = list(prefixes) + [f["path"] for f in s.get("files", []) if f.get("path")]
        for p in prefixes:
            pairs.append((p, s))
    pairs.sort(key=lambda t: len(t[0]), reverse=True)
    return pairs


def provenance_for(rel_posix: str, pairs):
    """Match a file's path against the source prefixes; default to CC-BY-4.0 original."""
    for prefix, s in pairs:
        if rel_posix.startswith(prefix):
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


def git_history():
    """Map repo-relative posix path -> [(short_sha, iso_date, subject)], newest first,
    from a single `git log` pass over databases/."""
    hist: dict[str, list] = {}
    try:
        out = subprocess.check_output(
            ["git", "log", "--no-renames", "--format=@%h|%cI|%s", "--name-only", "--", "databases"],
            cwd=REPO,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return hist
    current = None
    for line in out.splitlines():
        if line.startswith("@"):
            sha, when, subject = (line[1:].split("|", 2) + ["", ""])[:3]
            current = (sha, when, subject)
        elif line and current:
            hist.setdefault(line, []).append(current)
    return hist


def file_timestamp(path: Path) -> str:
    """Fallback for files not committed yet (local builds): the file's mtime."""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def describe(manufacturer: str, model: str, bus: str, mfrs: dict, models: dict) -> dict:
    """Display names and context from manufacturer.yml / model.yml (all optional)."""
    mi = mfrs.get(manufacturer, {})
    mo = models.get((manufacturer, model), {})
    bus_info = (mo.get("buses") or {}).get(bus) or None
    return {
        "manufacturer_name": mi.get("name") or manufacturer,
        "model_name": mo.get("name") or model,
        "manufacturer_info": {k: mi[k] for k in ("kind", "aliases", "description") if mi.get(k)} or None,
        "model_info": {k: mo[k] for k in ("years", "platform", "aliases", "description") if mo.get(k)} or None,
        "bus_info": bus_info,
    }


# Fields kept in the slim per-database records used by the home and browse pages.
SLIM_FIELDS = (
    "name", "format", "manufacturer", "model", "bus", "manufacturer_name", "model_name",
    "license", "source", "path", "valid", "counts", "size_bytes",
    "updated", "updated_at", "added", "added_at", "download", "metadata_url",
)


def slim(record: dict) -> dict:
    s = {k: record.get(k) for k in SLIM_FIELDS}
    lint = record.get("lint") or {}
    s["lint"] = {k: lint.get(k, 0) for k in ("error", "warning", "info")}
    mi, mo = record.get("model_info") or {}, record.get("manufacturer_info") or {}
    s["years"] = mi.get("years")
    s["aliases"] = (mo.get("aliases") or []) + (mi.get("aliases") or []) + ([mi["platform"]] if mi.get("platform") else [])
    return s


def build(out: Path, base_url: str) -> int:
    data_dir = out / "_data"
    files_dir = out / "databases"
    data_dir.mkdir(parents=True, exist_ok=True)
    if files_dir.exists():  # stale exports/history from removed files would linger
        shutil.rmtree(files_dir)
    files_dir.mkdir(parents=True)
    base = base_url.rstrip("/")

    sources = load_sources()
    history = git_history()
    mfrs, models = load_meta(DB_ROOT)
    index = []
    search = {"databases": [], "messages": [], "signals": []}

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
        repo_rel = f.relative_to(REPO).as_posix()
        commits = history.get(repo_rel, [])
        if commits:
            sha, updated_at = commits[0][0], commits[0][1]
            added_at = commits[-1][1]
        else:  # not committed yet
            sha, updated_at = None, file_timestamp(f)
            added_at = updated_at

        dest = files_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        url = lambda p: f"{base}/databases/{p}"  # noqa: E731

        exports = []
        if res.matrices is not None:
            export_dir = dest.parent / f"{f.stem}.export"
            for e in export_all(res.matrices, f.suffix.lower(), export_dir, f.stem):
                p = (rel.parent / export_dir.name / e["file"]).as_posix()
                exports.append({**e, "path": p, "url": url(p)})

        versions = file_history(repo_rel, commits, res.structure, HISTORY_LIMIT) if commits else []

        record = {
            **res.metadata,
            "manufacturer": manufacturer,
            "model": model,
            "bus": bus,
            **describe(manufacturer, model, bus, mfrs, models),
            **provenance_for(rel.as_posix(), sources),
            "path": rel.as_posix(),
            "valid": res.ok,
            "errors": res.errors,
            "warnings": res.warnings,
            "commit": sha,
            "updated": updated_at[:10],
            "updated_at": updated_at,
            "added": added_at[:10],
            "added_at": added_at,
            "versions": len([v for v in versions if v["commit"]]),
            "download": url(rel.as_posix()),
            "metadata_url": url(rel.with_suffix(".json").as_posix()),
            "structure_url": url(rel.with_suffix(".structure.json").as_posix()) if res.structure is not None else None,
            "history_url": url(rel.with_suffix(".history.json").as_posix()),
            "exports": exports,
        }
        index.append(record)

        # Per-file metadata carries every finding (info level included).
        dest.with_suffix(".json").write_text(json.dumps({**record, "findings": res.findings}, indent=2))
        if res.structure is not None:  # nodes/messages/signals, loaded on demand by the UI
            dest.with_suffix(".structure.json").write_text(json.dumps(res.structure, separators=(",", ":")))
        dest.with_suffix(".history.json").write_text(json.dumps({"path": rel.as_posix(), "versions": versions},
                                                                separators=(",", ":")))

        # Cross-database search index: compact rows (see site/search.html).
        if res.structure is not None:
            db_idx = len(search["databases"])
            search["databases"].append([rel.as_posix(), f.stem, record["manufacturer_name"], record["model_name"], bus])
            for m in res.structure["messages"]:
                msg_idx = len(search["messages"])
                search["messages"].append([db_idx, m["id"], 1 if m["extended"] else 0, m["name"]])
                for s in m["signals"]:
                    search["signals"].append([msg_idx, s["name"], s["unit"] or ""])

    index.sort(key=lambda r: (r["manufacturer"].lower(), (r.get("model") or "").lower(), r["name"].lower()))
    catalog = {"generated": date.today().isoformat(), "count": len(index), "databases": index}
    payload = json.dumps(catalog, indent=2)
    (data_dir / "catalog.json").write_text(payload)  # for Liquid (site.data.catalog)
    (out / "index.json").write_text(payload)  # served read-API endpoint

    slim_all = [slim(r) for r in index]
    (out / "browse.json").write_text(json.dumps({"count": len(slim_all), "databases": slim_all}))
    (out / "search-index.json").write_text(json.dumps(search, separators=(",", ":")))

    # Home page: recent activity + per-manufacturer counts. ISO-8601 timestamps with
    # offsets don't sort as strings, so compare as datetimes.
    def newest(key):
        ranked = sorted(index, key=lambda r: (datetime.fromisoformat(r[key]), r["path"]), reverse=True)
        return [slim(r) for r in ranked[:RECENT_LIMIT]]

    mfr_counts: dict[str, dict] = {}
    for r in index:
        e = mfr_counts.setdefault(r["manufacturer"], {"name": r["manufacturer"], "display": r["manufacturer_name"], "count": 0})
        e["count"] += 1
    recent = {
        "count": len(index),
        "manufacturers": sorted(mfr_counts.values(), key=lambda e: e["display"].lower()),
        "models": len({(r["manufacturer"], r["model"]) for r in index}),
        "signals": sum((r.get("counts") or {}).get("signals") or 0 for r in index),
        "updated": newest("updated_at"),
        "added": newest("added_at"),
    }
    (data_dir / "recent.json").write_text(json.dumps(recent, indent=2))

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
