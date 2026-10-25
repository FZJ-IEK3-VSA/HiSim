"""The subcomponent marker: what inherits it, that none of it is a component, and that no instance is shared.

A subcomponent is a plain class that models part of a device's behaviour inside a component; every such class
inherits :class:`hisim.subcomponents.base.Subcomponent` (``system_docs/component_design_principles.md``, section C).
The tests find the subcomponent classes through ``__subclasses__`` after importing every module of
``hisim.components``, so a class tagged later is checked without editing a list here.
"""

import dataclasses
import importlib
import pkgutil
from collections import defaultdict
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator, List, Set, Tuple, Type

import pytest

import hisim.components
from hisim.calculation_scope import CalculationScope
from hisim.component import Component
from hisim.energy_system.executor import SimulationParametersReader, build_energy_system
from hisim.subcomponents.base import Subcomponent

pytestmark = pytest.mark.base


class SubcomponentCensus:
    """Finds the subcomponent classes and the subcomponent instances a built energy system holds.

    The classes come from ``Subcomponent.__subclasses__``, followed down to every subclass of a subclass, after every
    module of ``hisim.components`` is imported. The instances come from a walk over each component's instance
    attributes, one level deep: an attribute itself, the items of a list, tuple, set or dictionary it holds, and the
    fields of every dataclass among them, followed into nested dataclasses. For example, a gas boiler's ``burner``
    attribute is a ``BurnerModulation``, and its field ``efficiency_curve`` is a ``CombustionEfficiencyCurve``.
    """

    #: The repository root, the twins built, and the parameters they are built with: one day at 900 s.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[2]
    TWINS: ClassVar[Tuple[str, ...]] = (
        "household_gas_building_sizer",
        "household_heatpump_building_sizer",
        "household_gas_solar_thermal",
    )
    PARAMETERS: ClassVar[Path] = ROOT / "simulation_parameters" / "one_day_15min_plain.simulation.yaml"

    #: The occupancy source the twins name, and the shipped profile the test uses instead, so building needs no
    #: LoadProfileGenerator run.
    LOCAL_LPG: ClassVar[str] = "USE_LOCAL_LPG"
    PREDEFINED_PROFILE: ClassVar[str] = "USE_PREDEFINED_PROFILE"

    @classmethod
    def subcomponent_classes(cls) -> List[Type[Subcomponent]]:
        """Return every subclass of :class:`Subcomponent`, after importing every module of ``hisim.components``.

        For example, the list holds ``BoilerPump`` from ``hisim.components.generic_boiler``. Importing every module
        first makes the list complete: a class is a subclass only once its module has run.

        Returns:
            The classes, sorted by module and name.
        """
        for module_info in pkgutil.walk_packages(hisim.components.__path__, "hisim.components."):
            importlib.import_module(module_info.name)
        found: Set[Type[Subcomponent]] = set()
        pending = list(Subcomponent.__subclasses__())
        while pending:
            subclass = pending.pop()
            if subclass not in found:
                found.add(subclass)
                pending.extend(subclass.__subclasses__())
        return sorted(found, key=lambda subclass: (subclass.__module__, subclass.__qualname__))

    @classmethod
    def built_components(cls, twin: str, work: Path) -> List[Component]:
        """Build one twin at one day and 900 s without running it, and return its components.

        Args:
            twin: The stem of an energy-system file under ``energy_systems/``, one of :attr:`TWINS`.
            work: An empty directory for the rewritten energy-system file and the results directory.

        Returns:
            The components the simulator holds, in the order they were added.
        """
        text = (cls.ROOT / "energy_systems" / f"{twin}.energy_system.yaml").read_text(encoding="utf-8")
        energy_system = work / f"{twin}.energy_system.yaml"
        energy_system.write_text(text.replace(cls.LOCAL_LPG, cls.PREDEFINED_PROFILE), encoding="utf-8")
        results_directory = work / "results"
        with CalculationScope.open(label=str(energy_system), run_directory=str(results_directory)):
            simulation_parameters = SimulationParametersReader.read(cls.PARAMETERS)
            simulation_parameters.result_directory = str(results_directory)
            simulator = build_energy_system(energy_system, simulation_parameters).simulator
        return [wrapper.my_component for wrapper in simulator.wrapped_components]

    @classmethod
    def reachable_values(cls, component: Component) -> Iterator[Any]:
        """Yield every value one level below a component, and the fields of every dataclass among them.

        One level is an instance attribute and, for a list, tuple, set or dictionary, its items. A dataclass instance
        is opened field by field, recursively, each object once. Another component is yielded but not opened, so the
        walk stays within the component it starts from.

        Args:
            component: The component whose attributes are walked.

        Yields:
            The values, each object at most once.
        """
        seen: Set[int] = set()
        pending: List[Any] = []
        for value in vars(component).values():
            pending.append(value)
            if isinstance(value, (list, tuple, set, frozenset)):
                pending.extend(value)
            elif isinstance(value, dict):
                pending.extend(value.values())
        while pending:
            value = pending.pop()
            if id(value) in seen:
                continue
            seen.add(id(value))
            yield value
            if dataclasses.is_dataclass(value) and not isinstance(value, type) and not isinstance(value, Component):
                pending.extend(getattr(value, field.name) for field in dataclasses.fields(value))

    @classmethod
    def holders_of_subcomponents(cls, components: List[Component]) -> Dict[int, List[str]]:
        """Return, for every subcomponent instance the components hold, the names of the components that hold it.

        For example, a gas boiler's ``BoilerPump`` maps to ``["CondensingGasBoiler"]``.

        Args:
            components: The components of one built system.

        Returns:
            The component names per instance, keyed by the instance's ``id``.
        """
        holders: Dict[int, List[str]] = defaultdict(list)
        for component in components:
            for value in cls.reachable_values(component):
                if isinstance(value, Subcomponent):
                    holders[id(value)].append(f"{component.component_name} ({type(value).__name__})")
        return holders


