"""Test for scaling of the energy system.

Depending on building properties like rooftop area, floor area, number of apartments and heating load the energy system components,
such as pv system, battery, heat pumps, water storage, etc. need to be scaled up.
"""
# clean

from typing import Tuple
import pytest
import numpy as np
from hisim.components import (
    building,
    generic_pv_system,
    advanced_heat_pump_hplib,
    advanced_battery_bslib,
    simple_water_storage,
)
from hisim.units import Quantity, Watt

from hisim import log
from hisim import utils


@pytest.mark.buildingtest
@utils.measure_execution_time
def test_energy_system_scalability() -> None:
    """Verify energy-system components scale linearly with building floor area.

    Sizes the energy system with a scaling factor of 1 and again with 5, then asserts
    that PV output, hplib thermal power, space-heating storage volume, and battery
    capacity scale by ~5×, while the DHW heat pump and DHW storage scale with the
    number of apartments (within 1 % relative tolerance).
    """

    # calculate energy system sizes for original case (scaling factors = 1)
    (
        number_of_apartments,
        original_pv_electricity_output_in_watt,
        original_hplib_thermal_output_power_in_watt,
        original_storage_size_for_space_heating_in_liter,
        original_battery_size_in_kilowatt_hours,
        original_storage_size_in_liter_for_dhw,
    ) = simulation_for_one_timestep(
        scaling_factor_for_absolute_conditioned_floor_area=1,
    )

    log.information(
        "original size pv in watt " + str(original_pv_electricity_output_in_watt)
    )
    log.information(
        "original size hplib in watt "
        + str(original_hplib_thermal_output_power_in_watt)
    )
    log.information(
        "original size storage for space heating in liter "
        + str(original_storage_size_for_space_heating_in_liter)
    )
    log.information(
        "original size battery in kWh " + str(original_battery_size_in_kilowatt_hours)
    )
    log.information(
        "original size storage for dwh in liter "
        + str(original_storage_size_in_liter_for_dhw)
        + "\n"
    )

    # calculate sizes and respective scaling factor when
    # the rooftop and floor area are scaled with factor=5
    (
        number_of_apartments,
        scaled_pv_electricity_output_in_watt,
        scaled_hplib_thermal_output_power_in_watt,
        scaled_storage_size_for_space_heating_in_liter,
        scaled_battery_size_in_kilowatt_hours,
        scaled_storage_size_in_liter_for_dhw,
    ) = simulation_for_one_timestep(
        scaling_factor_for_absolute_conditioned_floor_area=5,
    )

    log.information(
        "original size pv in watt " + str(scaled_pv_electricity_output_in_watt)
    )
    log.information(
        "original size hplib in watt " + str(scaled_hplib_thermal_output_power_in_watt)
    )
    log.information(
        "original size storage for space heating in liter "
        + str(scaled_storage_size_for_space_heating_in_liter)
    )
    log.information(
        "original size battery in kWh " + str(scaled_battery_size_in_kilowatt_hours)
    )
    log.information(
        "original size storage for dwh in liter "
        + str(scaled_storage_size_in_liter_for_dhw)
        + "\n"
    )

    # now compare the two results and test if sizes are upscaled correctly
    np.testing.assert_allclose(
        scaled_pv_electricity_output_in_watt,
        original_pv_electricity_output_in_watt * 5,
        rtol=0.01,
    )

    np.testing.assert_allclose(
        scaled_hplib_thermal_output_power_in_watt,
        original_hplib_thermal_output_power_in_watt * 5,
        rtol=0.01,
    )

    np.testing.assert_allclose(
        scaled_storage_size_for_space_heating_in_liter,
        original_storage_size_for_space_heating_in_liter * 5,
        rtol=0.01,
    )

    np.testing.assert_allclose(
        scaled_battery_size_in_kilowatt_hours,
        original_battery_size_in_kilowatt_hours * 5,
        rtol=0.01,
    )

    # storage modular for dhw scales with number of apartments
    np.testing.assert_allclose(
        scaled_storage_size_in_liter_for_dhw,
        original_storage_size_in_liter_for_dhw * number_of_apartments,
        rtol=0.01,
    )


