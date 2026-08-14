"""Human-readable lifecycle cost reports (postprocessing option LIFECYCLE_COST_REPORT).

Follows the money along the calculation chain so results can be checked for plausibility:

1. plausibility panel (automated checks, thresholds in cost_database/plausibility_checks.json)
2. input audit (facts x database resolution)
3. investment build-up waterfalls (year 0)
4. annual cash-flow timeline + cumulative discounted cost
5. year-1 energy bill decomposition with implied effective prices
6. subsidy decision cards
7. perspective overview (EAC bands)
8. per-component breakdowns
9. variant comparison (delta waterfall by subject + discounted payback curve)

Outputs: `cost_summary.md` (diffable text), `lifecycle_report.html` (self-contained, inline
SVG, light/dark aware) and matplotlib PNGs (see `report_plots.py`).

**Who this is for.** The reader is a domain expert reviewing a run they did not produce, so the
report is organized as a chain of falsifiable questions rather than as a dashboard: each section
is placed where a mistake made upstream of it first becomes visible, and every chart is paired
with the table it was drawn from. The HTML report numbers its sections along that chain — **0**
plausibility panel, **1** input audit, **2** year-0 investment waterfalls, **3** cash-flow
timeline, **4** year-1 energy bill, **4b** lifecycle CO2, **5** subsidy decisions, **6**
perspectives, **6b** actor split, **7** per-component stacks, **8** variant comparison, **9**
scenarios, **10** the lifecycle KPI table — and each `_*_section_html` function below documents
the question its section exists to answer. The most load-bearing of them is section 4: an
effective price of 300 EUR/kWh or 0.0003 EUR/kWh is the fastest detector of a unit mix-up
anywhere between the meter and the tariff, and it needs no domain knowledge to spot.

**Two outputs, two jobs.** The HTML is for reading a single run and is allowed to be rich
(collapsible details, tooltips, per-perspective tabs). The markdown summary is for *diffing*
runs: it is committed against golden scenarios so that a PR touching price data shows up as a
clean textual delta rather than as silent drift (§9.5). That is a real constraint on the
emitters below, not a style preference — the markdown carries fixed rounding, fixed row order,
one table per topic, and no run-dependent decoration, so that any line that moves moved because
a number moved. `tests/test_economics_report_goldens.py` byte-compares both outputs, normalizing
only the generation date.

**This module never computes** (cost-spec-v2 §2.4, the seam-4 invariant). Every displayed
number is read off the result object or off `views.py`; the only arithmetic left here is SVG
geometry — scales, bar heights, pixel coordinates — and the rounding inside `_fmt`. Discounting,
aggregation, category folding and the business rules that used to hide in chart helpers all live
engine-side. `tests/test_economics_import_lint.py` enforces the import half of that contract;
`tests/test_economics_report_goldens.py` pins the rendered output.
"""

from hisim.economics.reporting.charts import _annual_flow_svg, _stacked_subject_svg  # noqa: F401 — unit-tested directly
from hisim.economics.reporting.sections import _timeline_detail_table  # noqa: F401 — unit-tested directly
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
    "ReportFileNames",
    "all_bands_degenerate",
    "build_cost_summary_markdown",
    "build_lifecycle_report_html",
    "render_plausibility_findings",
    "write_cost_summary",
    "write_lifecycle_report",
]
