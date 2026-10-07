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
    EnergyBalanceError,
    EnergyBalanceReport,
)
from hisim.postprocessing.energy_balance.check import RoleTotals
from hisim.postprocessing.postprocessing_main import PostProcessor
from hisim.postprocessingoptions import PostProcessingOptions

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
    received. The loop loses 0.05 kWh it does not declare, so it carries a residual of 0.05 kWh. One 900 s step.
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
def test_the_sankey_draws_each_transfer_once_and_closes_every_declared_node() -> None:
    """Links follow the wiring; the receiver's figure is drawn, the difference goes to unaccounted."""
    diagrams = chain().sankeys()
    heat = flows(diagrams["space_heating_heat"])

    assert heat == {
        ("undeclared: Boiler", "Tank"): pytest.approx(1.0),
        ("Tank", "outdoors"): pytest.approx(0.05),
        ("Tank", "Tank (store)"): pytest.approx(0.25),
        ("Tank", "Loop"): pytest.approx(0.6996),
        ("Loop", "undeclared: Building"): pytest.approx(0.6496),
        ("Tank", "unaccounted"): pytest.approx(0.0004),
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
    chain().write(str(tmp_path))

    written = json.loads((tmp_path / "energy_sankeys" / "energy_sankey.json").read_text())
    assert set(written) == {"space_heating_heat", "electricity", "overall"}
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
