"""The matplotlib PNG companions: which files get written, and what they plot (W4.7).

`report_plots.py` is deliberately *not* golden-tested — matplotlib output is not byte-stable
across versions, so pinning its bytes would produce a test that fails on every upgrade and proves
nothing about the figures. What can be pinned is the four things a reader actually depends on:
that the file set a caller is promised really appears on disk — one set per perspective, each file
naming the perspective it is about — that the images are not blank frames, that a chart which
draws nothing hands its caller the reason instead of vanishing silently, and that the numbers
behind a chart are the ones the HTML report draws from the same view function.

That last half is the point of the whole W4.7 split. The PNGs and the report's inline SVGs are
two renderings of one set of figures, and they agree only because both read `views.py` rather than
each computing its own. A test that checked the pictures could never see that; a test that checks
the view the picture is built from can. The two geometry tests are the same argument one level
down: the raster Sankey and the report's SVG Sankey share `presentation_style.sankey_node_boxes`,
and what is asserted here is that this renderer really draws what that layout handed it.

**Skips are data here, not output.** The renderers return `SkippedPlot` records rather than
logging, so the assertions read the records — chart, perspective and the *reason clause*, not just
its opening phrase — instead of scraping captured stdout. What a run does with those records is
the caller's decision and is tested where the caller is: the sidecar file in the bridge's tests,
the printed lines in the CLI's.

**Error class.** A failure here is a *presentation* failure, like the report goldens: a PNG that is
missing, empty or drawn from the wrong figures misleads a reader of a hand-out, and can never
corrupt a stored result.
"""

# clean

import contextlib
import os
from typing import Callable, Dict, List, Optional, Tuple

import numpy
import pytest
from matplotlib import image as mpl_image
from matplotlib.figure import Figure
from matplotlib.patches import PathPatch, Rectangle

from hisim.economics import report_plots, views
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator
from hisim.economics.presentation_style import PresentationStyle, group_name, sankey_node_boxes
from hisim.economics.report_plots import (
    AUDIT_HEATMAP_FILE_NAME,
    SkippedPlot,
    PERSPECTIVE_COSTS_FILE_NAME,
    _draw_lane_events,
    _fitting_font_size,
    _HeatmapStyle,
    _SankeyStyle,
    _SwimlaneStyle,
    _comparison_basis,
    _component_label_geometry,
    _draw_sankey,
    _figure,
    _heatmap_caption_lines,
    _legend_below_axes_anchor,
    _stack_positive_negative,
    plot_component_costs,
    plot_payback_curve,
    report_plot_file_name,
    write_audit_plots,
    write_report_plots,
)
from hisim.economics.report_prose import payback_interval_sentence, treemap_disclosure
from hisim.economics.results import EvaluationMatrix, compare
from hisim.economics.timeline import CostCategory
from hisim.economics.subsidies import SubsidyCatalog

# The oracle's fixture, reused rather than rebuilt: it is the run these charts are meant to draw —
# banded, subsidised, multi-perspective, multi-subject — and a second fixture here would be a
# second thing to keep rich as the report grows.
from tests.test_economics_report_goldens import PARAMETERS, PERSPECTIVES, make_inputs

# The hand-written-timeline builder of the view tests, for the one thing the golden fixture cannot
# provide: a result with nothing in it at all, which is what every skip path is about.
from tests.test_economics_views_charts_a import entry, make_result

pytestmark = pytest.mark.base

#: The charts every perspective of this fixture gets, as file-name stems. Listed rather than
#: counted, because the set grew with the visualization extension and a bare count would be
#: satisfied by any seven files.
PER_PERSPECTIVE_CHARTS = (
    "annual_cash_flows",
    "component_costs",
    "swimlane",
    "liquidity_fan",
    "cost_treemap",
    "monthly_burden",
)

#: The charts of the per-perspective set that only some perspectives can draw, with the
#: perspectives of this fixture that can: the actor Sankey needs two parties, the funding
#: statement needs year 0 to be paid by something other than own capital, and the build-up needs
#: a year-0 investment the perspective's payer actually carries — the tenant's does not. They are
#: named here so the expected file set stays a statement about *this run* rather than a wildcard.
CONDITIONAL_CHARTS = {
    "actor_flows": {"landlord", "tenant"},
    "sources_and_uses": {"brownfield_net", "financed_net", "landlord"},
    "investment_waterfall": {"brownfield_gross", "brownfield_net", "financed_net", "landlord"},
}

#: The three charts that need a baseline to mean anything, drawn for the compared perspective
#: only.
COMPARISON_CHARTS = ("payback_curve", "comparison_bridge", "wealth_benchmark")

#: The perspective the fixture's reference result carries, i.e. the one the comparison is about.
COMPARED_PERSPECTIVE = "brownfield_net"

#: The audit's own figure, written beside `cost_audit.csv` rather than with the report set (Q9).
AUDIT_PNG_NAME = AUDIT_HEATMAP_FILE_NAME


def expected_report_png_names(matrix, with_comparison: bool):
    """Every file name `write_report_plots` should have written for this matrix.

    Built from the matrix rather than typed out, because the point of the per-perspective naming
    is that it is a rule and not a list: one file per (chart, perspective) pair that has something
    to draw, one matrix-wide comparison of perspectives, and the comparison charts for the
    compared perspective alone.
    """
    names = {PERSPECTIVE_COSTS_FILE_NAME}
    for perspective_id in matrix.results:
        for chart in PER_PERSPECTIVE_CHARTS:
            names.add(report_plot_file_name(chart, perspective_id))
        for chart, drawable_for in CONDITIONAL_CHARTS.items():
            if perspective_id in drawable_for:
                names.add(report_plot_file_name(chart, perspective_id))
    if with_comparison:
        names |= {
            report_plot_file_name(chart, COMPARED_PERSPECTIVE) for chart in COMPARISON_CHARTS
        }
    return names


#: First eight bytes of every PNG file. Checked instead of "size > 0" alone, because a truncated or
#: half-written file is non-empty too.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _actor_flows(matrix, _reference, path):
    """V1 on the landlord perspective — the only one of the fixture with two parties."""
    return report_plots.plot_actor_flows(matrix.results["landlord"], path)


def _liquidity_fan(matrix, _reference, path):
    """V2 without a comparison: the lower panel is then the discounted cost ending at the NPV."""
    return report_plots.plot_liquidity_fan(matrix.results["brownfield_net"], path)


