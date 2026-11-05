"""The meters of a run as nodes of the energy balance, and the check of what each publishes (spec §4.4, Q-J).

A meter that declares what it meters (:meth:`hisim.component.Component.metered_exchanges`) stands between its
carrier's boundary node and the devices whose metered ports it reads (owner decision Q-J of
``roadmap/complete_energy_sankeys_spec.md``): the published import enters it from "grid import" or the carrier's
supply node, the published export leaves it to "grid export", a consumer's ``CONSUMPTION`` port takes from it and a
producer's ``PRODUCTION`` port gives to it. The meter's node closes only if, per step ``t``,

``(import_t - export_t) - (sum(IN_t) - sum(OUT_t)) = 0``,

that is, what it publishes equals what it reads, within the tolerance of the balance. Nothing is derived: a gap is
the meter's residual, drawn to or from the red ``unaccounted`` node, never absorbed into the import or the export.
For example, a step in which the heat pump takes 0.5 kWh and the PV system gives 0.2 kWh closes when the meter
publishes an import of 0.3 kWh; if it publishes 0.4 kWh, 0.1 kWh is unaccounted.

Besides, every metered port must pass a meter (:meth:`~.ports.DeclaredPorts.unmetered`). Both findings are written
to the report in every run; they fail the run only once no component of the run is undeclared (owner decision Q-H),
since until then a meter may read the flows of a device that declares no port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from hisim import loadtypes as lt
from hisim.postprocessing.energy_balance.check import Tolerance
from hisim.postprocessing.energy_balance.ports import DeclaredPorts, MeterReading, PeerKind, PortSeries


@dataclass
class MeterBalance:
    """One meter's balance of one carrier over the run: what it publishes against the declared ports it reads.

    A step agrees when its residual is at most ``max(1e-6 kWh, 0.1 %)`` of the step's throughput, the larger of
    what enters the meter's node (import and the producers' ports) and what leaves it (export and the consumers'
    ports); the run agrees when every step does and the run's residual is at most 0.1 % of the summed throughput
    (decision 6 of hisim-energy-sankeys-9uoo).

    Attributes:
        reading: What the meter publishes.
        ports: The declared ports of the meter's carrier whose peer is the meter.
        residual_per_step: Published import minus export, minus the consumers' plus the producers' ports, in kWh.
        throughput_per_step: The step's throughput, in kWh.
        step_failures: The steps beyond the tolerance.
        agrees: Whether the meter closes over the run and in every step.
        enforced: Whether a gap fails the run: only in a run without undeclared components.
        not_enforced_because: Why a gap does not fail the run, or None when it does.
    """

    reading: MeterReading
    ports: List[PortSeries]
    residual_per_step: np.ndarray
    throughput_per_step: np.ndarray
    step_failures: np.ndarray
    agrees: bool
    enforced: bool
    not_enforced_because: Optional[str] = field(default=None)

    @staticmethod
    def sum_of_role(ports: List[PortSeries], role: lt.EnergyRole, steps: int) -> np.ndarray:
        """The sum per step of the ports of one role, in kWh."""
        total = np.zeros(steps)
        for series in ports:
            if series.port.role == role:
                total = total + series.kilowatt_hours
        return np.asarray(total, dtype=float)

    @classmethod
    def of(
        cls, reading: MeterReading, ports: List[PortSeries], tolerance: Tolerance, undeclared: List[str]
    ) -> "MeterBalance":
        """Check one meter's published exchange against the ports it reads.

        Args:
            reading: What the meter publishes.
            ports: The declared ports of the meter's carrier whose peer is the meter.
            tolerance: The balance's tolerance.
            undeclared: The run's undeclared components; while there is one, a gap is only reported.

        Returns:
            The meter's balance.
        """
        steps = len(reading.import_kilowatt_hours)
        consumed = cls.sum_of_role(ports, lt.EnergyRole.CONSUMPTION, steps)
        produced = cls.sum_of_role(ports, lt.EnergyRole.PRODUCTION, steps)
        entering = reading.import_kilowatt_hours + produced
        leaving = reading.export_kilowatt_hours + consumed
        residual = np.asarray(entering - leaving, dtype=float)
        throughput = np.asarray(np.maximum(entering, leaving), dtype=float)
        limit = np.maximum(tolerance.step_absolute_in_kilowatt_hour, tolerance.step_relative * throughput)
        step_failures = np.abs(residual) > limit
        annual_agrees = abs(float(residual.sum())) <= tolerance.annual_relative * float(throughput.sum())
        reason = (
            None
            if not undeclared
            else f"{len(undeclared)} components are undeclared, so the meter may read flows no port declares"
        )
        return cls(
            reading=reading,
            ports=ports,
            residual_per_step=residual,
            throughput_per_step=throughput,
            step_failures=step_failures,
            agrees=annual_agrees and not bool(step_failures.any()),
            enforced=not undeclared,
            not_enforced_because=reason,
        )

    @property
    def meter(self) -> str:
        """The meter's component name, which is also its node's name."""
        return self.reading.meter

    @property
    def carrier(self) -> lt.EnergyBalanceCarrier:
        """The carrier the meter counts."""
        return self.reading.exchange.carrier

    @property
    def residual(self) -> float:
        """The run's residual, in kWh: positive when the meter publishes more net import than it reads."""
        return float(self.residual_per_step.sum())

    @property
    def consumed_kilowatt_hours(self) -> float:
        """What the consumers the meter reads take over the run, in kWh."""
        return float(self.sum_of_role(self.ports, lt.EnergyRole.CONSUMPTION, len(self.residual_per_step)).sum())

    @property
    def produced_kilowatt_hours(self) -> float:
        """What the producers the meter reads give over the run, in kWh."""
        return float(self.sum_of_role(self.ports, lt.EnergyRole.PRODUCTION, len(self.residual_per_step)).sum())

    @property
    def largest_step_residual(self) -> float:
        """The largest absolute residual of any step, in kWh, also when it is within the tolerance."""
        return float(np.abs(self.residual_per_step).max()) if self.residual_per_step.size else 0.0

    @property
    def fails_the_run(self) -> bool:
        """Whether the meter does not close in a run in which that is a failure."""
        return self.enforced and not self.agrees

    @property
    def worst_step(self) -> Optional[int]:
        """The step with the largest residual, for a meter that does not close; None for one that does."""
        if self.agrees or self.residual_per_step.size == 0:
            return None
        return int(np.argmax(np.abs(self.residual_per_step)))

    def describe(self) -> str:
        """The gap in one sentence, for the error message: totals and the number of steps beyond tolerance."""
        return (
            f"meter {self.meter} publishes {self.reading.import_kilowatt_hours.sum():.6g} kWh of {self.carrier.value} "
            f"imported and {self.reading.export_kilowatt_hours.sum():.6g} kWh exported, the ports it reads take "
            f"{self.consumed_kilowatt_hours:.6g} kWh and give {self.produced_kilowatt_hours:.6g} kWh: residual "
            f"{self.residual:.6g} kWh; {int(self.step_failures.sum())} steps beyond tolerance, worst step "
            f"{self.worst_step}"
        )

    def as_dict(self, step_time: Callable[[Optional[int]], Optional[str]]) -> Dict[str, Any]:
        """The meter as the balance report lists it: what it publishes, what it reads and whether they agree.

        Args:
            step_time: Turns a step's index into the time stamp the report shows beside it.
        """
        worst = self.worst_step
        return {
            "meter": self.meter,
            "building": self.reading.building,
            "carrier": self.carrier.value,
            "import_output": self.reading.exchange.import_output,
            "export_output": self.reading.exchange.export_output,
            "published_import_kwh": float(self.reading.import_kilowatt_hours.sum()),
            "published_export_kwh": float(self.reading.export_kilowatt_hours.sum()),
            "reads": [
                {
                    "component": series.component,
                    "output": series.channel,
                    "role": series.port.role.value,
                    "total_kwh": series.annual_kilowatt_hours,
                }
                for series in self.ports
            ],
            "consumed_kwh": self.consumed_kilowatt_hours,
            "produced_kwh": self.produced_kilowatt_hours,
            "residual_kwh": self.residual,
            "throughput_kwh": float(self.throughput_per_step.sum()),
            "failing_steps": int(self.step_failures.sum()),
            "largest_step_residual_kwh": self.largest_step_residual,
            "worst_step": None
            if worst is None
            else {
                "index": worst,
                "time": step_time(worst),
                "residual_kwh": float(self.residual_per_step[worst]),
                "throughput_kwh": float(self.throughput_per_step[worst]),
            },
            "verdict": "agrees" if self.agrees else "does_not_agree",
            "enforced": self.enforced,
            "not_enforced_because": self.not_enforced_because,
        }


