# Contributing to dbchub

Thanks for helping build an open catalog of automotive signal databases.
Contributions come in as **Pull Requests** and are reviewed before they are merged.

## ⚠️ Only contribute databases you have the right to share

Automotive signal databases are frequently **proprietary or confidential** (OEM or
supplier intellectual property, often covered by NDAs). **Do not upload files you do
not have the right to publish.** Acceptable sources include:

- databases you authored yourself;
- databases a vendor or standard explicitly permits you to redistribute;
- databases already published under an open license.

See [`ACCEPTABLE_USE.md`](ACCEPTABLE_USE.md) for what is and isn't allowed. If in
doubt, don't upload it. Prohibited content is rejected and removed
(see [`TAKEDOWN.md`](TAKEDOWN.md)).

## Licensing of your contribution

- **Database content** you add (files under `databases/`) is licensed under
  **CC BY 4.0** — see [`LICENSE-DATA`](LICENSE-DATA).
- **Code** you add (scripts, site, CLI) is licensed under **Apache-2.0** — see
  [`LICENSE`](LICENSE).

By contributing, you agree your contribution is provided under these licenses and
that you have the right to do so.

## Importing databases someone else published

Files that are already openly licensed elsewhere are *imported* rather than
contributed: they keep their own license. Only the licenses listed in
[`ACCEPTABLE_USE.md`](ACCEPTABLE_USE.md#what-may-be-added) are accepted (permissive
or CC BY / CC0; no share-alike, non-commercial or no-derivatives terms, and never
unlicensed files). To import:

1. Add a source to [`databases/SOURCES.json`](databases/SOURCES.json): `name`, `url`,
   pinned `commit`, SPDX `license`, `license_file`, `license_upstream`, `attribution`,
   and one `files` entry per file (`path` under `databases/`, `upstream` path; set
   `method: opendbc-generator` for opendbc templates).
2. Put the license text at `license_file` (under `THIRD_PARTY_LICENSES/`).
3. Run `uv run python scripts/sources.py sync --source "<name>"` to fetch the files
   and `uv run python scripts/sources.py attributions` to update `ATTRIBUTIONS.md`.

`scripts/sources.py check` reports sources whose upstream has moved, and a weekly
workflow (`sync-sources.yml`) opens a pull request when it has. The tool only copies
files; it never runs code from the upstream repository.

## Sign off your commits (DCO)

We use the [Developer Certificate of Origin](DCO). Every commit must carry a
`Signed-off-by` line asserting you have the right to submit it:

```bash
git commit -s -m "Add examplecorp/sedan/can engine.dbc"
```

This appends `Signed-off-by: Your Name <you@example.com>`. Use your real name and a
reachable email. PRs with unsigned commits will be asked to amend.

## How to contribute

**From the website:** use the **Upload** dialog on the Explore page. It opens a PR
from your fork using a GitHub token you provide in your browser.

**From the CLI / git:**

```bash
# fork the repo, then:
uv run dbchub validate <your-file>          # optional: check locally
uv run dbchub upload <your-file> \
    --repo you/dbchub --upstream <org>/dbchub \
    --manufacturer examplecorp --model sedan --bus can
```

Or plain git: `fork → branch → add file under databases/<manufacturer>/<model>/<bus>/ → PR`.

## File requirements

- Place files at `databases/<manufacturer>/<model>/<bus>/<name>.<ext>`.
- Use lowercase, descriptive folder names (manufacturer = the vehicle maker the
  database describes; this is descriptive use, not a claim of endorsement).
- Supported formats: `.dbc .arxml .ldf .dbf .kcd .sym`.
- CI validates every file (parse + lint). Fix reported errors before merge.
- No real personal data, credentials, or confidential markings in the files.

### Describing manufacturers and models (optional)

Folder names are short slugs. To give them display names and context, add
`databases/<manufacturer>/manufacturer.yml` and/or
`databases/<manufacturer>/<model>/model.yml`:

```yaml
# databases/volkswagen/mqb/model.yml
name: "MQB platform"
years: "2012+"                 # free text
platform: "MQB"
aliases: ["Golf Mk7", "Passat B8"]   # also used by search
description: "Volkswagen Group transverse-engine platform."
buses:                         # keyed by the <bus> folder
  can:
    name: "Powertrain CAN"
    bitrate: 500000            # bit/s
```

`manufacturer.yml` takes `name`, `kind` (`vehicle-manufacturer`, `supplier`,
`platform`, `motorsport`, `research`, `aftermarket`), `aliases` and `description`.
CI rejects unknown keys and wrong types (see `scripts/meta.py`).

## Review

A maintainer checks that the contribution is permitted, valid, and correctly placed,
then merges. Thank you!
