"""The water storages and the heat distribution system declare their circuits and close (hydronic stage E).

Beads hisim-hydronics-fxix.6 and hisim-energy-sankeys-9uoo.3.

Complete Sankey spec, §5.1 (``roadmap/complete_energy_sankeys_spec.md``). Every water circuit is declared at both of
its ends as a ``HydronicPort``, and the balance derives its heat once from the mass flow and the two temperatures both
ends read, ``m c (T_sup - T_ret) dt``: the generator's circuit into the buffer and into the hot-water tank, the
buffer's circuit to the heat distribution system. The buffer (``SimpleHotWaterStorage``) takes its generators'
circuits in, gives the distribution circuit out, loses its standby heat outdoors (owner decision Q-F) and stores the
change of its heat; the tank (``SimpleDHWStorage``) takes its charging circuits in, gives the hot water drawn at the
taps to that boundary node, loses its standby heat outdoors and stores the change; the distribution system
(``HeatDistribution``) takes the buffer's circuit in and gives its heat to the building, space-heating heat up to the
building's input, while the building itself declares nothing until PR 4 of the spec.

The second January week at 900 s of the gas, heat-pump, gas-and-solar and heat-pump-and-solar households, each run
once per module: the run finishing proves every declared balance closes in every step (the check fails the run
otherwise); the tests read its report and its Sankey graph. The chains without a buffer are run too: district
heating and ``gas_boiler_household``, whose distribution system publishes its heat one step late and, in the boiler
house, runs a flow of its own beside the boiler's on the same circuit. There it declares no ports yet (beads
hisim-pnw7 and hisim-boiler-circuit-flow-gap-g2mx) and is drawn as undeclared; the tank of the district-heating
house closes all the same.
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Set, Tuple

import pytest

from tests.test_energy_balance_electricity import overall_links
from tests.test_energy_balance_generators import balance_entry, port_roles, run_household


class Expected:
    """What the households must show: their generators and the ports and links of the storages and the distribution."""

    #: The households with a buffer, and the generator that charges their buffer and, with the collector, their tank.
    TWINS: ClassVar[Dict[str, Tuple[str, Tuple[str, ...]]]] = {
        "household_gas_building_sizer": ("CondensingGasBoiler", ("CondensingGasBoiler",)),
        "household_heatpump_building_sizer": ("MoreAdvancedHeatPumpHPLib", ("MoreAdvancedHeatPumpHPLib",)),
        "household_gas_solar_thermal_building_sizer": (
            "CondensingGasBoiler",
            ("SolarThermalSystem", "CondensingGasBoiler"),
        ),
        "household_heatpump_solar_thermal_building_sizer": (
            "MoreAdvancedHeatPumpHPLib",
            ("SolarThermalSystem", "MoreAdvancedHeatPumpHPLib"),
        ),
    }

    #: The space-heating circuit of each generator, named by its mass flow.
    SPACE_HEATING_CIRCUIT: ClassVar[Dict[str, str]] = {
        "CondensingGasBoiler": "circuit CondensingGasBoiler.WaterOutputMassFlowSh",
        "MoreAdvancedHeatPumpHPLib": "circuit MoreAdvancedHeatPumpHPLib.MassFlowOutputSH",
    }

    #: The hot-water circuit of each generator, named by its mass flow.
    HOT_WATER_CIRCUIT: ClassVar[Dict[str, str]] = {
        "CondensingGasBoiler": "circuit CondensingGasBoiler.WaterOutputMassFlowDhw",
        "MoreAdvancedHeatPumpHPLib": "circuit MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW",
        "SolarThermalSystem": "circuit SolarThermalSystem.WaterMassFlowOutput",
        "DistrictHeating": "circuit DistrictHeating.WaterOutputDhwMassFlowRate",
    }

    #: The distribution circuit, named by its mass flow, which the distribution system pumps.
    DISTRIBUTION_CIRCUIT: ClassVar[str] = "circuit HeatDistributionSystem.WaterMassFlowHDS"

    #: The households without a buffer, whose distribution system declares no ports yet.
    WITHOUT_BUFFER: ClassVar[Tuple[str, ...]] = ("household_district_heating_building_sizer", "gas_boiler_household")


@pytest.fixture(name="twin", scope="module", params=sorted(Expected.TWINS))
def fixture_twin(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Tuple[str, Path]:
    """One household's second January week at 900 s, run once for every test of this module.

    Returns:
        The setup's name and its result directory.
    """
    setup: str = request.param
    return setup, run_household(tmp_path_factory.mktemp(setup), setup)


def report_of(results: Path) -> Dict[str, Any]:
    """The run's balance report."""
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    return report