def _liquidity_fan_with_comparison(matrix, reference, path):
    """V2 with a comparison, which is the branch that draws the payback interval note."""
    variant = matrix.results["brownfield_net"]
    return report_plots.plot_liquidity_fan(variant, path, compare(reference, variant))


def _comparison_bridge(matrix, reference, path):
    """V4, which exists only as a difference and therefore only with a reference."""
    return report_plots.plot_comparison_bridge(reference, matrix.results["brownfield_net"], path)


def _timeline_heatmap(matrix, _reference, path):
    """V6, the audit's figure, drawn here through its own renderer rather than the writer."""
    return report_plots.plot_timeline_heatmap(matrix.results["brownfield_net"], path)


def _cost_treemap(matrix, _reference, path):
    """V8 on a subsidised perspective, so the net panel really has credits to apply."""
    return report_plots.plot_cost_treemap(matrix.results["brownfield_net"], path)


def _lifecycle_swimlane(matrix, reference, path):
    """V9 on the financed perspective, so the financing lane is drawn instead of logged away."""
    variant = matrix.results["financed_net"]
    return report_plots.plot_lifecycle_swimlane(variant, path, compare(reference, variant))


def _sources_and_uses(matrix, _reference, path):
    """V10 on the financed perspective: a loan and subsidy schemes on the sources side."""
    return report_plots.plot_sources_and_uses(matrix.results["financed_net"], path)


def _wealth_benchmark(matrix, reference, path):
    """V13, the other chart that is a difference between two results."""
    return report_plots.plot_wealth_benchmark(reference, matrix.results["brownfield_net"], path)


def _monthly_burden(matrix, _reference, path):
    """V14 on the financed perspective, so debt service is part of the recurring stack."""
    return report_plots.plot_monthly_burden(matrix.results["financed_net"], path)


#: The renderers the visualization extension added, each paired with the fixture perspective that
#: gives it something to draw. Pairing matters: run against the fixture's first perspective, four
#: of these would skip themselves and the test would pass on an empty directory.
NEW_RENDERERS = {
    "V1 actor flows": _actor_flows,
    "V2 liquidity fan": _liquidity_fan,
    "V2 liquidity fan (comparison)": _liquidity_fan_with_comparison,
    "V4 comparison bridge": _comparison_bridge,
    "V6 timeline heatmap": _timeline_heatmap,
    "V8 cost treemap": _cost_treemap,
    "V9 lifecycle swimlane": _lifecycle_swimlane,
    "V10 sources and uses": _sources_and_uses,
    "V13 wealth benchmark": _wealth_benchmark,
    "V14 monthly burden": _monthly_burden,
}

#: The charts that refuse to draw an empty figure, with the chart name and a phrase from the
#: reason each one gives. An empty result is the one input that reaches every branch at once,
#: which is why they share it. The four charts at the end used to draw a blank figure instead: an
#: axis with no bars, a flat line at zero, two empty treemap frames — pictures that say "we
#: computed this and it came out empty", which is a different statement from "there was nothing
#: here to compute", and the reason the four now skip like the others.
SKIPPING_RENDERERS: Dict[str, Tuple[Callable[..., Optional[str]], str, str]] = {
    "V1 actor flows": (report_plots.plot_actor_flows, "actor-flow Sankey", "actor node(s)"),
    "V2 liquidity fan": (report_plots.plot_liquidity_fan, "liquidity fan", "zero in every year"),
    "V6 timeline heatmap": (report_plots.plot_timeline_heatmap, "ledger heatmap", "no flows at all"),
    "V8 cost treemap": (report_plots.plot_cost_treemap, "cost treemap", "positive area"),
    "V10 sources and uses": (
        report_plots.plot_sources_and_uses, "sources-and-uses Sankey", "own capital"
    ),
    "V14 monthly burden": (
        report_plots.plot_monthly_burden, "monthly burden", "zero recurring burden"
    ),
    "annual cash flows": (
        report_plots.plot_annual_cash_flows, "annual cash flows", "non-zero amount"
    ),
    "investment build-up": (
        report_plots.plot_investment_waterfall, "investment build-up", "positive year-0 investment"
    ),
    "per-component costs": (
        report_plots.plot_component_costs, "per-component costs", "no component breakdowns"
    ),
}


@pytest.fixture(name="evaluated", scope="module")
def fixture_evaluated():
    """The evaluated matrix and a reference result to compare it against.

    Evaluated once for the module: the charts are read-only consumers of the results, so every test
    here can share one evaluation, and re-running the evaluator per test would dominate the runtime
    of a module that is really about files on disk.

    Returns:
        `(matrix, reference)` — the matrix the PNGs are drawn from, and the cheaper variant that
        turns the set into a comparison and adds the three comparison charts.
    """
    database = CostDatabase()
    catalog = SubsidyCatalog.load("DE")
    evaluator = EconomicEvaluator(database, PARAMETERS, catalog)

    matrix = EvaluationMatrix()
    for perspective in PERSPECTIVES:
        matrix.results[perspective.id] = evaluator.evaluate(make_inputs(), perspective)
    reference = evaluator.evaluate(make_inputs(energy_kwh=15000.0, investment=2000.0), PERSPECTIVES[1])
    return matrix, reference


@pytest.fixture(name="plain_set", scope="module")
def fixture_plain_set(evaluated, tmp_path_factory):
    """The whole PNG set of a run without a reference, written once for the module.

    Drawing five perspectives is the expensive thing this file does, and half a dozen assertions
    are about one and the same rendering of it. Sharing the directory keeps them assertions rather
    than five re-runs of the renderers.

    Returns:
        `(directory, written)` — where the files are and what the writer returned.
    """
    matrix, _reference = evaluated
    directory = tmp_path_factory.mktemp("plain")
    return str(directory), write_report_plots(matrix, str(directory))


@pytest.fixture(name="compared_set", scope="module")
def fixture_compared_set(evaluated, tmp_path_factory):
    """The same set with a reference result, i.e. with the three comparison charts.

    Returns:
        `(directory, written)` — where the files are and what the writer returned.
    """
    matrix, reference = evaluated
    directory = tmp_path_factory.mktemp("compared")
    return str(directory), write_report_plots(matrix, str(directory), reference)


