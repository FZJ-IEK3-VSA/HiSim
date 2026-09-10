"""Renderer tests for the four captions that state a figure's own derivation (Q26 F1/F6/F7, Q27).

The fourth of the section-renderer files, and the one about *sentences* rather than charts. Where
`tests/test_economics_sections_a.py` and `_b.py` cover the visualization set and `_c.py` the
chapter shape, this covers the lines the report prints beside a number so a reader can check it
instead of trusting it: the heat-cost KPI written out as its own division, the anyway credit
multiplied out as `share x like-for-like cost`, the assumption each scenario row changed with both
of its values, and the footnote a row whose swing is exactly zero carries to say why its axis was
inert here.

It also owns the **rename** (Q27 R1). The heat KPI divides a perspective's whole NPV by the heat
delivered, which is not the literature's levelized cost of heat, and the F6 caption made that
impossible to keep calling one — so the figure now reads "system cost per unit of heat" at every
surface a reader sees: the KPI export, both perspectives tables, the plausibility row and the
caption. There is deliberately **no alias**: the tests below pin the new name at each surface and,
jointly, that the old one is gone from rendered output. The serialization key
`levelized_cost_of_heat_in_euro_per_kwh` is untouched and is not asserted on here; see
`results.HeatCostNaming` for why a name and a key part ways.

Two fixtures rather than one, because the four captions need different runs: a greenfield cash
purchase with a heat demand carries the heat-cost figure and the KPI set, and a brownfield run
replacing registered windows is the only one that books an anyway credit at all. The scenario
captions are rendered from a real evaluated cube, not from a stand-in, because the assumption
labels are only meaningful against the parameters a run was actually priced under.

**What a failure means.** A *presentation* failure: something a reader sees changed. The views
behind these captions carry their own arithmetic invariants in
`tests/test_economics_views_captions.py`, and a caption that does not reconcile with its KPI
raises there rather than rendering here.
"""

# clean

import re

import pytest

from hisim.economics.audit import build_input_audit
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.exports import build_lifecycle_kpi_entries
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    ExistingAsset,
    ExistingAssetRegister,
)
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.plausibility import run_plausibility_checks
from hisim.economics.reporting import build_cost_summary_markdown, build_lifecycle_report_html
from hisim.economics.reporting.assembly import _levelized_heat_cost_caption, _swing_text
from hisim.economics.reporting.sections import (
    _anyway_credit_cell,
    _anyway_share_caption,
    _timeline_detail_table,
)
from hisim.economics.results import AnywayBasisKinds, EvaluationMatrix, HeatCostNaming
from hisim.economics.scenarios import ScenarioSet, evaluate_cube
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


#: Pinned rather than defaulted: the assumption captions below print the central case's own
#: values, so an assertion on "central 3.00%" must be an assertion about this line and not about
#: whatever the dataclass or the shipped DE defaults happen to say this month. The per-carrier
#: escalation is deliberately *not* pinned — the electricity axis exists to exercise the fallback
#: to the country defaults file, and that test reads the resolved rate off the result rather than
#: hard-coding it.
PARAMETERS = EconomicParameters(
    country="DE",
    price_basis_year=2026,
    interest_rate=0.03,
    general_price_escalation_rate=0.02,
    observation_period_in_years=20,
)

