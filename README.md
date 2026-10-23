# HYDROFORUM: Pumped Storage in Poland and Lithuania

**Software version 0.1.0.** Original code is released under MIT. Third-party
price files are withheld pending redistribution clearance; see
[Data rights](DATA_RIGHTS.md) and [Sharing status](SHARING_STATUS.md).

This study examines whether day-ahead arbitrage can support pumped-storage
investment. Each year in 2023-2025 is optimised separately for Poland and
Lithuania, then annual outcomes are averaged. Price background data cover
2021-2025. Results describe **model-optimised operation, not observed operation
of actual power stations**. This is not a Roznow II project feasibility study,
engineering advice to TAURON, or a guarantee of realised revenues.

## Read Without Running Code

1. [Operating-pattern report](reports/phs_operating_patterns_summary.docx):
   short English report with two figures.
2. [Seasonal pumping](figures/figure_phs_seasonal_pumping.png) and
   [daily patterns](figures/figure_phs_daily_patterns.png).
3. [Technical results](data/phs_sensitivity_mean.csv) and
   [financial results](data/phs_financial_sensitivity.csv).
4. [Conference documents](conference_documents/README.md): paper and slides
   are pending supply and approval by the study owner.

## Main Findings and Baseline

Mean baseline arbitrage margins are EUR 112.63/kW/year in Poland and
EUR 184.17/kW/year in Lithuania, before fixed O&M and capital recovery.
Midday pumping-energy shares increase from 38.4% to 54.2% in Poland and
25.7% to 35.9% in Lithuania between 2023 and 2025. Winter pumping is mainly
at night; midday pumping strengthens in summer. Lithuania has higher overall
pumping utilisation and more days with substantial pumping in both windows.

- Total grid-side generating power: 1 MW; pumping power: 1 MW.
- Six-hour storage means **6 MWh of discharge-equivalent energy**, not raw
  hydraulic energy. Initial and terminal energy are each 3 MWh in each year.
- Hourly balance: `E[t+1] = E[t] + 0.75 * pump[t] * dt - generate[t] * dt`,
  with `dt = 1 hour`, and `0 <= E <= 6 MWh` for all states.
- Efficiency is applied once on pumping in this energy convention. Generation
  and pumping powers are continuous; binary plant mode prevents simultaneous
  pumping and generation. The model is a price-taking, perfect-foresight MILP.
- Storage may carry across days; no daily reset, cycle limit, startup cost,
  natural inflow, self-discharge or reserve commitment is imposed.
- The four-unit discrete baseline is retained as an existing comparison, not
  the main operating-pattern formulation. Existing technical sensitivities
  vary duration or efficiency one at a time; none were rerun during packaging.
- Financial assumptions: 40 years, 2.2% CAPEX/year fixed O&M, reference CAPEX
  EUR 2,500/kW and 7% discount rate. Financial parameters do not affect dispatch.

Local windows are midday [10:00,16:00), night [22:00,24:00) and [00:00,06:00).
UTC optimisation-year boundaries are retained for annual energy shares; daily
classification uses complete local days in Europe/Warsaw and Europe/Vilnius,
including daylight-saving days. No-pumping days are identified first; otherwise
both windows require at least 20% each, before applying the 60% dominance rule.
These are descriptive categories, not mechanical start counts or full cycles.

## Authors

Primary code author and maintainer: **Yuting Cui**, Wrocław University of Science
and Technology. Contact: [yuting.cui@pwr.edu.pl](mailto:yuting.cui@pwr.edu.pl).
The available code history identifies no additional named code contributors;
paper coauthors have not been treated as software authors. The limits of this
check are recorded in [Authorship and licence scope](AUTHORSHIP_AND_LICENSE.md).

## Citation

For this software, cite:

> Cui, Y. *HYDROFORUM PHS Day-Ahead Arbitrage in Poland and Lithuania*
> (Version 0.1.0) [Computer software].

[CITATION.cff](CITATION.cff) provides machine-readable software citation metadata
for GitHub's **Cite this repository** feature. No DOI has been assigned here.
The conference paper citation will be listed separately when its exact version
and bibliographic details have been supplied; no publication status is implied.

## License

