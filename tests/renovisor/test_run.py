"""T-RUN and T-RESULT: the whole chain, on real weather, from the vendored mockup.

Marked ``system_setups`` because these are the only RenoVisor tests that run a simulation. Three
requests go through: the mockup's baseline, the bare baseline with every optional block absent,
and a package. Each has to exit 0 and leave every file of §2.2 behind, the package has to lower
the building's heating demand, and the payload has to carry its values with their provenance and
name the fields it could not fill.

The package is the vendored mockup **verbatim**, low-temperature radiator and all. The
lifecycle cost engine still has no cost database row for that emitter
(``hisim/economics/adapter.py`` ``_hds_facts`` returns ``None`` for it on purpose, with its own
test) and decision D7 of the cost specification aborts a whole evaluation on an unresolvable
subject -- so the translator writes surface heating for it and reports the substitution, per
the step 8 addendum. :class:`TestTheLowTemperatureRadiatorBlocker` points at the cost adapter
rather than at a run, so the day a row is added it fails and the substitution can go.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from hisim.renovisor.costs import CostField
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.run import Calculation, ExitCode, Outputs
from hisim.renovisor.simulation import Period
from hisim.renovisor.translate import EmitterSubstitution
from hisim.renovisor.vocabulary import HeatDistributionType
from hisim.renovisor.whitelist import Whitelist

BASE_FILES = Path(__file__).resolve().parents[2] / "energy_systems"


def request_file(path: Path, document: Dict[str, Any]) -> Path:
    """Write one request and return its path."""
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def baseline_document() -> Dict[str, Any]:
    """Return the mockup with an empty package."""
    document = copy.deepcopy(ContractFiles.request_mockup())
    document["measures"] = []
    return document


def bare_document() -> Dict[str, Any]:
    """Return the smallest request the schema accepts: every optional block absent.

    This is the shape the defect of ``c02bc801`` was never exercised by -- no photovoltaics and
    no battery -- which is why decision D-D made it a probe and why it is run here too.
    """
    document = baseline_document()
    house = document["house"]
    for block in ("hot_water", "ventilation", "temperature_control", "air_conditioning",
                  "appliances", "pv_system", "battery", "solar_thermal_system", "electric_vehicles"):
        house.pop(block, None)
    house["building"]["roof"].pop("shape", None)
    return document


def package_document() -> Dict[str, Any]:
    """Return the vendored mockup verbatim: its five-measure package, nothing edited.

    Nothing is edited any more. The mockup asks for ``heating_installation:
    low_temperature_radiator``, which the translator writes as surface heating and reports as
    ``not_implemented_yet`` with its sentence, so the one example both sides of the contract
    point at is the one that runs here.
    """
    return copy.deepcopy(ContractFiles.request_mockup())


def run(document: Dict[str, Any], directory: Path, name: str) -> ExitCode:
    """Run one request over a single January day and return the exit code."""
    return Calculation(
        request_path=request_file(directory / f"{name}.json", document),
        output_directory=directory / name,
        period=Period.ONE_DAY_15MIN,
        base_files_directory=BASE_FILES,
    ).run()


def heating_demand(directory: Path) -> float:
    """Return the building's theoretical heating demand out of one run's ``all_kpis.json``."""
    document = json.loads((directory / "results" / "all_kpis.json").read_text(encoding="utf-8"))
    for block in document.values():
        for group in block.values():
            for entry in group.values():
                if isinstance(entry, dict) and entry.get("name") == "Theoretical heating demand":
                    return float(entry["value"])
    raise AssertionError("all_kpis.json carries no theoretical heating demand")


@pytest.mark.system_setups
class TestTheWholeChain:
    """Three requests, three runs, and everything §2.2 says each of them leaves behind."""

    def test_the_baseline_and_the_package_both_run_and_the_package_heats_less(
        self, tmp_path: Path
    ) -> None:
        """The whole point of a renovation calculation, asserted on the simulation's own KPI."""
        assert run(baseline_document(), tmp_path, "baseline") == ExitCode.FINISHED
        assert run(package_document(), tmp_path, "package") == ExitCode.FINISHED

        assert heating_demand(tmp_path / "package") < heating_demand(tmp_path / "baseline")

    def test_a_finished_run_leaves_every_file_the_contract_names(self, tmp_path: Path) -> None:
        """A caller collects files, so a missing one is a broken contract rather than a warning."""
        assert run(baseline_document(), tmp_path, "baseline") == ExitCode.FINISHED

        out = tmp_path / "baseline"
        written = {path.name for path in out.iterdir()}
        assert set(Outputs.RECORDS) <= written
        assert {Outputs.MAPPING_REPORT, Outputs.RESULT, Outputs.CALCULATION} <= written
        assert any(name.startswith("renovisor_") for name in written)
        assert (out / "results" / "all_kpis.json").is_file()

    def test_the_bare_baseline_runs_with_no_optional_block_at_all(self, tmp_path: Path) -> None:
        """No array, no battery: the request the earlier defect had never been run with."""
        assert run(bare_document(), tmp_path, "bare") == ExitCode.FINISHED

        realized = (tmp_path / "bare" / "realized.energy_system.yaml").read_text(encoding="utf-8")
        assert "metered_directly" in realized or "ElectricityMeter" in realized

    def test_nothing_is_written_outside_the_output_directory(self, tmp_path: Path) -> None:
        """The one documented exception is the cache directory, which this run does not use."""
        import subprocess

        repository = Path(__file__).resolve().parents[2]
        before = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout

        run(baseline_document(), tmp_path, "clean")

        after = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert after == before


