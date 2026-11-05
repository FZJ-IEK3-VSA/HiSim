"""The two setups with hydrogen and CHP components close their balance with the components' new energy ports.

Complete Sankey spec, PR 5c. ``dynamic_components`` runs two hydrogen CHPs beside the PV system, two batteries and the
occupancy; ``electrolyzer_with_renewables`` feeds a wind series through a transformer and rectifier into an
electrolyzer. Each CHP burns hydrogen, counted on the mass flow it draws, ``GasDemandReal`` in kg/s, by its lower
heating value, into electricity, heat and a loss; the transformer receives electricity, delivers it and loses the
rest; the electrolyzer turns electricity into hydrogen and loses the rest. ``dynamic_components`` meters what it buys
(bead hisim-meter-bought-carriers-lyfl): an electricity meter reads every electricity port, a hydrogen meter the CHPs'
mass flows. ``electrolyzer_with_renewables`` has no meter yet: where its wind electricity and its hydrogen go is the
open owner decision of bead hisim-component-physics-4g9.69, so every electricity and hydrogen port that no component
reads is unlinked and unmetered; the CSV series declares no ports yet, so neither finding is enforced (owner decision
Q-H).

The electrolyzer reads its load through its controller, a decision the balance never follows, so the electricity
the transformer delivers and the electricity the electrolyzer draws are two unlinked ports: in this week the
electrolyzer draws about half of what the transformer delivers, and the rest reaches no component (bead
hisim-component-physics-4g9.69). The CHPs of ``dynamic_components`` never run, because its energy manager does not
read the occupancy's consumption and so never asks them for power (bead hisim-dynamic-components-chp-idle-v8mt);
their ports close at zero.

The second January week of 2021 at 900 s of each setup, run once per module: the run finishing proves every declared
balance closes; the tests read its report.
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Set, Tuple

import pytest

from tests.test_energy_balance_generators import balance_entry, port_roles, run_household


class Expected:
    """What the two setups must show: the declared components' ports and the ports the wiring leaves unlinked."""

    #: The two setups of the spec's §9, row PR 5c.
    SETUPS: ClassVar[Tuple[str, ...]] = ("dynamic_components", "electrolyzer_with_renewables")

    #: The ports of the CHPs, as ``output -> (role, carrier)``.
    CHP_PORTS: ClassVar[Dict[str, Tuple[str, str]]] = {
        "ElectricityOutput": ("production", "electricity"),
        "ThermalOutputPower": ("production", "space_heating_heat"),
        "GasDemandReal": ("consumption", "hydrogen"),
        "ConversionLossInWatt": ("loss", "hydrogen"),
    }

    #: The meter each metered port of ``dynamic_components``' CHPs passes, as ``output -> meter``.
    CHP_METERS: ClassVar[Dict[str, str]] = {"ElectricityOutput": "ElectricityMeter", "GasDemandReal": "HydrogenMeter"}

    #: The ports of each setup's new components as ``setup -> component -> output -> (role, carrier)``.
    PORTS: ClassVar[Dict[str, Dict[str, Dict[str, Tuple[str, str]]]]] = {
        "dynamic_components": {"CHP1": CHP_PORTS, "CHP2": CHP_PORTS},
        "electrolyzer_with_renewables": {
            "StandardTransformerAndRectifier": {
                "MyTransformerOutput": ("production", "electricity"),
                "ElectricityReceivedInKilowatt": ("consumption", "electricity"),
                "ConversionLossInKilowatt": ("loss", "electricity"),
            },
            "Electrolyzer": {
                "CurrentLoad": ("consumption", "electricity"),
                "HydrogenProductionInWatt": ("production", "hydrogen"),
                "ConversionLossInWatt": ("loss", "electricity"),
            },
        },
    }

    #: The new components' metered ports that no component or meter reads in the setup without a meter, as
    #: ``setup -> {(component, output)}``; those that move energy in the week are unlinked.
    UNLINKED: ClassVar[Dict[str, Set[Tuple[str, str]]]] = {
        "electrolyzer_with_renewables": {
            ("StandardTransformerAndRectifier", "MyTransformerOutput"),
            ("StandardTransformerAndRectifier", "ElectricityReceivedInKilowatt"),
            ("Electrolyzer", "CurrentLoad"),
            ("Electrolyzer", "HydrogenProductionInWatt"),
        },
    }


