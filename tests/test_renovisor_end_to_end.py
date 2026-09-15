"""The one test that runs a whole RenoVisor calculation, simulation included.

Everything else in the translation layer is checked without a simulation, which is what keeps the
suite fast; this is the test that proves the files the layer writes are files HiSim can actually
run. It takes the committed worked example -- an Irish 1988 detached house and a deep retrofit
that ends on a heat pump with photovoltaics and a battery -- through ``calculate`` over one
January day, and asserts what a caller of the container would see: the exit code, the files, and
that the realized record loads back.

It carries ``@pytest.mark.system_setups`` because it loads a weather year, runs the
LoadProfileGenerator for the occupancy profile and simulates a day, none of which belongs in a
base-marked suite.
"""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from hisim import utils
from hisim.components.weather.config import LocationEnum
from hisim.energy_system.loader import load_energy_system
from hisim.renovisor.calculate import CalculationRunner, CalculationStatus, ExitCode, InputFiles, OutputFiles
from hisim.renovisor.costs import CostBuilder, CostField
from hisim.renovisor.kpis import KpiField
from hisim.renovisor.map import TraceExample
from hisim.renovisor.vocabulary import Provenance

pytestmark = pytest.mark.system_setups

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
BASE_FILES = REPOSITORY_ROOT / "energy_systems"
PARAMETERS_PATH = BASE_FILES / "one_day_15min.simulation.yaml"


def irish_weather_file() -> Path:
    """Return the weather file ``LocationEnum.IE`` points at.

    The location catalogue names a station, a data set family, a subdirectory and a file stem, and
    the constructor joins them onto the inputs directory. Rebuilding the same path here is what
    lets the test skip with a reason instead of failing inside the weather reader.

    Returns:
        The absolute path of the Dublin NSRDB file.
    """
    _, directory, subdirectory, file_stem, _ = LocationEnum.IE.value
    return Path(utils.get_input_directory()) / "weather" / directory / subdirectory / file_stem


