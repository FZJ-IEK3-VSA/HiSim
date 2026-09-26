"""T-E2E: the vendored mockup through ``run`` twice, then through ``staged`` once.

Everything else about the staged economics is checked on synthetic stages, where a failure is a
statement about the splice. This case checks the seam the synthetic tests cannot: that a real
RenoVisor job leaves behind an ``economic_inputs.json`` the staged evaluator can read, that the
translator's register and envelope subjects survive the round trip through that file, and that
the document the backend serves comes out valid on the end of it.

The plan is the one step 10 §7 names: the mockup's baseline and its five-measure package, both
in year 0 -- the ordinary "what does the package cost against doing nothing" question -- run over
a single January day so the whole case finishes in seconds.
"""

import copy
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from hisim.economics.__main__ import main as economics_main
from hisim.economics.staged_document import StagedDocument
from hisim.economics.subsidies import SubsidyCatalog
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.request import CatalogueTable
from hisim.renovisor.run import Calculation, ExitCode
from hisim.renovisor.simulation import Period, SimulationSetup

pytestmark = pytest.mark.system_setups

#: Where the recorded twins the translator writes into live.
BASE_FILES = Path(__file__).resolve().parents[2] / "energy_systems"

#: The ``--parameters`` block the plan is priced with: the shape ``economics-backend-spec.md``
#: §2.1 sends and ``economics_result.json`` publishes, with no country in it.
STAGED_PARAMETERS = {
    "horizon_years": 20,
    "interest_rate": 0.03,
    "perspective_id": "brownfield_net",
    "financing": {"kind": "cash"},
    "subsidy_mode": "full",
}


def _document(with_measures: bool) -> Dict[str, Any]:
    """The vendored mockup, with its package or with an empty one."""
    document = copy.deepcopy(ContractFiles.request_mockup())
    if not with_measures:
        document["measures"] = []
    return document


def _run(document: Dict[str, Any], directory: Path, name: str) -> Path:
    """Run one request over a single January day and return its output directory."""
    request_path = directory / f"{name}.json"
    request_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    out = directory / name
    code = Calculation(
        request_path=request_path,
        output_directory=out,
        period=Period.ONE_DAY_15MIN,
        base_files_directory=BASE_FILES,
    ).run()
    assert code == ExitCode.FINISHED, f"the {name} run did not finish"
    return out


@pytest.fixture(name="runs", scope="module")
def fixture_runs(tmp_path_factory) -> Tuple[Path, Path, Path]:
    """The mockup's baseline and package, run once for the whole module.

    The two runs are the expensive part of the case and three fixtures below read them, so
    running them once is what keeps the whole file at a few seconds.

    Returns:
        ``(the working directory, the baseline job directory, the package job directory)``.
    """
    directory = tmp_path_factory.mktemp("staged_economics")
    baseline = _run(_document(with_measures=False), directory, "baseline")
    package = _run(_document(with_measures=True), directory, "package")
    return directory, baseline, package


@pytest.fixture(name="parameters_file", scope="module")
def fixture_parameters_file(runs: Tuple[Path, Path, Path]) -> Path:
    """The ``--parameters`` file every staged invocation below is given.

    The block in the shape the backend sends (`economics-backend-spec.md` §2.1) and the document
    publishes. It names no country on purpose: an Irish request must be priced as Irish because
    its stages were, never because somebody wrote "IE" twice (shared todo H19).
    """
    directory, _baseline, _package = runs
    path = directory / "economics.json"
    path.write_text(json.dumps(STAGED_PARAMETERS), encoding="utf-8")
    return path


def _price(stages: List[str], parameters_file: Path, out: Path) -> Dict[str, Any]:
    """Run ``staged`` over the given ``--stage`` arguments and read the document back.

    Args:
        stages: The ``--stage`` arguments, in stage order.
        parameters_file: The ``--parameters`` file.
        out: Where the document goes.

    Returns:
        The written document.
    """
    arguments = ["staged"]
    for stage in stages:
        arguments += ["--stage", stage]
    arguments += ["--parameters", str(parameters_file), "--out", str(out)]
    code = economics_main(arguments)
    problems = out.parent / "problems.json"
    assert code == 0, (
        problems.read_text(encoding="utf-8")
        if problems.is_file()
        else "staged failed with no problems.json"
    )
    document: Dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return document


@pytest.fixture(name="document", scope="module")
def fixture_document(runs, parameters_file) -> Dict[str, Any]:
    """Price the two-stage plan out of the two job directories, as a hand-run plan does."""
    directory, baseline, package = runs
    return _price(
        [f"{baseline}:0:baseline", f"{package}:0:package"],
        parameters_file,
        directory / StagedDocument.FILE_NAME,
    )


