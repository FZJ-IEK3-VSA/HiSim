"""Golden-file oracle for the rendered reports (cost-spec-v2 §2.4, package S4b).

The switch-over of `reporting.py`/`report_plots.py` onto the view-model (W4.7) must not move a
single rendered digit. This module is the proof: it evaluates one deterministic, deliberately
rich in-memory fixture — no simulation run, no result directory — renders `cost_summary.md` and
`lifecycle_report.html` from it, and byte-compares both against files checked in under
`tests/goldens/`.

**Normalization.** Exactly one thing in the output is not a function of the fixture: the
generation date, which both reports stamp via `datetime.date.today()`. It is replaced by the
literal ``<DATE>`` — the *only* substitution made, and a targeted one (today's ISO date string,
not "any date-shaped text"), so the `retrieved` dates of the §3.10 source registry stay in the
golden and a data PR that changes one shows up as a diff. Everything else — every euro figure,
every SVG coordinate, every tooltip — is compared exactly. The PNG companions are not goldens
(matplotlib output is not byte-stable across versions); `report_plots` is covered by the
numbers it now reads out of the view-model plus its own unit tests.

Regenerate deliberately with ``HISIM_REGEN_GOLDENS=1 pytest tests/test_economics_report_goldens.py``.
Any regeneration outside a change that is *meant* to move numbers is a bug being papered over.

**Error class.** A failure here is a *presentation* failure: something a reader sees changed. It
says nothing about whether the underlying research numbers are right — that is what the engine,
view and property tests are for — and conversely a reporting bug caught here can mislead a reader
but can never corrupt a stored result (cost-spec-v2 §2.4). Read a diff in that light: if the
engine tests are green and only this file fails, the arithmetic is fine and the rendering moved.
`TestFixtureIsRich` guards the one way this oracle could quietly lose its value — a fixture that
stops reaching a section still compares two empty renderings and passes — and
`TestStoredResultsRoundTrip` extends the same comparison to the reload path (W4.5), which is what
lets the `report` CLI render stored files instead of re-running the evaluator.
"""

# clean

import datetime
import os

import pytest

from hisim.economics.audit import build_input_audit
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    ExistingAsset,
    ExistingAssetRegister,
)
from hisim.economics.financing import FinancingPlan
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import (
    ActorScope,
    InstallationContext,
    Perspective,
    SubsidyMode,
)
from hisim.economics.plausibility import run_plausibility_checks
from hisim.economics.reporting import build_cost_summary_markdown, build_lifecycle_report_html
from hisim.economics.results import EvaluationMatrix, compare
from hisim.economics.scenarios import ScenarioSet, evaluate_cube
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
)
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

GOLDEN_DIRECTORY = os.path.join(os.path.dirname(__file__), "goldens")
SUMMARY_GOLDEN = "cost_summary.md"
REPORT_GOLDEN = "lifecycle_report.html"

#: Pinned explicitly rather than defaulted: the price basis year decides which device and energy
#: entries are read, so leaving it implicit would let the default-year policy re-baseline the
#: goldens. 2026 is the vintage that carries real min/best_estimate/max bands, which the report needs to
#: draw whiskers and band polygons at all.
PARAMETERS = EconomicParameters(country="DE", price_basis_year=2026)


def make_inputs(energy_kwh: float = 5200.0, investment: float = 16000.0) -> EvaluationInputs:
    """A brownfield retrofit that exercises every report section.

    Banded heat-pump investment (whiskers), a second envelope subject (per-subject charts), an
    existing gas heater and old windows (removal, anyway credits, residual value), bought *and*
    sold electricity (feed-in in the year-1 bill), a subsidy context the DE catalog can price
    (decision cards, awards table) and tenancy data (the §6.5 actor split).
    """
    heat_pump = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue(investment, investment * 0.8, investment * 1.3),
        lifetime_override_in_years=18.0,
        override_source="golden fixture",
        technical_attributes={"scop": 4.2, "refrigerant": "R290"},
    )
    windows = ComponentCostFacts(
        asset_class=ComponentType.WINDOWS_TRIPLE_GLAZED,
        size=28.0,
        size_unit=Units.SQUARE_METER,
    )
    register = ExistingAssetRegister(
        assets=[
            ExistingAsset(
                asset_class=ComponentType.GAS_HEATER,
                size=18.0,
                size_unit=Units.KILOWATT,
                installation_year=2009,
                replaced_by_asset_classes=[ComponentType.HEAT_PUMP],
            ),
            ExistingAsset(
                asset_class=ComponentType.WINDOWS_TRIPLE_GLAZED,
                size=28.0,
                size_unit=Units.SQUARE_METER,
                installation_year=1993,
                replaced_by_asset_classes=[ComponentType.WINDOWS_TRIPLE_GLAZED],
            ),
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
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=energy_kwh,
                energy_sold_in_kwh=2500.0,
            )
        ],
        existing_assets=register,
        subsidy_context=SubsidyContext(
            applicant=ApplicantProfile(
                actor=ApplicantActor.OWNER_OCCUPIER,
                taxable_household_income_in_euro=35000.0,
                main_residence=True,
            ),
            building=SubsidyBuildingContext(
                construction_year=1985,
                dwelling_units=1,
                residential_floor_area_in_m2=150.0,
                commercial_floor_area_in_m2=0.0,
            ),
        ),
        annual_heat_demand_in_kwh=15000.0,
        living_area_in_m2=150.0,
        heated_floor_area_in_m2=160.0,
        current_cold_rent_in_euro_per_m2_month=8.5,
        building_specific_emissions_in_kg_per_m2_a=25.0,
    )


