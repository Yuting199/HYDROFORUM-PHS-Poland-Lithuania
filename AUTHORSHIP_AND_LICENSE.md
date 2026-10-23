# Authorship and Licence Scope

## Confirmed Code Attribution

Primary code author and maintainer: **Yuting Cui**, Wrocław University of Science
and Technology; contact: **yuting.cui@pwr.edu.pl**. Attribution and the MIT grant
for original code are based on the maintainer's explicit publication instruction.

The available parent-repository history for the ENTSO-E acquisition/processing
code identifies Yuting Cui. The price-cleaning predecessor is recorded under
commit `b8762bc`. The study documentation records adaptation of local TSV parsing
and market-filtering patterns from that code. No other named code contributor
or third-party copied-code notice was identified in the inspected study scripts
or relevant predecessor history. The HYDROFORUM study scripts themselves were
previously untracked, so their detailed development authorship cannot be
independently reconstructed from Git history.

This evidence does not establish that no other rights can exist. Paper
coauthorship is not treated as evidence of software authorship or code ownership.
No additional authors, ORCIDs, institutional copyright assignment, or publication
status have been inferred. Institutional/employment rights and any unrecorded
contributions remain the maintainer's responsibility to confirm; corrections
should be sent to the contact above.

## MIT Scope

[LICENSE](LICENSE) applies only to the original source code in `scripts/` and
original software documentation, to the extent the licensor holds the rights
necessary to grant it. It does not relicense third-party portions, dependencies,
price data or any other content whose rights are held by others.

Specifically, this code licence does **not** cover:

- ENTSO-E or primary data-owner content, including processed price series.
- Research datasets/results under `data/`, figures under `figures/`, and the
  report under `reports/`; public availability is not an additional reuse licence.
- Conference papers or slides (not yet supplied in this repository).
- NumPy, SciPy/HiGHS, PyArrow, Matplotlib, fonts or other external dependencies,
  which retain their own respective licences and are not vendored here.

For third-party data attribution and the withholding decision, see
[DATA_RIGHTS.md](DATA_RIGHTS.md). For reuse of non-code study material, contact
the maintainer rather than assuming the MIT licence applies.
