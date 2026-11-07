"""Direct tests of the rules after the run: each rule on a hand-built result table or balance, with its fault and without.

The balances are those of the energy-balance tests (``tests/test_energy_balance.py``, ``tests/test_energy_port_hydronic.py``):
a few outputs that declare energy ports, the columns they fill, and stand-ins for the components carrying their
wiring. The rules read them as the run's balance (``RunResults.energy_balance``); the finite-results rule reads the
table itself.
"""

import math
from typing import Any, List

import numpy as np
import pandas as pd
import pytest

from hisim import loadtypes as lt
from hisim.component import ComponentOutput
from hisim.config import ComponentID
from hisim import hydronics
from hisim.energy_port import EnergyPort, MeteredExchange
from hisim.postprocessing.energy_balance.ports import LoopColumns
from hisim.postprocessing.energy_balance import EnergyBalanceReport
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.results import (
    AllRows,
    LoopFaults,
    ColumnsReadable,
    ComponentBalanceCloses,
    ComponentDeclared,
    EnergyBalanceError,
    EnergyBalanceRule,
    Finite,
    LinkCloses,
    NonFiniteColumn,
    NonFiniteResultsError,
    NoPortOnANode,
    PortFinite,
    PortLinked,
)
from hisim.setup_rules.rule import RuleStage, SetupRuleViolation, Skip
from hisim.setup_rules.subjects import RunResults
from tests import functions_for_testing as fft
from tests.rules.test_energy_ports import ExemptController
from tests.test_energy_balance import CONSUMPTION, PRODUCTION, SH, Column, Manager, Meter, chain, report, vessel, wired_input
from tests.test_energy_port_hydronic import LoopRig

pytestmark = pytest.mark.base


class Table:
    """Hand-built result tables of one component, ``Table``, whose outputs are ``Column0``, ``Column1`` and so on."""

    @staticmethod
    def outputs(count: int, may_be_non_finite: bool = False) -> List[ComponentOutput]:
        """``count`` outputs in °C; ``may_be_non_finite`` declares them allowed to hold NaN."""
        return [
            ComponentOutput(
                "Table", f"Column{index}", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, component_id=ComponentID(name="Table"),
                may_be_non_finite=may_be_non_finite,
            )
            for index in range(count)
        ]

    @staticmethod
    def no_balance() -> EnergyBalanceReport:
        """Stands for the run's energy balance, which the finite-results rule never reads."""
        raise AssertionError("the finite-results rule reads no energy balance")

    @classmethod
    def subject(cls, values: List[List[float]], outputs: List[ComponentOutput]) -> RunResults:
        """The results of a run whose table holds ``values`` (one list per column), four 15-minute steps long at most."""
        index = pd.date_range("2021-01-01", periods=len(values[0]) if values else 0, freq="900s")
        table = pd.DataFrame({f"c{position}": column for position, column in enumerate(values)}, index=index)
        return RunResults(results=table, outputs=tuple(outputs), components=(), timesteps=len(table), read_energy_balance=cls.no_balance)


def codes_and_places(faults: Any) -> List[Any]:
    """The code, component and port of each fault."""
    return [(fault.code, fault.component, fault.port) for fault in faults]