@pytest.fixture(name="backend_document", scope="module")
def fixture_backend_document(runs, parameters_file) -> Dict[str, Any]:
    """Price the same plan out of the stage directories a backend's worker assembles.

    ``economics-backend-spec.md`` §3: the worker copies each stage job's ``economic_inputs.json``
    and ``mapping_report.json`` into ``<JobDir>/stages/<index>/`` and nothing else — no
    ``lifecycle_costs.json``, so no stored parameter record to read a country, a price basis year
    or a catalogue path out of. This is the layout every real economics job runs in, and the one
    the synthetic cases cannot check.

    The country and the basis year come out of the extracts, which carry them. The subsidy
    catalogue does not and is not supposed to: no flag is passed here, and the plan still prices
    Ireland's grants, because a staged run falls back to the shipped ``hisim/subsidy_catalog``
    directory when it has the country's file (step 11 §3, item 12).
    """
    directory, baseline, package = runs
    root = directory / "backend_stages"
    stages = []
    for index, job in enumerate((baseline, package)):
        stage = root / str(index)
        stage.mkdir(parents=True)
        shutil.copyfile(job / "results" / "economic_inputs.json", stage / "economic_inputs.json")
        shutil.copyfile(job / "mapping_report.json", stage / "mapping_report.json")
        stages.append(f"{stage}:0:{'baseline' if index == 0 else 'package'}")
    return _price(stages, parameters_file, root / StagedDocument.FILE_NAME)


class TestThePlanStartYear:
    """The weather year and the plan's calendar are different things (renovisorissues #57).

    The mockup's runs are simulated with the weather of :attr:`SimulationSetup.YEAR`. A plan the
    reader starts in 2026 is dated from 2026, and the weather year is published under its own name.
    """

    START = 2026

    @pytest.fixture(name="dated_document", scope="class")
    def fixture_dated_document(self, runs) -> Dict[str, Any]:
        """The same two-stage plan, priced with ``plan_start_year`` 2026 in its block."""
        directory, baseline, package = runs
        path = directory / "economics_dated.json"
        path.write_text(json.dumps({**STAGED_PARAMETERS, "plan_start_year": self.START}), encoding="utf-8")
        return _price(
            [f"{baseline}:0:baseline", f"{package}:0:package"], path, directory / "dated" / StagedDocument.FILE_NAME
        )

    def test_the_first_row_is_the_start_year_and_the_weather_year_stays_the_weathers(
        self, dated_document
    ) -> None:
        """Year 0 is 2026; ``weather_year`` is the year of the weather the runs used."""
        StagedDocument.validate(dated_document)
        assert SimulationSetup.YEAR != self.START
        assert dated_document["parameters"]["plan_start_year"] == self.START
        assert dated_document["parameters"]["weather_year"] == SimulationSetup.YEAR
        for variant in ("reference", "plan"):
            annual = dated_document[variant]["annual"]
            assert annual[0]["calendar_year"] == self.START
            assert [row["calendar_year"] for row in annual] == [self.START + row["year"] for row in annual]

    def test_without_it_nothing_is_dated(self, document) -> None:
        """No start year in the block, no calendar year in the document -- never the weather's."""
        assert document["parameters"]["plan_start_year"] is None
        assert document["parameters"]["weather_year"] == SimulationSetup.YEAR
        assert all(row["calendar_year"] is None for row in document["plan"]["annual"])

    def test_the_cylinder_is_due_in_its_calendar_year(self, dated_document) -> None:
        """Start year = basis year: the cylinder (2008, 20 years) is due in year 2, dated 2028.

        hisim-nl6j's "done when": installation_year + service_life_years - (year-0 calendar year)
        is the published replacement year, and that year's calendar_year is 2008 + 20.
        """
        assert dated_document["parameters"]["plan_start_year"] == dated_document["parameters"]["price_basis_year"]
        for variant in ("reference", "plan"):
            row = {row["subject"]: row for row in dated_document[variant]["by_subject"]}["DHWStorage"]
            due = row["replacement_years"][0]
            assert due == row["installation_year"] + row["service_life_years"] - self.START == 2, variant
            calendar = dated_document[variant]["annual"][due]["calendar_year"]
            assert calendar == row["installation_year"] + row["service_life_years"] == 2028, variant

    def test_a_later_start_ages_the_house_at_the_start_year(self, runs, document) -> None:
        """Start year = basis + 3: kept equipment falls due 3 plan years earlier, in the same calendar year.

        Plan year 0 is the start year (hisim-nl6j), while prices stay at the basis year 2026. The
        meters (mid-life 2018, 16 years, due 2034) move from year 8 to year 5, and 2029 + 5 = 2034.
        The cylinder (2008 + 20 = 2028) would fall 2 - 3 = -1: it is overdue at year 0 and the
        engine replaces an overdue kept asset in year 1 (``max(1, ...)``), dated 2030.
        """
        directory, baseline, package = runs
        basis = document["parameters"]["price_basis_year"]
        start = basis + 3
        path = directory / "economics_later.json"
        path.write_text(json.dumps({**STAGED_PARAMETERS, "plan_start_year": start}), encoding="utf-8")
        later = _price(
            [f"{baseline}:0:baseline", f"{package}:0:package"], path, directory / "later" / StagedDocument.FILE_NAME
        )
        assert later["parameters"]["price_basis_year"] == basis
        for variant in ("reference", "plan"):
            before = {row["subject"]: row for row in document[variant]["by_subject"]}
            after = {row["subject"]: row for row in later[variant]["by_subject"]}
            meter = after["ElectricityMeter"]
            assert meter["replacement_years"][0] == before["ElectricityMeter"]["replacement_years"][0] - 3, variant
            due = meter["replacement_years"][0]
            assert later[variant]["annual"][due]["calendar_year"] == meter["installation_year"] + round(
                meter["service_life_years"]
            ), variant
            assert after["DHWStorage"]["replacement_years"][0] == 1, variant
        package_heat_pump = {row["subject"]: row for row in later["plan"]["by_subject"]}["MoreAdvancedHeatPumpHPLib"]
        assert package_heat_pump["installation_year"] == start

    def test_it_dates_the_money_and_does_not_change_it(self, dated_document, document) -> None:
        """The stages state their price basis year, so the start year cannot re-base them."""
        assert dated_document["parameters"]["price_basis_year"] == document["parameters"]["price_basis_year"]
        assert dated_document["plan"]["totals"] == document["plan"]["totals"]
        assert dated_document["comparison"] == document["comparison"]