@pytest.fixture(name="drawn_text")
def fixture_drawn_text(monkeypatch):
    """Collects the text of every figure a test draws, titles included.

    The renderers close their figures on the way out (that is what `_figure` is for), so the only
    way to read what a chart *wrote* is to hold on to the figure while it is open. Wrapping the
    module's own constructor does that without any renderer knowing, and the figure's text objects
    survive the close.

    Returns:
        A callable returning everything drawn so far as one whitespace-normalized string.
    """
    figures: List[Figure] = []
    original = report_plots._figure  # pylint: disable=protected-access

    @contextlib.contextmanager
    def recording(*arguments, **keywords):
        with original(*arguments, **keywords) as (figure, axes):
            figures.append(figure)
            yield figure, axes

    monkeypatch.setattr(report_plots, "_figure", recording)

    def drawn():
        parts: List[str] = []
        for figure in figures:
            parts.extend(text.get_text() for text in figure.texts)
            # Every title in this module is set with `loc="left"`, and that is where it has to be
            # read from: `get_title()` answers about the centre and returns "" for all of them.
            parts.extend(axis.get_title(loc="left") for axis in figure.axes)
        return " ".join(" ".join(parts).split())

    return drawn


def _assert_is_a_real_png(path: str) -> None:
    """The file exists, begins with the PNG signature, and is not a blank frame.

    The header check alone passed for an image nothing had been drawn on — a renderer that
    silently produced an empty axis wrote a perfectly valid PNG of the surface colour, which is
    exactly the failure the skip branches exist to make impossible. So the image is decoded and
    its pixels are counted: a figure with marks on it has more than a handful of distinct colours,
    while a blank one has the surface, the grid and the frame and nothing else.
    """
    assert os.path.isfile(path), path
    with open(path, "rb") as file:
        head = file.read(len(PNG_MAGIC))
    assert os.path.getsize(path) > len(PNG_MAGIC), path
    assert head == PNG_MAGIC, path
    pixels = (mpl_image.imread(path)[..., :3] * 255).astype(numpy.uint32)
    packed = (pixels[..., 0] << 16) | (pixels[..., 1] << 8) | pixels[..., 2]
    painted = float((packed != packed[0, 0]).mean())
    assert len(numpy.unique(packed)) > 16, f"{path} carries too few colours to be a drawn figure"
    assert painted > 0.01, f"{path} is {100 * (1 - painted):.1f} % background — a blank frame"


class TestWrittenFiles:
    """What `write_report_plots` promises a caller: which files, for which perspectives."""

    def test_every_perspective_gets_its_own_charts(self, evaluated, plain_set):
        """The finding: the set used to be drawn for the matrix's first perspective only.

        A directory of files called `lifecycle_cost_treemap.png` beside a report that prints five
        perspectives is a directory in which nobody can say which perspective the picture is of —
        and the reader's most likely guess, "the one the report leads with", was right only by
        accident. Every perspective is drawn now, and the perspective is in the file name.

        The names are derived from the matrix rather than typed out, because that is the rule
        being asserted; the two conditional charts are named separately, since a chart that skips
        on four perspectives and draws on one is exactly what a wildcard would hide.
        """
        matrix, _reference = evaluated
        _directory, written = plain_set

        assert {os.path.basename(path) for path in written.paths} == expected_report_png_names(
            matrix, with_comparison=False
        )
        for path in written.paths:
            _assert_is_a_real_png(path)

    def test_the_matrix_wide_chart_is_written_once(self, plain_set):
        """The perspective comparison already contains every perspective, so it has no suffix.

        It is the one chart of the set drawn from the matrix rather than from a result, and
        writing it once per perspective would produce five identical images under five names.
        """
        _directory, written = plain_set

        assert [
            name for name in (os.path.basename(path) for path in written.paths)
            if "perspective_costs" in name
        ] == [PERSPECTIVE_COSTS_FILE_NAME]

    def test_a_reference_result_adds_the_comparison_charts(self, evaluated, compared_set):
        """The comparison's three charts belong to this set, not to whoever calls it.

        The payback curve used to be written by the `report` CLI as a separate call, under a file
        name only that call site knew, after `write_report_plots` had written the others. One
        function owns the set now: handed the comparison's reference result, it writes the payback
        curve, the NPV bridge and the fixed-interest benchmark along with the rest.
        """
        matrix, _reference = evaluated
        _directory, written = compared_set

        assert {os.path.basename(path) for path in written.paths} == expected_report_png_names(
            matrix, with_comparison=True
        )
        for path in written.paths:
            _assert_is_a_real_png(path)

    def test_the_comparison_charts_are_drawn_for_the_compared_perspective_only(self, compared_set):
        """A difference is about one perspective, and only that one gets the three charts.

        Drawing them per perspective would mean comparing, say, the tenant's variant against the
        landlord's reference — arithmetic on two different parties, presented as one bridge.
        """
        _directory, written = compared_set

        for chart in COMPARISON_CHARTS:
            drawn = [
                name for name in (os.path.basename(path) for path in written.paths)
                if name.startswith(f"lifecycle_{chart}_")
            ]
            assert drawn == [report_plot_file_name(chart, COMPARED_PERSPECTIVE)], chart

    def test_a_reference_perspective_the_matrix_lacks_refuses_instead_of_substituting(
        self, evaluated, tmp_path
    ):
        """The substitution this replaced: the first perspective standing in for the missing one.

        With the compared perspective evaluated only on the reference side, every comparison chart
        used to fall back to the matrix's first result — a bridge about `brownfield_gross`, a
        payback curve about `brownfield_gross`, both labelled as the comparison the caller asked
        for. There is no such thing as a like-for-like comparison here, so the three charts are
        skipped and the reason names the perspective that is missing.
        """
        _matrix, reference = evaluated
        without_the_reference_perspective = EvaluationMatrix()
        for perspective_id, result in _matrix.results.items():
            if perspective_id != reference.perspective_id:
                without_the_reference_perspective.results[perspective_id] = result

        written = write_report_plots(without_the_reference_perspective, str(tmp_path), reference)

        for chart in COMPARISON_CHARTS:
            assert not [
                name for name in (os.path.basename(path) for path in written.paths)
                if name.startswith(f"lifecycle_{chart}_")
            ], chart
        refusals = [skip for skip in written.skipped if skip.perspective_id == reference.perspective_id]
        assert {skip.chart for skip in refusals} == {
            "payback curve", "NPV bridge", "fixed-interest benchmark"
        }
        assert all("does not evaluate" in skip.reason for skip in refusals)

    def test_a_prebuilt_comparison_for_another_perspective_is_refused(self, evaluated, tmp_path):
        """The CLI hands its own comparison in; a mismatched one would mislabel three charts.

        `report --compare` builds the comparison with the two *directories* as reference and
        variant ids, so it passes the object rather than letting the set recompute it under the
        default labels. That door is exactly wide enough for a comparison of a different
        perspective to arrive, and the bridge, the benchmark and the fan's lower panel would then
        be three statements about different parties under one title.
        """
        matrix, reference = evaluated
        other = compare(matrix.results["landlord"], matrix.results["landlord"])

        with pytest.raises(views.CostDataError, match="different parties"):
            write_report_plots(matrix, str(tmp_path), reference, other)

    def test_an_empty_matrix_writes_nothing_instead_of_failing(self, tmp_path):
        """An evaluation that produced no perspective is a state, not a crash, for the PNGs.

        Unlike the HTML and markdown reports — which are built around a reference perspective and
        now refuse an empty matrix by name — the PNG set is a hand-out: with nothing to draw it
        contributes no files and lets the rest of the postprocessing finish.
        """
        written = write_report_plots(EvaluationMatrix(), str(tmp_path))

        assert written.paths == [] and written.skipped == []
        assert os.listdir(tmp_path) == []

    def test_the_audit_heatmap_is_written_beside_the_audit_tables(self, evaluated, tmp_path):
        """V6 travels with `cost_audit.csv` (owner decision Q9), not with the report set.

        It has the audit's audience and the audit's question, so it has the audit's entry point:
        `write_audit_plots`, called from the two places that write the audit tables. The check that
        it is *not* in `write_report_plots`' output is the half that matters — a chart written from
        both places would be refreshed twice and could disagree with itself between runs.
        """
        matrix, _reference = evaluated

        written = write_audit_plots(matrix.results["brownfield_net"], str(tmp_path))

        assert [os.path.basename(path) for path in written.paths] == [AUDIT_PNG_NAME]
        _assert_is_a_real_png(written.paths[0])
        assert AUDIT_PNG_NAME not in expected_report_png_names(matrix, with_comparison=True)

    def test_a_failing_heatmap_renderer_is_a_skip_and_leaves_no_half_written_file(
        self, evaluated, tmp_path, monkeypatch
    ):
        """The audit's figure may not take the audit's tables down with it.

        Both callers write `cost_audit.csv` first and delete their exports when the run fails
        afterwards, so an exception escaping this writer would remove a complete, correct audit
        table because the picture beside it could not be drawn. The failure is recorded like any
        other skip, with the exception in the reason, and a truncated PNG is removed.
        """
        matrix, _reference = evaluated

        def explode(*_arguments, **_keywords):
            raise RuntimeError("no fonts in this environment")

        monkeypatch.setattr(report_plots, "plot_timeline_heatmap", explode)

        written = write_audit_plots(matrix.results["brownfield_net"], str(tmp_path))

        assert written.paths == []
        assert [skip.chart for skip in written.skipped] == ["ledger heatmap"]
        assert "no fonts in this environment" in written.skipped[0].reason
        assert not os.path.exists(os.path.join(str(tmp_path), AUDIT_PNG_NAME))

    @pytest.mark.parametrize("chart", sorted(NEW_RENDERERS))
    def test_every_new_renderer_draws_a_real_png(self, evaluated, tmp_path, chart):
        """Each chart of the visualization extension renders the golden fixture without failing.

        The cheapest possible statement about a renderer, and the one nothing else makes: that it
        runs end to end on a rich, banded, subsidised, financed evaluation and leaves a real image
        behind. Matplotlib fails late — a bad colour, a mismatched sequence length or an empty
        series raises inside `savefig`, not in the code that assembled it — so "it produced a PNG
        that is not a blank frame" is a genuine result, and it is as far as an assertion can go
        before it would have to compare pixels.

        Each renderer is drawn on the perspective that gives it data (`NEW_RENDERERS`), because on
        the wrong one four of them would legitimately skip and this test would assert nothing.
        """
        matrix, reference = evaluated
        path = os.path.join(str(tmp_path), "chart.png")

        assert NEW_RENDERERS[chart](matrix, reference, path) == path

        _assert_is_a_real_png(path)


