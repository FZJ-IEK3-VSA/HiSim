"""The energy-port contract: what an output of a component is in that component's energy balance (hisim-energy-sankeys-9uoo.1).

An output that carries energy states it with an :class:`EnergyPort`, passed to
:meth:`hisim.component.Component.add_output` as ``energy_port=``: its role in the balance
(:class:`~hisim.loadtypes.EnergyRole`), its carrier (:class:`~hisim.loadtypes.EnergyBalanceCarrier`) and,
for the Sankey, who is on the other side. The balance check and the Sankeys in
:mod:`hisim.postprocessing.energy_balance` read nothing else. The check runs in every simulation and fails
the run on a balance that does not close; a component whose outputs declare no port is *undeclared*, which is
reported, never a failure.

The other side of a port is found from the wiring (complete Sankey spec, §4.3):

* ``environment`` names a boundary node; the port then names no component.
* ``peer_input`` names one of the component's own inputs; the component that input is connected to is
  the peer (a storage's heat from its generator: the generator feeds the storage's mass-flow input).
* otherwise the peer is the component reading ``peer_output`` -- one of the component's own outputs,
  by default the port's own output for a ``PRODUCTION`` port (the building reads the heat a distribution
  system delivers). A port names ``peer_input`` or ``peer_output``, never both.
* A controller is never a peer: it decides, it moves no energy (owner decision D1). Such a component is *exempt*
  (:attr:`hisim.component.Component.ENERGY_PORTS_EXEMPT_BECAUSE`) and the wiring skips it.
* A meter is a node (owner decision Q-J, 2026-10-20): a component that declares what it meters
  (:meth:`hisim.component.Component.metered_exchanges`) stands between the boundary and the devices whose flows it
  reads. A *metered* port (:attr:`EnergyPort.is_metered`: the ``CONSUMPTION`` or ``PRODUCTION`` port of electricity or of a bought
  carrier) watches its own output, whatever its role, and its peer is the component that reads it: a meter of its
  carrier, or a component that moves energy. The wiring is traced through exempt components to the first meter, so
  a heat pump whose electricity the energy manager reads and passes on summed to the electricity meter links to that
  meter. There is no derived junction: what the meter publishes is checked against what it reads, never assumed.
* A port that finds no peer takes its carrier's boundary default (:meth:`EnergyPort.default_boundary`): a bought
  carrier's ``CONSUMPTION`` port that carrier's supply node ("natural gas supply"), ambient heat and the sun their nodes,
  and every ``LOSS`` port ``OUTDOORS``. Electricity and heat have no default; such a port is *unlinked*. A metered
  port whose peer is no meter and no component is *unmetered*, which the balance reports.

A transfer between two components that both declare ports is booked by both: the sender's ``PRODUCTION`` port whose
peer is the receiver, and the receiver's ``CONSUMPTION`` port of the same carrier whose peer is the sender. The check
compares the two totals and fails the run when they disagree beyond its tolerance, and when only one side books
the transfer (owner, 2026-10-02: always fail hard). A one-sided booking is allowed only toward a component that
declares no ports at all, a meter, a boundary node or nobody.

A power output (W, kW) is converted to energy with the timestep, an energy output (Wh, kWh, kWh per timestep, J,
kJ) is taken per step as it is; any other unit is refused when the output is declared
(:meth:`EnergyPort.validate_unit`).

Hydronic circuits
-----------------

A water circuit carries no energy output: it is a mass flow, a supply temperature and a return temperature, and
its heat is derived from them, ``m c (T_sup - T_ret) dt``. A :class:`HydronicPort` names those three quantities and
derives the heat with :func:`hisim.hydronics.circuit_power_w`, the one rule. Both ends of a circuit declare it,
the supply owner with role ``PRODUCTION`` and the receiver with role ``CONSUMPTION``, each through
:meth:`hisim.component.Component.add_hydronic_port` and by its own names: a quantity the end publishes is one of its
outputs, a quantity it reads is one of its inputs, and the balance check follows an input to the output connected
to it (:class:`CircuitEnd`). So both ends of a circuit resolve to the same three columns of the result table, the
check derives the circuit's heat once for both, and a circuit balances by construction; a mismatch can only come
from inside a component. The rules the check applies (:mod:`hisim.postprocessing.energy_balance.ports`):

* A port names no peer. The two ends come from the wiring: the component that reads the circuit's mass-flow
  output is the other end of the mass flow's owner, and the owner is the other end of a component that reads it. A
  flow nobody reads (a boiler whose distribution system pumps a flow of its own) has as its other end the component
  that publishes or reads the circuit's temperatures.
* **One reader.** A mass-flow output read by more than one component that declares a ``HydronicPort`` on it
  fails the run: one flow cannot deliver its heat twice, and a split is a valve component with one circuit per
  branch.
* **Both ends or neither.** A circuit whose one end declares a ``HydronicPort`` while the other end, a
  component that declares ports, does not declare the matching one (the same three outputs, the other role) fails
  the run. A circuit whose other end declares no ports at all is drawn to that end as undeclared, as for an
  :class:`EnergyPort`.
* **An idle end.** An end whose mass flow it reads through an input that is not connected moves no water in this
  run and is left out, as a storage's second generator circuit without a second generator is. An end that pumps
  water into a circuit whose other quantities it cannot read fails the run.

The rules that need no value of the run fail it right after the build, before it simulates
(:meth:`hisim.postprocessing.energy_balance.ports.Wiring.refuse_a_broken_wiring`), together with rule (a) of the
owner's decision of 2026-10-02 for an :class:`EnergyPort`: a ``PRODUCTION`` port that is not metered and names no
``peer_input`` or ``peer_output`` must not be read by more than one component that declares ports.

The :class:`EnergyPort` peer pointers remain for the carriers that are not water: fuel, electricity, ambient
heat and solar, and for heat that leaves a component other than on a circuit (a tank's tap, a distribution
system's heat to the building).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Dict, FrozenSet, Mapping, Optional, Tuple

import numpy as np

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
    #: A boundary node as the peer, instead of a component (``lt.EnvironmentNode``).
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
        """Refuse a port whose role or carrier is not one of the enums, that names two peers, or that skips a meter.

        A metered port (``CONSUMPTION`` or ``PRODUCTION`` of electricity or a bought carrier) names no ``environment``: its peer is
        the meter that reads it, and a boundary node named on it would take its flow past every meter (owner decision
        Q-J). The grid nodes are no port's environment at all: "grid import" and "grid export" are where a meter's
        published exchange comes from and goes to (:meth:`MeteredExchange.import_node`).

        Raises:
            ValueError: For any of the above, naming the port's role, carrier and environment.
        """
        lt.EnergyRole(self.role)
        lt.EnergyBalanceCarrier(self.carrier)
        if self.environment is not None and (self.peer_input is not None or self.peer_output is not None):
            raise ValueError("An energy port's peer is either an environment node or a component, not both.")
        if self.environment in (lt.EnvironmentNode.GRID_IMPORT, lt.EnvironmentNode.GRID_EXPORT):
            raise ValueError(
                f"An energy port cannot name {self.environment.value!r} as its environment: the grid is reached only "
                "through the electricity meter's published exchange."
            )
        if (
            self.environment is not None
            and self.carrier in self.METERED_CARRIERS
            and self.role in (lt.EnergyRole.CONSUMPTION, lt.EnergyRole.PRODUCTION)
        ):
            raise ValueError(
                f"A {self.role.value} port of {self.carrier.value} is metered and names no environment "
                f"({self.environment.value!r}): its peer is the meter that reads it."
            )
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

    #: The carriers bought at the system boundary: a ``CONSUMPTION`` port of one takes it from the carrier's supply node.
    SUPPLIED_CARRIERS: ClassVar[FrozenSet[lt.EnergyBalanceCarrier]] = frozenset(
        {
            lt.EnergyBalanceCarrier.NATURAL_GAS,
            lt.EnergyBalanceCarrier.HEATING_OIL,
            lt.EnergyBalanceCarrier.PELLETS,
            lt.EnergyBalanceCarrier.WOOD_CHIPS,
            lt.EnergyBalanceCarrier.HYDROGEN,
            lt.EnergyBalanceCarrier.DIESEL,
            lt.EnergyBalanceCarrier.DISTRICT_HEAT,
        }
    )

    #: The carriers whose flows a meter counts at the system boundary: electricity and every bought carrier. Their
    #: ``CONSUMPTION`` and ``PRODUCTION`` ports are metered (:attr:`is_metered`).
    METERED_CARRIERS: ClassVar[FrozenSet[lt.EnergyBalanceCarrier]] = SUPPLIED_CARRIERS | {
        lt.EnergyBalanceCarrier.ELECTRICITY
    }

    @staticmethod
    def supply_node_name(carrier: lt.EnergyBalanceCarrier) -> str:
        """The name of the boundary node a bought carrier comes from.

        For example, ``NATURAL_GAS`` comes from "natural gas supply" and ``DISTRICT_HEAT`` from "district heat
        supply". The names are derived from the carrier, so a new fuel needs no new boundary node.

        Args:
            carrier: The bought carrier.

        Returns:
            The carrier's value with spaces for underscores, followed by "supply".
        """
        return f"{carrier.value.replace('_', ' ')} supply"

    def default_boundary(self) -> Optional[str]:
        """The boundary node this port goes to when the wiring names no peer for it, or None when it has none.

        The table of the complete Sankey spec, §4.3: a ``LOSS`` port, a bought carrier's ``CONSUMPTION`` port, an ambient-heat
        ``CONSUMPTION`` or ``PRODUCTION`` port and a solar ``CONSUMPTION`` port have one; an electricity port and a heat port have none, and
        such a port with no peer is unlinked. For example, a boiler's natural-gas ``CONSUMPTION`` port that no meter reads goes
        to "natural gas supply", a heat pump's ambient-heat ``CONSUMPTION`` port to "ambient heat", a collector's solar ``CONSUMPTION``
        port to "sun" and every ``LOSS`` port to "outdoors". A port that names its ``environment`` goes there.

        Returns:
            The boundary node's name, or None for a port whose carrier has no boundary default for its role.
        """
        if self.environment is not None:
            return self.environment.value
        if self.role == lt.EnergyRole.LOSS:
            return lt.EnvironmentNode.OUTDOORS.value
        if self.role == lt.EnergyRole.CONSUMPTION and self.carrier in self.SUPPLIED_CARRIERS:
            return self.supply_node_name(self.carrier)
        if self.carrier == lt.EnergyBalanceCarrier.AMBIENT_HEAT and self.role in (lt.EnergyRole.CONSUMPTION, lt.EnergyRole.PRODUCTION):
            return lt.EnvironmentNode.AMBIENT_HEAT.value
        if self.carrier == lt.EnergyBalanceCarrier.SOLAR and self.role == lt.EnergyRole.CONSUMPTION:
            return lt.EnvironmentNode.SOLAR.value
        return None

    @property
    def is_metered(self) -> bool:
        """Whether a meter is to count this port's flow: a ``CONSUMPTION`` or ``PRODUCTION`` port of electricity or a bought carrier.

        For example, a heat pump's electricity ``CONSUMPTION`` port and a boiler's natural-gas ``CONSUMPTION`` port are metered, a
        battery's electricity ``LOSS`` port is not. Such a port names no ``environment`` (:meth:`__post_init__`).
        """
        return self.carrier in self.METERED_CARRIERS and self.role in (lt.EnergyRole.CONSUMPTION, lt.EnergyRole.PRODUCTION)

    @property
    def names_a_component(self) -> bool:
        """Whether the port names a peer through the wiring.

        A ``PRODUCTION`` port and a metered ``CONSUMPTION`` port (:attr:`is_metered`) name one implicitly through their own output,
        which the receiver or the meter reads; any other ``LOSS`` or ``CONSUMPTION`` port only when it names ``peer_input`` or
        ``peer_output``. A port with an ``environment`` names none.
        """
        if self.environment is not None:
            return False
        if self.peer_input is not None or self.peer_output is not None:
            return True
        return self.role == lt.EnergyRole.PRODUCTION or self.is_metered

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


@dataclass(frozen=True)
class MeteredExchange:

    """What a meter publishes as the exchange of one carrier at the system boundary (complete Sankey spec, §4.4).

    A meter that declares an exchange is a node of the energy balance (owner decision Q-J): the published import
    enters it from the carrier's boundary node, the published export leaves it to the export node, and the metered
    ports of the devices it reads (:attr:`EnergyPort.is_metered`) link to it. Per step, what it publishes, import
    minus export, must equal what it reads, the consumers' ``CONSUMPTION`` ports minus the producers' ``PRODUCTION`` ports; a gap is
    the meter's residual, never absorbed. For example, the electricity meter declares
    ``MeteredExchange(ELECTRICITY, "ElectricityFromGridInWatt", "ElectricityToGridInWatt")``. A meter states this
    about itself (:meth:`hisim.component.Component.metered_exchanges`), so the balance depends only on its published
    outputs.

    Attributes:
        carrier: The carrier the meter counts.
        import_output: The meter's output that counts what enters the house, a power or an energy.
        export_output: The meter's output that counts what leaves it, or None for a carrier that is only bought.
    """

    carrier: lt.EnergyBalanceCarrier
    import_output: str
    export_output: Optional[str] = None

    def __post_init__(self) -> None:
        """Refuse an exchange of a carrier no meter counts, or whose import and export are the same output.

        Raises:
            ValueError: If the carrier is not electricity or a bought carrier (:attr:`EnergyPort.METERED_CARRIERS`),
                or if ``import_output`` and ``export_output`` coincide: one output cannot be both directions.
        """
        if lt.EnergyBalanceCarrier(self.carrier) not in EnergyPort.METERED_CARRIERS:
            raise ValueError(
                f"A metered exchange counts electricity or a bought carrier, not {self.carrier.value}: no meter "
                "stands at the system boundary of it."
            )
        if self.export_output is not None and self.export_output == self.import_output:
            raise ValueError(
                f"A metered exchange names two outputs, one per direction; got {self.import_output!r} for both."
            )

    def import_node(self) -> str:
        """The boundary node the import comes from: "grid import" for electricity, else the carrier's supply node.

        For example, a gas meter's import comes from "natural gas supply" (:meth:`EnergyPort.supply_node_name`).
        """
        if self.carrier == lt.EnergyBalanceCarrier.ELECTRICITY:
            return lt.EnvironmentNode.GRID_IMPORT.value
        return EnergyPort.supply_node_name(self.carrier)

    def export_node(self) -> str:
        """The boundary node the export goes to: "grid export" for electricity, else "<carrier> export"."""
        if self.carrier == lt.EnergyBalanceCarrier.ELECTRICITY:
            return lt.EnvironmentNode.GRID_EXPORT.value
        return f"{self.carrier.value.replace('_', ' ')} export"


class HydronicPortError(ValueError):

    """A hydronic port whose output names or role cannot describe a circuit.

    Raised when the port is declared, so a component with a malformed port fails when it is built rather than when
    the balance check reads it. It is a ``ValueError``, as every invalid declaration of this module is.
    """


@dataclass(frozen=True)
class HydronicPort:

    """The three quantities of one water circuit, as one end of it names them.

    ``mass_flow`` is the circuit's mass flow (kg/s), which the pump owner publishes, ``supply_temperature`` and
    ``return_temperature`` the temperatures (°C) of the water on each leg, each published by the component the water
    leaves. The declaring end names each by one of its own outputs or inputs (:class:`CircuitEnd` says which); an
    input stands for the output connected to it. Each name follows the component-key rule,
    :meth:`hisim.config.names.NameSyntax.require_component_key`: an identifier, or identifiers joined by ``-`` as
    an assembly member's serialized address is. For example, a buffer's end of its boiler's circuit names its inputs
    ``WaterMassFlowRateFromHeatGenerator`` and ``WaterTemperatureFromHeatGenerator`` and its output
    ``WaterTemperatureToHeatGenerator``; the boiler's end names its outputs ``WaterOutputMassFlowSh`` and
    ``WaterOutputTemperatureSh`` and its input ``WaterInputTemperatureSh``. Both resolve to the same three columns.

    ``carrier`` is the heat the circuit carries, ``SPACE_HEATING_HEAT`` or ``DOMESTIC_HOT_WATER_HEAT``, so that the
    Sankey knows which heat a circuit moves (complete Sankey spec, §4.2).

    ``role`` is ``PRODUCTION`` for the supply owner and ``CONSUMPTION`` for the receiver; it says on which side of that end's
    balance the circuit's heat counts and never changes the heat's sign, which is the circuit's own (negative for
    a cooling circuit). :meth:`heat_in_kilowatt_hour` returns that heat with the circuit's sign whatever the role,
    and the balance check adds it on the ``CONSUMPTION`` side and subtracts it on the ``PRODUCTION`` side. The role is
    checked at construction to be ``CONSUMPTION`` or ``PRODUCTION``, because :class:`~hisim.loadtypes.EnergyRole` also has
    ``LOSS`` and ``STORED_CHANGE``, which a circuit end cannot be.
    """

    mass_flow: str
    supply_temperature: str
    return_temperature: str
    role: lt.EnergyRole
    carrier: lt.EnergyBalanceCarrier

    #: The roles a circuit can have at one of its ends.
    CIRCUIT_ROLES: ClassVar[Tuple[lt.EnergyRole, ...]] = (lt.EnergyRole.CONSUMPTION, lt.EnergyRole.PRODUCTION)
    #: The heat a circuit can carry; a cooling circuit keeps its circuit's carrier and its heat turns negative.
    CIRCUIT_CARRIERS: ClassVar[Tuple[lt.EnergyBalanceCarrier, ...]] = (
        lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT,
        lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT,
    )

    def __post_init__(self) -> None:
        """Refuse names that are not distinct identifiers, a role other than ``CONSUMPTION`` or ``PRODUCTION`` and a foreign carrier.

        Raises:
            HydronicPortError: If a name is no component key (an identifier, or an assembly member's address joined
                by ``-``), two names coincide, the role is not an :class:`~hisim.loadtypes.EnergyRole` ``CONSUMPTION`` or
                ``PRODUCTION``, or the carrier is not ``SPACE_HEATING_HEAT`` or ``DOMESTIC_HOT_WATER_HEAT``.
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
                f"A hydronic port's role is EnergyRole.CONSUMPTION (the receiver) or EnergyRole.PRODUCTION (the supply owner), "
                f"got {self.role!r}."
            )
        if not isinstance(self.carrier, lt.EnergyBalanceCarrier) or self.carrier not in self.CIRCUIT_CARRIERS:
            raise HydronicPortError(
                f"A hydronic port carries space-heating or hot-water heat "
                f"({', '.join(carrier.name for carrier in self.CIRCUIT_CARRIERS)}), got {self.carrier!r}."
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
        number. For example, 0.1 kg/s over 10 K for one hour carries 4.18 kWh. The heat is
        :func:`hisim.hydronics.circuit_power_w` times the step, converted from J to kWh in that order, so it agrees to
        the last bit with the power a component books for the same three values.

        Args:
            mass_flow_in_kg_per_second: The circuit's mass flow, in kg/s, at least 0.
            supply_temperature_in_celsius: The supply temperature, in °C.
            return_temperature_in_celsius: The return temperature, in °C.
            seconds_per_timestep: The step duration, in s.

        Returns:
            The heat, in kWh; negative for a cooling circuit.

        Raises:
            NonPositiveTimestepError: If the step is not positive.
            NegativeMassFlowError: If the flow is negative.
        """
        if seconds_per_timestep <= 0.0:
            raise hydronics.NonPositiveTimestepError(f"seconds_per_timestep must be > 0 s, got {seconds_per_timestep!r}.")
        power_in_watt = hydronics.circuit_power_w(
            mass_flow_kg_per_s=mass_flow_in_kg_per_second,
            t_supply_c=supply_temperature_in_celsius,
            t_return_c=return_temperature_in_celsius,
        )
        return power_in_watt * seconds_per_timestep / hydronics.UnitConversion.JOULES_PER_KILOWATT_HOUR

    @staticmethod
    def heat_in_kilowatt_hours_per_step(
        *,
        mass_flow_in_kg_per_second: np.ndarray,
        supply_temperature_in_celsius: np.ndarray,
        return_temperature_in_celsius: np.ndarray,
        seconds_per_timestep: float,
    ) -> np.ndarray:
        """Return the heat a circuit's water carries in every step of a run, ``m c (T_sup - T_ret) dt``, in kWh.

        The rule of :meth:`heat_in_kilowatt_hour`, the same operations in the same order, applied to the three
        columns of the result table at once; the balance check derives every circuit's heat with it. For example,
        flows of 0.1 and 0 kg/s over 10 K for one hour carry 4.18 and 0 kWh.

        Args:
            mass_flow_in_kg_per_second: The circuit's mass flow per step, in kg/s, at least 0 in every step.
            supply_temperature_in_celsius: The supply temperature per step, in °C.
            return_temperature_in_celsius: The return temperature per step, in °C.
            seconds_per_timestep: The step duration, in s.

        Returns:
            The heat per step, in kWh; negative in a step in which the circuit cools.

        Raises:
            HydronicsError: If a step's flow is negative (``NegativeMassFlowError``, naming the first such step) or
                the step is not positive (``NonPositiveTimestepError``), as :meth:`heat_in_kilowatt_hour` and
                :func:`hisim.hydronics.circuit_power_w` raise them for one step.
        """
        mass_flow = np.asarray(mass_flow_in_kg_per_second, dtype=float)
        if seconds_per_timestep <= 0.0:
            raise hydronics.NonPositiveTimestepError(f"seconds_per_timestep must be > 0 s, got {seconds_per_timestep!r}.")
        negative = mass_flow < 0.0
        if negative.any():
            first = int(np.argmax(negative))
            raise hydronics.NegativeMassFlowError(
                f"mass_flow_kg_per_s must be >= 0 kg/s, got {mass_flow[first]!r} in step {first}."
            )
        supply = np.asarray(supply_temperature_in_celsius, dtype=float)
        returned = np.asarray(return_temperature_in_celsius, dtype=float)
        power_in_watt = mass_flow * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * (supply - returned)
        return np.asarray(
            power_in_watt * seconds_per_timestep / hydronics.UnitConversion.JOULES_PER_KILOWATT_HOUR, dtype=float
        )


@dataclass(frozen=True)
class CircuitEnd:

    """One end of a water circuit as a component declares it: its :class:`HydronicPort` and which names are inputs.

    A component publishes some of a circuit's three quantities and reads the others, so each name of its port is one
    of its outputs or one of its inputs; ``input_legs`` lists the legs (:attr:`LEGS`) it names by an input, and the
    balance check follows each such input to the output connected to it. A component declares an end with
    :meth:`hisim.component.Component.add_hydronic_port`, which builds this from its channels. For example, a buffer's
    end of its boiler's circuit reads the mass flow and the supply temperature, ``input_legs={"mass_flow",
    "supply_temperature"}``, and publishes the return.

    Attributes:
        port: The circuit's three names, role and carrier.
        input_legs: The legs the component names by one of its inputs; the others are its outputs.
    """

    port: HydronicPort
    input_legs: FrozenSet[str] = frozenset()

    #: The three legs of a circuit, as the fields of :class:`HydronicPort` name them.
    LEGS: ClassVar[Tuple[str, ...]] = ("mass_flow", "supply_temperature", "return_temperature")

    def __post_init__(self) -> None:
        """Refuse an input leg that is not one of :attr:`LEGS`.

        Raises:
            HydronicPortError: If ``input_legs`` names something other than a leg.
        """
        unknown = set(self.input_legs) - set(self.LEGS)
        if unknown:
            raise HydronicPortError(f"A circuit end's input legs are among {self.LEGS}, got {sorted(unknown)}.")

    def name_of(self, leg: str) -> str:
        """The component's own output or input that names ``leg``, for example ``"WaterOutputMassFlowSh"``."""
        name: str = getattr(self.port, leg)
        return name

    def is_input(self, leg: str) -> bool:
        """Whether the component names ``leg`` by one of its inputs rather than one of its outputs."""
        return leg in self.input_legs
