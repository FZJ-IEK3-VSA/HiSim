"""The heating curve: the L2 controller that sets the space-heating supply temperature and the heating season.

A heating curve gives the water temperature a space-heating circuit needs as a function of the day's mean outside
temperature: the colder the day, the hotter the supply. The same curve decides the heating season, the days on which
space heating runs at all, by comparing the day's mean outside temperature with the heating threshold.

Several L1 controllers share these two signals -- the heat pump's, the boiler's and the district-heating substation's
controllers aim at the supply temperature, and they and the heat-distribution controller heat only in the heating
season. A set point several L1 controllers share comes from an L2 controller, so the curve is a component of its own,
:class:`HeatingCurveController`, and every L1 controller reads the signals from its outputs instead of keeping a copy.
"""

import math
from dataclasses import dataclass
from enum import Enum, unique
from typing import ClassVar, List, assert_never

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import (
    CapexCostDataClass,
    ComponentConnection,
    ComponentInput,
    ComponentOutput,
    L2ControllerComponent,
    OpexCostDataClass,
    SingleTimeStepValues,
    StatelessComponent,
)
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.weather import Weather
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    Sizable,
    Size,
    SizingLaw,
    concrete,
    law,
    preset,
    sized_field,
)
from hisim.economics.facts import CostRelevance
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry
from hisim.simulationparameters import SimulationParameters


@unique
class HeatDistributionSystemType(str, Enum):
    """Set Heating System Types.

    Every member carries its own name as its value so that a serialized
    configuration names the heating system explicitly instead of encoding it as
    an integer. Only member identity is meaningful; no ordinal is used anywhere.
    """

    RADIATOR = "RADIATOR"
    FLOORHEATING = "FLOORHEATING"
    LOW_TEMPERATURE_RADIATOR = "LOW_TEMPERATURE_RADIATOR"


@dataclass(frozen=True)
class EmitterDesign:
    """The design point of an emitter type: its supply and return temperatures on the coldest day, and its exponent.

    The emitter is what hands the circuit's heat to the room: a floor heating or a radiator. At the heating reference
    temperature (the design day) it runs at its maximal supply and return temperatures; on milder days the heating
    curve lowers the supply along the emitter's exponent. For example, a floor heating is designed for 35 °C supply
    and 28 °C return with the exponent 1.1, a radiator for 70 °C and 55 °C with 1.3.

    Args:
        maximal_supply_temperature_in_celsius: The supply temperature on the design day, in °C.
        maximal_return_temperature_in_celsius: The return temperature on the design day, in °C.
        exponent: The emitter exponent of its heat output over the temperature difference to the room, dimensionless.
    """

    maximal_supply_temperature_in_celsius: float
    maximal_return_temperature_in_celsius: float
    exponent: float

    @staticmethod
    def for_type(heat_distribution_system_type: HeatDistributionSystemType) -> "EmitterDesign":
        """Return the design point of an emitter type.

        The values are the ones of the hplib heating-system table this repository has always used: floor heating
        35/28 °C with exponent 1.1, low-temperature radiators 55/45 °C with 1.2, radiators 70/55 °C with 1.3.

        Args:
            heat_distribution_system_type: The emitter type.

        Returns:
            Its design point.
        """
        match heat_distribution_system_type:
            case HeatDistributionSystemType.FLOORHEATING:
                return EmitterDesign(
                    maximal_supply_temperature_in_celsius=35.0, maximal_return_temperature_in_celsius=28.0, exponent=1.1
                )
            case HeatDistributionSystemType.LOW_TEMPERATURE_RADIATOR:
                return EmitterDesign(
                    maximal_supply_temperature_in_celsius=55.0, maximal_return_temperature_in_celsius=45.0, exponent=1.2
                )
            case HeatDistributionSystemType.RADIATOR:
                return EmitterDesign(
                    maximal_supply_temperature_in_celsius=70.0, maximal_return_temperature_in_celsius=55.0, exponent=1.3
                )
            case _:
                assert_never(heat_distribution_system_type)


