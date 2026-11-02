"""The energy-balance check and the Sankey graph, on synthetic components (hisim-energy-sankeys-9uoo.1, .2, .9, .18).

Each test builds a result table by hand: a few outputs that declare energy ports, the columns they fill, and
stand-ins for the wrapped components carrying their wiring. The numbers are chosen so the expected residual,
throughput and verdict can be read off the test. The graph tests follow ``roadmap/complete_energy_sankeys_spec.md``
§4 and owner decision Q-J: controllers are exempt and never peers, a meter is a node between its boundary node and
the devices whose metered ports it reads (directly or through exempt controllers), and what a meter publishes must
equal what it reads in every step; a gap is never absorbed.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import json

import numpy as np
import pandas as pd
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.config import ComponentID
from hisim.energy_port import EnergyPort, MeteredExchange
from hisim.postprocessing.energy_balance import (
    EnergyBalanceError,
    EnergyBalanceReport,
)
from hisim.postprocessing.energy_balance.check import RoleTotals
from hisim.postprocessing.energy_balance.ports import Peer, PeerKind
from hisim.postprocessing.energy_balance.sankey import SankeyDiagram
from hisim.postprocessing.postprocessing_main import PostProcessor
from hisim.postprocessingoptions import PostProcessingOptions

IN, OUT, LOSS, STORED = lt.EnergyRole.IN, lt.EnergyRole.OUT, lt.EnergyRole.LOSS, lt.EnergyRole.STORED_CHANGE
SH = lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT
ELECTRICITY = lt.EnergyBalanceCarrier.ELECTRICITY
GAS = lt.EnergyBalanceCarrier.NATURAL_GAS
SOLAR = lt.EnergyBalanceCarrier.SOLAR

#: (component, field, unit, port or None, values per step)
Column = Tuple[str, str, lt.Units, Optional[EnergyPort], Sequence[float]]


def output(component: str, field: str, unit: lt.Units, port: Optional[EnergyPort]) -> cp.ComponentOutput:
    """One output of a synthetic component."""
    return cp.ComponentOutput(
        component, field, lt.LoadTypes.HEATING, unit, energy_port=port, component_id=ComponentID(component)
    )


def wired_input(field: str, source: str, source_field: str) -> SimpleNamespace:
    """An input connected to ``source.source_field``, as the wiring leaves it."""
    return SimpleNamespace(field_name=field, src_object_name=source, src_field_name=source_field)


class Meter(cp.MeterComponent):
    """A stand-in electricity meter: a node, it publishes the grid import and export in W."""

    def metered_exchanges(self) -> Tuple[MeteredExchange, ...]:
        """The grid import and export."""
        return (MeteredExchange(ELECTRICITY, "FromGridInWatt", "ToGridInWatt"),)


class GasMeter(cp.MeterComponent):
    """A stand-in gas meter: a node, it publishes the natural gas bought in kWh."""

    def metered_exchanges(self) -> Tuple[MeteredExchange, ...]:
        """The gas bought."""
        return (MeteredExchange(GAS, "GasFromGrid"),)


class SilentMeter(cp.MeterComponent):
    """A stand-in meter that declares no metered exchange: exempt by its kind, since it publishes nothing to check."""


class Manager(cp.L2ControllerComponent):
    """A stand-in energy manager: exempt by its kind."""


class Controller(cp.L1ControllerComponent):
    """A stand-in L1 controller: exempt by its kind."""


class Helper(cp.PhysicsComponent):
    """A stand-in physics helper that states why it declares no port."""

    ENERGY_PORTS_EXEMPT_BECAUSE = "it sums two inputs, which moves no energy"


def stand_in(
    name: str, component_class: Optional[type], component_inputs: List[SimpleNamespace], outputs: List[Any]
) -> Any:
    """A component stand-in carrying its name, wiring and outputs; of ``component_class`` when one is given."""
    if component_class is None:
        return SimpleNamespace(component_name=name, inputs=component_inputs, outputs=outputs)
    component: Any = object.__new__(component_class)
    component.component_name = name
    component.inputs = component_inputs
    component.outputs = outputs
    return component


def report(
    columns: Sequence[Column],
    seconds_per_timestep: int = 900,
    inputs: Optional[Dict[str, List[SimpleNamespace]]] = None,
    extra_components: Sequence[str] = (),
    classes: Optional[Dict[str, type]] = None,
) -> EnergyBalanceReport:
    """The balance report of a run whose result table has ``columns``; ``classes`` gives stand-ins a kind."""
    outputs = [output(component, field, unit, port) for component, field, unit, port, _ in columns]
    steps = len(columns[0][4])
    table = pd.DataFrame(
        np.array([values for *_, values in columns], dtype=float).T.reshape(steps, len(columns)),
        index=pd.date_range("2021-01-01", periods=steps, freq=f"{seconds_per_timestep}s"),
    )
    names: List[str] = []
    for component, *_ in columns:
        if component not in names:
            names.append(component)
    names.extend(extra_components)
    wrapped: List[Any] = [
        SimpleNamespace(
            my_component=stand_in(
                name,
                (classes or {}).get(name),
                (inputs or {}).get(name, []),
                [o for o in outputs if o.component_name == name],
            )
        )
        for name in names
    ]
    return EnergyBalanceReport(table, outputs, wrapped, seconds_per_timestep)


def vessel(generator_watt: float, draw_watt_hour: float, loss_watt: float, stored_watt_hour: float) -> List[Column]:
    """A vessel's four ports over one 900 s step: generator heat in W, draw in Wh, loss in W, storage change in Wh."""
    return [
        ("Tank", "HeatIn", lt.Units.WATT, EnergyPort(IN, SH), [generator_watt]),
        ("Tank", "HeatOut", lt.Units.WATT_HOUR, EnergyPort(OUT, SH), [draw_watt_hour]),
        ("Tank", "Loss", lt.Units.WATT, EnergyPort(LOSS, SH), [loss_watt]),
        ("Tank", "Stored", lt.Units.WATT_HOUR, EnergyPort(STORED, SH), [stored_watt_hour]),
    ]


# --- the contract --------------------------------------------------------------------------------------------


@pytest.mark.base
def test_an_energy_port_on_an_output_that_is_neither_power_nor_energy_is_refused() -> None:
    """A temperature cannot carry energy: the output is refused when it is declared."""
    with pytest.raises(ValueError, match="power or an energy"):
        output("Tank", "Temperature", lt.Units.CELSIUS, EnergyPort(OUT, SH))


@pytest.mark.base
def test_an_energy_port_names_either_an_environment_node_or_a_component() -> None:
    """``environment`` with ``peer_input`` is refused; a port naming its environment goes there whatever its carrier."""
    with pytest.raises(ValueError, match="either an environment node"):
        EnergyPort(LOSS, SH, peer_input="X", environment=lt.EnvironmentNode.OUTDOORS)
    named = EnergyPort(
        OUT, lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT, environment=lt.EnvironmentNode.HOT_WATER_USE
    )
    assert named.default_boundary() == "hot water drawn at the taps" and not named.names_a_component