#: Fixed perspective set — not the shipped bundle, so a bundle data PR cannot silently
#: re-baseline the oracle. Covers gross/net, financing, and both sides of the actor split.
PERSPECTIVES = [
    Perspective(
        id="brownfield_gross",
        installation_context=InstallationContext.BROWNFIELD,
        subsidy_mode=SubsidyMode.none(),
    ),
    Perspective(
        id="brownfield_net",
        installation_context=InstallationContext.BROWNFIELD,
        subsidy_mode=SubsidyMode.full(),
    ),
    Perspective(
        id="financed_net",
        installation_context=InstallationContext.BROWNFIELD,
        subsidy_mode=SubsidyMode.full(),
        financing=FinancingPlan(financed_share=0.6, nominal_interest_rate=0.035, term_in_years=12),
    ),
    Perspective(
        id="landlord",
        installation_context=InstallationContext.BROWNFIELD,
        actor_scope=ActorScope.LANDLORD,
        subsidy_mode=SubsidyMode.full(),
    ),
    Perspective(
        id="tenant",
        installation_context=InstallationContext.BROWNFIELD,
        actor_scope=ActorScope.TENANT,
        subsidy_mode=SubsidyMode.full(),
    ),
]

#: The smallest scenario set that still renders section 9 (tornado + robustness): one axis, two
#: levels, ONE_AT_A_TIME — three evaluations. Interest rate is chosen because it moves every
#: perspective, so no scenario row can come out empty.
SCENARIO_SET = ScenarioSet.from_json(
    {
        "base": "central",
        "mode": "ONE_AT_A_TIME",
        "axes": [
            {"name": "interest", "field": "interest_rate", "levels": {"low": 0.01, "high": 0.05}},
        ],
    }
)


class RenderedReports:
    """The two rendered documents plus the objects they were rendered from.

    A plain container, not a builder: the fixture renders once and hands the result around, so
    every test in this module looks at the *same* two strings. Keeping the matrix, comparison and
    reference result alongside the text is what lets `TestFixtureIsRich` assert that the fixture
    really exercised loans, subsidies, feed-in and an actor split — checks that would otherwise
    have to re-evaluate and could then drift from what was rendered.
    """

    def __init__(self, summary: str, report: str, matrix, comparison, reference) -> None:
        """Holds the fixture's rendered output and its inputs, for the richness assertions."""
        self.summary = summary
        self.report = report
        self.matrix = matrix
        self.comparison = comparison
        self.reference = reference


@pytest.fixture(name="rendered", scope="module")
def fixture_rendered() -> RenderedReports:
    """Evaluates the fixture and renders both documents.

    Everything happens inside this one fixture, in a fixed order, so the rendered output does
    not depend on which subset of the tests below pytest was asked to run.
    """
    database = CostDatabase()
    catalog = SubsidyCatalog.load("DE")
    evaluator = EconomicEvaluator(database, PARAMETERS, catalog)
    inputs = make_inputs()

    matrix = EvaluationMatrix()
    for perspective in PERSPECTIVES:
        matrix.results[perspective.id] = evaluator.evaluate(inputs, perspective)

    reference_inputs = make_inputs(energy_kwh=15000.0, investment=2000.0)
    reference = evaluator.evaluate(reference_inputs, PERSPECTIVES[1])
    comparison = compare(reference, matrix.results["brownfield_net"], "base", "measures")

    cube = evaluate_cube(inputs, PARAMETERS, PERSPECTIVES[:2], SCENARIO_SET, database, catalog)
    plausibility = run_plausibility_checks(matrix)

    audit = build_input_audit(inputs, database, PARAMETERS, matrix.results["brownfield_gross"])
    summary = build_cost_summary_markdown(matrix, plausibility, comparison)
    report = build_lifecycle_report_html(matrix, plausibility, audit, comparison, scenario_cube=cube)
    return RenderedReports(summary, report, matrix, comparison, reference)