def simulation_for_one_timestep(
    scaling_factor_for_absolute_conditioned_floor_area: int,
) -> Tuple[int, float, float, float, float, float]:
    """Build a scaled energy system and return the sized component values for one timestep.

    Constructs a German single-family-home building scaled by the given factor, then
    sizes the PV system, hplib heat pump, space-heating hot-water storage, battery,
    and DHW storage against the scaled building, returning the
    resulting component sizes without running a full simulation.

    Args:
        scaling_factor_for_absolute_conditioned_floor_area: Multiplier applied to the
            baseline 121.2 m² conditioned floor area; also drives rooftop area and
            number of apartments via the building config.

    Returns:
        A tuple of:
          number_of_apartments: Number of apartments in the scaled building.
          pv_power_in_watt: Rated electric power of the scaled PV system [W].
          hplib_thermal_power_in_watt: Thermal output power set for the hplib heat pump [W].
          simple_hot_water_storage_size_in_liter: Volume of the space-heating water storage [L].
          battery_capacity_in_kilowatt_hours: Battery capacity [kWh].
          water_storage_size_for_dhw_in_liter: Volume of the DHW storage [L].
    """

    # Set building inputs
    absolute_conditioned_floor_area_in_m2 = (
        121.2 * scaling_factor_for_absolute_conditioned_floor_area
    )

    # Set Residence
    my_residence_config = (
        building.BuildingConfig.get_default_german_single_family_home()
    )
    my_residence_config.absolute_conditioned_floor_area_in_m2 = (
        absolute_conditioned_floor_area_in_m2
    )

    my_residence_information = building.BuildingInformation(config=my_residence_config)

    log.information("Building code" + str(my_residence_config.building_code))
    log.information(
        "Rooftop area " + str(my_residence_information.roof_area_in_m2)
    )
    log.information(
        "Floor area "
        + str(my_residence_information.scaled_conditioned_floor_area_in_m2)
    )

    log.information(
        "Heating load of building in W "
        + str(my_residence_information.max_thermal_building_demand_in_watt)
    )
    log.information(
        "Number of apartmens in building "
        + str(my_residence_information.number_of_apartments)
    )

    # Set PV
    my_pv_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        rooftop_area_in_m2=my_residence_information.roof_area_in_m2
    )

    # Set hplib
    my_hplib_config = advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(
        heating_load_of_building_in_watt=Quantity(my_residence_information.max_thermal_building_demand_in_watt, Watt)
    )

    # Set Hot Water Storage
    my_simple_hot_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_hplib_config.set_thermal_output_power_in_watt.value,
        sizing_option=simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP,
    )

    # Set Battery
    my_battery_config = advanced_battery_bslib.BatteryConfig.get_scaled_battery(
        total_pv_power_in_watt_peak=my_pv_config.power_in_watt
    )

    # Set DHW Storage
    my_dhw_storage_config = simple_water_storage.SimpleDHWStorageConfig.get_scaled_dhw_storage(
        number_of_apartments=my_residence_information.number_of_apartments
    )

    # Energy system sizes
    pv_power_in_watt = my_pv_config.power_in_watt
    hplib_thermal_power_in_watt = my_hplib_config.set_thermal_output_power_in_watt
    simple_hot_water_storage_size_in_liter = (
        my_simple_hot_water_storage_config.volume_heating_water_storage_in_liter
    )
    battery_capacity_in_kilowatt_hours = (
        my_battery_config.custom_battery_capacity_generic_in_kilowatt_hour
    )
    water_storage_size_for_dhw_in_liter = my_dhw_storage_config.volume_heating_water_storage_in_liter

    return (
        my_residence_information.number_of_apartments,
        pv_power_in_watt,
        hplib_thermal_power_in_watt.value,
        simple_hot_water_storage_size_in_liter,
        battery_capacity_in_kilowatt_hours,
        water_storage_size_for_dhw_in_liter,
    )