def test_the_worked_example_runs_end_to_end(tmp_path: Path) -> None:
    """One request in, one finished calculation out, with every artifact a caller collects.

    Args:
        tmp_path: The output location; everything the calculation writes goes below it, which is
            also what makes the run leave the checkout untouched (requirement R13.4).
    """
    weather = irish_weather_file()
    if not weather.is_file():
        pytest.skip(
            f"the Irish NSRDB weather file LocationEnum.IE names is not in this checkout: {weather}. "
            "The calculation cannot run without it, and switching the location would test a "
            "different dwelling than the one the trace page documents."
        )

    inputs = tmp_path / "in"
    inputs.mkdir()
    (inputs / InputFiles.INVENTORY).write_text(
        TraceExample.INVENTORY_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (inputs / InputFiles.PACKAGE).write_text(
        TraceExample.PACKAGE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (inputs / InputFiles.PARAMETER_NAMES[0]).write_text(
        PARAMETERS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    output = tmp_path / "out"

    code = CalculationRunner(inputs, output, base_files_directory=BASE_FILES).run()

    assert code is ExitCode.FINISHED, (output / OutputFiles.ERRORS).read_text(encoding="utf-8")
    assert not (output / OutputFiles.ERRORS).exists()
    for name in OutputFiles.expected():
        assert (output / name).is_file(), name
    assert (output / OutputFiles.RESULTS_DIRECTORY).is_dir()
    assert list((output / OutputFiles.RESULTS_DIRECTORY).glob("*.csv")), "the run wrote no result table"

    outcome: Dict[str, Any] = json.loads(
        (output / OutputFiles.CALCULATION).read_text(encoding="utf-8")
    )
    assert outcome["status"] == CalculationStatus.FINISHED.value

    realized = load_energy_system(output / OutputFiles.RECORDS[0])
    assert realized.name.startswith("renovisor ")
    assert "Building" in realized.all_components()

    report: Dict[str, Any] = json.loads(
        (output / OutputFiles.TRANSLATION_REPORT).read_text(encoding="utf-8")
    )
    package = json.loads(TraceExample.PACKAGE_PATH.read_text(encoding="utf-8"))["measures"]
    named = [line for line in report["measures"] if "measure_id" in line]
    assert {line["measure_id"] for line in named} == {entry["measure_id"] for entry in package}
    assert len(named) == len(package)


def test_the_file_that_ran_is_the_one_the_parametriser_wrote(tmp_path: Path) -> None:
    """Acceptance criterion AC3 at the outer edge: what ran is a parametrised base file.

    Args:
        tmp_path: The output location.
    """
    weather = irish_weather_file()
    if not weather.is_file():
        pytest.skip(f"the Irish NSRDB weather file is not in this checkout: {weather}")

    inputs = tmp_path / "in"
    inputs.mkdir()
    (inputs / InputFiles.INVENTORY).write_text(
        TraceExample.INVENTORY_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (inputs / InputFiles.PACKAGE).write_text(
        TraceExample.PACKAGE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (inputs / InputFiles.PARAMETER_NAMES[0]).write_text(
        PARAMETERS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    output = tmp_path / "out"

    CalculationRunner(inputs, output, base_files_directory=BASE_FILES).run()

    parametrised = load_energy_system(output / OutputFiles.PARAMETRISED)
    building = parametrised.components["Building"]
    assert building.constructor is not None
    assert building.constructor.arguments["building_code"].startswith("IE.")
    assert "weather_identity" not in building.config
    weather_entry = parametrised.components["Weather"]
    assert weather_entry.constructor is not None
    assert weather_entry.constructor.arguments == {"location": "IE"}


def test_the_result_payload_says_where_every_number_came_from(tmp_path: Path) -> None:
    """The step-6 payload of a one-day run: three real figures, the mocked rest, and the gaps.

    Everything asserted here is a claim the frontend acts on. The two annual figures come from a
    day and are therefore extrapolations, labelled ``PARTIAL`` and carrying the period they were
    extrapolated from; self-sufficiency is a rate and is not extrapolated at all. Embodied carbon
    is there because this package insulates two elements, and the investment carries the split
    between the engine's device estimate and the material-table envelope cost that decision Q23
    asks to stay visible. The three fields nobody can compute yet are in ``missing`` with reasons.

    Args:
        tmp_path: The output location.
    """
    weather = irish_weather_file()
    if not weather.is_file():
        pytest.skip(f"the Irish NSRDB weather file is not in this checkout: {weather}")

    inputs = tmp_path / "in"
    inputs.mkdir()
    (inputs / InputFiles.INVENTORY).write_text(
        TraceExample.INVENTORY_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (inputs / InputFiles.PACKAGE).write_text(
        TraceExample.PACKAGE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (inputs / InputFiles.PARAMETER_NAMES[0]).write_text(
        PARAMETERS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    output = tmp_path / "out"

    code = CalculationRunner(inputs, output, base_files_directory=BASE_FILES).run()
    assert code is ExitCode.FINISHED, (output / OutputFiles.ERRORS).read_text(encoding="utf-8")

    payload: Dict[str, Any] = json.loads((output / OutputFiles.RESULT).read_text(encoding="utf-8"))

    assert payload["weather_basis"] == {
        "location": "IE",
        "station": "Dublin",
        "dataset": "NSRDB_15MIN",
        "year": 2019,
    }
    assert payload["period"]["fraction_of_year"] == pytest.approx(1 / 365, rel=1e-2)

    kpis = payload["kpis"]
    for name in (KpiField.ENERGY_DEMAND.value, KpiField.EMISSIONS.value):
        assert kpis[name]["provenance"] == Provenance.PARTIAL.value
        assert kpis[name]["period"]["fraction_of_year"] == pytest.approx(1 / 365, rel=1e-2)
    assert kpis[KpiField.SELF_SUFFICIENCY.value]["provenance"] == Provenance.SIMULATED.value
    assert kpis[KpiField.EMBODIED_CO2.value]["provenance"] == Provenance.SIMULATED.value
    assert kpis[KpiField.ENERGY_LABEL.value]["value"] is None

    costs = payload["costs"]
    breakdown = costs[CostField.INVESTMENT_BREAKDOWN.value]
    assert set(breakdown) == {CostBuilder.DEVICES_KEY, CostBuilder.ENVELOPE_KEY}
    total = costs[CostField.INVESTMENT.value]["value"]["best_estimate"]
    assert total == pytest.approx(
        breakdown[CostBuilder.DEVICES_KEY]["value"]["best_estimate"]
        + breakdown[CostBuilder.ENVELOPE_KEY]["value"]["best_estimate"]
    )
    for name in (
        CostField.NET_PRESENT_VALUE.value,
        CostField.MONTHLY_TWENTY_YEARS.value,
        CostField.MONTHLY_TEN_YEARS.value,
        CostField.ENERGY.value,
        CostField.MAINTENANCE.value,
    ):
        assert set(costs[name]["value"]) == {"low", "best_estimate", "high"}

    missing = {entry["field"] for entry in payload["missing"]}
    assert missing == {
        f"costs.{CostField.GRANT.value}",
        f"costs.{CostField.PAYBACK.value}",
        f"costs.{CostField.PROPERTY_VALUE.value}",
    }