class TestSkippedCharts:
    """A chart with nothing to draw writes no file and hands its caller the reason."""

    @pytest.mark.parametrize("chart", sorted(SKIPPING_RENDERERS))
    def test_an_empty_view_records_a_skip_with_a_reason(self, tmp_path, chart):
        """Silence is the failure mode here, not the missing file.

        A hand-out that is short one chart is fine when the reason is "there was nothing to draw";
        it is not fine when nobody can tell that from the run. Some of these used to return early
        without a word and four of them drew a blank figure instead, which is worse: an empty axis
        is read as a result.

        The reason clause is asserted, not only the chart name. A record that named the chart and
        then said nothing usable would satisfy a check on the leading phrase and still leave the
        reader of `lifecycle_plots_not_drawn.txt` none the wiser.

        An empty timeline is the input that reaches every branch at once: no actors, no funding,
        no year-0 gross, no breakdowns, no flows in any category and nothing recurring.
        """
        renderer, expected_chart, expected_reason = SKIPPING_RENDERERS[chart]
        path = os.path.join(str(tmp_path), "chart.png")
        skips: List[SkippedPlot] = []

        assert renderer(make_result([]), path, skips=skips) is None

        assert not os.path.exists(path)
        assert [skip.chart for skip in skips] == [expected_chart]
        assert skips[0].perspective_id == "test"
        assert expected_reason in skips[0].reason

    def test_a_skip_reads_as_one_line_naming_chart_perspective_and_reason(self, tmp_path):
        """The line the bridge writes into the sidecar and the CLI prints, pinned once.

        Both callers render the record the same way, because both are answering the same question
        for the same reader: which picture is missing from this directory, for which perspective,
        and why.
        """
        skips: List[SkippedPlot] = []

        report_plots.plot_actor_flows(make_result([]), os.path.join(str(tmp_path), "x.png"), skips)

        assert skips[0].as_line() == (
            "actor-flow Sankey (test): the perspective has 0 actor node(s), so there is no "
            "who-pays-whom story to draw."
        )

    def test_a_run_without_a_reference_accounts_for_the_charts_it_cannot_draw(self, plain_set):
        """The same rule for the three charts whose input is a second evaluation, not a view.

        The payback curve, the bridge and the fixed-interest benchmark are differences: without a
        baseline there is nothing to decompose. That is the common case — most runs evaluate one
        variant — so the record has to name the reason rather than let a reader wonder which three
        files went missing. It carries no perspective, because the absence is a property of the
        run and not of any one perspective.
        """
        directory, written = plain_set

        differences = [skip for skip in written.skipped if not skip.perspective_id]
        assert {skip.chart for skip in differences} == {
            "payback curve", "NPV bridge", "fixed-interest benchmark"
        }
        assert all("no reference variant" in skip.reason for skip in differences)
        assert all(skip.as_line().startswith(skip.chart + ":") for skip in differences)
        for chart in COMPARISON_CHARTS:
            assert not [
                name for name in os.listdir(directory) if name.startswith(f"lifecycle_{chart}_")
            ]

    def test_an_empty_lane_is_recorded_as_its_own_skip(self, evaluated, tmp_path):
        """A swimlane lane with nothing on it is dropped, and the drop is accounted for.

        The chart itself is still drawn — its milestone lane always exists — so the skip is about
        the lane, not the figure, and it says which one. A reader comparing two runs' swimlanes
        otherwise sees a lane appear and disappear with no explanation.
        """
        matrix, _reference = evaluated
        skips: List[SkippedPlot] = []

        report_plots.plot_lifecycle_swimlane(
            matrix.results["brownfield_gross"], os.path.join(str(tmp_path), "lanes.png"),
            skips=skips,
        )

        assert [skip.chart for skip in skips] == [
            "lifecycle swimlane, Financing lane", "lifecycle swimlane, Subsidies & levies lane"
        ]
        assert all("no spans and no events" in skip.reason for skip in skips)