def test_the_subcomponent_classes_are_found() -> None:
    """Catches the discovery below finding no subcomponent, which would let every check pass on an empty list."""
    names = {subclass.__name__ for subclass in SubcomponentCensus.subcomponent_classes()}
    assert {"BoilerPump", "EmitterDesign", "ScopCalibration", "DiverterValve", "FlatPlateCollectorCurve"} <= names


@pytest.mark.parametrize("subclass", SubcomponentCensus.subcomponent_classes(), ids=lambda subclass: subclass.__name__)
def test_no_subcomponent_is_a_component(subclass: Type[Subcomponent]) -> None:
    """Catches a class that inherits both the marker and a component base.

    A subcomponent has no ports and no lifecycle and the simulator never sees it; a component is the opposite. A class
    that is both would be added to the simulator while also built inside another component.
    """
    assert not issubclass(subclass, Component), f"{subclass.__module__}.{subclass.__qualname__} is a Subcomponent and a Component"


def test_the_marker_carries_no_behaviour() -> None:
    """Catches behaviour or state added to the marker, which every subcomponent would then inherit unseen."""
    defined = set(vars(Subcomponent)) - {"__module__", "__qualname__", "__doc__", "__slots__", "__firstlineno__", "__static_attributes__"}
    assert not defined, f"Subcomponent defines {sorted(defined)}; it is a marker without behaviour"
    assert not Subcomponent.__slots__, "the marker's __slots__ must stay empty, so it adds no attribute"


@pytest.mark.parametrize("twin", SubcomponentCensus.TWINS)
def test_no_subcomponent_instance_is_reachable_from_two_components(twin: str, tmp_path: Path) -> None:
    """Catches one subcomponent instance held by two components of a built system.

    Classes are shared, instances are not: each component builds its own subcomponents from its own configuration.
    A shared instance would couple two components through an object instead of a configuration value, a sizing rule
    or a port. The walk must also find instances at all, so a twin without any would show that the walk is broken.
    """
    holders = SubcomponentCensus.holders_of_subcomponents(SubcomponentCensus.built_components(twin, tmp_path))
    assert holders, f"the walk found no subcomponent instance in {twin}"
    shared = {key: names for key, names in holders.items() if len(names) > 1}
    assert not shared, f"subcomponent instances reachable from two components in {twin}: {sorted(shared.values())}"
