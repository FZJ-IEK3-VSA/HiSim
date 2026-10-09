"""Simple Water Storage Module for dhw storage and hot water storage for heating."""

# Owned
import importlib
from dataclasses import dataclass
from typing import ClassVar, Dict, List, Any, Sequence, Tuple, Optional
from enum import Enum, unique
import numpy as np
import pandas as pd
from dataclasses_json import dataclass_json

import hisim.component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim import hydronics
from hisim.component import (
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
    OpexCostDataClass,
    CapexCostDataClass,
)
from hisim.config import (
    ComponentID,
    ConfigBase,
    ConfigSizingError,
    DisplayConfig,
    OwnFields,
    Sizable,
    Size,
    SizingContext,
    SizingLaw,
    concrete,
    law,
    preset,
    sized_field,
)
from hisim.components.configuration import PhysicsConfig
from hisim.components import configuration
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry, KpiHelperClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.economics.facts import CostRelevance


@unique
class HotWaterStorageSizingEnum(str, Enum):
    """Set Simple Hot Water Storage sizing options.

    Selects which heat generator the storage volume is scaled against. Every
    member carries its own name as its value so that the choice reads as text
    wherever it is written out; only member identity matters at runtime.
    """

    SIZE_ACCORDING_TO_HEAT_PUMP = "SIZE_ACCORDING_TO_HEAT_PUMP"
    SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM = "SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM"
    SIZE_ACCORDING_TO_GAS_HEATER = "SIZE_ACCORDING_TO_GAS_HEATER"
    SIZE_ACCORDING_TO_PELLET_HEATING = "SIZE_ACCORDING_TO_PELLET_HEATING"
    SIZE_ACCORDING_TO_WOOD_CHIP_HEATING = "SIZE_ACCORDING_TO_WOOD_CHIP_HEATING"


@unique
class PositionHotWaterStorageInSystemSetup(str, Enum):
    """Set Simple Hot Water Storage Position options.

    Decides whether the storage sits parallel to or in series with the heat
    source, which changes the component's input wiring. Every member carries its
    own name as its value so that a serialized configuration spells the position
    out instead of encoding it as an integer.
    """

    PARALLEL_TO_HEAT_SOURCE = "PARALLEL_TO_HEAT_SOURCE"
    SERIES_TO_HEAT_SOURCE = "SERIES_TO_HEAT_SOURCE"


def _buffer_volume_in_liter(ctx: SizingContext, own: OwnFields) -> float:
    """Computes a buffer vessel's volume from the generator it buffers and the kind that generator is.

    The law behind the ``volume_heating_water_storage_in_liter`` field of
    :class:`SimpleHotWaterStorageConfig`: the generator's maximal thermal power in kilowatt
    times the litres-per-kilowatt figure its kind is buffered at, rounded to two decimals. The
    figures themselves live in
    :attr:`SimpleHotWaterStorageConfig.LITRES_PER_KILOWATT_BY_SIZING_OPTION` with their sources,
    so they are written down once.

    The power read is the *generator's*, never the building's heating load: a vessel buffers the
    machine that fills it, not the house it stands in.

    Args:
        ctx: The sizing facts of the surrounding system; ``maximal_thermal_power_in_watt`` is
            read from it, contributed by the heat generator this vessel buffers.
        own: The sibling fields of the configuration being resolved, for the ``sizing_option``
            that selects the litres-per-kilowatt figure.

    Returns:
        float: The vessel's volume in litres, rounded to two decimals.

    Raises:
        ConfigSizingError: If the context carries no maximal thermal power, so the law has
            nothing to size from.
        ValueError: If ``sizing_option`` is not one of the five kinds the table covers.
    """
    maximal_thermal_power_in_watt: Optional[float] = ctx.one("maximal_thermal_power_in_watt")
    if maximal_thermal_power_in_watt is None:
        raise ConfigSizingError(
            "a buffer vessel is sized from 'maximal_thermal_power_in_watt', which this context "
            "does not carry. Resolve the storage configuration against a context the heat "
            "generator contributed to, or pin 'volume_heating_water_storage_in_liter' to the "
            "vessel's volume."
        )
    sizing_option = own.value_of("sizing_option")
    litres_per_kilowatt = SimpleHotWaterStorageConfig.LITRES_PER_KILOWATT_BY_SIZING_OPTION.get(sizing_option)
    if litres_per_kilowatt is None:
        known = ", ".join(option.name for option in SimpleHotWaterStorageConfig.LITRES_PER_KILOWATT_BY_SIZING_OPTION)
        raise ValueError(
            f"No litres-per-kilowatt figure is known for the sizing option {sizing_option}, so a "
            f"buffer vessel for it cannot be sized. The kinds of generator this table covers "
            f"are: {known}."
        )
    return round(maximal_thermal_power_in_watt / 1e3 * litres_per_kilowatt, 2)


@dataclass_json
@dataclass
class SimpleHotWaterStorageConfig(ConfigBase):
    """Configuration of the SimpleHotWaterStorage class.

    The space-heating buffer vessel. The named default is :meth:`preset_buffer`, and
    ``volume_heating_water_storage_in_liter`` is sizable: the preset leaves it ``AUTO`` and
    ``.resolve(ctx)`` computes it from the maximal thermal power of the generator the vessel
    buffers. An author who knows the vessel pins the field instead.

    ``sizing_option`` says which kind of generator the vessel buffers, and so which
    litres-per-kilowatt figure the volume law applies. It is a field rather than an argument of
    the law so that a finished configuration records the figure its volume came from.
    """

    MAIN_CLASS = "hisim.components.simple_water_storage.SimpleHotWaterStorage"

    #: Litres of buffer volume per kilowatt of installed generator power, by the kind of
    #: generator the vessel buffers. Heat pumps and wood chip boilers get the largest vessel
    #: (50 l/kW): a heat pump wants a long, flat run, and a wood chip boiler must not cycle.
    #: Pellet boilers are next (40 l/kW) for the same on-off reason, and gas heaters and the
    #: general case are the smallest (20 l/kW), a gas heater carrying more inertia of its own.
    #: The information for scaling the buffer storage is taken from the heating system
    #: guidelines from Buderus:
    #: https://www.baunetzwissen.de/heizung/fachwissen/speicher/dimensionierung-von-pufferspeichern-161296
    #: Or from here:
    #: https://www.flexiheatuk.com/buffer-vessel-sizing-for-hydronic-heating-systems/#:~:text=20%2D25%20litres%20per%20kW,kW%20for%20heat%20pump%20systems
    LITRES_PER_KILOWATT_BY_SIZING_OPTION: ClassVar[Dict[HotWaterStorageSizingEnum, float]] = {
        HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP: 50.0,
        HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM: 20.0,
        HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_PELLET_HEATING: 40.0,
        HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_WOOD_CHIP_HEATING: 50.0,
        HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GAS_HEATER: 20.0,
    }

    #: Sizing law of the vessel's volume: :func:`_buffer_volume_in_liter`, the generator's power
    #: times the litres-per-kilowatt figure of the sibling ``sizing_option``. Named as a ClassVar
    #: so that the field declaration reads as one line.
    VOLUME_LAW: ClassVar[SizingLaw] = law(
        _buffer_volume_in_liter,
        reads=(Size.MAXIMAL_THERMAL_POWER_IN_WATT,),
        fields=("sizing_option",),
        description=(
            "Size.MAXIMAL_THERMAL_POWER_IN_WATT in kilowatt, times the litres per kilowatt of"
            ' Self("sizing_option"), rounded to 2 decimals'
        ),
    )

    component_id: ComponentID
    #: Heat the vessel loses to its surroundings, per square metre of surface and kelvin of
    #: difference. It should be checked how much energy the storage lost over the simulated
    #: period; the accepted loss in kWh/day is on p. 2 of
    #: https://www.bdh-industrie.de/fileadmin/user_upload/ISH2019/Infoblaetter/Infoblatt_Nr_74_Energetische_Bewertung_Warmwasserspeicher.pdf
    heat_transfer_coefficient_in_watt_per_m2_per_kelvin: float = 2.0
    #: With the exchanger the outlet temperature is the vessel's mean; without it the vessel
    #: stratifies and a mixing factor is computed instead. The stratified mode still causes
    #: problems, which is why the exchanger is the default.
    heat_exchanger_is_present: bool = True
    #: Where the vessel sits, which decides its wiring: only ``PARALLEL_TO_HEAT_SOURCE`` gives
    #: the component its four heat-generator inputs.
    position_hot_water_storage_in_system: PositionHotWaterStorageInSystemSetup = (
        PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_SOURCE
    )
    #: Which kind of generator this vessel buffers, and so which litres-per-kilowatt figure of
    #: :attr:`LITRES_PER_KILOWATT_BY_SIZING_OPTION` the volume law applies. A plain field, not a
    #: sizable one: nothing in the system contributes it, the author states it beside the
    #: generator they installed.
    sizing_option: HotWaterStorageSizingEnum = HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM
    #: CO2 footprint of investment in kg. Unset throughout the repository, which is what makes
    #: postprocessing look the vessel up in the cost database instead.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None
    #: Volume of the vessel. Sizable: left ``AUTO`` it is computed by :data:`VOLUME_LAW` from the
    #: generator's maximal thermal power.
    volume_heating_water_storage_in_liter: Sizable[float] = sized_field(
        rule=VOLUME_LAW,
        unit=lt.Units.LITER,
        note="the generator's power in kW times the litres per kW of the sibling sizing_option",
    )

    @preset
    @classmethod
    def preset_buffer(cls, name: str) -> "SimpleHotWaterStorageConfig":
        """The fleet's space-heating buffer vessel, scaled to the generator it buffers.

        The field defaults are this vessel: a tank losing 2.0 watt per square metre and kelvin,
        standing parallel to the heat source and fitted with a heat exchanger, buffering a
        generator of no particular kind (``SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM``, 20 l/kW).
        What the preset does not fix is how large the tank is:
        ``volume_heating_water_storage_in_liter`` stays ``AUTO`` so that :data:`VOLUME_LAW`
        derives it from the generator's maximal thermal power, and an author who knows the vessel
        pins the field instead.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            SimpleHotWaterStorageConfig: The preset configuration, with the volume unsized.
        """
        return cls(component_id=ComponentID(name=name))