class TestTheEndToEndDocument:
    """What the backend gets when it prices the mockup's package against doing nothing."""

    def test_the_document_validates_against_its_schema(self, document) -> None:
        """The file is a contract with a frontend that cannot check it, so this does."""
        StagedDocument.validate(document)

    def test_the_irish_request_is_priced_as_irish(self, document) -> None:
        """The country comes from the stages' own runs, never from a default (shared todo H19).

        The parameters file names none, and the mockup is an Irish house: a staged run that took
        the engine record's ``"DE"`` default would publish German costs here, with nothing in the
        document to say so.
        """
        assert document["parameters"]["country"] == "IE"

    def test_the_block_that_priced_it_is_the_block_it_publishes(self, document) -> None:
        """Input and output are one vocabulary, so a reader can re-run what they are reading."""
        block = document["parameters"]
        for key, value in STAGED_PARAMETERS.items():
            assert block[key] == value, key

    def test_both_stages_are_in_it_in_year_zero(self, document) -> None:
        """The ordinary baseline-versus-package plan: the package supersedes the baseline at once."""
        assert [stage["label"] for stage in document["stages"]] == ["baseline", "package"]
        assert [stage["from_year"] for stage in document["stages"]] == [0, 0]

    def test_the_package_stage_names_the_measures_it_carried_out(self, document) -> None:
        """hisim-cyc.4: the stage timeline labels itself from this list, so it must be filled.

        The package's five measures, in catalogue order and with the not-implemented ones left
        out -- the baseline names nothing, because it carries out nothing.
        """
        baseline, package = document["stages"]

        assert baseline["measures"] == []
        order = list(CatalogueTable.ids())
        assert package["measures"] == sorted(
            [
                "heating_system",
                "heating_installation",
                "external_insulation",
                "photovoltaic_system",
                "hot_water_tank_and_pipe_insulation",
            ],
            key=order.index,
        )

    def test_the_comparison_is_present_and_has_a_payback_verdict(self, document) -> None:
        """A plan is a difference question, so the comparison is not an optional extra."""
        comparison = document["comparison"]
        assert set(comparison["npv_delta_in_euro"]) == {"min", "best", "max"}
        assert set(comparison["discounted_payback_year"]) == {"min", "best", "max"}

    def test_the_heat_pump_carries_its_measure_and_its_stage(self, document) -> None:
        """Chart V4 filters the investment build-up by these two fields and by nothing else."""
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        heat_pumps = [
            row
            for row in rows.values()
            for asset_class in [row["asset_class"]]
            if asset_class == "HeatPump"
        ]
        assert heat_pumps, f"no heat pump among {sorted(rows)}"
        assert any(row["measure_id"] == "heating_system" for row in heat_pumps)
        assert any(row["stage"] == 1 for row in heat_pumps)

    def test_the_envelope_subject_is_priced_by_its_cost_block(self, document) -> None:
        """The mockup carries the band out of materials.yaml, so the subject has an investment."""
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        assert "external_insulation" in rows
        assert rows["external_insulation"]["unpriced"] is False
        assert rows["external_insulation"]["investment_in_euro"]["best"] > 0.0

    def test_the_subsidy_rows_are_no_longer_all_undetermined(self, document) -> None:
        """Ireland has a catalogue now, so the rows carry real verdicts (step 11 §3.12)."""
        rows = document["plan"]["subsidies"]
        assert rows
        statuses = {row["status"] for row in rows}
        assert statuses - {"undetermined"}, "every row is still undetermined"
        assert all(row["scheme"] for row in rows), "a row names no scheme, i.e. no catalogue ran"

    def test_the_heat_pump_unit_grant_is_awarded(self, document) -> None:
        """The mockup is a detached 1975 house buying a heat pump: SEAI pays 6,500 EUR for it."""
        awarded = {
            row["scheme"]: row for row in document["plan"]["subsidies"] if row["status"] == "awarded"
        }
        assert "IE_SEAI_HEAT_PUMP_UNIT_HOUSE" in awarded, sorted(awarded)
        # Support is signed as a credit on the timeline, so the published band is negative.
        amount = awarded["IE_SEAI_HEAT_PUMP_UNIT_HOUSE"]["amount_in_euro"]
        assert amount["best"] == pytest.approx(-6500.0)

    def test_no_row_asks_what_the_house_heats_with(self, document) -> None:
        """The request states it, so the heat-pump schemes decide on it (renovisorissues #50).

        The mockup's gas boiler is replaced by a heat pump, which is what SEAI's renewable heat
        bonus pays 4,000 EUR for; before the subsidy context carried the existing heating, the row
        was undetermined and asked for the boiler's class and carrier.
        """
        rows = document["plan"]["subsidies"]
        asked = {name for row in rows for name in row["open_questions"]}
        assert not {name for name in asked if name.startswith("building.existing_heating.")}, sorted(asked)
        awarded = {
            (row["scheme"], row["measure_id"]): row for row in rows if row["status"] == "awarded"
        }
        assert ("IE_SEAI_RENEWABLE_HEAT_BONUS", "heating_system") in awarded, sorted(awarded)
        amount = awarded[("IE_SEAI_RENEWABLE_HEAT_BONUS", "heating_system")]["amount_in_euro"]
        assert amount["best"] == pytest.approx(-4000.0)

    def test_the_solar_pv_grant_is_the_tiered_formula_on_the_arrays_cost_facts_size(self, runs, document) -> None:
        """hisim-cyc.3 through the production wiring: the grant prices the size the stage extracted.

        The size is read from the package stage's ``economic_inputs.json`` — the cost facts the
        adapter built from ``PVSystem.power_in_watt`` — and the awarded row must be SEAI's rule on
        exactly that size: 700 EUR/kWp to 2 kWp, 200 EUR/kWp to 4 kWp, at most 1,800 EUR.
        """
        _directory, _baseline, package = runs
        extract = json.loads((package / "results" / "economic_inputs.json").read_text(encoding="utf-8"))
        arrays = [entry["facts"] for entry in extract["cost_facts"] if entry["facts"]["asset_class"] == "PV"]
        assert len(arrays) == 1, arrays
        assert arrays[0]["size_unit"] == "KILOWATT", "the scheme is an amount per kW"
        size = arrays[0]["size"]
        expected = min(700.0 * min(size, 2.0) + 200.0 * max(0.0, min(size, 4.0) - 2.0), 1800.0)
        awarded = {
            row["scheme"]: row for row in document["plan"]["subsidies"] if row["status"] == "awarded"
        }
        assert "IE_SEAI_SOLAR_PV" in awarded, sorted(awarded)
        amount = awarded["IE_SEAI_SOLAR_PV"]["amount_in_euro"]
        for slot in ("min", "best", "max"):
            assert amount[slot] == pytest.approx(-expected), (
                f"a {size:g} kW array should be granted {expected:g} EUR in the {slot} slot, got {-amount[slot]:g}"
            )

    def test_every_row_names_a_scheme_whose_display_name_carries_the_ai_marker(self, document) -> None:
        """Step 11 §1: a user must see that the Irish amounts are an unexamined AI draft.

        The document puts the scheme *id* in ``scheme`` and the display name in ``note`` of an
        awarded row, so the marker is checked on the catalogue entry every row points at — which
        is the string a report renders — and, where the document carries it, on the note too.
        """
        catalog = SubsidyCatalog.load("IE")
        marker = " [AI draft \u2014 needs examination]"
        for row in document["plan"]["subsidies"]:
            scheme = catalog.scheme_by_id(row["scheme"])
            assert scheme is not None, row["scheme"]
            assert scheme.label.endswith(marker), scheme.id
            if row["status"] == "awarded":
                assert str(row["note"]).endswith(marker), row

    def test_the_reference_costs_money(self, document) -> None:
        """Doing nothing has a price too: twenty years of gas, maintenance and replacements."""
        assert document["reference"]["totals"]["npv_in_euro"]["best"] > 0

    def test_every_row_that_sells_states_a_negative_revenue(self, document) -> None:
        """Sold kilowatt hours earn money, and money arriving is negative (spec §3, renovisorissues #47).

        The package's array feeds in at the Irish ``ELECTRICITY_FEED_IN`` rate; the row used to
        report the kWh it sold and a revenue of zero, because the engine books that revenue under
        the feed-in subject and the row read only the carrier's own.
        """
        selling = [row for row in document["plan"]["energy_year1"] if row["sold_in_kwh"] > 0]
        assert selling, "the package's array sells nothing, so this case would prove nothing"
        for variant in ("reference", "plan"):
            for row in document[variant]["energy_year1"]:
                revenue = row["revenue_in_euro"]
                if row["sold_in_kwh"] > 0:
                    assert revenue["best"] < 0 and revenue["max"] <= 0, (variant, row)
                else:
                    assert revenue == {"min": 0.0, "best": 0.0, "max": 0.0}, (variant, row)

    def test_the_stacks_add_up_on_the_written_file(self, document) -> None:
        """The document's own promise, on a real run rather than on a synthetic plan."""
        for variant in ("reference", "plan"):
            evaluation = document[variant]
            for slot in ("min", "best", "max"):
                stack = sum(band[slot] for band in evaluation["by_group"].values())
                assert stack == pytest.approx(evaluation["totals"]["npv_in_euro"][slot], abs=0.01)

    def test_the_headline_monthly_figure_is_the_annuity_over_twelve(self, document) -> None:
        """hisim-cyc.6 on a real run: both evaluations carry EAC / 12, and so does the comparison."""
        for variant in ("reference", "plan"):
            totals = document[variant]["totals"]
            assert totals["monthly_equivalent_cost_in_euro"]["best"] == pytest.approx(
                totals["equivalent_annual_cost_in_euro"]["best"] / 12.0
            )
        comparison = document["comparison"]
        assert comparison["monthly_equivalent_cost_delta_in_euro"]["best"] == pytest.approx(
            comparison["equivalent_annual_cost_delta_in_euro"]["best"] / 12.0
        )

    def test_both_evaluations_publish_a_cost_per_kwh_of_heat(self, document, runs) -> None:
        """hisim-4p86: the denominator is the useful heat each stage's run measured.

        Both job directories record the rooms' heat plus the hot water, and the package's
        insulation leaves the house needing less of it. The published figure is bound to that
        heat: the equivalent annual cost the document publishes, divided by the measured heat
        annualized as the bills are. The reference is the baseline alone; the plan's two stages
        both start in year 0, so its heat is the package's for the whole horizon.
        """
        _directory, baseline, package = runs
        annual_heat = {}
        for variant, job in (("reference", baseline), ("plan", package)):
            (inputs_file,) = job.rglob("economic_inputs.json")
            inputs = json.loads(inputs_file.read_text(encoding="utf-8"))
            by_kind = inputs["useful_heat_of_simulated_period_by_kind_in_kwh"]
            assert set(by_kind) == {"ROOM_HEATING", "HOT_WATER"}, variant
            measured = inputs["useful_heat_of_simulated_period_in_kwh"]
            assert measured == pytest.approx(sum(by_kind.values()), rel=1e-12), variant
            annual_heat[variant] = measured / inputs["simulated_period_fraction"]
        assert 0 < annual_heat["plan"] < annual_heat["reference"]
        for variant, heat in annual_heat.items():
            totals = document[variant]["totals"]
            heat_cost = totals["levelized_cost_of_heat_in_euro_per_kwh"]
            assert heat_cost is not None and heat_cost["best"] > 0, variant
            for slot in ("min", "best", "max"):
                assert heat_cost[slot] == pytest.approx(
                    totals["equivalent_annual_cost_in_euro"][slot] / heat, rel=1e-9
                ), (variant, slot)

    def test_the_subsidy_stack_is_the_awarded_rows(self, document) -> None:
        """hisim-cyc.5 on a real run: the grant the stacks book is the grant the rows award."""
        StagedDocument.assert_subsidies_reconciled(document)
        awarded = [row for row in document["plan"]["subsidies"] if row["status"] == "awarded"]
        booked = sum(year["by_group"]["Subsidies"]["best"] for year in document["plan"]["annual"])
        assert booked < 0, "the mockup's heat pump grant must reach the stack"
        assert booked == pytest.approx(sum(row["amount_in_euro"]["best"] for row in awarded), abs=0.01)

    def test_the_run_still_leaves_no_costs_block_in_the_payload(self, document) -> None:
        """The other half of the split: one implementation of the money, and this is it."""
        assert document["plan"]["totals"]["npv_in_euro"]["best"] != 0.0