class TestFinite:
    """``AR-01``: every result column is finite unless its output declares otherwise, one column per output."""

    def test_a_nan_column_is_a_fault_naming_it_and_a_clean_table_none(self) -> None:
        """Catches a NaN that passes into the KPIs unnoticed, and a refusal of a clean table."""
        (fault,) = Finite().check(Table.subject([[1.0, math.nan, 3.0], [1.0, 2.0, 3.0]], Table.outputs(2)))

        assert codes_and_places([fault]) == [("AR-01", "Table", "Column0")]
        assert "Table # Column0 [°C]: NaN or infinite from step 1 (2021-01-01 00:15:00), in 1 of 3 steps" in fault.message
        assert "add_output(..., may_be_non_finite=True)" in fault.message
        assert not Finite().check(Table.subject([[1.0, 2.0, 3.0]], Table.outputs(1)))

    def test_a_column_that_declares_it_may_hold_nan(self) -> None:
        """Catches a refusal of an output whose NaN is its meaning, a quantity undefined while a device stands still."""
        assert not Finite().check(Table.subject([[math.nan, math.nan]], Table.outputs(1, may_be_non_finite=True)))

    def test_a_table_that_does_not_fit_its_outputs_is_one_fault(self) -> None:
        """Catches a column read against the wrong output, which would name the wrong column."""
        (fault,) = Finite().check(Table.subject([[1.0], [2.0]], Table.outputs(1)))

        assert fault.message.startswith("The result table has 2 columns for 1 outputs.")

    def test_non_finite_columns_finds_infinities_and_clears_an_overflowing_sum(self) -> None:
        """Catches a check that only looks for NaN, or takes a finite column's overflowing sum for a fault."""
        results = pd.DataFrame({"a": [1.0, math.inf, 3.0, -math.inf], "b": [1e308, 1e308, 1e308, 1e308]})

        found = Finite.non_finite_columns(results, Table.outputs(2))

        assert [(column.output.field_name, column.first_step, column.steps) for column in found] == [("Column0", 1, 2)]

    def test_a_non_finite_column_describes_its_first_bad_step_and_count(self) -> None:
        """Catches a line that names the last bad step, or counts against the wrong total."""
        column = NonFiniteColumn(output=Table.outputs(1)[0], first_step=4, first_time="2021-01-01 01:00:00", steps=2)

        assert column.describe(96) == "Table # Column0 [°C]: NaN or infinite from step 4 (2021-01-01 01:00:00), in 2 of 96 steps"

    def test_its_faults_raise_non_finite_results_error_and_the_balance_waits(self) -> None:
        """Catches a balance read from a NaN table, and an error an ``except NonFiniteResultsError`` would miss."""
        subject = Table.subject([[math.nan]], Table.outputs(1))

        with pytest.raises(NonFiniteResultsError) as raised:
            SetupRuleRegistry.collect().run(RuleStage.AFTER_RUN, subject, SetupKind.RESIDENTIAL)

        assert [fault.code for fault in raised.value.faults] == ["AR-01"]
        assert Skip("AR-07", ("AR-01",)) in raised.value.skipped and Skip("AR-03", ("AR-07", "AR-08")) in raised.value.skipped


class TestAllRows:
    """``AR-02``: the table has a row for every step."""

    def test_a_table_short_of_a_step_is_a_fault_and_a_whole_one_none(self) -> None:
        """Catches totals of part of a run stated as the whole run, and a refusal of a whole table."""
        subject = Table.subject([[1.0, 2.0]], Table.outputs(1))
        short = RunResults(results=subject.results, outputs=subject.outputs, components=(), timesteps=3, read_energy_balance=Table.no_balance)

        (fault,) = AllRows().check(short)

        assert fault.code == "AR-02"
        assert fault.message.startswith("not all lines were generated: the result table has 2 rows for 3 steps")
        assert not AllRows().check(subject)

    def test_the_balance_waits_for_it(self) -> None:
        """Catches a balance read from a table that is not the run's whole window."""
        short = RunResults(results=pd.DataFrame(), outputs=(), components=(), timesteps=4, read_energy_balance=Table.no_balance)

        with pytest.raises(SetupRuleViolation) as raised:
            SetupRuleRegistry.collect().run(RuleStage.AFTER_RUN, short, SetupKind.RESIDENTIAL)

        assert [fault.code for fault in raised.value.faults] == ["AR-02"]
        assert Skip("AR-07", ("AR-02",)) in raised.value.skipped


class TestPortFinite:
    """``AR-03``: every declared port is finite in every step."""

    def test_a_port_that_is_nan_is_a_fault_and_a_finite_one_none(self) -> None:
        """Catches a balance read from a NaN port, and a refusal of a finite one."""
        columns: List[Column] = [
            ("Pipe", "In", lt.Units.KWH, EnergyPort(CONSUMPTION, SH), [1.0, math.nan]),
            ("Pipe", "Out", lt.Units.KWH, EnergyPort(PRODUCTION, SH), [1.0, 1.0]),
        ]

        (fault,) = PortFinite().check(fft.AfterRunRules.subject(report(columns)))

        assert codes_and_places([fault]) == [("AR-03", "Pipe", "In")]
        assert "energy port Pipe.In (consumption, space_heating_heat) is NaN or infinite at step 1" in fault.message
        assert not PortFinite().check(fft.AfterRunRules.subject(report(vessel(4000.0, 700.0, 200.0, 250.0))))

    def test_the_rules_of_the_balance_wait_for_it(self) -> None:
        """Catches a residual of NaN reported as a balance that does not close."""
        columns: List[Column] = [("Pipe", "In", lt.Units.KWH, EnergyPort(CONSUMPTION, SH), [math.nan])]

        findings = SetupRuleRegistry.collect().findings(RuleStage.AFTER_RUN, fft.AfterRunRules.subject(report(columns)), SetupKind.RESIDENTIAL)

        assert [fault.code for fault in findings.faults] == ["AR-03"]
        assert {skip.code for skip in findings.skipped} == {"AR-04", "AR-05", "AR-06", "AR-09", "AR-10", "MT-01", "MT-02"}
        assert findings.violation_type is EnergyBalanceError