class TestStackedBars:
    """`_stack_positive_negative`: the rule three charts share, asserted once on mixed signs."""

    def make_axis_and_stack(self, values_by_group, **options):
        """Stacks the given groups on a throwaway axis and hands back what was drawn.

        The figure is created through the module's own `_figure`, so it is closed on the way out
        exactly as it is in production; everything the assertions need is read off the axis before
        that happens.

        Args:
            values_by_group: Display-group index -> one signed value per position.
            **options: Passed straight through to the helper (orientation, bar size).

        Returns:
            `(positive_baseline, negative_baseline, legend_labels, drawn_extents)` — the two
            baselines the helper returns, the labels it registered, and the value-axis extent of
            every rectangle it drew.
        """
        positions = list(range(len(next(iter(values_by_group.values())))))
        with _figure() as (_figure_object, axes):
            axis = axes[0]
            positive, negative = _stack_positive_negative(
                axis, positions, values_by_group, **options
            )
            labels = axis.get_legend_handles_labels()[1]
            extents = [
                patch.get_width() if options.get("horizontal") else patch.get_height()
                for container in axis.containers
                for patch in container
            ]
        return positive, negative, labels, extents

    def test_costs_and_credits_stack_on_separate_baselines(self):
        """The invariant the two baselines exist for: a mixed-sign group nets against nothing.

        A display group that carries both a cost and a credit — support that is booked positive in
        one year and negative in another, an energy group with a feed-in credit — must contribute
        its full height on *both* sides of zero. Summing the signed values first would draw one
        shorter bar and hide half the money, which is precisely what a reader of the cash-flow
        timeline is looking for.
        """
        positive, negative, _labels, _extents = self.make_axis_and_stack(
            {0: [10.0, -4.0], 1: [0.0, 0.0], 2: [2.0, 3.0], 3: [-7.0, 0.0]}
        )

        assert positive == [12.0, 3.0]
        assert negative == [-7.0, -4.0]

    def test_a_group_on_both_sides_of_zero_is_one_legend_entry(self):
        """Two bar calls, one name: a repeated legend row reads as two different groups.

        The rule the three call sites each carried their own copy of, and the reason this helper
        exists at all. A group drawn only below the line keeps its label — it is its only bar — so
        the suppression has to be conditional on the positive call having happened, not on the
        sign.
        """
        _positive, _negative, labels, _extents = self.make_axis_and_stack(
            {0: [10.0, -4.0], 1: [0.0, 0.0], 2: [2.0, 3.0], 3: [-7.0, 0.0]}
        )

        assert labels == [group_name(0), group_name(2), group_name(3)]

    def test_an_all_zero_group_draws_nothing_at_all(self):
        """An empty group contributes no invisible rectangle and no legend row.

        Eight display groups exist and a typical evaluation touches four of them; without the skip
        every chart would carry a legend of eight, half of it naming bars that are not there.
        """
        _positive, _negative, labels, extents = self.make_axis_and_stack({1: [0.0, 0.0]})

        assert labels == []
        assert extents == []

    def test_the_horizontal_form_stacks_along_the_value_axis(self):
        """`horizontal=True` is the per-component chart's form: same arithmetic, `barh` geometry.

        One helper serves a year axis and a subject axis, and the only thing that may differ
        between them is which of a rectangle's two dimensions carries the money. If the keyword
        mapping were wrong the bars would still be drawn, just with the value as a thickness.
        """
        positive, negative, _labels, extents = self.make_axis_and_stack(
            {0: [10.0, -4.0], 2: [2.0, 3.0]}, horizontal=True
        )

        assert (positive, negative) == ([12.0, 3.0], [0.0, -4.0])
        assert sorted(extents) == [-4.0, 0.0, 0.0, 2.0, 3.0, 10.0]