@pytest.mark.base
@pytest.mark.parametrize(
    ("port", "boundary"),
    [
        (EnergyPort(IN, ELECTRICITY), None),
        (EnergyPort(OUT, ELECTRICITY), None),
        (EnergyPort(LOSS, ELECTRICITY), "outdoors"),
        (EnergyPort(IN, GAS), "natural gas supply"),
        (EnergyPort(IN, lt.EnergyBalanceCarrier.HEATING_OIL), "heating oil supply"),
        (EnergyPort(IN, lt.EnergyBalanceCarrier.WOOD_CHIPS), "wood chips supply"),
        (EnergyPort(IN, lt.EnergyBalanceCarrier.DISTRICT_HEAT), "district heat supply"),
        (EnergyPort(OUT, GAS), None),
        (EnergyPort(LOSS, GAS), "outdoors"),
        (EnergyPort(IN, lt.EnergyBalanceCarrier.AMBIENT_HEAT), "ambient heat"),
        (EnergyPort(OUT, lt.EnergyBalanceCarrier.AMBIENT_HEAT), "ambient heat"),
        (EnergyPort(IN, SOLAR), "sun"),
        (EnergyPort(OUT, SOLAR), None),
        (EnergyPort(IN, SH), None),
        (EnergyPort(OUT, lt.EnergyBalanceCarrier.ROOM_HEAT), None),
        (EnergyPort(LOSS, lt.EnergyBalanceCarrier.ROOM_HEAT), "outdoors"),
    ],
)
def test_every_carrier_has_the_boundary_default_of_the_spec(port: EnergyPort, boundary: Optional[str]) -> None:
    """Spec §4.3: bought carriers from their supply, losses outdoors; electricity and heat have no default (Q-J)."""
    assert port.default_boundary() == boundary


@pytest.mark.base
@pytest.mark.parametrize(
    ("role", "carrier", "environment", "refusal"),
    [
        (IN, ELECTRICITY, lt.EnvironmentNode.OUTDOORS, "is metered and names no environment"),
        (OUT, ELECTRICITY, lt.EnvironmentNode.OUTDOORS, "is metered and names no environment"),
        (IN, GAS, lt.EnvironmentNode.OUTDOORS, "is metered and names no environment"),
        (IN, lt.EnergyBalanceCarrier.DISTRICT_HEAT, lt.EnvironmentNode.OUTDOORS, "is metered and names no environment"),
        (IN, ELECTRICITY, lt.EnvironmentNode.GRID_IMPORT, "cannot name 'grid import'"),
        (LOSS, ELECTRICITY, lt.EnvironmentNode.GRID_EXPORT, "cannot name 'grid export'"),
        (OUT, SH, lt.EnvironmentNode.GRID_EXPORT, "cannot name 'grid export'"),
    ],
)
def test_a_port_cannot_name_an_environment_that_takes_its_flow_past_every_meter(
    role: lt.EnergyRole, carrier: lt.EnergyBalanceCarrier, environment: lt.EnvironmentNode, refusal: str
) -> None:
    """A metered port's peer is its meter, and the grid is reached only through the meter (Q-J): both are refused.

    A loss of electricity may still name its boundary, and a heat port any node but the grid.
    """
    with pytest.raises(ValueError, match=refusal):
        EnergyPort(role, carrier, environment=environment)
    assert EnergyPort(LOSS, ELECTRICITY, environment=lt.EnvironmentNode.OUTDOORS).default_boundary() == "outdoors"
    assert not EnergyPort(LOSS, ELECTRICITY).is_metered and EnergyPort(IN, GAS).is_metered


@pytest.mark.base
def test_a_metered_exchange_names_a_metered_carrier_and_two_distinct_outputs() -> None:
    """A meter counts electricity or a bought carrier, with one output per direction."""
    with pytest.raises(ValueError, match="got 'FlowInWatt' for both"):
        MeteredExchange(ELECTRICITY, "FlowInWatt", "FlowInWatt")
    with pytest.raises(ValueError, match="not space_heating_heat"):
        MeteredExchange(SH, "HeatInWatt")
    assert MeteredExchange(GAS, "GasFromGrid").export_output is None


@pytest.mark.base
def test_an_energy_port_names_its_peer_by_one_input_or_one_output_not_both() -> None:
    """``peer_input`` with ``peer_output`` is refused, naming both."""
    with pytest.raises(ValueError, match="peer_input='A', peer_output='B'"):
        EnergyPort(OUT, SH, peer_input="A", peer_output="B")


@pytest.mark.base
@pytest.mark.parametrize(
    ("unit", "seconds", "factor"),
    [
        (lt.Units.WATT, 900, 0.25e-3),
        (lt.Units.KILOWATT, 3600, 1.0),
        (lt.Units.WATT_HOUR, 900, 1e-3),
        (lt.Units.KWH, 60, 1.0),
        (lt.Units.KWH_PER_TIMESTEP, 60, 1.0),
        (lt.Units.JOULE, 60, 1 / 3.6e6),
        (lt.Units.KILOJOULE, 60, 1 / 3.6e3),
    ],
)
def test_every_power_and_energy_unit_converts_to_kilowatt_hours_per_step(
    unit: lt.Units, seconds: int, factor: float
) -> None:
    """A power is multiplied by the timestep, an energy taken per step; validate_unit accepts both."""
    EnergyPort.validate_unit(unit)
    assert EnergyPort.kilowatt_hours_per_step(unit, seconds) == pytest.approx(factor)


@pytest.mark.base
def test_validate_unit_refuses_what_is_neither_power_nor_energy() -> None:
    """The unit check stands on its own, without a timestep."""
    with pytest.raises(ValueError, match="power or an energy"):
        EnergyPort.validate_unit(lt.Units.CELSIUS)


@pytest.mark.base
def test_role_totals_refuse_a_role_they_do_not_know() -> None:
    """Every role is named; anything else raises instead of being booked as a storage change."""
    totals = RoleTotals()
    for role in lt.EnergyRole:
        totals.add(role, 1.0)
    assert totals.as_dict()["residual_kwh"] == pytest.approx(-2.0)
    with pytest.raises(ValueError, match="not an energy role"):
        totals.add("heat", 1.0)  # type: ignore[arg-type]


# --- the check -----------------------------------------------------------------------------------------------


@pytest.mark.base
def test_a_vessel_whose_ports_close_passes_with_power_converted_by_the_timestep() -> None:
    """4 kW for 900 s is 1 kWh in; 0.7 kWh drawn, 0.2 kW lost (0.05 kWh), 0.25 kWh stored: residual 0."""
    balance_report = report(vessel(4000.0, 700.0, 200.0, 250.0))

    (balance,) = balance_report.balances
    assert balance.closes
    assert balance.totals.energy_in == pytest.approx(1.0)
    assert balance.totals.loss == pytest.approx(0.05)
    assert balance.residual == pytest.approx(0.0, abs=1e-15)
    assert balance.throughput == pytest.approx(1.0)


@pytest.mark.base
def test_the_same_energy_as_power_at_3600_s_and_as_energy_closes() -> None:
    """1 kW for one hour in, 1000 Wh out: W and Wh meet at the timestep."""
    balance_report = report(
        [
            ("Pipe", "In", lt.Units.KILOWATT, EnergyPort(IN, SH), [1.0]),
            ("Pipe", "Out", lt.Units.WATT_HOUR, EnergyPort(OUT, SH), [1000.0]),
        ],
        seconds_per_timestep=3600,
    )
    assert balance_report.balances[0].closes


