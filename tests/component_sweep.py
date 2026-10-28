"""Builds every component class of ``hisim.components`` from its configurations, without a run.

Two ``base`` tests read what a component declares when it is built rather than what it does in a run:
``tests/test_energy_port_flag_agreement.py`` (the postprocessing flag and the energy port of an output never
contradict each other) and ``tests/test_energy_port_coverage.py`` (every component class declares energy ports or
is exempt from the energy balance). Both need the same thing: every component class, and an instance of each built
from every configuration it ships. :class:`ComponentSweep` provides both.

A component class is reached through its configuration classes: every ``ConfigBase`` subclass under
``hisim.components`` names its component class (``MAIN_CLASS``), and every preset of it is built, sized against
:attr:`ComponentSweep.REFERENCE_HOUSE` where the preset leaves a field ``AUTO``, and handed to the component's
constructor. A configuration without a preset is built only where :attr:`ComponentSweep.CONSTRUCTED` says how,
and a component class that shares another's configuration only where :attr:`ComponentSweep.OTHER_CLASSES_OF_CONFIG`
names it. Which configurations may ship no preset is decided by ``tests/test_config_contracts.py``.
"""

import dataclasses
import importlib
import inspect
import pkgutil
from typing import Any, Callable, ClassVar, List, Mapping, Set, Tuple, Type

import hisim.components
from hisim import loadtypes as lt
from hisim.component import Component
from hisim.components.fuel_meter import FuelMeterConfig
from hisim.components.generic_car import CarConfig
from hisim.components.heating_curve import HeatDistributionSystemType
from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLibConfig
from hisim.components.sumbuilder import CalculateOperation, SumBuilderConfig, SumBuilderForThreeInputs
from hisim.config import ConfigBase, auto_fields, presets_of
from hisim.config.context import SizingContext
from hisim.config.sizing import resolve_config
from hisim.simulationparameters import SimulationParameters


@dataclasses.dataclass(frozen=True)
class BuiltComponent:
    """One component built by the sweep.

    Attributes:
        component_class: The class the component was built from.
        label: The configuration it was built from: a preset's name, with a variant's settings if any, or the
            constructor's name.
        component: The component.
    """

    component_class: Type[Component]
    label: str
    component: Component


