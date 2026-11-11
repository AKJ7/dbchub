# Acceptable Use & Content Policy

This policy governs what may be uploaded to and distributed through dbchub. It exists
primarily because automotive signal databases are often proprietary. Maintainers may
reject, edit, or remove any contribution that violates it, with or without notice.

## What may be added

There are two routes into the catalog.

**1. Contributions.** You may upload a database if

- you created it yourself, **or**
- you have explicit permission from the rights holder to publish and license it,

**and** you license it under CC BY 4.0 (see [`LICENSE-DATA`](LICENSE-DATA)). This is
what the upload dialog and `dbchub upload` ask you to confirm.

**2. Imports of already-open databases.** A database published by someone else may
be added *under its own license* (it is not relicensed) if that license is one of:

| Accepted | SPDX ids |
|---|---|
| Permissive software licenses | `MIT`, `BSD-2-Clause`, `BSD-3-Clause`, `Apache-2.0`, `ISC` |
| Creative Commons, attribution only | `CC-BY-4.0` (and `CC-BY-3.0`) |
| Public-domain dedications | `CC0-1.0`, `Unlicense` |

Imports must be listed in [`databases/SOURCES.json`](databases/SOURCES.json) with the
upstream URL, pinned commit, license and attribution, and the full license text must
be added under `THIRD_PARTY_LICENSES/` (see "Importing" in
[`CONTRIBUTING.md`](CONTRIBUTING.md)).

**Not accepted (for now):**

- *Share-alike / copyleft* licenses (`CC-BY-SA-*`, `GPL-*`, `LGPL-*`, `AGPL-*`):
  the site publishes converted versions of every database (DBC/KCD/SYM/CSV/ARXML/C),
  and share-alike terms would bind each of those derivatives and anyone combining them.
- *Non-commercial* (`CC-BY-NC-*`) and *no-derivatives* (`CC-BY-ND-*`) licenses: they
  conflict with free reuse and with the format conversions.
- Repositories **without any license**, or files offered as a "free download" without
  license terms: being publicly visible is not permission to redistribute.

Maintainers may revisit the share-alike rule; until then such sources are linked to,
not copied.

## Prohibited content

Do **not** upload:

- **Proprietary or confidential** OEM/supplier databases, or anything obtained under
  an NDA or marked confidential/internal;
- Files **reverse-engineered or extracted** from a product in violation of its license,
  terms, or applicable law;
- Material that **infringes** any copyright, trade secret, patent, or other right;
- **Personal data** (e.g. VINs tied to individuals, driver data, telematics logs with
  personal information), credentials, API keys, or secrets;
- Malware, or files crafted to exploit parsers or downstream tools;
- Trademarks, logos, or branding used in a way that implies endorsement or affiliation
  (using a manufacturer's name purely to describe which vehicle a database applies to
  is fine; impersonation is not);
- Anything unlawful under applicable law, including export-controlled technical data you
  are not permitted to share.

## Safety

Databases here are community-contributed and **not validated for production or
safety-critical use**. See [`DISCLAIMER.md`](DISCLAIMER.md).

## Enforcement

Rights holders and others can report violations via [`TAKEDOWN.md`](TAKEDOWN.md).
Repeated or deliberate violations may result in contributions being blocked.
