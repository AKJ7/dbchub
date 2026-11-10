# Attributions

This project redistributes third-party signal databases under their original open-source
licenses. Machine-readable provenance (per file) lives in
[`databases/SOURCES.json`](databases/SOURCES.json) and is reflected in each database's
metadata (`license`, `source`, `source_url`, `source_commit`). Imported files keep their
upstream license — they are **not** relicensed under the project's CC BY 4.0 content
license, which applies only to original contributions.

## comma.ai — opendbc

- **Source:** https://github.com/commaai/opendbc
- **License:** MIT — full text in
  [`THIRD_PARTY_LICENSES/opendbc-LICENSE.txt`](THIRD_PARTY_LICENSES/opendbc-LICENSE.txt)
- **Copyright:** © comma.ai, Inc.
- **Imported at commit:** `27fa1c5d8eed3238a0a78c52ff5abb6cdc807bd2`

Imported files:

| Path | Upstream file |
|---|---|
| `databases/toyota/prius-2010/can/toyota_prius_2010_pt.dbc` | `opendbc/dbc/toyota_prius_2010_pt.dbc` |
| `databases/volkswagen/mqb/can/vw_mqb.dbc` | `opendbc/dbc/vw_mqb.dbc` |
| `databases/ford/fusion-2018/can/ford_fusion_2018_pt.dbc` | `opendbc/dbc/ford_fusion_2018_pt.dbc` |
| `databases/mazda/mazda3-2019/can/mazda_3_2019.dbc` | `opendbc/dbc/mazda_3_2019.dbc` |
| `databases/tesla/model3/can/tesla_model3_vehicle.dbc` | `opendbc/dbc/tesla_model3_vehicle.dbc` |
| `databases/hyundai/i30-2014/can/hyundai_i30_2014.dbc` | `opendbc/dbc/hyundai_i30_2014.dbc` |
| `databases/bmw/e8x-e9x/can/bmw_e9x_e8x.dbc` | `opendbc/dbc/bmw_e9x_e8x.dbc` |
| `databases/mercedes-benz/e350-2010/can/mercedes_benz_e350_2010.dbc` | `opendbc/dbc/mercedes_benz_e350_2010.dbc` |

> These DBCs are community-maintained and reverse-engineered; they are provided under MIT
> without warranty and are **not validated for safety-critical use** (see
> [`DISCLAIMER.md`](DISCLAIMER.md)).

## Adding more sources

To import more databases: drop the files under
`databases/<manufacturer>/<model>/<bus>/`, add (or extend) a matching entry in
`databases/SOURCES.json` with the upstream URL + license, place the license text under
`THIRD_PARTY_LICENSES/`, and list them here. Only import files whose license permits
redistribution (see [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`ACCEPTABLE_USE.md`](ACCEPTABLE_USE.md)).
