"""The three setups with a heat pump or an air conditioner that heats the building directly close their balance.

Complete Sankey spec, PR 5a. ``basic_household`` runs the generic heat pump, ``air_conditioned_house`` the air
conditioner, ``simple_air_conditioner_household_building_sizer`` the simple air conditioner, each feeding the building's
signed ``ThermalPowerDelivered`` without a water loop. Each device now declares its ports: the electricity it draws, the
heat it delivers while heating and the heat it draws from the building while cooling (two non-negative outputs,
decision D20 of ``roadmap/fluid_loops_spec.md``), and the outside air as its source while heating and its sink while
cooling, linked to the "ambient heat source" and the "ambient heat sink" boundary nodes (owner decision Q-M of
``roadmap/complete_energy_sankeys_spec.md``).

The building reads both heat outputs through its input ports ``HeatReceivedInWatt`` and
``HeatRemovedByCoolingInWatt`` (decision D9), wired by each setup, so the device's heat links to the building. The
electricity meter of the first two setups reads each device's electricity port; the third setup has no meter (bead
hisim-meter-bought-carriers-lyfl), so its electricity port is unlinked and unmetered, which is not enforced while a
component of the run (the weather) is undeclared (owner decision Q-H).

The second January week of 2021 (heating) and the first full July week (cooling: the simple air conditioner cools in
80 of its steps) at 900 s, each run once per module: the run finishing proves every declared balance closes; the tests
read its report.
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Set, Tuple

import pytest

from tests.test_energy_balance_generators import balance_entry, port_roles, run_household


class Expected:
    """What the three setups must show: the device of each, its ports and the ports the building does not read yet."""

    #: The setups of the spec's §9, row PR 5a, each with its device's component name.
    DEVICES: ClassVar[Dict[str, str]] = {
        "basic_household": "HeatPump",
        "air_conditioned_house": "AirConditioner",
        "simple_air_conditioner_household_building_sizer": "SimpleAirConditioner",
    }

    #: The two weeks as ``name -> (first day, day after the last)``.
    WEEKS: ClassVar[Dict[str, Tuple[str, str]]] = {
        "january": ("2021-01-10", "2021-01-17"),
        "july": ("2021-07-05", "2021-07-12"),
    }

    #: The device ports of each setup as ``output -> (role, carrier)``.
    PORTS: ClassVar[Dict[str, Dict[str, Tuple[str, str]]]] = {
        "basic_household": {
            "ElectricityOutput": ("consumption", "electricity"),
            "Heating": ("production", "space_heating_heat"),
            "HeatDrawnForCoolingInWatt": ("consumption", "cooling"),
            "HeatFromAmbientInWatt": ("consumption", "ambient_heat"),
            "HeatToAmbientInWatt": ("production", "ambient_heat"),
        },
        "air_conditioned_house": {
            "ElectricalPowerConsumption": ("consumption", "electricity"),
            "HeatDeliveredInWatt": ("production", "space_heating_heat"),
            "HeatDrawnForCoolingInWatt": ("consumption", "cooling"),
            "HeatFromAmbientInWatt": ("consumption", "ambient_heat"),
            "HeatToAmbientInWatt": ("production", "ambient_heat"),
        },
        "simple_air_conditioner_household_building_sizer": {
            "ElectricalPowerConsumption": ("consumption", "electricity"),
            "HeatDrawnForCoolingInWatt": ("consumption", "cooling"),
            "HeatToAmbientInWatt": ("production", "ambient_heat"),
        },
    }

    #: The output of each device's electricity and of its heat to the building.
    ELECTRICITY: ClassVar[Dict[str, str]] = {
        "basic_household": "ElectricityOutput",
        "air_conditioned_house": "ElectricalPowerConsumption",
        "simple_air_conditioner_household_building_sizer": "ElectricalPowerConsumption",
    }
    HEATING: ClassVar[Dict[str, str]] = {"basic_household": "Heating", "air_conditioned_house": "HeatDeliveredInWatt"}


@pytest.fixture(
    name="week_results",
    scope="module",
    params=[(setup, week) for setup in Expected.DEVICES for week in Expected.WEEKS],
    ids=lambda param: f"{param[0]}-{param[1]}",
)
def fixture_week_results(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Tuple[str, str, Path]:
    """One setup's week at 900 s, run once for every test of this module.

    Returns:
        The setup's name, the week's name and the result directory.
    """
    setup, week = request.param
    start, end = Expected.WEEKS[week]
    return setup, week, run_household(tmp_path_factory.mktemp(f"{setup}_{week}"), setup, start=start, end=end)


def report_of(results: Path) -> Dict[str, Any]:
    """The run's balance report."""
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    return report