class TestTheEquipmentTheHouseAlreadyHas:
    """renovisorissues #48 and hisim-fig7 on a real run: the house's own vessels, emitters and meter.

    The mockup's gas house has a buffer, a hot-water cylinder, radiators, a gas meter and an
    electricity meter, none of which the request describes. Before they were in the register the
    do-nothing reference bought all of them in year 0 and was awarded SEAI's central-heating grant
    for its own radiators, and the plan's buffer row stamped stage 1 carried stage 0's purchase.
    """

    def test_the_reference_buys_nothing_in_year_zero(self, document) -> None:
        """Doing nothing buys nothing: every part of the reference is already in the house."""
        reference = document["reference"]
        assert reference["totals"]["investment_year0_in_euro"]["best"] == pytest.approx(0.0)
        for row in reference["by_subject"]:
            assert row["investment_in_euro"]["best"] == pytest.approx(0.0), row["subject"]

    def test_the_reference_is_awarded_no_central_heating_grant(self, document) -> None:
        """hisim-fig7: the radiators are kept, so no scheme is even asked about them."""
        schemes = {row["scheme"] for row in document["reference"]["subsidies"]}
        assert "IE_SEAI_HEAT_PUMP_CENTRAL_HEATING_HOUSE" not in schemes

    def test_the_new_emitters_are_the_heating_installations_and_so_is_their_grant(self, document) -> None:
        """The mockup's heating_installation replaces the radiators; the grant follows the measure."""
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        assert rows["HeatDistributionSystem"]["measure_id"] == "heating_installation"
        grants = [
            row for row in document["plan"]["subsidies"] if row["scheme"] == "IE_SEAI_HEAT_PUMP_CENTRAL_HEATING_HOUSE"
        ]
        assert [(row["status"], row["measure_id"], row["stage"]) for row in grants] == [
            ("awarded", "heating_installation", 1)
        ]

    def test_the_buffer_is_a_new_vessel_bought_by_the_heating_system_in_stage_one(self, runs, document) -> None:
        """The whole heat-pump vessel at its price, in stage 1 only, the old one credited.

        The price is the cost database's own for the package's vessel size, so the row is the new
        vessel and not the increment over the old one that the splice used to charge.
        """
        from hisim.economics.database import CostDatabase
        from hisim.loadtypes import ComponentType

        _directory, _baseline, package = runs
        extract = json.loads((package / "results" / "economic_inputs.json").read_text(encoding="utf-8"))
        (facts,) = [entry["facts"] for entry in extract["cost_facts"] if entry["subject"] == "SimpleHotWaterStorage"]
        assert facts["asset_class"] == "SPACE_HEATING_STORAGE"
        entry = CostDatabase(None).get_device_entry(
            ComponentType.SPACE_HEATING_STORAGE, document["parameters"]["price_basis_year"], "IE"
        )
        price = entry.investment_for_size(facts["size"])

        row = {row["subject"]: row for row in document["plan"]["by_subject"]}["SimpleHotWaterStorage"]
        assert row["measure_id"] == "heating_system"
        assert row["stage"] == 1
        assert [stage["stage"] for stage in row["investment_by_stage"]] == [1]
        assert row["investment_by_stage"][0]["investment_in_euro"]["best"] == pytest.approx(price.best_estimate)
        assert row["investment_in_euro"]["best"] == pytest.approx(price.best_estimate)
        # What the row's NPV holds beyond the columns it lists is the anyway credit for the old
        # vessel, which the house would have had to replace within the threshold anyway.
        listed = sum(
            row[column]["best"]
            for column in (
                "investment_in_euro",
                "subsidy_in_euro",
                "replacements_in_euro",
                "maintenance_in_euro",
                "residual_value_in_euro",
            )
        )
        assert row["npv_in_euro"]["best"] - listed < 0.0

    def test_the_cylinder_the_radiators_and_the_meters_are_kept(self, document) -> None:
        """Kept equipment carries no measure and no purchase in any stage."""
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        for subject in ("DHWStorage", "ElectricityMeter"):
            assert rows[subject]["measure_id"] is None, subject
            assert rows[subject]["investment_by_stage"] == [], subject

    def test_every_plan_row_splits_its_investment_by_stage(self, document) -> None:
        """Both stages start in year 0, so each row's split sums to its plan-wide figure."""
        for row in document["plan"]["by_subject"]:
            for slot in ("min", "best", "max"):
                split = sum(stage["investment_in_euro"][slot] for stage in row["investment_by_stage"])
                assert split == pytest.approx(row["investment_in_euro"][slot], abs=0.01), (row["subject"], slot)

    def test_the_hot_water_lagging_has_its_unpriced_row(self, document) -> None:
        """The measure the plan bought is on the cost list, flagged and explained (renovisorissues #58)."""
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        row = rows["hot_water_tank_and_pipe_insulation"]
        assert row["measure_id"] == "hot_water_tank_and_pipe_insulation"
        assert row["stage"] == 1
        assert row["unpriced"] is True
        assert "hisim-5j3h" in row["note"] and "renovisorissues #39" in row["note"]
        assert row["service_life_years"] is None and row["installation_year"] is None
        reference = {row["subject"] for row in document["reference"]["by_subject"]}
        assert "hot_water_tank_and_pipe_insulation" not in reference

    def test_every_measure_a_stage_carries_out_has_a_row(self, document) -> None:
        """What the document refuses to be written without, checked on the written file."""
        named = {row["measure_id"] for row in document["plan"]["by_subject"]}
        for stage in document["stages"]:
            assert set(stage["measures"]) <= named, stage["label"]

    def test_the_cylinder_states_the_life_and_the_year_its_replacement_follows_from(self, document) -> None:
        """Why the kept cylinder is replaced in year 2, in published numbers (renovisorissues #58).

        The mockup states ``heating.installation_year`` 2008, which dates the cylinder too, and the
        cost database gives a domestic hot-water storage 20 years. The engine ages a kept asset at
        the price basis year, so its replacement falls 2008 + 20 - 2026 = 2 years into the horizon.
        """
        for variant in ("reference", "plan"):
            row = {row["subject"]: row for row in document[variant]["by_subject"]}["DHWStorage"]
            assert (row["service_life_years"], row["service_life_origin"]) == (20.0, "cost_database"), variant
            assert (row["installation_year"], row["installation_year_origin"]) == (2008, "request"), variant
            basis = document["parameters"]["price_basis_year"]
            due = row["installation_year"] + row["service_life_years"] - basis
            assert row["replacement_years"][0] == round(due) == 2, variant

    def test_the_meters_are_dated_at_mid_life(self, document) -> None:
        """The request dates no meter, so the translator's mid-life year stands, and the row says so."""
        row = {row["subject"]: row for row in document["reference"]["by_subject"]}["ElectricityMeter"]
        assert row["installation_year_origin"] == "mid_life_default"
        basis = document["parameters"]["price_basis_year"]
        assert row["installation_year"] == basis - round(row["service_life_years"] / 2)

    def test_what_the_package_buys_is_installed_in_its_stages_year(self, document) -> None:
        """The heat pump is bought by the package stage, which starts in the plan's year 0.

        The block states no ``plan_start_year``, so year 0 is the price basis year (hisim-dutz):
        2026 + from_year 0. Until then it was the weather year, 2019, seven years before the year
        the engine ages everything else at.
        """
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        heat_pump = rows["MoreAdvancedHeatPumpHPLib"]
        assert document["parameters"]["weather_year"] != document["parameters"]["price_basis_year"]
        assert heat_pump["installation_year"] == document["parameters"]["price_basis_year"]
        assert heat_pump["installation_year_origin"] == "stage"
        assert heat_pump["service_life_origin"] == "cost_database"


