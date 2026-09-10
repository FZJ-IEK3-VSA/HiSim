"""Renderer tests for the first half of the visualization set (visualization spec §5, rule 2.7).

Two halves, matching the two things that can go wrong once a view's numbers are correct. The
first half parses the SVG the shared chart builders emit and checks the geometry claims the spec
makes about it — that a Sankey ribbon keeps one width from end to end, that the ribbons on a node
face tile it exactly, that a column-skipping ribbon travels a corridor instead of crossing a
block, and that a node whose faces differ closes the deficient one with a labelled stub. Those
claims were all *false* in an earlier draft in a way that was invisible everywhere except in the
emitted file, which is why they are asserted on the file rather than on the layout's own numbers
(`tests/test_economics_layouts_prose.py` covers the layout side).

The second half renders the sections built on those builders and checks that each one opens the
way every section of this report opens — the four authored parts, or the one-line cross-reference
that replaces them at a repeat occurrence — and that the table of contents reaches every section
that rendered. Wording is not asserted here: it is owner-authored prose held in `report_prose.py`
and byte-compared by `tests/test_economics_report_goldens.py`.

**What a failure means.** A *presentation* failure: something a reader sees changed. It says
nothing about whether the numbers are right — `tests/test_economics_views_charts_a.py` owns that —
and a bug caught here can mislead a reader but can never corrupt a stored result.
"""

# clean

import inspect
import re

import pytest

from hisim.economics import views
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.financing import FinancingPlan
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import (
    ActorScope,
    InstallationContext,
    Perspective,
    SubsidyMode,
)
from hisim.economics.plausibility import run_plausibility_checks
from hisim.economics.presentation_style import sankey_node_boxes
from hisim.economics.report_prose import ReportProse
from hisim.economics.reporting import ReportSections, build_lifecycle_report_html
from hisim.economics.reporting.charts import (
    _attribution_tornado_svg,
    _bridge_svg,
    _cost_of_credit_svg,
    _declutter_labels,
    _gantt_svg,
    _sankey_svg,
    _xy_lines_svg,
)
from hisim.economics.reporting import assembly, sections_charts
from hisim.economics.reporting.scaffold import (
    ReportChapters,
    SkippedSection,
    _ChapterContext,
    _not_drawn_html,
)
from hisim.economics.report_prose import payback_interval_sentence
from hisim.economics.reporting.sections_charts import (
    _effective_rate_text,
    _first_result_where,
    _has_year_zero_funding,
    _liquidity_section_html,
)
from hisim.economics.results import EvaluationMatrix, compare
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

from tests.economics_report_test_helpers import (
    back_link_target,
    opens_with_four_parts,
    rects,
    rendered_sections,
)

pytestmark = pytest.mark.base


# ------------------------------------------------------------------ parsing what was emitted


def _ribbon_paths(svg: str):
    """Every ribbon path of an inline-SVG Sankey as its sixteen raw path numbers.

    The renderer emits one `M ... C ... L ... C ... Z` per ribbon leg, which is exactly sixteen
    coordinates: the left face's two y at indices 1 and 15, the right face's two at 7 and 9, and
    the two x at 0 and 6. Parsing the file rather than trusting the renderer's own variables is
    the whole point — the defects these tests pin were visible only in the output.

    Args:
        svg: The rendered `<svg>` element.

    Returns:
        One list of sixteen floats per ribbon leg, in emission order.
    """
    curves = []
    for path in re.findall(r'<path d="(M [^"]+)"', svg):
        numbers = [float(value) for value in re.findall(r"-?\d+\.?\d*", path)]
        if len(numbers) == 16:
            curves.append(numbers)
    return curves


def _ribbon_widths(svg: str):
    """Every ribbon leg as `(left width, right width)` in user units."""
    return [(numbers[15] - numbers[1], numbers[9] - numbers[7]) for numbers in _ribbon_paths(svg)]


def _ribbon_faces(svg: str):
    """Every ribbon leg as `(left x, left y-top, right x, right y-top, width)`."""
    return [
        (numbers[0], numbers[1], numbers[6], numbers[7], numbers[15] - numbers[1])
        for numbers in _ribbon_paths(svg)
    ]


def _horizontal_rules(svg: str):
    """Every horizontal `<line>` of an inline SVG as `(x1, x2)`, in emission order.

    A whisker is the only horizontal rule the bridge draws — its zero axis and its step cursors
    are vertical — so "which lines are horizontal" is a geometric way of saying "which lines are
    whiskers", without asserting on the colour and stroke width they happen to be drawn with.
    """
    return [
        (float(x1), float(x2))
        for x1, y1, x2, y2 in re.findall(
            r'<line x1="(-?[\d.]+)" y1="(-?[\d.]+)" x2="(-?[\d.]+)" y2="(-?[\d.]+)"', svg
        )
        if y1 == y2
    ]


def _stub_rects(svg: str):
    """The net-position stubs as `(x, y, width, height)`; they carry a class the nodes do not."""
    return [
        (float(x), float(y), float(w), float(h))
        for x, y, w, h in re.findall(
            r'<rect class="net-stub" x="(-?[\d.]+)" y="(-?[\d.]+)" width="([\d.]+)" height="([\d.]+)"',
            svg,
        )
    ]