#: One perspective, not the shipped bundle: every caption here is written for the matrix's first
#: perspective, and a single cash-purchase brownfield view keeps the evaluated cube cheap enough
#: to render three scenario axes through it.
PERSPECTIVE = Perspective(
    id="brownfield",
    installation_context=InstallationContext.BROWNFIELD,
    subsidy_mode=SubsidyMode.none(),
)


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database, module-scoped because loading and validating it dominates."""
    return CostDatabase()


def make_inputs() -> EvaluationInputs:
    """A heat pump and a like-for-like window replacement, in a house that buys electricity.

    Both halves are load-bearing. The heat demand and the electricity bill are what make the
    heat-cost figure exist and what let the CO2 axis be diagnosably inert (the run books a bill,
    and the electricity price entry declares no carbon-price exposure). The registered windows,
    replaced by windows, are what book an anyway credit: a like-for-like replacement of an asset
    with life left in it is a cost the building would have caused regardless, and that credit is
    the one the caption multiplies out.
    """
    heat_pump = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue(16000.0, 12800.0, 20800.0),
        lifetime_override_in_years=18.0,
        override_source="test",
    )
    windows = ComponentCostFacts(
        asset_class=ComponentType.WINDOWS_TRIPLE_GLAZED,
        size=25.0,
        size_unit=Units.SQUARE_METER,
    )
    register = ExistingAssetRegister(
        assets=[
            ExistingAsset(
                asset_class=ComponentType.WINDOWS_TRIPLE_GLAZED,
                size=25.0,
                size_unit=Units.SQUARE_METER,
                installation_year=2026 - 33,  # 2 a of 35 remaining -> credit at year 2
                replaced_by_asset_classes=[ComponentType.WINDOWS_TRIPLE_GLAZED],
            )
        ]
    )
    return EvaluationInputs(
        simulation_year=2026,
        simulated_period_fraction=1.0,
        cost_facts=[
            SubjectCostFacts("HeatPump", heat_pump),
            SubjectCostFacts("Envelope.Windows", windows),
        ],
        billing=[
            BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=5000.0)
        ],
        existing_assets=register,
        annual_heat_demand_in_kwh=15000.0,
        living_area_in_m2=150.0,
    )


#: The three perspectives the caption tests build matrices from. `brownfield` is the module's own
#: (the shipped fixture below renders through it); the other two exist so a test can put two
#: perspectives that credit alike, and two that do not, in front of the same caption.
_BROWNFIELD = PERSPECTIVE
_BROWNFIELD_NET = Perspective(
    id="brownfield_net",
    installation_context=InstallationContext.BROWNFIELD,
    subsidy_mode=SubsidyMode.full(),
)
_GREENFIELD = Perspective(
    id="greenfield",
    installation_context=InstallationContext.GREENFIELD,
    subsidy_mode=SubsidyMode.none(),
)


def _matrix_for(database, perspectives, inputs=None) -> EvaluationMatrix:
    """One evaluation of the same inputs through each perspective, in the order given."""
    evaluator = EconomicEvaluator(database, PARAMETERS)
    resolved = inputs if inputs is not None else make_inputs()
    matrix = EvaluationMatrix()
    for perspective in perspectives:
        matrix.results[perspective.id] = evaluator.evaluate(resolved, perspective)
    return matrix


def _greenfield_result(database):
    """A run with no existing assets at all: nothing is replaced, so nothing is credited."""
    inputs = make_inputs()
    inputs.existing_assets = ExistingAssetRegister()
    return _matrix_for(database, [_GREENFIELD], inputs).results[_GREENFIELD.id]


def _partial_share_inputs():
    """The same house, with only 40 % of the window replacement a cost it would have caused anyway.

    A first-time improvement rather than a like-for-like swap: the counterfactual would have
    repaired, not upgraded, so only the repair share of the measure is anyway cost (§4.1). It is
    the case the caption's closing sentence exists for, and the only one in which the share is
    doing visible work.
    """
    inputs = make_inputs()
    assert inputs.existing_assets is not None
    inputs.existing_assets.assets[0].anyway_share = 0.4
    return inputs


@pytest.fixture(name="matrix", scope="module")
def fixture_matrix(database) -> EvaluationMatrix:
    """The single-perspective evaluation every caption below is rendered from."""
    evaluator = EconomicEvaluator(database, PARAMETERS)
    matrix = EvaluationMatrix()
    matrix.results[PERSPECTIVE.id] = evaluator.evaluate(make_inputs(), PERSPECTIVE)
    return matrix


@pytest.fixture(name="cube", scope="module")
def fixture_cube(database):
    """A three-axis cube: one axis inert for a reason the timeline states, and two that move.

    `co2` is inert because an all-electric run books no carbon-price flow at all, which is the
    one inert case a stored result can diagnose; `interest` and the electricity escalation both
    genuinely move the headline, so the same cube pins the footnote *and* its absence. The
    undiagnosable case — an axis whose inertness nothing can name — is a view-level concern and
    is pinned in `tests/test_economics_views_captions.py`.
    """
    scenario_set = ScenarioSet.from_json(
        {
            "base": "central",
            "mode": "ONE_AT_A_TIME",
            "axes": [
                {"name": "co2", "field": "co2_price_scenario", "levels": {"high": "high"}},
                {"name": "interest", "field": "interest_rate", "levels": {"high": 0.05}},
                {
                    "name": "electricity",
                    "field": "energy_price_escalation_rates.ELECTRICITY",
                    "levels": {"flat": 0.0},
                },
            ],
        }
    )
    return evaluate_cube(make_inputs(), PARAMETERS, [PERSPECTIVE], scenario_set, database)


@pytest.fixture(name="audit", scope="module")
def fixture_audit(database, matrix):
    """The resolved-input audit, whose anyway-credit column is one of the four captions.

    `build_input_audit` resolves every declared fact against the database once and returns typed
    rows; the CSV writer and the HTML section render the same rows, which is what keeps them from
    disagreeing. The share and its basis travel on the row, so the audited credit multiplies out
    in the same table as the unit price that produced its basis.
    """
    return build_input_audit(
        make_inputs(), database, PARAMETERS, next(iter(matrix.results.values()))
    )


@pytest.fixture(name="report", scope="module")
def fixture_report(matrix, cube, audit) -> str:
    """The full HTML report with the audit and the cube attached, rendered once for the file."""
    return build_lifecycle_report_html(
        matrix, run_plausibility_checks(matrix), audit, scenario_cube=cube
    )


class TestHeatCostKpiIsNamedForWhatItMeasures:
    """Q27 R1: the heat KPI reads "system cost per unit of heat" everywhere a reader sees it.

    These pin the rename at each published surface and, jointly, that the old name is gone from
    rendered output. The break is intended and unaliased: a consumer reading the KPI set by name
    sees the new one and nothing else.
    """

    def test_the_kpi_export_publishes_the_new_name(self, matrix):
        """The name in `lifecycle_kpis.json` is the name in the report's KPI table: one list."""
        names = [entry.name for entry in build_lifecycle_kpi_entries(matrix)]
        heat = [name for name in names if "heat" in name.lower()]
        assert heat, "the fixture declares an annual heat demand, so the KPI must exist"
        assert all(name.startswith(f"{HeatCostNaming.FULL} [EUR/kWh]") for name in heat)
        assert not [name for name in names if "levelized" in name.lower()]

    def test_the_summary_carries_the_new_column_and_the_new_check_row(self, matrix):
        """The markdown perspectives column and the plausibility row both renamed, no alias left."""
        text = build_cost_summary_markdown(matrix, run_plausibility_checks(matrix))
        assert f"| {HeatCostNaming.COLUMN} |" in text
        assert f"| {HeatCostNaming.CHECK_LABEL} |" in text
        assert "levelized" not in text.lower()

    def test_the_html_perspectives_table_carries_the_new_column(self, report):
        """The HTML column header is the same constant as the markdown one, from one place.

        `LCOH` survives in exactly one place on the page and must: the authored "Terms used here"
        entry explains that this figure is deliberately *not* one, which is the whole reason for
        the rename. The assertion is therefore on the header cell rather than on the document.
        """
        assert f"<th>{HeatCostNaming.COLUMN}</th>" in report
        assert "<th>LCOH</th>" not in report
        assert "Levelized cost of heat" not in report

    def test_the_derivation_caption_leads_with_the_annual_division(self, report):
        """Q27 R4: EAC / annual heat first, the discounted-sum form as the stated equivalent."""
        caption = re.search(
            rf"<b>{HeatCostNaming.FULL}, in full\.</b>(.*?)</p>", report, re.S
        )
        assert caption, "the KPI section must carry the F6 derivation caption"
        body = caption.group(1)
        assert body.index("EUR/a &divide;") < body.index("equivalently NPV")
        assert "&divide; discounted heat sum" in body
        assert "No heating-only attribution is applied" in body

    def test_the_caption_names_every_subject_the_numerator_covers(self, report):
        """The attribution set is the honest half: the windows count toward the cost of heat."""
        caption = re.search(
            rf"<b>{HeatCostNaming.FULL}, in full\.</b>(.*?)</p>", report, re.S
        )
        assert caption
        assert "Envelope.Windows" in caption.group(1)

    def test_each_perspective_that_publishes_the_figure_gets_its_own_division(self, database):
        """The KPI table publishes the figure per perspective, and so does the caption below it.

        Written for the matrix's first perspective, the caption stated one row's division and
        silently mis-described the others: the numerator is that perspective's *whole* NPV, and a
        net view books a different set of flows from a gross one. Two perspectives that divide
        differently now get a line each, named.
        """
        caption = _levelized_heat_cost_caption(_matrix_for(database, [_BROWNFIELD, _GREENFIELD]))
        assert "Each perspective divides its own" in caption
        assert "<b>brownfield</b> — " in caption and "<b>greenfield</b> — " in caption
        assert caption.count("Subjects counted:") == 2

    def test_perspectives_that_divide_alike_state_the_division_once(self, matrix):
        """The shipped fixture has one perspective, so the caption is the single sentence form."""
        caption = _levelized_heat_cost_caption(matrix)
        assert "Each perspective divides its own" not in caption
        assert caption.count("EUR/kWh</b>") == 1