@dataclass_json
@dataclass
class HeatingCurveControllerConfig(ConfigBase):
    """Configuration of the heating curve: the emitter it is drawn for, the design day, the room and the threshold.

    Every field is a property of the building or of its emitter circuit, so :meth:`preset_building_derived` pins
    nothing and the fields resolve from the facts the building, the weather and the heat-distribution controller
    contribute::

        HeatingCurveControllerConfig.preset_building_derived("HeatingCurveController").resolve(
            SizingContext(
                heating_load_in_watt=8000.0,
                conditioned_floor_area_in_m2=120.0,
                heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
                heating_reference_temperature_in_celsius=-7.0,
                set_heating_temperature_in_celsius=20.0,
            )
        )

    gives a floor-heating curve for a house of 66.7 W/m², whose heating threshold is 18 °C.
    """

    MAIN_CLASS = "hisim.components.heating_curve.HeatingCurveController"

    @staticmethod
    def heating_threshold_for(specific_heating_load_of_building_in_watt_per_m2: float) -> float:
        """Return the day mean outside temperature above which space heating stops, by the building's efficiency, in °C.

        An inefficient building cools out quickly in the mornings and evenings of the shoulder season, so the worse
        the specific heating load, the earlier the heating has to come on: 16 °C up to 50 W/m², 18 °C up to 80 W/m²
        and 20 °C above that. For example, 66.7 W/m² gives 18 °C.

        Args:
            specific_heating_load_of_building_in_watt_per_m2: The building's heating load per conditioned floor
                area, in W/m².

        Returns:
            The heating threshold, in °C.
        """
        if specific_heating_load_of_building_in_watt_per_m2 > 80.0:
            return 20.0
        if specific_heating_load_of_building_in_watt_per_m2 > 50.0:
            return 18.0
        return 16.0

    #: Sizing law of the heating threshold: :meth:`heating_threshold_for` over the building's specific heating load.
    #: An author who knows the threshold writes it down and the law steps aside.
    HEATING_THRESHOLD_LAW: ClassVar[SizingLaw] = law(
        lambda ctx: HeatingCurveControllerConfig.heating_threshold_for(
            ctx.heating_load_in_watt / ctx.conditioned_floor_area_in_m2
        ),
        reads=(Size.HEATING_LOAD_IN_WATT, Size.CONDITIONED_FLOOR_AREA_IN_M2),
        description="heating_threshold_for(Size.HEATING_LOAD_IN_WATT / Size.CONDITIONED_FLOOR_AREA_IN_M2)",
    )

    component_id: ComponentID
    #: The daily mean outside temperature above which space heating stops, in °C; derived from the building's
    #: efficiency by :meth:`heating_threshold_for`.
    set_heating_threshold_outside_temperature_in_celsius: Sizable[float] = sized_field(
        rule=HEATING_THRESHOLD_LAW,
        note="16 °C up to 50 W/m² of specific heating load, 18 °C up to 80 W/m², 20 °C above that",
    )
    #: The emitter the curve is drawn for, the heat-distribution controller's emitter type.
    heat_distribution_system_type: Sizable[HeatDistributionSystemType] = sized_field(
        rule=Size.HEAT_DISTRIBUTION_SYSTEM_TYPE, value_type=HeatDistributionSystemType
    )
    #: The outside temperature of the design day, on which the emitter runs at its design point, in °C.
    heating_reference_temperature_in_celsius: Sizable[float] = sized_field(
        rule=Size.HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS
    )
    #: The room temperature the building is heated to, in °C; the lowest supply temperature of the curve.
    set_heating_temperature_for_building_in_celsius: Sizable[float] = sized_field(
        rule=Size.SET_HEATING_TEMPERATURE_IN_CELSIUS
    )

    @preset
    @classmethod
    def preset_building_derived(cls, name: str) -> "HeatingCurveControllerConfig":
        """The one heating curve, derived entirely from the building and the emitter circuit it serves.

        Everything the curve needs is a property of the building -- its heating load and floor area for the
        threshold, its room set temperature -- of the place, the design-day temperature, or of the emitter circuit,
        the emitter type. The preset therefore pins nothing.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            The configuration, with every field still ``AUTO``.
        """
        return cls(component_id=ComponentID(name=name))


