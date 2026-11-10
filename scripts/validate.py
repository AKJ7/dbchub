#!/usr/bin/env python3
"""Validate and extract metadata from signal-database files.

Supports DBC / ARXML / LDF / DBF / KCD / SYM via canmatrix (multi-format), with
cantools used as a second parser to cross-check DBC/ARXML/KCD/SYM. Produces one
metadata record per file and a set of lint findings.

Used by:
  * CI  -> `python scripts/validate.py` (validates everything under databases/)
  * the catalog builder (`build_index.py` imports `validate_file`)

Exit code is non-zero if any file has hard errors, so it can gate a pull request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Extensions we recognise. canmatrix handles all of these; LDF/DBF support
# depends on the installed canmatrix extras.
SUPPORTED_EXTS = {".dbc", ".arxml", ".ldf", ".dbf", ".kcd", ".sym"}

# Formats cantools can also parse — used as an independent second opinion.
CANTOOLS_EXTS = {".dbc", ".arxml", ".kcd", ".sym"}


@dataclass
class Result:
    path: str
    format: str
    ok: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def sha256_of(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_matrices(path: Path):
    """Return a dict {bus_name: CanMatrix} for the file."""
    import canmatrix.formats

    return canmatrix.formats.loadp(str(path))


def analyze(matrices):
    """Walk the parsed matrices, collecting counts and lint findings."""
    errors: list[str] = []
    warnings: list[str] = []
    nodes: set[str] = set()
    messages = 0
    signals = 0
    seen_ids: dict[tuple, str] = {}

    for busname, cm in matrices.items():
        if cm is None:
            continue
        for ecu in getattr(cm, "ecus", []) or []:
            if getattr(ecu, "name", None):
                nodes.add(ecu.name)

        for frame in cm.frames:
            messages += 1
            fid = getattr(getattr(frame, "arbitration_id", None), "id", None)
            if fid is not None:
                key = (busname, fid)
                if key in seen_ids:
                    errors.append(
                        f"Duplicate frame id {hex(fid)} on bus '{busname}': "
                        f"'{seen_ids[key]}' and '{frame.name}'"
                    )
                else:
                    seen_ids[key] = frame.name

            frame_bits = (getattr(frame, "size", 0) or 0) * 8
            sig_names: set[str] = set()
            for sig in frame.signals:
                signals += 1
                if sig.name in sig_names:
                    warnings.append(
                        f"Duplicate signal name '{sig.name}' in frame '{frame.name}'"
                    )
                sig_names.add(sig.name)

                if not getattr(sig, "unit", None):
                    warnings.append(f"Signal '{frame.name}.{sig.name}' has no unit")

                # Best-effort bit-overflow check (warning only — bit numbering
                # conventions vary, so we never fail CI on this alone).
                try:
                    start = sig.get_startbit(bit_numbering=1, start_little=True)
                    if frame_bits and start + sig.size > frame_bits:
                        warnings.append(
                            f"Signal '{frame.name}.{sig.name}' may exceed frame "
                            f"length ({start + sig.size} > {frame_bits} bits)"
                        )
                except Exception:
                    pass

    return nodes, messages, signals, errors, warnings


def cantools_crosscheck(path: Path, warnings: list[str]) -> None:
    """Second-opinion parse. canmatrix is the primary parser; cantools is stricter
    (e.g. it rejects overlapping/oversized signals that canmatrix tolerates and that
    appear in many real-world community DBCs). A disagreement is advisory, so it is
    reported as a warning, not a hard failure."""
    try:
        import cantools

        cantools.database.load_file(str(path))
    except Exception as exc:  # noqa: BLE001 - surface any parser disagreement
        warnings.append(f"cantools (second opinion) could not parse: {exc}")


def validate_file(path: Path) -> Result:
    ext = path.suffix.lower()
    res = Result(path=str(path), format=ext.lstrip("."))

    if ext not in SUPPORTED_EXTS:
        res.ok = False
        res.errors.append(f"Unsupported extension '{ext}'")
        return res

    try:
        matrices = load_matrices(path)
    except Exception as exc:  # noqa: BLE001
        res.ok = False
        res.errors.append(f"Parse failed: {exc}")
        return res

    if not matrices or all(cm is None for cm in matrices.values()):
        res.ok = False
        res.errors.append("No bus/matrix found in file")
        return res

    nodes, messages, signals, errors, warnings = analyze(matrices)

    if ext in CANTOOLS_EXTS:
        cantools_crosscheck(path, warnings)

    res.errors.extend(errors)
    res.warnings.extend(warnings)
    res.ok = not res.errors
    res.metadata = {
        "name": path.stem,
        "format": res.format,
        "checksum": sha256_of(path),
        "size_bytes": path.stat().st_size,
        "counts": {
            "nodes": len(nodes),
            "messages": messages,
            "signals": signals,
        },
    }
    return res


def iter_targets(paths, root):
    if paths:
        for p in paths:
            yield Path(p)
    else:
        for p in sorted(Path(root).rglob("*")):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                yield p


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate signal-database files.")
    ap.add_argument("paths", nargs="*", help="Specific files (default: scan --root).")
    ap.add_argument("--root", default="databases", help="Directory to scan.")
    ap.add_argument("--json", action="store_true", help="Emit JSON results.")
    args = ap.parse_args()

    results = [validate_file(p) for p in iter_targets(args.paths, args.root)]

    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))

    summary_lines = ["# Database validation\n", "| File | Status | Nodes | Msgs | Sigs | Warnings |", "|---|---|---|---|---|---|"]
    failed = 0
    for r in results:
        status = "✅ ok" if r.ok else "❌ FAIL"
        if not r.ok:
            failed += 1
        c = r.metadata.get("counts", {})
        summary_lines.append(
            f"| `{r.path}` | {status} | {c.get('nodes','-')} | "
            f"{c.get('messages','-')} | {c.get('signals','-')} | {len(r.warnings)} |"
        )
        for e in r.errors:
            print(f"ERROR  {r.path}: {e}", file=sys.stderr)
        for w in r.warnings:
            print(f"warn   {r.path}: {w}")

    summary = "\n".join(summary_lines) + "\n"
    print("\n" + summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as fh:
            fh.write(summary)

    if not results:
        print("No database files found.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
