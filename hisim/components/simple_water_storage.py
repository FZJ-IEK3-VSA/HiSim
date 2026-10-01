"""Simple Water Storage Module for dhw storage and hot water storage for heating."""

# Owned
import importlib
from dataclasses import dataclass
from typing import ClassVar, Dict, List, Any, Tuple, Optional
from enum import Enum, unique
import numpy as np
import pandas as pd
from dataclasses_json import dataclass_json

import hisim.component as cp
from hisim import loadtypes as lt
from hisim import utils
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
    maximal_thermal_power_in_watt = ctx.maximal_thermal_power_in_watt
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
    volume_heating_water_storage_in_liter: Sizable[float] = sized_field(rule=VOLUME_LAW)

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

    # set by the subclasses' build and __init__
    storage_surface_in_m2: float
    heat_transfer_coefficient_in_watt_per_m2_per_kelvin: float
    ambient_temperature_in_celsius: float
    water_mass_in_storage_in_kg: float
    thermal_power_draw_forecast_channel: ComponentOutput
    water_mass_in_storage_channel: ComponentOutput

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

    def check_water_mass(self, storage_label: str) -> None:
        """Refuse a vessel that holds no water.

        Every step divides by the vessel's heat capacity ``M c``; a volume of zero or less would divide by zero
        or run the energy balance backwards, so it is refused when the storage is built.
        """
        if self.water_mass_in_storage_in_kg <= 0:
            raise ValueError(
                f"The {storage_label} {self.component_name} holds {self.water_mass_in_storage_in_kg} kg of water; "
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

    # Energy balance of the vessel (hisim-4g9.16, owner decision of 2026-09-27, option B) ----------------------------
    #
    # The vessel is one well-mixed mass M at the start-of-step temperature T0. Per timestep it takes the heat its
    # generators deliver, gives the heat its consumer draws, and then loses its standby heat:
    #
    #     T_new = T0 + (Q_gen - Q_draw) / (M c),        T_end = T_new - Q_loss(T_new) / (M c)
    #
    # so Q_gen - Q_draw - Q_loss - M c (T_end - T0) is zero on every step. The vessel used to mix the generator's
    # *mass* into itself, (M T0 + m T_supply) / (M + m), while the generator booked m c (T_supply - T0): the share
    # m / (M + m) of every delivery was paid for and never arrived, 45-59 % of the hot-water heat at 900 s.
    #
    # What a generator's flow offers is m c (T_supply - T_return), T_return being the temperature the vessel told it.
    # The vessel accepts that, but no more than keeps its own temperature at or below the supply temperature:
    #
    #     Q_accepted <= M c (T_supply - T0) + Q_draw
    #
    # the draw of the same step included, because a well-mixed vessel that is drawn from while it is charged stays
    # below the supply temperature as long as the *net* heat does. A flow at or below the vessel's temperature is
    # accepted as nothing: no negative delivery, so a collector running cold or a cooling machine does not cool the
    # vessel and is booked nothing. The heat accepted is published per generator slot (``ThermalPowerFromHeatGenerator``
    # and ``ThermalPowerFromSecondaryHeatGenerator``), and every generator connected to it books -- and buys fuel or
    # electricity for -- that heat instead of its own.

    #: Output names shared by both vessels: what a generator controller needs for a feed-forward (hisim-6ehm).
    ThermalPowerDrawForecast = "ThermalPowerDrawForecast"
    WaterMassInStorage = "WaterMassInStorage"

    def add_feed_forward_outputs(self) -> Tuple[ComponentOutput, ComponentOutput]:
        """Declare the two outputs a generator controller's feed-forward reads (hisim-6ehm).

        ``ThermalPowerDrawForecast`` is the power the vessel will give this step -- the consumer's request and the
        standby loss at the start temperature, in W -- known before any generator acts. ``WaterMassInStorage`` is the
        vessel's water mass. With both, a controller asks its generator for ``draw + M c (T_set - T0) / dt``: the
        heat that leaves the vessel at the set temperature after the step.
        """
        draw_forecast_channel = self.add_output(
            self.component_name,
            self.ThermalPowerDrawForecast,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=(
                "Thermal power the vessel gives this timestep before any generator acts: the consumer's request "
                "plus the standby loss at the start temperature."
            ),
        )
        water_mass_channel = self.add_output(
            self.component_name,
            self.WaterMassInStorage,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG,
            output_description="Mass of the water in the vessel.",
        )
        return draw_forecast_channel, water_mass_channel

    def set_feed_forward_outputs(
        self, stsv: SingleTimeStepValues, heat_requested_in_watt_hour: float, start_temperature_in_celsius: float
    ) -> None:
        """Publish this step's draw forecast and the vessel's mass (see :meth:`add_feed_forward_outputs`)."""
        standby_loss_in_watt = self.calculate_heat_loss_in_watt(
            storage_surface_in_m2=self.storage_surface_in_m2,
            mean_temperature_in_storage_in_celsius=start_temperature_in_celsius,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=(
                self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin
            ),
            ambient_temperature_in_celsius=self.ambient_temperature_in_celsius,
        )
        stsv.set_output_value(
            self.thermal_power_draw_forecast_channel,
            heat_requested_in_watt_hour * 3600 / self.seconds_per_timestep + standby_loss_in_watt,
        )
        stsv.set_output_value(self.water_mass_in_storage_channel, self.water_mass_in_storage_in_kg)

    @staticmethod
    def heat_offered_by_water_flow_in_watt_hour(
        water_mass_in_kg: float,
        supply_temperature_in_celsius: float,
        return_temperature_in_celsius: float,
    ) -> float:
        """Heat a generator's flow carries relative to the return temperature it was told, in Wh.

        Args:
            water_mass_in_kg: Mass the generator pushed through the vessel's exchanger in this timestep.
            supply_temperature_in_celsius: Temperature the flow arrives with.
            return_temperature_in_celsius: Temperature the vessel told the generator its return has.

        Returns:
            float: ``m c (T_supply - T_return)`` in Wh; negative when the flow is colder than its return.
        """
        return (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=lt.LoadTypes.WATER
            ).specific_heat_capacity_in_watthour_per_kg_per_kelvin
            * water_mass_in_kg
            * (supply_temperature_in_celsius - return_temperature_in_celsius)
        )

    @staticmethod
    def heat_capacity_in_watt_hour_per_kelvin(water_mass_in_kg: float) -> float:
        """Heat capacity ``M c`` of a vessel holding ``water_mass_in_kg`` of water, in Wh/K."""
        return (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=lt.LoadTypes.WATER
            ).specific_heat_capacity_in_watthour_per_kg_per_kelvin
            * water_mass_in_kg
        )

    @staticmethod
    def heat_accepted_from_generator_in_watt_hour(
        heat_offered_in_watt_hour: float,
        supply_temperature_in_celsius: float,
        start_temperature_in_celsius: float,
        heat_capacity_in_watt_hour_per_kelvin: float,
        heat_drawn_in_watt_hour: float,
        heat_already_accepted_in_watt_hour: float = 0.0,
    ) -> float:
        """The part of a generator's delivery the vessel takes in one timestep, in Wh.

        A delivery is accepted up to the heat that brings the vessel, after this step's draw and after
        what an earlier generator slot already delivered, to the supply temperature:
        ``M c (T_supply - T0) + Q_draw - Q_already``. A flow that offers no heat, or arrives at or below the
        vessel's start temperature, is accepted as nothing.

        Args:
            heat_offered_in_watt_hour: What the flow carries relative to its return,
                :meth:`heat_offered_by_water_flow_in_watt_hour`.
            supply_temperature_in_celsius: Temperature the flow arrives with.
            start_temperature_in_celsius: The vessel's mean temperature at the start of the timestep.
            heat_capacity_in_watt_hour_per_kelvin: The vessel's ``M c``.
            heat_drawn_in_watt_hour: Heat the consumer side takes from the vessel in the same timestep; only a
                positive draw widens the room.
            heat_already_accepted_in_watt_hour: Heat accepted from generator slots handled before this one.

        Returns:
            float: The accepted heat, ``0 <= Q <= heat_offered_in_watt_hour``.
        """
        if heat_offered_in_watt_hour <= 0 or supply_temperature_in_celsius <= start_temperature_in_celsius:
            return 0.0
        room_in_watt_hour = (
            heat_capacity_in_watt_hour_per_kelvin * (supply_temperature_in_celsius - start_temperature_in_celsius)
            + max(heat_drawn_in_watt_hour, 0.0)
            - heat_already_accepted_in_watt_hour
        )
        return min(heat_offered_in_watt_hour, max(room_in_watt_hour, 0.0))

    def heat_accepted_from_both_generator_slots_in_watt_hour(
        self,
        start_temperature_in_celsius: float,
        heat_capacity_in_watt_hour_per_kelvin: float,
        heat_drawn_in_watt_hour: float,
        return_temperature_to_heat_generator_in_celsius: float,
        return_temperature_to_secondary_heat_generator_in_celsius: float,
        primary_flow: Tuple[float, float],
        secondary_flow: Tuple[float, float],
    ) -> Tuple[float, float]:
        """The heat the vessel accepts from its primary, then its secondary generator slot in one step, in Wh.

        Each slot offers what its flow carries relative to the return temperature the vessel told it
        (:meth:`heat_offered_by_water_flow_in_watt_hour`) and is accepted within the room left after this step's
        draw and after the slots handled before it (:meth:`heat_accepted_from_generator_in_watt_hour`).

        Args:
            start_temperature_in_celsius: The vessel's mean temperature at the start of the timestep.
            heat_capacity_in_watt_hour_per_kelvin: The vessel's ``M c``.
            heat_drawn_in_watt_hour: Heat the consumer side asks of the vessel in the same timestep.
            return_temperature_to_heat_generator_in_celsius: What the vessel told its primary generator.
            return_temperature_to_secondary_heat_generator_in_celsius: What it told its secondary generator.
            primary_flow: The primary generator's mass flow in kg/s and supply temperature in degC.
            secondary_flow: The secondary generator's mass flow in kg/s and supply temperature in degC.

        Returns:
            Tuple[float, float]: The heat accepted from the primary and from the secondary slot, in Wh.
        """
        accepted_in_watt_hour: List[float] = []
        for (mass_flow_in_kg_per_second, supply_temperature_in_celsius), return_temperature_in_celsius in (
            (primary_flow, return_temperature_to_heat_generator_in_celsius),
            (secondary_flow, return_temperature_to_secondary_heat_generator_in_celsius),
        ):
            accepted_in_watt_hour.append(
                self.heat_accepted_from_generator_in_watt_hour(
                    heat_offered_in_watt_hour=self.heat_offered_by_water_flow_in_watt_hour(
                        water_mass_in_kg=mass_flow_in_kg_per_second * self.seconds_per_timestep,
                        supply_temperature_in_celsius=supply_temperature_in_celsius,
                        return_temperature_in_celsius=return_temperature_in_celsius,
                    ),
                    supply_temperature_in_celsius=supply_temperature_in_celsius,
                    start_temperature_in_celsius=start_temperature_in_celsius,
                    heat_capacity_in_watt_hour_per_kelvin=heat_capacity_in_watt_hour_per_kelvin,
                    heat_drawn_in_watt_hour=heat_drawn_in_watt_hour,
                    heat_already_accepted_in_watt_hour=sum(accepted_in_watt_hour),
                )
            )
        return accepted_in_watt_hour[0], accepted_in_watt_hour[1]

    @staticmethod
    def heat_granted_to_draw_in_watt_hour(
        heat_requested_in_watt_hour: float,
        heat_accepted_in_watt_hour: float,
        start_temperature_in_celsius: float,
        refill_temperature_in_celsius: float,
        heat_capacity_in_watt_hour_per_kelvin: float,
    ) -> float:
        """The part of a consumer's request the vessel gives in one timestep, in Wh.

        The mirror of the delivery cap: a vessel cannot be drawn below the temperature at which its
        consumer stops taking heat -- the mains water (about 10 degC) that refills a hot-water tank, the
        indoor air (about 20 degC) a heating circuit cannot deliver below. It gives at most what it holds
        above that temperature plus what its generators delivered in the same step,
        ``max(M c (T0 - T_floor), 0) + Q_accepted``: a vessel already at or below its floor gives only what
        was delivered to it. Without this floor a space-heating buffer at 3600 s, asked by the distribution
        system for the heat it computed one step earlier from a warmer supply, would be drawn by an explicit
        update below the indoor air it is meant to heat, to a temperature no heating circuit delivers from. A
        request of nothing or less (a cooling distribution system) is passed through unchanged.

        Args:
            heat_requested_in_watt_hour: What the consumer asks for.
            heat_accepted_in_watt_hour: What the vessel accepted from its generators in the same timestep.
            start_temperature_in_celsius: The vessel's mean temperature at the start of the timestep.
            refill_temperature_in_celsius: The floor: the temperature at which the consumer takes no more heat.
            heat_capacity_in_watt_hour_per_kelvin: The vessel's ``M c``.

        Returns:
            float: The heat given, ``<= heat_requested_in_watt_hour``.
        """
        if heat_requested_in_watt_hour <= 0:
            return heat_requested_in_watt_hour
        content_above_refill_in_watt_hour = heat_capacity_in_watt_hour_per_kelvin * max(
            start_temperature_in_celsius - refill_temperature_in_celsius, 0.0
        )
        return max(
            min(heat_requested_in_watt_hour, heat_accepted_in_watt_hour + content_above_refill_in_watt_hour),
            0.0,
        )

    @staticmethod
    def temperature_after_heat_exchange_in_celsius(
        start_temperature_in_celsius: float,
        heat_accepted_in_watt_hour: float,
        heat_drawn_in_watt_hour: float,
        heat_capacity_in_watt_hour_per_kelvin: float,
    ) -> float:
        """The vessel's mean temperature after one step's deliveries and draw, before its standby loss.

        ``T_new = T0 + (Q_accepted - Q_drawn) / (M c)``: the vessel changes by exactly the heat that was booked.
        """
        return start_temperature_in_celsius + (
            heat_accepted_in_watt_hour - heat_drawn_in_watt_hour
        ) / heat_capacity_in_watt_hour_per_kelvin

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
    ThermalPowerRequestedByHeatDistributionSystem = "ThermalPowerRequestedByHeatDistributionSystem"
    WaterTemperatureFloorFromHeatDistribution = "WaterTemperatureFloorFromHeatDistribution"
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

        # The heat the distribution system asks for. The vessel grants it up to what it holds above the return
        # temperature and publishes the grant as ThermalPowerConsumptionHeatDistribution, which the distribution
        # system delivers to the building, so the two book one number (hisim-4g9.16). Unconnected, the vessel is
        # asked for the stream it sends out: m_hds c (T_to_hds - T_from_hds).
        self.thermal_power_heat_distribution_system_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.ThermalPowerRequestedByHeatDistributionSystem,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            False,
        )

        # The temperature the distribution system cannot draw the vessel below: at and below it, it delivers
        # nothing (the indoor air temperature). Unconnected, the return temperature the system reports is used.
        self.water_temperature_floor_heat_distribution_system_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureFloorFromHeatDistribution,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
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

        self.thermal_power_draw_forecast_channel, self.water_mass_in_storage_channel = self.add_feed_forward_outputs()

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
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.ThermalPowerRequestedByHeatDistributionSystem,
                hds_classname,
                component_class.ThermalPowerRequestedFromStorage,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterTemperatureFloorFromHeatDistribution,
                hds_classname,
                component_class.SupplyTemperatureFloor,
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

        start_temperature_in_celsius = self.state.mean_water_temperature_in_celsius

        # temperatures the vessel reports to its neighbours
        # ------------------------------
        # with heat exchanger in water storage perfect heat exchange is possible
        if self.heat_exchanger_is_present is True:
            water_temperature_to_heat_distribution_system_in_celsius = start_temperature_in_celsius
            water_temperature_to_heat_generator_in_celsius = start_temperature_in_celsius
            water_temperature_to_secondary_heat_generator_in_celsius = start_temperature_in_celsius

        # Without an exchanger the vessel is taken to be stratified: the flows leaving it are a mix, by the mixing
        # factor, of the vessel's mean and the flow entering on the other side. Since hisim-4g9.16 this shapes only
        # the temperatures reported to the distribution system and the generators; the vessel's own temperature
        # follows the booked energy balance like the exchanger case, and each generator is credited with what its
        # flow carries relative to the (mixed) return it was told, within the same cap.
        else:
            # state controller is 1 if the heat generator delivers a mass flow rate input
            if state_controller == 1:
                # hds gets water from heat generator (if heat generator is not off, mass flow is not zero)
                water_temperature_to_heat_distribution_system_in_celsius = self.calculate_water_output_temperature(
                    mean_water_temperature_in_water_storage_in_celsius=start_temperature_in_celsius,
                    mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                    mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                    water_input_temperature_in_celsius=water_temperature_from_heat_generator_in_celsius,
                )
            # no water coming from heat generator, hds gets mean water and heat generator gets still water from hds
            elif state_controller == 0:
                water_temperature_to_heat_distribution_system_in_celsius = start_temperature_in_celsius
            else:
                raise ValueError("unknown storage controller state.")
            # heat generators get water from hds (if heat generator is not off, mass flow is not zero)
            water_temperature_to_heat_generator_in_celsius = self.calculate_water_output_temperature(
                mean_water_temperature_in_water_storage_in_celsius=start_temperature_in_celsius,
                mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                water_input_temperature_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
            )
            water_temperature_to_secondary_heat_generator_in_celsius = water_temperature_to_heat_generator_in_celsius

        # energy balance of the vessel (see the note above heat_offered_by_water_flow_in_watt_hour)
        # ------------------------------
        heat_capacity_in_watt_hour_per_kelvin = self.heat_capacity_in_watt_hour_per_kelvin(
            self.water_mass_in_storage_in_kg
        )

        # the heat the distribution system asks for: its own figure, when it tells us
        if self.thermal_power_heat_distribution_system_input_channel.source_output is not None:
            thermal_power_requested_by_heat_distribution_in_watt = stsv.get_input_value(
                self.thermal_power_heat_distribution_system_input_channel
            )
        else:
            thermal_power_requested_by_heat_distribution_in_watt = self.calculate_thermal_power_of_water_flow(
                water_mass_flow_in_kg_per_s=water_mass_flow_rate_from_hds_in_kg_per_second,
                water_temperature_cold_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
                water_temperature_hot_in_celsius=water_temperature_to_heat_distribution_system_in_celsius,
            )
        heat_requested_in_watt_hour = (
            thermal_power_requested_by_heat_distribution_in_watt * self.seconds_per_timestep / 3600
        )
        self.set_feed_forward_outputs(stsv, heat_requested_in_watt_hour, start_temperature_in_celsius)

        (
            thermal_energy_input_from_heat_generator_in_watt_hour,
            thermal_energy_input_from_secondary_heat_generator_in_watt_hour,
        ) = self.heat_accepted_from_both_generator_slots_in_watt_hour(
            start_temperature_in_celsius=start_temperature_in_celsius,
            heat_capacity_in_watt_hour_per_kelvin=heat_capacity_in_watt_hour_per_kelvin,
            heat_drawn_in_watt_hour=heat_requested_in_watt_hour,
            return_temperature_to_heat_generator_in_celsius=water_temperature_to_heat_generator_in_celsius,
            return_temperature_to_secondary_heat_generator_in_celsius=(
                water_temperature_to_secondary_heat_generator_in_celsius
            ),
            primary_flow=(
                water_mass_flow_rate_from_heat_generator_in_kg_per_second,
                water_temperature_from_heat_generator_in_celsius,
            ),
            secondary_flow=(
                water_mass_flow_rate_from_secondary_heat_generator_in_kg_per_second,
                water_temperature_from_secondary_heat_generator_in_celsius,
            ),
        )

        # the distribution system gets what it asked for, down to the temperature at which it delivers nothing
        if self.water_temperature_floor_heat_distribution_system_input_channel.source_output is not None:
            floor_temperature_in_celsius = stsv.get_input_value(
                self.water_temperature_floor_heat_distribution_system_input_channel
            )
        else:
            floor_temperature_in_celsius = water_temperature_from_heat_distribution_system_in_celsius
        heat_drawn_in_watt_hour = self.heat_granted_to_draw_in_watt_hour(
            heat_requested_in_watt_hour=heat_requested_in_watt_hour,
            heat_accepted_in_watt_hour=thermal_energy_input_from_heat_generator_in_watt_hour
            + thermal_energy_input_from_secondary_heat_generator_in_watt_hour,
            start_temperature_in_celsius=start_temperature_in_celsius,
            refill_temperature_in_celsius=floor_temperature_in_celsius,
            heat_capacity_in_watt_hour_per_kelvin=heat_capacity_in_watt_hour_per_kelvin,
        )
        thermal_power_heat_distribution_in_watt = heat_drawn_in_watt_hour * 3600 / self.seconds_per_timestep

        self.mean_water_temperature_in_water_storage_in_celsius = self.temperature_after_heat_exchange_in_celsius(
            start_temperature_in_celsius=start_temperature_in_celsius,
            heat_accepted_in_watt_hour=thermal_energy_input_from_heat_generator_in_watt_hour
            + thermal_energy_input_from_secondary_heat_generator_in_watt_hour,
            heat_drawn_in_watt_hour=heat_drawn_in_watt_hour,
            heat_capacity_in_watt_hour_per_kelvin=heat_capacity_in_watt_hour_per_kelvin,
        )

        # calc thermal energies
        # ------------------------------
        previous_thermal_energy_in_storage_in_watt_hour = self.calculate_thermal_energy_in_storage(
            mean_water_temperature_in_storage_in_celsius=start_temperature_in_celsius,
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
        # heat entering the vessel from the distribution side: minus what the distribution system takes
        thermal_energy_input_from_heat_distribution_system_in_watt_hour = -heat_drawn_in_watt_hour

        # calc thermal power
        # ------------------------------
        thermal_power_from_heat_generator_in_watt = (
            thermal_energy_input_from_heat_generator_in_watt_hour * 3600 / self.seconds_per_timestep
        )
        thermal_power_from_secondary_heat_generator_in_watt = (
            thermal_energy_input_from_secondary_heat_generator_in_watt_hour * 3600 / self.seconds_per_timestep
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
        self.check_water_mass("heating water storage")
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


class SimpleDHWStorage(SimpleWaterStorage):
    """SimpleHotWaterStorage class."""

    cost_relevance = CostRelevance.PRICED

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
    StandbyTemperatureLoss = "StandbyTemperatureLoss"
    ThermalEnergyInStorage = "ThermalEnergyInStorage"
    ThermalEnergyFromHeatGenerator = "ThermalEnergyFromHeatGenerator"
    ThermalEnergyFromSecondaryHeatGenerator = "ThermalEnergyFromSecondaryHeatGenerator"
    ThermalEnergyConsumptionDHW = "ThermalEnergyConsumptionDHW"
    ThermalEnergyIncreaseInStorage = "ThermalEnergyIncreaseInStorage"
    ThermalPowerConsumptionDHW = "ThermalPowerConsumptionDHW"
    ThermalPowerFromHeatGenerator = "ThermalPowerFromHeatGenerator"
    ThermalPowerFromSecondaryHeatGenerator = "ThermalPowerFromSecondaryHeatGenerator"
    StandbyHeatLoss = "StandbyHeatLoss"
    WaterMassFlowRateOfDHW = "WaterMassFlowRateOfDHW"
    ThermalEnergyUnmetDHW = "ThermalEnergyUnmetDHW"

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
            output_description=f"here a description for {self.WaterTemperatureToHeatGenerator} will follow.",
        )

        self.water_temperature_secondary_heat_generator_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureToSecondaryHeatGenerator,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureToSecondaryHeatGenerator} will follow.",
        )

        self.water_temperature_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureFromHeatGeneratorOutput,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureFromHeatGeneratorOutput} will follow.",
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
            output_description=f"here a description for {self.WaterMeanTemperatureInStorage} will follow.",
        )

        self.temperature_loss_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.StandbyTemperatureLoss,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.StandbyTemperatureLoss} will follow.",
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
            output_description=f"here a description for {self.ThermalEnergyFromHeatGenerator} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_energy_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyConsumptionDHW,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyConsumptionDHW} will follow.",
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

        self.thermal_power_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerConsumptionDHW,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerConsumptionDHW} will follow.",
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
            output_description=f"here a description for {self.ThermalPowerFromHeatGenerator} will follow.",
        )
        self.water_mass_flow_rate_dhw_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterMassFlowRateOfDHW,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            output_description=f"here a description for {self.WaterMassFlowRateOfDHW} will follow.",
        )
        self.thermal_energy_unmet_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyUnmetDHW,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=(
                "Heat the hot water drawn in this timestep lacked: the tank was at or below the tap temperature, "
                "so the mixing valve passed tank water only and the tap got less than the warm-water temperature."
            ),
        )

        self.thermal_power_draw_forecast_channel, self.water_mass_in_storage_channel = self.add_feed_forward_outputs()

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

    @staticmethod
    def hot_water_draw(
        tap_water_mass_in_kg: float,
        tank_temperature_in_celsius: float,
        warm_water_temperature_in_celsius: float,
        cold_water_temperature_in_celsius: float,
    ) -> Tuple[float, float, float]:
        """What one timestep's tap draw takes from the tank, through a thermostatic mixing valve.

        The household asks for ``m_d`` of warm water at ``T_warm``. A tank hotter than that is mixed down with
        cold water, so only ``m_hot = m_d (T_warm - T_cold) / (T_tank - T_cold)`` leaves the tank and the heat
        drawn is exactly the demand ``m_d c (T_warm - T_cold)``. A tank at or below ``T_warm`` passes all of
        ``m_d`` unmixed: it gives ``m_d c (T_tank - T_cold)`` (nothing below ``T_cold``) and the rest of the
        demand is unmet. A tank at or below ``T_cold`` gives nothing: no hot water leaves it and the whole
        demand is unmet. The tank temperature is the one at the start of the timestep, so the draw does not
        depend on the step's own result. The tank then gives what the valve asks for down to its floor at the
        cold-water temperature (:meth:`SimpleWaterStorage.heat_granted_to_draw_in_watt_hour`).

        Args:
            tap_water_mass_in_kg: ``m_d``, the warm water drawn at the tap in this timestep.
            tank_temperature_in_celsius: The tank's mean temperature at the start of the timestep.
            warm_water_temperature_in_celsius: ``T_warm``, the temperature the tap asks for.
            cold_water_temperature_in_celsius: ``T_cold``, the mains water the valve mixes in and the tank refills with.

        Returns:
            Tuple[float, float, float]: The hot water mass leaving the tank in kg, the heat the valve asks the
            tank for in Wh (>= 0) and the demand at the tap in Wh, ``m_d c (T_warm - T_cold)``.
        """
        heat_demand_in_watt_hour = SimpleWaterStorage.heat_offered_by_water_flow_in_watt_hour(
            water_mass_in_kg=tap_water_mass_in_kg,
            supply_temperature_in_celsius=warm_water_temperature_in_celsius,
            return_temperature_in_celsius=cold_water_temperature_in_celsius,
        )
        if tank_temperature_in_celsius <= cold_water_temperature_in_celsius:
            return 0.0, 0.0, heat_demand_in_watt_hour
        if tank_temperature_in_celsius > warm_water_temperature_in_celsius:
            hot_water_mass_in_kg = (
                tap_water_mass_in_kg
                * (warm_water_temperature_in_celsius - cold_water_temperature_in_celsius)
                / (tank_temperature_in_celsius - cold_water_temperature_in_celsius)
            )
            # m_hot c (T_tank - T_cold) is the demand itself; stated so, not recomputed through the ratio
            heat_drawn_in_watt_hour = heat_demand_in_watt_hour
        else:
            hot_water_mass_in_kg = tap_water_mass_in_kg
            heat_drawn_in_watt_hour = SimpleWaterStorage.heat_offered_by_water_flow_in_watt_hour(
                water_mass_in_kg=hot_water_mass_in_kg,
                supply_temperature_in_celsius=tank_temperature_in_celsius,
                return_temperature_in_celsius=cold_water_temperature_in_celsius,
            )
        return hot_water_mass_in_kg, heat_drawn_in_watt_hour, heat_demand_in_watt_hour

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants an parameters for the calculations.
        """
        self.drain_water_temperature = configuration.HouseholdWarmWaterDemandConfig.freshwater_temperature

        self.warm_water_temperature = (
            configuration.HouseholdWarmWaterDemandConfig.ww_temperature_demand
            - configuration.HouseholdWarmWaterDemandConfig.temperature_difference_hot
        )

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
        self.check_water_mass("DHW water storage")
        self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin = (
            self.waterstorageconfig.heat_transfer_coefficient_in_watt_per_m2_per_kelvin
        )
        self.storage_surface_in_m2 = self.calculate_surface_area_of_storage(
            storage_volume_in_liter=concrete(self.waterstorageconfig.volume_heating_water_storage_in_liter),
        )

        self.ambient_temperature_in_celsius = 20.0

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
            self.mean_water_temperature_in_water_storage_in_celsius, "DHW water storage"
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the heating water storage."""

        # Get inputs --------------------------------------------------------------------------------------------------------

        water_temperature_input_of_dhw_in_celsius = self.drain_water_temperature
        water_temperature_output_of_dhw_in_celsius = self.warm_water_temperature
        water_mass_flow_rate_of_dhw_in_kg_per_second = (
            stsv.get_input_value(self.water_consumption_channel)
            * self.density_water_at_40_degree_celsius_in_kg_per_liter
            / self.seconds_per_timestep
        )

        water_temperature_from_heat_generator_in_celsius = stsv.get_input_value(
            self.water_temperature_heat_generator_input_channel
        )
        water_mass_flow_rate_from_heat_generator_in_kg_per_second = stsv.get_input_value(
            self.water_mass_flow_rate_heat_generator_input_channel
        )

        # Optional secondary heat generator
        water_temperature_from_secondary_heat_generator_in_celsius = stsv.get_input_value(
            self.water_temperature_secondary_heat_generator_input_channel
        )
        water_mass_flow_rate_from_secondary_heat_generator_in_kg_per_second = stsv.get_input_value(
            self.water_mass_flow_rate_secondary_heat_generator_input_channel
        )

        # if (water_mass_flow_rate_of_dhw_in_kg_per_second > 0) and (self.mean_water_temperature_in_water_storage_in_celsius < self.warm_water_temperature):
        #     # if there is water consumption, the temperature must be high enough
        #     log.warning(f"The DHW water temperature is only {self.mean_water_temperature_in_water_storage_in_celsius}°C.")

        # Calculations ------------------------------------------------------------------------------------------------------

        start_temperature_in_celsius = self.state.mean_water_temperature_in_celsius
        heat_capacity_in_watt_hour_per_kelvin = self.heat_capacity_in_watt_hour_per_kelvin(
            self.water_mass_in_storage_in_kg
        )

        # the draw, through a mixing valve (see hot_water_draw)
        # ------------------------------
        (
            _,
            heat_requested_in_watt_hour,
            heat_demand_in_watt_hour,
        ) = self.hot_water_draw(
            tap_water_mass_in_kg=water_mass_flow_rate_of_dhw_in_kg_per_second * self.seconds_per_timestep,
            tank_temperature_in_celsius=start_temperature_in_celsius,
            warm_water_temperature_in_celsius=water_temperature_output_of_dhw_in_celsius,
            cold_water_temperature_in_celsius=water_temperature_input_of_dhw_in_celsius,
        )
        self.set_feed_forward_outputs(stsv, heat_requested_in_watt_hour, start_temperature_in_celsius)

        # the deliveries, primary slot first (see heat_accepted_from_generator_in_watt_hour)
        # ------------------------------
        water_temperature_to_heat_generator_in_celsius = start_temperature_in_celsius
        water_temperature_to_secondary_heat_generator_in_celsius = start_temperature_in_celsius
        (
            thermal_energy_input_from_heat_generator_in_watt_hour,
            thermal_energy_input_from_secondary_heat_generator_in_watt_hour,
        ) = self.heat_accepted_from_both_generator_slots_in_watt_hour(
            start_temperature_in_celsius=start_temperature_in_celsius,
            heat_capacity_in_watt_hour_per_kelvin=heat_capacity_in_watt_hour_per_kelvin,
            heat_drawn_in_watt_hour=heat_requested_in_watt_hour,
            return_temperature_to_heat_generator_in_celsius=water_temperature_to_heat_generator_in_celsius,
            return_temperature_to_secondary_heat_generator_in_celsius=(
                water_temperature_to_secondary_heat_generator_in_celsius
            ),
            primary_flow=(
                water_mass_flow_rate_from_heat_generator_in_kg_per_second,
                water_temperature_from_heat_generator_in_celsius,
            ),
            secondary_flow=(
                water_mass_flow_rate_from_secondary_heat_generator_in_kg_per_second,
                water_temperature_from_secondary_heat_generator_in_celsius,
            ),
        )

        # the tap gets what the valve asked for, down to the tank's floor at the cold-water temperature
        heat_drawn_in_watt_hour = self.heat_granted_to_draw_in_watt_hour(
            heat_requested_in_watt_hour=heat_requested_in_watt_hour,
            heat_accepted_in_watt_hour=thermal_energy_input_from_heat_generator_in_watt_hour
            + thermal_energy_input_from_secondary_heat_generator_in_watt_hour,
            start_temperature_in_celsius=start_temperature_in_celsius,
            refill_temperature_in_celsius=water_temperature_input_of_dhw_in_celsius,
            heat_capacity_in_watt_hour_per_kelvin=heat_capacity_in_watt_hour_per_kelvin,
        )
        heat_unmet_in_watt_hour = max(heat_demand_in_watt_hour - heat_drawn_in_watt_hour, 0.0)

        self.mean_water_temperature_in_water_storage_in_celsius = self.temperature_after_heat_exchange_in_celsius(
            start_temperature_in_celsius=start_temperature_in_celsius,
            heat_accepted_in_watt_hour=thermal_energy_input_from_heat_generator_in_watt_hour
            + thermal_energy_input_from_secondary_heat_generator_in_watt_hour,
            heat_drawn_in_watt_hour=heat_drawn_in_watt_hour,
            heat_capacity_in_watt_hour_per_kelvin=heat_capacity_in_watt_hour_per_kelvin,
        )

        # calc thermal energies
        # ------------------------------
        previous_thermal_energy_in_storage_in_watt_hour = self.calculate_thermal_energy_in_storage(
            mean_water_temperature_in_storage_in_celsius=start_temperature_in_celsius,
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
        # the heat in the hot water delivered at the tap, as heat leaving the tank (<= 0)
        thermal_energy_consumption_of_dhw_in_watt_hour = -heat_drawn_in_watt_hour

        # calc thermal power
        # ------------------------------
        thermal_power_from_heat_generator_in_watt = (
            thermal_energy_input_from_heat_generator_in_watt_hour * 3600 / self.seconds_per_timestep
        )
        thermal_power_from_secondary_heat_generator_in_watt = (
            thermal_energy_input_from_secondary_heat_generator_in_watt_hour * 3600 / self.seconds_per_timestep
        )
        thermal_power_consumption_of_dhw_in_watt = heat_drawn_in_watt_hour * 3600 / self.seconds_per_timestep

        # Set outputs ------------------------------------------------------------------------------------------------
        stsv.set_output_value(self.thermal_energy_unmet_dhw_channel, heat_unmet_in_watt_hour)

        stsv.set_output_value(
            self.water_temperature_to_heat_generator_channel,
            water_temperature_to_heat_generator_in_celsius,
        )

        stsv.set_output_value(
            self.water_temperature_secondary_heat_generator_output_channel,
            water_temperature_to_secondary_heat_generator_in_celsius,
        )

        stsv.set_output_value(
            self.water_temperature_from_heat_generator_channel,
            water_temperature_from_heat_generator_in_celsius,
        )

        stsv.set_output_value(
            self.water_temperature_from_secondary_heat_generator_channel,
            water_temperature_from_secondary_heat_generator_in_celsius,
        )

        stsv.set_output_value(
            self.water_temperature_mean_channel,
            self.state.mean_water_temperature_in_celsius,
        )

        stsv.set_output_value(
            self.temperature_loss_channel,
            self.state.temperature_loss_in_celsius_per_timestep,
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
            self.thermal_energy_dhw_channel,
            thermal_energy_consumption_of_dhw_in_watt_hour,
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
            self.thermal_power_dhw_channel,
            thermal_power_consumption_of_dhw_in_watt,
        )

        stsv.set_output_value(
            self.thermal_power_from_heat_generator_channel,
            thermal_power_from_heat_generator_in_watt,
        )

        stsv.set_output_value(
            self.thermal_power_from_secondary_heat_generator_channel,
            thermal_power_from_secondary_heat_generator_in_watt,
        )

        stsv.set_output_value(
            self.water_mass_flow_rate_dhw_output_channel,
            water_mass_flow_rate_of_dhw_in_kg_per_second,
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
        return list_of_kpi_entries