class TestComponentBalanceCloses:
    """``AR-04``: every declared component closes its balance."""

    def test_a_leaking_component_is_a_fault_and_a_closing_one_none(self) -> None:
        """Catches energy that vanishes in a component, and a refusal of one that conserves it."""
        (fault,) = ComponentBalanceCloses().check(fft.AfterRunRules.subject(report(vessel(4000.0, 300.0, 200.0, 150.0))))

        assert codes_and_places([fault]) == [("AR-04", "Tank", None)]
        assert "component Tank (SimpleNamespace): residual 0.5 kWh (50 % of 1 kWh throughput)" in fault.message
        assert "(tolerance: 0.1 % of the throughput over the run" in fault.message
        assert not ComponentBalanceCloses().check(fft.AfterRunRules.subject(report(vessel(4000.0, 700.0, 200.0, 250.0))))

    def test_describe_names_the_worst_step_or_none(self) -> None:
        """Catches a sentence that loses the worst step of a failing balance, or invents one for a balance without it."""
        (leaking,) = report(vessel(4000.0, 300.0, 200.0, 150.0)).balances
        (closing,) = report(vessel(4000.0, 700.0, 200.0, 250.0)).balances

        assert ComponentBalanceCloses.describe(leaking, "2021-01-01T00:00:00").endswith(
            "1 steps beyond tolerance, worst step 0 (2021-01-01T00:00:00): residual 0.5 kWh of 1 kWh throughput"
        )
        assert ComponentBalanceCloses.describe(closing, None).endswith("0 steps beyond tolerance, worst no step")


class TestLinkCloses:
    """``AR-05``: every transfer between declared components is booked by both, alike."""

    def test_a_link_whose_sides_disagree_is_a_fault_and_one_that_agrees_none(self) -> None:
        """Catches heat that appears or vanishes on a wire, and a refusal of a wire whose two ends agree."""
        (fault,) = LinkCloses().check(fft.AfterRunRules.subject(chain(received_watt_hour=600.0)))

        assert codes_and_places([fault]) == [("AR-05", "Tank", None)]
        assert "link Tank -> Loop (space_heating_heat): Tank sends 0.7 kWh, Loop receives 0.6 kWh" in fault.message
        assert not LinkCloses().check(fft.AfterRunRules.subject(chain()))


class TestLoopFaults:
    """``AR-06``: the loop rules hold after the run, and no open end pumps water."""

    @staticmethod
    def open_boiler(flow_in_kg_per_second: float) -> EnergyBalanceReport:
        """A boiler supplying a loop whose return input is not connected, pumping ``flow_in_kg_per_second``."""
        return LoopRig.report(
            LoopRig.boiler_columns(flow_in_kg_per_second), {"Boiler": [LoopRig.wired("Return", None, None)]}, {"Boiler": [LoopRig.SUPPLY_END]}
        )

    def test_an_open_end_that_pumps_is_a_fault_and_a_paired_loop_none(self) -> None:
        """Catches pumped water whose heat goes nowhere the balance sees, and a refusal of a paired loop or an idle end."""
        (fault,) = LoopFaults().check(fft.AfterRunRules.subject(self.open_boiler(0.1)))

        assert codes_and_places([fault]) == [("AR-06", "Boiler", "Flow")]
        assert fault.message.startswith("loop: Boiler pumps water through its loop 'space_heating'")
        assert not LoopFaults().check(fft.AfterRunRules.subject(self.open_boiler(0.0)))
        assert not LoopFaults().check(fft.AfterRunRules.subject(LoopRig.paired()))

    def test_an_end_on_an_exempt_component_is_a_fault_after_the_run_too(self) -> None:
        """Catches a table read without the build's ``EP-03`` refused by a bare ``ValueError`` instead of a fault."""
        columns = LoopRig.boiler_columns() + [("Tank", "Return", lt.Units.CELSIUS, None, 50.0)]
        outputs = LoopRig.outputs_of(columns)
        boiler, tank = LoopRig.stand_ins(
            outputs, {"Boiler": [LoopRig.wired("Return", "Tank", "Return")], "Tank": LoopRig.tank_inputs()},
            {"Boiler": [LoopRig.SUPPLY_END], "Tank": [LoopRig.RECEIVING_END]},
        )
        table = pd.DataFrame([[value for *_, value in columns]], index=pd.date_range("2021-01-01", periods=1, freq="900s"))
        balance = EnergyBalanceReport(table, outputs, [boiler, ExemptController(tank)], LoopRig.SECONDS_PER_TIMESTEP)

        faults = LoopFaults().check(fft.AfterRunRules.subject(balance))

        assert ("AR-06", "Tank", None) in codes_and_places(faults)
        assert any("Tank declares the fluid loop 'space_heating', but it is exempt" in fault.message for fault in faults)