def _bezier(points, position: float):
    """A point on a cubic Bezier — the actual curve the browser draws, not the chord."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
    rest = 1.0 - position
    return (
        rest ** 3 * x0 + 3 * rest ** 2 * position * x1
        + 3 * rest * position ** 2 * x2 + position ** 3 * x3,
        rest ** 3 * y0 + 3 * rest ** 2 * position * y1
        + 3 * rest * position ** 2 * y2 + position ** 3 * y3,
    )


def _coloured(ribbons, color: str = "#2a78d6", is_credit: bool = False):
    """The `(source, target, amount)` triples in the five-tuple shape the renderer takes."""
    return [(source, target, amount, color, is_credit) for source, target, amount in ribbons]


TANGLED_COLUMNS = [["bank", "state", "market"], ["landlord", "tenant"], ["fees", "energy", "works"]]
#: Every source feeds the actor whose listed position is furthest from its own, so the naive
#: layout crosses on both sides; the middle column carries each unit twice, which is exactly the
#: situation that produced two different per-column scales before rule 2.7.
TANGLED_RIBBONS = [
    ("bank", "tenant", 400.0),
    ("state", "tenant", 300.0),
    ("market", "landlord", 500.0),
    ("landlord", "energy", 200.0),
    ("landlord", "fees", 300.0),
    ("tenant", "works", 500.0),
    ("tenant", "energy", 200.0),
]

SKIPPING_COLUMNS = [["bank", "state"], ["tenant"], ["landlord"], ["market", "suppliers"]]
#: Two ribbons skip a column and the landlord takes in 1,400 while paying out 700, so this
#: diagram exercises both the corridor routing and the net-position stub.
SKIPPING_RIBBONS = [
    ("bank", "landlord", 600.0),
    ("state", "landlord", 300.0),
    ("tenant", "landlord", 500.0),
    ("tenant", "suppliers", 200.0),
    ("landlord", "market", 700.0),
]


class TestSankeySvg:
    """Rule 2.7 and Q29 R7, asserted on what the renderer emits rather than on the layout."""

    def test_ribbons_keep_one_width_end_to_end(self):
        """A ribbon is one flow; the two ends of its path must measure the same."""
        svg = _sankey_svg(TANGLED_COLUMNS, _coloured(TANGLED_RIBBONS), {})
        widths = _ribbon_widths(svg)
        assert len(widths) == len(TANGLED_RIBBONS)
        for left, right in widths:
            assert left == pytest.approx(right, abs=0.2)

    def test_ribbons_tile_each_node_face(self):
        """The ribbons on a node's fuller face fill its rectangle exactly, with no overflow."""
        svg = _sankey_svg(TANGLED_COLUMNS, _coloured(TANGLED_RIBBONS), {})
        faces = _ribbon_faces(svg)
        for x, y, width, height in rects(svg):
            outgoing = sum(
                band for left_x, left_y, _rx, _ry, band in faces
                if abs(left_x - (x + width)) < 0.5 and y - 0.5 <= left_y + band / 2 <= y + height + 0.5
            )
            incoming = sum(
                band for _lx, _ly, right_x, right_y, band in faces
                if abs(right_x - x) < 0.5 and y - 0.5 <= right_y + band / 2 <= y + height + 0.5
            )
            assert max(outgoing, incoming) == pytest.approx(height, abs=0.3)

    def test_no_ribbon_path_intersects_a_node_rectangle(self):
        """The Q29 corridor invariant, sampled off the actual Bezier the report emits."""
        svg = _sankey_svg(SKIPPING_COLUMNS, _coloured(SKIPPING_RIBBONS), {})
        nodes = rects(svg)
        assert nodes
        hits = []
        for numbers in _ribbon_paths(svg):
            top = [(numbers[0], numbers[1]), (numbers[2], numbers[3]),
                   (numbers[4], numbers[5]), (numbers[6], numbers[7])]
            band = numbers[15] - numbers[1]
            for step in range(201):
                x, y = _bezier(top, step / 200.0)
                for node_x, node_y, node_w, node_h in nodes:
                    inside_x = node_x + 0.05 < x < node_x + node_w - 0.05
                    inside_y = node_y - 0.05 < y + band / 2.0 < node_y + node_h + 0.05
                    if inside_x and inside_y:
                        hits.append((x, y))
        assert not hits, f"{len(hits)} sampled ribbon point(s) inside a node rectangle"

    def test_both_faces_of_every_node_tile_at_full_height(self):
        """Q29 R7: with the net stub counted, no face of any node is left partly empty."""
        svg = _sankey_svg(SKIPPING_COLUMNS, _coloured(SKIPPING_RIBBONS), {})
        faces = _ribbon_faces(svg)
        stubs = _stub_rects(svg)
        assert stubs, "the landlord receives 1,400 and pays out 700; that gap must be drawn"
        for x, y, width, height in rects(svg):
            outgoing = sum(
                band for left_x, left_y, _rx, _ry, band in faces
                if abs(left_x - (x + width)) < 0.5 and y - 0.5 <= left_y + band / 2 <= y + height + 0.5
            ) + sum(stub_h for stub_x, _sy, _sw, stub_h in stubs if abs(stub_x - (x + width)) < 0.5)
            incoming = sum(
                band for _lx, _ly, right_x, right_y, band in faces
                if abs(right_x - x) < 0.5 and y - 0.5 <= right_y + band / 2 <= y + height + 0.5
            ) + sum(
                stub_h for stub_x, _sy, stub_w, stub_h in stubs if abs(stub_x + stub_w - x) < 0.5
            )
            for used in (outgoing, incoming):
                if used > 0.5:  # a first-column source has no incoming face at all
                    assert used == pytest.approx(height, abs=0.3)

    def test_the_stub_carries_the_callers_own_wording_for_the_net(self):
        """A section that publishes the net under its own sign convention must not be contradicted."""
        geometry = sankey_node_boxes(SKIPPING_COLUMNS, SKIPPING_RIBBONS)
        stubs = {stub.node: stub for stub in geometry.net_stubs}
        assert set(stubs) == {"landlord"}
        assert stubs["landlord"].amount == pytest.approx(1400.0 - 700.0)
        svg = _sankey_svg(
            SKIPPING_COLUMNS, _coloured(SKIPPING_RIBBONS), {},
            stub_labels={"landlord": "net -700 EUR"},
        )
        assert "net -700 EUR" in svg
        assert "net +700 EUR" not in svg

    def test_a_credit_ribbon_is_drawn_structurally_not_by_colour(self):
        """A ribbon has no sign, so cost and credit must differ in more than hue."""
        cost = _sankey_svg(TANGLED_COLUMNS, _coloured(TANGLED_RIBBONS), {})
        credit = _sankey_svg(TANGLED_COLUMNS, _coloured(TANGLED_RIBBONS, is_credit=True), {})
        assert 'stroke-dasharray="4 3"' in credit
        assert 'stroke-dasharray="4 3"' not in cost
        assert 'fill-opacity="0.18"' in credit and 'fill-opacity="0.5"' in cost

    def test_a_transfer_is_an_ordinary_ribbon_between_distinct_columns(self):
        """Q23: each party has its own column, so a levy travels left to right like any flow."""
        columns = [["market"], ["tenant"], ["landlord"], ["works"]]
        ribbons = [
            ("market", "landlord", 1000.0),
            ("tenant", "landlord", 400.0),
            ("landlord", "works", 1400.0),
        ]
        svg = _sankey_svg(columns, _coloured(ribbons), {})
        for left, right in _ribbon_widths(svg):
            assert left == pytest.approx(right, abs=0.2)
        for left_x, _left_y, right_x, _right_y, _width in _ribbon_faces(svg):
            assert right_x > left_x

    def test_an_amount_line_degrades_to_the_total_on_a_short_node(self):
        """Q28 R6: a small node may lose the split, never the number."""
        columns = [["big", "small"], ["sink"]]
        ribbons = [("big", "sink", 1000.0), ("small", "sink", 1.0)]
        svg = _sankey_svg(
            columns, _coloured(ribbons), {"big": "big", "small": "small", "sink": "sink"},
            sublabels={"big": ("costs 1,000 | credits -0", "1,000"),
                       "small": ("costs 1 | credits -0", "1")},
        )
        assert "costs 1,000 | credits -0" in svg  # tall enough for two baselines
        assert "costs 1 | credits -0" not in svg  # degraded to the compact form
        assert ">1<" in svg

    def test_a_ribbon_naming_a_node_no_column_declares_is_refused(self):
        """A path anchored at nothing is never drawn, because the layout refuses to lay it out.

        The renderer used to drop such a ribbon quietly, which left its amount counted into the
        height of the node it left and so into a rectangle the drawn ribbons could not tile.
        `sankey_node_boxes` now refuses the input instead, naming the node and the ribbon, so the
        chart cannot silently under-draw a flow it was given.
        """
        with pytest.raises(ValueError, match="which no column declares"):
            _sankey_svg([["a"], ["b"]], _coloured([("a", "b", 100.0), ("a", "ghost", 50.0)]), {})

    def test_the_node_width_is_the_layouts_own_fraction_of_the_plot(self):
        """Both renderers scale the node by `SankeyLayout.NODE_WIDTH`, so neither may re-guess it.

        The expectation is the literal 20.3 user units rather than the same product the renderer
        computes: a test that multiplies `NODE_WIDTH` by the plot width agrees with the renderer
        by construction and would keep agreeing if both moved together, which is exactly the
        change a reader of the report would notice first.
        """
        svg = _sankey_svg(TANGLED_COLUMNS, _coloured(TANGLED_RIBBONS), {})
        widths = {round(width, 1) for _x, _y, width, _h in rects(svg)}
        assert len(widths) == 1
        assert widths.pop() == pytest.approx(20.3, abs=0.1)  # 3.5 % of the 580-unit plot area


