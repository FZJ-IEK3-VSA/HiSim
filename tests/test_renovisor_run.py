"""T-RUN and T-RESULT: the whole chain, on real weather, from the vendored mockup.

Marked ``system_setups`` because these are the only RenoVisor tests that run a simulation. Three
requests go through: the mockup's baseline, the bare baseline with every optional block absent,
and a package. Each has to exit 0 and leave every file of §2.2 behind, the package has to lower
the building's heating demand, and the payload has to carry its values with their provenance and
name the fields it could not fill.

One blocker is written down here rather than hidden. The mockup's package installs
``heating_installation: low_temperature_radiator``, and the lifecycle cost engine has no cost
database row for that emitter (``hisim/economics/adapter.py`` ``_hds_facts`` returns ``None``
for it on purpose, with its own test). Decision D7 of the cost specification aborts the whole
evaluation on an unresolvable subject, so such a run ends at exit 5 with nothing written. The
package this test runs therefore asks for ``surface_heating``, which is a heat pump's other
natural emitter and is priced; :class:`TestTheLowTemperatureRadiatorBlocker` pins the gap so
that the day a row is added, this file is what says the workaround can go.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.run import Calculation, ExitCode, Outputs
from hisim.renovisor.simulation import Period

BASE_FILES = Path(__file__).resolve().parents[1] / "energy_systems"


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
    """Return the mockup's package with the emitter the cost engine can price."""
    document = copy.deepcopy(ContractFiles.request_mockup())
    for measure in document["measures"]:
        if measure["id"] == "heating_installation":
            measure["options"]["type_of_system"] = "surface_heating"
    return document


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

        repository = Path(__file__).resolve().parents[1]
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
        for block in ("kpis", "costs"):
            for name, value in payload[block].items():
                if isinstance(value, dict) and "provenance" in value:
                    assert value["source"], f"{block}.{name} has no source"

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

    def test_the_envelope_material_cost_is_absent_with_its_reason(self, tmp_path: Path) -> None:
        """Rule 5 moved the material into the request, and a request carries no price."""
        assert run(package_document(), tmp_path, "package") == ExitCode.FINISHED

        payload = json.loads((tmp_path / "package" / Outputs.RESULT).read_text(encoding="utf-8"))
        reasons = {entry["field"]: entry["reason"] for entry in payload["missing"]}
        assert "costs.investment_breakdown.envelope_material" in reasons
        assert "no material price" in reasons["costs.investment_breakdown.envelope_material"]

    def test_the_payload_names_the_weather_year_it_was_computed_against(self, tmp_path: Path) -> None:
        """Two results computed against different years are not comparable (requirement A4)."""
        assert run(baseline_document(), tmp_path, "baseline") == ExitCode.FINISHED

        payload = json.loads((tmp_path / "baseline" / Outputs.RESULT).read_text(encoding="utf-8"))
        assert payload["weather_basis"]["location"] == "IE"
        assert payload["period"]["start"].startswith("2019-01-01")


@pytest.mark.system_setups
class TestTheLowTemperatureRadiatorBlocker:
    """A pinned gap: the cost engine has no price for a low-temperature radiator.

    ``hisim/economics/adapter.py`` declines to price the emitter and decision D7 of the cost
    specification aborts the whole evaluation on an unresolvable subject, so the mockup's own
    package -- which installs exactly that emitter, as a heat-pump retrofit naturally would --
    cannot complete. It is not a translator fault and the translator must not hide it: the run
    ends at exit 5 with the engine's reason on standard error.

    The day the cost database gains a row for ``HEAT_DISTRIBUTION_SYSTEM_LOW_TEMPERATURE_RADIATOR``,
    this test fails, and that is the signal to run the vendored mockup verbatim again.
    """

    def test_the_vendored_mockup_verbatim_still_ends_at_the_cost_engine(self, tmp_path: Path) -> None:
        """Written down so the workaround above has a stated reason and an expiry."""
        document = copy.deepcopy(ContractFiles.request_mockup())

        assert run(document, tmp_path, "verbatim") == ExitCode.SIMULATION_ERROR