class TestColumnsReadable:
    """``AR-07``: every column a port, a loop or a meter names can be read, every loop's flow and step too."""

    def test_a_negative_flow_is_a_fault_and_the_loop_left_out(self) -> None:
        """Catches a negative flow refused by a bare error that names no loop, or booked as heat flowing back."""
        columns = LoopRig.boiler_columns(-0.1) + LoopRig.tank_columns()
        balance = LoopRig.report(
            columns, {"Boiler": [LoopRig.wired("Return", "Tank", "Return")], "Tank": LoopRig.tank_inputs()},
            {"Boiler": [LoopRig.SUPPLY_END], "Tank": [LoopRig.RECEIVING_END]},
        )

        (fault,) = ColumnsReadable().check(fft.AfterRunRules.subject(balance))

        assert codes_and_places([fault]) == [("AR-07", "Boiler", "Flow")]
        assert "the loop Boiler.Flow of Boiler: mass_flow_kg_per_s must be >= 0 kg/s, got -0.1 in step 0." in fault.message
        assert all(series.loop_columns is None for series in balance.declared.all_series())
        assert not ColumnsReadable().check(fft.AfterRunRules.subject(LoopRig.paired()))

    def test_a_meter_naming_an_output_it_does_not_have_is_a_fault(self) -> None:
        """Catches a meter reading taken from a column that is not there, which raised a bare error before."""

        class Misnamed(Meter):
            """A meter whose exchange names an import output it does not publish."""

            def metered_exchanges(self) -> Any:
                """The grid import, on an output the meter does not have."""
                return (MeteredExchange(lt.EnergyBalanceCarrier.ELECTRICITY, "NoSuchOutput"),)

        balance = report([("Meter", "FromGridInWatt", lt.Units.WATT, None, [0.0])], classes={"Meter": Misnamed})

        (fault,) = ColumnsReadable().check(fft.AfterRunRules.subject(balance))

        assert codes_and_places([fault]) == [("AR-07", "Meter", "NoSuchOutput")]
        assert fault.message.startswith("Meter declares that it meters electricity on its output NoSuchOutput, which it does not have.")
        assert not balance.meter_check.balances

    def test_refusal_names_a_negative_flow_or_a_step_that_is_not_positive(self) -> None:
        """Catches the balance and the hydronic arithmetic disagreeing on what a loop's heat cannot be derived for."""
        negative = LoopColumns.refusal(np.array([0.1, -0.1]), 900.0)
        empty_step = LoopColumns.refusal(np.array([0.1]), 0.0)

        assert isinstance(negative, hydronics.NegativeMassFlowError) and "got -0.1 in step 1" in str(negative)
        assert isinstance(empty_step, hydronics.NonPositiveTimestepError)
        assert LoopColumns.refusal(np.array([0.0, 0.1]), 900.0) is None


class TestNoPortOnANode:
    """``AR-08``: neither an exempt component nor a meter declares an energy port."""

    def test_a_port_on_a_controller_is_a_fault_and_one_on_a_device_none(self) -> None:
        """Catches a controller's port booked on a node that moves no energy, and a refusal of a device's port."""
        port = EnergyPort(CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY)
        balance = report(
            [("Tank", "HeatOut", lt.Units.KWH, None, [1.0])],
            inputs={"EMS": [wired_input("Power", "Tank", "HeatOut", port)]}, extra_components=["EMS"], classes={"EMS": Manager},
        )

        (fault,) = NoPortOnANode().check(fft.AfterRunRules.subject(balance))

        assert codes_and_places([fault]) == [("AR-08", "EMS", "Power")]
        assert "EMS declares the energy port Power, but it is exempt from the energy balance" in fault.message
        assert not NoPortOnANode().check(fft.AfterRunRules.subject(report(vessel(4000.0, 700.0, 200.0, 250.0))))

    def test_the_balance_rules_wait_for_it(self) -> None:
        """Catches a balance judged although it left a port out."""
        balance = report([("EMS", "Power", lt.Units.KWH, EnergyPort(CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY), [1.0])], classes={"EMS": Manager})

        with pytest.raises(EnergyBalanceError) as raised:
            fft.AfterRunRules.refuse(balance)

        assert [fault.code for fault in raised.value.faults] == ["AR-08"]
        assert Skip("AR-03", ("AR-08",)) in raised.value.skipped


