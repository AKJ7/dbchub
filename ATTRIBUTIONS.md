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

Imported files (each from `opendbc/dbc/<file>` upstream):

| Path |
|---|
| `databases/acura/ilx-2016/can/acura_ilx_2016_nidec.dbc` |
| `databases/bmw/e8x-e9x/can/bmw_e9x_e8x.dbc` |
| `databases/byd/atto3/can/byd_atto3.dbc` |
| `databases/cadillac/ct6/can/cadillac_ct6_powertrain.dbc` |
| `databases/chrysler/cusw/can/chrysler_cusw.dbc` |
| `databases/fca/giorgio/can/fca_giorgio.dbc` |
| `databases/ford/fusion-2018/can/ford_fusion_2018_pt.dbc` |
| `databases/gm/global-a/can/gm_global_a_chassis.dbc` |
| `databases/hongqi/hs5/can/hongqi_hs5.dbc` |
| `databases/hyundai/i30-2014/can/hyundai_i30_2014.dbc` |
| `databases/mazda/mazda3-2019/can/mazda_3_2019.dbc` |
| `databases/mercedes-benz/e350-2010/can/mercedes_benz_e350_2010.dbc` |
| `databases/nissan/xterra-2011/can/nissan_xterra_2011.dbc` |
| `databases/rivian/r1/can/rivian_primary_actuator.dbc` |
| `databases/tesla/model3/can/tesla_model3_vehicle.dbc` |
| `databases/toyota/prius-2010/can/toyota_prius_2010_pt.dbc` |
| `databases/volkswagen/mqb/can/vw_mqb.dbc` |
| `databases/volvo/v60-2015/can/volvo_v60_2015_pt.dbc` |

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