@pytest.mark.base
def test_a_leaking_vessel_fails_and_reports_its_worst_step(tmp_path: Path) -> None:
    """1 kWh in, 0.5 kWh accounted for: 50 % of the throughput is missing; the report says so."""
    balance_report = report(vessel(4000.0, 300.0, 200.0, 150.0))

    (balance,) = balance_report.balances
    assert not balance.closes
    assert balance.residual == pytest.approx(0.5)
    assert balance.residual_percent == pytest.approx(50.0)

    written = json.loads(balance_report.write(str(tmp_path), with_html=False).read_text())
    (entry,) = written["components"]
    assert written["verdict"] == entry["verdict"] == "does_not_close"
    assert entry["worst_step"] == {
        "index": 0,
        "time": "2021-01-01T00:00:00",
        "residual_kwh": pytest.approx(0.5),
        "throughput_kwh": pytest.approx(1.0),
    }
    assert entry["carriers"]["space_heating_heat"]["residual_kwh"] == pytest.approx(0.5)


@pytest.mark.base
@pytest.mark.parametrize(("accounted", "closes"), [(1.0 - 0.999e-3, True), (1.0 - 1.001e-3, False)])
def test_a_step_closes_up_to_a_tenth_of_a_percent_of_its_throughput(accounted: float, closes: bool) -> None:
    """1 kWh in: 0.999 per mille missing closes, 1.001 per mille does not."""
    balance_report = report(
        [
            ("Pipe", "In", lt.Units.KWH, EnergyPort(IN, SH), [1.0]),
            ("Pipe", "Out", lt.Units.KWH, EnergyPort(OUT, SH), [accounted]),
        ]
    )
    assert balance_report.balances[0].closes is closes


@pytest.mark.base
@pytest.mark.parametrize(("residual", "closes"), [(0.9e-6, True), (1.1e-6, False)])
def test_a_step_with_nothing_passing_may_carry_a_microkilowatt_hour(residual: float, closes: bool) -> None:
    """A step whose only flow is a stray micro-kWh closes up to 1e-6 kWh of residual, not 0.1 % of it."""
    values = [0.0] * 1000
    values[3] = residual
    balance_report = report(
        [
            ("Pipe", "In", lt.Units.KWH, EnergyPort(IN, SH), values),
            ("Pipe", "Big", lt.Units.KWH, EnergyPort(IN, SH), [0.0, 1.0] + [0.0] * 998),
            ("Pipe", "Out", lt.Units.KWH, EnergyPort(OUT, SH), [0.0, 1.0] + [0.0] * 998),
        ]
    )
    assert bool(balance_report.balances[0].step_failures.any()) is not closes
    assert balance_report.balances[0].closes is closes


@pytest.mark.base
def test_small_residuals_on_every_step_still_fail_the_annual_rule() -> None:
    """1000 steps of 0.1 Wh each, 0.9 mWh missing per step: each step is under 1e-6 kWh, the run is 0.9 % short."""
    balance_report = report(
        [
            ("Pipe", "In", lt.Units.WATT_HOUR, EnergyPort(IN, SH), [0.1] * 1000),
            ("Pipe", "Out", lt.Units.WATT_HOUR, EnergyPort(OUT, SH), [0.1 - 0.0009] * 1000),
        ]
    )
    (balance,) = balance_report.balances
    assert not balance.step_failures.any()
    assert not balance.closes
    assert balance.residual_percent == pytest.approx(0.9)


@pytest.mark.base
def test_a_balance_that_does_not_close_fails_the_run_naming_what_and_where(tmp_path: Path) -> None:
    """The report is written first when asked; the error names component, carrier, residual and worst step."""
    leaking = report(vessel(4000.0, 300.0, 200.0, 150.0))
    with pytest.raises(EnergyBalanceError) as raised:
        leaking.conclude(str(tmp_path), with_html=False)
    message = str(raised.value)
    assert "component Tank" in message
    assert "residual 0.5 kWh (50 % of 1 kWh throughput)" in message
    assert "space_heating_heat 0.5 kWh" in message
    assert "worst step 0 (2021-01-01T00:00:00)" in message
    assert json.loads((tmp_path / "balance_report.json").read_text())["verdict"] == "does_not_close"
    assert isinstance(raised.value, ValueError)


@pytest.mark.base
def test_without_a_result_directory_nothing_is_written_and_the_run_still_fails(tmp_path: Path) -> None:
    """The check is independent of the files: no directory, no report, the same error."""
    with pytest.raises(EnergyBalanceError, match="Tank"):
        report(vessel(4000.0, 300.0, 200.0, 150.0)).conclude(None)
    assert report(vessel(4000.0, 700.0, 200.0, 250.0)).conclude(None) is None
    assert not list(tmp_path.iterdir())


@pytest.mark.base
def test_every_failing_component_is_named() -> None:
    """Two leaking components, one error naming both."""
    columns = vessel(4000.0, 300.0, 200.0, 150.0) + [
        ("Pipe", "In", lt.Units.KWH, EnergyPort(IN, SH), [1.0]),
        ("Pipe", "Out", lt.Units.KWH, EnergyPort(OUT, SH), [0.5]),
    ]
    with pytest.raises(EnergyBalanceError) as raised:
        report(columns).conclude(None)
    assert "component Tank" in str(raised.value) and "component Pipe" in str(raised.value)


@pytest.mark.base
def test_the_post_processor_fails_the_run_whatever_the_options(tmp_path: Path) -> None:
    """Without EXPORT_ENERGY_BALANCE the files are not written, and the run fails all the same; with it, both."""
    for options, written in (([], False), ([PostProcessingOptions.EXPORT_ENERGY_BALANCE], True)):
        directory = tmp_path / str(written)
        directory.mkdir()
        ppdt: Any = SimpleNamespace(
            post_processing_options=options,
            simulation_parameters=SimpleNamespace(result_directory=str(directory), seconds_per_timestep=900),
        )
        with pytest.raises(EnergyBalanceError, match="Tank"):
            PostProcessor.export_sankeys(ppdt, report(vessel(4000.0, 300.0, 200.0, 150.0)))
        assert (directory / "balance_report.json").exists() is written


@pytest.mark.base
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_port_that_is_not_finite_fails_the_run_naming_the_port_and_its_first_step(
    bad: float, tmp_path: Path
) -> None:
    """A NaN or an infinity in a declared port fails before anything is written."""
    columns: List[Column] = [
        ("Pipe", "In", lt.Units.KWH, EnergyPort(IN, SH), [1.0, bad, bad]),
        ("Pipe", "Out", lt.Units.KWH, EnergyPort(OUT, SH), [1.0, 1.0, 1.0]),
    ]
    with pytest.raises(EnergyBalanceError) as raised:
        report(columns).conclude(str(tmp_path))
    assert "energy port Pipe.In (in, space_heating_heat)" in str(raised.value)
    assert "at step 1 (2021-01-01T00:15:00), in 2 steps in all" in str(raised.value)
    assert "Pipe.Out" not in str(raised.value)
    assert not list(tmp_path.iterdir())


