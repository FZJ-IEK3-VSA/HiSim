"""The postprocessing flag and the energy port of an output never contradict each other.

An output of a component can say in two ways that it is electricity consumed, produced or stored. Its
``postprocessing_flag`` holds ``lt.InandOutputType`` members, by which the KPI computation and the energy manager
select their columns. Its ``energy_port`` (:class:`hisim.energy_port.EnergyPort`) states the output's role and
carrier in its component's energy balance, which the balance check, the Sankey and the electricity meter read. Three
flags have a port that means the same: ``ELECTRICITY_CONSUMPTION_*`` an electricity ``CONSUMPTION`` port,
``ELECTRICITY_PRODUCTION`` an electricity ``PRODUCTION`` port, and ``STORED_ENERGY_INCREASE`` an electricity
``STORED_CHANGE`` port. If the two disagreed, the KPIs would count a column as consumption that the balance books as
production, and nobody would notice.

The test builds every component class from each preset of its configuration and checks every output:

* an output that carries one of the three flags and a port has the port that matches its flag;
* an output that carries only one of the two -- a flag without its port, or an electricity port of one of the three
  roles without its flag -- is listed in :attr:`FlagPortAgreement.ONE_SIDED`, with the reason it is one-sided.

The list may only shrink. An entry whose output now carries both tags or neither fails the test, so the entry is
removed in the change that fixes its output, and a new one-sided output is added only by a change a reviewer sees.
"""

import dataclasses
import enum
import importlib
import inspect
import pkgutil
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple, Type

import pytest

import hisim.components
from hisim import loadtypes as lt
from hisim.component import Component
from hisim.components.advanced_battery_bslib import Battery
from hisim.components.advanced_fuel_cell import CHP
from hisim.components.fuel_meter import FuelMeterConfig
from hisim.components.generic_electrolyzer_h2 import Electrolyzer
from hisim.components.heating_curve import HeatDistributionSystemType
from hisim.components.idealized_electric_heater import IdealizedElectricHeater
from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLib, MoreAdvancedHeatPumpHPLibConfig
from hisim.components.simple_air_conditioner import SimpleAirConditioner
from hisim.components.transformer_rectifier import Transformer
from hisim.config import ConfigBase, auto_fields, presets_of
from hisim.config.context import SizingContext
from hisim.config.sizing import resolve_config
from hisim.energy_port import EnergyPort
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


class Tagging(enum.Enum):
    """How the flags and the energy port of one output relate, as :meth:`FlagPortAgreement.tagging` finds it."""

    #: The output carries one of the three flags and the electricity port of the flag's role.
    AGREE = "agree"
    #: The output carries one of the three flags and a port of another role or another carrier, or flags of two roles.
    DISAGREE = "disagree"
    #: The output carries one of the three flags and no port.
    FLAG_ONLY = "flag only"
    #: The output carries an electricity port of one of the three roles and none of the three flags.
    PORT_ONLY = "port only"
    #: The output carries none of the three flags and no electricity port of the three roles.
    UNTAGGED = "untagged"


@dataclasses.dataclass(frozen=True)
class TaggedOutput:
    """One output of a component built from one preset, and how its flags and its port relate.

    Attributes:
        component_class: The class of the component that declares the output.
        preset: The preset the component's configuration was built from, with the variant's settings if any.
        field_name: The output's name.
        tagging: How the output's flags and its port relate.
        flags: The output's postprocessing flags, by name.
        port: The output's energy port, or None.
    """

    component_class: type
    preset: str
    field_name: str
    tagging: Tagging
    flags: Tuple[str, ...]
    port: Optional[EnergyPort]

    def describe(self) -> str:
        """The output, its preset, its flags and its port, in one line for a failure message."""
        port = "no port" if self.port is None else f"port {self.port.role.name} {self.port.carrier.name}"
        return f"{self.component_class.__name__}.{self.field_name} (preset {self.preset}): flags {list(self.flags)}, {port}"


