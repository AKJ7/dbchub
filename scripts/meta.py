#!/usr/bin/env python3
"""Optional descriptive metadata for manufacturers and models.

Folder names under databases/ are lowercase slugs (`hyundai/2015`, `delphi/esr`)
and say little on their own. Two optional YAML files add display names and context
without renaming anything:

  databases/<manufacturer>/manufacturer.yml
      name:        "BMW"                     # display name (required)
      kind:        vehicle-manufacturer      # see KINDS below (default vehicle-manufacturer)
      aliases:     ["Bayerische Motoren Werke"]
      description: "..."

  databases/<manufacturer>/<model>/model.yml
      name:        "E90 3 Series"            # display name (required)
      years:       "2005–2013"               # free text, e.g. "2018+"
      platform:    "E9x"
      aliases:     ["3er", "E91", "E92"]
      description: "..."
      buses:                                 # keyed by the <bus> folder name
        can:
          name:     "K-CAN"
          bitrate:  100000                   # bit/s
          description: "Body CAN"

Both files are validated in CI (unknown keys and wrong types are errors) and their
contents are merged into the catalog by build_index.py.
"""
from __future__ import annotations

from pathlib import Path

MANUFACTURER_FILE = "manufacturer.yml"
MODEL_FILE = "model.yml"

KINDS = {"vehicle-manufacturer", "supplier", "platform", "motorsport", "research", "aftermarket"}

_STR = (str,)
MANUFACTURER_SCHEMA = {"name": _STR, "kind": _STR, "aliases": list, "description": _STR}
MODEL_SCHEMA = {"name": _STR, "years": _STR, "platform": _STR, "aliases": list, "description": _STR, "buses": dict}
BUS_SCHEMA = {"name": _STR, "bitrate": int, "description": _STR}


def _load_yaml(path: Path):
    import yaml

    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _check(data, schema: dict, where: str, required=("name",)) -> list[str]:
    errs = []
    if not isinstance(data, dict):
        return [f"{where}: expected a mapping at the top level"]
    for k in required:
        if not data.get(k):
            errs.append(f"{where}: '{k}' is required")
    for k, v in data.items():
        if k not in schema:
            errs.append(f"{where}: unknown key '{k}' (allowed: {', '.join(sorted(schema))})")
            continue
        typ = schema[k]
        if typ is list:
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                errs.append(f"{where}: '{k}' must be a list of strings")
        elif typ is int:
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                errs.append(f"{where}: '{k}' must be a positive integer")
        elif not isinstance(v, typ):
            errs.append(f"{where}: '{k}' must be {'text' if typ is _STR else typ.__name__}")
    return errs


def validate_meta_tree(root: Path) -> list[str]:
    """Return a list of human-readable errors for all metadata files under root."""
    errs: list[str] = []
    for f in sorted(root.rglob("*.yml")) + sorted(root.rglob("*.yaml")):
        rel = f.relative_to(root)
        where = f"databases/{rel.as_posix()}"
        if f.suffix == ".yaml" or f.name not in (MANUFACTURER_FILE, MODEL_FILE):
            errs.append(f"{where}: unexpected file; use {MANUFACTURER_FILE} or {MODEL_FILE}")
            continue
        depth = len(rel.parts)
        if f.name == MANUFACTURER_FILE and depth != 2:
            errs.append(f"{where}: {MANUFACTURER_FILE} belongs in databases/<manufacturer>/")
            continue
        if f.name == MODEL_FILE and depth != 3:
            errs.append(f"{where}: {MODEL_FILE} belongs in databases/<manufacturer>/<model>/")
            continue
        try:
            data = _load_yaml(f)
        except Exception as exc:  # noqa: BLE001
            errs.append(f"{where}: invalid YAML ({exc})")
            continue
        if f.name == MANUFACTURER_FILE:
            errs += _check(data, MANUFACTURER_SCHEMA, where)
            if isinstance(data, dict) and data.get("kind") and data["kind"] not in KINDS:
                errs.append(f"{where}: kind must be one of {', '.join(sorted(KINDS))}")
        else:
            errs += _check(data, MODEL_SCHEMA, where)
            buses = data.get("buses") if isinstance(data, dict) else None
            if isinstance(buses, dict):
                for bus, info in buses.items():
                    errs += _check(info, BUS_SCHEMA, f"{where} (buses.{bus})", required=())
                    if not (f.parent / str(bus)).is_dir():
                        errs.append(f"{where}: buses.{bus} has no matching folder {f.parent.name}/{bus}/")
    return errs


# Licenses accepted for imported databases (see ACCEPTABLE_USE.md).
ALLOWED_IMPORT_LICENSES = {
    "MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC",
    "CC-BY-3.0", "CC-BY-4.0", "CC0-1.0", "Unlicense",
}


def validate_sources(root: Path) -> list[str]:
    """Check databases/SOURCES.json against the import policy."""
    import json

    manifest = root / "SOURCES.json"
    if not manifest.exists():
        return []
    where = f"databases/{manifest.name}"
    try:
        data = json.loads(manifest.read_text())
    except ValueError as exc:
        return [f"{where}: invalid JSON ({exc})"]
    errs, seen = [], {}
    repo = root.parent
    for s in data.get("sources", []):
        name = s.get("name", "?")
        for k in ("name", "url", "commit", "license", "license_file", "attribution"):
            if not s.get(k):
                errs.append(f"{where}: source '{name}' is missing '{k}'")
        lic = s.get("license")
        if lic and lic not in ALLOWED_IMPORT_LICENSES:
            errs.append(f"{where}: source '{name}' has license '{lic}', which ACCEPTABLE_USE.md does not accept for imports")
        if s.get("license_file") and not (repo / s["license_file"]).is_file():
            errs.append(f"{where}: source '{name}': license_file {s['license_file']} does not exist")
        for n in s.get("notices", []):
            if not (repo / n).is_file():
                errs.append(f"{where}: source '{name}': notice {n} does not exist")
        for f in s.get("files", []):
            p = f.get("path", "")
            if not p or ".." in Path(p).parts or not (root / p).is_file():
                errs.append(f"{where}: source '{name}' lists {p!r}, which is not a file under databases/")
            if f.get("method", "copy") not in ("copy", "opendbc-generator"):
                errs.append(f"{where}: source '{name}': unknown method {f.get('method')!r} for {p}")
            if p in seen:
                errs.append(f"{where}: {p} is claimed by both '{seen[p]}' and '{name}'")
            seen[p] = name
    return errs


def load_meta(root: Path) -> tuple[dict, dict]:
    """Return ({manufacturer: data}, {(manufacturer, model): data}); invalid files are skipped."""
    mfrs, models = {}, {}
    for f in root.glob(f"*/{MANUFACTURER_FILE}"):
        try:
            data = _load_yaml(f)
        except Exception:  # noqa: BLE001 - validated separately
            continue
        if isinstance(data, dict) and not _check(data, MANUFACTURER_SCHEMA, ""):
            mfrs[f.parent.name] = data
    for f in root.glob(f"*/*/{MODEL_FILE}"):
        try:
            data = _load_yaml(f)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and not _check(data, MODEL_SCHEMA, ""):
            models[(f.parent.parent.name, f.parent.name)] = data
    return mfrs, models