@pytest.mark.base
def test_a_component_without_ports_is_undeclared_never_failing(tmp_path: Path) -> None:
    """A component with no declared port is listed as undeclared; the run passes."""
    balance_report = report(
        [("Tank", "Temperature", lt.Units.CELSIUS, None, [40.0]), *vessel(4000.0, 700.0, 200.0, 250.0)],
        extra_components=["Weather"],
    )
    report_path = balance_report.conclude(str(tmp_path), with_html=False)
    assert report_path is not None
    written = json.loads(report_path.read_text())

    assert written["verdict"] == "closes"
    assert written["undeclared"] == [{"component": "Weather", "class": "SimpleNamespace"}]
    assert [entry["component"] for entry in written["components"]] == ["Tank"]


@pytest.mark.base
def test_a_run_in_which_nothing_declares_a_port_passes_and_says_so(tmp_path: Path) -> None:
    """No port anywhere: not an error; the report's verdict is nothing_declared."""
    balance_report = report([("Tank", "Temperature", lt.Units.CELSIUS, None, [40.0])], extra_components=["Weather"])
    report_path = balance_report.conclude(str(tmp_path), with_html=False)
    assert report_path is not None
    written = json.loads(report_path.read_text())
    assert written["verdict"] == "nothing_declared"
    assert written["components"] == [] and written["links"] == []
    assert [entry["component"] for entry in written["undeclared"]] == ["Tank", "Weather"]


@pytest.mark.base
def test_a_balance_that_closes_reports_no_worst_step(tmp_path: Path) -> None:
    """The worst step is a failure's; a closed balance has none."""
    balance_report = report(vessel(4000.0, 700.0, 200.0, 250.0))
    assert balance_report.balances[0].worst_step is None
    (entry,) = json.loads(balance_report.write(str(tmp_path), with_html=False).read_text())["components"]
    assert entry["worst_step"] is None


@pytest.mark.base
def test_a_negated_port_is_read_with_the_sign_of_its_role() -> None:
    """Heat leaving published as -0.7 kWh, declared negated, closes like +0.7 kWh."""
    balance_report = report(
        [
            ("Pipe", "In", lt.Units.KWH, EnergyPort(IN, SH), [0.7]),
            ("Pipe", "Out", lt.Units.KWH, EnergyPort(OUT, SH, negated=True), [-0.7]),
        ]
    )
    assert balance_report.balances[0].closes


# --- the Sankey ----------------------------------------------------------------------------------------------


def chain(received_watt_hour: float = 699.6) -> EnergyBalanceReport:
    """A boiler (undeclared) feeds a tank, the tank a radiator loop, the loop the building (undeclared).

    The tank sends 0.7 kWh, the loop books 0.6996 kWh of it: 0.4 Wh, within the tolerance, is sent but not
    received. The loop loses 0.05 kWh it does not declare, so it carries a residual of 0.05 kWh, beyond the
    tolerance. One 900 s step.
    """
    columns: List[Column] = [
        ("Tank", "HeatIn", lt.Units.WATT, EnergyPort(IN, SH, peer_input="FlowFromBoiler"), [4000.0]),
        ("Tank", "HeatOut", lt.Units.WATT_HOUR, EnergyPort(OUT, SH), [700.0]),
        ("Tank", "Loss", lt.Units.WATT, EnergyPort(LOSS, SH), [200.0]),
        ("Tank", "Stored", lt.Units.WATT_HOUR, EnergyPort(STORED, SH), [250.0]),
        (
            "Loop",
            "Received",
            lt.Units.WATT_HOUR,
            EnergyPort(IN, SH, peer_input="HeatFromTank"),
            [received_watt_hour],
        ),
        ("Loop", "Delivered", lt.Units.WATT_HOUR, EnergyPort(OUT, SH), [received_watt_hour - 50.0]),
        ("Pump", "Power", lt.Units.WATT, EnergyPort(IN, ELECTRICITY), [100.0]),
    ]
    inputs = {
        "Tank": [wired_input("FlowFromBoiler", "Boiler", "Flow")],
        "Loop": [wired_input("HeatFromTank", "Tank", "HeatOut")],
        "Building": [wired_input("HeatFromLoop", "Loop", "Delivered")],
    }
    return report(columns, inputs=inputs, extra_components=["Boiler", "Building"])


def flows(diagram: Dict[str, Any]) -> Dict[Tuple[str, str], float]:
    """A diagram's links by (source, target) name."""
    names = [node["name"] for node in diagram["nodes"]]
    return {(names[link["source"]], names[link["target"]]): link["value_kwh"] for link in diagram["links"]}


@pytest.mark.base
def test_the_sankey_draws_each_transfer_once_and_only_residuals_beyond_the_tolerance() -> None:
    """Links follow the wiring and the receiver's figure is drawn.

    The loop's 0.05 kWh residual is beyond the tolerance and goes to unaccounted; the 0.4 Wh the tank sent and the
    loop did not book is within it, so it is not drawn (owner, 2026-10-20), but the report still lists it.
    """
    balance_report = chain()
    diagrams = balance_report.sankeys()
    heat = flows(diagrams["space_heating_heat"])

    assert heat == {
        ("undeclared: Boiler", "Tank"): pytest.approx(1.0),
        ("Tank", "Tank · Loss"): pytest.approx(0.05),
        ("Tank · Loss", "outdoors"): pytest.approx(0.05),
        ("Tank", "Tank · stored"): pytest.approx(0.25),
        ("Tank", "Loop"): pytest.approx(0.6996),
        ("Loop", "undeclared: Building"): pytest.approx(0.6496),
        ("Loop", "unaccounted"): pytest.approx(0.05),
    }
    (link,) = balance_report.links
    assert link.closes and link.as_dict()["difference_kwh"] == pytest.approx(0.0004)
    kinds = {node["name"]: node["kind"] for node in diagrams["overall"]["nodes"]}
    assert kinds["undeclared: Boiler"] == "undeclared" and kinds["unaccounted"] == "unaccounted"
    assert kinds["outdoors"] == "boundary" and kinds["Tank · stored"] == "storage" and kinds["Tank · Loss"] == "loss"
    assert kinds["unlinked"] == "unlinked"

    overall = flows(diagrams["overall"])
    assert overall[("unlinked", "Pump")] == pytest.approx(0.025)
    for node, within_tolerance in (("Tank", 0.0004), ("Loop", 0.0), ("Tank · Loss", 0.0)):
        inflow = sum(value for (_, target), value in overall.items() if target == node)
        outflow = sum(value for (source, _), value in overall.items() if source == node)
        assert inflow - outflow == pytest.approx(within_tolerance, abs=1e-12), node


@pytest.mark.base
def test_the_sankey_files_are_written_into_the_result_directory(tmp_path: Path) -> None:
    """The JSON and one HTML per view, the connected graph first, sharing one plotly.min.js."""
    chain().write(str(tmp_path))

    written = json.loads((tmp_path / "energy_sankeys" / "energy_sankey.json").read_text())
    assert list(written) == ["overall", "electricity", "space_heating_heat"]
    for name in written:
        assert (tmp_path / "energy_sankeys" / f"{name}.html").stat().st_size > 0
    assert (tmp_path / "energy_sankeys" / "plotly.min.js").exists()