class TestRowLabelsAndLegend:
    """The two placement rules of the per-component chart, on the arithmetic that decides them.

    Both were bugs a file-size assertion cannot see: a label printed over the marker it describes,
    and a legend printed over a data row. Both are now decided by a pure function of numbers, which
    is what lets them be checked without rendering — and the rendering itself is still checked, one
    class up, by the file that comes out of `write_report_plots`.
    """

    def test_a_label_clears_the_whisker_cap_and_not_just_the_bars(self):
        """The finding itself: a row whose net band reaches past its stack is labelled past it.

        Row 0 stacks 100 EUR of cost but its net band runs to 400 — residual value and a subsidy
        pull the marker to the right of the bars, which is the normal shape once support is in.
        Positioning from the stack end alone put the text at 100, i.e. on top of the marker and its
        upper cap. Row 1 is the other way round and must still be labelled from its bar.
        """
        starts, gap, _span = _component_label_geometry([100.0, 900.0], [0.0, -50.0], [400.0, 200.0])

        assert starts == [400.0, 900.0]
        assert gap > 0.0

    def test_a_row_that_is_pure_credit_is_labelled_at_zero_not_in_the_credits(self):
        """A subject whose whole NPV is a credit has no cost stack, and its label belongs right of 0.

        Its band maximum is negative and its cost end is zero, so the clamp is what keeps the text
        out of the credit stack it would otherwise be printed across.
        """
        starts, _gap, span = _component_label_geometry([0.0], [-800.0], [-300.0])

        assert starts == [0.0]
        # The credit stack still counts towards the widest row, or the axis reserve would ignore it.
        assert span == pytest.approx(800.0)

    def test_the_legend_offset_is_the_same_half_inch_at_every_figure_height(self):
        """Three rows or fifteen, the legend sits the same distance below the axes.

        The anchor is in axes fractions, so a constant fraction would drift as the figure grows
        with the row count — far below a tall chart, on top of the x label of a short one. What is
        held fixed is the distance in inches; the fraction is derived from the figure height.
        """
        short, tall = _legend_below_axes_anchor(3.0), _legend_below_axes_anchor(9.0)

        assert short < 0 and tall < 0, "the legend has to sit below the axes, not inside them"
        assert short * (3.0 - 1.2) == pytest.approx(tall * (9.0 - 1.2))
        assert abs(short) > abs(tall), "a shorter chart needs a larger fraction for the same inch"

    def test_the_component_chart_still_writes_a_real_png_with_the_outside_legend(
        self, evaluated, tmp_path
    ):
        """The placement rules put text outside the data range; the file must still contain it.

        `bbox_inches="tight"` is what keeps a legend drawn below the axes inside the saved image,
        and getting that wrong produces a perfectly valid PNG with the legend cropped off — so the
        assertion here is the one a cropped figure would not survive being compared against: it is
        drawn, it is written, and it is bigger than the empty frame would be.
        """
        matrix, _reference = evaluated
        path = os.path.join(str(tmp_path), "component_costs.png")

        assert plot_component_costs(matrix.results["brownfield_net"], path) == path

        _assert_is_a_real_png(path)


class TestComparisonBasis:
    """The payback chart says which perspective its savings are computed on."""

    def test_one_shared_perspective_is_named_once(self, evaluated):
        """The ordinary comparison: both directories carry the same perspective.

        The name still has to appear, because the swimlane's payback milestone is drawn for the
        matrix's *first* perspective and this chart for the shared one; the two legitimately differ
        and a reader with no basis on either chart reads that as a contradiction.
        """
        matrix, _reference = evaluated
        variant = matrix.results["brownfield_net"]

        assert _comparison_basis(variant, variant) == "brownfield_net"

    def test_two_different_perspectives_are_both_named(self, evaluated):
        """Two perspectives compared: naming one of them is the misleading half of the truth.

        `write_report_plots` no longer produces this pairing on its own — a reference whose
        perspective the matrix lacks skips the comparison charts rather than substituting one —
        but the renderers are public and take any two results, and a caller that compares a gross
        variant against a net reference deserves a title that says so rather than one that quietly
        picks a side.
        """
        matrix, _reference = evaluated

        basis = _comparison_basis(matrix.results["brownfield_gross"], matrix.results["financed_net"])

        assert basis == "financed_net vs brownfield_gross"

    def test_the_payback_chart_is_written_for_a_cross_perspective_comparison(
        self, evaluated, tmp_path
    ):
        """The whole function still runs on the two-perspective case the basis wording is for."""
        matrix, reference = evaluated
        path = os.path.join(str(tmp_path), "payback.png")

        assert plot_payback_curve(reference, matrix.results["financed_net"], path) == path

        _assert_is_a_real_png(path)


class TestSankeyRendering:
    """The raster Sankey draws what the shared layout handed it — rule 2.7 and Q29 R7, PNG side.

    The SVG side of these invariants is asserted in `tests/test_economics_layouts_prose.py` against
    `SankeyGeometry` itself. What is left for here is the step that turns that geometry into
    patches, because that is where a renderer can lose it: a ribbon drawn with a per-end width, or
    a node face left partly untiled, is a picture that contradicts the layout it came from.
    """

    def tangled_diagram(self):
        """A three-column diagram whose middle column carries every unit twice.

        The same fixture the layout tests use, for the same reason: it is the shape that produced
        two different per-column scales before Q17, so a ribbon that changes width in flight shows
        up here and nowhere else.
        """
        columns = [["bank", "state", "market"], ["landlord", "tenant"], ["fees", "energy", "works"]]
        ribbons = [
            ("bank", "tenant", 400.0),
            ("state", "tenant", 300.0),
            ("market", "landlord", 500.0),
            ("landlord", "energy", 200.0),
            ("landlord", "fees", 300.0),
            ("tenant", "works", 500.0),
            ("tenant", "energy", 200.0),
        ]
        return columns, ribbons

    def skipping_diagram(self):
        """Four columns with two column-skipping ribbons and one party that nets a gain.

        The rented view reduced to its defect: the corridors a skipping ribbon is routed through,
        and the outgoing face of a party that receives more than it passes on — which is the face
        the net stub closes.
        """
        columns = [["bank", "state"], ["tenant"], ["landlord"], ["market", "suppliers"]]
        ribbons = [
            ("bank", "landlord", 600.0),
            ("state", "landlord", 300.0),
            ("tenant", "landlord", 500.0),
            ("tenant", "suppliers", 200.0),
            ("landlord", "market", 700.0),
        ]
        return columns, ribbons

    def coloured(self, ribbons):
        """The same flows in the (source, target, amount, colour) shape the renderer takes."""
        return [(source, target, amount, "#2a78d6") for source, target, amount in ribbons]

    def drawn_patches(self, columns, ribbons):
        """Draws the diagram on a throwaway axis and returns its ribbons, nodes and stubs.

        Reading the patches back off the axis is the only way to assert on geometry a renderer
        produced rather than on geometry a layout function returned. The figure goes through
        `_figure`, so it is closed before the assertions run and nothing leaks into the next test.

        Returns:
            `(ribbon_vertices, node_rectangles, stub_rectangles)` — the eight-point path of every
            ribbon leg, and the `(x, y, width, height)` of every node and every net stub.
        """
        with _figure() as (_figure_object, axes):
            axis = axes[0]
            _draw_sankey(axis, columns, self.coloured(ribbons))
            ribbon_vertices, nodes, stubs = [], [], []
            for patch in axis.patches:
                if isinstance(patch, PathPatch):
                    ribbon_vertices.append(patch.get_path().vertices)
                elif isinstance(patch, Rectangle):
                    box = (patch.get_x(), patch.get_y(), patch.get_width(), patch.get_height())
                    if patch.get_label() == _SankeyStyle.STUB_LABEL:
                        stubs.append(box)
                    else:
                        nodes.append(box)
        return ribbon_vertices, nodes, stubs

    def test_a_ribbon_keeps_one_width_from_end_to_end(self):
        """Rule 2.7 as the patch vertices see it: a flow is one number, so it is one width.

        The two ends of the drawn path are measured against each other. When the layout scaled
        each column to fill the height, the middle column's ribbons arrived about half as wide as
        they left, and the picture said a payment shrank on its way to the payee.
        """
        columns, ribbons = self.tangled_diagram()

        vertices, _nodes, _stubs = self.drawn_patches(columns, ribbons)

        assert len(vertices) == len(ribbons)
        for path in vertices:
            assert path[0][1] - path[7][1] == pytest.approx(path[3][1] - path[4][1], abs=1e-12)

    def test_the_ribbons_tile_the_fuller_face_of_every_node(self):
        """No gap and no overflow: a node is exactly as tall as what passes through it.

        A node whose ribbons overflowed its rectangle would be drawing more money than the node
        carries; one they under-filled would be leaving money unaccounted for on the face. Both
        are checked against the height the renderer actually drew.
        """
        columns, ribbons = self.tangled_diagram()

        vertices, nodes, _stubs = self.drawn_patches(columns, ribbons)

        bands = [
            (path[7][0], path[7][1], path[4][0], path[4][1], path[0][1] - path[7][1])
            for path in vertices
        ]
        for x, y, width, height in nodes:
            outgoing = sum(
                band for left_x, left_y, _rx, _ry, band in bands
                if abs(left_x - (x + width)) < 1e-6 and y - 1e-6 <= left_y + band / 2 <= y + height + 1e-6
            )
            incoming = sum(
                band for _lx, _ly, right_x, right_y, band in bands
                if abs(right_x - x) < 1e-6 and y - 1e-6 <= right_y + band / 2 <= y + height + 1e-6
            )
            assert max(outgoing, incoming) == pytest.approx(height, abs=1e-9)

    def test_the_net_position_of_an_internal_node_is_drawn_as_a_stub(self):
        """Q29 R7: the face the ribbons cannot fill is the node's net position, and it is drawn.

        The landlord takes in 1,400 and passes on 700, so a third of its outgoing face used to sit
        blank and unexplained. The stub is that remainder, at exactly its size, and it is labelled
        as an artist so a reader of the axis can tell it from a node rectangle.
        """
        columns, ribbons = self.skipping_diagram()

        vertices, _nodes, stubs = self.drawn_patches(columns, ribbons)

        assert len(vertices) == 8, "five ribbons, three of which are cut into two legs"
        assert len(stubs) == 1
        assert stubs[0][3] == pytest.approx(700.0 * sankey_node_boxes(columns, ribbons).unit_scale)


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