class TestAnywayShareCaption:
    """Q22 and Q26 F7: an anyway credit is a visible multiplication, not a figure to be trusted."""

    def test_the_caption_multiplies_the_share_out_against_its_basis(self, report):
        """`share x like-for-like cost = credit`, named per subject rather than averaged away."""
        caption = re.search(
            r"<p class='sub'>Anyway credits in this run are booked at(.*?)</p>", report, re.S
        )
        assert caption, "the cash-flow timeline section must carry the F7 caption"
        body = caption.group(1)
        assert "<b>share x like-for-like cost = credit</b>" in body
        assert re.search(r"Envelope\.Windows 100% x [\d,]+ EUR = [\d,]+ EUR", body)

    def test_the_detail_table_cell_carries_the_same_multiplication(self, matrix):
        """The credit row states its own basis, so the amount beside it can be reconstructed."""
        result = next(iter(matrix.results.values()))
        table = _timeline_detail_table(result)
        credit_row = next(row for row in table.split("<tr>") if "ANYWAY_COST_CREDIT" in row)
        assert re.search(r"ANYWAY_COST_CREDIT \(anyway 100% x [\d,]+ EUR\)", credit_row)

    def test_the_audit_table_audits_the_share_beside_the_price(self, report):
        """Q22: the share is an input of the same standing as a unit price, so it is audited."""
        assert "<th>Anyway credit (share x basis)</th>" in report
        header, _, body = report.partition("<th>Anyway credit (share x basis)</th>")
        assert header, "the audit table must open before its anyway column"
        windows_row = next(
            row for row in body.split("<tr>") if "Envelope.Windows" in row and "EUR = " in row
        )
        assert re.search(r"<td>100% x [\d,]+ EUR = [\d,]+ EUR</td>", windows_row)

    def test_an_audited_row_without_a_credit_states_no_value(self, audit):
        """A subject that earned no anyway credit gets the empty-value dash, never a 0 %.

        Most rows are this one, and the dash is the same one the lifetime cell uses for an absent
        value: printing `0%` there would read as "the counterfactual pays for none of this", which
        is a claim, where the truth is that the question does not arise for that subject.
        """
        rows = {row.subject: row for row in audit.rows}
        assert rows["HeatPump"].anyway_share is None
        assert _anyway_credit_cell(rows["HeatPump"]) == "-"
        assert rows["Envelope.Windows"].anyway_share == pytest.approx(1.0)
        assert _anyway_credit_cell(rows["Envelope.Windows"]) == (
            f"100% x {rows['Envelope.Windows'].anyway_basis_in_euro:,.0f} EUR = "
            f"{rows['Envelope.Windows'].anyway_basis_in_euro:,.0f} EUR"
        )

    def test_a_run_that_credits_nothing_says_nothing(self, database):
        """Most runs have no replaced asset, and the section then carries no caption at all."""
        result = _greenfield_result(database)
        assert not result.anyway_share_by_subject
        assert _anyway_share_caption(EvaluationMatrix(results={result.perspective_id: result})) == ""

    def test_a_share_below_100_percent_multiplies_out_and_is_explained(self, database):
        """The interesting case: only the repair share of a first-time improvement was anyway cost.

        A `40 %` share against a `12,000 EUR` basis credits `4,800 EUR`, and all three numbers are
        on the page — the share alone would state a factor whose base the reader cannot see, and
        the credit alone a figure they cannot check. The sentence that follows is what makes the
        share readable at all: below 100 % means the counterfactual would not have paid for the
        whole measure.
        """
        matrix = _matrix_for(database, [_BROWNFIELD], _partial_share_inputs())
        result = next(iter(matrix.results.values()))
        share = result.anyway_share_by_subject["Envelope.Windows"]
        basis = result.anyway_basis_by_subject["Envelope.Windows"]
        assert share == pytest.approx(0.4)
        caption = _anyway_share_caption(matrix)
        assert f"Envelope.Windows 40% x {basis:,.0f} EUR = {share * basis:,.0f} EUR" in caption
        assert "A share below 100 % means the measure was a first-time improvement" in caption
        assert result.anyway_basis_kind_by_subject["Envelope.Windows"] == (
            AnywayBasisKinds.LIKE_FOR_LIKE
        )
        assert "<b>share x like-for-like cost = credit</b>" in caption

    def test_perspectives_that_credit_alike_collapse_to_one_sentence(self, database):
        """Two perspectives booking the same credits state them once, not once each."""
        matrix = _matrix_for(database, [_BROWNFIELD, _BROWNFIELD_NET])
        caption = _anyway_share_caption(matrix)
        assert caption.startswith("<p class='sub'>Anyway credits in this run are booked at")
        assert caption.count("Envelope.Windows") == 1
        assert "<b>brownfield</b>" not in caption

    def test_perspectives_that_credit_differently_state_one_line_each(self, database):
        """A greenfield view books no replaced asset, so "in this run" is not one sentence.

        The caption used to be written from the matrix's first perspective and to speak for the
        whole run; a perspective that books no investment books no anyway credit either, so that
        claim was the first perspective's, presented as everyone's.
        """
        matrix = _matrix_for(database, [_BROWNFIELD, _GREENFIELD])
        caption = _anyway_share_caption(matrix)
        assert "The perspectives of this run do not book the same ones" in caption
        assert re.search(r"<b>brownfield</b> — Envelope\.Windows 100% x [\d,]+ EUR = ", caption)
        assert "<b>greenfield</b> — no anyway credit is booked" in caption


