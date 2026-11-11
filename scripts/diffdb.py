#!/usr/bin/env python3
"""Semantic diffs between versions of a signal database.

Two uses:
  * build_index.py records every committed version of each file with what changed
    (messages / signals added, removed, changed) -> <name>.history.json
  * CI: `python scripts/diffdb.py BASE_REF HEAD_REF` prints a Markdown report of
    the database files a pull request touches, for reviewers.

Messages are matched by (identifier, extended) and signals by name within a message.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from validate import SUPPORTED_EXTS, validate_file
except ImportError:
    from scripts.validate import SUPPORTED_EXTS, validate_file

REPO = Path(__file__).resolve().parent.parent
MESSAGE_FIELDS = ("name", "length", "senders", "cycle_ms", "extended", "fd", "comment")
SIGNAL_FIELDS = ("start", "length", "byte_order", "signed", "float", "factor", "offset",
                 "min", "max", "unit", "receivers", "multiplex", "comment", "values")
MAX_ENTRIES = 300  # cap per diff, keeps history JSON bounded


def _msg_key(m: dict):
    return (m.get("id"), m.get("extended")) if m.get("id") is not None else ("name", m.get("name"))


def _fields(old: dict, new: dict, names) -> dict:
    out = {}
    for f in names:
        a, b = old.get(f), new.get(f)
        if a != b:
            if f == "values":  # value tables can be long: summarize
                out[f] = [len(a or {}), len(b or {})]
            else:
                out[f] = [a, b]
    return out


def diff_structures(old: dict | None, new: dict | None) -> dict:
    """Diff two structure() dicts. Either may be None (file added / removed)."""
    old_msgs = {_msg_key(m): m for m in (old or {}).get("messages", [])}
    new_msgs = {_msg_key(m): m for m in (new or {}).get("messages", [])}
    summary = dict.fromkeys(("messages_added", "messages_removed", "messages_changed",
                             "signals_added", "signals_removed", "signals_changed"), 0)
    entries = []

    def entry(change, m, **extra):
        return {"change": change, "id": m.get("id"), "extended": m.get("extended"), "name": m.get("name"), **extra}

    for k in sorted(set(old_msgs) | set(new_msgs), key=lambda k: (str(k[0]), str(k[1]))):
        a, b = old_msgs.get(k), new_msgs.get(k)
        if a is None:
            summary["messages_added"] += 1
            summary["signals_added"] += len(b["signals"])
            entries.append(entry("added", b, signals=len(b["signals"])))
        elif b is None:
            summary["messages_removed"] += 1
            summary["signals_removed"] += len(a["signals"])
            entries.append(entry("removed", a, signals=len(a["signals"])))
        else:
            fields = _fields(a, b, MESSAGE_FIELDS)
            sa = {s["name"]: s for s in a["signals"]}
            sb = {s["name"]: s for s in b["signals"]}
            sig_changes = []
            for n in sorted(set(sa) | set(sb)):
                if n not in sa:
                    sig_changes.append({"change": "added", "name": n})
                elif n not in sb:
                    sig_changes.append({"change": "removed", "name": n})
                else:
                    f = _fields(sa[n], sb[n], SIGNAL_FIELDS)
                    if f:
                        sig_changes.append({"change": "changed", "name": n, "fields": f})
            if fields or sig_changes:
                summary["messages_changed"] += 1
                for c in sig_changes:
                    summary["signals_" + c["change"]] += 1
                entries.append(entry("changed", b, fields=fields, signal_changes=sig_changes))
    truncated = max(0, len(entries) - MAX_ENTRIES)
    return {"summary": summary, "messages": entries[:MAX_ENTRIES], "truncated": truncated}


def is_empty(d: dict) -> bool:
    return not any(d["summary"].values())


# ------------------------------------------------------------------- git helpers

def _git(*args) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True, stderr=subprocess.DEVNULL)


def structure_at(ref: str, path: str, cache: dict | None = None):
    """(structure, result) of `path` (repo-relative) at git `ref`, or (None, None)."""
    try:
        blob = _git("rev-parse", f"{ref}:{path}").strip()
    except subprocess.CalledProcessError:
        return None, None
    if cache is not None and blob in cache:
        return cache[blob]
    data = subprocess.check_output(["git", "cat-file", "blob", blob], cwd=REPO)
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / Path(path).name
        f.write_bytes(data)
        res = validate_file(f)
    out = (res.structure, res)
    if cache is not None:
        cache[blob] = out
    return out


def file_history(path: str, commits: list[tuple[str, str, str]], current: dict | None, limit: int = 20) -> list[dict]:
    """Versions of `path` (newest first) with the diff each introduced.

    commits: [(sha, iso_date, subject)] newest first, as collected by build_index.
    current: structure of the working-tree file (used for uncommitted changes).
    """
    cache: dict = {}
    versions = []
    commits = commits[: limit + 1]
    for i, (sha, when, subject) in enumerate(commits[:limit]):
        new, _ = structure_at(sha, path, cache)
        prev = commits[i + 1][0] if i + 1 < len(commits) else None
        old, _ = structure_at(prev, path, cache) if prev else (None, None)
        versions.append({"commit": sha, "date": when, "subject": subject, "diff": diff_structures(old, new)})
    if current is not None and commits:
        head, _ = structure_at(commits[0][0], path, cache)
        d = diff_structures(head, current)
        if not is_empty(d):
            versions.insert(0, {"commit": None, "date": None, "subject": "Uncommitted changes", "diff": d})
    return versions


# ----------------------------------------------------------------- PR reporting

def _fmt_id(e):
    if e.get("id") is None:
        return ""
    return ("0x%08X" if e.get("extended") else "0x%03X") % e["id"]


def markdown_report(base: str, head: str) -> str:
    changed = _git("diff", "--name-status", "--no-renames", f"{base}...{head}", "--", "databases").splitlines()
    files = [(line.split("\t")[0], line.split("\t")[-1]) for line in changed if line.strip()]
    files = [(s, p) for s, p in files if Path(p).suffix.lower() in SUPPORTED_EXTS]
    if not files:
        return ""
    out = ["## Database changes in this pull request", ""]
    for status, path in files:
        old, old_res = structure_at(base, path) if status != "A" else (None, None)
        new, new_res = structure_at(head, path) if status != "D" else (None, None)
        verb = {"A": "added", "D": "deleted", "M": "modified"}.get(status[0], "changed")
        out.append(f"### `{path}` ({verb})")
        if new_res is not None:
            c = new_res.metadata["counts"]
            lint = new_res.metadata["lint"]
            state = "✅ valid" if new_res.ok else "❌ invalid"
            out.append(f"{state} · {c['messages']} messages · {c['signals']} signals · {c['nodes']} nodes · "
                       f"lint: {lint['error']} errors, {lint['warning']} warnings, {lint['info']} info")
            if old_res is not None:
                ol = old_res.metadata["lint"]
                delta = lint["warning"] - ol["warning"]
                if delta:
                    out.append(f"Warnings {'+' if delta > 0 else ''}{delta} compared to the base branch.")
            for f in new_res.findings:
                if f["level"] == "error":
                    out.append(f"- ❌ `{f['rule']}`: {f['message']}")
        d = diff_structures(old, new)
        s = d["summary"]
        out.append("")
        out.append(f"| | added | removed | changed |\n|---|---|---|---|\n"
                   f"| messages | {s['messages_added']} | {s['messages_removed']} | {s['messages_changed']} |\n"
                   f"| signals | {s['signals_added']} | {s['signals_removed']} | {s['signals_changed']} |")
        if d["messages"] and status != "A":
            out.append("\n<details><summary>Details</summary>\n")
            for e in d["messages"][:100]:
                line = f"- **{e['change']}** message `{e['name']}` {_fmt_id(e)}"
                if e["change"] == "changed":
                    parts = [f"{k}: `{v[0]}` → `{v[1]}`" for k, v in e["fields"].items()]
                    parts += [f"{c['change']} signal `{c['name']}`" + (
                        " (" + ", ".join(f"{k}: `{v[0]}` → `{v[1]}`" for k, v in c["fields"].items()) + ")"
                        if c.get("fields") else "") for c in e["signal_changes"][:20]]
                    line += ": " + "; ".join(parts)
                out.append(line)
            if len(d["messages"]) > 100 or d["truncated"]:
                out.append("- …")
            out.append("\n</details>")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Report database changes between two git refs as Markdown.")
    ap.add_argument("base")
    ap.add_argument("head")
    ap.add_argument("--out", help="Write the report to this file (default: stdout).")
    args = ap.parse_args()
    report = markdown_report(args.base, args.head)
    if args.out:
        Path(args.out).write_text(report)
    else:
        print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and report:
        with open(summary, "a") as fh:
            fh.write(report + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