@pytest.mark.base
def test_a_receiver_booking_more_than_was_sent_within_the_tolerance_draws_no_negative_remainder() -> None:
    """The loop books 0.7004 kWh of the tank's 0.7: no link between the tank and unaccounted, the link closes."""
    balance_report = chain(received_watt_hour=700.4)
    heat = flows(balance_report.sankeys()["space_heating_heat"])
    assert ("Tank", "unaccounted") not in heat and ("unaccounted", "Tank") not in heat
    assert heat[("Tank", "Loop")] == pytest.approx(0.7004)
    (link,) = balance_report.links
    assert link.closes and link.sent == pytest.approx(0.7) and link.received == pytest.approx(0.7004)


@pytest.mark.base
@pytest.mark.parametrize(("received_watt_hour", "percent"), [(600.0, "14.29"), (800.0, "-12.5")])
def test_a_link_whose_two_sides_disagree_fails_the_run_in_either_direction(
    received_watt_hour: float, percent: str
) -> None:
    """The tank sends 0.7 kWh; the loop booking 0.6 or 0.8 kWh of it is beyond the tolerance: the run fails."""
    balance_report = chain(received_watt_hour=received_watt_hour)
    (link,) = balance_report.failing_links
    assert (link.sender, link.receiver, link.carrier) == ("Tank", "Loop", "space_heating_heat")
    heat = flows(balance_report.sankeys()["space_heating_heat"])
    difference = (700.0 - received_watt_hour) / 1000
    edge = ("Tank", "unaccounted") if difference > 0 else ("unaccounted", "Tank")
    assert heat[edge] == pytest.approx(abs(difference)), "a difference beyond the tolerance is drawn"
    with pytest.raises(EnergyBalanceError) as raised:
        balance_report.conclude(None)
    assert (
        f"link Tank -> Loop (space_heating_heat): Tank sends 0.7 kWh, Loop receives {received_watt_hour / 1000:g} kWh "
        f"({percent} % of the larger)"
    ) in str(raised.value)


# --- one-sided bookings between declared components (owner, 2026-10-02: always fail hard) ----------------------


def receiver_only() -> EnergyBalanceReport:
    """The loop books 0.7 kWh of heat from the tank; the tank declares ports, but none sends heat to the loop."""
    columns: List[Column] = [
        ("Tank", "Power", lt.Units.KWH, EnergyPort(IN, ELECTRICITY), [1.0]),
        ("Tank", "Loss", lt.Units.KWH, EnergyPort(LOSS, SH), [1.0]),
        ("Loop", "Received", lt.Units.KWH, EnergyPort(IN, SH, peer_input="HeatFromTank"), [0.7]),
        ("Loop", "Delivered", lt.Units.KWH, EnergyPort(OUT, SH), [0.7]),
    ]
    inputs = {
        "Loop": [wired_input("HeatFromTank", "Tank", "HeatOut")],
        "Building": [wired_input("HeatFromLoop", "Loop", "Delivered")],
    }
    return report(columns, inputs=inputs, extra_components=["Building"])


def sender_only() -> EnergyBalanceReport:
    """The tank sends 0.7 kWh of heat, read by the loop; the loop declares ports, but none receives it."""
    columns: List[Column] = [
        ("Tank", "Power", lt.Units.KWH, EnergyPort(IN, ELECTRICITY), [0.7]),
        ("Tank", "HeatOut", lt.Units.KWH, EnergyPort(OUT, SH), [0.7]),
        ("Loop", "Power", lt.Units.KWH, EnergyPort(IN, ELECTRICITY), [0.7]),
        ("Loop", "Delivered", lt.Units.KWH, EnergyPort(OUT, SH), [0.7]),
    ]
    inputs = {
        "Loop": [wired_input("HeatFromTank", "Tank", "HeatOut")],
        "Building": [wired_input("HeatFromLoop", "Loop", "Delivered")],
    }
    return report(columns, inputs=inputs, extra_components=["Building"])


@pytest.mark.base
def test_a_booking_only_the_receiver_declares_fails_the_run_naming_both_the_carrier_and_the_amount(
    tmp_path: Path,
) -> None:
    """Both components declare ports, only the loop books the transfer: the link fails, the report lists it."""
    balance_report = receiver_only()
    assert all(balance.closes for balance in balance_report.balances)
    (link,) = balance_report.failing_links
    assert (link.sender, link.receiver, link.carrier, link.sent) == ("Tank", "Loop", "space_heating_heat", None)
    with pytest.raises(EnergyBalanceError) as raised:
        balance_report.conclude(str(tmp_path), with_html=False)
    assert (
        "link Tank -> Loop (space_heating_heat): Loop books 0.7 kWh of space_heating_heat from Tank, "
        "but Tank declares nothing sent to Loop"
    ) in str(raised.value)
    written = json.loads((tmp_path / "balance_report.json").read_text())
    (entry,) = written["links"]
    assert entry["sent_kwh"] is None and entry["received_kwh"] == pytest.approx(0.7)
    assert entry["verdict"] == "one_sided" and "declares nothing sent" in entry["reason"]
    assert written["verdict"] == "does_not_close"
    assert flows(balance_report.sankeys()["space_heating_heat"])[("Tank", "Loop")] == pytest.approx(0.7)


@pytest.mark.base
def test_a_booking_only_the_sender_declares_fails_the_run_naming_both_the_carrier_and_the_amount() -> None:
    """Both components declare ports, only the tank books the transfer: the link fails."""
    balance_report = sender_only()
    assert all(balance.closes for balance in balance_report.balances)
    (link,) = balance_report.failing_links
    assert (link.sender, link.receiver, link.received) == ("Tank", "Loop", None)
    assert link.as_dict()["sent_kwh"] == pytest.approx(0.7)
    with pytest.raises(EnergyBalanceError) as raised:
        balance_report.conclude(None)
    assert (
        "link Tank -> Loop (space_heating_heat): Tank sends 0.7 kWh of space_heating_heat to Loop, "
        "but Loop declares nothing received from Tank"
    ) in str(raised.value)
    assert flows(balance_report.sankeys()["space_heating_heat"])[("Tank", "Loop")] == pytest.approx(0.7)


@pytest.mark.base
def test_a_one_sided_booking_toward_a_component_without_ports_passes(tmp_path: Path) -> None:
    """The tank books heat from an undeclared boiler and to an undeclared building: no link, the run passes."""
    columns: List[Column] = [
        ("Tank", "HeatIn", lt.Units.KWH, EnergyPort(IN, SH, peer_input="FlowFromBoiler"), [0.7]),
        ("Tank", "HeatOut", lt.Units.KWH, EnergyPort(OUT, SH), [0.7]),
    ]
    inputs = {
        "Tank": [wired_input("FlowFromBoiler", "Boiler", "Flow")],
        "Building": [wired_input("HeatFromTank", "Tank", "HeatOut")],
    }
    balance_report = report(columns, inputs=inputs, extra_components=["Boiler", "Building"])
    report_path = balance_report.conclude(str(tmp_path), with_html=False)
    assert report_path is not None
    written = json.loads(report_path.read_text())
    assert written["verdict"] == "closes" and written["links"] == []
    assert [entry["component"] for entry in written["undeclared"]] == ["Boiler", "Building"]