@dataclass_json
@dataclass
class SimpleDHWStorageConfig(ConfigBase):
    """Configuration of the SimpleDHWStorage class.

    The domestic-hot-water vessel of a household. The named default is
    :meth:`preset_standard`, and ``volume_heating_water_storage_in_liter`` is sizable: the
    preset leaves it ``AUTO`` and ``.resolve(ctx)`` computes it from the number of apartments
    the building contributes. An author who knows the vessel pins the field instead.
    """

    MAIN_CLASS = "hisim.components.simple_water_storage.SimpleDHWStorage"

    #: Litres of domestic hot water storage per apartment. The number is the fleet's convention
    #: rather than a cited standard: every household in the repository uses it.
    VOLUME_PER_APARTMENT_IN_LITER: ClassVar[float] = 250.0

    #: Sizing law of the vessel's volume: :data:`VOLUME_PER_APARTMENT_IN_LITER` for every
    #: apartment the building has, and for at least one apartment -- the clamp the factory
    #: wrote as ``max(number_of_apartments, 1)``, so a context reporting zero apartments still
    #: sizes one household's vessel instead of a storage of no volume. Named as a ClassVar so
    #: that the field declaration reads as one line.
    VOLUME_LAW: ClassVar[SizingLaw] = Size.NUMBER_OF_APARTMENTS.at_least(1) * VOLUME_PER_APARTMENT_IN_LITER

    component_id: ComponentID
    #: Heat the vessel loses to its surroundings, per square metre of surface and kelvin of
    #: difference.
    heat_transfer_coefficient_in_watt_per_m2_per_kelvin: float = 0.36
    #: CO2 footprint of investment in kg. Unset throughout the repository, which is what makes
    #: postprocessing look the vessel up in the cost database instead.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None
    #: Volume of the vessel. Sizable: left ``AUTO`` it is computed by :data:`VOLUME_LAW` from
    #: the apartment count the building contributes.
    volume_heating_water_storage_in_liter: Sizable[float] = sized_field(rule=VOLUME_LAW, unit=lt.Units.LITER)

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "SimpleDHWStorageConfig":
        """The fleet's domestic-hot-water vessel, scaled to the building it stands in.

        The field defaults are this vessel: a tank losing 0.36 watt per square metre and kelvin
        to its surroundings. What the preset does not fix is how large the tank is:
        ``volume_heating_water_storage_in_liter`` stays ``AUTO`` so that :data:`VOLUME_LAW`
        derives it from the building's apartment count, and an author who knows the vessel pins
        the field instead.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            SimpleDHWStorageConfig: The preset configuration, with the volume unsized.
        """
        return cls(component_id=ComponentID(name=name))


@dataclass
class SimpleWaterStorageState:
    """SimpleHotWaterStorageState class."""

    mean_water_temperature_in_celsius: float = 25.0
    temperature_loss_in_celsius_per_timestep: float = 0.0
    heat_loss_in_watt: float = 0.0

    def self_copy(self):
        """Copy the Simple Hot Water Storage State."""
        return SimpleWaterStorageState(
            self.mean_water_temperature_in_celsius,
            self.temperature_loss_in_celsius_per_timestep,
            self.heat_loss_in_watt,
        )


class SimpleWaterStorage(cp.Component):
    """SimpleWaterStorage class with generic functions."""

    cost_relevance = CostRelevance.PRICED

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        name: str,
        my_config: ConfigBase,
        my_display_config: DisplayConfig,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(name, my_simulation_parameters, my_config, my_display_config)
        self.my_simulation_parameters = my_simulation_parameters
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep

    def check_water_mass(self, water_mass_in_kg: float, storage_label: str) -> None:
        """Refuse a vessel that holds no water.

        A fully mixed vessel divides by its heat capacity ``M c`` on every step, so a vessel of zero or negative
        volume is refused when it is built, with the configuration field to correct, rather than on its first step.
        For example, ``volume_heating_water_storage_in_liter = 0`` fails here.

        Args:
            water_mass_in_kg: The water the vessel holds, kg.
            storage_label: How the error message names the vessel, e.g. ``"DHW water storage"``.

        Raises:
            ValueError: If ``water_mass_in_kg`` is zero or negative.
        """
        if water_mass_in_kg <= 0:
            raise ValueError(
                f"The {storage_label} {self.component_name} holds {water_mass_in_kg} kg of water; "
                "a water storage needs a positive volume_heating_water_storage_in_liter."
            )

    #: Plausible range of a storage's converged mean water temperature. Outside it the run fails.
    PLAUSIBLE_MEAN_WATER_TEMPERATURE_RANGE_IN_CELSIUS: ClassVar[Tuple[float, float]] = (0.0, 90.0)

    def check_converged_mean_water_temperature(
        self, mean_water_temperature_in_celsius: float, storage_label: str
    ) -> None:
        """Fail the run when the converged mean water temperature leaves the plausible range.

        Called from ``i_doublecheck``, that is once per timestep on the value the timestep converged
        to. The check used to run at the top of ``i_simulate`` on whatever the previous call had
        computed, which inside a timestep is an intermediate iterate of the convergence loop: at
        timestep 0 of a 3600 s run the boiler controller first answers the zero-initialised storage
        input with a 70 K lift, the boiler adds it to the storage's real 60 degC, and one hour of
        that 130 degC flow mixes the vessel to above 90 degC in an iterate the next iteration
        discards (hisim-4g9.11). A converged value outside the range still fails, one timestep
        earlier than before and with the same message.
        """
        lowest, highest = self.PLAUSIBLE_MEAN_WATER_TEMPERATURE_RANGE_IN_CELSIUS
        if mean_water_temperature_in_celsius > highest or mean_water_temperature_in_celsius < lowest:
            raise ValueError(
                f"The water temperature in the {storage_label} is with {mean_water_temperature_in_celsius}°C"
                " way too high or too low."
            )

    def calculate_masses_of_water_flows(
        self,
        water_mass_flow_rate_from_heat_generator_in_kg_per_second: float,
        water_mass_flow_rate_of_secondary_side_in_kg_per_second: float,
        seconds_per_timestep: float,
    ) -> Any:
        """ "Calculate masses of the water flows in kg."""

        mass_of_input_water_flows_from_heat_generator_in_kg = (
            water_mass_flow_rate_from_heat_generator_in_kg_per_second * seconds_per_timestep
        )
        mass_of_input_water_flows_from_secondary_side_in_kg = (
            water_mass_flow_rate_of_secondary_side_in_kg_per_second * seconds_per_timestep
        )

        return (
            mass_of_input_water_flows_from_heat_generator_in_kg,
            mass_of_input_water_flows_from_secondary_side_in_kg,
        )

    def calculate_mean_water_temperature_in_water_storage(
        self,
        water_temperature_input_of_secondary_side_in_celsius: float,
        water_temperature_from_heat_generator_in_celsius: float,
        mass_of_input_water_flows_from_heat_generator_in_kg: float,
        mass_of_input_water_flows_of_secondary_side_in_kg: float,
        water_mass_in_storage_in_kg: float,
        previous_mean_water_temperature_in_water_storage_in_celsius: float,
        water_temperature_from_secondary_heat_generator_in_celsius: float = 0,
        mass_of_input_water_flows_from_secondary_heat_generator_in_kg: float = 0,
    ) -> float:
        """Calculate the mean temperature of the water in the water boiler."""

        mean_water_temperature_in_water_storage_in_celsius = (
            water_mass_in_storage_in_kg * previous_mean_water_temperature_in_water_storage_in_celsius
            + mass_of_input_water_flows_from_heat_generator_in_kg * water_temperature_from_heat_generator_in_celsius
            + mass_of_input_water_flows_from_secondary_heat_generator_in_kg * water_temperature_from_secondary_heat_generator_in_celsius
            + mass_of_input_water_flows_of_secondary_side_in_kg * water_temperature_input_of_secondary_side_in_celsius
        ) / (
            water_mass_in_storage_in_kg
            + mass_of_input_water_flows_from_heat_generator_in_kg
            + mass_of_input_water_flows_from_secondary_heat_generator_in_kg
            + mass_of_input_water_flows_of_secondary_side_in_kg
        )

        return mean_water_temperature_in_water_storage_in_celsius

    def calculate_mixing_factor_for_water_temperature_outputs(self) -> Any:
        """Calculate mixing factor for water outputs."""

        # mixing factor depends on seconds per timestep
        # if one timestep = 1h (3600s) or more, the factor for the water storage portion is one

        if 0 <= self.seconds_per_timestep <= 3600:
            factor_for_water_storage_portion = self.seconds_per_timestep / 3600
            factor_for_water_input_portion = 1 - factor_for_water_storage_portion

        elif self.seconds_per_timestep > 3600:
            factor_for_water_storage_portion = 1
            factor_for_water_input_portion = 0

        else:
            raise ValueError("unknown value for seconds per timestep")

        return factor_for_water_storage_portion, factor_for_water_input_portion

    def calculate_water_output_temperature(
        self,
        mean_water_temperature_in_water_storage_in_celsius: float,
        mixing_factor_water_storage_portion: float,
        mixing_factor_water_input_portion: float,
        water_input_temperature_in_celsius: float,
    ) -> float:
        """Calculate the water output temperature of the water storage."""

        water_temperature_output_in_celsius = (
            mixing_factor_water_input_portion * water_input_temperature_in_celsius
            + mixing_factor_water_storage_portion * mean_water_temperature_in_water_storage_in_celsius
        )

        return water_temperature_output_in_celsius

    def calculate_heat_loss_and_temperature_loss(
        self,
        storage_surface_in_m2: float,
        mean_water_temperature_in_water_storage_in_celsius: float,
        heat_transfer_coefficient_in_watt_per_m2_per_kelvin: float,
        ambient_temperature_in_celsius: float,
        mass_in_storage_in_kg: float,
    ) -> Tuple[float, float]:
        """Calculate heat energy loss in W and temperature loss in K/s.

        Calculate the heat energy loss in W and the temperature loss in K/s of the water storage
        based on surface area, heat transfer coefficient, inner and outer temperature and water
        mass in storage.
        """
        heat_loss_in_watt = self.calculate_heat_loss_in_watt(
            mean_temperature_in_storage_in_celsius=mean_water_temperature_in_water_storage_in_celsius,
            storage_surface_in_m2=storage_surface_in_m2,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=heat_transfer_coefficient_in_watt_per_m2_per_kelvin,
            ambient_temperature_in_celsius=ambient_temperature_in_celsius,
        )

        # basis here: Q = m * cw * delta temperature, temperature loss is another term for delta temperature here
        temperature_loss_of_water_in_kelvin_per_s = heat_loss_in_watt / (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=lt.LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
            * mass_in_storage_in_kg
        )

        return heat_loss_in_watt, temperature_loss_of_water_in_kelvin_per_s

    def calculate_heat_loss_in_watt(
        self,
        storage_surface_in_m2: float,
        mean_temperature_in_storage_in_celsius: float,
        heat_transfer_coefficient_in_watt_per_m2_per_kelvin: float,
        ambient_temperature_in_celsius: float,
    ) -> float:
        """Calculate the current heat loss.

        It is dependent on storage surface area and current water temperature as well as heat transfer coefficient and ambient temperature.
        """

        # loss = heat coeff * surface * delta temperature
        heat_loss_in_watt = (
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin
            * storage_surface_in_m2
            * (mean_temperature_in_storage_in_celsius - ambient_temperature_in_celsius)
        )
        return heat_loss_in_watt

    def calculate_surface_area_of_storage(self, storage_volume_in_liter: float) -> float:
        """Calculate the surface area of the storage which is assumed to be a cylinder."""

        storage_volume_in_m3 = storage_volume_in_liter * 1e-3
        # volume = r^2 * pi * h = r^2 * pi * 4r = 4 * r^3 * pi
        radius_of_storage_in_m = (storage_volume_in_m3 / (4 * np.pi)) ** (1 / 3)

        # lateral surface = 2 * pi * r * h (h=4*r here)
        lateral_surface_in_m2 = 2 * radius_of_storage_in_m * np.pi * (4 * radius_of_storage_in_m)
        # circle surface
        circle_surface_in_m2 = np.pi * radius_of_storage_in_m**2

        # total storage surface
        # cylinder surface area = lateral surface +  2 * circle surface
        storage_surface_in_m2 = lateral_surface_in_m2 + 2 * circle_surface_in_m2

        return float(storage_surface_in_m2)

    #########################################################################################################################################################

    def calculate_thermal_energy_in_storage(
        self,
        mean_water_temperature_in_storage_in_celsius: float,
        mass_in_storage_in_kg: float,
    ) -> float:
        """Calculate thermal energy with respect to 0°C temperature."""
        # Q = c * m * (Tout - Tin)

        thermal_energy_in_storage_in_joule = (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=lt.LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
            * mass_in_storage_in_kg
            * (mean_water_temperature_in_storage_in_celsius)
        )  # T_mean - 0°C
        # 1Wh = J / 3600
        thermal_energy_in_storage_in_watt_hour = thermal_energy_in_storage_in_joule / 3600

        return thermal_energy_in_storage_in_watt_hour

    def calculate_thermal_energy_of_water_flow(
        self, water_mass_in_kg: float, water_temperature_difference_in_kelvin: float
    ) -> float:
        """Calculate thermal energy of the water flow with respect to 0°C temperature."""
        # Q = c * m * (Tout - Tin)
        thermal_energy_of_input_water_flow_in_watt_hour = (
            (1 / 3600)
            * PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=lt.LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
            * water_mass_in_kg
            * water_temperature_difference_in_kelvin
        )

        return thermal_energy_of_input_water_flow_in_watt_hour

    def calculate_thermal_energy_increase_or_decrease_in_storage(
        self,
        current_thermal_energy_in_storage_in_watt_hour: float,
        previous_thermal_energy_in_storage_in_watt_hour: float,
    ) -> float:
        """Calculate thermal energy difference of current and previous state."""
        thermal_energy_difference_in_watt_hour = (
            current_thermal_energy_in_storage_in_watt_hour - previous_thermal_energy_in_storage_in_watt_hour
        )

        return thermal_energy_difference_in_watt_hour

    def calculate_thermal_power_of_water_flow(
        self,
        water_mass_flow_in_kg_per_s: float,
        water_temperature_cold_in_celsius: float,
        water_temperature_hot_in_celsius: float,
    ) -> float:
        """Calculate thermal energy of the water flow with respect to 0°C temperature."""

        thermal_power_of_input_water_flow_in_watt = (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=lt.LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
            * water_mass_flow_in_kg_per_s
            * (water_temperature_hot_in_celsius - water_temperature_cold_in_celsius)
        )

        return thermal_power_of_input_water_flow_in_watt