class HeatingCurveController(StatelessComponent, L2ControllerComponent):
    """L2 controller of space heating: the supply temperature set point of the heating curve, and the heating season.

    Every step it reads the day's mean outside temperature and the energy manager's raise of the room set
    temperature, and publishes:

    - ``SpaceHeatingSupplyTemperatureSetInCelsius``, the supply temperature the space-heating circuit needs
      (:meth:`supply_temperature_set_in_celsius`), which the generators' L1 controllers aim at;
    - ``HeatingSeason``, 1 on a day whose mean outside temperature is at or below the heating threshold and 0 otherwise
      (:meth:`is_heating_season`), which every L1 controller of space heating obeys.

    The curve holds no state. Like the L1 controllers it is frozen while the simulator forces a step to converge:
    its outputs then keep the values of the last free pass.
    """

    cost_relevance = CostRelevance.FREE_OF_COST
    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The lowest supply temperature in the cooling case, in °C: colder water would let the room air condense on a
    #: floor heating (suissetec, "Kühlung mit Fussbodenheizung", 2021).
    MINIMAL_COOLING_SUPPLY_TEMPERATURE_IN_CELSIUS: ClassVar[float] = 19.0

    # Inputs
    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"
    BuildingTemperatureModifierInKelvin = "BuildingTemperatureModifierInKelvin"

    # Outputs
    SpaceHeatingSupplyTemperatureSetInCelsius = "SpaceHeatingSupplyTemperatureSetInCelsius"
    HeatingSeason = "HeatingSeason"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatingCurveControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Build the heating curve from its resolved configuration and declare its ports.

        Args:
            my_simulation_parameters: The simulation parameters.
            config: The resolved configuration.
            my_display_config: How the component appears in reports.

        Raises:
            ValueError: If the design day is not colder than the room set temperature, which would make the curve
                divide by zero or rise without bound.
        """
        self.config = config
        self.my_simulation_parameters = my_simulation_parameters
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.emitter_design = EmitterDesign.for_type(concrete(config.heat_distribution_system_type))
        self.heating_reference_temperature_in_celsius = float(concrete(config.heating_reference_temperature_in_celsius))
        self.room_set_temperature_in_celsius = float(concrete(config.set_heating_temperature_for_building_in_celsius))
        self.heating_threshold_in_celsius = float(concrete(config.set_heating_threshold_outside_temperature_in_celsius))
        if self.heating_reference_temperature_in_celsius >= self.room_set_temperature_in_celsius:
            raise ValueError(
                "The heating curve's design day must be colder than the room it heats: the heating reference "
                f"temperature is {self.heating_reference_temperature_in_celsius} °C and the room set temperature "
                f"{self.room_set_temperature_in_celsius} °C."
            )

        self.daily_average_outside_temperature_channel: ComponentInput = self.add_input(
            self.component_name, self.DailyAverageOutsideTemperature, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True
        )
        self.building_temperature_modifier_channel: ComponentInput = self.add_input(
            self.component_name,
            self.BuildingTemperatureModifierInKelvin,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.KELVIN,
            mandatory=False,
        )
        self.supply_temperature_set_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.SpaceHeatingSupplyTemperatureSetInCelsius,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="The supply temperature the space-heating circuit needs by the heating curve, in °C.",
        )
        self.heating_season_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingSeason,
            lt.LoadTypes.ANY,
            lt.Units.BINARY,
            output_description=(
                "1 while the day's mean outside temperature is at or below the heating threshold, else 0."
            ),
        )
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_energy_management_system())

    @staticmethod
    def get_default_connections_from_weather() -> List[ComponentConnection]:
        """Return the connection of the day's mean outside temperature from the weather.

        Returns:
            The one connection.
        """
        return [
            ComponentConnection(
                HeatingCurveController.DailyAverageOutsideTemperature,
                Weather.get_classname(),
                Weather.DailyAverageOutsideTemperatures,
            )
        ]

    @staticmethod
    def get_default_connections_from_energy_management_system() -> List[ComponentConnection]:
        """Return the connection of the energy manager's raise of the room set temperature.

        Returns:
            The one connection.
        """
        return [
            ComponentConnection(
                HeatingCurveController.BuildingTemperatureModifierInKelvin,
                L2GenericEnergyManagementSystem.get_classname(),
                L2GenericEnergyManagementSystem.BuildingIndoorTemperatureModifier,
            )
        ]

    @staticmethod
    def is_heating_season(
        *, daily_average_outside_temperature_in_celsius: float, heating_threshold_in_celsius: float
    ) -> bool:
        """Return whether space heating runs on a day, from its mean outside temperature and the heating threshold.

        Space heating runs on a day whose mean outside temperature is at or below the threshold. For example, with
        an 18 °C threshold, a day at 17.9 °C or at 18 °C is in the heating season and a day at 18.1 °C is not.

        Args:
            daily_average_outside_temperature_in_celsius: The day's mean outside temperature, in °C.
            heating_threshold_in_celsius: The heating threshold, in °C.

        Returns:
            Whether the day is in the heating season.

        Raises:
            ValueError: If the day's mean outside temperature is NaN or infinite.
        """
        if not math.isfinite(daily_average_outside_temperature_in_celsius):
            raise ValueError(
                "The day's mean outside temperature must be a finite temperature in °C, got "
                f"{daily_average_outside_temperature_in_celsius!r}."
            )
        return daily_average_outside_temperature_in_celsius <= heating_threshold_in_celsius

    @staticmethod
    def supply_temperature_set_in_celsius(
        *,
        daily_average_outside_temperature_in_celsius: float,
        room_set_temperature_in_celsius: float,
        room_set_temperature_raise_in_kelvin: float,
        heating_reference_temperature_in_celsius: float,
        emitter_design: EmitterDesign,
    ) -> float:
        """Return the supply temperature of the heating curve for a day, in °C.

        On a heating day the supply runs from the room set temperature at no load up to the emitter's design supply
        temperature on the design day (DIN/TS 18599-12:2021-04, p. 170, eq. 127): with the load fraction
        ``x = (T_room - T_out) / (T_room - T_ref)``, the supply is ``T_room + x ** (1 / n) * (T_max - T_room)``, ``n``
        being the emitter exponent. The energy manager's raise lifts the room set temperature of this heating branch.
        On a day warmer than the unraised room set temperature (cooling) the supply is the unraised room set
        temperature, but not below :attr:`MINIMAL_COOLING_SUPPLY_TEMPERATURE_IN_CELSIUS`; the raise does not apply
        there. For example, a floor heating (35 °C, n = 1.1) for a 20 °C room and a -7 °C design day asks for 28.8 °C
        on a 5 °C day.

        Args:
            daily_average_outside_temperature_in_celsius: The day's mean outside temperature, in °C.
            room_set_temperature_in_celsius: The room temperature the building is heated to, in °C.
            room_set_temperature_raise_in_kelvin: The energy manager's raise of the room set temperature, in K.
            heating_reference_temperature_in_celsius: The outside temperature of the design day, in °C.
            emitter_design: The design point of the emitter.

        Returns:
            The supply temperature, in °C.

        Raises:
            ValueError: If the raised room set temperature is not warmer than the design day, or if the load fraction
                is negative or NaN, as a negative raise that puts the raised room below the day mean or a NaN raise
                would make it.
        """
        if room_set_temperature_in_celsius < daily_average_outside_temperature_in_celsius:
            return max(
                room_set_temperature_in_celsius, HeatingCurveController.MINIMAL_COOLING_SUPPLY_TEMPERATURE_IN_CELSIUS
            )
        raised_room_set_temperature_in_celsius = room_set_temperature_in_celsius + room_set_temperature_raise_in_kelvin
        if not raised_room_set_temperature_in_celsius > heating_reference_temperature_in_celsius:
            raise ValueError(
                f"The heating curve's raised room set temperature, {raised_room_set_temperature_in_celsius} °C, must "
                f"be warmer than its design day, {heating_reference_temperature_in_celsius} °C."
            )
        load_fraction = (raised_room_set_temperature_in_celsius - daily_average_outside_temperature_in_celsius) / (
            raised_room_set_temperature_in_celsius - heating_reference_temperature_in_celsius
        )
        if not load_fraction >= 0.0:
            raise ValueError(
                f"The heating curve's load fraction must be at least 0, got {load_fraction} from a day mean of "
                f"{daily_average_outside_temperature_in_celsius} °C, a room set temperature of "
                f"{room_set_temperature_in_celsius} °C raised by {room_set_temperature_raise_in_kelvin} K and a design "
                f"day of {heating_reference_temperature_in_celsius} °C."
            )
        return float(
            raised_room_set_temperature_in_celsius
            + load_fraction ** (1 / emitter_design.exponent)
            * (emitter_design.maximal_supply_temperature_in_celsius - raised_room_set_temperature_in_celsius)
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Publish the supply temperature set point and the heating season of this step.

        Under forced convergence the outputs keep the values of the last free pass, as the L1 controllers' do.
        """
        if force_convergence:
            return
        daily_average_outside_temperature_in_celsius = stsv.get_input_value(
            self.daily_average_outside_temperature_channel
        )
        stsv.set_output_value(
            self.supply_temperature_set_channel,
            self.supply_temperature_set_in_celsius(
                daily_average_outside_temperature_in_celsius=daily_average_outside_temperature_in_celsius,
                room_set_temperature_in_celsius=self.room_set_temperature_in_celsius,
                room_set_temperature_raise_in_kelvin=stsv.get_input_value(self.building_temperature_modifier_channel),
                heating_reference_temperature_in_celsius=self.heating_reference_temperature_in_celsius,
                emitter_design=self.emitter_design,
            ),
        )
        stsv.set_output_value(
            self.heating_season_channel,
            float(
                self.is_heating_season(
                    daily_average_outside_temperature_in_celsius=daily_average_outside_temperature_in_celsius,
                    heating_threshold_in_celsius=self.heating_threshold_in_celsius,
                )
            ),
        )

    def write_to_report(self) -> List[str]:
        """Return the report lines of the heating curve: its configuration.

        Returns:
            One line per configuration field.
        """
        return self.config.get_string_dict()

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame) -> OpexCostDataClass:
        """Return no operating costs: the heating curve is control logic, not a device.

        Returns:
            The zero operating costs.
        """
        return OpexCostDataClass.get_default_opex_cost_data_class()

    @staticmethod
    def get_cost_capex(
        config: HeatingCurveControllerConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Return no investment: the heating curve is control logic, not a device.

        Returns:
            The zero investment.
        """
        return CapexCostDataClass.get_default_capex_cost_data_class()

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame) -> List[KpiEntry]:
        """Return no KPIs: the curve's signals are judged by the devices that follow them.

        Returns:
            An empty list.
        """
        return []