class FlagPortAgreement:
    """Builds every component class from its presets and finds how the flags and the port of each output relate.

    A component class is reached through its configuration classes: every ``ConfigBase`` subclass under
    ``hisim.components`` names its component class, and every preset of it is built, sized against
    :attr:`REFERENCE_HOUSE` where the preset leaves a field ``AUTO``, and handed to the component's constructor.
    A component class whose configuration has no preset is not built here; which configurations may ship none is
    decided by ``tests/test_config_contracts.py``.
    """

    #: The flags that mean electricity consumed, produced or stored, each with the role of its energy port.
    FLAG_ROLES: ClassVar[Mapping[lt.InandOutputType, lt.EnergyRole]] = {
        lt.InandOutputType.ELECTRICITY_CONSUMPTION: lt.EnergyRole.CONSUMPTION,
        lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED: lt.EnergyRole.CONSUMPTION,
        lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED: lt.EnergyRole.CONSUMPTION,
        lt.InandOutputType.ELECTRICITY_PRODUCTION: lt.EnergyRole.PRODUCTION,
        lt.InandOutputType.STORED_ENERGY_INCREASE: lt.EnergyRole.STORED_CHANGE,
    }

    #: The facts the presets' sizing laws read: a single-family house with one apartment, three residents, a
    #: 7.8 kW heating load at a design outside temperature of -7 °C, radiators and a gas boiler of 3 to 12 kW. The
    #: numbers only have to let every law resolve; which outputs a class declares does not depend on them.
    REFERENCE_HOUSE: ClassVar[SizingContext] = SizingContext(
        heating_load_in_watt=7780.75,
        heating_reference_temperature_in_celsius=-7.0,
        number_of_apartments=1,
        conditioned_floor_area_in_m2=120.0,
        water_mass_flow_rate_in_kg_per_second=0.5,
        heat_distribution_system_type=HeatDistributionSystemType.RADIATOR,
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
        weather_identity="flag-port-agreement-weather",
        occupancy_identity="flag-port-agreement-occupancy",
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

    #: Settings that no preset reaches and that change which outputs a class declares, each built in addition to
    #: the presets. The heat pump declares its hot-water outputs only when it also heats domestic hot water.
    CONFIG_VARIANTS: ClassVar[Mapping[Type[ConfigBase], Tuple[Mapping[str, Any], ...]]] = {
        MoreAdvancedHeatPumpHPLibConfig: ({"with_domestic_hot_water_preparation": True},),
    }

    #: The reason the heat pump's electricity per mode carries a port and no flag.
    HEAT_PUMP_MODE_REASON: ClassVar[str] = (
        "a port and no flag: the balance books the heat pump's electricity per mode, the KPIs count it once as "
        "ElectricalInputPowerTotalHeatpump; a consumption flag here would count this share twice."
    )
    #: The reason an electricity port of a device in a golden setup carries no flag.
    RESULTS_KEEP_REASON: ClassVar[str] = (
        "a port and no flag: the device runs in a golden setup (dynamic_components, electrolyzer_with_renewables) "
        "whose KPIs would move if they counted this electricity."
    )

    #: Every output that carries one of the two tags without the other, as (component class, output name), with the
    #: reason. The list may only shrink.
    ONE_SIDED: ClassVar[Mapping[Tuple[type, str], str]] = {
        (MoreAdvancedHeatPumpHPLib, MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH): HEAT_PUMP_MODE_REASON,
        (MoreAdvancedHeatPumpHPLib, MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW): HEAT_PUMP_MODE_REASON,
        (MoreAdvancedHeatPumpHPLib, MoreAdvancedHeatPumpHPLib.ElectricalInputPowerForCooling): HEAT_PUMP_MODE_REASON,
        (MoreAdvancedHeatPumpHPLib, MoreAdvancedHeatPumpHPLib.ElectricalInputPowerBrinePump): HEAT_PUMP_MODE_REASON,
        (MoreAdvancedHeatPumpHPLib, MoreAdvancedHeatPumpHPLib.ElectricalInputPowerTotal): (
            "a flag and no port: the sum of the heat pump's electricity per mode, which the KPIs count; a port here "
            "would book that electricity twice in the heat pump's balance."
        ),
        (Battery, Battery.ChargingPower): (
            "a port and no flag: the KPIs read the battery through its signed CHARGE_DISCHARGE output and its "
            "STORED_ENERGY_INCREASE output; flagged as consumption, the charging would count as the household's own."
        ),
        (Battery, Battery.DischargingPower): (
            "a port and no flag: the KPIs read the battery through its signed CHARGE_DISCHARGE output and its "
            "STORED_ENERGY_INCREASE output; flagged as production, the discharging would count as generation."
        ),
        (CHP, CHP.ElectricityOutput): RESULTS_KEEP_REASON,
        (Electrolyzer, Electrolyzer.CurrentLoad): RESULTS_KEEP_REASON,
        (IdealizedElectricHeater, IdealizedElectricHeater.ElectricityConsumptionInWatt): (
            "a port and no flag: the idealized heater holds a building at its set points to measure the building's "
            "demand; nothing bills it, and a consumption flag would put its electricity into the KPIs of the building "
            "tests that run it."
        ),
        (SimpleAirConditioner, SimpleAirConditioner.ElectricalPowerConsumption): (
            "a port and no flag: the device runs in the golden setup simple_air_conditioner_household_building_sizer, "
            "whose KPIs would move if they counted this electricity."
        ),
        (Transformer, Transformer.ElectricityReceivedInKilowatt): RESULTS_KEEP_REASON,
    }

    @classmethod
    def tagging(cls, flags: Sequence[Any], port: Optional[EnergyPort]) -> Tagging:
        """Return how the postprocessing flags and the energy port of one output relate.

        Only the three flags of :attr:`FLAG_ROLES` and the electricity ports of their three roles count; any other
        flag (``DISPLAY_IN_WEBTOOL``, a ``ComponentType``) is ignored. For example, ``ELECTRICITY_PRODUCTION`` with
        ``EnergyPort(PRODUCTION, ELECTRICITY)`` agrees, with ``EnergyPort(CONSUMPTION, ELECTRICITY)`` or with a heat
        port it disagrees, and a heat port without a flag is untagged.

        Args:
            flags: The output's postprocessing flags.
            port: The output's energy port, or None.

        Returns:
            The relation of the two.
        """
        flag_roles = {cls.FLAG_ROLES[flag] for flag in flags if flag in cls.FLAG_ROLES}
        if flag_roles:
            if port is None:
                return Tagging.FLAG_ONLY
            matches = flag_roles == {port.role} and port.carrier == lt.EnergyBalanceCarrier.ELECTRICITY
            return Tagging.AGREE if matches else Tagging.DISAGREE
        if port is not None and port.carrier == lt.EnergyBalanceCarrier.ELECTRICITY and port.role in cls.FLAG_ROLES.values():
            return Tagging.PORT_ONLY
        return Tagging.UNTAGGED

    @staticmethod
    def config_classes() -> List[Type[ConfigBase]]:
        """Return every configuration class defined in a module of ``hisim.components``, sorted by full name."""
        found: Set[Type[ConfigBase]] = set()
        for module_info in pkgutil.walk_packages(hisim.components.__path__, "hisim.components."):
            module = importlib.import_module(module_info.name)
            for _, member in inspect.getmembers(module, inspect.isclass):
                if issubclass(member, ConfigBase) and member.__module__ == module.__name__:
                    found.add(member)
        return sorted(found, key=lambda config_class: f"{config_class.__module__}.{config_class.__qualname__}")

    @classmethod
    def configurations(cls, config_class: Type[ConfigBase]) -> List[Tuple[str, Any]]:
        """Return every configuration this test builds of one class, sized, each with its label.

        One configuration per preset, and one more per preset and variant of :attr:`CONFIG_VARIANTS`. A preset that
        leaves a field ``AUTO`` is sized against :attr:`REFERENCE_HOUSE`, changed by :attr:`FACTS_OF_CONFIG`.

        Args:
            config_class: The configuration class.

        Returns:
            Pairs of a label (the preset's name and the variant's settings) and the configuration.
        """
        context = cls.REFERENCE_HOUSE.with_facts(**cls.FACTS_OF_CONFIG.get(config_class, {}))
        configurations = []
        for preset_name, builder in presets_of(config_class).items():
            preset_config = builder.build("FlagPortProbe")
            variants: Tuple[Mapping[str, Any], ...] = ({},) + cls.CONFIG_VARIANTS.get(config_class, ())
            for variant in variants:
                config = dataclasses.replace(preset_config, **variant)
                if auto_fields(config):
                    config = resolve_config(config, context)
                configurations.append((f"{preset_name} {dict(variant)}" if variant else preset_name, config))
        return configurations

    @classmethod
    def tagged_outputs(cls, simulation_parameters: SimulationParameters) -> List[TaggedOutput]:
        """Build every component class from its configurations and return each of its outputs with its tagging.

        Args:
            simulation_parameters: The parameters every component is built with.

        Returns:
            One entry per output of every built component, in the order of :meth:`config_classes`.
        """
        tagged = []
        for config_class in cls.config_classes():
            module_name, class_name = config_class.get_main_classname().rsplit(".", 1)
            component_class = getattr(importlib.import_module(module_name), class_name)
            for label, config in cls.configurations(config_class):
                component: Component = component_class(my_simulation_parameters=simulation_parameters, config=config)
                for output in component.outputs:
                    flags = tuple(output.postprocessing_flag or [])
                    tagged.append(
                        TaggedOutput(
                            component_class=component_class,
                            preset=label,
                            field_name=output.field_name,
                            tagging=cls.tagging(flags, output.energy_port),
                            flags=tuple(flag.name for flag in flags),
                            port=output.energy_port,
                        )
                    )
        return tagged


@pytest.fixture(name="tagged_outputs", scope="module")
def fixture_tagged_outputs() -> List[TaggedOutput]:
    """Every output of every component class built from its presets, with its tagging; built once per module."""
    return FlagPortAgreement.tagged_outputs(SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600))


