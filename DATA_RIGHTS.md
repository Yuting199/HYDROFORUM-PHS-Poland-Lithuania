# Data Source, Processing and Publication Decision

Reviewed 2026-10-23 against the official ENTSO-E documents linked below.

## Source and Processing

Source of publication: **ENTSO-E Transparency Platform**, FMS extract
`EnergyPrices_12.1.D_r3`, day-ahead bidding-zone prices for Poland
(`10YPL-AREA-----S`) and Lithuania (`10YLT-1001A0008Q`), 2021-2025.
Copyright and related/database rights, where applicable, remain with ENTSO-E
and/or the respective primary data owners. This project claims no ownership
of the underlying prices. ENTSO-E does not sponsor or endorse this study.

The study retained reported EUR/MWh and UTC interval-start timestamps, selected
the confirmed bidding-zone/day-ahead/EUR records, removed only exact duplicates,
and retained negative/extreme prices. Native hourly prices were used directly;
four complete, distinct quarter-hours were averaged where the source changed
to PT15M from 2025-09-30 22:00 UTC. Missing/ambiguous prices were not interpolated,
forward-filled or replaced by zero. No inflation adjustment was applied.

## Terms Checked and Decision

The official [legal terms page](https://transparencyplatform.zendesk.com/hc/en-us/articles/40921911218961-Legal-Terms-and-Conditions)
links the [29 March 2023 Terms of Use](https://transparencyplatform.zendesk.com/hc/en-us/article_attachments/40921869376401)
and the [18 October 2023 free-reuse list](https://transparencyplatform.zendesk.com/hc/en-us/article_attachments/40921869379729).
Terms section 2.5 limits the open-data arrangement to a subset; section 3.1
requires attribution and respect for primary-owner rights. The list does not
include day-ahead energy prices under article **12.1.d**. Its capacity/allocation
and physical-flow entries are different data items.

Consequently, the list does not establish CC BY 4.0 permission for these price
series. No separate primary-owner redistribution authorisation was found in
the project. Public release of the underlying prices is therefore withheld,
not declared universally prohibited. Attribution alone is not treated as
clearance. MIT does not apply to these data.

Questions still requiring evidence are: which primary-owner rights apply to
each PL/LT price series and period, and whether permission covers bulk public
redistribution of both the source observations and their hourly transformation.
Obtain the applicable permission or a clearly applicable primary-owner licence
before adding these files to a public commit or release asset.

## Files Excluded From Public Git History

Original files remain unchanged locally. They are ignored by Git and excluded
from the public manifest; no columns have been deleted to disguise the issue.

- `data/prices_PL_LT_native_2021_2025.parquet`
- `data/prices_PL_LT_hourly_2021_2025.parquet`
- `data/phs_baseline_dispatch.parquet`
- `data/phs_discrete_baseline_dispatch.parquet`
- `data/phs_sensitivity_dispatch.parquet`
- `data/phs_operating_hourly_profiles.csv`
- `data/price_spread_diagnostics_2021_2025.csv`
- `figures/figure_1_market_background.png` and `.pdf`
- `figures/figure_price_structure.png` and `.pdf`
- `figures/figure_spread_contributions.png` and `.pdf`

The dispatch files contain explicit hourly prices. Hour-of-day price summaries
and price-background charts are also withheld conservatively pending clarification.
The retained model-result summaries, operational diagrams and report are authored
research outputs, not a substitute distribution of the source price time series;
they are not relicensed under MIT or assigned a data licence by this release.

## Reproduction With Lawfully Obtained Local Inputs

Obtain the relevant extract using the official Transparency Platform/FMS access
route under its applicable terms. The existing `scripts/prepare_prices.py`
documents the selection and hourly construction. It expects an external
`project_root/data/raw/entsoe/EnergyPrices_12.1.D_r3` monthly archive and download
manifest relative to the study's parent directory; that archive is not included.
No credentials or licences are supplied. Restore authorised local inputs under
their original names to run the original workflows. Public clones alone cannot
rerun dispatch-dependent analyses without these withheld inputs; the existing
scripts and models have not been refactored to hide this limitation.