#: The measures of the co-installation cases: the mockup's own two heating measures.
HEAT_PUMP = {"id": "heating_system", "options": {"type_of_system": "air_source_heat_pump"}}
RADIATORS = {"id": "heating_installation", "options": {"type_of_system": "low_temperature_radiator"}}


@pytest.fixture(name="heating_plans", scope="module")
def fixture_heating_plans(tmp_path_factory, parameters_file):
    """Price a house heated by one generator against one package, each pair run at most once.

    Returns:
        A function ``(generator, package name) -> document``; the package names are
        ``"radiators"`` (heating_installation alone) and ``"heat_pump_and_radiators"``.
    """
    directory = tmp_path_factory.mktemp("heating_plans")
    packages = {"radiators": [RADIATORS], "heat_pump_and_radiators": [HEAT_PUMP, RADIATORS]}
    jobs: Dict[str, Path] = {}
    documents: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def job(generator: str, package: str) -> Path:
        name = f"{generator}-{package}"
        if name not in jobs:
            document = _document(with_measures=False)
            document["house"]["heating"] = {"type_of_system": generator}
            document["measures"] = copy.deepcopy(packages.get(package, []))
            jobs[name] = _run(document, directory, name)
        return jobs[name]

    def priced(generator: str, package: str) -> Dict[str, Any]:
        if (generator, package) not in documents:
            documents[(generator, package)] = _price(
                [f"{job(generator, 'baseline')}:0:baseline", f"{job(generator, package)}:0:package"],
                parameters_file,
                directory / f"{generator}-{package}-{StagedDocument.FILE_NAME}",
            )
        return documents[(generator, package)]

    return priced


