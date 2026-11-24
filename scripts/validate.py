#!/usr/bin/env python3
"""Validate and extract metadata from signal-database files.

Supports DBC / ARXML / LDF / DBF / KCD / SYM via canmatrix (multi-format), with
cantools used as a second parser to cross-check DBC/ARXML/KCD/SYM. Produces one
metadata record per file, the file's structure (nodes / messages / signals) and a
list of lint findings, each with a rule id and a level:

  error    the file is broken or misleading (e.g. nothing could be parsed);
           fails CI
  warning  probably wrong (overlapping bits, signal outside its frame, a line the
           parser had to skip)
  info     stylistic or tolerated quirks (missing unit, 29-bit IDs written
           without the extended flag, cantools' stricter parser disagreeing)

Used by:
  * CI  -> `python scripts/validate.py` (validates everything under databases/)
  * the catalog builder (`build_index.py` imports `validate_file`)

Exit code is non-zero if any file has errors, so it can gate a pull request.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Extensions we recognise. canmatrix handles all of these; LDF/DBF support
# depends on the installed canmatrix extras.
SUPPORTED_EXTS = {".dbc", ".arxml", ".ldf", ".dbf", ".kcd", ".sym"}

# Formats cantools can also parse — used as an independent second opinion.
CANTOOLS_EXTS = {".dbc", ".arxml", ".kcd", ".sym"}

LEVELS = ("error", "warning", "info")

# rule id -> (level, short human title). The UI groups findings by rule.
RULES = {
    "parse-failed": ("error", "File could not be parsed"),
    "empty-database": ("error", "No messages could be parsed"),
    "duplicate-frame-id": ("error", "Duplicate frame identifier"),
    "frame-dropped": ("warning", "Frame could not be parsed and was left out"),
    "parser-skipped-line": ("warning", "Line skipped by the parser"),
    "signal-overlap": ("warning", "Signals overlap"),
    "signal-outside-frame": ("warning", "Signal extends beyond its frame"),
    "duplicate-signal-name": ("warning", "Duplicate signal name in a frame"),
    "structure-failed": ("warning", "Structure could not be extracted"),
    "extended-id-unflagged": ("info", "29-bit IDs without the extended flag"),
    "missing-unit": ("info", "Signal has no unit"),
    "cantools-disagrees": ("info", "cantools' strict parser rejects the file"),
}

MAX_VALUE_ENTRIES = 256  # per-signal value-table entries kept in structure JSON
MAX_FINDINGS_PER_RULE = 2000  # keep per-file metadata bounded


@dataclass
class Result:
    path: str
    format: str
    ok: bool = True
    findings: list = field(default_factory=list)  # [{rule, level, message}]
    metadata: dict = field(default_factory=dict)
    structure: dict | None = None  # nodes / messages / signals, see structure()
    matrices: object = field(default=None, repr=False)  # parsed canmatrix objects (not serialized)

    def add(self, rule: str, message: str) -> None:
        level = RULES[rule][0]
        if sum(1 for f in self.findings if f["rule"] == rule) >= MAX_FINDINGS_PER_RULE:
            return
        self.findings.append({"rule": rule, "level": level, "message": message})

    @property
    def errors(self) -> list[str]:
        return [f["message"] for f in self.findings if f["level"] == "error"]

    @property
    def warnings(self) -> list[str]:
        return [f["message"] for f in self.findings if f["level"] == "warning"]

    def lint_summary(self) -> dict:
        out = {lvl: 0 for lvl in LEVELS}
        rules: dict[str, dict] = {}
        for f in self.findings:
            out[f["level"]] += 1
            r = rules.setdefault(f["rule"], {"level": f["level"], "title": RULES[f["rule"]][1], "count": 0})
            r["count"] += 1
        out["rules"] = rules
        return out


def sha256_of(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- parsing

# Statements whose first number is a frame id. Community DBCs (opendbc and others)
# often write 29-bit identifiers without the 0x80000000 "extended" flag; canmatrix
# then raises and silently drops the frame, its signals and attributes.
_ID_STATEMENT = re.compile(
    r'^(\s*(?:BO_|CM_\s+BO_|CM_\s+SG_|BA_\s+"[^"]*"\s+(?:BO_|SG_)|VAL_|SIG_VALTYPE_|BO_TX_BU_|SG_MUL_VAL_|SIG_GROUP_)\s+)(\d+)\b',
    re.MULTILINE,
)


_BO_LINE = re.compile(r"^BO_\s.*$", re.MULTILINE)
PSEUDO_FRAMES = {"VECTOR__INDEPENDENT_SIG_MSG"}  # Vector's holder for unassigned signals


def normalize_dbc(text: str) -> tuple[str, int]:
    """In-memory fixes that keep line numbers: collapse stray whitespace in BO_
    lines, and add the extended flag to 29-bit ids that lack it. Returns
    (text, number of distinct ids fixed)."""
    fixed: set[int] = set()
    text = _BO_LINE.sub(lambda m: " ".join(m.group(0).split()), text)

    def repl(m):
        n = int(m.group(2))
        if 0x7FF < n <= 0x1FFFFFFF:
            fixed.add(n)
            return m.group(1) + str(n | 0x80000000)
        return m.group(0)

    return _ID_STATEMENT.sub(repl, text), len(fixed)


def load_matrices(path: Path, res: Result | None = None):
    """Return {bus_name: CanMatrix}. Lines canmatrix skips are reported on `res`."""
    ext = path.suffix.lower()
    data = path.read_bytes()
    if ext == ".dbc":
        text, n_fixed = normalize_dbc(data.decode("iso-8859-1"))
        data = text.encode("iso-8859-1")
        if n_fixed and res is not None:
            res.add("extended-id-unflagged",
                    f"{n_fixed} frame id(s) above 0x7FF are written without the extended (0x80000000) "
                    "flag; they were read as 29-bit identifiers")
    matrices, skipped = _load(data, ext)
    lines = data.decode("iso-8859-1").splitlines()
    if ext == ".dbc":
        # canmatrix attaches the SG_ lines that follow an unparseable BO_ line to the
        # *previous* frame. Blank out such frames (line numbers kept) and reload.
        bad = [n for n in skipped if lines[n - 1].lstrip().startswith("BO_ ")]
        if bad:
            for n in bad:
                k, dropped = n, 0
                lines[n - 1] = ""
                while k < len(lines) and lines[k].lstrip().startswith("SG_ "):
                    lines[k] = ""
                    k += 1
                    dropped += 1
                if res is not None:
                    orig = path.read_text(encoding="iso-8859-1").splitlines()[n - 1].strip()[:100]
                    res.add("frame-dropped", f"Line {n}: frame could not be parsed and was left out, "
                                             f"with its {dropped} signal(s): {orig}")
            data = "\n".join(lines).encode("iso-8859-1")
            matrices, skipped = _load(data, ext)
    if res is not None and skipped:
        orig = path.read_text(encoding="iso-8859-1").splitlines()
        for n in skipped:
            snippet = orig[n - 1].strip()[:100] if 0 < n <= len(orig) else ""
            res.add("parser-skipped-line", f"Line {n} was skipped by the parser: {snippet}")
    return matrices


def _load(data: bytes, ext: str):
    """canmatrix load that captures the line numbers it silently skips."""
    import canmatrix.formats

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        matrices = canmatrix.formats.load(io.BytesIO(data), ext.lstrip("."))
    return matrices, [int(n) for n in re.findall(r"error with line no: (\d+)", out.getvalue())]


# ------------------------------------------------------------------------- structure

def _num(v):
    """canmatrix uses Decimal; emit ints where exact, floats otherwise, None if absent."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return int(f) if f.is_integer() and abs(f) < 2**53 else f