def closes_in_every_step(entry: Dict[str, Any]) -> None:
    """Assert that a component's balance closes over the run and in every step."""
    assert entry["verdict"] == "closes" and entry["failing_steps"] == 0 and entry["worst_step"] is None, entry
    assert abs(entry["residual_kwh"]) <= 1e-6 * max(1.0, entry["throughput_kwh"])


@pytest.mark.extendedbase2
def test_the_buffer_declares_its_circuits_loss_and_stored_heat_and_closes(twin: Tuple[str, Path]) -> None:
    """The generator's circuit in, the distribution circuit out, the standby loss and the stored change (§5.1)."""
    setup, results = twin
    generator, _ = Expected.TWINS[setup]
    entry = balance_entry(results, "SimpleHotWaterStorage")
    closes_in_every_step(entry)
    assert port_roles(entry) == {
        Expected.SPACE_HEATING_CIRCUIT[generator]: ("in", "space_heating_heat"),
        Expected.DISTRIBUTION_CIRCUIT: ("out", "space_heating_heat"),
        "StandbyHeatLoss": ("loss", "space_heating_heat"),
        "ThermalEnergyIncreaseInStorage": ("stored_change", "space_heating_heat"),
    }
    peers = {port["output"]: (port["peer_kind"], port["peer"]) for port in entry["ports"]}
    assert peers[Expected.SPACE_HEATING_CIRCUIT[generator]] == ("component", generator)
    assert peers[Expected.DISTRIBUTION_CIRCUIT] == ("component", "HeatDistributionSystem")
    assert peers["StandbyHeatLoss"] == ("boundary", "outdoors")
    # a week in January: the generator charges, the distribution draws, the buffer loses
    assert entry["in_kwh"] > 10.0 and entry["out_kwh"] > 10.0 and entry["loss_kwh"] > 0.0


@pytest.mark.extendedbase2
def test_the_hot_water_tank_declares_its_circuits_taps_loss_and_stored_heat_and_closes(twin: Tuple[str, Path]) -> None:
    """Every charging circuit in, the hot water drawn at the taps out to its boundary node, loss and stored change."""
    setup, results = twin
    _, charging = Expected.TWINS[setup]
    entry = balance_entry(results, "DHWStorage")
    closes_in_every_step(entry)
    expected = {Expected.HOT_WATER_CIRCUIT[generator]: ("in", "domestic_hot_water_heat") for generator in charging}
    expected.update(
        {
            "ThermalPowerConsumptionDHW": ("out", "domestic_hot_water_heat"),
            "StandbyHeatLoss": ("loss", "domestic_hot_water_heat"),
            "ThermalEnergyIncreaseInStorage": ("stored_change", "domestic_hot_water_heat"),
        }
    )
    assert port_roles(entry) == expected
    peers = {port["output"]: (port["peer_kind"], port["peer"]) for port in entry["ports"]}
    assert peers["ThermalPowerConsumptionDHW"] == ("boundary", "hot water drawn at the taps")
    for generator in charging:
        assert peers[Expected.HOT_WATER_CIRCUIT[generator]] == ("component", generator)
    assert entry["out_kwh"] > 1.0


@pytest.mark.extendedbase2
def test_the_distribution_system_takes_the_buffer_s_circuit_and_heats_the_building(twin: Tuple[str, Path]) -> None:
    """Behind a buffer the distribution system books this step's circuit and gives all of it to the building."""
    _, results = twin
    entry = balance_entry(results, "HeatDistributionSystem")
    closes_in_every_step(entry)
    assert port_roles(entry) == {
        Expected.DISTRIBUTION_CIRCUIT: ("in", "space_heating_heat"),
        "ThermalPowerDelivered": ("out", "space_heating_heat"),
    }
    peers = {port["output"]: (port["peer_kind"], port["peer"]) for port in entry["ports"]}
    assert peers == {
        Expected.DISTRIBUTION_CIRCUIT: ("component", "SimpleHotWaterStorage"),
        "ThermalPowerDelivered": ("component", "Building"),
    }
    assert entry["out_kwh"] > 10.0