class TestPortLinked:
    """``AR-09``: a port that moves energy has a peer; it only logs."""

    def test_an_unread_port_is_a_fault_and_one_with_a_boundary_none(self) -> None:
        """Catches energy drawn to nowhere, and a fault for a port its carrier's boundary node takes."""
        unread = EnergyPort(CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY)
        (fault,) = PortLinked().check(fft.AfterRunRules.subject(report([("Pump", "Power", lt.Units.KWH, unread, [0.1])])))
        outdoors = EnergyPort(PRODUCTION, SH, environment=lt.EnvironmentNode.HEAT_LOSS_TO_OUTDOORS)

        assert codes_and_places([fault]) == [("AR-09", "Pump", "Power")]
        assert "the energy port Pump.Power (consumption, electricity, 0.1 kWh) has no peer" in fault.message
        assert not PortLinked().check(fft.AfterRunRules.subject(report([("Heater", "Heat", lt.Units.KWH, outdoors, [0.1])])))
        assert not PortLinked.ENFORCED

    def test_its_faults_are_logged_and_the_run_passes(self) -> None:
        """Catches the log-only rule refusing a run, which the balance report has always only listed."""
        balance = report([("Heater", "Heat", lt.Units.KWH, EnergyPort(PRODUCTION, SH), [0.1]), ("Heater", "Gas", lt.Units.KWH, EnergyPort(CONSUMPTION, SH), [0.1])])

        findings = SetupRuleRegistry.collect().run(RuleStage.AFTER_RUN, fft.AfterRunRules.subject(balance), SetupKind.RESIDENTIAL)

        assert not findings.faults and "AR-09" in {fault.code for fault in findings.logged}


class TestComponentDeclared:
    """``AR-10``: a component declares ports, is exempt or is a meter; it only logs."""

    def test_a_component_without_ports_is_a_fault_and_a_declared_one_none(self) -> None:
        """Catches an undeclared component left out of the log, and a fault for a component that declares its ports."""
        columns = vessel(4000.0, 700.0, 200.0, 250.0)

        (fault,) = ComponentDeclared().check(fft.AfterRunRules.subject(report(columns, extra_components=["Weather"])))

        assert codes_and_places([fault]) == [("AR-10", "Weather", None)]
        assert "Weather (SimpleNamespace) declares no energy port and states no reason to be exempt" in fault.message
        assert not ComponentDeclared().check(fft.AfterRunRules.subject(report(columns)))
        assert not ComponentDeclared.ENFORCED


class TestTheFamily:
    """What the rules of the balance share: the error they raise and the one balance they read."""

    def test_the_balance_rules_raise_energy_balance_error(self) -> None:
        """Catches an ``except EnergyBalanceError`` (the assembly harness has one) that a balance fault would slip past."""
        rules = (ColumnsReadable, NoPortOnANode, PortFinite, ComponentBalanceCloses, LinkCloses, LoopFaults, PortLinked, ComponentDeclared)
        assert all(rule.VIOLATION is EnergyBalanceError for rule in rules)
        assert issubclass(EnergyBalanceError, ValueError) and EnergyBalanceRule.VIOLATION is EnergyBalanceError

    def test_the_subject_reads_the_balance_once(self) -> None:
        """Catches a balance computed again by every rule that reads it, which costs a year's table each time."""
        reads: List[int] = []
        balance = report(vessel(4000.0, 700.0, 200.0, 250.0))

        def read() -> EnergyBalanceReport:
            """Counts the reads."""
            reads.append(1)
            return balance

        subject = RunResults(results=pd.DataFrame(), outputs=(), components=(), timesteps=0, read_energy_balance=read)
        SetupRuleRegistry.collect().run(RuleStage.AFTER_RUN, subject, SetupKind.RESIDENTIAL)

        assert subject.energy_balance is balance and len(reads) == 1

    def test_a_finite_table_and_a_closing_balance_pass_every_rule(self) -> None:
        """Catches a rule that refuses a run whose results and balance hold."""
        balance = report(vessel(4000.0, 700.0, 200.0, 250.0))
        subject = RunResults(
            results=pd.DataFrame(np.ones((1, 1)), index=pd.date_range("2021-01-01", periods=1, freq="900s")),
            outputs=tuple(Table.outputs(1)), components=(), timesteps=1, read_energy_balance=lambda: balance,
        )

        findings = SetupRuleRegistry.collect().run(RuleStage.AFTER_RUN, subject, SetupKind.RESIDENTIAL)

        assert not findings.faults and not findings.skipped