class TestScenarioRowsStateTheirAssumption:
    """Q26 F1 and Q27 R2: a scenario row names what it changed, and a `+0` row names why not."""

    def test_every_row_names_the_assumption_with_both_values(self, report, matrix):
        """A row labelled `interest=high` beside a swing is a number without a cause."""
        assert "Assumption (scenario value, central value)" in report
        assert "interest_rate 5.00% (central case: 3.00%)" in report
        # The carrier rate is not configured on the parameters, so the central value is the one
        # the run resolved from the country defaults file — not a blank. Read off the result
        # rather than written down here: what is under test is that the row states the rate the
        # run was priced at, not what this year's defaults file happens to hold.
        resolved = next(
            rate.rate
            for label, rate in next(iter(matrix.results.values())).assumptions.escalation_rates.items()
            if label.upper() == "ENERGY:ELECTRICITY"
        )
        assert (
            f"energy_price_escalation_rates.ELECTRICITY 0.00% (central case: {resolved:.2%})"
            in report
        )

    def test_the_base_row_says_it_changed_nothing(self, report):
        """The central case gets a stated non-assumption rather than an empty cell."""
        assert "central case — nothing changed" in report

    def test_an_inert_axis_footnotes_what_the_timeline_shows(self, report):
        """The all-electric run books no carbon price, so the CO2 axis moves nothing and says so."""
        assert (
            "<b>co2=high</b> — swing is exactly zero: the stored timeline books no CO2-price "
            "entry for any carrier, electricity included." in report
        )
        # What it must *not* say: the zero exposure that is the likely cause is not on a stored
        # result, and the footnote states only what the booked flows show.
        assert "co2_price_exposure" not in report

    def test_an_axis_that_moved_carries_no_footnote(self, report):
        """Only inert rows are footnoted; a real swing needs no excuse."""
        assert "<b>interest=high</b> — swing is exactly zero" not in report

    def test_a_tiny_swing_is_printed_as_a_tiny_swing(self):
        """A swing column of whole euro turned a real 0.42 EUR/a effect into the inert row's `+0`.

        The footnote below the table explains a `+0` as an axis nothing in the run depends on, so
        a cell that rounds a small swing into that spelling makes the table state something about
        itself that is not true. Above 100 EUR/a the cents are noise and whole euro is what a
        reader wants; below it the magnitude is the finding.
        """
        assert _swing_text(0.0) == "+0"
        assert _swing_text(0.42) == "+0.42"
        assert _swing_text(-3.5) == "-3.5"
        assert _swing_text(99.6) == "+99.6"
        assert _swing_text(190.4) == "+190"
        assert _swing_text(-1240.2) == "-1,240"
