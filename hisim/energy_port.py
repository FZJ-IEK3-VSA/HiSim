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

Hydronic circuits
-----------------

A water circuit carries no energy output: it is a mass flow, a supply temperature and a return temperature, and
its heat is derived from them, ``m c (T_sup - T_ret) dt``. A :class:`HydronicPort` names those three outputs and
derives the heat with :func:`hisim.hydronics.circuit_heat_kwh`, the one rule. Both ends of a circuit declare the
same port, the supply owner with role ``OUT`` and the receiver with role ``IN``, so a circuit balances by
construction. No component declares one yet, and the balance check does not read them yet. The contract the
balance check is to implement:

* A port names no peer. The two ends come from the wiring: the component that reads the circuit's mass-flow
  output is the other end.
* **One reader.** A mass-flow output read by more than one component that declares a ``HydronicPort`` on it
  fails the run: one flow cannot deliver its heat twice, and a split is a valve component with one circuit per
  branch.
* **Both ends or neither.** A circuit whose one end declares a ``HydronicPort`` while the other end, a
  component that declares ports, does not declare the matching one fails the run. A circuit whose other end
  declares no ports at all is reported as undeclared, as for an :class:`EnergyPort`.

The :class:`EnergyPort` peer pointers remain for the carriers that are not water: fuel, electricity, ambient
heat and solar.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Dict, Mapping, Optional, Tuple

from hisim import hydronics
from hisim import loadtypes as lt
from hisim.config.names import NameSyntax


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

    #: The balance carrier of a fuel a component names by its :class:`~hisim.loadtypes.LoadTypes` (read-only).
    FUEL_CARRIERS: ClassVar[Mapping[lt.LoadTypes, lt.EnergyBalanceCarrier]] = MappingProxyType(
        {
            lt.LoadTypes.GAS: lt.EnergyBalanceCarrier.NATURAL_GAS,
            lt.LoadTypes.OIL: lt.EnergyBalanceCarrier.HEATING_OIL,
            lt.LoadTypes.PELLETS: lt.EnergyBalanceCarrier.PELLETS,
            lt.LoadTypes.WOOD_CHIPS: lt.EnergyBalanceCarrier.WOOD_CHIPS,
            lt.LoadTypes.GREEN_HYDROGEN: lt.EnergyBalanceCarrier.HYDROGEN,
            lt.LoadTypes.DIESEL: lt.EnergyBalanceCarrier.DIESEL,
            lt.LoadTypes.DISTRICTHEATING: lt.EnergyBalanceCarrier.DISTRICT_HEAT,
            lt.LoadTypes.ELECTRICITY: lt.EnergyBalanceCarrier.ELECTRICITY,
        }
    )

    @classmethod
    def carrier_of_fuel(cls, load_type: lt.LoadTypes) -> lt.EnergyBalanceCarrier:
        """The balance carrier of the fuel ``load_type`` names.

        Raises:
            ValueError: If ``load_type`` is not a fuel the balance knows.
        """
        if load_type not in cls.FUEL_CARRIERS:
            raise ValueError(f"{load_type!r} is not a fuel with an energy-balance carrier.")
        return cls.FUEL_CARRIERS[load_type]

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


class HydronicPortError(ValueError):

    """A hydronic port whose output names or role cannot describe a circuit.

    Raised when the port is declared, so a component with a malformed port fails when it is built rather than when
    the balance check reads it. It is a ``ValueError``, as every invalid declaration of this module is.
    """


@dataclass(frozen=True)
class HydronicPort:

    """The three outputs of one water circuit, as one end of it declares them.

    ``mass_flow`` is the pump owner's output (kg/s), ``supply_temperature`` and ``return_temperature`` the
    outputs (°C) of the components the water leaves on each leg; each is a name under the component-key rule,
    :meth:`hisim.config.names.NameSyntax.require_component_key`: an identifier, or identifiers joined by ``-`` as
    an assembly member's serialized address is.

    ``role`` is ``OUT`` for the supply owner and ``IN`` for the receiver; it says on which side of that end's
    balance the circuit's heat counts and never changes the heat's sign, which is the circuit's own (negative for
    a cooling circuit). :meth:`heat_in_kilowatt_hour` returns that heat with the circuit's sign whatever the role,
    and the balance check adds it on the ``IN`` side and subtracts it on the ``OUT`` side. The role is
    checked at construction to be ``IN`` or ``OUT``, because :class:`~hisim.loadtypes.EnergyRole` also has
    ``LOSS`` and ``STORED_CHANGE``, which a circuit end cannot be.
    """

    mass_flow: str
    supply_temperature: str
    return_temperature: str
    role: lt.EnergyRole

    #: The roles a circuit can have at one of its ends.
    CIRCUIT_ROLES: ClassVar[Tuple[lt.EnergyRole, ...]] = (lt.EnergyRole.IN, lt.EnergyRole.OUT)

    def __post_init__(self) -> None:
        """Refuse names that are not distinct identifiers and a role other than ``IN`` or ``OUT``.

        Raises:
            HydronicPortError: If a name is no component key (an identifier, or an assembly member's address joined
                by ``-``), two names coincide, or the role is not an
                :class:`~hisim.loadtypes.EnergyRole` ``IN`` or ``OUT``.
        """
        names = {
            "mass_flow": self.mass_flow,
            "supply_temperature": self.supply_temperature,
            "return_temperature": self.return_temperature,
        }
        for field_name, output_name in names.items():
            try:
                NameSyntax.require_component_key(output_name)
            except ValueError as error:
                raise HydronicPortError(f"A hydronic port's {field_name} must name an output: {error}") from error
        if len(set(names.values())) != len(names):
            raise HydronicPortError(
                f"A hydronic port names three distinct outputs, got mass_flow={self.mass_flow!r}, "
                f"supply_temperature={self.supply_temperature!r}, return_temperature={self.return_temperature!r}."
            )
        if not isinstance(self.role, lt.EnergyRole) or self.role not in self.CIRCUIT_ROLES:
            raise HydronicPortError(
                f"A hydronic port's role is EnergyRole.IN (the receiver) or EnergyRole.OUT (the supply owner), "
                f"got {self.role!r}."
            )

    @staticmethod
    def heat_in_kilowatt_hour(
        *,
        mass_flow_in_kg_per_second: float,
        supply_temperature_in_celsius: float,
        return_temperature_in_celsius: float,
        seconds_per_timestep: float,
    ) -> float:
        """Return the heat a circuit's water carries over one step, ``m c (T_sup - T_ret) dt``, in kWh.

        The balance check applies it to the three outputs the port names, so both ends of a circuit derive the same
        number. For example, 0.1 kg/s over 10 K for one hour carries 4.18 kWh. It delegates to
        :func:`hisim.hydronics.circuit_heat_kwh`.

        Args:
            mass_flow_in_kg_per_second: The circuit's mass flow, in kg/s, at least 0.
            supply_temperature_in_celsius: The supply temperature, in °C.
            return_temperature_in_celsius: The return temperature, in °C.
            seconds_per_timestep: The step duration, in s.

        Returns:
            The heat, in kWh; negative for a cooling circuit.

        Raises:
            HydronicsError: If the flow is negative or not finite, a temperature is not finite or the step is not
                positive, as :func:`hisim.hydronics.circuit_heat_kwh` raises it.
        """
        return hydronics.circuit_heat_kwh(
            mass_flow_kg_per_s=mass_flow_in_kg_per_second,
            t_supply_c=supply_temperature_in_celsius,
            t_return_c=return_temperature_in_celsius,
            dt_s=seconds_per_timestep,
        )