@pytest.mark.extendedbase2
def test_every_circuit_is_one_link_whose_ends_book_the_same_heat(twin: Tuple[str, Path]) -> None:
    """Both ends of a circuit derive its heat from the same three outputs: sent equals received to the last bit."""
    setup, results = twin
    generator, charging = Expected.TWINS[setup]
    links = {(link["sender"], link["receiver"], link["carrier"]): link for link in report_of(results)["links"]}
    circuits: List[Tuple[str, str, str]] = [
        (generator, "SimpleHotWaterStorage", "space_heating_heat"),
        ("SimpleHotWaterStorage", "HeatDistributionSystem", "space_heating_heat"),
    ] + [(name, "DHWStorage", "domestic_hot_water_heat") for name in charging]
    for key in circuits:
        link = links[key]
        assert link["verdict"] == "closes" and link["sent_kwh"] == link["received_kwh"], link
    assert not report_of(results)["circuit_faults"]


@pytest.mark.extendedbase2
def test_the_connected_diagram_runs_from_the_generators_through_the_storages(twin: Tuple[str, Path]) -> None:
    """Generator to buffer to distribution to the building, generator to tank to the taps, losses outdoors."""
    setup, results = twin
    generator, charging = Expected.TWINS[setup]
    links = overall_links(results)
    expected: Set[Tuple[str, str, str]] = {
        ("space_heating_heat", generator, "SimpleHotWaterStorage"),
        ("space_heating_heat", "SimpleHotWaterStorage", "HeatDistributionSystem"),
        ("space_heating_heat", "HeatDistributionSystem", "undeclared: Building"),
        ("space_heating_heat", "SimpleHotWaterStorage", "SimpleHotWaterStorage · StandbyHeatLoss"),
        ("space_heating_heat", "SimpleHotWaterStorage · StandbyHeatLoss", "outdoors"),
        ("domestic_hot_water_heat", "DHWStorage", "hot water drawn at the taps"),
        ("domestic_hot_water_heat", "DHWStorage", "DHWStorage · StandbyHeatLoss"),
        ("domestic_hot_water_heat", "DHWStorage · StandbyHeatLoss", "outdoors"),
    }
    expected |= {("domestic_hot_water_heat", name, "DHWStorage") for name in charging if name != "SolarThermalSystem"}
    assert expected <= links, sorted(expected - links)
    nodes = {node for _, source, target in links for node in (source, target)}
    assert not {
        "undeclared: SimpleHotWaterStorage",
        "undeclared: DHWStorage",
        "undeclared: HeatDistributionSystem",
    } & nodes
    assert "unaccounted" not in nodes


@pytest.mark.extendedbase2
@pytest.mark.parametrize("setup", Expected.WITHOUT_BUFFER)
def test_without_a_buffer_the_distribution_system_is_undeclared_and_the_tank_closes(
    setup: str, tmp_path: Path
) -> None:
    """The district-heating and the boiler house run; their distribution system is drawn as undeclared.

    Without a buffer the distribution system publishes the previous step's heat (hisim-pnw7), and in
    ``gas_boiler_household`` the boiler books a flow of its own on the circuit the distribution system pumps
    (hisim-boiler-circuit-flow-gap-g2mx), so its balance would not close; it declares no ports until they land. The
    generator's space-heating circuit ends at it as an undeclared node, and a tank, where there is one, closes.
    """
    results = run_household(tmp_path, setup)
    report = report_of(results)
    distribution = "hds" if setup == "gas_boiler_household" else "HeatDistributionSystem"
    assert distribution in {item["component"] for item in report["undeclared"]}
    assert not report["circuit_faults"]
    generator = "boiler" if setup == "gas_boiler_household" else "DistrictHeating"
    links = overall_links(results)
    assert ("space_heating_heat", generator, f"undeclared: {distribution}") in links
    if setup == "household_district_heating_building_sizer":
        closes_in_every_step(balance_entry(results, "DHWStorage"))
        assert ("domestic_hot_water_heat", "DistrictHeating", "DHWStorage") in links
