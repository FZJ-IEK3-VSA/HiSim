"""Human-readable lifecycle cost reports for the LIFECYCLE_COST_REPORT postprocessing option.

Outputs: `cost_summary.md` (fixed rounding and row order, so golden diffs show only moved numbers),
`lifecycle_report.html` (self-contained, inline SVG) and matplotlib PNGs (`report_plots.py`). Each section opens with
explanation prose from `report_prose.ReportProse`. This package never computes: every number is read off the result or
`views.py`, and only SVG geometry and rounding happen here (§2.4). The modules stack as `charts` -> `scaffold` ->
`sections`/`sections_charts` -> `assembly`.
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
