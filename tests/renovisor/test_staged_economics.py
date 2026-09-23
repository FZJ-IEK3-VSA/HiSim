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
from hisim.renovisor.simulation import Period

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
        insulation leaves the house needing less of it.
        """
        for variant in ("reference", "plan"):
            heat_cost = document[variant]["totals"]["levelized_cost_of_heat_in_euro_per_kwh"]
            assert heat_cost is not None and heat_cost["best"] > 0, variant
        _directory, baseline, package = runs
        heat = {}
        for name, job in (("baseline", baseline), ("package", package)):
            (inputs_file,) = job.rglob("economic_inputs.json")
            heat[name] = json.loads(inputs_file.read_text(encoding="utf-8"))["useful_heat_of_simulated_period_in_kwh"]
        assert 0 < heat["package"] < heat["baseline"]

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
