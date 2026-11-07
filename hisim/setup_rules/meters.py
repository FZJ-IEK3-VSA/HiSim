"""The meter rules after the run: each meter publishes what it reads, and every bought carrier passes a meter.

A meter that declares what it meters (:meth:`hisim.component.Component.metered_exchanges`) is a node of the energy
balance between its carrier's boundary node and the devices whose metered ports it reads (owner decision Q-J of
``roadmap/complete_energy_sankeys_spec.md``). The balance computes, per meter and carrier, what it publishes against
what it reads (:class:`~hisim.postprocessing.energy_balance.meters.MeterCheck`), and every metered port whose flow
passes no meter; these rules judge what it computed.

Both rules refuse only in a run in which no component is undeclared (owner decision Q-H): while a component declares
no port, a meter may read a flow no port declares, and the wiring is not complete. That switch is a property of the
run, computed by the balance from the run's components, never of the kind. Their faults raise ``EnergyBalanceError``,
as the rest of the balance's do. ``MT-02`` is waived for a demo.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Sequence

from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.results import EnergyBalanceRule
from hisim.setup_rules.rule import Fault, RuleStage
from hisim.setup_rules.subjects import RunResults


class MeterBalanceAgrees(EnergyBalanceRule):
    """What it checks: every meter publishes, per carrier and step, what it reads from the declared ports linked to it, in a run without undeclared components.

    Per step, the published import minus the published export equals the consumers' ports minus the producers' ports
    whose peer is the meter, within the larger of 1e-6 kWh and 0.1 % of the step's throughput, and over the run within
    0.1 % of the summed throughput. While a component of the run declares no port, a gap is only reported in the
    balance report (owner decision Q-H): the meter may then read a flow no port declares.

    Why: the meter is what a bill, a self-consumption KPI and a grid chart read. Nothing is derived from the devices:
    a gap between what it publishes and what its devices take is energy that appears or vanishes at the meter.

    Example of a fault: ``meter Meter publishes 1 kWh of electricity imported and 0.6 kWh exported, the ports it reads
    take 1.2 kWh and give 1.2 kWh: residual 0.4 kWh; 1 steps beyond tolerance, worst step 0 (2021-01-01T00:00:00): the
    meter reads a flow no port declares, or a port linked to it is not what the meter reads``.

    How to fix: declare the energy port of the device whose flow the meter reads, or connect the meter's input to the
    output the device's port is declared on; a meter that sums its inputs publishes exactly their sum.
    """

    CODE = "MT-01"
    NAME = "meter-balance"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    # A meter's residual read from a port that is NaN is NaN.
    REQUIRES = frozenset({"AR-03"})

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per meter and carrier whose publication differs from what it reads, in a run where that refuses."""
        balance = subject.energy_balance
        return [
            self.fault(
                f"{meter.describe()} ({balance.step_time(meter.worst_step)}): the meter reads a flow no port declares, "
                f"or a port linked to it is not what the meter reads (tolerance: {balance.tolerance.describe()})",
                component=meter.meter,
                port=meter.reading.exchange.import_output,
            )
            for meter in balance.meter_check.failing
        ]


class MeteredPortHasAMeter(EnergyBalanceRule):
    """What it checks: every metered energy port (electricity, a bought carrier) passes a meter or hands its flow to a component, while nothing is undeclared.

    A metered port's peer is a meter that reads it, directly or through exempt controllers, or a component that moves
    energy, whose own metered ports pass a meter in turn. A port whose peer is a boundary node or nobody passes no
    meter. While a component of the run declares no port, an unmetered port is only reported (owner decision Q-H).

    Why: every bought carrier's flow passes a meter (owner decision Q-J); a boiler's gas bought past the meter is
    missing from the bill, the KPIs and the grid's balance.

    Example of a fault: ``port Boiler.GasIn (consumption, natural_gas, 1 kWh) passes no meter: its peer is natural gas
    supply``.

    How to fix: add the meter of the carrier and connect it to the device's output, or connect an existing meter's
    input to it. A demo does not run the rule.
    """

    CODE = "MT-02"
    NAME = "metered-port-has-a-meter"
    STAGE = RuleStage.AFTER_RUN
    ENFORCED = True
    # A port's peer is read once its values are known to be finite.
    REQUIRES = frozenset({"AR-03"})
    WAIVED_FOR = MappingProxyType(
        {SetupKind.DEMO: "a demo may leave a bought carrier unmetered; the balance reports the port as unmetered"}
    )

    def check(self, subject: RunResults) -> Sequence[Fault]:
        """Return one fault per metered port that passes no meter, in a run where that refuses."""
        return [
            self.fault(
                f"port {series.name} ({series.port.role.value}, {series.port.carrier.value}, "
                f"{series.annual_kilowatt_hours:.6g} kWh) passes no meter: its peer is {series.peer.name}",
                component=series.component,
                port=series.port_name,
            )
            for series in subject.energy_balance.meter_check.failing_unmetered
        ]