class TestXyLinesAndLabels:
    """The line chart the cash curve and the loan balance share."""

    def test_a_band_becomes_one_filled_polygon_under_the_lines(self):
        """The envelope is a single polygon, drawn before the line so it cannot cover it."""
        svg = _xy_lines_svg(
            series=[("best estimate", [(0, 0.0), (1, 10.0), (2, 20.0)], "var(--g0)", 2.0, "")],
            bands=[([(0, 0.0), (1, 8.0), (2, 16.0)], [(0, 0.0), (1, 12.0), (2, 24.0)], "var(--g0)")],
        )
        assert svg.count("<polygon") == 1
        assert svg.index("<polygon") < svg.index("<polyline")

    def test_the_axis_always_includes_zero_so_a_sign_is_readable(self):
        """An all-negative series still gets its zero line, or the curve reads as a positive one."""
        svg = _xy_lines_svg(series=[("s", [(0, -10.0), (1, -20.0)], "var(--g0)", 2.0, "")])
        assert ">0<" in svg
        assert "<line" in svg  # the zero rule itself

    def test_an_empty_series_draws_nothing_rather_than_an_empty_frame(self):
        """A section with no data skips its chart; an axis with no curve would be worse."""
        assert _xy_lines_svg(series=[("s", [], "var(--g0)", 2.0, "")]) == ""

    def test_a_dash_pattern_reaches_the_polyline(self):
        """The debt-service line is dashed against the solid balance; that has to survive."""
        svg = _xy_lines_svg(series=[("s", [(0, 1.0), (1, 2.0)], "var(--g5)", 1.6, "5 4")])
        assert 'stroke-dasharray="5 4"' in svg

    def test_labels_are_pushed_apart_rather_than_dropped(self):
        """Three lines converging at the horizon must still be tellable apart."""
        placed = _declutter_labels([(10.0, 100.0, "a"), (10.0, 102.0, "b"), (10.0, 103.0, "c")])
        assert [label for _x, _y, label in placed] == ["a", "b", "c"]
        heights = [y for _x, y, _label in placed]
        assert all(later - earlier >= 11.0 for earlier, later in zip(heights, heights[1:]))

    def test_decluttering_never_moves_a_label_horizontally(self):
        """A label that moved off its line's end point would stop naming that line."""
        placed = _declutter_labels([(10.0, 100.0, "a"), (30.0, 100.5, "b")])
        assert sorted(x for x, _y, _label in placed) == [10.0, 30.0]


