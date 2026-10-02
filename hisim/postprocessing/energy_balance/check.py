"""Checks whether each component's energy balance closes (hisim-9uoo.2).

Per component and timestep, vectorised over the run:
``residual = sum(IN) - sum(OUT) - sum(LOSS) - sum(STORED_CHANGE)`` over all its declared ports. The balance is
the component's, not a carrier's: a converter turns one carrier into another, so only its total is
conserved; the per-carrier totals are reported beside it.

The tolerance is the owner's (decision 6 of hisim-9uoo): the run's residual is at most 0.1 % of the
component's throughput over the run, and every step's residual at most
``max(1e-6 kWh, 0.001 * step throughput)``. A step's throughput is the larger of what enters the component
(``IN``, energy taken from its store, and any port running backwards) and what leaves it; the run's is the sum
over its steps.

Beside the balances, two more things are checked (owner, 2026-10-02: always fail hard): every declared port is
finite in every step, and where both sides of a transfer declare it (a sender's ``OUT`` port and its receiver's
``IN`` port, :meth:`~.ports.DeclaredPorts.paired_links`) the two totals agree within the run's tolerance,
relative to the larger of the two. The check runs in every simulation; whatever does not hold fails the run
with :class:`EnergyBalanceError`. A run in which no component declares a port passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Dict, List, Optional

import numpy as np

from hisim import loadtypes as lt
from hisim.postprocessing.energy_balance.ports import DeclaredPorts, PortSeries


class EnergyBalanceError(ValueError):
    """A run whose declared energy flows do not hold: a balance or a link that does not close, or a port not finite."""


@dataclass(frozen=True)
class Tolerance:
    """When a balance counts as closed (owner decision 6 of hisim-9uoo)."""

    annual_relative: float = 1e-3
    step_relative: float = 1e-3
    step_absolute_in_kilowatt_hour: float = 1e-6

    def as_dict(self) -> Dict[str, float]:
        """The tolerance as the report states it."""
        return {
            "annual_relative": self.annual_relative,
            "step_relative": self.step_relative,
            "step_absolute_kwh": self.step_absolute_in_kilowatt_hour,
        }

    def describe(self) -> str:
        """The tolerance in one phrase, for an error message."""
        return (
            f"{100 * self.annual_relative:g} % of the throughput over the run, and per step "
            f"max({self.step_absolute_in_kilowatt_hour:g} kWh, {100 * self.step_relative:g} % of the step's throughput)"
        )


@dataclass
class RoleTotals:
    """Totals over the run of the four roles, in kWh."""

    energy_in: float = 0.0
    energy_out: float = 0.0
    loss: float = 0.0
    stored_change: float = 0.0

    def add(self, role: lt.EnergyRole, kilowatt_hours: float) -> None:
        """Add one port's total to its role.

        Raises:
            ValueError: For a role that is not one of the four.
        """
        if role == lt.EnergyRole.IN:
            self.energy_in += kilowatt_hours
        elif role == lt.EnergyRole.OUT:
            self.energy_out += kilowatt_hours
        elif role == lt.EnergyRole.LOSS:
            self.loss += kilowatt_hours
        elif role == lt.EnergyRole.STORED_CHANGE:
            self.stored_change += kilowatt_hours
        else:
            raise ValueError(f"{role!r} is not an energy role this balance knows.")

    @property
    def residual(self) -> float:
        """In minus out, loss and storage change."""
        return self.energy_in - self.energy_out - self.loss - self.stored_change

    def as_dict(self) -> Dict[str, float]:
        """The totals as the report states them."""
        return {
            "in_kwh": self.energy_in,
            "out_kwh": self.energy_out,
            "loss_kwh": self.loss,
            "stored_change_kwh": self.stored_change,
            "residual_kwh": self.residual,
        }


@dataclass
class ComponentBalance:
    """The balance of one component over the run."""

    component: str
    class_name: str
    ports: List[PortSeries]
    totals: RoleTotals
    carriers: Dict[str, RoleTotals]
    residual_per_step: np.ndarray
    throughput_per_step: np.ndarray
    step_failures: np.ndarray
    closes: bool
    #: The step with the largest residual, for a balance that does not close; None for one that closes.
    worst_step: Optional[int] = field(default=None)

    @property
    def throughput(self) -> float:
        """The component's throughput over the run, in kWh."""
        return float(self.throughput_per_step.sum())

    @property
    def residual(self) -> float:
        """The residual over the run, in kWh."""
        return float(self.residual_per_step.sum())

    @property
    def residual_percent(self) -> float:
        """The residual over the run in percent of the throughput (0 for a component nothing passed)."""
        return 100.0 * self.residual / self.throughput if self.throughput > 0 else 0.0

    @property
    def single_carrier(self) -> Optional[str]:
        """The carrier of all ports, when there is one; the Sankey draws the residual in that carrier's diagram."""
        carriers = set(self.carriers)
        return carriers.pop() if len(carriers) == 1 else None


