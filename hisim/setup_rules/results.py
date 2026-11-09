"""The rules after the run: every result is finite, and the run's declared energy flows close.

The rules read the results of a run (``RunResults``): the result table, its outputs, and the energy balance the
post-processing computes from them (:class:`~hisim.postprocessing.energy_balance.EnergyBalanceReport`: the declared
ports in kWh per step, each component's balance, each link between declared components, the loops and the
meters). The balance computes; these rules judge what it computed, and the balance report and the Sankeys stay in
``hisim/postprocessing/energy_balance/``.

``AR-01`` checks every result column for NaN and infinity and ``AR-02`` that the table has a row for every step,
before anything reads the table; ``AR-01``'s faults raise ``NonFiniteResultsError``. The rules of the balance raise
``EnergyBalanceError``: ``AR-07`` (every column a port, a loop or a meter names can be read) and ``AR-08`` (no port
on an exempt component or a meter) require the two rules of the table, and ``AR-03`` (the declared ports are finite)
requires those two, since a balance that left out what it could not read is not the run's. ``AR-04`` to ``AR-06``
and the meter rules of ``hisim/setup_rules/meters.py`` require ``AR-03``: a balance read from NaN reports nothing
useful. ``AR-11`` (no supplier of a loop that may not cool removes heat from its water) requires ``AR-03`` too.
``AR-09`` (an unlinked port) refuses every run but a demo's (the acceptance of the complete Sankeys); ``AR-10``
(an undeclared component) only logs, as the balance report has always listed it without failing the run, until the
heat distribution system without a buffer declares its ports (bead hisim-sankey-enforcement-4f03). The codes follow
the rows of the inventory. No rule raises: the balance
records what it cannot read, and the rules report it.

The balance is imported where it is read, by the subject's ``read_energy_balance``, so that collecting the rules
imports no module of the post-processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, List, Sequence

import numpy as np
import pandas as pd

from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.rule import Fault, RuleStage, SetupRule, SetupRuleViolation
from hisim.setup_rules.subjects import RunResults

if TYPE_CHECKING:
    from hisim.component import ComponentOutput
    from hisim.postprocessing.energy_balance.check import ComponentBalance


class NonFiniteResultsError(SetupRuleViolation):
    """A run whose result table holds NaN or infinite values in a column that does not allow them.

    The registry of the setup rules raises it when the finite-results rule ``AR-01`` alone found faults: one fault per
    such column, naming its component, output and unit, its first bad step (index and time) and the number of bad
    steps. It is a ``ValueError``, as it was before the check moved here.
    """


class EnergyBalanceError(SetupRuleViolation):
    """A run whose declared energy flows do not hold: a port that is not finite, a balance, a link or a meter that does not close.

    The registry of the setup rules raises it when every refusing fault after the run comes from the rules of the
    energy balance (``hisim/setup_rules/results.py`` and ``hisim/setup_rules/meters.py``), each fault naming the
    component, the link, the loop or the meter. It is a ``ValueError``, as it was before the checks moved here.
    """


@dataclass(frozen=True)
class NonFiniteColumn:
    """One result column that holds NaN or infinite values: its output, its first bad step and the bad steps' count.

    For example, the column of ``DHWStorage # WaterMeanTemperature`` with ``first_step=1234`` and ``steps=3`` is NaN
    or infinite in three steps, the first of them step 1234.
    """

    #: The output whose column it is.
    output: ComponentOutput
    #: The index of the first step whose value is NaN or infinite.
    first_step: int
    #: The time of that step, as the result table's index names it.
    first_time: str
    #: The number of steps whose value is NaN or infinite.
    steps: int

    def describe(self, total_steps: int) -> str:
        """Return one line that names the column, its first bad step and the count of bad steps.

        For example, "DHWStorage # WaterMeanTemperature [°C]: NaN or infinite from step 1234 (2021-01-13 20:30:00),
        in 3 of 35040 steps".

        Args:
            total_steps: The number of steps of the run.

        Returns:
            The line, without a trailing newline.
        """
        return (
            f"{self.output.component_name} # {self.output.field_name} [{self.output.unit.value}]: NaN or infinite "
            f"from step {self.first_step} ({self.first_time}), in {self.steps} of {total_steps} steps"
        )


class Finite(SetupRule[RunResults]):
    """What it checks: every result column holds only finite values, unless its output declares ``may_be_non_finite``, and the table has one column per output.

    The components of a run do not check on every pass that the values they compute are finite: a NaN or an
    infinity passes through their arithmetic into the outputs that publish it. The rule reads the result table once,
    after the last step, with one vectorized pass over its values plus one per suspect column.

    Why: every later reader of the table, the energy balance, the KPIs and the charts, would compute from the NaN
    without saying so. A check on every call of the hydronic arithmetic cost about a tenth of a run's time; the
    column, its first bad step and the count of bad steps are enough to start looking for the cause.

    Example of a fault: ``the results of this run hold NaN or infinite values: DHWStorage # WaterMeanTemperature
    [°C]: NaN or infinite from step 1234 (2021-01-13 20:30:00), in 3 of 35040 steps``.

    How to fix: find what the component computes at that step (a division by a zero mass flow, a temperature of an
    empty tank), and clamp or guard it in the component. An output that may hold such values by its meaning declares
    it with ``add_output(..., may_be_non_finite=True)``.
    """

    CODE = "AR-01"
    NAME = "finite"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    VIOLATION = NonFiniteResultsError

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per column that holds NaN or infinite values, or one fault for a table that does not fit its outputs."""
        if subject.results.shape[1] != len(subject.outputs):
            return [self.fault(f"The result table has {subject.results.shape[1]} columns for {len(subject.outputs)} outputs.")]
        steps = len(subject.results)
        return [
            self.fault(
                f"the results of this run hold NaN or infinite values: {column.describe(steps)}. An output that may "
                "hold such values declares it with add_output(..., may_be_non_finite=True)",
                component=column.output.component_name,
                port=column.output.field_name,
            )
            for column in self.non_finite_columns(subject.results, subject.outputs)
        ]

    @staticmethod
    def non_finite_columns(results: pd.DataFrame, all_outputs: Sequence[ComponentOutput]) -> List[NonFiniteColumn]:
        """Return every column that holds a NaN or an infinity, in the order of the outputs.

        A column whose output declares ``may_be_non_finite`` is skipped. The columns are found in two stages: the sum
        of a column is NaN or infinite whenever one of its values is, so one vectorized sum over the table finds the
        suspects; each suspect column is then read value by value with ``np.isfinite``, which also clears a column
        whose finite values only sum beyond the float range. Neither stage copies the table when all its columns are
        floats, which is how the simulator builds it. For example, a column ``[1, inf, 3, -inf]`` gives its first
        bad step 1 and two bad steps.

        Args:
            results: The result table, one column per output and one row per step, as many columns as outputs.
            all_outputs: The run's outputs, in the order of the table's columns.

        Returns:
            The columns that hold NaN or infinite values, each with its first bad step and the count of bad steps.
        """
        values = results.to_numpy(dtype=float)
        with np.errstate(over="ignore", invalid="ignore"):
            suspects = np.flatnonzero(~np.isfinite(values.sum(axis=0)))
        found: List[NonFiniteColumn] = []
        for column in suspects.tolist():
            output = all_outputs[column]
            if output.may_be_non_finite:
                continue
            bad = ~np.isfinite(values[:, column])
            if not bad.any():
                continue
            first_step = int(np.argmax(bad))
            found.append(NonFiniteColumn(output=output, first_step=first_step, first_time=str(results.index[first_step]), steps=int(bad.sum())))
        return found


