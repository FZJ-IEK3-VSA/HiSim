"""The energy-port contract: what a channel of a component is in that component's energy balance (hisim-energy-sankeys-9uoo.1).

A channel that carries energy states it with an :class:`EnergyPort`, passed to
:meth:`hisim.component.Component.add_output` or :meth:`hisim.component.Component.add_input` as ``energy_port=``: its
role in the balance (:class:`~hisim.loadtypes.EnergyRole`) and its carrier
(:class:`~hisim.loadtypes.EnergyBalanceCarrier`). The balance check and the Sankeys in
:mod:`hisim.postprocessing.energy_balance` read nothing else. The check runs in every simulation and fails
the run on a balance that does not close; a component whose channels declare no port is *undeclared*, which is
reported, never a failure.

A port names no other component; the other side is found from the wiring (complete Sankey spec, §4.3, and §3.7 of
``roadmap/fluid_loops_spec.md``, decision D9):

* ``environment`` names a boundary node; the port then names no component.
* An *input port* books the energy that arrives on its input: its series is the column of the output connected to
  the input, and its peer is that output's component. For example, a building that reads a distribution system's
  heat through an input carrying ``EnergyPort(CONSUMPTION, SPACE_HEATING_HEAT)`` books that heat from the
  distribution system. An input port is a ``CONSUMPTION`` or ``PRODUCTION`` port of a carrier no meter counts
  (:meth:`EnergyPort.check_fits_an_input`); an optional input port that is not connected moves nothing and is left
  out.
* An *output port* that is read through an input carrying a port it pairs with (:meth:`EnergyPort.pairs_with`: the
  same carrier, the other of ``CONSUMPTION`` and ``PRODUCTION``) has that reader as its peer. Otherwise a
  ``PRODUCTION`` port's peer is the component that reads its output (the building reads the heat a distribution
  system delivers).
* A controller is never a peer: it decides, it moves no energy (owner decision D1). Such a component is *exempt*
  (:attr:`hisim.component.Component.ENERGY_PORTS_EXEMPT_BECAUSE`) and the wiring skips it.
* A meter is a node (owner decision Q-J, 2026-10-20): a component that declares what it meters
  (:meth:`hisim.component.Component.metered_exchanges`) stands between the boundary and the devices whose flows it
  reads. A *metered* port (:attr:`EnergyPort.is_metered`: the ``CONSUMPTION`` or ``PRODUCTION`` port of electricity
  or of a bought carrier) is an output port; it watches its own output, whatever its role, and its peer is the
  component that reads it: a meter of its carrier, or a component that moves energy. The wiring is traced through
  exempt components to the first meter, so a heat pump whose electricity the energy manager reads and passes on
  summed to the electricity meter links to that meter. There is no derived junction: what the meter publishes is
  checked against what it reads, never assumed.
* A port that finds no peer takes its carrier's boundary default (:meth:`EnergyPort.default_boundary`): a bought
  carrier's ``CONSUMPTION`` port that carrier's supply node ("natural gas supply"), ambient heat and the sun their
  nodes, and every ``LOSS`` port ``OUTDOORS``. Electricity and heat have no default; such a port is *unlinked*. A
  metered port whose peer is no meter and no component is *unmetered*, which the balance reports.

A transfer between two components that both declare ports is booked by both: the sender's ``PRODUCTION`` port whose
peer is the receiver, and the receiver's ``CONSUMPTION`` port of the same carrier whose peer is the sender. The check
compares the two totals and fails the run when they disagree beyond its tolerance, and when only one side books
the transfer (owner, 2026-10-02: always fail hard). A one-sided booking is allowed only toward a component that
declares no ports at all, a meter, a boundary node or nobody. A transfer on one wire, the sender's output port read
through the receiver's input port, resolves to one column at both ends and so agrees by construction; the check
still proves each side's own balance.

A power output (W, kW) is converted to energy with the timestep, an energy output (Wh, kWh, kWh per timestep, J,
kJ) is taken per step as it is; any other unit is refused when the output is declared
(:meth:`EnergyPort.validate_unit`).

Fluid loops
-----------

A water loop carries no energy output: each of its members publishes or reads a mass flow and two temperatures, and
the loop's heat is derived from them, ``m c (T_out - T_in) dt``. A component says which of its channels belong to a
loop with a tag on the channel itself, passed to ``add_input`` or ``add_output`` as ``fluid_loop=``
(:class:`FluidLoopChannel`): which of the component's loops (:class:`FluidLoop`) and which of the four quantities
(:class:`LoopQuantity`). The component's tagged channels, grouped by loop, are its *port sets*
(:meth:`hisim.component.Component.port_sets`); nothing else declares a loop (D8 of
``roadmap/hydraulic_circuits_spec.md``). Each :class:`FluidLoop` states the facts the wiring cannot tell: the heat
the loop carries while it heats (``carrier``), its fluid (``fluid``, which gives ``c``), whether this member supplies
the loop (``supplies``, its generator end), whether it pumps it (``pumps``) and whether it lets the loop cool
(``may_cool``). For example, a buffer reads its generator's flow and supply and publishes the return::

    self.generator_loop = FluidLoop(
        "generator", carrier=lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT, fluid=LoopFluid.WATER,
        supplies=False, may_cool=True, pumps=False,
    )
    self.add_input(..., lt.Units.KG_PER_SEC, True, fluid_loop=self.generator_loop.flow_in)

The balance check (:mod:`hisim.postprocessing.energy_balance.ports`) follows each tagged input to the output
connected to it, so both members of a loop resolve to the same flow and the same two temperatures, derives the
loop's heat once from them and books it by the supplier's sign (§3.4 of the spec): in a step in which the supplier
heats the water, the loop's ``carrier`` goes from the supplier to the other member; in a step in which it cools the
water, ``COOLING`` goes from the other member to the supplier. The rules it applies:

* A loop names no peer. The two members come from the wiring: the component that reads a member's mass-flow output
  is its other member, and the owner of the flow a member reads is its other member. A flow nobody reads (a boiler
  whose distribution system pumps a flow of its own) has as its other member the component that publishes or reads
  the loop's temperatures.
* **One reader.** A mass-flow output read by more than one component that tags a loop on it fails the run: one flow
  cannot deliver its heat twice, and a split is a valve component with one loop per branch.
* **Every member or none.** A loop whose one member tags it while the other member, a component that declares
  ports, does not tag the same loop fails the run. A loop whose other member declares no ports at all is drawn to
  that member as undeclared, as for an :class:`EnergyPort`.
* **Exactly one supplier.** Of the two members of a loop exactly one states ``supplies=True``.
* **An idle member.** A member whose mass flow it reads through an input that is not connected moves no water in
  this run and is left out, as a storage's second generator loop without a second generator is. A member that
  pumps water into a loop whose other quantities it cannot read fails the run.

The rules that need no value of the run fail it right after the build, before it simulates
(:meth:`hisim.postprocessing.energy_balance.ports.Wiring.refuse_a_broken_wiring`), together with two rules of the
:class:`EnergyPort` wiring: rule (a) of the owner's decision of 2026-10-02, a ``PRODUCTION`` port that is not metered
and is read by more than one component that declares ports is refused unless exactly one reader tags the reading
input with a port it pairs with; and an input port must pair with the port of the output it reads, if that output
declares one.

:class:`EnergyPort` stays for everything a loop does not carry: fuel, electricity, ambient heat and solar, and heat
that leaves a component other than on a loop (a tank's tap, a distribution system's heat to the building).
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Dict, FrozenSet, Mapping, Optional, Tuple

from hisim import hydronics
from hisim import loadtypes as lt


@dataclass(frozen=True)
class EnergyPort:
    """Role and carrier of one energy-carrying output or input; the wiring finds its peer."""

    role: lt.EnergyRole
    carrier: lt.EnergyBalanceCarrier
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
        """Refuse a port whose role or carrier is not one of the enums, or that skips a meter.

        A metered port (``CONSUMPTION`` or ``PRODUCTION`` of electricity or a bought carrier) names no ``environment``: its peer is
        the meter that reads it, and a boundary node named on it would take its flow past every meter (owner decision
        Q-J). The grid nodes are no port's environment at all: "grid import" and "grid export" are where a meter's
        published exchange comes from and goes to (:meth:`MeteredExchange.import_node`).

        Raises:
            ValueError: For any of the above, naming the port's role, carrier and environment.
        """
        lt.EnergyRole(self.role)
        lt.EnergyBalanceCarrier(self.carrier)
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

    def check_fits_an_input(self, component_name: str, field_name: str) -> None:
        """Refuse this port on an input it cannot describe: an input port books energy exchanged with its source.

        An input's other side is the component whose output it reads, so the port is a ``CONSUMPTION`` (energy
        arriving from the source) or a ``PRODUCTION`` (energy given to the source, as cooling leaves a building) and
        names no ``environment``. A metered carrier is booked on the output a meter reads (owner decision Q-J), never
        on an input. For example, ``EnergyPort(CONSUMPTION, SPACE_HEATING_HEAT)`` fits an input, while
        ``EnergyPort(LOSS, SPACE_HEATING_HEAT)`` and ``EnergyPort(CONSUMPTION, ELECTRICITY)`` do not.

        Args:
            component_name: The input's component, for the message.
            field_name: The input's name, for the message.

        Raises:
            ValueError: If the role is ``LOSS`` or ``STORED_CHANGE``, the port names an environment or it is metered.
        """
        where = f"The energy port of the input {component_name}.{field_name} ({self.role.value}, {self.carrier.value})"
        if self.role not in (lt.EnergyRole.CONSUMPTION, lt.EnergyRole.PRODUCTION):
            raise ValueError(f"{where} is no exchange with its source: an input port is consumption or production.")
        if self.environment is not None:
            raise ValueError(
                f"{where} names the environment {self.environment.value!r}; an input port's peer is its source."
            )
        if self.is_metered:
            raise ValueError(f"{where} is metered: a metered carrier is booked on the output its meter reads.")

    def pairs_with(self, other: "EnergyPort") -> bool:
        """Whether this port and ``other`` book the two ends of one transfer: the same carrier, opposite exchange roles.

        For example, a distribution system's ``EnergyPort(PRODUCTION, SPACE_HEATING_HEAT)`` pairs with a building's
        ``EnergyPort(CONSUMPTION, SPACE_HEATING_HEAT)``, and its ``EnergyPort(CONSUMPTION, COOLING)`` with the
        building's ``EnergyPort(PRODUCTION, COOLING)``; two ``PRODUCTION`` ports, a ``LOSS`` port or two carriers never
        pair.
        """
        exchange = {lt.EnergyRole.CONSUMPTION, lt.EnergyRole.PRODUCTION}
        return self.carrier == other.carrier and {self.role, other.role} == exchange

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

    #: The boundary node of an ambient-heat port by its role: heat taken from the outside air comes from its source node,
    #: heat rejected to it goes to its sink node (owner decision Q-M of the complete Sankey spec).
    AMBIENT_HEAT_NODES: ClassVar[Mapping[lt.EnergyRole, lt.EnvironmentNode]] = MappingProxyType(
        {
            lt.EnergyRole.CONSUMPTION: lt.EnvironmentNode.AMBIENT_HEAT_SOURCE,
            lt.EnergyRole.PRODUCTION: lt.EnvironmentNode.AMBIENT_HEAT_SINK,
        }
    )

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
        to "natural gas supply", a heat pump's ambient-heat ``CONSUMPTION`` port to "ambient heat source", an air conditioner's
        ambient-heat ``PRODUCTION`` port (the heat it rejects while cooling) to "ambient heat sink", a collector's solar
        ``CONSUMPTION`` port to "sun" and every ``LOSS`` port to "outdoors". The source and the sink of ambient heat are two
        nodes (owner decision Q-M of the complete Sankey spec), so the Sankey draws the one left and the other right. A
        port that names its ``environment`` goes there.

        Returns:
            The boundary node's name, or None for a port whose carrier has no boundary default for its role.
        """
        if self.environment is not None:
            return self.environment.value
        if self.role == lt.EnergyRole.LOSS:
            return lt.EnvironmentNode.OUTDOORS.value
        if self.role == lt.EnergyRole.CONSUMPTION and self.carrier in self.SUPPLIED_CARRIERS:
            return self.supply_node_name(self.carrier)
        if self.carrier == lt.EnergyBalanceCarrier.AMBIENT_HEAT and self.role in self.AMBIENT_HEAT_NODES:
            return self.AMBIENT_HEAT_NODES[self.role].value
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

    def as_dict(self) -> Dict[str, object]:
        """The port as the balance report lists it."""
        return {
            "role": self.role.value,
            "carrier": self.carrier.value,
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


@enum.unique
class LoopQuantity(str, enum.Enum):

    """Which of a loop member's four quantities a channel is: what the fluid does at it.

    A member of a fluid loop reads the fluid arriving and publishes the fluid leaving, each as a mass flow and a
    temperature: ``FLOW_IN`` and ``TEMPERATURE_IN`` are inputs, ``FLOW_OUT`` and ``TEMPERATURE_OUT`` outputs. For
    example, a boiler's supply temperature is its ``TEMPERATURE_OUT`` on its space-heating loop, and the return it reads
    from the buffer its ``TEMPERATURE_IN``.
    """

    FLOW_IN = "flow_in"
    FLOW_OUT = "flow_out"
    TEMPERATURE_IN = "temperature_in"
    TEMPERATURE_OUT = "temperature_out"

    @property
    def is_input(self) -> bool:
        """Whether a channel of this quantity is an input: the fluid arriving, its flow or its temperature."""
        return self in (LoopQuantity.FLOW_IN, LoopQuantity.TEMPERATURE_IN)

    @property
    def unit(self) -> lt.Units:
        """The unit of a channel of this quantity: kg/s for a flow, °C for a temperature."""
        if self in (LoopQuantity.FLOW_IN, LoopQuantity.FLOW_OUT):
            return lt.Units.KG_PER_SEC
        return lt.Units.CELSIUS


@enum.unique
class LoopFluid(str, enum.Enum):

    """The fluid a loop carries, which gives the specific heat its heat is derived with.

    Water in every heating and hot-water loop, brine in a ground-source collector loop; air, for ventilation, is to
    follow. For example, ``LoopFluid.WATER.specific_heat_j_per_kg_k`` is 4180.0, the value every hydronic component
    books with (:class:`hisim.hydronics.Water`).
    """

    WATER = "water"
    BRINE = "brine"

    @property
    def specific_heat_j_per_kg_k(self) -> float:
        """The fluid's specific heat capacity, in J/(kg K), from :mod:`hisim.hydronics`, where its source is stated."""
        if self is LoopFluid.BRINE:
            return hydronics.Brine.SPECIFIC_HEAT_J_PER_KG_K
        return hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K


class FluidLoopError(ValueError):

    """A fluid-loop declaration that cannot describe a loop, refused when the component declares it.

    Raised by :class:`FluidLoop` for a malformed loop, by ``add_input`` and ``add_output`` for a loop tag that does not
    fit its channel, and when a component's tagged channels are grouped into port sets
    (:meth:`hisim.component.Component.port_sets`) for a loop tagged by half. It is a ``ValueError``, as every invalid
    declaration of this module is.
    """


@dataclass(frozen=True)
class FluidLoop:

    """One fluid loop as one of its members names it: its name, its heat, its fluid and what this member is to it.

    A component creates one per loop it can be a member of and tags each channel of that loop with it
    (:class:`FluidLoopChannel`, through :attr:`flow_in`, :attr:`flow_out`, :attr:`temperature_in` and
    :attr:`temperature_out`). The two members of a loop each state their own: the balance check books the loop's heat
    by its supplier, and the build refuses a loop with no or two suppliers. For example, a buffer's loop to its
    generator::

        FluidLoop("generator", carrier=lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT, fluid=LoopFluid.WATER,
                  supplies=False, may_cool=True, pumps=False)

    Attributes:
        name: The component's own name for the loop, free text, for example ``"space_heating"`` or
            ``"apartment_3"``: a component may have as many loops as its configuration says. It must differ from the
            names of the component's other loops.
        carrier: The heat the loop carries while it heats, ``SPACE_HEATING_HEAT`` or ``DOMESTIC_HOT_WATER_HEAT``, or
            ``AMBIENT_HEAT`` on a collector loop that brings the heat of the ground or the outside air to a heat pump
            (``SimpleHeatSource``'s brine loop); while it cools, the balance books ``COOLING``.
        fluid: The fluid the loop carries, which gives its specific heat.
        supplies: Whether this member is the loop's generator end, the one whose heat the other member receives.
        may_cool: Whether this member lets the loop remove heat from the room side; a loop may cool only if every
            member allows it. Stage 2 of the spec fails a run that cools a loop that may not.
        pumps: Whether this member moves the loop's fluid; None where the component can do either and does not state
            it yet (stage 1b of the spec states it on every loop).
    """

    name: str
    _: dataclasses.KW_ONLY
    carrier: lt.EnergyBalanceCarrier
    fluid: LoopFluid
    supplies: bool
    may_cool: bool
    pumps: Optional[bool] = None

    #: The heat a loop can carry while it heats: space heating, hot water, or the environment's heat on a collector loop.
    LOOP_CARRIERS: ClassVar[Tuple[lt.EnergyBalanceCarrier, ...]] = (
        lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT,
        lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT,
        lt.EnergyBalanceCarrier.AMBIENT_HEAT,
    )

    def __post_init__(self) -> None:
        """Refuse an empty name, a carrier that is no loop's heat, a fluid that is no :class:`LoopFluid` and a non-bool.

        Raises:
            FluidLoopError: For any of the above, naming the loop.
        """
        if not isinstance(self.name, str) or not self.name.strip():
            raise FluidLoopError(f"A fluid loop's name is a non-empty text, got {self.name!r}.")
        if not isinstance(self.carrier, lt.EnergyBalanceCarrier) or self.carrier not in self.LOOP_CARRIERS:
            raise FluidLoopError(
                f"The fluid loop {self.name!r} carries space-heating, hot-water or ambient heat "
                f"({', '.join(carrier.name for carrier in self.LOOP_CARRIERS)}), got {self.carrier!r}."
            )
        if not isinstance(self.fluid, LoopFluid):
            raise FluidLoopError(f"The fluid loop {self.name!r} names its fluid by LoopFluid, got {self.fluid!r}.")
        for flag, value in (("supplies", self.supplies), ("may_cool", self.may_cool)):
            if not isinstance(value, bool):
                raise FluidLoopError(f"The fluid loop {self.name!r} states {flag} as True or False, got {value!r}.")
        if self.pumps is not None and not isinstance(self.pumps, bool):
            raise FluidLoopError(f"The fluid loop {self.name!r} states pumps as True, False or None, got {self.pumps!r}.")

    @property
    def flow_in(self) -> "FluidLoopChannel":
        """The tag of the input that reads the mass flow arriving, in kg/s."""
        return FluidLoopChannel(self, LoopQuantity.FLOW_IN)

    @property
    def flow_out(self) -> "FluidLoopChannel":
        """The tag of the output that publishes the mass flow leaving, in kg/s."""
        return FluidLoopChannel(self, LoopQuantity.FLOW_OUT)

    @property
    def temperature_in(self) -> "FluidLoopChannel":
        """The tag of the input that reads the temperature of the fluid arriving, in °C."""
        return FluidLoopChannel(self, LoopQuantity.TEMPERATURE_IN)

    @property
    def temperature_out(self) -> "FluidLoopChannel":
        """The tag of the output that publishes the temperature of the fluid leaving, in °C."""
        return FluidLoopChannel(self, LoopQuantity.TEMPERATURE_OUT)


@dataclass(frozen=True)
class FluidLoopChannel:

    """The loop tag of one channel: which fluid loop of its component, and which of the four quantities.

    Passed to ``add_input`` or ``add_output`` as ``fluid_loop=``, usually through the loop's properties, for example
    ``fluid_loop=self.generator_loop.flow_in``. The tag is the only declaration of a loop: the component's tagged
    channels, grouped by loop, are its port sets (:meth:`hisim.component.Component.port_sets`).
    """

    loop: FluidLoop
    quantity: LoopQuantity

    def check_fits(
        self,
        component_name: str,
        field_name: str,
        unit: lt.Units,
        *,
        is_input: bool,
        energy_port: Optional[EnergyPort] = None,
    ) -> None:
        """Refuse this tag on a channel it cannot describe (§3.1 of ``roadmap/hydraulic_circuits_spec.md``).

        A flow is in kg/s and a temperature in °C; the fluid arriving is read through an input and the fluid leaving
        published through an output; a channel is a leg of a loop or an energy port, never both. For example, the tag
        ``loop.flow_in`` on an output in °C is refused for its unit first.

        Args:
            component_name: The channel's component, for the message.
            field_name: The channel's name, for the message.
            unit: The channel's unit.
            is_input: Whether the channel is an input.
            energy_port: The channel's energy port, if it declares one.

        Raises:
            FluidLoopError: If the unit, the kind of channel or an energy port beside the tag does not fit.
        """
        where = f"{component_name}.{field_name} (loop {self.loop.name!r}, {self.quantity.value})"
        if unit != self.quantity.unit:
            raise FluidLoopError(f"{where} is in {unit.value}; a {self.quantity.value} is in {self.quantity.unit.value}.")
        if is_input != self.quantity.is_input:
            channel_kind = "an input" if is_input else "an output"
            quantity_kind = "an input" if self.quantity.is_input else "an output"
            raise FluidLoopError(f"{where} is {channel_kind}; a {self.quantity.value} is {quantity_kind}.")
        if energy_port is not None:
            raise FluidLoopError(f"{where} also declares an energy port; a channel is a leg of a loop or a power, not both.")
