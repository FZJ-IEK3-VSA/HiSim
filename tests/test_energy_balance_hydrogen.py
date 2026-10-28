"""The two setups with hydrogen and CHP components close their balance with the components' new energy ports.

Complete Sankey spec, PR 5c. ``dynamic_components`` runs two hydrogen CHPs beside the PV system, two batteries and the
occupancy; ``electrolyzer_with_renewables`` is a grid-connected electrolyzer plant fed by a wind turbine. Each CHP
burns hydrogen, counted on the mass flow it draws, ``GasDemandReal`` in kg/s, by its lower heating value, into
electricity, heat and a loss. ``dynamic_components`` meters what it buys (bead hisim-meter-bought-carriers-lyfl): an
electricity meter reads every electricity port, a hydrogen meter the CHPs' mass flows.

In ``electrolyzer_with_renewables`` (owner decision of 2026-10-28 on bead hisim-component-physics-4g9.69) the
transformer and rectifier receives the wind turbine's electricity, read from a series, from the "wind turbine" boundary
node, delivers it times its efficiency and loses the rest; it is rated to the turbine's 5,000 kW, so no wind is
curtailed. The electrolyzer turns the electricity it draws into hydrogen, published as a mass flow in kg/s, and loses
the rest. The electricity meter reads what the transformer delivers and what the electrolyzer draws and exports the
difference to the grid; the hydrogen meter reads the hydrogen and passes it out of the system. The CSV series is exempt
(Sankey PR 5d), so nothing in the run is undeclared and the meters' findings are enforced (owner decision Q-H). The CHPs of ``dynamic_components``
never run, because its energy manager does not read the occupancy's consumption and so never asks them for power (bead
hisim-dynamic-components-chp-idle-v8mt); their ports close at zero.

The second January week of 2021 at 900 s of each setup, run once per module: the run finishing proves every declared
balance closes; the tests read its report.
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Tuple

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
                "ElectricityDeliveredInWatt": ("production", "electricity"),
                "ElectricityReceivedInWatt": ("consumption", "wind_electricity"),
                "ConversionLossInWatt": ("loss", "electricity"),
            },
            "Electrolyzer": {
                "CurrentLoad": ("consumption", "electricity"),
                "CurrentHydrogenFlowRate": ("production", "hydrogen"),
                "ConversionLossInWatt": ("loss", "electricity"),
            },
        },
    }

    #: The peer of every exchange port of the grid-connected electrolyzer plant, as ``(component, output) -> peer``:
    #: the wind comes from its boundary node, the electricity and the hydrogen pass their meters.
    PLANT_PEERS: ClassVar[Dict[Tuple[str, str], str]] = {
        ("StandardTransformerAndRectifier", "ElectricityReceivedInWatt"): "wind turbine",
        ("StandardTransformerAndRectifier", "ElectricityDeliveredInWatt"): "ElectricityMeter",
        ("Electrolyzer", "CurrentLoad"): "ElectricityMeter",
        ("Electrolyzer", "CurrentHydrogenFlowRate"): "HydrogenMeter",
    }

    #: The transformer's rating in the setup, in kW: the wind turbine's 5,000 kW, the peak of its series (owner decision
    #: of 2026-10-28), so it carries all the wind and none is curtailed.
    TRANSFORMER_RATING_IN_KILOWATT: ClassVar[float] = 5000.0


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
def test_the_transformer_delivers_up_to_its_rating_and_the_electrolyzer_makes_hydrogen_at_a_physical_efficiency(
    setup_results: Tuple[str, Path],
) -> None:
    """The transformer loses 5 %, delivers at most its rating, and the electrolyzer turns 50 to 80 % into hydrogen.

    Catches a transformer that passes more than its rating (bead hisim-component-physics-4g9.69), a loss of the wrong
    size, a hydrogen flow in kg/h read as kg/s, and an electrolyzer that draws more than the transformer delivers.
    """
    _, results = setup_results
    transformer = {port["output"]: port["total_kwh"] for port in balance_entry(results, "StandardTransformerAndRectifier")["ports"]}
    electrolyzer = {port["output"]: port["total_kwh"] for port in balance_entry(results, "Electrolyzer")["ports"]}
    report = report_of(results)
    hours = report["timesteps"] * report["seconds_per_timestep"] / 3600.0
    assert transformer["ConversionLossInWatt"] == pytest.approx(0.05 * transformer["ElectricityReceivedInWatt"], rel=1e-9)
    assert 0.0 < transformer["ElectricityDeliveredInWatt"] < Expected.TRANSFORMER_RATING_IN_KILOWATT * hours
    assert 0.0 < electrolyzer["CurrentLoad"] < transformer["ElectricityDeliveredInWatt"]
    assert 0.5 < electrolyzer["CurrentHydrogenFlowRate"] / electrolyzer["CurrentLoad"] < 0.8


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
def test_the_plants_electricity_and_hydrogen_pass_their_meters_and_no_port_is_unmetered_or_unlinked(
    setup_results: Tuple[str, Path],
) -> None:
    """The wind comes from its node, the electricity meter exports the surplus, the hydrogen meter passes the hydrogen out.

    The electricity meter reads the transformer's delivery and the electrolyzer's draw and publishes their difference,
    as export where the delivery is larger and as import where the electrolyzer draws more; the hydrogen meter
    publishes all the hydrogen as export. Both agree in every step, and no port is unmetered or unlinked: the
    delivered electricity the electrolyzer does not draw, which reached no component before the owner's decision, now
    leaves through the meter. Catches a port taking a boundary past its meter, a meter that drops the surplus, and the
    controller (which decides and moves no energy) drawn as a peer.
    """
    _, results = setup_results
    report = report_of(results)
    peers = {
        (component, port["output"]): port["peer"]
        for component in ("StandardTransformerAndRectifier", "Electrolyzer")
        for port in balance_entry(results, component)["ports"]
        if port["role"] != "loss"
    }
    assert peers == Expected.PLANT_PEERS
    assert not report["unmetered"]["ports"] and not report["unlinked"]
    assert report["unmetered"]["enforced"] is True and not report["undeclared"]
    meters = {meter["meter"]: meter for meter in report["meters"]}
    assert {name: meter["carrier"] for name, meter in meters.items()} == {"ElectricityMeter": "electricity", "HydrogenMeter": "hydrogen"}
    assert all(meter["verdict"] == "agrees" and meter["failing_steps"] == 0 for meter in meters.values())
    electricity = meters["ElectricityMeter"]
    assert electricity["published_export_kwh"] - electricity["published_import_kwh"] == pytest.approx(
        electricity["produced_kwh"] - electricity["consumed_kwh"], rel=1e-9
    )
    assert electricity["published_export_kwh"] > 0.0 and electricity["published_import_kwh"] >= 0.0
    hydrogen = meters["HydrogenMeter"]
    assert hydrogen["published_import_kwh"] == 0.0
    assert hydrogen["published_export_kwh"] == pytest.approx(hydrogen["produced_kwh"], rel=1e-9) and hydrogen["produced_kwh"] > 0.0