@pytest.fixture(name="setup_results", scope="module", params=Expected.SETUPS)
def fixture_setup_results(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Tuple[str, Path]:
    """One setup's second January week at 900 s, run once for every test of this module.

    Returns:
        The setup's name and its result directory.
    """
    setup: str = request.param
    return setup, run_household(tmp_path_factory.mktemp(setup), setup)


def report_of(results: Path) -> Dict[str, Any]:
    """The run's balance report."""
    report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
    return report


@pytest.mark.extendedbase2
def test_the_new_components_declare_their_ports_and_close_in_every_step(setup_results: Tuple[str, Path]) -> None:
    """Each CHP, the transformer and the electrolyzer declare the ports of PR 5c and close to 1e-9 kWh in every step.

    Catches a port of the wrong role or carrier, a CHP fuel port moved off the mass flow it draws, and a loss that
    does not close its component's step.
    """
    setup, results = setup_results
    for component, ports in Expected.PORTS[setup].items():
        entry = balance_entry(results, component)
        assert entry["verdict"] == "closes" and entry["worst_step"] is None, component
        assert abs(entry["residual_kwh"]) <= 1e-9, component
        assert port_roles(entry) == ports, component


@pytest.mark.extendedbase2
@pytest.mark.parametrize("setup_results", ["electrolyzer_with_renewables"], indirect=True)
def test_the_wind_reaches_the_electrolyzer_only_in_part_and_it_makes_hydrogen_at_a_physical_efficiency(
    setup_results: Tuple[str, Path],
) -> None:
    """The transformer loses 5 %, the electrolyzer draws less than it delivers and turns 50 to 80 % into hydrogen.

    Catches a transformer loss of the wrong size, a hydrogen production rate in kg/h read as kg/s, and an
    electrolyzer that draws more than the transformer delivers.
    """
    _, results = setup_results
    transformer = {port["output"]: port["total_kwh"] for port in balance_entry(results, "StandardTransformerAndRectifier")["ports"]}
    electrolyzer = {port["output"]: port["total_kwh"] for port in balance_entry(results, "Electrolyzer")["ports"]}
    assert transformer["ConversionLossInKilowatt"] == pytest.approx(0.05 * transformer["ElectricityReceivedInKilowatt"], rel=1e-9)
    assert 0.0 < electrolyzer["CurrentLoad"] < transformer["MyTransformerOutput"]
    assert 0.5 < electrolyzer["HydrogenProductionInWatt"] / electrolyzer["CurrentLoad"] < 0.8


@pytest.mark.extendedbase2
@pytest.mark.parametrize("setup_results", ["dynamic_components"], indirect=True)
def test_the_chps_electricity_and_hydrogen_pass_their_meters_and_no_port_is_unmetered(setup_results: Tuple[str, Path]) -> None:
    """The electricity meter reads the CHPs' electricity, the hydrogen meter their mass flows, and both agree.

    The CHPs are idle in the week, so both meters agree at zero; the hydrogen meter's conversion of a mass flow is
    tested on its own in ``tests/test_gas_meter.py``. Catches a CHP port taking the "hydrogen supply" boundary past
    the meter, a meter that reads a port of another carrier, and an unmetered port left in the setup.
    """
    _, results = setup_results
    report = report_of(results)
    for component in ("CHP1", "CHP2"):
        peers = {port["output"]: port["peer"] for port in balance_entry(results, component)["ports"]}
        assert {output: peers[output] for output in Expected.CHP_METERS} == Expected.CHP_METERS, component
    assert not report["unmetered"]["ports"]
    meters = {meter["meter"]: meter for meter in report["meters"]}
    assert {name: meter["carrier"] for name, meter in meters.items()} == {"ElectricityMeter": "electricity", "HydrogenMeter": "hydrogen"}
    assert all(meter["verdict"] == "agrees" and meter["failing_steps"] == 0 for meter in meters.values())
    assert {(read["component"], read["output"]) for read in meters["HydrogenMeter"]["reads"]} == {
        ("CHP1", "GasDemandReal"),
        ("CHP2", "GasDemandReal"),
    }


@pytest.mark.extendedbase2
@pytest.mark.parametrize("setup_results", ["electrolyzer_with_renewables"], indirect=True)
def test_the_ports_no_component_reads_are_unlinked_and_unmetered_and_not_enforced(setup_results: Tuple[str, Path]) -> None:
    """Without a meter the electricity and hydrogen ports nobody reads are unlinked and unmetered, reported, not failing.

    Catches a port of these components linked to a controller (which decides and moves no energy), and an unmetered
    finding that fails the run while a component is still undeclared.
    """
    setup, results = setup_results
    report = report_of(results)
    new_components = set(Expected.PORTS[setup])
    moving = {
        (component, port["output"])
        for component in new_components
        for port in balance_entry(results, component)["ports"]
        if port["total_kwh"] != 0.0
    }
    unlinked = {(port["component"], port["output"]) for port in report["unlinked"] if port["component"] in new_components}
    assert unlinked == Expected.UNLINKED[setup] & moving
    unmetered = report["unmetered"]
    assert unmetered["enforced"] is False and "undeclared" in unmetered["not_enforced_because"]
    unmetered_ports = {(port["component"], port["output"]): port["peer"] for port in unmetered["ports"]}
    for component, output in Expected.UNLINKED[setup]:
        assert unmetered_ports[(component, output)] == "unlinked"
    assert not report["links"] and not report["meters"]
    assert not new_components & {entry["component"] for entry in report["undeclared"]}
