"""The energy-balance check and the Sankey data, on synthetic components (hisim-9uoo.1, .2, .9).

Each test builds a result table by hand: a few outputs that declare energy ports, the columns they fill, and
stand-ins for the wrapped components carrying their wiring. The numbers are chosen so the expected residual,
throughput and verdict can be read off the test.
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
from hisim.energy_port import EnergyPort
from hisim.postprocessing.energy_balance import (
    MODE_VARIABLE,
    BalanceMode,
    EnergyBalanceError,
    EnergyBalanceReport,
)

IN, OUT, LOSS, STORED = lt.EnergyRole.IN, lt.EnergyRole.OUT, lt.EnergyRole.LOSS, lt.EnergyRole.STORED_CHANGE
SH = lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT
ELECTRICITY = lt.EnergyBalanceCarrier.ELECTRICITY

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


def report(
    columns: Sequence[Column],
    seconds_per_timestep: int = 900,
    inputs: Optional[Dict[str, List[SimpleNamespace]]] = None,
    extra_components: Sequence[str] = (),
) -> EnergyBalanceReport:
    """The balance report of a run whose result table has ``columns``."""
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
            my_component=SimpleNamespace(
                component_name=name,
                inputs=(inputs or {}).get(name, []),
                outputs=[o for o in outputs if o.component_name == name],
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
    """``environment`` with ``peer_input`` is refused; a loss defaults to outdoors, ambient heat to its node."""
    with pytest.raises(ValueError, match="either an environment node"):
        EnergyPort(LOSS, SH, peer_input="X", environment=lt.EnvironmentNode.OUTDOORS)
    assert EnergyPort(LOSS, SH).default_environment() == lt.EnvironmentNode.OUTDOORS
    assert EnergyPort(IN, lt.EnergyBalanceCarrier.AMBIENT_HEAT).default_environment() == (
        lt.EnvironmentNode.AMBIENT_HEAT
    )
    assert EnergyPort(IN, SH, peer_input="X").default_environment() is None


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

    written = json.loads(balance_report.write(str(tmp_path), BalanceMode.REPORT, with_html=False).read_text())
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
def test_strict_mode_fails_the_run_and_report_mode_only_warns(tmp_path: Path, monkeypatch: Any) -> None:
    """The report is written either way; strict raises, an unknown mode is refused."""
    leaking = report(vessel(4000.0, 300.0, 200.0, 150.0))
    monkeypatch.setenv(MODE_VARIABLE, "strict")
    with pytest.raises(EnergyBalanceError, match="Tank"):
        leaking.write(str(tmp_path), with_html=False)
    assert json.loads((tmp_path / "balance_report.json").read_text())["mode"] == "strict"

    report(vessel(4000.0, 700.0, 200.0, 250.0)).write(str(tmp_path), with_html=False)  # a closed balance passes

    monkeypatch.setenv(MODE_VARIABLE, "report")
    leaking.write(str(tmp_path), with_html=False)
    monkeypatch.setenv(MODE_VARIABLE, "lenient")
    with pytest.raises(ValueError, match="HISIM_ENERGY_BALANCE"):
        leaking.write(str(tmp_path), with_html=False)


@pytest.mark.base
def test_a_component_without_ports_is_undeclared_never_failing(tmp_path: Path, monkeypatch: Any) -> None:
    """A component with no declared port is listed as undeclared; strict mode lets the run pass."""
    monkeypatch.setenv(MODE_VARIABLE, "strict")
    balance_report = report(
        [("Tank", "Temperature", lt.Units.CELSIUS, None, [40.0]), *vessel(4000.0, 700.0, 200.0, 250.0)],
        extra_components=["Weather"],
    )
    written = json.loads(balance_report.write(str(tmp_path), with_html=False).read_text())

    assert written["undeclared"] == [{"component": "Weather", "class": "SimpleNamespace"}]
    assert [entry["component"] for entry in written["components"]] == ["Tank"]


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


def chain() -> EnergyBalanceReport:
    """A boiler (undeclared) feeds a tank, the tank a radiator loop, the loop the building (undeclared).

    The tank sends 0.7 kWh, the loop books 0.6 kWh of it: 0.1 kWh is sent but not received. The loop loses 0.05
    kWh it does not declare, so it carries a residual of 0.05 kWh. One 900 s step.
    """
    columns: List[Column] = [
        ("Tank", "HeatIn", lt.Units.WATT, EnergyPort(IN, SH, peer_input="FlowFromBoiler"), [4000.0]),
        ("Tank", "HeatOut", lt.Units.WATT_HOUR, EnergyPort(OUT, SH), [700.0]),
        ("Tank", "Loss", lt.Units.WATT, EnergyPort(LOSS, SH), [200.0]),
        ("Tank", "Stored", lt.Units.WATT_HOUR, EnergyPort(STORED, SH), [250.0]),
        ("Loop", "Received", lt.Units.WATT_HOUR, EnergyPort(IN, SH, peer_input="HeatFromTank"), [600.0]),
        ("Loop", "Delivered", lt.Units.WATT_HOUR, EnergyPort(OUT, SH), [550.0]),
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
def test_the_sankey_draws_each_transfer_once_and_closes_every_declared_node() -> None:
    """Links follow the wiring; the receiver's figure is drawn, the difference goes to unaccounted."""
    diagrams = chain().sankeys()
    heat = flows(diagrams["space_heating_heat"])

    assert heat == {
        ("undeclared: Boiler", "Tank"): pytest.approx(1.0),
        ("Tank", "outdoors"): pytest.approx(0.05),
        ("Tank", "Tank (store)"): pytest.approx(0.25),
        ("Tank", "Loop"): pytest.approx(0.6),
        ("Loop", "undeclared: Building"): pytest.approx(0.55),
        ("Tank", "unaccounted"): pytest.approx(0.1),
        ("Loop", "unaccounted"): pytest.approx(0.05),
    }
    kinds = {node["name"]: node["kind"] for node in diagrams["overall"]["nodes"]}
    assert kinds["undeclared: Boiler"] == "undeclared" and kinds["unaccounted"] == "unaccounted"
    assert kinds["outdoors"] == "environment" and kinds["Tank (store)"] == "store"

    overall = flows(diagrams["overall"])
    for node in ("Tank", "Loop", "Pump"):
        inflow = sum(value for (_, target), value in overall.items() if target == node)
        outflow = sum(value for (source, _), value in overall.items() if source == node)
        assert inflow == pytest.approx(outflow), node


@pytest.mark.base
def test_the_sankey_files_are_written_into_the_result_directory(tmp_path: Path) -> None:
    """The JSON and one HTML per carrier plus overall, sharing one plotly.min.js."""
    chain().write(str(tmp_path), BalanceMode.REPORT)

    written = json.loads((tmp_path / "energy_sankeys" / "energy_sankey.json").read_text())
    assert set(written) == {"space_heating_heat", "electricity", "overall"}
    for name in written:
        assert (tmp_path / "energy_sankeys" / f"{name}.html").stat().st_size > 0
    assert (tmp_path / "energy_sankeys" / "plotly.min.js").exists()