def test_the_sweep_builds_every_class_that_declares_ports(tagged_outputs: List[TaggedOutput]) -> None:
    """Catches a sweep that builds too little, which would let every check below pass without looking."""
    built: FrozenSet[str] = frozenset(output.component_class.__name__ for output in tagged_outputs)
    assert {
        "Battery",
        "CarBattery",
        "DistrictHeating",
        "ElectricHeating",
        "GenericBoiler",
        "MoreAdvancedHeatPumpHPLib",
        "PVSystem",
        "SolarThermalSystem",
        "UtspLpgConnector",
    } <= built
    assert any(output.field_name == MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW for output in tagged_outputs)


def test_a_flag_and_a_port_on_one_output_agree(tagged_outputs: List[TaggedOutput]) -> None:
    """Catches an output flagged as consumption whose port says production, or any other pair that contradicts."""
    disagreeing = sorted({output.describe() for output in tagged_outputs if output.tagging is Tagging.DISAGREE})
    assert not disagreeing, "flag and energy port disagree on:\n" + "\n".join(disagreeing)


def test_every_one_sided_output_is_listed(tagged_outputs: List[TaggedOutput]) -> None:
    """Catches a new output that carries a flow flag without its energy port, or the port without its flag."""
    unlisted = sorted(
        {
            output.describe()
            for output in tagged_outputs
            if output.tagging in (Tagging.FLAG_ONLY, Tagging.PORT_ONLY) and (output.component_class, output.field_name) not in FlagPortAgreement.ONE_SIDED
        }
    )
    assert not unlisted, "one-sided outputs missing from FlagPortAgreement.ONE_SIDED:\n" + "\n".join(unlisted)


