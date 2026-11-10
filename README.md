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
| `GET /databases/{manufacturer}/{model}/{bus}/{name}.json` | one database's metadata |
| `GET /databases/{manufacturer}/{model}/{bus}/{name}.{ext}` | the raw file (latest) |
| `GET /raw/{commit-sha}/databases/.../{name}.{ext}` | a pinned historical version (via `raw.githubusercontent.com`) |

Example metadata record:

```json
{
  "name": "engine", "format": "dbc",
  "manufacturer": "volkswagen", "model": "golf", "bus": "can",
  "checksum": "sha256:…", "counts": { "nodes": 2, "messages": 2, "signals": 5 },
  "valid": true, "commit": "a1b9f3c", "updated": "2026-11-10",
  "download": "https://you.github.io/dbchub/databases/volkswagen/golf/can/engine.dbc"
}
```

### Write — two ways, both end in a Pull Request

**A. From the website (Docker Hub-style UI).** The Explore page has **Upload** and
**Download** built in. Download is a direct link to the file on Pages. Upload opens a
dialog where the visitor supplies their own GitHub token and fork; the browser then
calls the GitHub REST API directly (create branch → commit → open PR) — **no backend,
nothing stored server-side**. The token is used only against `api.github.com` and is
never sent anywhere else. Set the PR target in `site/_config.yml`:

```yaml
upstream_repo: "org/dbchub"   # where frontend uploads open their PR
```

**B. From the CLI** (same three API calls, scripted):

```bash
export GITHUB_TOKEN=ghp_...        # 'public_repo' scope on your fork

uv run dbchub upload engine.dbc \
    --repo you/dbchub --upstream org/dbchub \
    --manufacturer volkswagen --model golf --bus can

uv run dbchub update databases/volkswagen/golf/can/engine.dbc \
    --repo you/dbchub --upstream org/dbchub
```

Either way a branch is created, the file committed, and a PR opened. Prefer plain git?
`fork → branch → PR` does exactly the same thing.

> **Nicer "Sign in with GitHub" button?** The token-paste flow stays 100% static. If you
> want a one-click OAuth login instead, add a single serverless function (Cloudflare
> Workers / Vercel) to do the OAuth code exchange — the only server-side piece in the
> whole project. The browser upload logic in `site/assets/js/app.js` is unchanged.

### Check (automatic)

On every PR, `.github/workflows/validate.yml` runs `scripts/validate.py`:
parse validity, duplicate frame IDs, duplicate/unitless signals, bit-overflow, and a
cantools second-opinion parse for DBC/ARXML/KCD/SYM. Results appear in the PR checks.

## Repository layout

Databases are organised **by car manufacturer**:

```
databases/<manufacturer>/<model>/<bus>/<name>.<ext>   # the databases themselves
pyproject.toml                           # uv project: dependencies + script entry points
uv.lock                                  # pinned, reproducible dependency set
scripts/validate.py                      # parse + lint + metadata (the "check")
scripts/build_index.py                   # generate catalog data for Jekyll (the "read API")
scripts/serve.py                         # build + serve the site locally
cli/dbchub.py                            # write client (upload/update -> PR)
site/                                    # Jekyll site (source) — Docker Hub-style UI
  _config.yml                            #   site config (incl. upstream_repo)
  _layouts/default.html                  #   nav bar + page shell
  index.html                            #   Explore page: facets, repo cards, modals
  assets/css/style.css                   #   styles
  assets/js/app.js                       #   filtering, detail view, browser upload -> PR
  Gemfile                                #   github-pages gem, for local preview
.github/workflows/validate.yml           # runs on every PR
.github/workflows/publish.yml            # generates data + builds Jekyll + deploys on merge
```

The catalog data (`site/_data/catalog.json`, `site/index.json`) and the downloadable
file copies (`site/databases/`) are **generated** by `build_index.py` and are gitignored.

## Local development

Dependencies are managed with [uv](https://docs.astral.sh/uv/). `uv run` creates and
syncs the project virtualenv automatically — no manual install step.

```bash
# build the catalog and serve the site at http://127.0.0.1:4000
uv run dbchub-serve

# validate everything  (entry point defined in pyproject.toml)
uv run dbchub-validate --root databases
```

`dbchub-serve` regenerates the catalog data and then runs Jekyll's dev server (which
live-reloads edits to the site). It needs Ruby + Bundler for Jekyll; the first run does
`bundle install` automatically. Options: `--host`, `--port`, and `--no-build` (serve
without regenerating). Editing files under `databases/` requires a rebuild, so re-run
the command (or `uv run dbchub-build`).

The scripts are linked to the project as entry points: `dbchub-serve`, `dbchub-validate`,
`dbchub-build`, and `dbchub` (the write client). You can also run the files directly with
`uv run scripts/serve.py …`. Use `uv sync` to materialise `.venv` ahead of time and
`uv lock` to refresh the lockfile.

## Setup checklist for a new deployment

1. Push this repo to GitHub.
2. **Settings → Pages → Source: GitHub Actions.**
3. Merge a PR (or push to `master`) to trigger the first catalog build.
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