@dataclass(frozen=True)
class LinkBalance:
    """One transfer both sides declare: what the sender books as sent and the receiver as received, in kWh."""

    sender: str
    receiver: str
    carrier: str
    sent: float
    received: float
    closes: bool

    @property
    def difference(self) -> float:
        """Sent minus received, in kWh."""
        return self.sent - self.received

    @property
    def difference_percent(self) -> float:
        """The difference in percent of the larger total (0 when both are 0)."""
        larger = max(abs(self.sent), abs(self.received))
        return 100.0 * self.difference / larger if larger > 0 else 0.0

    def as_dict(self) -> Dict[str, object]:
        """The link as the balance report lists it."""
        return {
            "sender": self.sender,
            "receiver": self.receiver,
            "carrier": self.carrier,
            "sent_kwh": self.sent,
            "received_kwh": self.received,
            "difference_kwh": self.difference,
            "difference_percent": self.difference_percent,
            "verdict": "closes" if self.closes else "does_not_close",
        }


@dataclass(frozen=True)
class NonFinitePort:
    """A declared port that is NaN or infinite in at least one step."""

    series: PortSeries
    first_step: int
    steps: int

    @property
    def name(self) -> str:
        """``component.output``."""
        return f"{self.series.component}.{self.series.output.field_name}"


class BalanceCheck:
    """Computes every declared component's balance, every paired link and every port that is not finite."""

    SIGN_OF_ROLE: ClassVar[Dict[lt.EnergyRole, float]] = {
        lt.EnergyRole.IN: 1.0,
        lt.EnergyRole.OUT: -1.0,
        lt.EnergyRole.LOSS: -1.0,
        lt.EnergyRole.STORED_CHANGE: -1.0,
    }

    def __init__(self, tolerance: Tolerance = Tolerance()) -> None:
        """Check against ``tolerance``."""
        self.tolerance = tolerance

    def run(self, declared: DeclaredPorts) -> List[ComponentBalance]:
        """The balance of every declared component, in the order the run declared them."""
        return [
            self.balance(component, declared.class_names.get(component, ""), ports)
            for component, ports in declared.by_component.items()
        ]

    def links(self, declared: DeclaredPorts) -> List[LinkBalance]:
        """Every transfer both sides declare, with whether its two totals agree within the run's tolerance."""
        return [
            LinkBalance(
                sender=sender,
                receiver=receiver,
                carrier=carrier,
                sent=sent,
                received=received,
                closes=abs(sent - received) <= self.tolerance.annual_relative * max(abs(sent), abs(received)),
            )
            for (sender, receiver, carrier), (sent, received) in declared.paired_links().items()
        ]

    @staticmethod
    def nonfinite(declared: DeclaredPorts) -> List[NonFinitePort]:
        """Every declared port that is NaN or infinite in some step, with its first such step."""
        found: List[NonFinitePort] = []
        for series in declared.all_series():
            bad = ~np.isfinite(series.kilowatt_hours)
            if bad.any():
                found.append(NonFinitePort(series, int(np.argmax(bad)), int(bad.sum())))
        return found

    def balance(self, component: str, class_name: str, ports: List[PortSeries]) -> ComponentBalance:
        """One component's balance."""
        steps = len(ports[0].kilowatt_hours)
        residual = np.zeros(steps)
        gains = np.zeros(steps)
        uses = np.zeros(steps)
        totals = RoleTotals()
        carriers: Dict[str, RoleTotals] = {}
        for port in ports:
            signed = self.SIGN_OF_ROLE[port.port.role] * port.kilowatt_hours
            residual += signed
            gains += np.maximum(signed, 0.0)
            uses += np.maximum(-signed, 0.0)
            totals.add(port.port.role, port.annual_kilowatt_hours)
            carriers.setdefault(port.port.carrier.value, RoleTotals()).add(
                port.port.role, port.annual_kilowatt_hours
            )
        throughput = np.maximum(gains, uses)
        step_limit = np.maximum(
            self.tolerance.step_absolute_in_kilowatt_hour, self.tolerance.step_relative * throughput
        )
        step_failures = np.abs(residual) > step_limit
        annual_closes = abs(float(residual.sum())) <= self.tolerance.annual_relative * float(throughput.sum())
        closes = annual_closes and not bool(step_failures.any())
        worst_step = int(np.argmax(np.abs(residual))) if steps and not closes else None
        return ComponentBalance(
            component=component,
            class_name=class_name,
            ports=ports,
            totals=totals,
            carriers=carriers,
            residual_per_step=residual,
            throughput_per_step=throughput,
            step_failures=step_failures,
            closes=closes,
            worst_step=worst_step,
        )
