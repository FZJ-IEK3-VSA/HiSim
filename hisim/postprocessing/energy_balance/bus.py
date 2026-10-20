"""The house electricity bus: grid import and export derived per step from the declared ports (spec §4.4).

Electricity flows between physical devices through a bus, one per building, that is not a component (owner decision
D1 of ``roadmap/complete_energy_sankeys_spec.md``). Its members are every electricity port whose peer resolves to the
bus (:meth:`hisim.postprocessing.energy_balance.ports.Wiring.peer_of`): a consumer's ``IN`` port takes from it, a
producer's ``OUT`` port feeds it. Per step ``t`` the bus nets them, ``net_t = sum(IN_t) - sum(OUT_t)`` in kWh, and
the grid is what is left over: ``grid import = max(net_t, 0)`` and ``grid export = max(-net_t, 0)``. So the bus
closes by construction and has no residual. For example, a step in which the heat pump takes 0.5 kWh and the PV
system gives 0.2 kWh imports 0.3 kWh; a step in which the PV system gives 0.7 kWh exports 0.2 kWh.

A meter is not a node; what it counts is the cross-check of the derived grid (:class:`MeterCrossCheck`). Its import
and export must equal the bus's per step within the tolerance of the balance. A difference means a consumer or
producer the meter counts and the bus does not (an undeclared device), or the reverse. As long as a component of the
run is undeclared, the bus may miss that component's electricity, so a disagreement is reported and does not yet
fail the run; in a run without undeclared components it fails the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from hisim import loadtypes as lt
from hisim.postprocessing.energy_balance.check import Tolerance
from hisim.postprocessing.energy_balance.ports import DeclaredPorts, MeterReading, Peer, PeerKind, PortSeries


@dataclass
class ElectricityBus:
    """The house electricity bus of one building: its member ports and the grid exchange derived from them.

    Attributes:
        name: The bus's node name, "house electricity" or "house electricity · <building>".
        building: The building, None in a run without building ids.
        members: The electricity ports whose peer is this bus.
        steps: The number of steps of the run.
    """

    name: str
    building: Optional[str]
    members: List[PortSeries] = field(default_factory=list)
    steps: int = 0

    @classmethod
    def of_run(cls, declared: DeclaredPorts) -> List["ElectricityBus"]:
        """Every bus of a run: one per building that has a member port or an electricity meter.

        A building whose meter counts electricity but whose components declare no electricity port gets a bus
        without members, so that its meter is still cross-checked: its derived import and export are zero.

        Args:
            declared: The declared ports and meters of the run.

        Returns:
            The buses in the order their first member or meter appears in the run.
        """
        steps = 0 if declared.index is None else len(declared.index)
        buses: Dict[str, ElectricityBus] = {}
        for series in declared.all_series():
            if series.peer.kind != PeerKind.BUS:
                continue
            building = series.output.component_id.building
            bus = buses.setdefault(series.peer.name, cls(series.peer.name, building, steps=steps))
            bus.members.append(series)
        for reading in declared.meters:
            if reading.exchange.carrier != lt.EnergyBalanceCarrier.ELECTRICITY:
                continue
            name = Peer.bus_of(reading.building).name
            buses.setdefault(name, cls(name, reading.building, steps=steps))
        return list(buses.values())

    def sum_of_role(self, role: lt.EnergyRole) -> np.ndarray:
        """The sum per step of the members of one role, in kWh."""
        total = np.zeros(self.steps)
        for series in self.members:
            if series.port.role == role:
                total = total + series.kilowatt_hours
        return total

    @property
    def consumption_per_step(self) -> np.ndarray:
        """What the consumers take from the bus per step, in kWh: the sum of the members' ``IN`` ports."""
        return self.sum_of_role(lt.EnergyRole.IN)

    @property
    def production_per_step(self) -> np.ndarray:
        """What the producers give to the bus per step, in kWh: the sum of the members' ``OUT`` ports."""
        return self.sum_of_role(lt.EnergyRole.OUT)

    @property
    def net_per_step(self) -> np.ndarray:
        """Consumption minus production per step, in kWh: positive when the house imports."""
        return np.asarray(self.consumption_per_step - self.production_per_step, dtype=float)

    @property
    def grid_import_per_step(self) -> np.ndarray:
        """The grid import per step, in kWh: the net where it is positive."""
        return np.asarray(np.maximum(self.net_per_step, 0.0), dtype=float)

    @property
    def grid_export_per_step(self) -> np.ndarray:
        """The grid export per step, in kWh: minus the net where it is negative."""
        return np.asarray(np.maximum(-self.net_per_step, 0.0), dtype=float)

    @property
    def throughput_per_step(self) -> np.ndarray:
        """What passes the bus per step, in kWh: production plus import, which equals consumption plus export."""
        return np.asarray(self.production_per_step + self.grid_import_per_step, dtype=float)

    def as_dict(self) -> Dict[str, Any]:
        """The bus as the balance report lists it, with its members and its derived grid totals in kWh."""
        return {
            "name": self.name,
            "building": self.building,
            "members": [
                {
                    "component": series.component,
                    "output": series.output.field_name,
                    "role": series.port.role.value,
                    "total_kwh": series.annual_kilowatt_hours,
                }
                for series in self.members
            ],
            "consumption_kwh": float(self.consumption_per_step.sum()),
            "production_kwh": float(self.production_per_step.sum()),
            "grid_import_kwh": float(self.grid_import_per_step.sum()),
            "grid_export_kwh": float(self.grid_export_per_step.sum()),
        }


