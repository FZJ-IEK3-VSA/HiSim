"""The energy every declared port of a finished run moved, as the energy balance computed it: plain data for the cost engine.

The energy balance (``hisim/postprocessing/energy_balance/``) reads every energy port the components declare, every
fluid loop they tag and every exchange a meter publishes, converts each to kWh per step and checks that each
component's balance closes. :class:`PortEnergies` is what it hands on from that work: per port its role, carrier,
peer and kWh; per component the fluid loops it tags; per meter what it published and whether that agrees with the
ports it reads. The cost engine reads this record and never derives an energy itself, so the numbers it prices are
the ones the balance check vouched for.

Terms:

- **Port**: an output or input that declares an :class:`~hisim.energy_port.EnergyPort`, or one end of a fluid loop.
- **Peer**: the other side of a port: another component, a meter, a boundary node such as "natural gas supply", the
  component's own store, or nobody (:class:`PeerKind`).
- **Simulated period**: the run from its first to its last step; a one-day run's totals are one day's energy.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple, Type, TypeVar

import numpy as np

from hisim import loadtypes as lt

EnumMember = TypeVar("EnumMember", bound=enum.Enum)


@enum.unique
class PeerKind(str, enum.Enum):

    """What the other side of a port is.

    ``COMPONENT`` is another component, declared or not; ``METER`` a meter that reads the port; ``BOUNDARY`` a node
    where energy enters or leaves the house ("natural gas supply", "ambient heat source", "unconverted sunlight",
    "heat loss to outdoors", "hot water drawn at the taps"); ``STORE`` the component's own store, the other side of its
    storage change; ``UNLINKED`` nobody, a port whose wiring names no meter and no component that moves energy and
    whose carrier has no default boundary.
    """

    COMPONENT = "component"
    METER = "meter"
    BOUNDARY = "boundary"
    STORE = "store"
    UNLINKED = "unlinked"


@dataclass(frozen=True)
class PortEnergy:
    """The energy one declared port moved over the simulated period, with its role, carrier and peer.

    The sign is already resolved: a ``negated`` port and the supplier's sign of a fluid loop are applied, so a positive
    total is energy moved in the direction the role states. For example, a heat pump's electricity input over a year is
    ``PortEnergy("HeatPump", "ElectricalInputPowerSH", CONSUMPTION, ELECTRICITY, None, "ElectricityMeter",
    METER, 3200.0)``.

    Attributes:
        subject: The component's name, the subject the cost engine prices it under.
        port: The output or input the port is declared on, or, for a fluid-loop end, the loop's label
            (``loop <component>.<mass-flow output>``).
        role: Consumption, production, loss or storage change.
        carrier: The energy carrier the port moves.
        environment: The boundary node the port names as its peer, or None when its peer comes from the wiring.
        peer: The name of the other side: a component, a meter, a boundary node, the component's store or "unlinked".
        peer_kind: What the other side is.
        energy_of_simulated_period_in_kwh: The port's total over the simulated period, in kWh.
        energy_per_step_in_kwh: The port's energy in each step, in kWh, as the balance computed it; None for a record
            read from a file, which keeps only the totals. Left out of equality, so a record read back equals the one
            written.
    """

    subject: str
    port: str
    role: lt.EnergyRole
    carrier: lt.EnergyBalanceCarrier
    environment: Optional[lt.EnvironmentNode]
    peer: str
    peer_kind: PeerKind
    energy_of_simulated_period_in_kwh: float
    energy_per_step_in_kwh: Optional[np.ndarray] = field(default=None, compare=False, repr=False)

    def to_json(self) -> Dict[str, Any]:
        """Return the port as ``economic_inputs.json`` stores it: its total, without the steps, enums by member name."""
        return {
            "subject": self.subject,
            "port": self.port,
            "role": self.role.name,
            "carrier": self.carrier.name,
            "environment": None if self.environment is None else self.environment.name,
            "peer": self.peer,
            "peer_kind": self.peer_kind.name,
            "energy_of_simulated_period_in_kwh": self.energy_of_simulated_period_in_kwh,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "PortEnergy":
        """Read a port written by :meth:`to_json`; its energy per step is None, since the file keeps only the total.

        Raises:
            ValueError: If a role, carrier, environment or peer kind is not a member name of its enum.
            KeyError: If a field is missing.
        """
        environment = raw["environment"]
        return cls(
            subject=str(raw["subject"]),
            port=str(raw["port"]),
            role=PortEnergies.member_named(lt.EnergyRole, raw["role"]),
            carrier=PortEnergies.member_named(lt.EnergyBalanceCarrier, raw["carrier"]),
            environment=None if environment is None else PortEnergies.member_named(lt.EnvironmentNode, environment),
            peer=str(raw["peer"]),
            peer_kind=PortEnergies.member_named(PeerKind, raw["peer_kind"]),
            energy_of_simulated_period_in_kwh=float(raw["energy_of_simulated_period_in_kwh"]),
        )


@dataclass(frozen=True)
class LoopTag:
    """One fluid loop a component tags on its channels, whether or not the energy balance books the loop's heat.

    A component that tags a loop of space-heating or hot-water heat takes part in the heating, even where the balance
    does not book the loop: the heat distribution system without a buffer tags its loop but books no heat on it. For
    example, a gas boiler that heats its space-heating loop is ``LoopTag("GasBoiler", "space_heating",
    SPACE_HEATING_HEAT, supplies=True)``.

    Attributes:
        subject: The component's name.
        loop: The loop's name, as the component states it.
        carrier: The heat the loop carries while it heats.
        supplies: Whether the component supplies the loop (heats its water) rather than receives from it.
    """

    subject: str
    loop: str
    carrier: lt.EnergyBalanceCarrier
    supplies: bool

    def to_json(self) -> Dict[str, Any]:
        """Return the loop tag as ``economic_inputs.json`` stores it, the carrier by its member name."""
        return {"subject": self.subject, "loop": self.loop, "carrier": self.carrier.name, "supplies": self.supplies}

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "LoopTag":
        """Read a loop tag written by :meth:`to_json`.

        Raises:
            ValueError: If the carrier is not a member name of ``EnergyBalanceCarrier`` or ``supplies`` is not a boolean.
            KeyError: If a field is missing.
        """
        supplies = raw["supplies"]
        if not isinstance(supplies, bool):
            raise ValueError(f"The loop tag {raw['subject']}.{raw['loop']} states supplies={supplies!r}; expected true or false.")
        return cls(
            subject=str(raw["subject"]),
            loop=str(raw["loop"]),
            carrier=PortEnergies.member_named(lt.EnergyBalanceCarrier, raw["carrier"]),
            supplies=supplies,
        )


@dataclass(frozen=True)
class MeterEnergy:
    """What one meter published of one carrier over the simulated period, and whether it agrees with the ports it reads.

    A meter agrees when, in every step and over the run, its published import minus export equals what the ports it
    reads consume minus produce, within the tolerance of the energy balance. For example, an electricity meter that
    published 2,500 kWh bought and 100 kWh sold is ``MeterEnergy("ElectricityMeter", ELECTRICITY,
    "ElectricityFromGridInWatt", "ElectricityToGridInWatt", 2500.0, 100.0, agrees=True)``.

    Attributes:
        meter: The meter's component name.
        carrier: The carrier the meter counts.
        import_output: The output that publishes the import.
        export_output: The output that publishes the export, or None for a carrier the meter counts only bought.
        import_of_simulated_period_in_kwh: The published import over the simulated period, in kWh.
        export_of_simulated_period_in_kwh: The published export over the simulated period, in kWh; 0 without an export.
        agrees: Whether the published exchange equals what the ports the meter reads moved.
    """

    meter: str
    carrier: lt.EnergyBalanceCarrier
    import_output: str
    export_output: Optional[str]
    import_of_simulated_period_in_kwh: float
    export_of_simulated_period_in_kwh: float
    agrees: bool

    def to_json(self) -> Dict[str, Any]:
        """Return the meter's exchange as ``economic_inputs.json`` stores it, the carrier by its member name."""
        return {
            "meter": self.meter,
            "carrier": self.carrier.name,
            "import_output": self.import_output,
            "export_output": self.export_output,
            "import_of_simulated_period_in_kwh": self.import_of_simulated_period_in_kwh,
            "export_of_simulated_period_in_kwh": self.export_of_simulated_period_in_kwh,
            "agrees": self.agrees,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "MeterEnergy":
        """Read a meter's exchange written by :meth:`to_json`.

        Raises:
            ValueError: If the carrier is not a member name of ``EnergyBalanceCarrier`` or ``agrees`` is not a boolean.
            KeyError: If a field is missing.
        """
        agrees = raw["agrees"]
        if not isinstance(agrees, bool):
            raise ValueError(f"The meter {raw['meter']} states agrees={agrees!r}; expected true or false.")
        export_output = raw["export_output"]
        return cls(
            meter=str(raw["meter"]),
            carrier=PortEnergies.member_named(lt.EnergyBalanceCarrier, raw["carrier"]),
            import_output=str(raw["import_output"]),
            export_output=None if export_output is None else str(export_output),
            import_of_simulated_period_in_kwh=float(raw["import_of_simulated_period_in_kwh"]),
            export_of_simulated_period_in_kwh=float(raw["export_of_simulated_period_in_kwh"]),
            agrees=agrees,
        )


@dataclass(frozen=True)
class PortEnergies:
    """Every declared port of a run with its energy, every fluid loop the components tag, and every meter's exchange.

    Filled once by the energy balance after the run, read by the cost engine and written to ``economic_inputs.json``
    (totals only). An empty record is a run in which no component declares a port, tags a loop or meters a carrier.

    Attributes:
        ports: Every declared port, in the order the balance lists them.
        loop_tags: Every loop each component tags, in the order of the run's components.
        meters: Every metered exchange, one per meter and carrier.
    """

    ports: Tuple[PortEnergy, ...] = ()
    loop_tags: Tuple[LoopTag, ...] = ()
    meters: Tuple[MeterEnergy, ...] = ()

    def to_json(self) -> Dict[str, Any]:
        """Return the record as ``economic_inputs.json`` stores it: totals only, every enum by its member name.

        Member names (``ELECTRICITY``, ``CONSUMPTION``) are the spelling every economics file uses for an enum, as an
        energy-system file does. The energy per step stays in memory.
        """
        return {
            "ports": [port.to_json() for port in self.ports],
            "loop_tags": [tag.to_json() for tag in self.loop_tags],
            "meters": [meter.to_json() for meter in self.meters],
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "PortEnergies":
        """Read a record written by :meth:`to_json`; a missing list reads as empty.

        Raises:
            ValueError: If an enum is not spelled by a member name of its enum, or a flag is not a boolean.
            KeyError: If a port, loop tag or meter lacks a field.
        """
        return cls(
            ports=tuple(PortEnergy.from_json(item) for item in raw.get("ports", [])),
            loop_tags=tuple(LoopTag.from_json(item) for item in raw.get("loop_tags", [])),
            meters=tuple(MeterEnergy.from_json(item) for item in raw.get("meters", [])),
        )

    @staticmethod
    def member_named(enum_class: Type[EnumMember], name: Any) -> EnumMember:
        """Return the member of ``enum_class`` whose name is ``name``, as the record's file spells an enum.

        For example, ``member_named(EnergyBalanceCarrier, "ELECTRICITY")`` is ``EnergyBalanceCarrier.ELECTRICITY``; the
        value ``"electricity"`` is refused, so a file in another spelling fails where it is read.

        Args:
            enum_class: The enum to look the name up in.
            name: The member's name, as read from the file.

        Returns:
            The member.

        Raises:
            ValueError: If ``name`` is not the name of a member, listing the names it could be.
        """
        member = enum_class.__members__.get(name) if isinstance(name, str) else None
        if member is None:
            raise ValueError(f"{name!r} is not a member name of {enum_class.__name__}; expected one of {', '.join(enum_class.__members__)}.")
        return member
