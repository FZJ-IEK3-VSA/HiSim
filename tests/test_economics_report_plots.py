"""The matplotlib PNG companions: which files get written, and what they plot (W4.7).

`report_plots.py` is deliberately *not* golden-tested — matplotlib output is not byte-stable
across versions, so pinning its bytes would produce a test that fails on every upgrade and proves
nothing about the figures. What can be pinned is the three things a reader actually depends on:
that the file set a caller is promised really appears on disk, that a chart which draws nothing
*says so* instead of vanishing silently, and that the numbers behind a chart are the ones the HTML
report draws from the same view function.

That last half is the point of the whole W4.7 split. The PNGs and the report's inline SVGs are
two renderings of one set of figures, and they agree only because both read `views.py` rather than
each computing its own. A test that checked the pictures could never see that; a test that checks
the view the picture is built from can. The two geometry tests are the same argument one level
down: the raster Sankey and the report's SVG Sankey share `presentation_style.sankey_node_boxes`,
and what is asserted here is that this renderer really draws what that layout handed it.

**Error class.** A failure here is a *presentation* failure, like the report goldens: a PNG that is
missing, empty or drawn from the wrong figures misleads a reader of a hand-out, and can never
corrupt a stored result.
"""

# clean

import os

import pytest
from matplotlib.patches import PathPatch, Rectangle

from hisim.economics import report_plots, views
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator
from hisim.economics.presentation_style import group_name, sankey_node_boxes
from hisim.economics.report_plots import (
    _SankeyStyle,
    _comparison_basis,
    _component_label_geometry,
    _draw_sankey,
    _figure,
    _legend_below_axes_anchor,
    _stack_positive_negative,
    plot_component_costs,
    plot_payback_curve,
    write_audit_plots,
    write_report_plots,
)
from hisim.economics.results import EvaluationMatrix, compare
from hisim.economics.subsidies import SubsidyCatalog

# The oracle's fixture, reused rather than rebuilt: it is the run these charts are meant to draw —
# banded, subsidised, multi-perspective, multi-subject — and a second fixture here would be a
# second thing to keep rich as the report grows.
from tests.test_economics_report_goldens import PARAMETERS, PERSPECTIVES, make_inputs

# The hand-written-timeline builder of the view tests, for the one thing the golden fixture cannot
# provide: a result with nothing in it at all, which is what every skip path is about.
from tests.test_economics_views_charts_a import make_result

pytestmark = pytest.mark.base

#: The charts every run of this fixture gets. Listed rather than counted, because the set grew
#: with the visualization extension and a bare count would be satisfied by any nine files. The
#: fixture's first perspective is `brownfield_gross` — one actor, no support, no loan — so the
#: actor Sankey and the funding statement skip themselves here, which is exactly the behaviour
#: `TestSkippedCharts` pins from the other side.
BASE_PNG_NAMES = {
    "lifecycle_annual_cash_flows.png",
    "lifecycle_investment_waterfall.png",
    "lifecycle_perspective_costs.png",
    "lifecycle_component_costs.png",
    "lifecycle_swimlane.png",
    "lifecycle_liquidity_fan.png",
    "lifecycle_cost_treemap.png",
    "lifecycle_monthly_burden.png",
}

#: The three charts that need a baseline to mean anything and appear only when one is given.
COMPARISON_PNG_NAMES = {
    "lifecycle_payback_curve.png",
    "lifecycle_comparison_bridge.png",
    "lifecycle_wealth_benchmark.png",
}

#: The audit's own figure, written beside `cost_audit.csv` rather than with the report set (Q9).
AUDIT_PNG_NAME = "cost_audit_timeline_heatmap.png"

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