@dataclass
class MeterCrossCheck:
    """One meter's grid import and export against the bus of its building, per step (spec §4.4).

    A step agrees when both the import and the export differ by at most ``max(1e-6 kWh, 0.1 %)`` of the step's
    reference, the larger of the bus's throughput and the meter's import plus export; the run agrees when every
    step does and both run totals differ by at most 0.1 % of the summed reference (decision 6 of hisim-energy-sankeys-9uoo).

    Attributes:
        reading: What the meter counts.
        bus: The bus of the meter's building.
        enforced: Whether a disagreement fails the run: only in a run without undeclared components.
        not_enforced_because: Why a disagreement does not fail the run, or None when it does.
        step_failures: The steps in which the meter and the bus disagree.
        agrees: Whether the meter and the bus agree over the run and in every step.
    """

    reading: MeterReading
    bus: ElectricityBus
    enforced: bool
    not_enforced_because: Optional[str]
    step_failures: np.ndarray
    agrees: bool

    @classmethod
    def compare(
        cls, reading: MeterReading, bus: ElectricityBus, tolerance: Tolerance, undeclared: List[str]
    ) -> "MeterCrossCheck":
        """Compare one meter with the bus of its building.

        Args:
            reading: What the meter counts.
            bus: The bus of the meter's building.
            tolerance: The balance's tolerance.
            undeclared: The run's undeclared components; while there is one, a disagreement is only reported.

        Returns:
            The comparison.
        """
        reference = np.maximum(bus.throughput_per_step, reading.import_kilowatt_hours + reading.export_kilowatt_hours)
        limit = np.maximum(tolerance.step_absolute_in_kilowatt_hour, tolerance.step_relative * reference)
        import_difference = reading.import_kilowatt_hours - bus.grid_import_per_step
        export_difference = reading.export_kilowatt_hours - bus.grid_export_per_step
        step_failures = (np.abs(import_difference) > limit) | (np.abs(export_difference) > limit)
        annual_limit = tolerance.annual_relative * float(reference.sum())
        annual_agrees = (
            abs(float(import_difference.sum())) <= annual_limit and abs(float(export_difference.sum())) <= annual_limit
        )
        reason = (
            None
            if not undeclared
            else f"{len(undeclared)} components are undeclared, so the bus may miss their electricity"
        )
        return cls(
            reading=reading,
            bus=bus,
            enforced=not undeclared,
            not_enforced_because=reason,
            step_failures=step_failures,
            agrees=annual_agrees and not bool(step_failures.any()),
        )

    @property
    def fails_the_run(self) -> bool:
        """Whether the meter and the bus disagree in a run in which that is a failure."""
        return self.enforced and not self.agrees

    @property
    def worst_step(self) -> Optional[int]:
        """The step with the largest difference of import or export, for a check that does not agree."""
        if self.agrees or self.step_failures.size == 0:
            return None
        difference = np.maximum(
            np.abs(self.reading.import_kilowatt_hours - self.bus.grid_import_per_step),
            np.abs(self.reading.export_kilowatt_hours - self.bus.grid_export_per_step),
        )
        return int(np.argmax(difference))

    def describe(self) -> str:
        """The disagreement in one sentence, for the error message: totals and the number of steps beyond tolerance."""
        return (
            f"meter {self.reading.meter} counts {self.reading.import_kilowatt_hours.sum():.6g} kWh imported and "
            f"{self.reading.export_kilowatt_hours.sum():.6g} kWh exported, the bus {self.bus.name} derives "
            f"{self.bus.grid_import_per_step.sum():.6g} kWh and {self.bus.grid_export_per_step.sum():.6g} kWh; "
            f"{int(self.step_failures.sum())} steps beyond tolerance, worst step {self.worst_step}"
        )

    def as_dict(self) -> Dict[str, Any]:
        """The cross-check as the balance report lists it beside its bus."""
        worst = self.worst_step
        return {
            "meter": self.reading.meter,
            "carrier": self.reading.exchange.carrier.value,
            "metered_import_kwh": float(self.reading.import_kilowatt_hours.sum()),
            "metered_export_kwh": float(self.reading.export_kilowatt_hours.sum()),
            "derived_import_kwh": float(self.bus.grid_import_per_step.sum()),
            "derived_export_kwh": float(self.bus.grid_export_per_step.sum()),
            "failing_steps": int(self.step_failures.sum()),
            "worst_step": worst,
            "agrees": self.agrees,
            "enforced": self.enforced,
            "not_enforced_because": self.not_enforced_because,
        }


