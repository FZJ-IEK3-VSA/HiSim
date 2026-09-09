"""Human-readable lifecycle cost reports.

Produced for the LIFECYCLE_COST_REPORT postprocessing option, which arrives with stack part 8/8
(the bridge). The report follows the money along the calculation chain so results can be checked
for plausibility. `scaffold.ReportSections.ORDER` is the authoritative list of its sections and
of the order they appear in; it is deliberately not restated here, because a second list is a
second thing to keep in step and the first one to go stale.

Outputs: `cost_summary.md` (diffable text), `lifecycle_report.html` (self-contained, inline
SVG, light/dark aware) and matplotlib PNGs (see `report_plots.py`).

**Who this is for.** The reader is a domain expert reviewing a run they did not produce, so the
report is organized as a chain of falsifiable questions rather than as a dashboard: each section
is placed where a mistake made upstream of it first becomes visible, and every chart is paired
with the table it was drawn from. Sections carry **names**, not numbers: the report used to
interleave a 0-to-10 numbering with the V-numbers of the chart set, neither of which ran
monotonically down the page, so a reader could not navigate by either. Names replace both, a
table of contents provides the navigation the numbering was supposed to, and every
`_*_section_html` function documents the question its section exists to answer. The most
load-bearing of them is the energy bill: an effective price of 300 EUR/kWh or 0.0003 EUR/kWh is
the fastest detector of a unit mix-up anywhere between the meter and the tariff, and it needs no
domain knowledge to spot.

**Every section explains itself.** Each one opens with the same four authored parts — what it
shows, what it adds, a "Terms used here" disclosure and a "How this is calculated" disclosure —
rendered by `scaffold._explanation_html` from `report_prose.ReportProse`. The text is authored
prose held in one file and never assembled here, so an editorial change is a change to that file
and the renderer cannot quietly reword anything.

**Two outputs, two jobs.** The HTML is for reading a single run and is allowed to be rich
(collapsible details, tooltips, per-perspective blocks). The markdown summary is for *diffing*
runs: it is committed against golden scenarios so that a PR touching price data shows up as a
clean textual delta rather than as silent drift (§9.5). That is a real constraint on the
emitters below, not a style preference — the markdown carries fixed rounding, fixed row order,
one table per topic, and no run-dependent decoration, so that any line that moves moved because
a number moved. `tests/test_economics_report_goldens.py` byte-compares both outputs, normalizing
only the generation date.

**This package never computes** (cost-spec-v2 §2.4, the seam-4 invariant). Every displayed
number is read off the result object or off `views.py`; the only arithmetic left here is SVG
geometry — scales, bar heights, pixel coordinates — and the rounding inside `_fmt`. Discounting,
aggregation, category folding and the business rules that used to hide in chart helpers all live
engine-side. `tests/test_economics_report_goldens.py` pins the rendered output; the import half
of the contract is enforced by `tests/test_economics_import_lint.py`.

The modules stack in one direction: `charts` (SVG primitives and chart builders) at the bottom,
`scaffold` (section identity, chapters, the explanation block) on top of it, `sections` and
`sections_charts` (one function per section) on top of that, and `assembly` (the document) last.
"""

from hisim.economics.reporting.charts import _annual_flow_svg, _stacked_subject_svg  # noqa: F401 — unit-tested directly
from hisim.economics.reporting.sections import _timeline_detail_table  # noqa: F401 — unit-tested directly
from hisim.economics.reporting.scaffold import (
    ReportChapters,
    ReportSections,
)
from hisim.economics.reporting.assembly import (
    build_lifecycle_report_html,
    write_lifecycle_report,
)
from hisim.economics.reporting.summary import (
    PlausibilityCheck,
    ReportFileNames,
    all_bands_degenerate,
    build_cost_summary_markdown,
    render_plausibility_findings,
    write_cost_summary,
)

__all__ = [
    "PlausibilityCheck",
    "ReportChapters",
    "ReportFileNames",
    "ReportSections",
    "all_bands_degenerate",
    "build_cost_summary_markdown",
    "build_lifecycle_report_html",
    "render_plausibility_findings",
    "write_cost_summary",
    "write_lifecycle_report",
]
