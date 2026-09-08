"""The matplotlib PNG companions: which files get written, and what they plot (W4.7).

`report_plots.py` is deliberately *not* golden-tested — matplotlib output is not byte-stable
across versions, so pinning its bytes would produce a test that fails on every upgrade and proves
nothing about the figures. What can be pinned is the two things a reader actually depends on: that
the file set a caller is promised really appears on disk, and that the numbers behind a chart are
the ones the HTML report draws from the same view function.

That second half is the point of the whole W4.7 split. The PNGs and the report's inline SVGs are
two renderings of one set of figures, and they agree only because both read `views.py` rather than
each computing its own. A test that checked the pictures could never see that; a test that checks
the view the picture is built from can.

**Error class.** A failure here is a *presentation* failure, like the report goldens: a PNG that is
missing, empty or drawn from the wrong figures misleads a reader of a hand-out, and can never
corrupt a stored result.
"""

# clean

import os

import pytest

from hisim.economics import views
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator
from hisim.economics.report_plots import write_report_plots
from hisim.economics.results import EvaluationMatrix
from hisim.economics.subsidies import SubsidyCatalog

# The oracle's fixture, reused rather than rebuilt: it is the run these charts are meant to draw —
# banded, subsidised, multi-perspective, multi-subject — and a second fixture here would be a
# second thing to keep rich as the report grows.
from tests.test_economics_report_goldens import PARAMETERS, PERSPECTIVES, make_inputs

pytestmark = pytest.mark.base

#: The four charts every run gets, plus the fifth that only a comparison produces.
BASE_PNG_NAMES = {
    "lifecycle_annual_cash_flows.png",
    "lifecycle_investment_waterfall.png",
    "lifecycle_perspective_costs.png",
    "lifecycle_component_costs.png",
}
PAYBACK_PNG_NAME = "lifecycle_payback_curve.png"

#: First eight bytes of every PNG file. Checked instead of "size > 0" alone, because a truncated or
#: half-written file is non-empty too.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(name="evaluated", scope="module")
def fixture_evaluated():
    """The evaluated matrix and a reference result to compare it against.

    Evaluated once for the module: the charts are read-only consumers of the results, so every test
    here can share one evaluation, and re-running the evaluator per test would dominate the runtime
    of a module that is really about files on disk.

    Returns:
        `(matrix, reference)` — the matrix the PNGs are drawn from, and the cheaper variant that
        turns the set into a comparison and adds the payback curve.
    """
    database = CostDatabase()
    catalog = SubsidyCatalog.load("DE")
    evaluator = EconomicEvaluator(database, PARAMETERS, catalog)

    matrix = EvaluationMatrix()
    for perspective in PERSPECTIVES:
        matrix.results[perspective.id] = evaluator.evaluate(make_inputs(), perspective)
    reference = evaluator.evaluate(make_inputs(energy_kwh=15000.0, investment=2000.0), PERSPECTIVES[1])
    return matrix, reference


def _assert_is_a_real_png(path: str) -> None:
    """The file exists, is not empty and begins with the PNG signature."""
    assert os.path.isfile(path), path
    with open(path, "rb") as file:
        head = file.read(len(PNG_MAGIC))
    assert os.path.getsize(path) > len(PNG_MAGIC), path
    assert head == PNG_MAGIC, path


class TestWrittenFiles:
    """What `write_report_plots` promises a caller: the paths it returns are files that exist."""

    def test_the_four_base_charts_are_written(self, evaluated, tmp_path):
        """Catches the PNG set silently shrinking — a chart that returns early writes no file.

        Two of the five plot functions return their path without writing anything when they have
        nothing to draw (no subjects, no breakdowns), and `write_report_plots` filters those paths
        out of its return value. That is the right behaviour and also the way the set can quietly
        lose a chart: a view that starts returning empty, a perspective that stops carrying
        breakdowns, and the caller still gets a list of real files and a report that looks whole.
        On a fixture this rich all four have something to draw.
        """
        matrix, _reference = evaluated

        written = write_report_plots(matrix, str(tmp_path))

        assert {os.path.basename(path) for path in written} == BASE_PNG_NAMES
        for path in written:
            _assert_is_a_real_png(path)

    def test_a_reference_result_adds_the_payback_curve(self, evaluated, tmp_path):
        """The comparison's fifth chart belongs to this set, not to whoever calls it.

        The payback curve used to be written by the `report` CLI as a separate call, under a file
        name only that call site knew, after `write_report_plots` had written the other four. One
        function owns the set now: handed the comparison's reference result, it writes all five.
        """
        matrix, reference = evaluated

        written = write_report_plots(matrix, str(tmp_path), reference)

        assert {os.path.basename(path) for path in written} == BASE_PNG_NAMES | {PAYBACK_PNG_NAME}
        for path in written:
            _assert_is_a_real_png(path)

    def test_an_empty_matrix_writes_nothing_instead_of_failing(self, tmp_path):
        """An evaluation that produced no perspective is a state, not a crash, for the PNGs.

        Unlike the HTML and markdown reports — which are built around a reference perspective and
        now refuse an empty matrix by name — the PNG set is a hand-out: with nothing to draw it
        contributes no files and lets the rest of the postprocessing finish.
        """
        assert write_report_plots(EvaluationMatrix(), str(tmp_path)) == []
        assert os.listdir(tmp_path) == []


class TestPlottedFigures:
    """The numbers behind the charts, read from the same view functions the HTML report reads."""

    def test_the_investment_waterfall_segments_add_up_to_the_gross(self, evaluated):
        """Catches the funded-share split drifting from the gross it is a split *of*.

        The waterfall draws one bar per subject as two stacked segments — the net investment and
        the part support covers — and prints the gross at its end. If those two segments did not
        sum to that gross, the bar would end somewhere other than the number written beside it, and
        the "how much of this measure is funded" reading, which is the chart's entire purpose, would
        be wrong while looking fine. Both segments come from `views.subsidy_share_of_gross`,
        including the `min(subsidy, gross)` clamp that is the one home of that business rule, so
        this also pins the clamp: reported support never exceeds the gross, and the funded share
        stays a fraction.
        """
        matrix, _reference = evaluated
        shares = views.subsidy_share_of_gross(matrix.results["brownfield_net"])

        assert shares, "the fixture has to produce at least one priced subject"
        for share in shares.values():
            assert share.net_in_euro + share.subsidy_in_euro == pytest.approx(share.gross_in_euro)
            assert 0.0 <= share.subsidy_in_euro <= share.gross_in_euro
            assert 0.0 <= share.share_of_gross <= 1.0
        # Not a vacuous check: this fixture really is subsidised, so the split has two segments.
        assert any(share.subsidy_in_euro > 0 for share in shares.values())

    def test_a_gross_perspective_funds_nothing_and_still_splits_cleanly(self, evaluated):
        """The same identity where the support side is zero — the bar is one segment, not none.

        A gross perspective applies no support by construction, and the chart still has to draw the
        full gross rather than an empty bar. It is the degenerate end of the same arithmetic, and
        the one a "share" formula divides by zero in if the guard in `share_of_gross` is lost.
        """
        matrix, _reference = evaluated
        shares = views.subsidy_share_of_gross(matrix.results["brownfield_gross"])

        assert shares
        for share in shares.values():
            assert share.subsidy_in_euro == pytest.approx(0.0)
            assert share.net_in_euro == pytest.approx(share.gross_in_euro)