class AllRows(SetupRule[RunResults]):
    """What it checks: the result table has one row for every step the simulation parameters ask for.

    The simulator appends one row per step it finished; the post-processing reads the table as the whole window.

    Why: a table missing steps would make every total, every KPI and every balance a total of part of the run,
    stated as the whole of it, and the time index would no longer name the steps it labels.

    Example of a fault: ``not all lines were generated: the result table has 95 rows for 96 steps``.

    How to fix: the simulator's loop stopped early or skipped a step; find the step it left out in the log. A setup
    cannot cause it by its components alone.
    """

    CODE = "AR-02"
    NAME = "all-rows"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault when the table's row count is not the number of steps."""
        rows = len(subject.results)
        if rows == subject.timesteps:
            return []
        return [self.fault(f"not all lines were generated: the result table has {rows} rows for {subject.timesteps} steps")]


class EnergyBalanceRule(SetupRule[RunResults]):
    """The shared base of the rules of the energy balance after the run: the subject they read and the error they raise.

    It declares no ``CODE``, so it is no rule itself. Its rules read the run's energy balance
    (:attr:`~hisim.setup_rules.subjects.RunResults.energy_balance`) and raise ``EnergyBalanceError``; the meter rules
    of ``hisim/setup_rules/meters.py`` derive from it as well.
    """

    VIOLATION = EnergyBalanceError


