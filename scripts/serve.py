#!/usr/bin/env python3
"""Serve the dbchub site locally.

One command: (re)generate the catalog data, then run Jekyll's dev server with
live rebuilds. Jekyll needs Ruby + Bundler — the gems are declared in site/Gemfile.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

try:  # run as a file (python scripts/serve.py)
    from build_index import build
except ImportError:  # run as an installed entry point (uv run dbchub-serve)
    from scripts.build_index import build

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "site"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build the catalog and serve the site with Jekyll."
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4000)
    ap.add_argument(
        "--no-build", action="store_true", help="Skip regenerating the catalog data."
    )
    args = ap.parse_args()

    base_url = f"http://{args.host}:{args.port}"

    if not args.no_build:
        build(SITE, base_url)

    if not shutil.which("bundle"):
        sys.exit(
            "error: `bundle` (Ruby Bundler) not found — needed to run Jekyll.\n"
            "Install Ruby + Bundler, then re-run. See "
            "https://jekyllrb.com/docs/installation/"
        )

    if not (SITE / "Gemfile.lock").exists():
        print("Installing Jekyll gems (first run only)…")
        subprocess.run(["bundle", "install"], cwd=SITE, check=False)

    print(f"\nServing dbchub on {base_url}  (Ctrl-C to stop)\n")
    try:
        return subprocess.run(
            ["bundle", "exec", "jekyll", "serve",
             "--host", args.host, "--port", str(args.port)],
            cwd=SITE,
        ).returncode
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