class TestGanttBridgeAndTornado:
    """The three row-based builders, which all lay their rows out with `_bar_row`."""

    def test_a_gantt_row_carries_its_span_its_event_and_its_tooltip(self):
        """Every event keeps a marker and a tooltip even where its label is suppressed."""
        svg = _gantt_svg(
            [("HeatPump", [(0, 18, "in service")],
              [(0, "purchase", 20000.0), (1, "replacement", 500.0), (18, "residual", -5000.0)],
              "var(--g4)")],
            20,
        )
        assert "in service (0-18)" in svg
        assert svg.count("<title>year") == 3  # every event keeps its tooltip
        assert "purchase 20,000" in svg
        assert "replacement 500" not in svg  # within the cluster window of year 0
        assert "residual -5,000" in svg

    def test_an_open_ended_span_runs_to_the_horizon(self):
        """`None` means "still in service at the horizon", not "zero length"."""
        svg = _gantt_svg([("HeatPump", [(5, None, "in service")], [], "var(--g4)")], 20)
        assert "in service (5-20)" in svg

    def test_no_lane_means_no_chart(self):
        """A carriers-only perspective skips the strip rather than drawing an empty axis."""
        assert _gantt_svg([], 20) == ""

    def test_the_bridge_anchors_carry_bands_and_the_steps_do_not(self):
        """The band of a difference is not the difference of the bands, so deltas get no whisker.

        Asserted as geometry: exactly two horizontal rules — the anchors' whiskers, the step
        cursors being vertical — and each one has to *straddle* its own anchor, running from that
        anchor's minimum to its maximum with the best estimate strictly inside. A count of a
        stroke string would go green for a whisker drawn in the right colour at the wrong place,
        which is the failure this chart is actually prone to.
        """
        anchors = (
            ("reference: base", UncertainValue(1000.0, 800.0, 1200.0)),
            ("variant: measures", UncertainValue(600.0, 500.0, 800.0)),
        )
        svg = _bridge_svg(anchors, [("Investment", 400.0, "var(--g0)"),
                                    ("Energy", -800.0, "var(--g1)")])
        whiskers = _horizontal_rules(svg)
        assert len(whiskers) == 2  # the two anchors; the delta steps carry none

        def to_x(value: float) -> float:
            """Euro to user units: the axis spans 0..1,400 over the 580-unit plot area."""
            return 150 + value * (580 / 1400)

        for (_label, band), (x1, x2) in zip(anchors, whiskers):
            assert x1 == pytest.approx(to_x(band.minimum), abs=0.1)
            assert x2 == pytest.approx(to_x(band.maximum), abs=0.1)
            assert x1 < to_x(band.best_estimate) < x2
        assert "+400" in svg and "-800" in svg
        assert "1,000 [800 | 1,200]" in svg

    def test_the_bridge_axis_covers_the_running_total_not_just_the_anchors(self):
        """A step that overshoots both anchors still has to be on the canvas."""
        svg = _bridge_svg(
            (("base", UncertainValue.exact(100.0)), ("variant", UncertainValue.exact(120.0))),
            [("huge", 5000.0, "var(--g0)"), ("back", -4980.0, "var(--g1)")],
        )
        width = 860 - 150 - 130
        for numbers in re.findall(r'<rect x="(-?[\d.]+)" y="-?[\d.]+" width="([\d.]+)"', svg):
            start, bar_width = float(numbers[0]), float(numbers[1])
            assert start >= 150 - 0.5
            assert start + bar_width <= 150 + width + 0.5

    def test_the_tornado_puts_a_mirrored_subject_wholly_on_one_side(self):
        """A revenue subject can have a positive LOW delta; the chart must not hide that."""
        rows = [
            views.AttributionRow("FeedIn", -1000.0, 200.0, 400.0),
            views.AttributionRow("HeatPump", 20000.0, -3000.0, 4000.0),
        ]
        svg = _attribution_tornado_svg(rows, UncertainValue(19000.0, 16200.0, 23400.0))
        zero_x = 150 + (860 - 150 - 130) / 2
        starts = [float(x) for x in re.findall(r'<rect x="(-?[\d.]+)"', svg)]
        assert min(starts) < zero_x  # the heat pump's LOW bar runs left
        assert all(start >= zero_x - 0.001 for start in starts[:2])  # both FeedIn bars run right
        assert "total band 19,000 [16,200 | 23,400]" in svg

    def test_no_attribution_row_means_no_tornado(self):
        """A degenerate band has no width to attribute, so there is nothing to draw."""
        assert _attribution_tornado_svg([], UncertainValue.exact(0.0)) == ""

    def test_the_cost_of_credit_bar_stacks_to_what_is_repaid(self):
        """The consumer-credit disclosure: the segments have to add up to the stated total."""
        credit = views.TotalCostOfCredit(
            principal_in_euro=50000.0, interest_in_euro=12000.0, fees_in_euro=1400.0,
            grants_in_euro=5000.0, unrepaid_principal_in_euro=0.0, effective_annual_rate=0.041,
        )
        assert credit.total_repaid_in_euro == pytest.approx(63400.0)
        svg = _cost_of_credit_svg(credit)
        assert "total 63,400 EUR" in svg
        assert "principal 50,000" in svg and "interest 12,000" in svg
        assert "-5,000 EUR" in svg  # the grant comes back, on its own row

    def test_a_cash_purchase_draws_no_credit_bar(self):
        """Nothing was repaid, so there is no disclosure to make."""
        credit = views.TotalCostOfCredit(
            principal_in_euro=0.0, interest_in_euro=0.0, fees_in_euro=0.0, grants_in_euro=0.0,
            unrepaid_principal_in_euro=0.0, effective_annual_rate=None,
        )
        assert _cost_of_credit_svg(credit) == ""

    def test_a_loan_without_a_rate_prints_the_reason_it_has_none(self):
        """A bare "n/a" reads as a gap in the report; the view's reason says it is not one."""
        truncated = views.TotalCostOfCredit(
            principal_in_euro=50000.0, interest_in_euro=8000.0, fees_in_euro=0.0,
            grants_in_euro=0.0, unrepaid_principal_in_euro=30000.0, effective_annual_rate=None,
            effective_annual_rate_note="the loan term reaches past the observation horizon",
        )
        assert _effective_rate_text(truncated) == (
            "n/a (the loan term reaches past the observation horizon)"
        )
        assert _effective_rate_text(
            views.TotalCostOfCredit(
                principal_in_euro=0.0, interest_in_euro=0.0, fees_in_euro=0.0, grants_in_euro=0.0,
                unrepaid_principal_in_euro=0.0, effective_annual_rate=None,
            )
        ) == "n/a"  # no loan at all: there is no reason to give
        assert _effective_rate_text(
            views.TotalCostOfCredit(
                principal_in_euro=50000.0, interest_in_euro=12000.0, fees_in_euro=0.0,
                grants_in_euro=0.0, unrepaid_principal_in_euro=0.0, effective_annual_rate=0.041,
            )
        ) == "4.10%"