class PortFinite(EnergyBalanceRule):
    """What it checks: every declared energy port of the run is finite in every step.

    A port's series is its column in kWh per step; an input port's is the column of the output it reads, and a
    loop end's is the heat derived from the loop's mass flow and its two temperatures. A column whose output
    declares ``may_be_non_finite`` passes the finite-results rule ``AR-01``, but not this one when it is a port.

    Why: no balance, link or meter can be read from a NaN; every residual would be NaN and every comparison false.

    Example of a fault: ``the declared energy flows of this run are not finite: energy port Pipe.In (consumption,
    space_heating_heat) is NaN or infinite at step 1 (2021-01-01T00:15:00), in 2 steps in all``.

    How to fix: find what the component publishes at that step and guard it in the component; an energy port never
    declares ``may_be_non_finite``.
    """

    CODE = "AR-03"
    NAME = "port-finite"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    # A balance that left out what it could not read is not the run's; those two rules wait for the rules of the
    # table in turn, so a NaN column is reported by its column first.
    REQUIRES = frozenset({"AR-07", "AR-08"})

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per declared port that is NaN or infinite in some step, naming its first such step."""
        balance = subject.energy_balance
        return [
            self.fault(
                f"the declared energy flows of this run are not finite: energy port {port.name} "
                f"({port.series.port.role.value}, {port.series.port.carrier.value}) is NaN or infinite at step "
                f"{port.first_step} ({balance.step_time(port.first_step)}), in {port.steps} steps in all",
                component=port.series.component,
                port=port.series.channel,
            )
            for port in balance.nonfinite
        ]


class ComponentBalanceCloses(EnergyBalanceRule):
    """What it checks: every component that declares energy ports closes its balance, over the run and in every step.

    Per component and step, consumption minus production, loss and stored change over all its declared ports is the
    residual. The run's residual is at most 0.1 % of the component's throughput over the run, and every step's at most
    the larger of 1e-6 kWh and 0.1 % of the step's throughput (owner decision 6 of hisim-energy-sankeys-9uoo,
    :class:`~hisim.postprocessing.energy_balance.check.Tolerance`). The balance is the component's, not a carrier's:
    a converter turns one carrier into another, so only its total is conserved.

    Why: energy is conserved. A component that publishes more than it takes, or loses energy no port declares, makes
    every Sankey, every KPI built on its flows and every balance around it wrong.

    Example of a fault: ``component Tank (SimpleDHWStorage): residual 0.5 kWh (50 % of 1 kWh throughput); per carrier
    space_heating_heat 0.5 kWh; 1 steps beyond tolerance, worst step 0 (2021-01-01T00:00:00): residual 0.5 kWh of 1
    kWh throughput``.

    How to fix: declare the port the energy leaves or enters by (a loss, a stored change, a second carrier), or
    correct the component's arithmetic so that its published flows conserve energy in every step.
    """

    CODE = "AR-04"
    NAME = "component-balance-closes"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    # A balance read from a port that is NaN is NaN itself.
    REQUIRES = frozenset({"AR-03"})

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per declared component whose balance does not close, with its residual and worst step."""
        balance = subject.energy_balance
        return [
            self.fault(
                self.describe(component, balance.step_time(component.worst_step)) + f" (tolerance: {balance.tolerance.describe()})",
                component=component.component,
            )
            for component in balance.failing
        ]

    @staticmethod
    def describe(component: ComponentBalance, worst_time: object) -> str:
        """Return the sentence of one component whose balance does not close: its residual, per carrier, and its worst step.

        Example: ``component Tank (SimpleDHWStorage): residual 0.5 kWh (50 % of 1 kWh throughput); per carrier
        space_heating_heat 0.5 kWh; 1 steps beyond tolerance, worst step 0 (2021-01-01T00:00:00): residual 0.5 kWh of
        1 kWh throughput``; a balance that misses only the run's tolerance has ``worst no step`` when it has no step.

        Args:
            component: The component's balance.
            worst_time: The time of its worst step, as the result table names it.

        Returns:
            The sentence.
        """
        carriers = ", ".join(f"{carrier} {totals.residual_in_kilowatt_hour:.6g} kWh" for carrier, totals in component.carriers.items())
        worst = component.worst_step
        worst_text = (
            "no step"
            if worst is None
            else f"step {worst} ({worst_time}): residual {component.residual_per_step[worst]:.6g} kWh "
            f"of {component.throughput_per_step[worst]:.6g} kWh throughput"
        )
        return (
            f"component {component.component} ({component.class_name}): residual {component.residual:.6g} kWh "
            f"({component.residual_percent:.4g} % of {component.throughput:.6g} kWh throughput); per carrier "
            f"{carriers}; {int(component.step_failures.sum())} steps beyond tolerance, worst {worst_text}"
        )


