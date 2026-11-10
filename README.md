# dbchub

A **Git-native repository for signal databases** — DBC, ARXML, LDF, DBF, KCD, SYM.
The Git repo *is* the database: every file is versioned, every change is reviewed,
and the whole thing runs on GitHub with no server to host.

## Architecture at a glance

```
 Contributor                 GitHub (store + CI)                 Consumer
 ───────────                 ───────────────────                 ────────
 dbchub upload ──PR──▶   repo ──Actions: validate/lint──▶ check   │
   (GitHub REST API)          ──Actions: build catalog───▶ Pages ◀── GET /index.json
                              (git = versioned database)          ◀── GET /…/engine.dbc
 maintainer review ──merge──▶
```

- **Storage** — files live under `databases/<project>/<bus>/<name>.<ext>`. Git gives
  history, diff, rollback and permalinks for free.
- **Read API** — a static catalog (`index.json` + per-file metadata) served by GitHub
  Pages / CDN. No backend.
- **Write API** — `upload`/`update` open a **Pull Request** via GitHub's REST API.
- **Check** — GitHub Actions parses and lints every changed file; invalid files block
  the merge.

## The API

### Read (static — served from GitHub Pages)

| Request | Returns |
|---|---|
| `GET /index.json` | the full catalog |
| `GET /databases/{project}/{bus}/{name}.json` | one database's metadata |
| `GET /databases/{project}/{bus}/{name}.{ext}` | the raw file (latest) |
| `GET /raw/{commit-sha}/databases/.../{name}.{ext}` | a pinned historical version (via `raw.githubusercontent.com`) |

Example metadata record:

```json
{
  "name": "engine", "format": "dbc", "project": "powertrain", "bus": "can",
  "checksum": "sha256:…", "counts": { "nodes": 2, "messages": 2, "signals": 5 },
  "valid": true, "commit": "a1b9f3c", "updated": "2026-11-10",
  "download": "https://you.github.io/dbchub/databases/powertrain/can/engine.dbc"
}
```

### Write (via the CLI → Pull Request)

```bash
export GITHUB_TOKEN=ghp_...        # 'public_repo' scope on your fork

# add a new database
python cli/dbchub.py upload engine.dbc \
    --repo you/dbchub --upstream org/dbchub \
    --project powertrain --bus can

# revise an existing one
python cli/dbchub.py update databases/powertrain/can/engine.dbc \
    --repo you/dbchub --upstream org/dbchub
```

Each write creates a branch, commits the file, and opens a PR — the command prints
the PR URL. Prefer plain git? `fork → branch → PR` does exactly the same thing.

### Check (automatic)

On every PR, `.github/workflows/validate.yml` runs `scripts/validate.py`:
parse validity, duplicate frame IDs, duplicate/unitless signals, bit-overflow, and a
cantools second-opinion parse for DBC/ARXML/KCD/SYM. Results appear in the PR checks.

## Repository layout

```
databases/<project>/<bus>/<name>.<ext>   # the databases themselves
scripts/validate.py                      # parse + lint + metadata (the "check")
scripts/build_index.py                   # build index.json + site (the "read API")
scripts/requirements.txt                 # canmatrix, cantools, lxml
cli/dbchub.py                            # write client (upload/update -> PR)
site/index.html                          # static browse UI
.github/workflows/validate.yml           # runs on every PR
.github/workflows/publish.yml            # builds + deploys catalog to Pages on merge
```

## Local development

```bash
pip install -r scripts/requirements.txt

# validate everything
python scripts/validate.py --root databases

# build the catalog + site into _site/, then open it
python scripts/build_index.py --out _site --base-url http://localhost:8000
python -m http.server -d _site 8000
```

## Setup checklist for a new deployment

1. Push this repo to GitHub.
2. **Settings → Pages → Source: GitHub Actions.**
3. Merge a PR (or push to `main`) to trigger the first catalog build.
4. Tell contributors to fork and use the CLI (or raw PRs).

## Notes & extensions

- **Large ARXML**: enable **Git LFS** for `*.arxml` if files get big.
- **LDF**: canmatrix's LDF support depends on extras; if a format fails to parse,
  `validate.py` reports it rather than crashing.
- **Search**: `index.json` is small enough to filter client-side (the browse UI does
  this); add a prebuilt `search.json` if the catalog grows large.
- **Direct writes / auth**: if you later want authenticated writes without review,
  that's the point to add a serverless function (Cloudflare Workers / Vercel) in front
  of the same repo.
