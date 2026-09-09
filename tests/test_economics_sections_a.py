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
from hisim.economics.presentation_style import SankeyLayout, sankey_node_boxes
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
from hisim.economics.reporting.scaffold import ReportChapters
from hisim.economics.reporting.sections_charts import (
    _effective_rate_text,
    _first_result_where,
    _has_year_zero_funding,
)
from hisim.economics.results import EvaluationMatrix, compare
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

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


def _node_rects(svg: str):
    """The node rectangles of an inline-SVG Sankey as `(x, y, width, height)`."""
    return [
        (float(x), float(y), float(w), float(h))
        for x, y, w, h in re.findall(
            r'<rect x="(-?[\d.]+)" y="(-?[\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg
        )
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
        for x, y, width, height in _node_rects(svg):
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
        nodes = _node_rects(svg)
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
        for x, y, width, height in _node_rects(svg):
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
        """Both renderers scale the node by `SankeyLayout.NODE_WIDTH`, so neither may re-guess it."""
        svg = _sankey_svg(TANGLED_COLUMNS, _coloured(TANGLED_RIBBONS), {})
        widths = {round(width, 1) for _x, _y, width, _h in _node_rects(svg)}
        assert len(widths) == 1
        assert widths.pop() == pytest.approx(SankeyLayout.NODE_WIDTH * (860 - 150 - 130), abs=0.1)


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
        """The band of a difference is not the difference of the bands, so deltas get no whisker."""
        svg = _bridge_svg(
            (
                ("reference: base", UncertainValue(1000.0, 800.0, 1200.0)),
                ("variant: measures", UncertainValue(600.0, 500.0, 800.0)),
            ),
            [("Investment", 400.0, "var(--g0)"), ("Energy", -800.0, "var(--g1)")],
        )
        # Two anchor whiskers, drawn as horizontal rules in the muted chrome colour.
        assert svg.count('stroke="var(--muted)" stroke-width="1.4"') == 2
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


def _rendered_sections(text: str):
    """Every anchored section of a rendered report as `(anchor, html)`, in page order."""
    return [
        (match.group(1), match.group(0))
        for match in re.finditer(r"<section id=\"([^\"]+)\">.*?</section>", text, flags=re.S)
    ]


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

    #: `<p>` `<p>` `<details>` `<details>` in that order, immediately after a section's heading.
    #: The heading is an `<h3>` under the chapter's `<h2>` and carries the chapter as a trailing
    #: `<span>`.
    FOUR_PART_OPENING = (
        r"<section id=\"[^\"]+\"><h3>[^<]*(?:<span class='chapter-tag'>[^<]*</span>)?</h3>"
        r"<p class='sub'>.+?</p>"
        r"<p class='sub'>.+?</p>"
        r"<details><summary>Terms used here</summary><dl><dt>.+?</dl></details>"
        r"<details><summary>How this is calculated</summary><p class='sub'>.+?</details>"
    )

    #: The single paragraph a *repeated* section name renders instead of the four parts.
    CROSS_REFERENCE_OPENING = (
        r"<section id=\"[^\"]+\"><h3>[^<]*(?:<span class='chapter-tag'>[^<]*</span>)?</h3>"
        r"<p class='sub'>The same chart, read the same way: see the explanation under "
        r"<a href=\"#[^\"]+\">[^<]+</a>\.</p>"
    )

    def test_every_rendered_section_opens_with_the_four_parts(self, report):
        """No section may render its chart without the block that explains it — or a link to it."""
        sections = _rendered_sections(report)
        assert len(sections) > 15, "the fixture stopped reaching most sections"
        explained = set()
        for anchor, html in sections:
            if re.match(self.FOUR_PART_OPENING, html, flags=re.S):
                explained.add(anchor)
                continue
            assert re.match(self.CROSS_REFERENCE_OPENING, html, flags=re.S), anchor
        assert len(explained) > 15, "the four-part blocks disappeared entirely"

    def test_the_primer_renders_once_at_the_top(self, report):
        """The conventions every other section leans on are stated first, and only once."""
        anchors = [anchor for anchor, _html in _rendered_sections(report)]
        assert anchors[0] == "building-how-to-read"
        assert anchors.count("building-how-to-read") == 1
        assert report.index(">How to read this report") > report.index("<nav")

    def test_the_rendered_prose_is_the_authored_prose(self, report):
        """The renderer marks the text up; it never edits it."""
        by_anchor = dict(_rendered_sections(report))
        for anchor, name in ReportSections.ORDER:
            html = by_anchor.get(f"building-{anchor}")
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

    def test_the_chapter_carries_its_authored_lead_in(self, report):
        """A chapter heading with no lead-in is a silent editorial regression."""
        anchor, name = ReportChapters.THE_BUILDING
        assert f"<h2 class='chapter' id=\"{anchor}\">" in report
        assert ReportProse.to_html(ReportProse.for_chapter(name)) in report

    def test_anchors_are_unique_and_chapter_prefixed(self, report):
        """Two sections sharing an anchor would send every contents link to the same place."""
        anchors = [anchor for anchor, _html in _rendered_sections(report)]
        assert len(anchors) == len(set(anchors))
        for anchor in anchors:
            assert anchor.startswith(f"{ReportChapters.THE_BUILDING[0]}-"), anchor


class TestVisualizationSectionsRender:
    """The sections of the first half of the chart set, on an ordinary evaluated run."""

    def test_the_chart_sections_are_present_and_name_their_perspective(self, report):
        """Each perspective-scoped section says which perspective it is showing."""
        for anchor in ("building-cash-curve", "building-uncertainty-drivers",
                       "building-lifetimes", "building-who-pays-whom", "building-npv-bridge"):
            assert f'id="{anchor}"' in report, anchor
        curve = dict(_rendered_sections(report))["building-cash-curve"]
        assert "<h3>Cash curve (gross)" in curve
        credit = dict(_rendered_sections(report))["building-cost-of-credit"]
        assert "<h3>Cost of credit (financed)" in credit  # not the matrix's first perspective

    def test_the_cash_curve_states_its_payback_in_words(self, report):
        """An absent annotation reads as "did not pay back" to one reader and "not computed" to another."""
        curve = dict(_rendered_sections(report))["building-cash-curve"]
        assert "deepest out-of-pocket" in curve
        assert "world" in curve  # the payback interval sentence names the optimistic/pessimistic worlds

    def test_the_who_pays_whom_section_publishes_what_it_folded(self, report):
        """A folded ribbon is hidden from the picture, so its count and total are stated."""
        flows = dict(_rendered_sections(report))["building-who-pays-whom"]
        assert "ribbon(s) below 0.5 % of the flow volume" in flows
        assert "<svg" in flows

    def test_the_loan_and_credit_sections_arrive_together(self, report):
        """Two halves of one question: what the debt service looks like, and what it costs."""
        anchors = [anchor for anchor, _html in _rendered_sections(report)]
        assert "building-loan" in anchors
        assert "building-cost-of-credit" in anchors
        credit = dict(_rendered_sections(report))["building-cost-of-credit"]
        assert "Effective annual rate" in credit
        assert "Repayment grant" in credit  # the disclosure table

    def test_the_landlord_statement_states_both_sides_and_draws_them(self, report):
        """The table partitions the NPV and the income Sankey draws the same partition."""
        statement = dict(_rendered_sections(report))["building-landlord-statement"]
        assert "cash flows, subtotal" in statement
        assert "accounting credits, subtotal" in statement
        assert "net position" in statement
        assert "modernization levy" in statement or "Levy income" in statement
        assert "Dashed, translucent ribbons are the accounting credits" in statement
        for left, right in _ribbon_widths(statement):
            assert left == pytest.approx(right, abs=0.2)

    def test_the_uncertainty_table_lists_every_attributed_subject(self, report):
        """The tornado folds small rows; the table beside it must not."""
        drivers = dict(_rendered_sections(report))["building-uncertainty-drivers"]
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