class LinkCloses(EnergyBalanceRule):
    """What it checks: every transfer between two components that declare ports is booked by both, and the two totals agree.

    A sender's ``PRODUCTION`` port whose peer is the receiver pairs with the receiver's ``CONSUMPTION`` port of the
    same carrier whose peer is the sender; the two totals over the run agree within 0.1 % of the larger. A transfer
    only one of the two books fails; one toward a component that declares no ports, a meter, a boundary node or
    nobody is no link and does not.

    Why: the two ends of one wire book one transfer. When they disagree, energy appears or vanishes on the wire, and
    the Sankey draws the difference as unaccounted.

    Example of a fault: ``link Tank -> Loop (space_heating_heat): Tank sends 0.7 kWh, Loop receives 0.6 kWh (14.29 % of
    the larger)``; ``link Tank -> Loop (space_heating_heat): Loop books 0.7 kWh of space_heating_heat from Tank, but
    Tank declares nothing sent to Loop``.

    How to fix: declare the missing port on the side that books nothing, or correct the side whose value differs, for
    example an input port that converts its column with the wrong unit.
    """

    CODE = "AR-05"
    NAME = "link-closes"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    # A link's totals read from a port that is NaN are NaN.
    REQUIRES = frozenset({"AR-03"})

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per link whose two sides disagree, or that only one side books."""
        return [
            self.fault(f"link {link.sender} -> {link.receiver} ({link.carrier}): {link.reason}", component=link.sender)
            for link in subject.energy_balance.failing_links
        ]


class LoopFaults(EnergyBalanceRule):
    """What it checks: the loop rules of the energy ports hold after the run, and no loop end pumps water the balance cannot follow.

    The loop rules (``EP-01`` to ``EP-03`` of ``hisim/setup_rules/energy_ports.py``: an end pairs with its other end,
    a loop has one supplier, one flow is read by one loop end, no exempt component or meter declares an end)
    refuse the build before the first step; the balance resolves the loops again from the run's wiring, and a
    table read without that build is judged here. Besides, an end that pumps water (its own mass flow is not 0 in
    some step) while one of the quantities it reads is not connected is a fault; an open end that moves no water is
    left out.

    Why: the heat of a loop is derived once from its mass flow and its two temperatures, and both ends book it.
    An end the balance cannot pair, or water pumped into a loop whose return it cannot read, moves heat nowhere the
    balance can see.

    Example of a fault: ``loop: Boiler pumps water through its loop 'space_heating' (Flow, space_heating_heat), but
    its temperature_in input Return is not connected: the heat of that water goes nowhere the balance can see``.

    How to fix: connect the end's open input to the other end's output, or tag the loop on both ends with one
    supplier; a split of one flow needs a valve with one loop per branch.
    """

    CODE = "AR-06"
    NAME = "loop-faults"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    # A loop's heat read from a mass flow that is NaN is NaN.
    REQUIRES = frozenset({"AR-03"})

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per breach of a loop rule the balance found, each at its component and output."""
        return [
            self.fault(f"loop: {breach.message}", component=breach.component, port=breach.port)
            for breach in subject.energy_balance.declared.loop_breaches
        ]