@pytest.mark.base
def test_a_transfer_both_declared_sides_book_alike_passes(tmp_path: Path) -> None:
    """The tank sends 0.7 kWh, the loop books 0.7 kWh from it: the link closes and states no reason."""
    columns: List[Column] = [
        ("Tank", "Power", lt.Units.KWH, EnergyPort(IN, ELECTRICITY), [0.7]),
        ("Tank", "HeatOut", lt.Units.KWH, EnergyPort(OUT, SH), [0.7]),
        ("Loop", "Received", lt.Units.KWH, EnergyPort(IN, SH, peer_input="HeatFromTank"), [0.7]),
        ("Loop", "Delivered", lt.Units.KWH, EnergyPort(OUT, SH), [0.7]),
    ]
    inputs = {
        "Loop": [wired_input("HeatFromTank", "Tank", "HeatOut")],
        "Building": [wired_input("HeatFromLoop", "Loop", "Delivered")],
    }
    balance_report = report(columns, inputs=inputs, extra_components=["Building"])
    report_path = balance_report.conclude(str(tmp_path), with_html=False)
    assert report_path is not None
    (entry,) = json.loads(report_path.read_text())["links"]
    assert entry["verdict"] == "closes" and entry["reason"] is None
    assert entry["sent_kwh"] == pytest.approx(0.7) and entry["received_kwh"] == pytest.approx(0.7)


@pytest.mark.base
def test_a_sankey_is_titled_with_its_carrier_in_words() -> None:
    """The carrier's identifier is written as words in the title; ``overall`` reads "all carriers"."""
    assert SankeyDiagram.title("domestic_hot_water_heat") == "Energy flows over the run: domestic hot water heat (kWh)"
    assert SankeyDiagram.title("overall") == "Energy flows over the run: all carriers (kWh)"


@pytest.mark.base
def test_a_sankey_node_label_carries_the_energy_through_the_node() -> None:
    """A printed Sankey shows no hover text, so each node label names its energy: the larger of in and out."""
    diagram = {
        "nodes": [{"name": "grid", "kind": "undeclared"}, {"name": "pump", "kind": "component"},
                  {"name": "store", "kind": "undeclared"}, {"name": "ambient heat", "kind": "environment"}],
        "links": [
            {"source": 0, "target": 1, "value_kwh": 3621.6, "carrier": "electricity", "label": "a"},
            {"source": 3, "target": 1, "value_kwh": 11630.7, "carrier": "ambient_heat", "label": "b"},
            {"source": 1, "target": 2, "value_kwh": 15252.3, "carrier": "space_heating_heat", "label": "c"},
        ],
    }
    assert SankeyDiagram.node_labels(diagram) == [
        "grid<br>3,622 kWh",
        "pump<br>15,252 kWh",
        "store<br>15,252 kWh",
        "ambient heat<br>11,631 kWh",
    ]


# --- the graph model of the complete Sankey (roadmap/complete_energy_sankeys_spec.md §4, owner decision Q-J) ----


def house(
    metered_import_watt: Sequence[float] = (2400.0, 0.0, 0.0),
    metered_export_watt: Sequence[float] = (0.0, 1200.0, 1200.0),
    extra_components: Sequence[str] = (),
    through_the_manager: bool = True,
) -> EnergyBalanceReport:
    """A heat pump and a PV system whose electricity the meter reads; three 900 s steps.

    The heat pump takes 1.0, 0.2 and 0.0 kWh of electricity and 3, 0.6 and 0 kWh of ambient heat and gives their sum
    as space heat outdoors; the PV system turns 0.4, 0.5 and 0.3 kWh of sun into as much electricity. With
    ``through_the_manager`` the energy manager reads both and gives the meter one signed sum, as in the household
    twins, and sends the heat pump a decision; else the meter reads both devices itself. Per step the devices take
    0.6 kWh net, then give 0.3 and 0.3 kWh: the meter's defaults, in W, publish exactly that as import and export.
    """
    columns: List[Column] = [
        ("HeatPump", "ElectricityIn", lt.Units.KWH, EnergyPort(IN, ELECTRICITY), [1.0, 0.2, 0.0]),
        ("HeatPump", "AmbientIn", lt.Units.KWH, EnergyPort(IN, lt.EnergyBalanceCarrier.AMBIENT_HEAT), [3.0, 0.6, 0.0]),
        (
            "HeatPump",
            "HeatOut",
            lt.Units.KWH,
            EnergyPort(OUT, SH, environment=lt.EnvironmentNode.OUTDOORS),
            [4.0, 0.8, 0.0],
        ),
        ("PV", "SunIn", lt.Units.KWH, EnergyPort(IN, SOLAR), [0.4, 0.5, 0.3]),
        ("PV", "ElectricityOut", lt.Units.KWH, EnergyPort(OUT, ELECTRICITY), [0.4, 0.5, 0.3]),
        ("EMS", "GridBalance", lt.Units.WATT, None, [-2400.0, 1200.0, 1200.0]),
        ("EMS", "Decision", lt.Units.ANY, None, [0.0, 0.0, 0.0]),
        ("Meter", "FromGridInWatt", lt.Units.WATT, None, list(metered_import_watt)),
        ("Meter", "ToGridInWatt", lt.Units.WATT, None, list(metered_export_watt)),
    ]
    device_inputs = [
        wired_input("HeatPumpElectricity", "HeatPump", "ElectricityIn"),
        wired_input("PV", "PV", "ElectricityOut"),
    ]
    inputs = (
        {
            "EMS": device_inputs,
            "Meter": [wired_input("Production", "EMS", "GridBalance")],
            "HeatPump": [wired_input("Decision", "EMS", "Decision")],
        }
        if through_the_manager
        else {"Meter": device_inputs}
    )
    return report(
        columns, inputs=inputs, extra_components=extra_components, classes={"EMS": Manager, "Meter": Meter}
    )


@pytest.mark.base
@pytest.mark.parametrize("through_the_manager", [True, False])
def test_a_device_the_meter_reads_links_to_the_meter_also_through_an_exempt_controller(
    through_the_manager: bool,
) -> None:
    """Whether the meter reads the devices or the energy manager's sum of them, both devices link to the meter.

    The energy manager also sends the heat pump a decision; what a controller hands to anything but a meter is not
    the flow, so the heat pump is never its own peer.
    """
    balance_report = house(through_the_manager=through_the_manager)
    peers = {series.name: series.peer for series in balance_report.declared.all_series()}
    assert peers["HeatPump.ElectricityIn"] == peers["PV.ElectricityOut"] == Peer(PeerKind.METER, "Meter")
    assert peers["HeatPump.AmbientIn"].name == "ambient heat" and peers["PV.SunIn"].name == "sun"
    assert not balance_report.meter_check.unmetered