class TestSharedCaptions:
    """The two captions both renderers print come from one function, so they cannot drift.

    Each of them existed twice — once in the HTML section builder, once here — and each pair had
    already diverged: the payback sentence disagreed about which world pays back first and the
    PNG's version never mentioned the third one at all, and the two treemap disclosures named the
    same fold with different words ("subsidies" against "support") and the same clamp with
    different arithmetic. They live in `report_prose` now, and what is asserted here is that this
    renderer prints that function's output rather than a copy of it.
    """

    def test_the_treemap_panels_print_the_shared_disclosure(self, evaluated, tmp_path, drawn_text):
        """The PNG's caption is `treemap_disclosure`'s string, for both bases.

        Asserted through the figure's own texts rather than by calling the function twice: the
        question is whether the renderer *uses* it, which a second call could not answer.
        """
        matrix, _reference = evaluated
        result = matrix.results["brownfield_net"]
        expected = [
            treemap_disclosure(
                views.cost_structure_tiles(result, PresentationStyle.CATEGORY_TO_GROUP, basis),
                basis,
            )
            for basis in (views.TileBasis.GROSS, views.TileBasis.NET_OF_CREDITS)
        ]

        report_plots.plot_cost_treemap(result, os.path.join(str(tmp_path), "treemap.png"))

        for sentence in expected:
            assert " ".join(sentence.split()) in drawn_text()

    def test_the_gross_disclosure_names_the_credits_and_the_fold(self, evaluated):
        """The gross panel's whole job is to say what it is not showing."""
        matrix, _reference = evaluated
        tiles = views.cost_structure_tiles(
            matrix.results["brownfield_net"], PresentationStyle.CATEGORY_TO_GROUP,
            views.TileBasis.GROSS,
        )

        sentence = treemap_disclosure(tiles, views.TileBasis.GROSS)

        assert sentence.startswith("The gross panel leaves out")
        assert "of credits" in sentence and "folded into an 'other' tile per group" in sentence

    def test_the_net_disclosure_names_every_clamped_subject_or_says_none(self, evaluated):
        """A clamped subject is the entry a reviewer should ask about, so it is named.

        The net basis applies each subject's credits to its own tiles and clamps the subjects that
        end up negative; a panel that merely came out smaller would not tell anyone which ones.
        """
        matrix, _reference = evaluated
        tiles = views.cost_structure_tiles(
            matrix.results["brownfield_net"], PresentationStyle.CATEGORY_TO_GROUP,
            views.TileBasis.NET_OF_CREDITS,
        )

        sentence = treemap_disclosure(tiles, views.TileBasis.NET_OF_CREDITS)

        assert "clamps at zero the subjects whose credits exceed their costs" in sentence
        clamped = tiles.clamped_tiles()
        assert all(tile.subject in sentence for tile in clamped)
        assert ("none" in sentence) == (not clamped)

    def test_the_payback_sentence_under_the_fan_is_the_reports_sentence(
        self, evaluated, tmp_path, drawn_text
    ):
        """The fan's lower panel carries `payback_interval_sentence`, wrapped and nothing else.

        Wrapped, because a matplotlib title has no line breaking of its own — but the words are
        the report's, so a reader who has both in front of them reads one statement twice rather
        than two statements that have to be reconciled.
        """
        matrix, reference = evaluated
        variant = matrix.results["brownfield_net"]
        comparison = compare(reference, variant)
        crossings = views.band_zero_crossings(comparison.cumulative_discounted_savings_in_euro)
        expected = payback_interval_sentence(
            crossings.get("low"), crossings.get("best_estimate"), crossings.get("high")
        )

        report_plots.plot_liquidity_fan(
            variant, os.path.join(str(tmp_path), "fan.png"), comparison
        )

        assert drawn_text().count(" ".join(expected.split())) == 1