@pytest.mark.system_setups
class TestTheResultPayload:
    """T-RESULT: the step-6 payload assertions, adjusted for what rule 5 took away."""

    def test_every_published_value_carries_its_provenance_and_its_source(self, tmp_path: Path) -> None:
        """A reader has to be able to tell a simulated figure from a constant without asking."""
        assert run(package_document(), tmp_path, "package") == ExitCode.FINISHED

        payload = json.loads((tmp_path / "package" / Outputs.RESULT).read_text(encoding="utf-8"))
        # Only the KPI half is published: step 10 moved the money to economics_result.json, and
        # the payload carries no `costs` block at all any more.
        assert "costs" not in payload
        for name, value in payload["kpis"].items():
            if isinstance(value, dict) and "provenance" in value:
                assert value["source"], f"kpis.{name} has no source"

    def test_the_operational_carbon_comes_from_the_cost_engine(self, tmp_path: Path) -> None:
        """Never from the legacy KPI: Ireland's rows in that table are sentinel placeholders."""
        assert run(package_document(), tmp_path, "package") == ExitCode.FINISHED

        payload = json.loads((tmp_path / "package" / Outputs.RESULT).read_text(encoding="utf-8"))
        emissions = payload["kpis"]["emissions_in_kg_co2_per_year"]
        assert "lifecycle_costs.json" in emissions["source"]
        assert "IE" in emissions["source"]
        assert emissions["value"] > 0

    def test_the_mapping_report_warns_that_the_legacy_tables_are_placeholders(
        self, tmp_path: Path
    ) -> None:
        """So that a reader of the raw KPI document knows why its cost entries are absurd."""
        assert run(baseline_document(), tmp_path, "baseline") == ExitCode.FINISHED

        report = json.loads((tmp_path / "baseline" / Outputs.MAPPING_REPORT).read_text(encoding="utf-8"))
        assert report["translator"]["legacy_factors"] == "placeholder"

    def test_every_cost_field_is_missing_and_says_where_the_money_is(self, tmp_path: Path) -> None:
        """Step 10 moved the money; decision R8 says the payload has to state that, field by field."""
        assert run(package_document(), tmp_path, "package") == ExitCode.FINISHED

        payload = json.loads((tmp_path / "package" / Outputs.RESULT).read_text(encoding="utf-8"))
        reasons = {entry["field"]: entry["reason"] for entry in payload["missing"]}
        for field in CostField:
            assert f"costs.{field.value}" in reasons
        assert "economics_result.json" in reasons[f"costs.{CostField.NET_PRESENT_VALUE.value}"]
        assert "A13" in reasons[f"costs.{CostField.PROPERTY_VALUE.value}"]

    def test_the_mapping_report_names_the_cost_subjects_and_the_unpriced_ones(
        self, tmp_path: Path
    ) -> None:
        """The staged evaluator stamps `measure_id` from this map, and flags what has no price."""
        assert run(package_document(), tmp_path, "package") == ExitCode.FINISHED

        report = json.loads((tmp_path / "package" / Outputs.MAPPING_REPORT).read_text(encoding="utf-8"))
        assert report["subjects"], "a package with measures names at least one cost subject"
        # The vendored mockup carries no `cost` block on its envelope measures (findings F7/F10),
        # so every envelope subject is in the economics unpriced rather than left out.
        assert set(report["unpriced_subjects"]) <= set(report["subjects"])

    def test_the_payload_names_the_weather_year_it_was_computed_against(self, tmp_path: Path) -> None:
        """Two results computed against different years are not comparable (requirement A4)."""
        assert run(baseline_document(), tmp_path, "baseline") == ExitCode.FINISHED

        payload = json.loads((tmp_path / "baseline" / Outputs.RESULT).read_text(encoding="utf-8"))
        assert payload["weather_basis"]["location"] == "IE"
        assert payload["period"]["start"].startswith("2019-01-01")