The [MIT License](LICENSE) covers original code and software documentation only,
to the extent the licensor holds the necessary rights. It does **not** license
ENTSO-E/primary-owner data, study datasets, figures, the operating report, papers,
slides, fonts or third-party dependencies. See [licence scope](AUTHORSHIP_AND_LICENSE.md).
Contact the maintainer about reuse of non-code research material.

## Data source

ENTSO-E Transparency Platform, `EnergyPrices_12.1.D_r3`, day-ahead bidding zones
PL (`10YPL-AREA-----S`) and LT (`10YLT-1001A0008Q`). Prices remain in reported
EUR/MWh, including negative values, without inflation adjustment or gap filling.
UTC interval-start timestamps are retained. From 2025-09-30 22:00 UTC, four
complete, distinct quarter-hours are averaged for each hourly model input.
There is no silent resolution mix. Copyright and related/database rights in
the source data, where applicable, remain with ENTSO-E and/or their primary owners.
This study is not endorsed by ENTSO-E.

The [official reuse terms and data-item list](https://transparencyplatform.zendesk.com/hc/en-us/articles/40921911218961-Legal-Terms-and-Conditions)
do not establish a blanket CC BY 4.0 licence for day-ahead prices (article 12.1.d).
Redistribution permission for these PL/LT series remains unconfirmed. Native and
hourly price files, dispatch files containing prices, and price-background outputs
are therefore **excluded from public Git history**, while retained unchanged
locally. [DATA_RIGHTS.md](DATA_RIGHTS.md) records the evidence and exact exclusions.

## Files

| Folder / file | Contents |
|---|---|
| `scripts/` | Original model, preparation, analysis and plotting code; package integrity checker |
| `data/phs_baseline_annual.csv` | Original continuous baseline annual results |
| `data/phs_discrete_baseline_annual.csv` | Existing four-unit comparison |
| `data/phs_sensitivity_annual.csv`, `phs_sensitivity_mean.csv`, `phs_financial_sensitivity.csv`, `phs_capex_sensitivity.csv` | Existing technical and financial results |
| `data/phs_operating_daily.parquet`, `phs_operating_period_summary.csv`, `phs_operating_pattern_robustness.csv`, `phs_operating_patterns_audit.json` | Existing operating-pattern results and historical audit |
| `figures/` | Technical, financial and operating-pattern PNG/PDF figures |
| `reports/` | Existing English Word operating-pattern report |
| `conference_documents/` | Pending paper and presentation |
| `PACKAGING_NOTES.md`, `FILE_MANIFEST.json` | Packaging record and file hashes |

## Environment and Commands

Recorded environment: Python 3.12.3, NumPy 2.4.6, SciPy 1.18.1, PyArrow 25.0.1,
Matplotlib 3.10.9. MILP solving uses HiGHS via SciPy, with relative gap target
`1e-4` and a 300-second annual time limit. No commercial solver licence is needed.
Core dependencies are listed in `requirements.txt`; no environment or fonts
are bundled. Some original figure scripts require Times New Roman and stop if
it is unavailable. The operating-pattern script has a DejaVu Serif fallback.

Run commands from this directory after installing the listed dependencies in
your own environment. **Only the package-integrity command was executed during
this packaging task. No analysis, plotting or optimisation was rerun.**

```bash
python scripts/check_package.py
```

Existing entry points, for later use on a working copy after restoring any
required lawfully obtained inputs:

```bash
python scripts/analyse_phs_operating_patterns.py
python scripts/plot_price_summary.py
python scripts/plot_revised_main_figures.py
python scripts/run_phs.py --pumping-formulation continuous
```

The operating-pattern and price-summary commands require files withheld from
the public repository. The revised-results plotting command uses the retained
technical/financial summaries. The first three commands regenerate/overwrite
their own derived files. The
last runs the original continuous baseline and prints outcomes **without saving
new continuous baseline files**; it retains binary exclusivity. The original
default `run_phs.py` mode is discrete. `run_sensitivities.py` refuses to overwrite
existing sensitivity outputs. Do not remove the distributed results to bypass
these protections; use a separate working copy for any new computations.

`prepare_prices.py` is retained for provenance. It requires the original
external raw-archive/manifest layout, which is not bundled; its source and
processing steps are described in [DATA_RIGHTS.md](DATA_RIGHTS.md). Model runs
also require the withheld hourly price input. The current scripts are preserved, not
presented as a newly tested from-scratch deployment pipeline. No new operating
episode analysis or seven-day examples were added in this packaging task.
