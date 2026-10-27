"""The electric car and its battery state their energy flows and close, and the meter reads the car's charging.

Bead hisim-energy-sankeys-9uoo.5, the car's part (complete Sankey spec, §5.5, PR 5b). The car battery takes its
charging from the electricity meter, which reads it by its role; it discharges straight into the car, past every
meter, so its discharge is vehicle electricity, a carrier no meter reads (owner decision Q-K), which the car books on
the input it reads the discharge with; it loses in its inverter and in its cells, and it stores the change. The car turns the electricity it receives into
driving energy, which leaves the system at the ``driving`` node. The charge controller is exempt: it decides, it moves
no energy. Per step, the import minus the export the meter publishes must equal what the devices linked to it take
minus what they give; before the car battery declared its ports the meter read the controller's copy of the charging
and published 34 kWh more than any declared port took in this week.

The second January week at 900 s of ``household_heatpump_car_building_sizer``, run once for the module with its local
LoadProfileGenerator, which alone publishes the car's driving profile: the run finishing proves every declared balance
closes; the tests read its report. The building and the storages declare no ports yet, so the meter check is reported,
not enforced (owner decision Q-H).
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Set, Tuple

import pytest

from tests.test_energy_balance_electricity import overall_links
from tests.test_energy_balance_generators import balance_entry, port_roles, run_household


class Expected:
    """What the car household must show: the setup, the two components' ports and the links of the diagram."""

    #: The car household of the spec's §9, row PR 5b.
    SETUP: ClassVar[str] = "household_heatpump_car_building_sizer"
    #: The car battery's name in the twin.
    CAR_BATTERY: ClassVar[str] = "CarBattery_1"
    #: The car's name in the twin: the LoadProfileGenerator's car with its index.
    CAR: ClassVar[str] = "Car_2_22kW_Charging_Power_avg_Speed_30_kmh_0"

    #: The ports of the two components as ``component -> output or input -> (role, carrier)``.
    PORTS: ClassVar[Dict[str, Dict[str, Tuple[str, str]]]] = {
        CAR_BATTERY: {
            "AcBatteryChargingPower": ("consumption", "electricity"),
            "AcBatteryDischargingPower": ("production", "vehicle_electricity"),
            "StoredEnergyIncreaseInWattHour": ("stored_change", "electricity"),
            "InverterLossInWatt": ("loss", "electricity"),
            "CellLossInWatt": ("loss", "electricity"),
        },
        CAR: {
            "ElectricityFromCarBatteryInWatt": ("consumption", "vehicle_electricity"),
            "ElectricityOutput": ("production", "driving"),
        },
    }

    #: Links of the connected diagram as (carrier, source, target).
    LINKS: ClassVar[Set[Tuple[str, str, str]]] = {
        ("electricity", "ElectricityMeter", CAR_BATTERY),
        ("vehicle_electricity", CAR_BATTERY, CAR),
        ("electricity", CAR_BATTERY, f"{CAR_BATTERY} · InverterLossInWatt"),
        ("electricity", CAR_BATTERY, f"{CAR_BATTERY} · CellLossInWatt"),
        ("driving", CAR, "driving"),
    }


@pytest.fixture(name="results", scope="module")
def fixture_results(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The car household's second January week at 900 s, run once for every test of this module.

    Returns:
        The result directory.
    """
    return run_household(tmp_path_factory.mktemp(Expected.SETUP), Expected.SETUP, predefined_profile=False)


@pytest.mark.extendedbase2
@pytest.mark.parametrize("component", sorted(Expected.PORTS))
def test_the_car_and_its_battery_declare_their_ports_and_close(results: Path, component: str) -> None:
    """The car battery and the car declare the ports of §5.5 and close to 1e-9 kWh in every step."""
    entry = balance_entry(results, component)
    assert entry["verdict"] == "closes" and entry["worst_step"] is None
    assert abs(entry["residual_kwh"]) <= 1e-9
    assert port_roles(entry) == Expected.PORTS[component]
    assert entry["consumption_kwh"] > 1.0


@pytest.mark.extendedbase2
def test_the_battery_gives_the_car_what_it_drives_with_and_loses_only_while_charging(results: Path) -> None:
    """The battery's discharge is the car's electricity, the car's driving equals it, and both losses are positive.

    Catches a car that drives on more than its battery gave it, a car input port that does not read the battery's
    discharge, a link only one side books, and a loss with the wrong sign.
    """
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    (link,) = [link for link in report["links"] if link["sender"] == Expected.CAR_BATTERY]
    assert link["receiver"] == Expected.CAR and link["verdict"] == "closes" and link["difference_kwh"] == 0.0
    battery = {port["output"]: port["total_kwh"] for port in balance_entry(results, Expected.CAR_BATTERY)["ports"]}
    car_ports = balance_entry(results, Expected.CAR)["ports"]
    car = {port["output"]: port["total_kwh"] for port in car_ports}
    (received,) = [port for port in car_ports if port["output"] == "ElectricityFromCarBatteryInWatt"]
    assert received["reads"] == f"{Expected.CAR_BATTERY}.AcBatteryDischargingPower"
    assert received["peer"] == Expected.CAR_BATTERY
    assert battery["AcBatteryChargingPower"] > battery["AcBatteryDischargingPower"] > 1.0
    assert battery["InverterLossInWatt"] > 0.0 and battery["CellLossInWatt"] > 0.0
    assert car["ElectricityFromCarBatteryInWatt"] == car["ElectricityOutput"] == battery["AcBatteryDischargingPower"]


@pytest.mark.extendedbase2
def test_the_electricity_meter_reads_the_car_s_charging_and_publishes_what_it_reads_in_every_step(results: Path) -> None:
    """The meter reads the car battery's charging and nothing of the car's driving, and agrees in every step.

    Catches the meter reading the charge controller's copy of the charging, which lags the battery by a pass, and
    the car's driving counted at the grid boundary.
    """
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    (meter,) = report["meters"]
    reads = {(read["component"], read["output"]) for read in meter["reads"]}
    assert (Expected.CAR_BATTERY, "AcBatteryChargingPower") in reads
    assert not {component for component, _ in reads} & {Expected.CAR, "L1EVChargeControl_1"}
    assert (Expected.CAR_BATTERY, "AcBatteryDischargingPower") not in reads
    assert meter["verdict"] == "agrees" and meter["failing_steps"] == 0 and meter["worst_step"] is None
    assert meter["enforced"] is False and "undeclared" in meter["not_enforced_because"]
    assert abs(meter["residual_kwh"]) <= 1e-6 and meter["largest_step_residual_kwh"] <= 1e-9
    assert not report["unmetered"]["ports"] and not report["unlinked"]
    exempt = {entry["component"]: entry["reason"] for entry in report["exempt"]}
    assert exempt["L1EVChargeControl_1"] == "an L1 controller decides for its device, it moves no energy"


@pytest.mark.extendedbase2
def test_the_connected_diagram_draws_the_car_from_the_meter_to_driving(results: Path) -> None:
    """The meter to the car battery, the battery straight to the car, its losses, and the car to driving."""
    links = overall_links(results)
    assert Expected.LINKS <= links
    nodes = {node for _, source, target in links for node in (source, target)}
    assert not {f"undeclared: {Expected.CAR}", f"undeclared: {Expected.CAR_BATTERY}", "L1EVChargeControl_1"} & nodes