# ---------------------------------------------------------------- the sections built on them


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database; module-scoped because validating it dominates the runtime."""
    return CostDatabase()


def make_inputs(energy_kwh: float = 5000.0, investment: float = 16000.0) -> EvaluationInputs:
    """A banded heat pump plus bought electricity — the smallest input set that fills a report.

    The tenancy fields are here because half the sections under test only exist when money
    crosses an actor boundary: without a cold rent and a heated area the DE_2024 ruleset books no
    modernization levy, and the landlord statement and the who-pays-whom diagram then have
    nothing to say.
    """
    facts = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue(investment, investment * 0.8, investment * 1.3),
        lifetime_override_in_years=18.0,
        override_source="test",
    )
    return EvaluationInputs(
        simulation_year=2026,
        simulated_period_fraction=1.0,
        cost_facts=[SubjectCostFacts("HeatPump", facts)],
        billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=energy_kwh)],
        annual_heat_demand_in_kwh=15000.0,
        living_area_in_m2=150.0,
        heated_floor_area_in_m2=150.0,
        current_cold_rent_in_euro_per_m2_month=8.5,
    )


#: A fixed perspective set rather than the shipped bundle: the sections under test need a
#: *financed* view (the loan and the cost of credit) and both sides of an actor split (the
#: landlord statement, who pays whom), and a bundle data PR must not be able to remove them.
REPORT_PERSPECTIVES = [
    Perspective(id="gross", installation_context=InstallationContext.GREENFIELD,
                subsidy_mode=SubsidyMode.none()),
    Perspective(id="financed", installation_context=InstallationContext.GREENFIELD,
                subsidy_mode=SubsidyMode.none(),
                financing=FinancingPlan(financed_share=0.6, nominal_interest_rate=0.035,
                                        term_in_years=12)),
    Perspective(id="landlord", installation_context=InstallationContext.GREENFIELD,
                actor_scope=ActorScope.LANDLORD, subsidy_mode=SubsidyMode.none()),
    Perspective(id="tenant", installation_context=InstallationContext.GREENFIELD,
                actor_scope=ActorScope.TENANT, subsidy_mode=SubsidyMode.none()),
]


@pytest.fixture(name="report", scope="module")
def fixture_report(database) -> str:
    """The richest document these fixtures reach: every perspective, a comparison and a bridge."""
    evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
    matrix = EvaluationMatrix()
    for perspective in REPORT_PERSPECTIVES:
        matrix.results[perspective.id] = evaluator.evaluate(make_inputs(), perspective)
    reference = evaluator.evaluate(
        make_inputs(energy_kwh=15000.0, investment=2000.0), REPORT_PERSPECTIVES[0]
    )
    comparison = compare(reference, matrix.results["gross"], "base", "measures")
    return build_lifecycle_report_html(
        matrix, run_plausibility_checks(matrix), None, comparison, reference_result=reference
    )


class TestSectionExplanations:
    """Every section explains itself the same way, in the same four parts (rule 2.6).

    The report is read by people who did not build it and who arrive in the middle of it by
    following a contents link, so "explained somewhere" is not good enough: each section carries
    what it *shows*, what it *adds*, the terms it uses and how its numbers are calculated, in that
    order, directly under its heading. These tests pin the structure rather than the wording — the
    wording is owner-authored prose held in `ReportProse` and byte-compared by the golden oracle —
    because a section that renders three of the four parts is the failure mode a reviewer reading
    one section at a time would never notice.
    """

    def test_every_rendered_section_opens_with_the_four_parts(self, report):
        """No section may render its chart without the block that explains it — or a link to it.

        The two patterns are `tests/economics_report_test_helpers`', shared with the chapter file
        that asserts the same shape on a document with four chapters in it: two copies of the
        regex is how one of them comes to accept an opening the other rejects.
        """
        sections = rendered_sections(report)
        assert len(sections) > 15, "the fixture stopped reaching most sections"
        explained = set()
        for anchor, html in sections:
            if opens_with_four_parts(html):
                explained.add(anchor)
                continue
            assert back_link_target(html) is not None, anchor
        assert len(explained) > 15, "the four-part blocks disappeared entirely"

    def test_the_primer_renders_once_at_the_top(self, report):
        """The conventions every other section leans on are stated first, and only once."""
        anchors = [anchor for anchor, _html in rendered_sections(report)]
        assert anchors[0] == "building-how-to-read"
        assert anchors.count("building-how-to-read") == 1
        assert report.index(">How to read this report") > report.index("<nav")

    def test_the_rendered_prose_is_the_authored_prose(self, report):
        """The renderer marks the text up; it never edits it — in every chapter it rendered."""
        by_anchor = dict(rendered_sections(report))
        for chapter, _chapter_name in ReportChapters.ORDER:
            self._assert_prose_of_chapter(by_anchor, chapter)

    @staticmethod
    def _assert_prose_of_chapter(by_anchor, chapter: str) -> None:
        """Every section of one chapter carries `ReportProse`'s own markup, or links to it."""
        for anchor, name in ReportSections.ORDER:
            html = by_anchor.get(f"{chapter}-{anchor}")
            if html is None or "see the explanation under" in html:
                continue
            prose = ReportProse.for_section(name)
            assert f"<p class='sub'>{ReportProse.to_html(prose.shows)}</p>" in html, name
            assert f"<p class='sub'>{ReportProse.to_html(prose.adds)}</p>" in html, name
            for term, definition in prose.terms:
                assert f"<dt><em>{ReportProse.to_html(term)}</em></dt>" in html, term
                assert f"<dd>{ReportProse.to_html(definition)}</dd>" in html, term

    def test_every_section_of_the_order_has_authored_prose(self):
        """Including the sections this fixture cannot reach, and the one outside the report."""
        for _anchor, name in ReportSections.ORDER:
            prose = ReportProse.for_section(name)
            assert prose.shows and prose.adds and prose.terms and prose.calculation
        assert ReportProse.for_section(ReportProse.LEDGER_HEATMAP_SECTION_NAME).shows

    def test_anchors_are_unique_and_chapter_prefixed(self, report):
        """Two sections sharing an anchor would send every contents link to the same place.

        Uniqueness is the load-bearing half now that a section name can appear in more than one
        chapter: "Cash curve" under Owner-occupied and under Rented out are two different charts,
        and they are only two different destinations because the chapter is in the anchor.
        """
        anchors = [anchor for anchor, _html in rendered_sections(report)]
        assert len(anchors) == len(set(anchors))
        prefixes = tuple(f"{chapter}-" for chapter, _name in ReportChapters.ORDER)
        for anchor in anchors:
            assert anchor.startswith(prefixes), anchor