def _text(v):
    return str(v) if v not in (None, "") else None


def _signal(sig) -> dict:
    little = bool(sig.is_little_endian)
    try:  # start bit as written in a DBC (MSB for Motorola, LSB for Intel)
        start = sig.get_startbit(bit_numbering=1, start_little=little)
    except Exception:
        start = sig.start_bit
    mux = None
    if getattr(sig, "is_multiplexer", False):
        mux = "multiplexor"
    elif sig.multiplex not in (None, "Multiplexor"):
        mux = _num(sig.multiplex)
    values = {}
    for k, v in list((sig.values or {}).items())[:MAX_VALUE_ENTRIES]:
        values[str(_num(k) if _num(k) is not None else k)] = str(v)
    return {
        "name": sig.name,
        "start": _num(start),
        "length": _num(sig.size),
        "byte_order": "little_endian" if little else "big_endian",
        "signed": bool(sig.is_signed),
        "float": bool(getattr(sig, "is_float", False)),
        "factor": _num(sig.factor),
        "offset": _num(sig.offset),
        "min": _num(sig.min),
        "max": _num(sig.max),
        "unit": _text(sig.unit),
        "receivers": [r for r in (sig.receivers or []) if r],
        "multiplex": mux,
        "comment": _text(sig.comment),
        "values": values or None,
    }