def _grant_rows(document: Dict[str, Any], scheme: str) -> List[Tuple[str, Any, Any]]:
    """``(status, measure_id, stage)`` of every plan row of one scheme."""
    return [
        (row["status"], row["measure_id"], row["stage"])
        for row in document["plan"]["subsidies"]
        if row["scheme"] == scheme
    ]


class TestTheHeatPumpGrantsFollowWhatThePackageInstalls:
    """Owner decisions 2026-09-26 on the Irish heat-pump grants, on real runs of three houses.

    The central-heating grant is for emitters installed *beside a heat pump* (its legal basis), which
    the catalogue now states with ``package.installed_asset_classes contains HeatPump``; the unit
    grant, like the central-heating one, is not for a house that already heats with a heat pump.
    """

    @pytest.mark.parametrize("generator", ["conventional_oil_heating", "conventional_gas_heating"])
    def test_new_radiators_on_a_boiler_get_no_central_heating_grant(self, heating_plans, generator) -> None:
        """heating_installation alone: the emitters are replaced, and nothing heats them with a heat pump."""
        rows = _grant_rows(heating_plans(generator, "radiators"), "IE_SEAI_HEAT_PUMP_CENTRAL_HEATING_HOUSE")
        assert rows == [("ineligible", "heating_installation", 1)]

    @pytest.mark.parametrize("generator", ["conventional_oil_heating", "conventional_gas_heating"])
    def test_new_radiators_beside_a_new_heat_pump_get_it(self, heating_plans, generator) -> None:
        """heating_system and heating_installation in one stage: the grant is awarded, once."""
        document = heating_plans(generator, "heat_pump_and_radiators")
        assert _grant_rows(document, "IE_SEAI_HEAT_PUMP_CENTRAL_HEATING_HOUSE") == [
            ("awarded", "heating_installation", 1)
        ]
        assert _grant_rows(document, "IE_SEAI_HEAT_PUMP_UNIT_HOUSE") == [("awarded", "heating_system", 1)]

    def test_a_heat_pump_replacing_a_heat_pump_gets_neither_grant(self, heating_plans) -> None:
        """A house that already heats with a heat pump: no unit grant and no central-heating grant."""
        document = heating_plans("air_source_heat_pump", "heat_pump_and_radiators")
        assert _grant_rows(document, "IE_SEAI_HEAT_PUMP_UNIT_HOUSE") == [("ineligible", "heating_system", 1)]
        assert _grant_rows(document, "IE_SEAI_HEAT_PUMP_CENTRAL_HEATING_HOUSE") == [
            ("ineligible", "heating_installation", 1)
        ]


