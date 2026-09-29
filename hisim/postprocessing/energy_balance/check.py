"""Checks whether each component's energy balance closes (hisim-9uoo.2).

Per component and timestep, vectorised over the run:
``residual = sum(IN) - sum(OUT) - sum(LOSS) - sum(STORED_CHANGE)`` over all its declared ports. The balance is
the component's, not a carrier's: a converter turns one carrier into another, so only its total is
conserved; the per-carrier totals are reported beside it.

The tolerance is the owner's (decision 6 of hisim-9uoo): the run's residual is at most 0.1 % of the
component's throughput over the run, and every step's residual at most ``max(1e-6 kWh, 0.1 %`` of that step's
throughput). A step's throughput is the larger of what enters the component (``IN``, energy taken from its
store, and any port running backwards) and what leaves it; the run's is the sum over its steps.

A run checked in strict mode (``HISIM_ENERGY_BALANCE=strict``) fails when a balance does not close; the
default, ``report``, writes the report and logs one warning per failing component.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass, field
from typing import ClassVar, Dict, List, Optional

import numpy as np

from hisim import loadtypes as lt
from hisim.postprocessing.energy_balance.ports import DeclaredPorts, PortSeries


#: The environment variable that selects the :class:`BalanceMode`; ``report`` when unset.
MODE_VARIABLE = "HISIM_ENERGY_BALANCE"


class EnergyBalanceError(RuntimeError):
    """Raised in strict mode when at least one component's energy balance does not close."""


@enum.unique
class BalanceMode(str, enum.Enum):
    """What a balance that does not close does to the run."""

    REPORT = "report"
    STRICT = "strict"

    @classmethod
    def from_environment(cls) -> "BalanceMode":
        """The mode ``HISIM_ENERGY_BALANCE`` selects.

        Raises:
            ValueError: When the variable holds anything but ``report`` or ``strict``.
        """
        value = os.environ.get(MODE_VARIABLE, cls.REPORT.value).strip().lower()
        try:
            return cls(value)
        except ValueError:
            accepted = ", ".join(mode.value for mode in cls)
            raise ValueError(f"{MODE_VARIABLE}={value!r} is not one of {accepted}") from None


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


@dataclass
class RoleTotals:
    """Totals over the run of the four roles, in kWh."""

    energy_in: float = 0.0
    energy_out: float = 0.0
    loss: float = 0.0
    stored_change: float = 0.0

    def add(self, role: lt.EnergyRole, kilowatt_hours: float) -> None:
        """Add one port's total to its role."""
        if role == lt.EnergyRole.IN:
            self.energy_in += kilowatt_hours
        elif role == lt.EnergyRole.OUT:
            self.energy_out += kilowatt_hours
        elif role == lt.EnergyRole.LOSS:
            self.loss += kilowatt_hours
        else:
            self.stored_change += kilowatt_hours

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


class BalanceCheck:
    """Computes every declared component's balance and its verdict."""

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
        worst_step = int(np.argmax(np.abs(residual))) if steps else None
        return ComponentBalance(
            component=component,
            class_name=class_name,
            ports=ports,
            totals=totals,
            carriers=carriers,
            residual_per_step=residual,
            throughput_per_step=throughput,
            step_failures=step_failures,
            closes=annual_closes and not bool(step_failures.any()),
            worst_step=worst_step,
        )