def structure(matrices) -> dict:
    """Nodes, messages and signals of a parsed file, as plain JSON-able data."""
    nodes: dict[str, dict] = {}
    messages = []
    for busname, cm in matrices.items():
        if cm is None:
            continue
        for ecu in getattr(cm, "ecus", []) or []:
            if getattr(ecu, "name", None):
                nodes.setdefault(ecu.name, {"name": ecu.name, "comment": _text(getattr(ecu, "comment", None))})
        for frame in cm.frames:
            arb = getattr(frame, "arbitration_id", None)
            fid = getattr(arb, "id", None)
            senders = [t for t in (frame.transmitters or []) if t]
            for n in senders:
                nodes.setdefault(n, {"name": n, "comment": None})
            sigs = [_signal(s) for s in frame.signals]
            for s in sigs:
                for n in s["receivers"]:
                    nodes.setdefault(n, {"name": n, "comment": None})
            messages.append({
                "bus": busname or None,
                "id": fid,
                "extended": bool(getattr(arb, "extended", False)),
                "name": frame.name,
                "length": _num(frame.size),
                "fd": bool(getattr(frame, "is_fd", False)),
                "pseudo": frame.name in PSEUDO_FRAMES,
                "senders": senders,
                "cycle_ms": _num(getattr(frame, "cycle_time", None)) or None,
                "comment": _text(frame.comment),
                "signals": sorted(sigs, key=lambda s: (s["start"] if s["start"] is not None else 0, s["name"])),
            })
    messages.sort(key=lambda m: (m["id"] if m["id"] is not None else -1, m["name"]))
    return {"nodes": sorted(nodes.values(), key=lambda n: n["name"].lower()), "messages": messages}


def signal_bits(sig: dict) -> list[int]:
    """Absolute bit positions (byte*8 + bit) covered by a signal, MSB first for
    Motorola. Mirrors sigBits() in site/assets/js/app.js."""
    out, b, n = [], sig.get("start"), min(sig.get("length") or 0, 512)
    if b is None:
        return out
    for _ in range(n):
        out.append(b)
        if sig.get("byte_order") == "little_endian":
            b += 1
        else:  # Motorola sawtooth: MSB -> LSB
            b = b + 15 if b % 8 == 0 else b - 1
    return out


def _can_coexist(a, b) -> bool:
    """Two signals may share bits if they belong to different multiplexer values."""
    ma, mb = a.get("multiplex"), b.get("multiplex")
    return isinstance(ma, int) and isinstance(mb, int) and ma != mb


def lint(struct: dict, res: Result) -> None:
    seen_ids: dict[tuple, str] = {}
    for m in struct["messages"]:
        if m.get("pseudo"):
            continue  # not a real frame: its signals are unassigned, not laid out
        key = (m["bus"], m["id"], m["extended"])
        if m["id"] is not None:
            if key in seen_ids:
                res.add("duplicate-frame-id", f"Duplicate frame id {hex(m['id'])}: '{seen_ids[key]}' and '{m['name']}'")
            else:
                seen_ids[key] = m["name"]
        frame_bits = (m["length"] or 0) * 8
        names: set[str] = set()
        owners: dict[int, list[dict]] = {}
        for s in m["signals"]:
            if s["name"] in names:
                res.add("duplicate-signal-name", f"Duplicate signal name '{s['name']}' in frame '{m['name']}'")
            names.add(s["name"])
            if not s["unit"]:
                res.add("missing-unit", f"Signal '{m['name']}.{s['name']}' has no unit")
            bits = signal_bits(s)
            if frame_bits and any(b < 0 or b >= frame_bits for b in bits):
                res.add("signal-outside-frame",
                        f"Signal '{m['name']}.{s['name']}' (start {s['start']}, length {s['length']}) "
                        f"extends beyond the {m['length']}-byte frame")
            for b in bits:
                owners.setdefault(b, []).append(s)
        reported: set[tuple] = set()
        for b, sigs in owners.items():
            for i in range(len(sigs)):
                for j in range(i + 1, len(sigs)):
                    a, c = sigs[i], sigs[j]
                    pair = (a["name"], c["name"])
                    if pair in reported or _can_coexist(a, c):
                        continue
                    reported.add(pair)
                    res.add("signal-overlap", f"Signals '{m['name']}.{a['name']}' and '{c['name']}' overlap (e.g. bit {b})")