def test_every_listed_output_is_still_one_sided(tagged_outputs: List[TaggedOutput]) -> None:
    """Catches an entry of the list whose output was fixed or renamed, so the list only shrinks and never goes stale."""
    taggings: Dict[Tuple[type, str], Set[Tagging]] = {}
    for output in tagged_outputs:
        taggings.setdefault((output.component_class, output.field_name), set()).add(output.tagging)
    stale = sorted(
        f"{component_class.__name__}.{field_name}: {sorted(tagging.value for tagging in taggings.get((component_class, field_name), set()))}"
        for component_class, field_name in FlagPortAgreement.ONE_SIDED
        if not taggings.get((component_class, field_name), set()) & {Tagging.FLAG_ONLY, Tagging.PORT_ONLY}
    )
    assert not stale, "listed in FlagPortAgreement.ONE_SIDED but no longer one-sided:\n" + "\n".join(stale)


@pytest.mark.parametrize(
    ("flags", "port", "expected"),
    [
        ([lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED], EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY), Tagging.AGREE),
        ([lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED], EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY), Tagging.AGREE),
        ([lt.InandOutputType.ELECTRICITY_PRODUCTION, lt.ComponentType.PV], EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY), Tagging.AGREE),
        (
            [lt.InandOutputType.STORED_ENERGY_INCREASE, lt.ComponentType.BATTERY],
            EnergyPort(lt.EnergyRole.STORED_CHANGE, lt.EnergyBalanceCarrier.ELECTRICITY),
            Tagging.AGREE,
        ),
        ([lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED], EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY), Tagging.DISAGREE),
        ([lt.InandOutputType.ELECTRICITY_PRODUCTION], EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY), Tagging.DISAGREE),
        ([lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED], EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY), Tagging.DISAGREE),
        (
            [lt.InandOutputType.ELECTRICITY_PRODUCTION],
            EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
            Tagging.DISAGREE,
        ),
        (
            [lt.InandOutputType.ELECTRICITY_PRODUCTION, lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY),
            Tagging.DISAGREE,
        ),
        ([lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED], None, Tagging.FLAG_ONLY),
        ([], EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY), Tagging.PORT_ONLY),
        ([lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL], EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY), Tagging.PORT_ONLY),
        ([], EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT), Tagging.UNTAGGED),
        ([], EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY), Tagging.UNTAGGED),
        ([lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL], None, Tagging.UNTAGGED),
    ],
)
def test_tagging_relates_flags_and_port(flags: List[Any], port: Optional[EnergyPort], expected: Tagging) -> None:
    """Catches a classification that misses a contradiction, counts a heat port or a loss, or ignores a flag."""
    assert FlagPortAgreement.tagging(flags, port) is expected