class SimpleHotWaterStorage(SimpleWaterStorage):
    """SimpleHotWaterStorage class."""

    cost_relevance = CostRelevance.PRICED

    # Input
    # A hot water storage can be used also with more than one heat generator. In this case you need to add a new input and output.
    WaterTemperatureToHeatDistribution = "WaterTemperatureToHeatDistribution"
    WaterTemperatureFromHeatDistribution = "WaterTemperatureFromHeatDistribution"
    WaterTemperatureFromHeatGenerator = "WaterTemperatureFromHeatGenerator"
    WaterTemperatureFromSecondaryHeatGenerator = "WaterTemperatureFromSecondaryHeatGenerator"
    WaterMassFlowRateFromSecondaryHeatGenerator = "WaterMassFlowRateFromSecondaryHeatGenerator"
    WaterMassFlowRateFromHeatGenerator = "WaterMassFlowRateFromHeatGenerator"
    WaterMassFlowRateFromHeatDistributionSystem = "WaterMassFlowRateFromHeatDistributionSystem"
    State = "State"

    # Output

    WaterTemperatureToHeatGenerator = "WaterTemperatureToHeatGenerator"
    WaterTemperatureToSecondaryHeatGenerator = "WaterTemperatureToSecondaryHeatGenerator"
    WaterMeanTemperatureInStorage = "WaterMeanTemperatureInStorage"

    # make some more outputs for testing simple storage

    ThermalEnergyInStorage = "ThermalEnergyInStorage"
    ThermalEnergyFromHeatGenerator = "ThermalEnergyFromHeatGenerator"
    ThermalEnergyFromSecondaryHeatGenerator = "ThermalEnergyFromSecondaryHeatGenerator"
    ThermalEnergyFromHeatDistribution = "ThermalEnergyFromHeatDistribution"
    ThermalEnergyIncreaseInStorage = "ThermalEnergyIncreaseInStorage"

    StandbyHeatLoss = "StandbyHeatLoss"
    ThermalPowerConsumptionHeatDistribution = "ThermalPowerConsumptionHeatDistribution"
    ThermalPowerFromHeatGenerator = "ThermalPowerFromHeatGenerator"
    ThermalPowerFromSecondaryHeatGenerator = "ThermalPowerFromSecondaryHeatGenerator"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleHotWaterStorageConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        # =================================================================================================================================
        # Initialization of variables
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep
        self.waterstorageconfig = config

        self.mean_water_temperature_in_water_storage_in_celsius: float = 35

        self.position_hot_water_storage_in_system = self.waterstorageconfig.position_hot_water_storage_in_system
        self.build(heat_exchanger_is_present=self.waterstorageconfig.heat_exchanger_is_present)

        self.state: SimpleWaterStorageState = SimpleWaterStorageState(
            mean_water_temperature_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            temperature_loss_in_celsius_per_timestep=0,
        )
        self.previous_state = self.state.self_copy()

        # =================================================================================================================================
        # Input channels

        self.water_temperature_heat_distribution_system_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureFromHeatDistribution,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.water_mass_flow_rate_heat_distribution_system_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterMassFlowRateFromHeatDistributionSystem,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            False,
        )

        if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_SOURCE:
            self.water_temperature_heat_generator_input_channel: ComponentInput = self.add_input(
                self.component_name,
                self.WaterTemperatureFromHeatGenerator,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
                True,
            )

            self.water_mass_flow_rate_heat_generator_input_channel: ComponentInput = self.add_input(
                self.component_name,
                self.WaterMassFlowRateFromHeatGenerator,
                lt.LoadTypes.WARM_WATER,
                lt.Units.KG_PER_SEC,
                False,
            )
            self.water_temperature_secondary_heat_generator_input_channel: ComponentInput = self.add_input(
                self.component_name,
                self.WaterTemperatureFromSecondaryHeatGenerator,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
                False,
            )
            self.water_mass_flow_rate_secondary_heat_generator_input_channel: ComponentInput = self.add_input(
                self.component_name,
                self.WaterMassFlowRateFromSecondaryHeatGenerator,
                lt.LoadTypes.WARM_WATER,
                lt.Units.KG_PER_SEC,
                False,
            )

        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY, False
        )

        # Output channels

        self.water_temperature_heat_distribution_system_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureToHeatDistribution,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureToHeatDistribution} will follow.",
        )

        self.water_temperature_heat_generator_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureToHeatGenerator,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureToHeatGenerator} will follow.",
        )

        self.water_temperature_secondary_heat_generator_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureToSecondaryHeatGenerator,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureToSecondaryHeatGenerator} will follow.",
        )

        self.water_temperature_mean_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterMeanTemperatureInStorage,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterMeanTemperatureInStorage} will follow.",
        )

        self.thermal_energy_in_storage_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyInStorage,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyInStorage} will follow.",
        )
        self.thermal_energy_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyFromHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyFromHeatGenerator} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_energy_from_secondary_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyFromSecondaryHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyFromSecondaryHeatGenerator} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_energy_input_heat_distribution_system_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyFromHeatDistribution,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyFromHeatDistribution} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_energy_increase_in_storage_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyIncreaseInStorage,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyIncreaseInStorage} will follow.",
        )
        self.stand_by_heat_loss_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.StandbyHeatLoss,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.StandbyHeatLoss} will follow.",
        )
        self.thermal_power_heat_distribution_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerConsumptionHeatDistribution,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerConsumptionHeatDistribution} will follow.",
        )

        self.thermal_power_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerFromHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerFromHeatGenerator} will follow.",
        )

        self.thermal_power_from_secondary_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerFromSecondaryHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerFromSecondaryHeatGenerator} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_heat_distribution_system())
        self.add_default_connections(self.get_default_connections_from_more_advanced_heat_pump())
        self.add_default_connections(self.get_default_connections_from_generic_boiler())

    def get_default_connections_from_heat_distribution_system(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get heat distribution default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.heat_distribution_system"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "HeatDistribution")
        connections = []
        hds_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterTemperatureFromHeatDistribution,
                hds_classname,
                component_class.WaterTemperatureOutput,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterMassFlowRateFromHeatDistributionSystem,
                hds_classname,
                component_class.WaterMassFlowHDS,
            )
        )
        return connections

    def get_default_connections_from_more_advanced_heat_pump(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get advanced het pump default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.more_advanced_heat_pump_hplib"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "MoreAdvancedHeatPumpHPLib")
        connections = []
        hp_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterTemperatureFromHeatGenerator,
                hp_classname,
                component_class.TemperatureOutputSH,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterMassFlowRateFromHeatGenerator,
                hp_classname,
                component_class.MassFlowOutputSH,
            )
        )
        return connections

    def get_default_connections_from_generic_boiler(self) -> List[cp.ComponentConnection]:
        """Get gasheater default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.generic_boiler"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "GenericBoiler")
        connections = []
        gasheater_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterTemperatureFromHeatGenerator,
                gasheater_classname,
                component_class.WaterOutputTemperatureSh,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterMassFlowRateFromHeatGenerator,
                gasheater_classname,
                component_class.WaterOutputMassFlowSh,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.waterstorageconfig.get_string_dict()

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_state = self.state.self_copy()

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.state = self.previous_state.self_copy()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Check the converged mean water temperature of the timestep."""
        self.check_converged_mean_water_temperature(
            self.mean_water_temperature_in_water_storage_in_celsius, "water storage"
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the heating water storage."""

        # Get inputs --------------------------------------------------------------------------------------------------------

        state_controller = stsv.get_input_value(self.state_channel)

        water_temperature_from_heat_distribution_system_in_celsius = stsv.get_input_value(
            self.water_temperature_heat_distribution_system_input_channel
        )

        water_mass_flow_rate_from_hds_in_kg_per_second = stsv.get_input_value(
            self.water_mass_flow_rate_heat_distribution_system_input_channel
        )

        if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_SOURCE:
            water_temperature_from_heat_generator_in_celsius = stsv.get_input_value(
                self.water_temperature_heat_generator_input_channel
            )
            water_temperature_from_secondary_heat_generator_in_celsius = stsv.get_input_value(
                self.water_temperature_secondary_heat_generator_input_channel
            )

            water_mass_flow_rate_from_heat_generator_in_kg_per_second = stsv.get_input_value(
                self.water_mass_flow_rate_heat_generator_input_channel
            )
            water_mass_flow_rate_from_secondary_heat_generator_in_kg_per_second = stsv.get_input_value(
                self.water_mass_flow_rate_secondary_heat_generator_input_channel
            )
        else:
            water_temperature_from_heat_generator_in_celsius = 0
            water_mass_flow_rate_from_heat_generator_in_kg_per_second = 0
            water_temperature_from_secondary_heat_generator_in_celsius = 0
            water_mass_flow_rate_from_secondary_heat_generator_in_kg_per_second = 0

        # Calculations ------------------------------------------------------------------------------------------------------

        # calc water masses
        # ------------------------------
        (
            water_mass_from_heat_generator_in_kg,
            water_mass_from_heat_distribution_system_in_kg,
        ) = self.calculate_masses_of_water_flows(
            water_mass_flow_rate_from_heat_generator_in_kg_per_second=water_mass_flow_rate_from_heat_generator_in_kg_per_second,
            water_mass_flow_rate_of_secondary_side_in_kg_per_second=water_mass_flow_rate_from_hds_in_kg_per_second,
            seconds_per_timestep=self.seconds_per_timestep,
        )

        # Secondary
        (
            water_mass_from_secondary_heat_generator_in_kg,
            _,
        ) = self.calculate_masses_of_water_flows(
            water_mass_flow_rate_from_heat_generator_in_kg_per_second=water_mass_flow_rate_from_secondary_heat_generator_in_kg_per_second,
            water_mass_flow_rate_of_secondary_side_in_kg_per_second=water_mass_flow_rate_from_hds_in_kg_per_second,
            seconds_per_timestep=self.seconds_per_timestep,
        )

        # calc water temperatures
        # ------------------------------
        # mean temperature in storage when all water flows are mixed with previous mean water storage temp
        self.mean_water_temperature_in_water_storage_in_celsius = self.calculate_mean_water_temperature_in_water_storage(
            water_temperature_input_of_secondary_side_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
            water_temperature_from_heat_generator_in_celsius=water_temperature_from_heat_generator_in_celsius,
            water_mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
            mass_of_input_water_flows_from_heat_generator_in_kg=water_mass_from_heat_generator_in_kg,
            water_temperature_from_secondary_heat_generator_in_celsius=water_temperature_from_secondary_heat_generator_in_celsius,
            mass_of_input_water_flows_from_secondary_heat_generator_in_kg=water_mass_from_secondary_heat_generator_in_kg,
            mass_of_input_water_flows_of_secondary_side_in_kg=water_mass_from_heat_distribution_system_in_kg,
            previous_mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
        )

        # calc thermal energies
        # ------------------------------

        previous_thermal_energy_in_storage_in_watt_hour = self.calculate_thermal_energy_in_storage(
            mean_water_temperature_in_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
            mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
        )
        current_thermal_energy_in_storage_in_watt_hour = self.calculate_thermal_energy_in_storage(
            mean_water_temperature_in_storage_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
        )
        thermal_energy_increase_current_vs_previous_mean_temperature_in_watt_hour = (
            self.calculate_thermal_energy_increase_or_decrease_in_storage(
                current_thermal_energy_in_storage_in_watt_hour=current_thermal_energy_in_storage_in_watt_hour,
                previous_thermal_energy_in_storage_in_watt_hour=previous_thermal_energy_in_storage_in_watt_hour,
            )
        )

        thermal_energy_input_from_heat_generator_in_watt_hour = self.calculate_thermal_energy_of_water_flow(
            water_mass_in_kg=water_mass_from_heat_generator_in_kg,
            water_temperature_difference_in_kelvin=water_temperature_from_heat_generator_in_celsius
            - self.state.mean_water_temperature_in_celsius,
        )
        thermal_energy_input_from_heat_distribution_system_in_watt_hour = self.calculate_thermal_energy_of_water_flow(
            water_mass_in_kg=water_mass_from_heat_distribution_system_in_kg,
            water_temperature_difference_in_kelvin=water_temperature_from_heat_distribution_system_in_celsius
            - self.state.mean_water_temperature_in_celsius,
        )
        # Secondary heat generator
        thermal_energy_input_from_secondary_heat_generator_in_watt_hour = self.calculate_thermal_energy_of_water_flow(
            water_mass_in_kg=water_mass_from_secondary_heat_generator_in_kg,
            water_temperature_difference_in_kelvin=water_temperature_from_secondary_heat_generator_in_celsius
            - self.state.mean_water_temperature_in_celsius,
        )

        # with heat exchanger in water storage perfect heat exchange is possible
        if self.heat_exchanger_is_present is True:
            water_temperature_to_heat_distribution_system_in_celsius = self.state.mean_water_temperature_in_celsius
            water_temperature_to_heat_generator_in_celsius = self.state.mean_water_temperature_in_celsius
            water_temperature_to_secondary_heat_generator_in_celsius = self.state.mean_water_temperature_in_celsius

        # otherwise the water in the water storage is more stratified, which demands some more calculations
        else:
            # state controller is 1 if the heat generator delivers a mass flow rate input
            if state_controller == 1:
                # hds gets water from heat generator (if heat generator is not off, mass flow is not zero)
                water_temperature_to_heat_distribution_system_in_celsius = self.calculate_water_output_temperature(
                    mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
                    mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                    mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                    water_input_temperature_in_celsius=water_temperature_from_heat_generator_in_celsius,
                )
                # heat generator gets water from hds (if heat generator is not off, mass flow is not zero)
                water_temperature_to_heat_generator_in_celsius = self.calculate_water_output_temperature(
                    mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
                    mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                    mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                    water_input_temperature_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
                )
                water_temperature_to_secondary_heat_generator_in_celsius = self.calculate_water_output_temperature(
                    mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
                    mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                    mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                    water_input_temperature_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
                )

            # no water coming from heat generator, hds gets mean water and heat generator gets still water from hds
            elif state_controller == 0:
                water_temperature_to_heat_distribution_system_in_celsius = self.state.mean_water_temperature_in_celsius

                water_temperature_to_heat_generator_in_celsius = self.calculate_water_output_temperature(
                    mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
                    mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                    mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                    water_input_temperature_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
                )

                water_temperature_to_secondary_heat_generator_in_celsius = self.calculate_water_output_temperature(
                    mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
                    mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                    mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                    water_input_temperature_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
                )

            else:
                raise ValueError("unknown storage controller state.")

        # calc thermal power
        # ------------------------------
        thermal_power_from_heat_generator_in_watt = self.calculate_thermal_power_of_water_flow(
            water_mass_flow_in_kg_per_s=water_mass_flow_rate_from_heat_generator_in_kg_per_second,
            water_temperature_cold_in_celsius=self.state.mean_water_temperature_in_celsius,
            water_temperature_hot_in_celsius=water_temperature_from_heat_generator_in_celsius,
        )
        thermal_power_heat_distribution_in_watt = self.calculate_thermal_power_of_water_flow(
            water_mass_flow_in_kg_per_s=water_mass_flow_rate_from_hds_in_kg_per_second,
            water_temperature_cold_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
            water_temperature_hot_in_celsius=self.state.mean_water_temperature_in_celsius,
        )
        # secondary
        thermal_power_from_secondary_heat_generator_in_watt = self.calculate_thermal_power_of_water_flow(
            water_mass_flow_in_kg_per_s=water_mass_flow_rate_from_secondary_heat_generator_in_kg_per_second,
            water_temperature_cold_in_celsius=self.state.mean_water_temperature_in_celsius,
            water_temperature_hot_in_celsius=water_temperature_from_secondary_heat_generator_in_celsius,
        )

        # Set outputs -------------------------------------------------------------------------------------------------------
        if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_SOURCE:

            stsv.set_output_value(
                self.water_temperature_heat_distribution_system_output_channel,
                water_temperature_to_heat_distribution_system_in_celsius,
            )

        stsv.set_output_value(
            self.water_temperature_heat_generator_output_channel,
            water_temperature_to_heat_generator_in_celsius,
        )

        stsv.set_output_value(
            self.water_temperature_secondary_heat_generator_output_channel,
            water_temperature_to_secondary_heat_generator_in_celsius,
        )

        stsv.set_output_value(
            self.water_temperature_mean_channel,
            self.state.mean_water_temperature_in_celsius,
        )

        stsv.set_output_value(
            self.thermal_energy_in_storage_channel,
            current_thermal_energy_in_storage_in_watt_hour,
        )

        stsv.set_output_value(
            self.thermal_energy_from_heat_generator_channel,
            thermal_energy_input_from_heat_generator_in_watt_hour,
        )
        stsv.set_output_value(
            self.thermal_energy_from_secondary_heat_generator_channel,
            thermal_energy_input_from_secondary_heat_generator_in_watt_hour,
        )
        stsv.set_output_value(
            self.thermal_energy_input_heat_distribution_system_channel,
            thermal_energy_input_from_heat_distribution_system_in_watt_hour,
        )

        stsv.set_output_value(
            self.thermal_energy_increase_in_storage_channel,
            thermal_energy_increase_current_vs_previous_mean_temperature_in_watt_hour,
        )

        stsv.set_output_value(
            self.stand_by_heat_loss_channel,
            self.state.heat_loss_in_watt,
        )

        stsv.set_output_value(
            self.thermal_power_heat_distribution_channel,
            thermal_power_heat_distribution_in_watt,
        )

        stsv.set_output_value(
            self.thermal_power_from_heat_generator_channel,
            thermal_power_from_heat_generator_in_watt,
        )

        stsv.set_output_value(
            self.thermal_power_from_secondary_heat_generator_channel,
            thermal_power_from_secondary_heat_generator_in_watt,
        )
        # Set state -------------------------------------------------------------------------------------------------------

        # calc heat loss in W and the temperature loss
        self.state.heat_loss_in_watt, t_loss = self.calculate_heat_loss_and_temperature_loss(
            storage_surface_in_m2=self.storage_surface_in_m2,
            mean_water_temperature_in_water_storage_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin,
            mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
            ambient_temperature_in_celsius=self.ambient_temperature_in_celsius,
        )

        self.state.temperature_loss_in_celsius_per_timestep = t_loss * self.seconds_per_timestep

        self.state.mean_water_temperature_in_celsius = (
            self.mean_water_temperature_in_water_storage_in_celsius
            - self.state.temperature_loss_in_celsius_per_timestep
        )

    def build(self, heat_exchanger_is_present: bool) -> None:
        """Build function.

        The function sets important constants an parameters for the calculations.
        """
        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=lt.LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
        )
        self.specific_heat_capacity_of_water_in_watthour_per_kilogram_per_celsius = (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=lt.LoadTypes.WATER
            ).specific_heat_capacity_in_watthour_per_kg_per_kelvin
        )
        # https://www.internetchemie.info/chemie-lexikon/daten/w/wasser-dichtetabelle.php
        self.density_water_at_40_degree_celsius_in_kg_per_liter = 0.992

        # physical parameters of storage
        self.water_mass_in_storage_in_kg = (
            self.density_water_at_40_degree_celsius_in_kg_per_liter
            * concrete(self.waterstorageconfig.volume_heating_water_storage_in_liter)
        )
        self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin = (
            self.config.heat_transfer_coefficient_in_watt_per_m2_per_kelvin
        )
        self.storage_surface_in_m2 = self.calculate_surface_area_of_storage(
            storage_volume_in_liter=concrete(self.waterstorageconfig.volume_heating_water_storage_in_liter),
        )

        # the ambient temperature is here assumed as the basement temperature which is all year 17°C, this is where the water storage is located
        self.ambient_temperature_in_celsius = 20.0

        self.heat_exchanger_is_present = heat_exchanger_is_present
        # if heat exchanger is present, the heat is perfectly exchanged so the water output temperature corresponds to the mean temperature
        if self.heat_exchanger_is_present is True:
            (
                self.factor_for_water_storage_portion,
                self.factor_for_water_input_portion,
            ) = (1, 0)
        # if heat exchanger is not present, the water temperatures in the storage are more stratified
        # here a mixing factor is calcualted
        else:
            (
                self.factor_for_water_storage_portion,
                self.factor_for_water_input_portion,
            ) = self.calculate_mixing_factor_for_water_temperature_outputs()

    @staticmethod
    def get_cost_capex(
        config: SimpleHotWaterStorageConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime.

        This legacy capex path prices the buffer as ``THERMAL_ENERGY_STORAGE``, while the
        lifecycle cost engine's adapter (``hisim/economics/adapter.py``) declares it
        ``SPACE_HEATING_STORAGE``, a class of its own so the existing-asset register can tell the
        buffer from the hot-water cylinder (renovisorissues #48). The two classes are priced alike;
        this path and the report goldens keep the old class so that no golden moves.
        """
        kpi_tag = KpiTagEnumClass.STORAGE_HOT_WATER_SPACE_HEATING
        component_type = lt.ComponentType.THERMAL_ENERGY_STORAGE
        unit = lt.Units.LITER
        size_of_energy_system = concrete(config.volume_heating_water_storage_in_liter)

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=simulation_parameters,
        component_type=component_type,
        unit=unit,
        size_of_energy_system=size_of_energy_system,
        config=config,
        kpi_tag=kpi_tag
        )
        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(config=config, capex_cost_data_class=capex_cost_data_class)

        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for hot water storage."""
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ANY,
            kpi_tag=KpiTagEnumClass.STORAGE_HOT_WATER_SPACE_HEATING,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.StandbyHeatLoss and output.unit == lt.Units.WATT:
                    # calc heat loss
                    heat_loss_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    # get energy from power
                    heat_loss_in_kilowatt_hour = round(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=heat_loss_in_watt,
                            time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                        ),
                        1,
                    )
                    heat_loss_entry = KpiEntry(
                        name="Standby heat loss of Hot water storage",
                        unit="kWh",
                        value=heat_loss_in_kilowatt_hour,
                        tag=KpiTagEnumClass.STORAGE_HOT_WATER_SPACE_HEATING,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(heat_loss_entry)
        return list_of_kpi_entries


@dataclass(frozen=True)
class DhwTankStep:

    """One step of the hot-water tank: the node step, the hot water its tap valve let out, and the unmet share.

    ``node`` is the :class:`hisim.hydronics.NodeStep` of the tank with every inflow it had over the step: the supply
    of each charging circuit, in the order the tank lists them, and last the mains water that refilled the tank for
    the hot water the tap valve let out. ``hot_water_mass_flow_kg_per_s`` is that hot water, ``m_hot``, and
    ``unmet_fraction`` the share of the household's hot-water heat demand the tank could not cover, ``0`` while the
    step mean is above the tap temperature (:func:`hisim.hydronics.mixing_valve_draw`).
    """

    node: hydronics.NodeStep
    hot_water_mass_flow_kg_per_s: float
    unmet_fraction: float


class SimpleDHWStorage(SimpleWaterStorage):

    """The domestic-hot-water tank: a fully mixed node with one or two charging circuits and a tap.

    A node is a component that holds water and integrates its temperature over the step
    (:class:`hisim.hydronics.MixedNode`): with its start temperature ``T0``, the supply temperature and mass flow of
    each charging circuit, the cold mains water that refills what the tap takes and the standby loss to a 20 °C room,
    it computes in closed form the step mean ``T̄`` and the end temperature ``T_end``. Example: a 248 kg tank at
    ``T0 = 50 °C`` charged for 900 s by a boiler at 0.2 kg/s and 70 °C ends at 60.3 °C, with ``T̄ = 55.8 °C``.

    The tank publishes ``T̄`` as the return temperature of every charging circuit
    (``WaterTemperatureToHeatGenerator`` and ``WaterTemperatureToSecondaryHeatGenerator``), so the heat each circuit
    brings, ``m c (T_sup - T̄) dt``, is the same number for the generator and for the tank, and the tank's balance
    ``sum of circuits - tap - loss = C (T_end - T0)`` closes on every step. ``T0`` is published as
    ``WaterMeanTemperatureInStorage``: generator controllers decide on it, a value that stays constant while the
    step iterates. ``T_end`` is ``WaterTemperatureAtEndOfStep``, the next step's ``T0``.

    The tap is a thermostatic mixing valve (:func:`hisim.hydronics.mixing_valve_draw`): the household asks for
    ``m_d`` of warm water at ``T_warm`` (40 °C); above it the valve lets out only ``m_hot = m_d (T_warm - T_cold) /
    (T̄ - T_cold)`` of tank water, and the same mass of mains water at ``T_cold`` (10 °C) refills the tank. Because
    ``m_hot`` depends on ``T̄``, the tank solves the valve on its own step mean inside one call
    (:meth:`solve_tank_step`), so the heat drawn equals the demand exactly while ``T̄ > T_warm``. Below ``T_warm`` all
    of ``m_d`` leaves unmixed and the shortfall is ``ThermalEnergyUnmetDHW``.

    When the simulator iterates a step more than six times, the tank publishes an extrapolation of its own fixed
    point instead of its raw step mean (:func:`hisim.hydronics.accelerated_node_mean`), clamped to the range of the
    temperatures it mixes; this shortens the iteration and does not change where it ends. The first iteration of a
    step publishes ``T0``. Once the simulator forces convergence and the iteration cycles (:meth:`iteration_cycles`),
    the tank holds its published step mean for the rest of the step.
    """

    cost_relevance = CostRelevance.PRICED

    #: Litres to kilograms: the water density of the hydronics library, 0.992 kg/l (water at 40 °C).
    WATER_DENSITY_IN_KG_PER_LITER: ClassVar[float] = hydronics.WATER_DENSITY_KG_PER_M3 / 1000.0

    #: The temperature of the room the tank stands in, which it loses its standby heat to.
    AMBIENT_TEMPERATURE_IN_CELSIUS: ClassVar[float] = 20.0

    #: The tap valve's local solve stops when the step mean it assumed and the one it got differ by at most this.
    TAP_SOLVE_TOLERANCE_IN_KELVIN: ClassVar[float] = 1e-10

    #: The most evaluations the tap valve's local solve may take before it fails the run.
    TAP_SOLVE_MAXIMUM_ITERATIONS: ClassVar[int] = 200

    # Input
    # A hot water storage can be used also with more than one heat generator. In this case you need to add a new input and output.
    WaterTemperatureFromHeatGenerator = "WaterTemperatureFromHeatGenerator"
    WaterMassFlowRateFromHeatGenerator = "WaterMassFlowRateFromHeatGenerator"
    WaterTemperatureFromSecondaryHeatGenerator = "WaterTemperatureFromSecondaryHeatGenerator"
    WaterMassFlowRateFromSecondaryHeatGenerator = "WaterMassFlowRateFromSecondaryHeatGenerator"
    WaterConsumption = "WaterConsumption"

    # Output
    WaterTemperatureToHeatGenerator = "WaterTemperatureToHeatGenerator"
    WaterTemperatureToSecondaryHeatGenerator = "WaterTemperatureToSecondaryHeatGenerator"
    WaterTemperatureFromHeatGeneratorOutput = "WaterTemperatureFromHeatGenerator"
    WaterTemperatureFromSecondaryHeatGeneratorOutput = "WaterTemperatureFromSecondaryHeatGenerator"
    WaterMeanTemperatureInStorage = "WaterMeanTemperatureInStorage"
    WaterTemperatureAtEndOfStep = "WaterTemperatureAtEndOfStep"
    StandbyTemperatureLoss = "StandbyTemperatureLoss"
    ThermalEnergyInStorage = "ThermalEnergyInStorage"
    ThermalEnergyFromHeatGenerator = "ThermalEnergyFromHeatGenerator"
    ThermalEnergyFromSecondaryHeatGenerator = "ThermalEnergyFromSecondaryHeatGenerator"
    ThermalEnergyConsumptionDHW = "ThermalEnergyConsumptionDHW"
    ThermalEnergyUnmetDHW = "ThermalEnergyUnmetDHW"
    ThermalEnergyIncreaseInStorage = "ThermalEnergyIncreaseInStorage"
    ThermalPowerConsumptionDHW = "ThermalPowerConsumptionDHW"
    ThermalPowerFromHeatGenerator = "ThermalPowerFromHeatGenerator"
    ThermalPowerFromSecondaryHeatGenerator = "ThermalPowerFromSecondaryHeatGenerator"
    StandbyHeatLoss = "StandbyHeatLoss"
    WaterMassFlowRateOfDHW = "WaterMassFlowRateOfDHW"
    HotWaterMassFlowRateFromStorage = "HotWaterMassFlowRateFromStorage"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleDHWStorageConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        # =================================================================================================================================
        # Initialization of variables
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep
        self.waterstorageconfig = config

        self.mean_water_temperature_in_water_storage_in_celsius: float = 60

        self.build()

        self.state: SimpleWaterStorageState = SimpleWaterStorageState(
            mean_water_temperature_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            temperature_loss_in_celsius_per_timestep=0,
            heat_loss_in_watt=0,
        )
        self.previous_state = self.state.self_copy()
        # The node's own iteration history on the current step, for the acceleration of its published step mean:
        # the mean it had published when it computed each new one, and that new one. Reset at every step's start.
        self.published_step_means_in_celsius: List[float] = []
        self.computed_step_means_in_celsius: List[float] = []
        # The step mean the tank published last, which the generators' current supply temperatures answer.
        self.last_published_step_mean_in_celsius: float = self.mean_water_temperature_in_water_storage_in_celsius
        # Whether the tank holds its published step mean for the rest of the current step (see i_simulate).
        self.holding_step_mean: bool = False

        # =================================================================================================================================
        # Input channels

        self.water_consumption_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterConsumption,
            lt.LoadTypes.WARM_WATER,
            lt.Units.LITER,
            True,
        )
        self.water_temperature_heat_generator_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureFromHeatGenerator,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.water_mass_flow_rate_heat_generator_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterMassFlowRateFromHeatGenerator,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            True,
        )

        self.water_temperature_secondary_heat_generator_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureFromSecondaryHeatGenerator,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            False,
        )
        self.water_mass_flow_rate_secondary_heat_generator_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterMassFlowRateFromSecondaryHeatGenerator,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            False,
        )

        # Output channels

        self.water_temperature_to_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureToHeatGenerator,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=(
                "The tank's step mean temperature T̄, the return temperature of the primary charging circuit: the "
                "mean of the tank temperature over the step, which makes m c (T_sup - T̄) the heat the circuit brings."
            ),
        )

        self.water_temperature_secondary_heat_generator_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureToSecondaryHeatGenerator,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=(
                "The tank's step mean temperature T̄, the return temperature of the secondary charging circuit."
            ),
        )

        self.water_temperature_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureFromHeatGeneratorOutput,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="Supply temperature [°C] of the primary charging circuit, as the tank received it.",
        )

        self.water_temperature_from_secondary_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureFromSecondaryHeatGeneratorOutput,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="Water temperature [°C] from secondary DHW heat generator",
        )

        self.water_temperature_mean_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterMeanTemperatureInStorage,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=(
                "The tank's temperature at the start of the step, T0, the end temperature of the step before. "
                "Generator controllers decide on it, since it does not change while the step iterates."
            ),
        )

        self.water_temperature_at_end_of_step_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureAtEndOfStep,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="The tank's temperature at the end of the step, T_end: the next step's T0.",
        )

        self.temperature_loss_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.StandbyTemperatureLoss,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="The standby heat loss of the step divided by the tank's heat capacity, in kelvin.",
        )

        self.thermal_energy_in_storage_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyInStorage,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description="The heat the tank holds at the end of the step, counted from 0 °C: C T_end.",
        )
        self.thermal_energy_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyFromHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description="The heat the primary charging circuit brought over the step, m c (T_sup - T̄) dt.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_energy_from_secondary_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyFromSecondaryHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description="The heat the secondary charging circuit brought over the step, m c (T_sup - T̄) dt.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_energy_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyConsumptionDHW,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=(
                "The heat the tap drew over the step, booked negative: m_hot c (T_cold - T̄) dt. It equals the "
                "household's hot-water demand while T̄ is above the tap temperature, and falls short of it below."
            ),
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_energy_unmet_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyUnmetDHW,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=(
                "The hot-water heat demand of the step the tank could not cover: with T̄ at or below the tap "
                "temperature the mixing valve passes tank water only, m_d c (T_warm - T̄) dt; zero above it."
            ),
        )

        self.thermal_energy_increase_in_storage_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyIncreaseInStorage,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description="The change of the heat the tank holds over the step, C (T_end - T0).",
        )

        self.stand_by_heat_loss_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.StandbyHeatLoss,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description="The standby heat loss of the step to the room, UA (T̄ - T_amb).",
        )

        self.thermal_power_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerConsumptionDHW,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description="The heat flow the tap drew over the step, m_hot c (T̄ - T_cold), positive.",
        )

        self.thermal_power_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerFromHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description="The heat flow of the primary charging circuit, m c (T_sup - T̄).",
        )
        self.thermal_power_from_secondary_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerFromSecondaryHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description="The heat flow of the secondary charging circuit, m c (T_sup - T̄).",
        )
        self.water_mass_flow_rate_dhw_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterMassFlowRateOfDHW,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            output_description="The warm water the household asks for at the tap temperature, m_d.",
        )
        self.hot_water_mass_flow_rate_from_storage_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.HotWaterMassFlowRateFromStorage,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            output_description=(
                "The tank water the tap valve lets out, m_hot, refilled by the same mass of mains water: m_d "
                "(T_warm - T_cold) / (T̄ - T_cold) above the tap temperature, all of m_d at or below it."
            ),
        )

        self.add_default_connections(self.get_default_connections_from_more_advanced_heat_pump())
        self.add_default_connections(self.get_default_connections_from_generic_dhw_boiler())
        self.add_default_connections(self.get_default_connections_from_district_heating())
        self.add_default_connections(self.get_default_connections_from_utsp())
        self.add_default_connections(self.get_default_connections_from_solar_thermal_system())
        self.add_default_connections(self.get_default_connections_from_electric_heating())

    def get_default_connections_from_more_advanced_heat_pump(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get advanced het pump default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.more_advanced_heat_pump_hplib"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "MoreAdvancedHeatPumpHPLib")
        connections = []
        hp_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterTemperatureFromHeatGenerator,
                hp_classname,
                component_class.TemperatureOutputDHW,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterMassFlowRateFromHeatGenerator,
                hp_classname,
                component_class.MassFlowOutputDHW,
            )
        )
        return connections

    def get_default_connections_from_utsp(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get advanced het pump default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.loadprofilegenerator_utsp_connector"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "UtspLpgConnector")
        connections = []
        utsp_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterConsumption,
                utsp_classname,
                component_class.WaterConsumption,
            )
        )
        return connections

    def get_default_connections_from_generic_dhw_boiler(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get generic dhw boiler default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.generic_boiler"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "GenericBoiler")
        connections = []
        dhw_boiler_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterTemperatureFromHeatGenerator,
                dhw_boiler_classname,
                component_class.WaterOutputTemperatureDhw,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterMassFlowRateFromHeatGenerator,
                dhw_boiler_classname,
                component_class.WaterOutputMassFlowDhw,
            )
        )
        return connections

    def get_default_connections_from_solar_thermal_system(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get solar thermal system default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.solar_thermal_system"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "SolarThermalSystem")
        connections = []
        solar_thermal_system_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterTemperatureFromHeatGenerator,
                solar_thermal_system_classname,
                component_class.WaterTemperatureOutput,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterMassFlowRateFromHeatGenerator,
                solar_thermal_system_classname,
                component_class.WaterMassFlowOutput,
            )
        )
        return connections

    def get_default_connections_from_district_heating(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get dhw district heating default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.generic_district_heating"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "DistrictHeating")
        connections = []
        component_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterTemperatureFromHeatGenerator,
                component_classname,
                component_class.WaterOutputDhwTemperature,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterMassFlowRateFromHeatGenerator,
                component_classname,
                component_class.WaterOutputDhwMassFlowRate,
            )
        )
        return connections

    def get_default_connections_from_electric_heating(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get dhw electric heating default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.generic_electric_heating"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "ElectricHeating")
        connections = []
        component_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterTemperatureFromHeatGenerator,
                component_classname,
                component_class.WaterOutputDhwTemperature,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterMassFlowRateFromHeatGenerator,
                component_classname,
                component_class.WaterOutputDhwMassFlowRate,
            )
        )
        return connections

    def build(
        self,
    ) -> None:
        """Set the tank's constants: tap temperatures, water mass, heat capacity and loss coefficient.

        The heat capacity is ``C = M c`` with ``M`` the volume times 0.992 kg/l and ``c`` the hydronics library's
        4180 J/(kg K); the loss coefficient is ``UA``, the configured heat transfer coefficient times the surface of a
        cylinder four radii high. For example, the 250 l preset tank holds 248 kg, ``C = 1.04 MJ/K``, and loses
        ``UA = 0.83 W/K`` to the room.

        Raises:
            ValueError: If the tank holds no water (a volume of zero or less).
        """
        self.drain_water_temperature = configuration.HouseholdWarmWaterDemandConfig.freshwater_temperature

        self.warm_water_temperature = (
            configuration.HouseholdWarmWaterDemandConfig.ww_temperature_demand
            - configuration.HouseholdWarmWaterDemandConfig.temperature_difference_hot
        )

        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = (
            hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K
        )
        self.density_water_at_40_degree_celsius_in_kg_per_liter = self.WATER_DENSITY_IN_KG_PER_LITER

        # physical parameters of storage
        self.water_mass_in_storage_in_kg = (
            self.density_water_at_40_degree_celsius_in_kg_per_liter
            * concrete(self.waterstorageconfig.volume_heating_water_storage_in_liter)
        )
        self.check_water_mass(self.water_mass_in_storage_in_kg, "DHW water storage")
        self.heat_capacity_in_joule_per_kelvin = (
            self.water_mass_in_storage_in_kg * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
        )
        self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin = (
            self.waterstorageconfig.heat_transfer_coefficient_in_watt_per_m2_per_kelvin
        )
        self.storage_surface_in_m2 = self.calculate_surface_area_of_storage(
            storage_volume_in_liter=concrete(self.waterstorageconfig.volume_heating_water_storage_in_liter),
        )
        self.loss_coefficient_in_watt_per_kelvin = (
            self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin * self.storage_surface_in_m2
        )

        self.ambient_temperature_in_celsius = self.AMBIENT_TEMPERATURE_IN_CELSIUS

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.waterstorageconfig.get_string_dict()

    def i_save_state(self) -> None:
        """Save the state at the start of a step and start the step's iteration history afresh.

        The simulator calls this once per step, before the step's first iteration; the history the node accelerates
        its published step mean from belongs to one step only.
        """
        self.previous_state = self.state.self_copy()
        self.published_step_means_in_celsius = []
        self.computed_step_means_in_celsius = []
        self.holding_step_mean = False

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.state = self.previous_state.self_copy()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Check the converged mean water temperature of the timestep."""
        self.check_converged_mean_water_temperature(
            self.mean_water_temperature_in_water_storage_in_celsius, "DHW water storage"
        )

    def solve_tank_step(
        self, t0_c: float, generator_inflows: Sequence[hydronics.Inflow], demand_kg_per_s: float
    ) -> DhwTankStep:
        """Integrate the tank over one step with its charging circuits and its tap valve (spec §4.1, §4.3).

        The tap valve lets out ``m_hot`` of tank water, which depends on the tank's step mean ``T̄``
        (:func:`hisim.hydronics.mixing_valve_draw`), and ``T̄`` depends on ``m_hot`` through the node step
        (:meth:`hisim.hydronics.MixedNode.step`, with the mains refill as one more inflow at ``T_cold``). The method
        solves ``g(x) = x``, where ``g(x)`` is the node's step mean when the valve answers an assumed step mean
        ``x``, by the Illinois variant of regula falsi on ``g(x) - x`` over the range of every temperature the tank
        mixes (``T0``, the supplies that flow, the room, the mains): ``g`` never leaves that range, so the range
        brackets the solution. The solve is deterministic, needs no start value from an earlier iteration, and stops at
        :data:`TAP_SOLVE_TOLERANCE_IN_KELVIN`. Without a demand the node step is evaluated once.

        Example: a 248 kg tank at 55 °C asked for 0.05 kg/s of 40 °C water over 900 s cools to a step mean of
        52.2 °C and lets out 0.0355 kg/s, about 30 % less than the demand; the heat drawn,
        ``m_hot c (T̄ - 10) dt``, equals the demand ``0.05 c (40 - 10) dt`` to the tolerance.

        Args:
            t0_c: The tank's temperature at the start of the step, °C.
            generator_inflows: The supply of each charging circuit, in the order the tank books them.
            demand_kg_per_s: The warm water the household asks for at the tap temperature, kg/s.

        Returns:
            The node step with the refill as its last inflow, ``m_hot`` and the unmet share of the demand.

        Raises:
            RuntimeError: If the solve has not converged after :data:`TAP_SOLVE_MAXIMUM_ITERATIONS` evaluations.
        """
        t_warm = self.warm_water_temperature
        t_cold = self.drain_water_temperature

        def node_step(hot_water_kg_per_s: float) -> hydronics.NodeStep:
            return hydronics.MixedNode.step(
                t0_c,
                list(generator_inflows) + [hydronics.Inflow(hot_water_kg_per_s, t_cold)],
                self.loss_coefficient_in_watt_per_kelvin,
                self.ambient_temperature_in_celsius,
                self.seconds_per_timestep,
                self.heat_capacity_in_joule_per_kelvin,
            )

        def answered(assumed_mean_c: float) -> DhwTankStep:
            hot_water_kg_per_s, unmet_fraction = hydronics.mixing_valve_draw(
                assumed_mean_c, t_warm, t_cold, demand_kg_per_s
            )
            return DhwTankStep(node_step(hot_water_kg_per_s), hot_water_kg_per_s, unmet_fraction)

        if demand_kg_per_s <= 0.0:
            return DhwTankStep(node_step(0.0), 0.0, 0.0)

        temperatures = [t0_c, t_cold, self.ambient_temperature_in_celsius] + [
            inflow.temperature_c for inflow in generator_inflows if inflow.mass_flow_kg_per_s > 0.0
        ]
        low, high = min(temperatures), max(temperatures)
        low_step, high_step = answered(low), answered(high)
        low_residual = low_step.node.t_mean_c - low
        high_residual = high_step.node.t_mean_c - high
        if low_residual <= self.TAP_SOLVE_TOLERANCE_IN_KELVIN:
            return low_step
        if high_residual >= -self.TAP_SOLVE_TOLERANCE_IN_KELVIN:
            return high_step
        side = 0
        for _ in range(self.TAP_SOLVE_MAXIMUM_ITERATIONS):
            assumed = (low * high_residual - high * low_residual) / (high_residual - low_residual)
            candidate = answered(assumed)
            residual = candidate.node.t_mean_c - assumed
            if abs(residual) <= self.TAP_SOLVE_TOLERANCE_IN_KELVIN or high - low <= self.TAP_SOLVE_TOLERANCE_IN_KELVIN:
                return candidate
            if residual > 0.0:
                low, low_residual = assumed, residual
                if side == -1:
                    high_residual /= 2.0
                side = -1
            else:
                high, high_residual = assumed, residual
                if side == 1:
                    low_residual /= 2.0
                side = 1
        raise RuntimeError(
            f"{self.component_name}: the tap valve's step mean did not converge within "
            f"{self.TAP_SOLVE_MAXIMUM_ITERATIONS} evaluations (bracket {low} to {high} °C)."
        )

    #: How many of the latest residuals the tank inspects for a cycle.
    CYCLE_RESIDUALS: ClassVar[int] = 3

    #: The largest residual of a cycle the tank holds: a smaller jump than this between two grid cells, K.
    CYCLE_RESIDUAL_LIMIT_IN_KELVIN: ClassVar[float] = 0.05

    def iteration_cycles(self) -> bool:
        """Whether the step's iteration cycles near a point instead of contracting to it.

        The residual of an iteration is the step mean the tank computed minus the one it had published. A contraction
        keeps one sign; a generator whose answer jumps at the edge of a grid cell makes the residuals change sign
        again and again at the size of the jump. The iteration cycles when the last :data:`CYCLE_RESIDUALS`
        residuals change sign at least once and are all smaller than :data:`CYCLE_RESIDUAL_LIMIT_IN_KELVIN`. For
        example, +0.003, -0.002 and -0.001 K cycle; +0.2, +0.1 and +0.05 K do not, and neither do +0.3, -0.2 and
        +0.1 K, an iteration still far from its point.

        Returns:
            True when the iteration cycles.
        """
        count = self.CYCLE_RESIDUALS
        if len(self.computed_step_means_in_celsius) < count:
            return False
        residuals = [
            computed - published
            for computed, published in zip(
                self.computed_step_means_in_celsius[-count:], self.published_step_means_in_celsius[-count:]
            )
        ]
        changes_sign = any(earlier * later < 0.0 for earlier, later in zip(residuals, residuals[1:]))
        return changes_sign and max(abs(residual) for residual in residuals) < self.CYCLE_RESIDUAL_LIMIT_IN_KELVIN

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Integrate the tank over the step and publish its step mean to its charging circuits.

        Reads the supply temperature and mass flow of both charging circuits and the household's warm-water demand,
        solves the step with the tap valve (:meth:`solve_tank_step`), publishes the step mean ``T̄`` as both
        circuits' return temperature (accelerated after six iterations, :func:`hisim.hydronics.accelerated_node_mean`)
        and books every heat flow from the same node step, so the balance closes. The end temperature becomes the
        state the next step starts from.
        """
        demand_kg_per_s = (
            stsv.get_input_value(self.water_consumption_channel)
            * self.density_water_at_40_degree_celsius_in_kg_per_liter
            / self.seconds_per_timestep
        )
        supply_primary_c = stsv.get_input_value(self.water_temperature_heat_generator_input_channel)
        mass_flow_primary_kg_per_s = stsv.get_input_value(self.water_mass_flow_rate_heat_generator_input_channel)
        supply_secondary_c = stsv.get_input_value(self.water_temperature_secondary_heat_generator_input_channel)
        mass_flow_secondary_kg_per_s = stsv.get_input_value(
            self.water_mass_flow_rate_secondary_heat_generator_input_channel
        )

        t0_c = self.state.mean_water_temperature_in_celsius
        generator_inflows = [
            hydronics.Inflow(mass_flow_primary_kg_per_s, supply_primary_c),
            hydronics.Inflow(mass_flow_secondary_kg_per_s, supply_secondary_c),
        ]
        tank = self.solve_tank_step(t0_c, generator_inflows, demand_kg_per_s)
        node = tank.node
        heat_primary_j, heat_secondary_j, heat_tap_j = node.heat_in_j_per_inflow
        dt = self.seconds_per_timestep

        # The step mean the circuits see: the plain iterate, accelerated after six iterations on the step and kept
        # inside the range of the temperatures the tank mixes.
        self.published_step_means_in_celsius.append(self.last_published_step_mean_in_celsius)
        self.computed_step_means_in_celsius.append(node.t_mean_c)
        published_mean_c = hydronics.accelerated_node_mean(
            self.published_step_means_in_celsius, self.computed_step_means_in_celsius
        )
        mixed_temperatures = [t0_c, self.drain_water_temperature, self.ambient_temperature_in_celsius] + [
            inflow.temperature_c for inflow in generator_inflows if inflow.mass_flow_kg_per_s > 0.0
        ]
        published_mean_c = min(max(published_mean_c, min(mixed_temperatures)), max(mixed_temperatures))
        if len(self.computed_step_means_in_celsius) == 1:
            # The first iteration of a step starts the circuits from the start temperature: the simulator begins
            # every step from zeroed outputs, so a generator simulated before the tank has answered a 0 °C return,
            # and the step mean computed from that answer is a worse start than T0.
            published_mean_c = t0_c
        elif self.holding_step_mean or (force_convergence and self.iteration_cycles()):
            # Once the simulator forces convergence and the iteration cycles, the tank holds the step
            # mean it published last for the rest of the step, as the controllers hold their decisions: a generator
            # whose answer jumps between two cells of hplib's 0.1 K grid would otherwise keep the iteration cycling
            # until the simulator aborts the run (spec §5.2, §6).
            self.holding_step_mean = True
            published_mean_c = self.last_published_step_mean_in_celsius
        self.last_published_step_mean_in_celsius = published_mean_c

        demand_heat_j = (
            demand_kg_per_s
            * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            * (self.warm_water_temperature - self.drain_water_temperature)
            * dt
        )
        joules_per_watt_hour = 3600.0

        stsv.set_output_value(self.water_temperature_to_heat_generator_channel, published_mean_c)
        stsv.set_output_value(self.water_temperature_secondary_heat_generator_output_channel, published_mean_c)
        stsv.set_output_value(self.water_temperature_from_heat_generator_channel, supply_primary_c)
        stsv.set_output_value(self.water_temperature_from_secondary_heat_generator_channel, supply_secondary_c)
        stsv.set_output_value(self.water_temperature_mean_channel, t0_c)
        stsv.set_output_value(self.water_temperature_at_end_of_step_channel, node.t_end_c)
        stsv.set_output_value(self.temperature_loss_channel, node.loss_j / self.heat_capacity_in_joule_per_kelvin)
        stsv.set_output_value(
            self.thermal_energy_in_storage_channel,
            self.heat_capacity_in_joule_per_kelvin * node.t_end_c / joules_per_watt_hour,
        )
        stsv.set_output_value(self.thermal_energy_from_heat_generator_channel, heat_primary_j / joules_per_watt_hour)
        stsv.set_output_value(
            self.thermal_energy_from_secondary_heat_generator_channel, heat_secondary_j / joules_per_watt_hour
        )
        stsv.set_output_value(self.thermal_energy_dhw_channel, heat_tap_j / joules_per_watt_hour)
        stsv.set_output_value(
            self.thermal_energy_unmet_dhw_channel, tank.unmet_fraction * demand_heat_j / joules_per_watt_hour
        )
        stsv.set_output_value(
            self.thermal_energy_increase_in_storage_channel,
            self.heat_capacity_in_joule_per_kelvin * (node.t_end_c - t0_c) / joules_per_watt_hour,
        )
        stsv.set_output_value(self.stand_by_heat_loss_channel, node.loss_j / dt)
        stsv.set_output_value(self.thermal_power_dhw_channel, -heat_tap_j / dt)
        stsv.set_output_value(self.thermal_power_from_heat_generator_channel, heat_primary_j / dt)
        stsv.set_output_value(self.thermal_power_from_secondary_heat_generator_channel, heat_secondary_j / dt)
        stsv.set_output_value(self.water_mass_flow_rate_dhw_output_channel, demand_kg_per_s)
        stsv.set_output_value(self.hot_water_mass_flow_rate_from_storage_channel, tank.hot_water_mass_flow_kg_per_s)

        # Set state: the end temperature is the next step's start temperature.
        self.state.heat_loss_in_watt = node.loss_j / dt
        self.state.temperature_loss_in_celsius_per_timestep = node.loss_j / self.heat_capacity_in_joule_per_kelvin
        self.state.mean_water_temperature_in_celsius = node.t_end_c
        self.mean_water_temperature_in_water_storage_in_celsius = node.t_end_c

    @staticmethod
    def get_cost_capex(
        config: SimpleDHWStorageConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime.

        This legacy capex path prices the cylinder as ``THERMAL_ENERGY_STORAGE``, while the
        lifecycle cost engine's adapter (``hisim/economics/adapter.py``) declares it
        ``DOMESTIC_HOT_WATER_STORAGE``, a class of its own so the existing-asset register can tell
        the cylinder from the space-heating buffer (renovisorissues #48). The two classes are priced
        alike; this path and the report goldens keep the old class so that no golden moves.
        """
        kpi_tag = KpiTagEnumClass.STORAGE_DOMESTIC_HOT_WATER
        component_type = lt.ComponentType.THERMAL_ENERGY_STORAGE
        unit = lt.Units.LITER
        size_of_energy_system = concrete(config.volume_heating_water_storage_in_liter)

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=simulation_parameters,
        component_type=component_type,
        unit=unit,
        size_of_energy_system=size_of_energy_system,
        config=config,
        kpi_tag=kpi_tag
        )

        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for hot water storage."""
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ANY,
            kpi_tag=KpiTagEnumClass.STORAGE_HOT_WATER_SPACE_HEATING,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.StandbyHeatLoss and output.unit == lt.Units.WATT:
                    # calc heat loss
                    heat_loss_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    # get energy from power
                    heat_loss_in_kilowatt_hour = round(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=heat_loss_in_watt,
                            time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                        ),
                        1,
                    )
                    heat_loss_entry = KpiEntry(
                        name="Standby heat loss of DHW storage",
                        unit="kWh",
                        value=heat_loss_in_kilowatt_hour,
                        tag=KpiTagEnumClass.STORAGE_DOMESTIC_HOT_WATER,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(heat_loss_entry)
                if output.field_name == self.ThermalEnergyUnmetDHW and output.unit == lt.Units.WATT_HOUR:
                    # the hot-water heat the tank could not cover, summed over the run
                    unmet_in_kilowatt_hour = round(float(postprocessing_results.iloc[:, index].sum()) * 1e-3, 1)
                    list_of_kpi_entries.append(
                        KpiEntry(
                            name="Unmet DHW heat demand",
                            unit="kWh",
                            value=unmet_in_kilowatt_hour,
                            tag=KpiTagEnumClass.STORAGE_DOMESTIC_HOT_WATER,
                            description=self.component_name,
                        )
                    )
        return list_of_kpi_entries
