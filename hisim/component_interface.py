"""What a component class says about its ports before any instance of it exists.

A HiSim component creates its inputs, its outputs and its default connections in its constructor,
which is the right place for a simulation and the wrong one for a loader: whether an assembly's port
can bind a member to a partner, whether a ``wires:`` line names a real input, and which outputs an
assembly's test contract has to bound (``assemblies_spec.md`` §3.3, §9.4) are questions the
expansion has to answer at load time, long before a configuration exists to construct one with.

:class:`ClassInterface` is the class-level statement of those three things — the inputs and outputs
with their load types and units, and the source classes the component declares default connections
from — and :class:`hisim.component.Component` carries it as the class attribute ``CLASS_INTERFACE``.
For an aggregating component (a meter) it also states the dynamic default connections it accepts
from other classes, with their tags and weights, and for an output whose energy-balance carrier is
fixed by the class, that carrier. A class that leaves it ``None`` has made no statement; the
assembly loader refuses to bind such a class through a port and says which class must declare it,
so a missing declaration fails loudly instead of being guessed from naming conventions. A class
that declares it is held to it at construction: an input, an output, a default connection or a
dynamic default connection its constructor adds and the declaration does not list, and an output
whose energy port carries another carrier than the declared one, are refused by the component
itself, so the two can never drift apart.

The module imports nothing but the load-type vocabulary, so a component module can use it without
pulling in the file format.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, FrozenSet, Optional, Tuple

from hisim import loadtypes as lt


@dataclass(frozen=True)
class DeclaredPort:
    """One input or output a component class declares, with its load type and unit.

    Attributes:
        name: The port's field name, the one ``add_input``/``add_output`` is called with.
        load_type: Its load type.
        unit: Its unit.
        carrier: For an output whose ``EnergyPort`` carrier is the same for every instance of the
            class, that carrier (``hisim/energy_port.py``); the component is held to it when it
            adds the output, and an assembly's carrier need checks it at load time
            (``assemblies_spec.md`` §5.1). ``None`` when the class does not state it — an input,
            an output without an energy port, or one whose carrier follows the configuration (a
            boiler's fuel, ``EnergyPort.carrier_of_fuel``), which the run checks once the
            component is built.
    """

    name: str
    load_type: lt.LoadTypes
    unit: lt.Units
    carrier: Optional[lt.EnergyBalanceCarrier] = None


@dataclass(frozen=True)
class DeclaredFeed:
    """One dynamic default connection an aggregating component (a meter, an EMS) declares from a source class.

    The class-level statement of what ``DynamicComponent.add_dynamic_default_connections`` adds in
    the constructor: from components of ``source_class``, the output ``output`` is fed into this
    aggregator with these tags and this weight. An assembly's carrier need lowers to exactly these
    feeds on its provider's meter (``assemblies_spec.md`` §3.2, §5.1), the items a recorded twin
    writes (``from: CondensingGasBoiler.EnergyDemandSh, tags: [GAS_CONSUMPTION_UNCONTROLLED],
    weight: 999``), and they are the candidates an observer's selectors choose from (§4.1, §4.2):
    a class without them cannot observe.

    The weight is the class's default rank: 999 marks an output the aggregator only measures, any
    other weight one it ranks, which a controller assembly re-derives from its priorities (§4.4).
    ``dispatch_target`` states which input of the source the aggregator may actuate directly through
    this feed (the battery's ``LoadingPowerInput``, D21); an assembly's ``controllable:
    {target_input: …}`` must name exactly that input.

    Attributes:
        source_class: The class name (``Component.get_classname()``) of the feeding component.
        output: The feeding component's output.
        tags: The feed's flow tags, as ``lt.InandOutputType`` member names.
        weight: The feed's weight.
        component_type: The feed's ``component_type`` (an ``lt.ComponentType`` member name), or
            ``None``.
        dispatch_target: The source's input the aggregator may actuate through this feed, or
            ``None``.
    """

    #: The weight of a feed the aggregator only measures (``FeedRequest.MONITORED_ONLY_WEIGHT``).
    MEASURED_ONLY_WEIGHT: ClassVar[int] = 999

    source_class: str
    output: str
    tags: Tuple[str, ...]
    weight: int
    component_type: Optional[str] = None
    dispatch_target: Optional[str] = None

    @property
    def all_tags(self) -> Tuple[str, ...]:
        """The component type followed by the flow tags, the order a runtime connection lists them in."""
        return ((self.component_type,) if self.component_type is not None else ()) + tuple(self.tags)

    @property
    def is_ranked(self) -> bool:
        """Whether the aggregator ranks this feed rather than only measuring it."""
        return self.weight != self.MEASURED_ONLY_WEIGHT


@dataclass(frozen=True)
class ClassInterface:
    """The class-level statement of a component's inputs, outputs and default-connection sources.

    Attributes:
        inputs: Every input the component adds, by name, load type and unit.
        outputs: Every output it adds, likewise.
        default_connection_sources: The class names (``Component.get_classname()``) of the
            components it declares default connections from.
        kpis: The names of the component KPIs it reports, which an assembly's test contract may
            bound by name (``assemblies_spec.md`` §9.4).
        default_feeds: For an aggregating component, the dynamic default connections it declares
            (:class:`DeclaredFeed`); the component is held to them when it adds them.
    """

    #: The units in which an output carries energy: a power or an energy, the units an
    #: ``EnergyPort`` accepts (``hisim/energy_port.py``).
    ENERGY_UNITS: ClassVar[FrozenSet[lt.Units]] = frozenset(
        {
            lt.Units.WATT,
            lt.Units.KILOWATT,
            lt.Units.WATT_HOUR,
            lt.Units.KWH,
            lt.Units.KWH_PER_TIMESTEP,
            lt.Units.JOULE,
            lt.Units.KILOJOULE,
        }
    )

    #: The units of a temperature.
    TEMPERATURE_UNITS: ClassVar[FrozenSet[lt.Units]] = frozenset({lt.Units.CELSIUS, lt.Units.KELVIN})

    inputs: Tuple[DeclaredPort, ...] = ()
    outputs: Tuple[DeclaredPort, ...] = ()
    default_connection_sources: Tuple[str, ...] = ()
    kpis: Tuple[str, ...] = ()
    default_feeds: Tuple[DeclaredFeed, ...] = ()

    def output(self, name: str) -> Optional[DeclaredPort]:
        """Returns the declared output of that name, or ``None``."""
        return next((port for port in self.outputs if port.name == name), None)

    def input(self, name: str) -> Optional[DeclaredPort]:
        """Returns the declared input of that name, or ``None``."""
        return next((port for port in self.inputs if port.name == name), None)

    def feed(self, source_class_name: str, output: str) -> Optional[DeclaredFeed]:
        """Returns the declared default feed from that class's output, or ``None``."""
        return next(
            (feed for feed in self.default_feeds if feed.source_class == source_class_name and feed.output == output),
            None,
        )

    def feeds_from(self, source_class_name: str) -> Tuple[DeclaredFeed, ...]:
        """The declared default feeds from components of that class, in declaration order."""
        return tuple(feed for feed in self.default_feeds if feed.source_class == source_class_name)

    def declares_defaults_from(self, source_class_name: str) -> bool:
        """Whether the class declares default connections from components of that class."""
        return source_class_name in self.default_connection_sources

    @classmethod
    def carries_energy_or_temperature(cls, port: DeclaredPort) -> bool:
        """Whether an output carries energy or a temperature (the rule of the test contract).

        An output carries energy when its unit is a power or an energy (W, kW, Wh, kWh, kWh per
        timestep, J, kJ); it carries a temperature when its load type is ``TEMPERATURE`` or its
        unit is °C or K. ``assemblies_spec.md`` §9.4 requires a ``tests.bounds`` entry for every
        such output of every member of an assembly.

        Args:
            port: The declared output.

        Returns:
            ``True`` for an energy-carrying or temperature output.
        """
        return (
            port.unit in cls.ENERGY_UNITS
            or port.unit in cls.TEMPERATURE_UNITS
            or port.load_type == lt.LoadTypes.TEMPERATURE
        )


class ClassInterfaceViolation(ValueError):
    """A component added a port or a default connection its class interface does not declare."""