class ComponentSweep:
    """Every component class of ``hisim.components``, and an instance of each per configuration it ships."""

    #: The facts the presets' sizing laws read: a single-family house with one apartment, three residents, a
    #: 7.8 kW heating load at a design outside temperature of -7 °C, radiators and a gas boiler of 3 to 12 kW. The
    #: numbers only have to let every law resolve; which channels a class declares does not depend on them.
    REFERENCE_HOUSE: ClassVar[SizingContext] = SizingContext(
        heating_load_in_watt=7780.75,
        heating_reference_temperature_in_celsius=-7.0,
        number_of_apartments=1,
        conditioned_floor_area_in_m2=120.0,
        water_mass_flow_rate_in_kg_per_second=0.5,
        heat_distribution_system_type=HeatDistributionSystemType.RADIATOR,
        heat_distribution_cooling_enabled=False,
        number_of_residents=3,
        maximal_thermal_power_in_watt=12000.0,
        minimal_thermal_power_in_watt=3000.0,
        set_heating_temperature_in_celsius=20.0,
        set_cooling_temperature_in_celsius=24.0,
        roof_area_in_m2=168.9,
        pv_peak_power_in_watt=(10000.0,),
        energy_carrier=lt.LoadTypes.GAS,
        heating_value_of_fuel_in_kwh_per_liter=None,
        fuel_density_in_kg_per_m3=None,
        weather_identity="component-sweep-weather",
        occupancy_identity="component-sweep-occupancy",
    )

    #: Facts that differ from :attr:`REFERENCE_HOUSE` for one configuration class. The fuel meter counts a fuel
    #: delivered by the litre, not gas, and refuses gas; an oil boiler beside it gives it oil and oil's constants.
    FACTS_OF_CONFIG: ClassVar[Mapping[Type[ConfigBase], Mapping[str, Any]]] = {
        FuelMeterConfig: {
            "energy_carrier": lt.LoadTypes.OIL,
            "heating_value_of_fuel_in_kwh_per_liter": 9.821666666666667,
            "fuel_density_in_kg_per_m3": 830.0,
        },
    }

    #: Settings that no preset reaches and that change which channels a class declares, each built in addition to
    #: the presets. The heat pump declares its hot-water outputs only when it also heats domestic hot water.
    CONFIG_VARIANTS: ClassVar[Mapping[Type[ConfigBase], Tuple[Mapping[str, Any], ...]]] = {
        MoreAdvancedHeatPumpHPLibConfig: ({"with_domestic_hot_water_preparation": True},),
    }

    #: How to build a configuration class that ships no preset, by its constructor, each with its label. The car
    #: names the household and the car whose driving profile it reads, an open space no preset can name; the names
    #: here only have to build, since the car reads its profile when the run prepares, not when it is built.
    CONSTRUCTED: ClassVar[Mapping[Type[ConfigBase], Tuple[Tuple[str, Callable[[str], ConfigBase]], ...]]] = {
        CarConfig: (
            ("for_household electric", lambda name: CarConfig.for_household(name, "SweepHousehold", "SweepCar")),
            (
                "for_household diesel",
                lambda name: CarConfig.for_household(name, "SweepHousehold", "SweepCar", fuel=lt.LoadTypes.DIESEL),
            ),
        ),
    }

    #: Component classes built from a configuration whose ``MAIN_CLASS`` names another class. The sum builder's
    #: configuration serves all three classes of its module.
    OTHER_CLASSES_OF_CONFIG: ClassVar[Mapping[Type[ConfigBase], Tuple[Type[Component], ...]]] = {
        SumBuilderConfig: (CalculateOperation, SumBuilderForThreeInputs),
    }

    #: The instance name every built component carries.
    INSTANCE_NAME: ClassVar[str] = "SweepProbe"

    @staticmethod
    def classes_defined_in_components(base: type) -> List[type]:
        """Return every subclass of ``base`` defined in a module of ``hisim.components``, sorted by full name.

        A class is counted in the module that defines it, not in a module that imports it. For example,
        ``classes_defined_in_components(ConfigBase)`` returns ``GenericBoilerConfig`` once, from
        ``hisim.components.generic_boiler``.

        Args:
            base: The base class, ``ConfigBase`` or ``Component``.

        Returns:
            The classes, sorted by module and qualified name.
        """
        found: Set[type] = set()
        for module_info in pkgutil.walk_packages(hisim.components.__path__, "hisim.components."):
            module = importlib.import_module(module_info.name)
            for _, member in inspect.getmembers(module, inspect.isclass):
                if issubclass(member, base) and member is not base and member.__module__ == module.__name__:
                    found.add(member)
        return sorted(found, key=lambda found_class: f"{found_class.__module__}.{found_class.__qualname__}")

    @classmethod
    def component_classes(cls) -> List[Type[Component]]:
        """Return every component class defined in a module of ``hisim.components``, sorted by full name."""
        return cls.classes_defined_in_components(Component)

    @classmethod
    def config_classes(cls) -> List[Type[ConfigBase]]:
        """Return every configuration class defined in a module of ``hisim.components``, sorted by full name."""
        return cls.classes_defined_in_components(ConfigBase)

    @classmethod
    def configurations(cls, config_class: Type[ConfigBase]) -> List[Tuple[str, Any]]:
        """Return every configuration the sweep builds of one class, sized, each with its label.

        One configuration per preset, one more per preset and variant of :attr:`CONFIG_VARIANTS`, and one per
        constructor of :attr:`CONSTRUCTED`. A configuration that leaves a field ``AUTO`` is sized against
        :attr:`REFERENCE_HOUSE`, changed by :attr:`FACTS_OF_CONFIG`. For example, the heat pump's configuration
        gives ``air_water`` and ``air_water {'with_domestic_hot_water_preparation': True}``.

        Args:
            config_class: The configuration class.

        Returns:
            Pairs of a label and the configuration.
        """
        context = cls.REFERENCE_HOUSE.with_facts(**cls.FACTS_OF_CONFIG.get(config_class, {}))
        unsized: List[Tuple[str, Any]] = []
        for preset_name, builder in presets_of(config_class).items():
            preset_config = builder.build(cls.INSTANCE_NAME)
            unsized.append((preset_name, preset_config))
            for variant in cls.CONFIG_VARIANTS.get(config_class, ()):
                unsized.append((f"{preset_name} {dict(variant)}", dataclasses.replace(preset_config, **variant)))
        for label, construct in cls.CONSTRUCTED.get(config_class, ()):
            unsized.append((label, construct(cls.INSTANCE_NAME)))
        return [(label, resolve_config(config, context) if auto_fields(config) else config) for label, config in unsized]

    @classmethod
    def built_components(cls, simulation_parameters: SimulationParameters) -> List[BuiltComponent]:
        """Build every component class from each of its configurations.

        Args:
            simulation_parameters: The parameters every component is built with.

        Returns:
            One entry per class and configuration, in the order of :meth:`config_classes`.
        """
        built = []
        for config_class in cls.config_classes():
            module_name, class_name = config_class.get_main_classname().rsplit(".", 1)
            main_class = getattr(importlib.import_module(module_name), class_name)
            component_classes = (main_class,) + cls.OTHER_CLASSES_OF_CONFIG.get(config_class, ())
            for label, config in cls.configurations(config_class):
                for component_class in component_classes:
                    component = component_class(my_simulation_parameters=simulation_parameters, config=config)
                    built.append(BuiltComponent(component_class=component_class, label=label, component=component))
        return built