@pytest.mark.base
class TestTheLowTemperatureRadiatorBlocker:
    """A pinned gap: the cost engine has no price for a low-temperature radiator.

    ``hisim/economics/adapter.py`` declines to price the emitter and decision D7 of the cost
    specification aborts the whole evaluation on an unresolvable subject, so a package installing
    exactly that emitter -- as a heat-pump retrofit naturally would -- could not complete at all.
    The translator therefore writes surface heating for it and says so, which is a stated
    approximation rather than a hidden one.

    This test points at the reason rather than at the workaround: the day the cost database gains
    a row for ``HEAT_DISTRIBUTION_SYSTEM_LOW_TEMPERATURE_RADIATOR`` the adapter stops returning
    ``None``, this fails, and the substitution together with its two whitelist entries can go --
    T-NIY will demand it, because nothing will reach them any more.
    """

    def test_the_cost_adapter_still_has_no_row_for_the_emitter(self) -> None:
        """The whole reason the substitution exists, asserted where the reason lives."""
        from hisim.components.heat_distribution_system import HeatDistributionSystemType
        from hisim.economics.adapter import FactsExtractors

        extractor = FactsExtractors.BY_CLASS_NAME["HeatDistribution"]

        assert extractor(_emitter_config(HeatDistributionSystemType.LOW_TEMPERATURE_RADIATOR)) is None
        assert extractor(_emitter_config(HeatDistributionSystemType.FLOORHEATING)) is not None

    def test_the_translator_writes_the_priced_emitter_and_reports_the_substitution(self) -> None:
        """Written into the file as floor heating, into the report as the value that was asked."""
        assert (
            EmitterSubstitution.written_as(HeatDistributionType.LOW_TEMPERATURE_RADIATOR)
            is HeatDistributionType.SURFACE_HEATING
        )
        assert EmitterSubstitution.written_as(
            HeatDistributionType.LOW_TEMPERATURE_RADIATOR
        ).hisim_member.name == "FLOORHEATING"
        assert EmitterSubstitution.is_substituted(HeatDistributionType.CONVENTIONAL_RADIATOR) is False

    def test_the_two_whitelist_entries_carry_the_reason(self) -> None:
        """A user reads the sentence, so the sentence is what the list is checked on."""
        notes = {
            entry.item: entry.note
            for entry in Whitelist.load().entries()
            if entry.item
            in ("house.heat_distribution.type_of_system", "heating_installation.type_of_system")
            or entry.item.startswith("heating_installation.type_of_system=")
        }
        sentence = notes["heating_installation.type_of_system=low_temperature_radiator"]

        assert "no cost row" in sentence.lower()
        assert "modelled as surface heating" in sentence.lower()


def _emitter_config(emitter: Any) -> Any:
    """Return the smallest object the cost adapter's heat-distribution extractor reads.

    The extractor reads two attributes off a component config and nothing else, so a stand-in
    carrying those two is enough and keeps the test free of a component import chain.

    Args:
        emitter: The ``HeatDistributionSystemType`` member the config declares.

    Returns:
        An object with ``heating_system`` and ``absolute_conditioned_floor_area_in_m2``.
    """

    class _Config:  # pylint: disable=too-few-public-methods
        """A config stand-in with the two fields the extractor reads."""

        heating_system = emitter
        absolute_conditioned_floor_area_in_m2 = 140.0

    return _Config()