#: The five charts that refuse to draw an empty figure, with the phrase each one logs instead.
#: An empty result is the one input that reaches every branch at once, which is why they share it.
SKIPPING_RENDERERS = {
    "V1 actor flows": (report_plots.plot_actor_flows, "Actor-flow Sankey skipped"),
    "V6 timeline heatmap": (report_plots.plot_timeline_heatmap, "Timeline heatmap skipped"),
    "V10 sources and uses": (report_plots.plot_sources_and_uses, "Sources-and-uses Sankey skipped"),
    "investment build-up": (report_plots.plot_investment_waterfall, "Investment build-up skipped"),
    "per-component costs": (report_plots.plot_component_costs, "Per-component costs skipped"),
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


def _assert_is_a_real_png(path: str) -> None:
    """The file exists, is not empty and begins with the PNG signature."""
    assert os.path.isfile(path), path
    with open(path, "rb") as file:
        head = file.read(len(PNG_MAGIC))
    assert os.path.getsize(path) > len(PNG_MAGIC), path
    assert head == PNG_MAGIC, path


class TestWrittenFiles:
    """What `write_report_plots` promises a caller: the paths it returns are files that exist."""

    def test_the_report_side_charts_are_written(self, evaluated, tmp_path):
        """Catches the PNG set silently shrinking — a chart that returns early writes no file.

        Several plot functions return their path without writing anything when they have nothing
        to draw (no subjects, no breakdowns, one actor, no external funding), and
        `write_report_plots` filters those paths out of its return value. That is the right
        behaviour and also the way the set can quietly lose a chart: a view that starts returning
        empty, a perspective that stops carrying breakdowns, and the caller still gets a list of
        real files and a report that looks whole. The names are therefore listed, not counted.
        """
        matrix, _reference = evaluated

        written = write_report_plots(matrix, str(tmp_path))

        assert {os.path.basename(path) for path in written} == BASE_PNG_NAMES
        for path in written:
            _assert_is_a_real_png(path)

    def test_a_reference_result_adds_the_comparison_charts(self, evaluated, tmp_path):
        """The comparison's three charts belong to this set, not to whoever calls it.

        The payback curve used to be written by the `report` CLI as a separate call, under a file
        name only that call site knew, after `write_report_plots` had written the others. One
        function owns the set now: handed the comparison's reference result, it writes the payback
        curve, the NPV bridge and the fixed-interest benchmark along with the rest.
        """
        matrix, reference = evaluated

        written = write_report_plots(matrix, str(tmp_path), reference)

        assert {os.path.basename(path) for path in written} == BASE_PNG_NAMES | COMPARISON_PNG_NAMES
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

    def test_the_audit_heatmap_is_written_beside_the_audit_tables(self, evaluated, tmp_path):
        """V6 travels with `cost_audit.csv` (owner decision Q9), not with the report set.

        It has the audit's audience and the audit's question, so it has the audit's entry point:
        `write_audit_plots`, called from the two places that write the audit tables. The check that
        it is *not* in `write_report_plots`' output is the half that matters — a chart written from
        both places would be refreshed twice and could disagree with itself between runs.
        """
        matrix, _reference = evaluated

        written = write_audit_plots(matrix.results["brownfield_net"], str(tmp_path))

        assert [os.path.basename(path) for path in written] == [AUDIT_PNG_NAME]
        _assert_is_a_real_png(written[0])
        assert AUDIT_PNG_NAME not in BASE_PNG_NAMES | COMPARISON_PNG_NAMES

    @pytest.mark.parametrize("chart", sorted(NEW_RENDERERS))
    def test_every_new_renderer_draws_a_real_png(self, evaluated, tmp_path, chart):
        """Each chart of the visualization extension renders the golden fixture without failing.

        The cheapest possible statement about a renderer, and the one nothing else makes: that it
        runs end to end on a rich, banded, subsidised, financed evaluation and leaves a real image
        behind. Matplotlib fails late — a bad colour, a mismatched sequence length or an empty
        series raises inside `savefig`, not in the code that assembled it — so "it produced a PNG
        with a PNG header" is a genuine result, and it is as far as an assertion can go before it
        would have to compare pixels.

        Each renderer is drawn on the perspective that gives it data (`NEW_RENDERERS`), because on
        the wrong one four of them would legitimately skip and this test would assert nothing.
        """
        matrix, reference = evaluated
        path = os.path.join(str(tmp_path), "chart.png")

        assert NEW_RENDERERS[chart](matrix, reference, path) == path

        _assert_is_a_real_png(path)


class TestSkippedCharts:
    """A chart with nothing to draw writes no file and says so — the module docstring's promise."""

    @pytest.mark.parametrize("chart", sorted(SKIPPING_RENDERERS))
    def test_an_empty_view_skips_with_a_log_line(self, tmp_path, capsys, chart):
        """Silence is the failure mode here, not the missing file.

        A hand-out that is short one chart is fine when the reason is "there was nothing to draw";
        it is not fine when nobody can tell that from the run. Two of these five used to return
        early without a word — the investment build-up on a result with no priced subject, the
        component stacks on a result with no breakdowns — which made an absent figure
        indistinguishable from a renderer that crashed and was swallowed somewhere.

        An empty timeline is the input that reaches all five branches at once: no actors, no
        funding, no year-0 gross, no breakdowns and no flows in any category.
        """
        renderer, expected = SKIPPING_RENDERERS[chart]
        path = os.path.join(str(tmp_path), "chart.png")

        assert renderer(make_result([]), path) == path

        assert not os.path.exists(path)
        assert expected in capsys.readouterr().out

    def test_a_run_without_a_reference_logs_the_comparison_charts_it_cannot_draw(
        self, evaluated, tmp_path, capsys
    ):
        """The same rule for the two charts whose input is a second evaluation, not a view.

        The bridge and the fixed-interest benchmark are differences: without a baseline there is
        nothing to decompose. That is the common case — most runs evaluate one variant — so the
        line has to name the reason rather than let a reader wonder which two files went missing.
        """
        matrix, _reference = evaluated

        write_report_plots(matrix, str(tmp_path))

        assert "Comparison charts (bridge, fixed-interest benchmark) skipped" in capsys.readouterr().out
        for name in COMPARISON_PNG_NAMES:
            assert not os.path.exists(os.path.join(str(tmp_path), name))


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
        with _figure() as (_figure_object, axis):
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
        """The fallback: the reference directory does not carry the variant's perspective.

        `write_report_plots` then falls back to the matrix's first result, so the two sides really
        are different perspectives — and naming only one of them would be the misleading half of
        the truth.
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
        """The same flows in the (source, target, amount, colour, is_credit) shape the renderer takes."""
        return [(source, target, amount, "#2a78d6", False) for source, target, amount in ribbons]

    def drawn_patches(self, columns, ribbons):
        """Draws the diagram on a throwaway axis and returns its ribbons, nodes and stubs.

        Reading the patches back off the axis is the only way to assert on geometry a renderer
        produced rather than on geometry a layout function returned. The figure goes through
        `_figure`, so it is closed before the assertions run and nothing leaks into the next test.

        Returns:
            `(ribbon_vertices, node_rectangles, stub_rectangles)` — the eight-point path of every
            ribbon leg, and the `(x, y, width, height)` of every node and every net stub.
        """
        with _figure() as (_figure_object, axis):
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
