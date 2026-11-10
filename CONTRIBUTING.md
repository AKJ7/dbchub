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

## Review

A maintainer checks that the contribution is permitted, valid, and correctly placed,
then merges. Thank you!