def cantools_crosscheck(path: Path, res: Result) -> None:
    """Second-opinion parse. canmatrix is the primary parser; cantools is stricter
    (e.g. it rejects overlapping/oversized signals and unflagged 29-bit ids that
    canmatrix tolerates and that appear in many real-world community DBCs). A
    disagreement is advisory."""
    try:
        import cantools

        cantools.database.load_file(str(path))
    except Exception as exc:  # noqa: BLE001 - surface any parser disagreement
        res.add("cantools-disagrees", f"cantools (strict) could not parse the file: {str(exc)[:300]}")


# ------------------------------------------------------------------------ top level

def validate_file(path: Path) -> Result:
    ext = path.suffix.lower()
    res = Result(path=str(path), format=ext.lstrip("."))
    res.metadata = {
        "name": path.stem,
        "format": res.format,
        "checksum": sha256_of(path) if path.is_file() else None,
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "counts": {"nodes": 0, "messages": 0, "signals": 0},
    }

    if ext not in SUPPORTED_EXTS:
        res.add("parse-failed", f"Unsupported extension '{ext}'")
        return _finish(res)

    try:
        matrices = load_matrices(path, res)
    except Exception as exc:  # noqa: BLE001
        res.add("parse-failed", f"Parse failed: {exc}")
        return _finish(res)

    if not matrices or all(cm is None for cm in matrices.values()):
        res.add("parse-failed", "No bus/matrix found in file")
        return _finish(res)
    res.matrices = matrices

    try:
        res.structure = structure(matrices)
    except Exception as exc:  # noqa: BLE001
        res.add("structure-failed", f"Could not extract structure: {exc}")

    if res.structure is not None:
        lint(res.structure, res)
        msgs = res.structure["messages"]
        res.metadata["counts"] = {
            "nodes": len(res.structure["nodes"]),
            "messages": len(msgs),
            "signals": sum(len(m["signals"]) for m in msgs),
        }
        if not msgs:
            res.add("empty-database", "The file parsed, but no messages could be read from it")

    if ext in CANTOOLS_EXTS:
        cantools_crosscheck(path, res)
    return _finish(res)


def _finish(res: Result) -> Result:
    order = {lvl: i for i, lvl in enumerate(LEVELS)}
    res.findings.sort(key=lambda f: order[f["level"]])  # stable within a level
    res.ok = not any(f["level"] == "error" for f in res.findings)
    res.metadata["lint"] = res.lint_summary()
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
    ap.add_argument("--verbose", "-v", action="store_true", help="Also print info-level findings.")
    args = ap.parse_args()

    results = [validate_file(p) for p in iter_targets(args.paths, args.root)]

    # Model / manufacturer metadata files are validated too (see scripts/meta.py).
    try:
        from meta import validate_meta_tree, validate_sources
    except ImportError:
        from scripts.meta import validate_meta_tree, validate_sources
    meta_errors = (validate_meta_tree(Path(args.root)) + validate_sources(Path(args.root))) if not args.paths else []

    if args.json:
        print(json.dumps([{k: v for k, v in r.__dict__.items() if k not in ("structure", "matrices")} for r in results], indent=2))

    summary_lines = [
        "# Database validation\n",
        "| File | Status | Nodes | Msgs | Sigs | Errors | Warnings | Info |",
        "|---|---|---|---|---|---|---|---|",
    ]
    failed = 0
    for r in results:
        status = "✅ ok" if r.ok else "❌ FAIL"
        failed += not r.ok
        c = r.metadata.get("counts", {})
        lint_s = r.metadata.get("lint", {})
        summary_lines.append(
            f"| `{r.path}` | {status} | {c.get('nodes', '-')} | {c.get('messages', '-')} | {c.get('signals', '-')} | "
            f"{lint_s.get('error', 0)} | {lint_s.get('warning', 0)} | {lint_s.get('info', 0)} |"
        )
        for f in r.findings:
            if f["level"] == "error":
                print(f"ERROR  {r.path}: [{f['rule']}] {f['message']}", file=sys.stderr)
            elif f["level"] == "warning" or args.verbose:
                print(f"{f['level']:<8}{r.path}: [{f['rule']}] {f['message']}")
    for e in meta_errors:
        print(f"ERROR  {e}", file=sys.stderr)
    if meta_errors:
        summary_lines.append("\n**Metadata errors:**\n" + "\n".join(f"- {e}" for e in meta_errors))

    summary = "\n".join(summary_lines) + "\n"
    print("\n" + summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as fh:
            fh.write(summary)

    if not results:
        print("No database files found.")
    return 1 if failed or meta_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
