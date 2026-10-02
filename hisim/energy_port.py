"""The energy-port contract: what an output of a component is in that component's energy balance (hisim-9uoo.1).

An output that carries energy states it with an :class:`EnergyPort`, passed to
:meth:`hisim.component.Component.add_output` as ``energy_port=``: its role in the balance
(:class:`~hisim.loadtypes.EnergyRole`), its carrier (:class:`~hisim.loadtypes.EnergyBalanceCarrier`) and,
for the Sankey, who is on the other side. The balance check and the Sankeys in
:mod:`hisim.postprocessing.energy_balance` read nothing else. The check runs in every simulation and fails
the run on a balance that does not close; a component whose outputs declare no port is *undeclared*, which is
reported, never a failure.

The other side of a port is found from the wiring:

* ``peer_input`` names one of the component's own inputs; the component that input is connected to is
  the peer (a storage's heat from its generator: the generator feeds the storage's mass-flow input).
* otherwise the peer is the component reading ``peer_output`` -- one of the component's own outputs,
  by default the port's own output for an ``OUT`` port (the building reads the heat a distribution
  system delivers). A port names ``peer_input`` or ``peer_output``, never both.
* ``environment`` names an environment node instead; an ``IN`` port of carrier ``AMBIENT_HEAT`` or
  ``SOLAR`` defaults to the node of that name and a ``LOSS`` port to ``OUTDOORS``.

A transfer between two components that both declare ports is booked by both: the sender's ``OUT`` port whose
peer is the receiver, and the receiver's ``IN`` port of the same carrier whose peer is the sender. The check
compares the two totals and fails the run when they disagree beyond its tolerance, and when only one side books
the transfer (owner, 2026-10-02: always fail hard). A one-sided booking is allowed only toward a component that
declares no ports at all, an environment node or nobody the wiring names.

A power output (W, kW) is converted to energy with the timestep, an energy output (Wh, kWh, kWh per timestep, J,
kJ) is taken per step as it is; any other unit is refused when the output is declared
(:meth:`EnergyPort.validate_unit`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Dict, Optional

from hisim import loadtypes as lt


@dataclass(frozen=True)
class EnergyPort:
    """Role, carrier and peer of one energy-carrying output."""

    role: lt.EnergyRole
    carrier: lt.EnergyBalanceCarrier
    #: One of the component's own inputs; the component it is connected to is the peer.
    peer_input: Optional[str] = None
    #: One of the component's own outputs; the component reading it is the peer.
    peer_output: Optional[str] = None
    #: An environment node as the peer, instead of a component.
    environment: Optional[lt.EnvironmentNode] = None
    #: True when the output is published with the opposite sign of the role (heat leaving as a negative number).
    negated: bool = False

    #: Kilowatt hours per step of one unit of an energy output.
    ENERGY_UNITS_IN_KILOWATT_HOUR: ClassVar[Dict[lt.Units, float]] = {
        lt.Units.WATT_HOUR: 1e-3,
        lt.Units.KWH: 1.0,
        lt.Units.KWH_PER_TIMESTEP: 1.0,
        lt.Units.JOULE: 1.0 / 3.6e6,
        lt.Units.KILOJOULE: 1.0 / 3.6e3,
    }
    #: Kilowatts of one unit of a power output.
    POWER_UNITS_IN_KILOWATT: ClassVar[Dict[lt.Units, float]] = {lt.Units.WATT: 1e-3, lt.Units.KILOWATT: 1.0}

    def __post_init__(self) -> None:
        """Refuse a port whose role or carrier is not one of the enums, or that names two peers."""
        lt.EnergyRole(self.role)
        lt.EnergyBalanceCarrier(self.carrier)
        if self.environment is not None and (self.peer_input is not None or self.peer_output is not None):
            raise ValueError("An energy port's peer is either an environment node or a component, not both.")
        if self.peer_input is not None and self.peer_output is not None:
            raise ValueError(
                f"An energy port names its peer by one of peer_input or peer_output, not both: "
                f"peer_input={self.peer_input!r}, peer_output={self.peer_output!r}."
            )

    @classmethod
    def validate_unit(cls, unit: lt.Units) -> None:
        """Refuse a unit that is neither a power nor an energy.

        Raises:
            ValueError: If ``unit`` is neither a power nor an energy.
        """
        if unit not in cls.ENERGY_UNITS_IN_KILOWATT_HOUR and unit not in cls.POWER_UNITS_IN_KILOWATT:
            raise ValueError(f"An energy port must be a power or an energy, not {unit!r}.")

    @classmethod
    def kilowatt_hours_per_step(cls, unit: lt.Units, seconds_per_timestep: float) -> float:
        """The factor that turns one step's value of an output in ``unit`` into kWh.

        Raises:
            ValueError: If ``unit`` is neither a power nor an energy.
        """
        cls.validate_unit(unit)
        if unit in cls.ENERGY_UNITS_IN_KILOWATT_HOUR:
            return cls.ENERGY_UNITS_IN_KILOWATT_HOUR[unit]
        return cls.POWER_UNITS_IN_KILOWATT[unit] * seconds_per_timestep / 3600.0

    def default_environment(self) -> Optional[lt.EnvironmentNode]:
        """The environment node this port is linked to when it names no peer of its own."""
        if self.environment is not None:
            return self.environment
        if self.peer_input is not None or self.peer_output is not None:
            return None
        if self.role == lt.EnergyRole.IN and self.carrier == lt.EnergyBalanceCarrier.AMBIENT_HEAT:
            return lt.EnvironmentNode.AMBIENT_HEAT
        if self.role == lt.EnergyRole.IN and self.carrier == lt.EnergyBalanceCarrier.SOLAR:
            return lt.EnvironmentNode.SOLAR
        if self.role == lt.EnergyRole.LOSS:
            return lt.EnvironmentNode.OUTDOORS
        return None

    def as_dict(self) -> Dict[str, object]:
        """The port as the balance report lists it."""
        return {
            "role": self.role.value,
            "carrier": self.carrier.value,
            "peer_input": self.peer_input,
            "peer_output": self.peer_output,
            "environment": None if self.environment is None else self.environment.value,
            "negated": self.negated,
        }