class BusCheck:
    """Forms the buses of a run and cross-checks every electricity meter against the bus of its building."""

    def __init__(self, declared: DeclaredPorts, tolerance: Tolerance = Tolerance()) -> None:
        """Form the buses of ``declared`` and compare each electricity meter with its bus.

        Args:
            declared: The declared ports and meters of the run.
            tolerance: The balance's tolerance.
        """
        self.buses: List[ElectricityBus] = ElectricityBus.of_run(declared)
        by_name = {bus.name: bus for bus in self.buses}
        self.cross_checks: List[MeterCrossCheck] = [
            MeterCrossCheck.compare(
                reading, by_name[Peer.bus_of(reading.building).name], tolerance, declared.undeclared
            )
            for reading in declared.meters
            if reading.exchange.carrier == lt.EnergyBalanceCarrier.ELECTRICITY
        ]

    @property
    def failing(self) -> List[MeterCrossCheck]:
        """The cross-checks that fail the run."""
        return [check for check in self.cross_checks if check.fails_the_run]

    def bus_entry(self, bus: ElectricityBus, step_time: Callable[[Optional[int]], Optional[str]]) -> Dict[str, Any]:
        """One bus's entry of the report, with the cross-checks of the meters of its building.

        Args:
            bus: The bus.
            step_time: Turns a step's index into the time stamp the report shows beside it.
        """
        checks = []
        for check in self.cross_checks:
            if check.bus is bus:
                entry = check.as_dict()
                entry["worst_step"] = (
                    None
                    if check.worst_step is None
                    else {"index": check.worst_step, "time": step_time(check.worst_step)}
                )
                checks.append(entry)
        return {**bus.as_dict(), "meter_cross_checks": checks}