class ColumnsReadable(EnergyBalanceRule):
    """What it checks: the balance can read every column a declared port, a loop end or a meter names, and every loop's flow and step.

    An input port reads the output connected to it; a loop end the three outputs of its loop (mass flow,
    supply and return temperature); a meter the outputs it declares in ``metered_exchanges``. Each must be a column of
    the run. A loop's mass flow is never negative and the step is positive, as the hydronic arithmetic requires
    (:func:`hisim.hydronics.circuit_power_w`). What cannot be read is left out of the balance.

    Why: a port, a loop or a meter left out of the balance would make every component around it fail or pass for
    the wrong reason. A negative flow is always the caller's error: a loop's direction is its topology.

    Example of a fault: ``Meter declares that it meters electricity on its output FromGridInWatt, which it does not
    have.``; ``the loop Boiler.Flow of Tank: mass_flow_kg_per_s must be >= 0 kg/s, got -0.1 in step 1.``

    How to fix: correct the output named in the component's ``metered_exchanges`` or its loop tags, or correct the
    component that publishes a negative mass flow.
    """

    CODE = "AR-07"
    NAME = "loop-columns"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    # The balance is read only from a whole table whose columns are finite.
    REQUIRES = frozenset({"AR-01", "AR-02"})

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per column, loop or meter reading the balance could not read, at its component."""
        return [
            self.fault(fault.message, component=fault.component, port=fault.port)
            for fault in subject.energy_balance.declared.column_faults
        ]


class NoPortOnANode(EnergyBalanceRule):
    """What it checks: no component that is exempt from the energy balance, and no meter, declares an energy port on an output or an input.

    A component is exempt when its kind moves no energy (a controller decides) or its class states why in
    ``ENERGY_PORTS_EXEMPT_BECAUSE``; a meter is a node that publishes what it meters (``metered_exchanges``). The
    balance leaves such a port out.

    Why: a controller or a meter that declares a port contradicts its kind; the balance never takes either as the
    peer of a port, so the port's energy would be booked on a node that moves none.

    Example of a fault: ``EMS declares the energy port Power, but it is exempt from the energy balance: an L2
    controller decides, it moves no energy.``

    How to fix: move the port to the device whose energy it is, or, if the component does move energy, remove the
    reason it is exempt.
    """

    CODE = "AR-08"
    NAME = "no-port-on-a-node"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    # The balance is read only from a whole table whose columns are finite.
    REQUIRES = frozenset({"AR-01", "AR-02"})

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per energy port an exempt component or a meter declares."""
        return [
            self.fault(fault.message, component=fault.component, port=fault.port)
            for fault in subject.energy_balance.declared.node_port_faults
        ]