@pytest.mark.base
def test_grid_import_and_export_are_what_the_meter_publishes_and_the_meter_node_closes(tmp_path: Path) -> None:
    """The meter publishes 0.6 kWh imported and 0.6 kWh exported; it is a node between the grid and the devices."""
    balance_report = house()
    overall = flows(balance_report.sankeys()["overall"])
    assert overall[("grid import", "Meter")] == pytest.approx(0.6)
    assert overall[("Meter", "grid export")] == pytest.approx(0.6)
    assert overall[("Meter", "HeatPump")] == pytest.approx(1.2)
    assert overall[("PV", "Meter")] == pytest.approx(1.2)
    assert overall[("sun", "PV")] == pytest.approx(1.2)
    assert ("Meter", "unaccounted") not in overall and ("unaccounted", "Meter") not in overall
    inflow = sum(value for (_, target), value in overall.items() if target == "Meter")
    outflow = sum(value for (source, _), value in overall.items() if source == "Meter")
    assert inflow == pytest.approx(outflow) == pytest.approx(1.8)
    kinds = {node["name"]: node["kind"] for node in balance_report.sankeys()["overall"]["nodes"]}
    assert kinds["Meter"] == "meter" and kinds["grid import"] == "boundary"

    written = json.loads(balance_report.write(str(tmp_path), with_html=False).read_text())
    assert written["schema"] == "hisim.energy_balance/2" and "buses" not in written
    (entry,) = written["meters"]
    assert entry["meter"] == "Meter" and entry["carrier"] == "electricity"
    assert entry["published_import_kwh"] == pytest.approx(0.6) and entry["published_export_kwh"] == pytest.approx(0.6)
    assert {(read["component"], read["role"]) for read in entry["reads"]} == {("HeatPump", "in"), ("PV", "out")}
    assert entry["verdict"] == "agrees" and entry["enforced"] and entry["failing_steps"] == 0
    assert entry["residual_kwh"] == pytest.approx(0.0, abs=1e-12)
    assert entry["largest_step_residual_kwh"] == pytest.approx(0.0, abs=1e-12)
    assert written["unmetered"] == {"enforced": True, "not_enforced_because": None, "ports": []}


@pytest.mark.base
def test_the_controllers_are_exempt_and_the_meter_is_a_node_never_undeclared(tmp_path: Path) -> None:
    """The energy manager is exempt and never drawn; the meter is drawn, neither exempt nor undeclared."""
    report_path = house().conclude(str(tmp_path), with_html=False)
    assert report_path is not None
    written = json.loads(report_path.read_text())
    assert written["verdict"] == "closes" and written["undeclared"] == []
    assert written["exempt"] == [
        {"component": "EMS", "class": "Manager", "reason": "an L2 controller coordinates devices, it moves no energy"}
    ]
    nodes = {node["name"] for node in json.loads((tmp_path / "energy_sankeys" / "energy_sankey.json").read_text())[
        "overall"]["nodes"]}
    assert "Meter" in nodes and not {"EMS", "undeclared: EMS", "undeclared: Meter"} & nodes


@pytest.mark.base
def test_a_meter_that_publishes_other_than_it_reads_has_an_unaccounted_flow_and_fails_the_run() -> None:
    """The meter publishes 1 kWh imported in step 0 where its devices take 0.6 kWh net: 0.4 kWh is unaccounted.

    The grid import drawn is what the meter publishes, 1 kWh, never a value derived from the devices.
    """
    balance_report = house(metered_import_watt=(4000.0, 0.0, 0.0))
    (meter,) = balance_report.meter_check.balances
    assert not meter.agrees and meter.enforced and meter.worst_step == 0
    assert meter.residual_per_step == pytest.approx([0.4, 0.0, 0.0])
    overall = flows(balance_report.sankeys()["overall"])
    assert overall[("grid import", "Meter")] == pytest.approx(1.0)
    assert overall[("Meter", "unaccounted")] == pytest.approx(0.4)
    with pytest.raises(EnergyBalanceError) as raised:
        balance_report.conclude(None)
    assert (
        "meter Meter publishes 1 kWh of electricity imported and 0.6 kWh exported, the ports it reads take 1.2 kWh "
        "and give 1.2 kWh: residual 0.4 kWh; 1 steps beyond tolerance, worst step 0 (2021-01-01T00:00:00)"
    ) in str(raised.value)


@pytest.mark.base
def test_a_meter_gap_within_the_tolerance_is_listed_but_not_drawn(tmp_path: Path) -> None:
    """0.1 Wh more import than the devices take in a step of about 1 kWh is within 0.1 %: no red link, but reported."""
    balance_report = house(metered_import_watt=(2400.4, 0.0, 0.0))
    (meter,) = balance_report.meter_check.balances
    assert meter.agrees and meter.residual == pytest.approx(1e-4)
    overall = flows(balance_report.sankeys()["overall"])
    assert ("Meter", "unaccounted") not in overall and "unaccounted" not in {node for edge in overall for node in edge}
    written = json.loads(balance_report.write(str(tmp_path), with_html=False).read_text())
    (entry,) = written["meters"]
    assert entry["residual_kwh"] == pytest.approx(1e-4) and entry["largest_step_residual_kwh"] == pytest.approx(1e-4)


@pytest.mark.base
def test_a_meter_that_publishes_other_than_it_reads_is_reported_while_a_component_is_undeclared(
    tmp_path: Path,
) -> None:
    """With the occupancy undeclared, the meter may read flows no port declares: the gap is reported, not failed."""
    balance_report = house(metered_import_watt=(4000.0, 0.0, 0.0), extra_components=["Occupancy"])
    report_path = balance_report.conclude(str(tmp_path), with_html=False)
    assert report_path is not None
    (entry,) = json.loads(report_path.read_text())["meters"]
    assert entry["verdict"] == "does_not_agree" and not entry["enforced"]
    assert entry["not_enforced_because"] == (
        "1 components are undeclared, so the meter may read flows no port declares"
    )
    assert entry["worst_step"]["index"] == 0 and entry["worst_step"]["residual_kwh"] == pytest.approx(0.4)
    assert entry["largest_step_residual_kwh"] == pytest.approx(0.4)
    assert flows(balance_report.sankeys()["overall"])[("Meter", "unaccounted")] == pytest.approx(0.4)


def boiler(metered: bool, extra_components: Sequence[str] = ()) -> EnergyBalanceReport:
    """A boiler burning 1 kWh of gas in one step, its gas port read by a gas meter when ``metered``, else by nobody."""
    columns: List[Column] = [
        ("Boiler", "GasIn", lt.Units.KWH, EnergyPort(IN, GAS), [1.0]),
        ("Boiler", "FlueLoss", lt.Units.KWH, EnergyPort(LOSS, GAS), [0.1]),
        ("Boiler", "HeatOut", lt.Units.KWH, EnergyPort(OUT, SH, environment=lt.EnvironmentNode.OUTDOORS), [0.9]),
        ("GasMeter", "GasFromGrid", lt.Units.KWH, None, [1.0]),
    ]
    inputs = {"GasMeter": [wired_input("Gas", "Boiler", "GasIn")]} if metered else {}
    return report(
        columns,
        inputs=inputs,
        extra_components=extra_components,
        classes={"GasMeter": GasMeter if metered else SilentMeter},
    )


