# Packaging Record

Version: v0.1.0. The initial local-review package and the publication subset are
distinguished below. Remote publication status must be checked separately;
these notes do not certify a completed GitHub upload.

## Initial Local-review Scope

- Copied all nine existing study Python scripts without any code changes.
- Retained all saved CSV/Parquet results, native/hourly prices and operating audit.
- Kept seven distinct figure pairs (PNG/PDF) and the existing Word report.
- Excluded old duplicate price-summary/layout figures and personal run screenshots.
- Did not copy the raw archive, virtual environment, caches, account configuration,
  solver licences or parent Git history. Original study files remain untouched.
- Paper and PPT are explicitly pending; no substitute documents were inserted.

## Provenance-only cleanup

In release copies only, personal absolute paths in JSON/Parquet provenance were
replaced by package-relative paths or `external_source/...` labels. External
source labels are provenance descriptions, not files expected inside this bundle.
Affected file-hash references were updated to the packaged serialization so the
original model can still recognise the matching input. No prices, timestamps,
dispatch values, financial values, parameters or classifications were changed.
Every Parquet table was compared exactly to its original, ignoring metadata only.
CSV changes, where needed, were restricted to hash-reference cells. Code, images
and the report are byte-identical to the source files.

`SOURCE_PROVENANCE.json` records original and packaged SHA-256 values and each
treatment. Original figure metadata may refer to original-source hashes; this
mapping explains the correspondence. The copied operating audit documents the
historical analysis, not a new run or a complete manifest of this curated bundle.
Use `FILE_MANIFEST.json` for the current package contents.

## Initial Packaging Checks

File checks covered source immutability, exact tabular-value preservation,
unchanged source code, current input-hash consistency, Python syntax, Markdown
local links, ZIP/Word integrity, both embedded report images, curated file lists,
and targeted credential/personal-path checks including structured metadata.
The final TAURON ZIP was extracted into a fresh temporary directory and its
integrity checker was executed there. The environment versions in requirements
were checked against the existing environment; no clean dependency installation
or cross-platform execution was attempted.

**No model/solver test, optimisation, postprocessing analysis, plotting, or
new episode/seven-day analysis was executed in the initial packaging task.**
The unrelated parent history was not included, scanned or rewritten. Targeted
security checks are not a comprehensive security certification.

The original raw-data preparation path still requires external source files,
and some plotting scripts require Times New Roman. These are disclosed in the
README rather than addressed by model or workflow refactoring.

## Public Subset and Metadata Update

Only this `github_upload` candidate is being prepared for publication. The
TAURON package and ZIP remain untouched and no longer have identical contents
or README text. No model, analysis script, numerical result, figure or report
was modified in this update. The package-integrity checker was adjusted to
accept a Git repository and the explicitly withheld local files.

The maintainer supplied Yuting Cui's attribution and authorised MIT for original
code. `LICENSE`, `CITATION.cff` and the authorship/data-rights documentation record
the grant and its limits. Public release excludes the 13 price-containing or
price-background files listed in `DATA_RIGHTS.md`. Their unchanged local copies
are not part of the public manifest. This is a file-selection decision, not a
recalculation or alteration of research outputs.

The new Git history must start in this candidate directory; no parent commits
are to be imported. `FILE_MANIFEST.json` is the authoritative public file list
(excluding itself), with SHA-256 hashes. It also lists the withheld local paths.
Historical audit and source-provenance records can name omitted files; those
references do not mean the files are distributed.