class TestTheBackendsStageLayout:
    """The same plan out of the two files a backend's worker ships per stage (§3).

    A stage directory with no ``lifecycle_costs.json`` states what it was priced for only in its
    ``economic_inputs.json``, which is why that file carries the country. Without it every real
    economics job would have to be told its country in the ``economics`` block, and a job that was
    not told would be priced as German (shared todo H19).
    """

    def test_it_is_priced_as_irish_with_no_country_in_the_block(self, backend_document) -> None:
        """The country comes out of the extract, which is the only file that still states it."""
        assert backend_document["parameters"]["country"] == "IE"

    def test_it_prices_irelands_grants_without_being_told_where_they_are(
        self, backend_document, document
    ) -> None:
        """No ``--subsidy-catalog`` flag, no catalogue path in the extract, and the grants apply.

        The shipped directory is the default when it has the country's file, so the one input a
        stage extract does not carry does not have to be supplied per run either.
        """
        assert backend_document["parameters"]["subsidy_catalog"] is not None
        assert backend_document["parameters"]["subsidy_catalog"] == document["parameters"]["subsidy_catalog"]
        assert {row["status"] for row in backend_document["plan"]["subsidies"]} - {"undetermined"}

    def test_it_prices_at_the_basis_year_the_runs_used(self, backend_document, document) -> None:
        """The extract carries the resolved basis year, so no layout re-derives a different one.

        Before the key existed, a stage directory without ``lifecycle_costs.json`` lost the
        resolved year and the basis year was re-derived from ``simulation_year`` — the same class
        of silent difference as a defaulted country, and it showed up as two different plan NPVs
        over one pair of runs.
        """
        assert backend_document["parameters"]["price_basis_year"] is not None
        assert backend_document["parameters"]["price_basis_year"] == document["parameters"]["price_basis_year"]

    def test_it_is_the_same_plan_and_the_same_money_as_the_job_directories(
        self, backend_document, document
    ) -> None:
        """Two layouts of the same two runs are one plan, down to the euro.

        This is what the country and the basis year travelling in the extract buy: a backend that
        ships only ``economic_inputs.json`` and ``mapping_report.json`` per stage gets exactly the
        document it would get from the full job directories.
        """
        StagedDocument.validate(backend_document)
        assert [stage["label"] for stage in backend_document["stages"]] == [
            stage["label"] for stage in document["stages"]
        ]
        assert {row["subject"] for row in backend_document["plan"]["by_subject"]} == {
            row["subject"] for row in document["plan"]["by_subject"]
        }
        assert backend_document["plan"]["totals"]["npv_in_euro"] == document["plan"]["totals"]["npv_in_euro"]
        assert backend_document["reference"]["totals"]["npv_in_euro"] == document["reference"]["totals"]["npv_in_euro"]
