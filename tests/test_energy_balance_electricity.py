"""The electricity devices state their energy flows and close, and the electricity meter publishes what it reads.

Beads hisim-energy-sankeys-9uoo.5 and .8.

Complete Sankey spec, §5.3 and §4.4 (``roadmap/complete_energy_sankeys_spec.md``, owner decision Q-J). The PV system
takes the sun on its modules and gives its AC power to the electricity meter, losing the rest outdoors (Q-D); the
battery charges from the meter and discharges to it, loses in its inverter and in its cells, and stores the change;
the occupancy takes the appliances' electricity from the meter and the residents' body heat from the residents,
releases the device heat gains and the body heat into the building, and loses the electricity the device heat gains
do not account for. In both households the energy manager reads the devices and gives the meter their signed sum, so
each device links to the meter through the exempt energy manager. Per step, the import minus the export the meter
publishes must equal what the devices linked to it take minus what they give.

The second January week at 900 s of the heat-pump and the gas household (each run once per module): the run
finishing proves every declared balance closes; the tests read its report. The building declares no ports until
PR 4 of the spec, so the room heat ends at the grey "undeclared: Building" and the meter check is reported, not
enforced (owner decision Q-H: it fails the run only once no component is undeclared).
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Set, Tuple

import pytest

from tests.test_energy_balance_generators import balance_entry, port_roles, run_household


class Expected:
    """What the two households must show: their names, the devices' ports and the links of the connected diagram."""

    #: The two households of the spec's §9, row PR 2.
    TWINS: ClassVar[Tuple[str, ...]] = ("household_gas_building_sizer", "household_heatpump_building_sizer")

    #: The ports of the three electricity devices as ``component -> output -> (role, carrier)``.
    PORTS: ClassVar[Dict[str, Dict[str, Tuple[str, str]]]] = {
        "PVSystem": {
            "ElectricityOutput": ("out", "electricity"),
            "SolarPowerOnModulesInWatt": ("in", "solar"),
            "ModuleAndInverterLossInWatt": ("loss", "solar"),
        },
        "Battery": {
            "ChargingPower": ("in", "electricity"),
            "DischargingPower": ("out", "electricity"),
            "StoredEnergyIncreaseInWattHour": ("stored_change", "electricity"),
            "InverterLossInWatt": ("loss", "electricity"),
            "CellLossInWatt": ("loss", "electricity"),
        },
        "UTSPConnector": {
            "ElectricalPowerConsumption": ("in", "electricity"),
            "HeatingByResidents": ("in", "room_heat"),
            "HeatingByDevices": ("out", "room_heat"),
            "BodyHeatToBuildingInWatt": ("out", "room_heat"),
            "ApplianceElectricityNotReleasedIndoorsInWatt": ("loss", "electricity"),
        },
    }

    #: The heat pump's electricity ports nothing reads, so no meter counts them (hisim-component-physics-4g9.39): its
    #: cooling and its brine pump, both zero in the air-source heat pump of the twin.
    UNMETERED: ClassVar[Dict[str, Set[Tuple[str, str]]]] = {
        "household_gas_building_sizer": set(),
        "household_heatpump_building_sizer": {
            ("MoreAdvancedHeatPumpHPLib", "ElectricalInputPowerForCooling"),
            ("MoreAdvancedHeatPumpHPLib", "ElectricalInputPowerBrinePump"),
        },
    }

    #: Links of the connected diagram as (carrier, source, target), in both households.
    LINKS: ClassVar[Set[Tuple[str, str, str]]] = {
        ("solar", "sun", "PVSystem"),
        ("electricity", "PVSystem", "ElectricityMeter"),
        ("solar", "PVSystem", "PVSystem · ModuleAndInverterLossInWatt"),
        ("solar", "PVSystem · ModuleAndInverterLossInWatt", "outdoors"),
        ("electricity", "ElectricityMeter", "Battery"),
        ("electricity", "Battery", "ElectricityMeter"),
        ("electricity", "Battery", "Battery · InverterLossInWatt"),
        ("electricity", "Battery", "Battery · CellLossInWatt"),
        ("electricity", "ElectricityMeter", "UTSPConnector"),
        ("room_heat", "residents' body heat", "UTSPConnector"),
        ("room_heat", "UTSPConnector", "undeclared: Building"),
        ("electricity", "UTSPConnector", "UTSPConnector · ApplianceElectricityNotReleasedIndoorsInWatt"),
        ("electricity", "grid import", "ElectricityMeter"),
        ("electricity", "ElectricityMeter", "grid export"),
    }


