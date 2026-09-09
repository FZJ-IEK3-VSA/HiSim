"""Converts the Excel worked-example workbooks into the YAML fixtures the tests read.

Implements cost-spec-v2 §3.3 (converter, validation rules 1-10, arithmetic cross-check),
§3.4 (YAML format), §3.6 (drift check) and §3.8 (review attestations).

The workbook is the source of truth; the YAML next to it is generated and must never be
hand-edited. Run this tool after every workbook change::

    python tools/convert_worked_examples.py            # regenerate all YAML files
    python tools/convert_worked_examples.py --check    # CI: fail if a YAML drifted
    python tools/convert_worked_examples.py --list-unreviewed

**This package must not import `hisim`.** Its dependencies are deliberately limited to `openpyxl`
and `PyYAML`, because the drift check runs in a bare CI job that installs nothing else
(`.github/workflows/worked-examples.yml`); the examples themselves are checked against the engine
by the normal test suite, not by this tool. The dependency between the two runs one way only:
`tests/test_worked_examples.py` imports the fingerprint protocol and the workbook-discovery
predicate from here, so each has a single owner, and nothing here imports anything from `tests/`.

**What this tool protects.** The whole value of the worked-example library is that its expected
numbers are *tamper-evident*: they must not be able to drift quietly towards whatever a buggy
implementation happens to produce. The converter never computes an expected value. Each one is the
number Excel/LibreOffice cached when the author's own spreadsheet formula was last recalculated,
and that formula is a genuinely independent second implementation of the engine's arithmetic
(cost-spec-v2 §3). Four mechanisms keep it that way:

* the derivation travels with the number — every expected cell holds a formula, rewritten into
  named quantities, or an explicit note stating where the constant comes from (rule 5) — so a
  value pasted in from a failing test run stands out in the PR diff;
* pure-arithmetic formulas are re-evaluated in Python and compared against the cached value, which
  catches a workbook edited without recalculating it (§3.3 arithmetic cross-check);
* `--check` regenerates every YAML in CI and fails on a single byte of difference, so neither a
  hand-edited YAML nor an unconverted workbook edit survives review (§3.6);
* the whole semantic part of the YAML (inputs, derivations, expected values, tolerances) is hashed
  into a short content fingerprint, and the `review:` block is emitted only while the fingerprint
  the reviewer typed into the workbook still matches — so any semantic change invalidates the
  attestation by construction rather than by anyone remembering to clear a flag (§3.8).

Nothing here ever writes a workbook: openpyxl discards cached formula values on save, and attesting
a review has to stay a human action performed inside Excel with the sheet open (§3.8).

Workbook layout (see `tests/worked_examples/_template.xlsx`, §3.2). One sheet, three
sections, each opened by an all-caps marker in column A:

===============  ==========================  ==================  ==============
column A         column B                    column C            column D
===============  ==========================  ==================  ==============
``METADATA``     name / spec_section / computed_by / description / reviewed_by /
                 review_date / reviewed_fingerprint, one per row
``INPUTS``       literal value               comment             --
``EXPECTED``     formula (or noted constant) abs_tol             note
===============  ==========================  ==================  ==============

Every input and expected row carries a workbook-scoped defined name on its column-B cell, so
formulas read `=principal_in_euro*interest_rate` instead of `=B4*B5`. The `spec_section` metadata
row names a subsection of **`cost_spec.md`** — the domain specification — and not of
`roadmap/cost-spec-v2.md`, which describes this library; both documents have a §3.2 and a §3.6
whose meanings differ, so the field would otherwise be ambiguous.

**Module map.** The package was split out of a single 1,147-line `tools/convert_worked_examples.py`
in the PR-561 review round; each module owns one seam and can be read on its own:

======================  ====================================================================
`model.py`              the dataclasses one example is held in, and `ValidationError`
`attestation.py`        §3.8: content fingerprint, review state, the warn/error switch
`emitter.py`            §3.4: every byte of the generated YAML text
`arithmetic.py`         §3.3: the Excel-to-Python translator of the stale-cache cross-check
`formulas.py`           rewriting raw cell references into defined names (rules 1, 3, 4, 7)
`workbook.py`           the §3.2 sheet template, the row-level rules, `convert_workbook`
`cli.py`                workbook discovery, the three run modes, the exit codes
======================  ====================================================================
"""

from __future__ import annotations

from tools.worked_examples.cli import main

__all__ = ["main"]