class TestVisualizationSectionsRender:
    """The sections of the first half of the chart set, on an ordinary evaluated run."""

    def test_the_chart_sections_are_present_and_name_their_perspective(self, report):
        """Each perspective-scoped section says which perspective it is showing.

        The presence of the anchors themselves is `tests/test_economics_report_goldens.py`'s
        job — it lists every one of them, chapter by chapter — so what is left here is the half
        that list cannot check: *which* perspective a section that had to choose one ended up
        drawing.
        """
        sections = dict(rendered_sections(report))
        assert "<h3>Cash curve (gross)" in sections["owner-cash-curve"]
        credit = sections["owner-cost-of-credit"]
        # One block per financed perspective, each naming itself; not the chapter's lead.
        assert "<b>financed</b>" in credit
        assert "<b>gross</b>" not in credit

    def test_the_cash_curve_states_its_payback_in_words(self, report):
        """An absent annotation reads as "did not pay back" to one reader and "not computed" to another."""
        curve = dict(rendered_sections(report))["owner-cash-curve"]
        # Both markers are this run's own annotations. "deepest out-of-pocket" and "world" also
        # occur in the authored prose above the chart, so neither would fail if the chart lost
        # them; the amount and the year of each can only come from the renderer.
        assert re.search(r"deepest out-of-pocket [\d,-]+ EUR in year \d+", curve)
        assert re.search(r"Payback lands in year \d+ in the central world", curve)

    def test_the_who_pays_whom_section_publishes_what_it_folded(self, report):
        """A folded ribbon is hidden from the picture, so its count and total are stated."""
        flows = dict(rendered_sections(report))["rented-who-pays-whom"]
        assert "ribbon(s) below 0.5 % of the flow volume" in flows
        assert "<svg" in flows

    def test_the_loan_and_credit_sections_arrive_together(self, report):
        """Two halves of one question: what the debt service looks like, and what it costs."""
        anchors = [anchor for anchor, _html in rendered_sections(report)]
        assert "owner-loan" in anchors
        assert "owner-cost-of-credit" in anchors
        credit = dict(rendered_sections(report))["owner-cost-of-credit"]
        assert "Effective annual rate" in credit
        assert "Repayment grant" in credit  # the disclosure table

    def test_the_landlord_statement_states_both_sides_and_draws_them(self, report):
        """The table partitions the NPV and the income Sankey draws the same partition."""
        statement = dict(rendered_sections(report))["rented-landlord-statement"]
        assert "cash flows, subtotal" in statement
        assert "accounting credits, subtotal" in statement
        assert "net position" in statement
        # The caption's own figure. "Levy income" and "§559" both occur in the authored prose
        # above it, so only the amount tells you the caption itself rendered.
        assert re.search(r"Levy income [\d,-]+ EUR per year", statement)
        assert "Dashed, translucent ribbons are the accounting credits" in statement
        for left, right in _ribbon_widths(statement):
            assert left == pytest.approx(right, abs=0.2)

    def test_the_uncertainty_table_lists_every_attributed_subject(self, report):
        """The tornado folds small rows; the table beside it must not."""
        drivers = dict(rendered_sections(report))["building-uncertainty-drivers"]
        assert "attribution table" in drivers
        assert "Optimistic delta" in drivers and "Pessimistic delta" in drivers

    def test_a_section_picks_a_perspective_that_has_its_subject_matter(self, database):
        """`_first_result_where` and `_has_year_zero_funding`, the two halves of that choice.

        Rendering the loan panel for the matrix's *first* perspective would drop it from every run
        whose first row happens to be an unfinanced view, even though a financed one sits right
        below it — which is the defect these two exist to prevent.
        """
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
        matrix = EvaluationMatrix()
        for perspective in REPORT_PERSPECTIVES:
            matrix.results[perspective.id] = evaluator.evaluate(make_inputs(), perspective)
        financed = _first_result_where(
            matrix, lambda result: views.loan_amortization_series(result).has_flows()
        )
        assert financed is not None and financed.perspective_id == "financed"
        assert _first_result_where(matrix, lambda _result: False) is None
        # These perspectives take no support, so the only year-0 funding in the run is the loan.
        assert _has_year_zero_funding(matrix.results["financed"])
        assert not _has_year_zero_funding(matrix.results["gross"])

    def test_the_report_stays_self_contained(self, report):
        """Inline SVG, no script, no external request — the whole point of hand-drawn charts."""
        assert "<script" not in report
        assert "https://" not in report
        assert report.count("<svg") >= 10