@pytest.mark.base
def test_a_fuel_port_its_gas_meter_reads_comes_from_the_supply_through_the_meter() -> None:
    """The gas comes from natural gas supply through GasMeter to the boiler: the meter is its peer and closes."""
    balance_report = boiler(metered=True)
    overall = flows(balance_report.sankeys()["overall"])
    assert overall == {
        ("natural gas supply", "GasMeter"): pytest.approx(1.0),
        ("GasMeter", "Boiler"): pytest.approx(1.0),
        ("Boiler", "Boiler · FlueLoss"): pytest.approx(0.1),
        ("Boiler · FlueLoss", "outdoors"): pytest.approx(0.1),
        ("Boiler", "outdoors"): pytest.approx(0.9),
    }
    (meter,) = balance_report.meter_check.balances
    assert meter.agrees and meter.carrier == GAS
    assert not balance_report.meter_check.unmetered


@pytest.mark.base
def test_a_bought_carrier_port_no_meter_reads_is_unmetered_reported_then_failing(tmp_path: Path) -> None:
    """Without a meter the gas comes straight from its supply: reported while a component is undeclared, else fails.

    A meter that declares no metered exchange counts nothing the balance can check: it is exempt, never a peer.
    """
    balance_report = boiler(metered=False, extra_components=["Occupancy"])
    assert flows(balance_report.sankeys()["overall"])[("natural gas supply", "Boiler")] == pytest.approx(1.0)
    report_path = balance_report.conclude(str(tmp_path), with_html=False)
    assert report_path is not None
    written = json.loads(report_path.read_text())
    assert written["unmetered"] == {
        "enforced": False,
        "not_enforced_because": "1 components are undeclared, so the wiring is not complete yet",
        "ports": [
            {
                "component": "Boiler",
                "output": "GasIn",
                "role": "in",
                "carrier": "natural_gas",
                "peer": "natural gas supply",
                "peer_kind": "boundary",
                "total_kwh": 1.0,
            }
        ],
    }
    assert written["exempt"] == [
        {
            "component": "GasMeter",
            "class": "SilentMeter",
            "reason": "a meter that declares no metered exchange publishes nothing the balance can check",
        }
    ]

    with pytest.raises(EnergyBalanceError, match=r"port Boiler.GasIn \(in, natural_gas, 1 kWh\) passes no meter"):
        boiler(metered=False).conclude(None)


@pytest.mark.base
def test_an_electricity_port_nothing_reads_is_unlinked_and_unmetered() -> None:
    """No junction absorbs it: a pump whose electricity no meter and no component reads goes to ``unlinked``."""
    balance_report = report([("Pump", "Power", lt.Units.KWH, EnergyPort(IN, ELECTRICITY), [0.1])])
    (series,) = balance_report.declared.unlinked()
    assert series.name == "Pump.Power"
    assert [port.name for port in balance_report.meter_check.unmetered] == ["Pump.Power"]
    assert flows(balance_report.sankeys()["overall"])[("unlinked", "Pump")] == pytest.approx(0.1)


@pytest.mark.base
def test_a_meter_is_the_peer_only_of_the_carrier_it_meters() -> None:
    """A gas meter reading a pump's electricity is no peer of it: the electricity stays unlinked and unmetered."""
    balance_report = report(
        [
            ("Pump", "Power", lt.Units.KWH, EnergyPort(IN, ELECTRICITY), [0.1]),
            ("GasMeter", "GasFromGrid", lt.Units.KWH, None, [0.0]),
        ],
        inputs={"GasMeter": [wired_input("Gas", "Pump", "Power")]},
        classes={"GasMeter": GasMeter},
    )
    (series,) = balance_report.declared.all_series()
    assert series.peer == Peer.unlinked()
    assert [port.name for port in balance_report.meter_check.unmetered] == ["Pump.Power"]


@pytest.mark.base
def test_a_heat_port_whose_only_reader_is_exempt_is_unlinked_and_reported(tmp_path: Path) -> None:
    """Only a helper reads the boiler's flow: the heat has no peer; the report lists the port as unlinked."""
    columns: List[Column] = [
        ("Boiler", "AmbientIn", lt.Units.KWH, EnergyPort(IN, lt.EnergyBalanceCarrier.AMBIENT_HEAT), [1.0]),
        ("Boiler", "HeatOut", lt.Units.KWH, EnergyPort(OUT, SH, peer_output="Flow"), [1.0]),
        ("Boiler", "Flow", lt.Units.KG_PER_SEC, None, [0.1]),
    ]
    balance_report = report(
        columns,
        inputs={"Sum": [wired_input("Flow", "Boiler", "Flow")]},
        extra_components=["Sum"],
        classes={"Sum": Helper},
    )
    report_path = balance_report.conclude(str(tmp_path), with_html=False)
    assert report_path is not None
    written = json.loads(report_path.read_text())
    assert written["unlinked"] == [
        {"component": "Boiler", "output": "HeatOut", "role": "out", "carrier": "space_heating_heat", "total_kwh": 1.0}
    ]
    assert written["exempt"] == [
        {"component": "Sum", "class": "Helper", "reason": "it sums two inputs, which moves no energy"}
    ]
    kinds = {node["name"]: node["kind"] for node in balance_report.sankeys()["overall"]["nodes"]}
    assert kinds["unlinked"] == "unlinked" and kinds["ambient heat"] == "boundary"


@pytest.mark.base
@pytest.mark.parametrize(
    ("name", "kind", "refusal"), [("EMS", Manager, "it is exempt"), ("Meter", Meter, "it is a meter")]
)
def test_an_exempt_component_or_a_meter_that_declares_a_port_is_refused(name: str, kind: type, refusal: str) -> None:
    """A controller or a meter moves no energy of its own: one that declares an energy port contradicts its kind."""
    with pytest.raises(ValueError, match=f"{name} declares the energy port Power, but {refusal}"):
        report([(name, "Power", lt.Units.KWH, EnergyPort(IN, ELECTRICITY), [1.0])], classes={name: kind})


@pytest.mark.base
def test_each_carrier_is_a_filter_of_the_one_connected_graph() -> None:
    """``overall`` holds every edge; the electricity view only the meter's and the metered ports' edges."""
    diagrams = house().sankeys()
    overall = flows(diagrams["overall"])
    electricity = flows(diagrams["electricity"])
    assert set(electricity) == {
        ("grid import", "Meter"),
        ("Meter", "grid export"),
        ("Meter", "HeatPump"),
        ("PV", "Meter"),
    }
    assert set(electricity) < set(overall)
    assert set(diagrams) == {"overall", "electricity", "ambient_heat", "solar", "space_heating_heat"}
    assert {link["kind"] for link in diagrams["electricity"]["links"]} == {"meter_boundary", "metered"}


@pytest.mark.base
def test_links_are_coloured_by_carrier_and_every_carrier_has_a_colour() -> None:
    """Okabe-Ito blue for electricity, grey for a converter's residual, which has no carrier."""
    assert SankeyDiagram.link_colour("electricity") == "rgba(0,114,178,0.45)"
    assert SankeyDiagram.link_colour(None) == "rgba(153,153,153,0.45)"
    assert set(SankeyDiagram.CARRIER_COLOURS) == set(lt.EnergyBalanceCarrier)