def _normalize(text: str) -> str:
    """The single documented substitution: today's date -> `<DATE>` (see the module docstring)."""
    return text.replace(datetime.date.today().isoformat(), "<DATE>")


def _assert_matches_golden(file_name: str, text: str) -> None:
    """Compares one rendered document against its checked-in golden, or regenerates it.

    Regeneration is deliberate and visible: the file is (re)written only when
    `HISIM_REGEN_GOLDENS=1` is set or no golden exists yet, and that path reports a *skip*, never
    a pass — so a missing or freshly written golden can never be mistaken for a verified one. On
    mismatch the failure carries a unified diff capped at 80 lines: enough to name the section and
    the figure that moved, without dumping a large HTML document into the test log.
    """
    path = os.path.join(GOLDEN_DIRECTORY, file_name)
    normalized = _normalize(text)
    if os.environ.get("HISIM_REGEN_GOLDENS") == "1" or not os.path.isfile(path):
        os.makedirs(GOLDEN_DIRECTORY, exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            file.write(normalized)
        pytest.skip(f"regenerated golden {file_name}")
    with open(path, encoding="utf-8") as file:
        expected = file.read()
    if normalized == expected:
        return
    import difflib

    diff = "\n".join(
        list(
            difflib.unified_diff(
                expected.splitlines(), normalized.splitlines(), "golden", "rendered", lineterm="", n=1
            )
        )[:80]
    )
    raise AssertionError(f"{file_name} drifted from its golden:\n{diff}")


class TestFixtureIsRich:
    """The oracle is only worth its runtime if the fixture reaches every rendering path."""

    def test_every_report_section_is_present(self, rendered):
        """All sections the switch-over touches actually render on this fixture."""
        for marker in (
            "0 - Plausibility panel",
            "1 - Input audit",
            "sources used",
            "2 - Investment build-up",
            "3 - Cash-flow timeline",
            "4 - Year-1 energy bill",
            "4b - Lifecycle CO2",
            "5 - Subsidy decisions",
            "6 - Perspectives at a glance",
            "6b - Who pays what",
            "7 - Per-component breakdown",
            "8 - Variant comparison",
            "9 - Scenario analysis",
            "10 - Lifecycle KPIs",
        ):
            assert marker in rendered.report, marker

    def test_every_computation_path_is_exercised(self, rendered):
        """Bands, a loan, subsidies, feed-in, anyway credits and an allocation are all present."""
        from hisim.economics.timeline import Actor, CostCategory

        financed = rendered.matrix.results["financed_net"]
        categories = {entry.category for entry in financed.timeline.entries}
        assert not financed.total_npv_in_euro.is_exact()  # banded -> whiskers + band polygon
        assert CostCategory.LOAN_INTEREST in categories  # loan amortization chart
        assert CostCategory.SUBSIDY in categories  # subsidy composition chart
        assert CostCategory.FEED_IN_REVENUE in categories  # year-1 bill credit
        assert CostCategory.ANYWAY_COST_CREDIT in categories  # brownfield credit rows
        assert financed.subsidy_decisions  # decision cards + awards table
        landlord = rendered.matrix.results["landlord"]
        assert landlord.scope_payer == Actor.LANDLORD  # scoped charts differ from the full timeline
        assert {entry.payer for entry in landlord.timeline.entries} != {Actor.SYSTEM}
        assert rendered.comparison.npv_delta_by_subject  # delta waterfall + payback curve

    def test_rendering_is_deterministic(self, rendered):
        """Rendering the same objects twice yields the same bytes (no set-ordering leaks)."""
        plausibility = run_plausibility_checks(rendered.matrix)
        again = build_cost_summary_markdown(rendered.matrix, plausibility, rendered.comparison)
        assert again == rendered.summary


class TestStoredResultsRoundTrip:
    """W4.5: a report rendered from stored files is the report rendered from memory."""

    def test_reloaded_matrix_renders_identically(self, rendered, tmp_path):
        """Write the export set, read it back, render — byte-identical to the direct render.

        This is what lets `python -m hisim.economics report` stop re-running the engine: if the
        stored files could not reproduce the rendering, the CLI would be quietly reporting on a
        second evaluation rather than on the one that was stored.
        """
        from hisim.economics.audit import build_input_audit
        from hisim.economics.exports import (
            write_cash_flow_timeline,
            write_lifecycle_costs_json,
            write_provenance_ledger,
        )
        from hisim.economics.input_audit import read_input_audit, write_input_audit
        from hisim.economics.serialization import read_results

        directory = str(tmp_path)
        write_lifecycle_costs_json(rendered.matrix, directory)
        write_cash_flow_timeline(rendered.matrix, directory)
        write_provenance_ledger(rendered.matrix, directory)
        database = CostDatabase()
        audit = build_input_audit(
            make_inputs(), database, PARAMETERS, rendered.matrix.results["brownfield_gross"]
        )
        write_input_audit(audit, directory)

        reloaded = read_results(directory)
        assert reloaded is not None and list(reloaded.results) == list(rendered.matrix.results)
        reloaded_audit = read_input_audit(directory)
        plausibility = run_plausibility_checks(reloaded)
        assert build_cost_summary_markdown(reloaded, plausibility, rendered.comparison) == rendered.summary
        # Section 9 needs a fresh cube (it is a set of evaluations, not a stored result), so the
        # comparison here is against the same report without it.
        direct = build_lifecycle_report_html(
            rendered.matrix, run_plausibility_checks(rendered.matrix), audit, rendered.comparison
        )
        assert build_lifecycle_report_html(
            reloaded, plausibility, reloaded_audit, rendered.comparison
        ) == direct

    def test_reload_preserves_scope_and_physical_context(self, rendered, tmp_path):
        """The fields the reports need but a naive DTO reload would silently drop."""
        from hisim.economics.exports import write_cash_flow_timeline, write_lifecycle_costs_json
        from hisim.economics.serialization import read_results
        from hisim.economics.timeline import Actor

        directory = str(tmp_path)
        write_lifecycle_costs_json(rendered.matrix, directory)
        write_cash_flow_timeline(rendered.matrix, directory)
        reloaded = read_results(directory)
        assert reloaded is not None
        landlord = reloaded.results["landlord"]
        original = rendered.matrix.results["landlord"]
        assert landlord.scope_payer == Actor.LANDLORD
        assert landlord.simulation_year == 2026
        assert len(landlord.timeline.entries) == len(original.timeline.entries)
        assert len(landlord.scoped_timeline().entries) == len(original.scoped_timeline().entries)
        assert landlord.timeline.entries[0].subject_kind == original.timeline.entries[0].subject_kind
        assert (
            landlord.annual_energy_quantities_by_carrier.keys()
            == original.annual_energy_quantities_by_carrier.keys()
        )
        assert landlord.subsidy_decisions[0].applied[0].scheme_id == (
            original.subsidy_decisions[0].applied[0].scheme_id
        )

    def test_report_cli_prefers_stored_results(self, rendered, tmp_path, capsys):
        """`report` on a directory with stored results does not re-evaluate."""
        from hisim.economics.__main__ import main
        from hisim.economics.exports import write_cash_flow_timeline, write_lifecycle_costs_json
        from hisim.economics.serialization import write_inputs

        directory = tmp_path / "stored"
        directory.mkdir()
        write_inputs(make_inputs(), str(directory))
        write_lifecycle_costs_json(rendered.matrix, str(directory))
        write_cash_flow_timeline(rendered.matrix, str(directory))
        assert main(["report", str(directory)]) == 0
        assert "re-evaluating" not in capsys.readouterr().out
        summary = (directory / "cost_summary.md").read_text(encoding="utf-8")
        # The perspective set is the stored one, not the default bundle's.
        assert "financed_net" in summary and "landlord" in summary


class TestGoldenFiles:
    """The oracle itself."""

    def test_cost_summary_markdown_matches_golden(self, rendered):
        """`cost_summary.md`, byte-for-byte."""
        _assert_matches_golden(SUMMARY_GOLDEN, rendered.summary)

    def test_lifecycle_report_html_matches_golden(self, rendered):
        """`lifecycle_report.html`, byte-for-byte (numbers, SVG geometry, tooltips)."""
        _assert_matches_golden(REPORT_GOLDEN, rendered.report)
