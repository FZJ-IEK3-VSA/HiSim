"""What a component class says about its ports before any instance of it exists.

A HiSim component creates its inputs, its outputs and its default connections in its constructor,
which is the right place for a simulation and the wrong one for a loader: whether an assembly's port
can bind a member to a partner, whether a ``wires:`` line names a real input, and which outputs an
assembly's test contract has to bound (``assemblies_spec.md`` §3.3, §9.4) are questions the
expansion has to answer at load time, long before a configuration exists to construct one with.

:class:`ClassInterface` is the class-level statement of those three things — the inputs and outputs
with their load types and units, and the source classes the component declares default connections
from — and :class:`hisim.component.Component` carries it as the class attribute ``CLASS_INTERFACE``.
A class that leaves it ``None`` has made no statement; the assembly loader refuses to bind such a
class through a port and says which class must declare it, so a missing declaration fails loudly
instead of being guessed from naming conventions. A class that declares it is held to it at
construction: an input, an output or a default connection its constructor adds and the declaration
does not list is refused by the component itself, so the two can never drift apart.

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
    """

    name: str
    load_type: lt.LoadTypes
    unit: lt.Units


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

    def output(self, name: str) -> Optional[DeclaredPort]:
        """Returns the declared output of that name, or ``None``."""
        return next((port for port in self.outputs if port.name == name), None)

    def input(self, name: str) -> Optional[DeclaredPort]:
        """Returns the declared input of that name, or ``None``."""
        return next((port for port in self.inputs if port.name == name), None)

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