class MeterCheck:
    """Checks every meter of a run against the ports it reads, and finds every metered port that passes no meter."""

    def __init__(self, declared: DeclaredPorts, tolerance: Tolerance = Tolerance()) -> None:
        """Balance each meter's readings against the declared ports whose peer it is, and collect the unmetered ports.

        Args:
            declared: The declared ports and meters of the run.
            tolerance: The balance's tolerance.
        """
        self.balances: List[MeterBalance] = [
            MeterBalance.of(
                reading,
                [
                    series
                    for series in declared.all_series()
                    if series.peer.kind == PeerKind.METER
                    and series.peer.name == reading.meter
                    and series.port.carrier == reading.exchange.carrier
                ],
                tolerance,
                declared.undeclared,
            )
            for reading in declared.meters
        ]
        self.unmetered: List[PortSeries] = declared.unmetered()
        #: Whether an unmetered port fails the run: only in a run without undeclared components (owner decision Q-H).
        self.unmetered_enforced: bool = not declared.undeclared
        self.unmetered_not_enforced_because: Optional[str] = (
            None
            if not declared.undeclared
            else f"{len(declared.undeclared)} components are undeclared, so the wiring is not complete yet"
        )

    @property
    def failing(self) -> List[MeterBalance]:
        """The meters whose gap fails the run."""
        return [balance for balance in self.balances if balance.fails_the_run]

    @property
    def failing_unmetered(self) -> List[PortSeries]:
        """The unmetered ports that fail the run: all of them in a run without undeclared components, else none."""
        return list(self.unmetered) if self.unmetered_enforced else []

    def unmetered_entry(self) -> Dict[str, Any]:
        """The unmetered ports as the report lists them, with whether they fail the run and why not."""
        return {
            "enforced": self.unmetered_enforced,
            "not_enforced_because": self.unmetered_not_enforced_because,
            "ports": [
                {
                    "component": series.component,
                    "output": series.channel,
                    "role": series.port.role.value,
                    "carrier": series.port.carrier.value,
                    "peer": series.peer.name,
                    "peer_kind": series.peer.kind.value,
                    "total_kwh": series.annual_kilowatt_hours,
                }
                for series in self.unmetered
            ],
        }