class TestTheDocumentSaysWhatItDidNotDraw:
    """A skipped section is reported to the reader, not to the log (owner decision).

    The report is read by people who did not run it, often long after the process that wrote it
    has gone. A section that is simply absent is indistinguishable from one that was never
    written — "no loan chart" reads as "this run has no loan" to one reader and as "the loan
    chart is broken" to another — and a log line answers neither of them, because the person
    holding the HTML is not the person tailing the output. The reason therefore travels into the
    document, under the table of contents.
    """

    @pytest.fixture(name="thin_report", scope="class")
    def fixture_thin_report(self, database) -> str:
        """One unfinanced, un-tenanted perspective: several sections have nothing to draw."""
        evaluator = EconomicEvaluator(
            database, EconomicParameters(country="DE", price_basis_year=2026)
        )
        matrix = EvaluationMatrix()
        matrix.results["gross"] = evaluator.evaluate(make_inputs(), REPORT_PERSPECTIVES[0])
        return build_lifecycle_report_html(matrix, run_plausibility_checks(matrix))

    def test_a_section_that_could_not_be_drawn_is_named_with_its_reason(self, thin_report):
        """The loan and the credit disclosure are absent *and* accounted for."""
        assert "Not drawn for this run" in thin_report
        block = thin_report.split("Not drawn for this run", maxsplit=1)[1].split("</div>")[0]
        assert "<b>Loan</b>" in block
        assert "every purchase in it is a cash purchase" in block
        assert "<b>Cost of credit</b>" in block
        assert 'id="owner-loan"' not in thin_report  # named there instead of drawn here

    def test_the_rented_story_is_no_longer_dropped_in_silence(self, thin_report):
        """It used to vanish with no signal at all when nothing was landlord- or tenant-scoped.

        The landlord statement is a section of the rented chapter, so on a run with no rented
        story the omission is one chapter rather than a handful of sections — which is the
        honest statement, and the reason a chapter can be skipped the same way a section can.
        """
        block = thin_report.split("Not drawn for this run", maxsplit=1)[1].split("</div>")[0]
        assert "<b>Rented out</b>" in block
        assert "no landlord and no tenant perspective" in block
        assert 'id="rented-landlord-statement"' not in thin_report

    def test_a_skip_entry_names_the_chapter_it_would_have_been_drawn_in(self, thin_report):
        """Four chapters can each drop a section, so an entry that named none would be ambiguous."""
        block = thin_report.split("Not drawn for this run", maxsplit=1)[1].split("</div>")[0]
        assert "<span class='chapter-tag'>The building</span>" in block
        assert block.count("<span class='chapter-tag'>Owner-occupied</span>") >= 2

    def test_a_run_that_drew_everything_says_nothing(self):
        """An empty "nothing was skipped" box would be noise on every complete report.

        Pinned on the renderer rather than on a rendered document: since the second half of the
        chart set landed there is no fixture in this suite that draws every section it has a
        builder for — the rich report cannot draw the energy balance, because its inputs carry
        no device energy flows — so asserting the absence of the box on a report would only be
        restating that fixture's inputs.
        """
        assert _not_drawn_html([]) == ""

    def test_the_rich_report_names_what_it_could_not_draw(self, report):
        """Its counterpart on a real document: sections and chapters, each with its reason."""
        block = report.split("Not drawn for this run", maxsplit=1)[1].split("</div>")[0]
        assert "<b>Energy balance</b>" in block  # a section of the building chapter
        assert "fewer than two device energy flows" in block
        assert "<b>Who pays whom</b>" in block  # a section of a story chapter
        assert "<b>Society</b>" in block  # and a whole chapter
        assert 'id="society-society-statement"' not in report

    def test_the_bridge_says_why_it_could_not_decompose_a_comparison(self, database):
        """A comparison without its reference result: the section is named, not logged away."""
        evaluator = EconomicEvaluator(
            database, EconomicParameters(country="DE", price_basis_year=2026)
        )
        matrix = EvaluationMatrix()
        matrix.results["gross"] = evaluator.evaluate(make_inputs(), REPORT_PERSPECTIVES[0])
        reference = evaluator.evaluate(
            make_inputs(energy_kwh=15000.0, investment=2000.0), REPORT_PERSPECTIVES[0]
        )
        comparison = compare(reference, matrix.results["gross"], "base", "measures")
        rendered = build_lifecycle_report_html(
            matrix, run_plausibility_checks(matrix), None, comparison
        )
        assert 'id="vs-reference-npv-bridge"' not in rendered
        assert "<b>NPV bridge</b>" in rendered
        assert "without the reference result the bridge decomposes" in rendered

    def test_a_chapter_can_be_skipped_the_same_way(self):
        """The unit behind the three story chapters' skips, pinned on its own."""
        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        blocks = context.skip_chapter(ReportChapters.RENTED_OUT, "Nothing was rented out.")
        assert isinstance(blocks, list) and not blocks
        assert context.skipped == [
            SkippedSection(name="Rented out", reason="Nothing was rented out.")
        ]
        # A chapter *is* a chapter, so it carries no chapter tag of its own.
        assert "chapter-tag" not in _not_drawn_html(context.skipped)

    def test_nothing_in_the_render_path_logs(self):
        """The whole point: the reason is in the document, so no builder reaches for the log."""
        for module in (assembly, sections_charts):
            source = inspect.getsource(module)
            assert "log.information" not in source, module.__name__


class TestTheCashCurveTellsOnePerspectivesStory:
    """The comparison names one perspective; the whole section has to be that one's.

    A `VariantComparison` is computed for exactly one perspective, so a payback sentence read off
    it under some other perspective's cash position is two runs' figures presented as one — and
    invisible in the output, because both halves are individually plausible.
    """

    def _matrix(self, database) -> EvaluationMatrix:
        """The report perspective set, evaluated."""
        evaluator = EconomicEvaluator(
            database, EconomicParameters(country="DE", price_basis_year=2026)
        )
        matrix = EvaluationMatrix()
        for perspective in REPORT_PERSPECTIVES:
            matrix.results[perspective.id] = evaluator.evaluate(make_inputs(), perspective)
        return matrix

    def test_the_section_draws_the_perspective_the_comparison_was_computed_for(self, database):
        """The heading, both panels and the payback sentence come from one result."""
        matrix = self._matrix(database)
        evaluator = EconomicEvaluator(
            database, EconomicParameters(country="DE", price_basis_year=2026)
        )
        reference = evaluator.evaluate(
            make_inputs(energy_kwh=15000.0, investment=2000.0), REPORT_PERSPECTIVES[1]
        )
        comparison = compare(reference, matrix.results["financed"], "base", "measures")
        rendered = build_lifecycle_report_html(
            matrix, run_plausibility_checks(matrix), None, comparison, reference_result=reference
        )
        sections = dict(rendered_sections(rendered))
        curve = sections["owner-cash-curve"]
        # The owner chapter's lead is "gross"; the comparison's perspective is "financed".
        assert "<h3>Cash curve (financed)" in curve
        assert "cumulative discounted savings" in curve
        # And the chapter that does *not* own the comparison draws its own curve without it.
        assert "cumulative discounted savings" not in sections["rented-cash-curve"]

    def test_a_mismatched_pair_is_refused_rather_than_drawn(self, database):
        """A caller cannot reintroduce the mix by handing the section the wrong result."""
        matrix = self._matrix(database)
        comparison = compare(
            matrix.results["financed"], matrix.results["financed"], "base", "measures"
        )
        with pytest.raises(views.CostDataError, match="two different parties"):
            _liquidity_section_html(
                matrix.results["gross"], comparison,
                _ChapterContext(chapter=ReportChapters.THE_BUILDING),
            )

    def test_a_missing_savings_curve_is_named_rather_than_defaulted(self, database):
        """A silently flat curve reads as "no savings in that world", which is a different claim."""
        matrix = self._matrix(database)
        comparison = compare(matrix.results["gross"], matrix.results["gross"], "base", "measures")
        del comparison.cumulative_discounted_savings_in_euro["high"]
        with pytest.raises(views.CostDataError, match="'high' cumulative savings curve"):
            _liquidity_section_html(
                matrix.results["gross"], comparison,
                _ChapterContext(chapter=ReportChapters.THE_BUILDING),
            )