class TestTheReserveCaption:
    """The monthly burden explains its dashed line only when there is a dashed line.

    The two captions used to be printed unconditionally, so an evaluation that books no
    replacement promised "the dashed line adds the replacement reserve of 0 EUR/month" under a
    chart with no dashed line on it — a reader hunting for a mark that was never drawn, and a
    sentence about a decision nobody made.
    """

    def test_a_run_with_a_replacement_reserve_explains_its_dashed_line(self, evaluated, tmp_path, drawn_text):
        """The fixture replaces its heat pump inside the horizon, so the reserve is real."""
        matrix, _reference = evaluated

        report_plots.plot_monthly_burden(
            matrix.results["brownfield_net"], os.path.join(str(tmp_path), "burden.png")
        )

        assert "The dashed line adds the replacement reserve" in drawn_text()
        assert "Excludes every capital event" in drawn_text()

    def test_a_run_without_one_prints_only_the_capital_note(self, tmp_path, drawn_text):
        """Energy for twenty years and nothing capital: recurring cost, no reserve, no promise."""
        result = make_result(
            [entry(year, 900.0, CostCategory.ENERGY_WORKING) for year in range(1, 21)]
        )

        report_plots.plot_monthly_burden(result, os.path.join(str(tmp_path), "burden.png"))

        assert "replacement reserve" not in drawn_text()
        assert "Excludes every capital event" in drawn_text()


class TestLabelGeometry:
    """The pure decisions behind two labelling rules, checked without rendering anything."""

    def test_a_label_that_cannot_fit_its_tile_is_not_drawn_at_all(self):
        """`_fitting_font_size` returns None rather than a size nobody can read.

        A treemap tile three pixels wide will happily carry a two-line label, and the result is
        two subjects writing across each other. The shrink stops at `MIN_FONT_SIZE`; below it the
        tile goes unlabelled, which the HTML twin can afford to do differently because it has
        hover text and a PNG has nothing.
        """
        assert _fitting_font_size(["ElectricityMeter", "12,345"], 0.004, 0.004, 320.0, 240.0) is None
        assert _fitting_font_size([], 0.5, 0.5, 320.0, 240.0) is None
        assert _fitting_font_size(["PV"], 0.5, 0.5, 320.0, 240.0) is not None

    def lane_labels(self, events, horizon=20, years_per_character=0.25):
        """Draws one lane on a throwaway axis and reads back its labels' anchors.

        Returns:
            `[(text, x, alignment)]` in the order the lane drew them.
        """
        with _figure() as (_figure_object, axes):
            _draw_lane_events(axes[0], 0.0, events, "#2a78d6", horizon, years_per_character)
            return [
                (text.get_text(), text.get_position()[0], text.get_horizontalalignment())
                for text in axes[0].texts
            ]

    def test_two_labels_that_would_overlap_are_stacked_not_overprinted(self):
        """A label claims its text width, not just its year, or wide neighbours collide.

        Two events one year apart with long labels overlap horizontally however far apart their
        markers are, so the second one goes down a row. The y positions are what carries that,
        and they are asserted rather than the x, which is unchanged by the rule.
        """
        events = [(2, "replacement HeatPump", 12000.0), (3, "replacement Windows", 8000.0)]

        with _figure() as (_figure_object, axes):
            _draw_lane_events(axes[0], 0.0, events, "#2a78d6", 20, 0.25)
            rows = sorted(text.get_position()[1] for text in axes[0].texts)

        assert len(rows) == 2 and rows[0] < rows[1], "the second label has to drop a row"

    def test_a_lane_that_runs_out_of_rows_says_how_many_it_dropped(self):
        """The "+N more" line is the chart degrading by naming what it left out.

        Beyond three stacked rows a lane stops printing labels: the alternative is a wall of text
        over the spans. What it may not do is drop them silently, so the count is printed at the
        last hidden event.
        """
        events = [(year, "replacement HeatPump", 12000.0) for year in range(2, 9)]

        labels = self.lane_labels(events)

        drawn = [text for text, _x, _alignment in labels]
        assert drawn.count("replacement HeatPump 12,000") == 3
        assert drawn[-1] == "+4 more"

    def test_a_label_at_the_horizon_is_flipped_to_the_left_of_its_marker(self):
        """The residual credit lives at the horizon, and cutting its label off is not an option."""
        labels = self.lane_labels([(20, "residual", 4000.0)])

        assert labels[0][2] == "right"
        assert labels[0][1] < 20

    def test_a_flipped_label_is_clamped_at_the_left_edge_instead_of_running_off_it(self):
        """The other edge of the same rule: a wide label on an early event has nowhere to flip to.

        A one-year horizon puts every event at the right edge, so the label flips — and a label
        wider than the whole axis then started outside the frame, where `savefig` simply cut it
        off. It is clamped to the left edge instead, which is the only choice that keeps the text
        in the image.
        """
        left_edge = -_SwimlaneStyle.RIGHT_MARGIN_IN_YEARS

        labels = self.lane_labels([(1, "residual value of everything", 4000.0)], horizon=1)

        text, x, alignment = labels[0]
        assert alignment == "right"
        assert x - len(text) * 0.25 >= left_edge - 1e-9, "the label starts inside the frame"


class TestHeatmapCaption:
    """The audit figure's caption states what the picture cannot show by itself."""

    def test_a_matrix_too_large_to_annotate_says_so(self):
        """Catches an unannotated cell being read as a cell whose amount was too small to print.

        Above `MAX_ANNOTATED_CELLS` the per-cell euros are dropped and the colour bar carries the
        reading alone. That is the right call at that size and invisible at any size: nothing in
        the image distinguishes "no number here" from "nothing happened here", so the caption says
        which of the two it is.
        """
        cells = _HeatmapStyle.MAX_ANNOTATED_CELLS + 1

        caption = " ".join(_heatmap_caption_lines(3, cells))

        assert f"Per-cell euros omitted ({cells} cells)" in caption
        assert "colour bar" in caption

    def test_an_annotated_matrix_does_not_mention_omitted_numbers(self):
        """The sentence appears only when it is true; the small case is the ordinary one."""
        caption = " ".join(_heatmap_caption_lines(3, _HeatmapStyle.MAX_ANNOTATED_CELLS))

        assert "omitted" not in caption
        assert "3 categor(ies) carried no flows" in caption