class PortLinked(EnergyBalanceRule):
    """What it checks: every declared port that moves energy has a peer: a meter, a component that moves energy, or its carrier's boundary node.

    A port's peer comes from the wiring; a port no meter and no such component reads, whose carrier has no boundary
    default, is unlinked. An idle port, one that moves nothing in the whole run, needs no peer.

    Why: an unlinked port's energy goes nowhere in the Sankey; the run's balance cannot say where it went, so it is a
    wiring fault (``roadmap/complete_energy_sankeys_spec.md`` §4.3, the acceptance of PR 6). A demo does not run the
    rule: it may run an occupancy without a building, whose room heat then has no receiver.

    Example of a fault: ``the energy port UTSPConnector.HeatingByResidents (production, room_heat, 412 kWh) has no peer:
    no meter and no component that moves energy reads it, and its carrier has no boundary node``.

    How to fix: connect the output to the component that receives the energy (the building for room heat), or let
    that component tag its reading input with the matching port.
    """

    CODE = "AR-09"
    NAME = "port-linked"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    # An idle port is told from one that moves energy by its values, which a NaN makes unreadable.
    REQUIRES = frozenset({"AR-03"})
    WAIVED_FOR = MappingProxyType(
        {
            SetupKind.DEMO: (
                "a demo may run an occupancy without a building; the room heat it does not place is reported as unlinked"
            )
        }
    )

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per declared port that moves energy and has no peer."""
        return [
            self.fault(
                f"the energy port {series.name} ({series.port.role.value}, {series.port.carrier.value}, "
                f"{series.annual_kilowatt_hours:.6g} kWh) has no peer: no meter and no component that moves energy reads "
                "it, and its carrier has no boundary node",
                component=series.component,
                port=series.port_name,
            )
            for series in subject.energy_balance.declared.unlinked()
        ]


class ComponentDeclared(EnergyBalanceRule):
    """What it checks: every component of the run declares energy ports, or is exempt from the balance with a reason, or is a meter.

    A component is exempt when its kind moves no energy (a controller decides) or its class states why in
    ``ENERGY_PORTS_EXEMPT_BECAUSE``; a meter is a node that publishes what it meters. Any other component that declares
    no port is undeclared.

    Why: the balance cannot see the flows of an undeclared component; the Sankey draws it as a node of its own, and
    while one exists, the meter rules (``MT-01``, ``MT-02``) only report what they find. The rule only logs, as the
    balance report has always listed the undeclared components; ``tests/test_energy_port_coverage.py`` refuses a class
    that does neither in CI.

    Example of a fault: ``Building (Building) declares no energy port and states no reason to be exempt: the balance
    draws it as an undeclared node``.

    How to fix: declare the component's energy ports on its outputs (``add_output(..., energy_port=EnergyPort(...))``),
    or state on its class why it moves no energy (``ENERGY_PORTS_EXEMPT_BECAUSE``).
    """

    CODE = "AR-10"
    NAME = "component-declared"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = False
    # The balance is read once its ports are known to be finite; an undeclared component is found by the same read.
    REQUIRES = frozenset({"AR-03"})

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per component that declares no port, is not exempt and is no meter."""
        declared = subject.energy_balance.declared
        return [
            self.fault(
                f"{name} ({declared.class_names.get(name, '')}) declares no energy port and states no reason to be exempt: "
                "the balance draws it as an undeclared node",
                component=name,
            )
            for name in declared.undeclared
        ]


class HeatingOnlyLoopNeverCools(EnergyBalanceRule):
    """What it checks: no supplier of a fluid loop that may not cool removes heat from the loop's water beyond the noise.

    A loop may cool only if every member's ``FluidLoop`` says ``may_cool`` (D19 of ``roadmap/fluid_loops_spec.md``): a
    boiler, a district-heating substation, a hot-water tank and a distribution system without ``cooling_enabled`` say
    ``False``. The balance derives each loop's heat from its supplier's columns, ``m c (T_sup - T_ret)``; a negative
    heat of at most 1 W is the noise of the convergence and is booked as the loop's heat, never as cooling
    (``DeclaredPorts.COOLING_NOISE_IN_WATT``). A step beyond it on a loop that may not cool is a fault.

    Why: the step books cooling the loop cannot deliver. A hot-water tank does not cool its heat pump, and a
    distribution system that cannot cool does not take room heat to the generator; the run's heat and cooling flows,
    and every Sankey and KPI built on them, would be wrong.

    Example of a fault: ``the loop HeatPump.dhw heats only (DHWStorage.generator says may_cool=False), and its supplier
    HeatPump removed heat from the water in 3 steps, first in step 1234 (2021-01-13T20:30:00), up to 209 W``.

    How to fix: find why the supplier sends water colder than what comes back while it runs: a supply temperature
    computed from a stale return, a generator in cooling mode wired to an emitter that cannot cool (switch the
    emitter's cooling on, or the generator's off). A loop meant to cool says ``may_cool=True`` on every member.
    """

    CODE = "AR-11"
    NAME = "heating-only-loop-never-cools"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    # A loop's heat read from a column that is NaN is NaN, and no comparison with the noise holds.
    REQUIRES = frozenset({"AR-03"})

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per heating-only loop whose supplier removed heat beyond the noise, at the supplier's flow."""
        balance = subject.energy_balance
        return [
            self.fault(loop.describe(balance.step_time(loop.first_step)), component=loop.supplier, port=loop.flow)
            for loop in balance.declared.cooling_on_heating_only_loops
        ]