class TestThePaybackSentence:
    """Savings are reference minus variant, so the larger savings pay back earlier.

    The slot that carries the *smaller* savings — `"low"` — is therefore the pessimistic world
    and `"high"` the optimistic one. The sentence used to name them the other way round, which
    inverted the conclusion a reader drew from it, and it consulted two of the three slots, so
    the central world it is actually about was never stated.

    The sentence now lives in `report_prose` because the PNG companion prints it too, and its
    own copy of it had a fourth wording again. It is asserted from this side, where the section
    that shows it is tested, and the two renderings are held together in
    `tests/test_economics_report_plots.py`.
    """

    def test_all_three_worlds_pay_back(self):
        """The central world leads; the other two are the interval around it."""
        assert payback_interval_sentence(9, 7, 5) == (
            "Payback lands in year 7 in the central world, between year 5 (optimistic) and "
            "year 9 (pessimistic)."
        )

    def test_no_world_pays_back(self):
        """Said in words, because an omitted sentence is read as "not computed"."""
        assert payback_interval_sentence(None, None, None) == (
            "The investment does not pay back within the horizon in any of the three worlds."
        )

    def test_only_the_optimistic_world_pays_back(self):
        """The weakest case a reader can still act on, and it has to be marked as weak."""
        assert payback_interval_sentence(None, None, 6) == (
            "Payback lands in year 6 in the optimistic world only; in the central and the "
            "pessimistic world the curve never reaches zero within the horizon."
        )

    def test_an_open_pessimistic_end_is_spelled_out(self):
        """Two worlds cross and one does not: the interval says so instead of printing a None."""
        assert payback_interval_sentence(None, 8, 6) == (
            "Payback lands in year 8 in the central world, between year 6 (optimistic) and "
            "never within the horizon (pessimistic)."
        )


class TestTheSectionOrderIsTheAssemblys:
    """`ReportSections.ORDER` is the membership, the assembly is the order (owner decision).

    The docstring used to call `ORDER` the authoritative page order while `_document_sections`
    restated that order and the contents sorted by page position — three claims about one thing,
    two of which could go stale without anything failing. What `ORDER` really owns is the *set*:
    which sections exist and what they are called. This is what pins that.
    """

    def test_every_anchor_the_document_emits_is_a_member_of_the_order(self, report):
        """A section with an anchor outside `ORDER` is one the contents cannot list."""
        known = {anchor for anchor, _name in ReportSections.ORDER}
        chapters = [chapter for chapter, _name in ReportChapters.ORDER]
        for anchor, _html in rendered_sections(report):
            chapter = next((one for one in chapters if anchor.startswith(f"{one}-")), None)
            assert chapter is not None, anchor
            assert anchor[len(chapter) + 1:] in known, anchor

    def test_every_heading_the_document_emits_is_a_name_of_the_order(self, report):
        """The names are `ORDER`'s too, so a contents entry and a heading cannot drift apart."""
        known = {name for _anchor, name in ReportSections.ORDER}
        headings = {
            re.sub(r"\s*\(.*", "", re.split(r"<span", heading)[0]).strip()
            for heading in re.findall(r"<section id=\"[^\"]+\"><h3>(.*?)</h3>", report)
        }
        assert headings <= known, headings - known

    def test_the_order_is_a_set_not_a_sequence_of_duplicates(self):
        """A name listed twice would make "explained at its first occurrence" ambiguous."""
        assert len(ReportSections.ORDER) == len({name for _anchor, name in ReportSections.ORDER})


class TestCostOfCreditCoversEveryFinancedPerspective:
    """One block per financed perspective, over the same set the loan section draws."""

    def test_two_financed_perspectives_get_two_blocks(self, database):
        """Disclosing only the first is a silent half-answer when two views are both financed."""
        evaluator = EconomicEvaluator(
            database, EconomicParameters(country="DE", price_basis_year=2026)
        )
        matrix = EvaluationMatrix()
        matrix.results["gross"] = evaluator.evaluate(make_inputs(), REPORT_PERSPECTIVES[0])
        for share, rate in ((0.6, 0.035), (0.9, 0.05)):
            perspective = Perspective(
                id=f"financed_{int(share * 100)}",
                installation_context=InstallationContext.GREENFIELD,
                subsidy_mode=SubsidyMode.none(),
                financing=FinancingPlan(financed_share=share, nominal_interest_rate=rate,
                                        term_in_years=12),
            )
            matrix.results[perspective.id] = evaluator.evaluate(make_inputs(), perspective)
        rendered = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix))
        credit = dict(rendered_sections(rendered))["owner-cost-of-credit"]
        assert "<b>financed_60</b>" in credit and "<b>financed_90</b>" in credit
        assert credit.count("Effective annual rate: <b>") == 2  # the authored prose says it once more
        assert "<b>gross</b>" not in credit  # the cash purchase has no credit to price
        loan = dict(rendered_sections(rendered))["owner-loan"]
        assert "<b>financed_60</b>" in loan and "<b>financed_90</b>" in loan  # the same set


class TestTheBridgePrintsThePublishedDelta:
    """The net figure is the comparison's own, not a subtraction the renderer performs (seam 4)."""

    def test_the_net_difference_is_the_comparisons_npv_delta(self, database):
        """One published number, quoted once, so no section can contradict another."""
        evaluator = EconomicEvaluator(
            database, EconomicParameters(country="DE", price_basis_year=2026)
        )
        matrix = EvaluationMatrix()
        matrix.results["gross"] = evaluator.evaluate(make_inputs(), REPORT_PERSPECTIVES[0])
        reference = evaluator.evaluate(
            make_inputs(energy_kwh=15000.0, investment=2000.0), REPORT_PERSPECTIVES[0]
        )
        comparison = compare(reference, matrix.results["gross"], "base", "measures")
        rendered = build_lifecycle_report_html(
            matrix, run_plausibility_checks(matrix), None, comparison, reference_result=reference
        )
        bridge = dict(rendered_sections(rendered))["vs-reference-npv-bridge"]
        expected = f"{comparison.npv_delta_in_euro.best_estimate:,.0f}"
        assert f"Net NPV difference: <b>{expected} EUR</b>" in bridge