def totals_of(entry: Dict[str, Any]) -> Dict[str, float]:
    """A component's ports as ``output -> total over the run in kWh``."""
    return {port["output"]: port["total_kwh"] for port in entry["ports"]}


@pytest.mark.extendedbase2
def test_the_device_declares_its_ports_and_closes_in_every_step(week_results: Tuple[str, str, Path]) -> None:
    """Each device declares the ports of PR 5a and closes to 1e-9 kWh in every step, and no port's total is negative.

    Catches a port of the wrong role or carrier, an ambient-heat output that misses the electricity (a cooling device
    rejects what it draws plus what drives it), and an output published with the wrong sign.
    """
    setup, _, results = week_results
    device = Expected.DEVICES[setup]
    entry = balance_entry(results, device)
    assert entry["verdict"] == "closes" and entry["worst_step"] is None, device
    assert abs(entry["residual_kwh"]) <= 1e-9, device
    assert port_roles(entry) == Expected.PORTS[setup]
    assert all(total >= 0.0 for total in totals_of(entry).values())


@pytest.mark.extendedbase2
def test_the_device_heats_in_january_and_cools_in_july_at_a_physical_efficiency(week_results: Tuple[str, str, Path]) -> None:
    """In January the heat pump and the air conditioner heat; in July every device cools, at a ratio above 1.

    Catches a week that exercises only one direction (so that the other's ports would pass at zero), heat delivered
    from no electricity, and cooling rejected to the air without the electricity that drives it.
    """
    setup, week, results = week_results
    totals = totals_of(balance_entry(results, Expected.DEVICES[setup]))
    electricity = totals[Expected.ELECTRICITY[setup]]
    if week == "january" and setup in Expected.HEATING:
        heating = totals[Expected.HEATING[setup]]
        assert heating > 100.0 and totals["HeatDrawnForCoolingInWatt"] == 0.0
        assert 1.0 < heating / electricity < 10.0
    if week == "july":
        cooling = totals["HeatDrawnForCoolingInWatt"]
        assert cooling > 10.0
        assert totals["HeatToAmbientInWatt"] > cooling


@pytest.mark.extendedbase2
def test_the_electricity_meter_reads_the_device_and_the_building_reads_its_heat(week_results: Tuple[str, str, Path]) -> None:
    """The meter reads the device's electricity, and the building's input ports read its heat to and from the building.

    In the setup without a meter the electricity port is unlinked and unmetered, reported and not enforced; in a week in
    which the device never runs (the simple air conditioner in January) it is idle and not listed as unlinked. Catches
    an electricity port linked to a controller, a heat port linked to anything but the building, a setup that leaves one
    of the building's two inputs unwired, and a meter that publishes other than it reads once the device's port is
    declared.
    """
    setup, _, results = week_results
    device = Expected.DEVICES[setup]
    report = report_of(results)
    entry = balance_entry(results, device)
    peers = {port["output"]: port["peer"] for port in entry["ports"]}
    moving = {output for output, total in totals_of(entry).items() if total != 0.0}
    assert peers["HeatToAmbientInWatt"] == "ambient heat sink"
    building_heat: Set[str] = {"HeatDrawnForCoolingInWatt", *([Expected.HEATING[setup]] if setup in Expected.HEATING else [])}
    unlinked = {port["output"] for port in report["unlinked"] if port["component"] == device}
    unmetered = report["unmetered"]
    assert unmetered["enforced"] is False and "undeclared" in unmetered["not_enforced_because"]
    assert {output: peers[output] for output in building_heat} == {output: "Building" for output in building_heat}
    assert "Building" not in {item["component"] for item in report["undeclared"]}
    if setup == "simple_air_conditioner_household_building_sizer":
        assert unlinked == {Expected.ELECTRICITY[setup]} & moving
        assert {(port["component"], port["output"]) for port in unmetered["ports"]} == {(device, Expected.ELECTRICITY[setup])}
        assert not report["meters"]
        return
    assert not unlinked
    assert peers[Expected.ELECTRICITY[setup]] == "ElectricityMeter"
    assert peers["HeatFromAmbientInWatt"] == "ambient heat source"
    assert not unmetered["ports"]
    (meter,) = report["meters"]
    assert meter["verdict"] == "agrees" and meter["failing_steps"] == 0