@pytest.fixture(name="twin", scope="module", params=Expected.TWINS)
def fixture_twin(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Tuple[str, Path]:
    """One household's second January week at 900 s, run once for every test of this module.

    Returns:
        The setup's name and its result directory.
    """
    setup: str = request.param
    return setup, run_household(tmp_path_factory.mktemp(setup), setup)


def overall_links(results: Path) -> Set[Tuple[str, str, str]]:
    """Every link of the connected diagram as (carrier, source, target)."""
    diagrams: Dict[str, Any] = json.loads((results / "energy_sankeys" / "energy_sankey.json").read_text("utf-8"))
    overall = diagrams["overall"]
    names = [node["name"] for node in overall["nodes"]]
    return {(link["carrier"], names[link["source"]], names[link["target"]]) for link in overall["links"]}


@pytest.mark.extendedbase2
@pytest.mark.parametrize("component", sorted(Expected.PORTS))
def test_the_electricity_device_declares_its_ports_and_closes(twin: Tuple[str, Path], component: str) -> None:
    """PV, battery and occupancy declare the ports of §5.3 and close to 1e-9 kWh in every step."""
    _, results = twin
    entry = balance_entry(results, component)
    assert entry["verdict"] == "closes" and entry["worst_step"] is None
    assert abs(entry["residual_kwh"]) <= 1e-9
    assert port_roles(entry) == Expected.PORTS[component]
    assert entry["in_kwh"] > 1.0


@pytest.mark.extendedbase2
def test_the_flows_of_the_electricity_devices_are_physical(twin: Tuple[str, Path]) -> None:
    """The sun brings four to eight times the PV's electricity, the battery loses, the occupancy releases its gains.

    Catches: a module area off by a factor, a battery loss with the wrong sign, and body heat that does not pass
    through the occupancy unchanged.
    """
    _, results = twin
    pv = balance_entry(results, "PVSystem")["carriers"]
    assert 4.0 < pv["solar"]["in_kwh"] / pv["electricity"]["out_kwh"] < 8.0
    battery = {port["output"]: port["total_kwh"] for port in balance_entry(results, "Battery")["ports"]}
    assert battery["InverterLossInWatt"] > 0.0 and battery["CellLossInWatt"] > 0.0
    assert battery["ChargingPower"] > battery["DischargingPower"] > 0.0
    occupancy = {port["output"]: port["total_kwh"] for port in balance_entry(results, "UTSPConnector")["ports"]}
    assert occupancy["BodyHeatToBuildingInWatt"] == pytest.approx(occupancy["HeatingByResidents"], rel=1e-12)
    assert 0.0 < occupancy["HeatingByDevices"] < occupancy["ElectricalPowerConsumption"]


@pytest.mark.extendedbase2
def test_the_electricity_meter_publishes_what_it_reads_in_every_step(twin: Tuple[str, Path]) -> None:
    """Import minus export of the electricity meter equals what the devices it reads take minus what they give.

    The meter reads the energy manager's sum of the devices, so every device links to it through the manager. The
    check is reported and not yet enforced, because the building is still undeclared (Q-H); with PV, battery and
    occupancy declared it already agrees in every step.
    """
    setup, results = twin
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    meters = {meter["meter"]: meter for meter in report["meters"]}
    meter = meters["ElectricityMeter"]
    reads = {(read["component"], read["output"]) for read in meter["reads"]}
    assert {
        ("PVSystem", "ElectricityOutput"),
        ("Battery", "ChargingPower"),
        ("Battery", "DischargingPower"),
        ("UTSPConnector", "ElectricalPowerConsumption"),
    } <= reads
    assert (setup == "household_heatpump_building_sizer") == any(
        component == "MoreAdvancedHeatPumpHPLib" for component, _ in reads
    )
    assert meter["verdict"] == "agrees" and meter["failing_steps"] == 0 and meter["worst_step"] is None
    assert meter["enforced"] is False and "undeclared" in meter["not_enforced_because"]
    assert abs(meter["residual_kwh"]) <= 1e-6
    assert meter["published_import_kwh"] - meter["published_export_kwh"] == pytest.approx(
        meter["consumed_kwh"] - meter["produced_kwh"], rel=1e-6, abs=1e-6
    )
    assert meter["published_export_kwh"] > 1.0
    unmetered = {(port["component"], port["output"]) for port in report["unmetered"]["ports"]}
    assert unmetered == Expected.UNMETERED[setup]


@pytest.mark.extendedbase2
def test_the_connected_diagram_draws_the_electricity_devices(twin: Tuple[str, Path]) -> None:
    """Sun to PV, PV and battery to and from the meter, their losses, and the occupancy's room heat to the building."""
    setup, results = twin
    links = overall_links(results)
    assert Expected.LINKS <= links
    nodes = {node for _, source, target in links for node in (source, target)}
    assert not {"undeclared: PVSystem", "undeclared: Battery", "undeclared: UTSPConnector"} & nodes
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    unlinked = {(port["component"], port["output"]) for port in report["unlinked"]}
    assert unlinked == Expected.UNMETERED[setup], "every port but the ones nothing reads finds its peer"
