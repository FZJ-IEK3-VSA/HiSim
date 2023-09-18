"""Test for scaling of the energy system.

Depending on the building floor area and the rooftop area the energy system components,
such as pv system, battery, heat pumps, water storage, etc. need to be scaled up.
"""
# clean

import numpy as np
import pytest
from typing import Tuple, Any

from hisim.components import (
    building,
    loadprofilegenerator_connector,
    generic_pv_system,
    advanced_heat_pump_hplib,
    advanced_battery_bslib,
    simple_hot_water_storage,
    generic_heat_pump_modular,
    generic_hot_water_storage_modular,
)
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum


@pytest.mark.buildingtest
@utils.measure_execution_time
def test_energy_system_scalability():
    """Test function for the scability of the whole energy system."""

    # calculate energy system sizes for original case (scaling factors = 1)
    (
        number_of_apartments,
        original_pv_electricity_output_in_watt,
        original_hplib_thermal_outout_power_in_watt,
        original_storage_size_for_space_heating_in_liter,
        original_battery_size_in_kilowatt_hours,
        original_hp_modular_thermal_power_in_watt_for_dhw,
        original_storage_modular_size_in_liter_for_dhw,
    ) = simulation_for_one_timestep(
        scaling_factor_for_absolute_conditioned_floor_area=1,
        scaling_factor_for_rooftop_area=1,
    )

    log.information(
        "original size pv in watt " + str(original_pv_electricity_output_in_watt)
    )
    log.information(
        "original size hplib in watt "
        + str(original_hplib_thermal_outout_power_in_watt)
    )
    log.information(
        "original size storage for space heating in liter "
        + str(original_storage_size_for_space_heating_in_liter)
    )
    log.information(
        "original size battery in kWh " + str(original_battery_size_in_kilowatt_hours)
    )
    log.information(
        "original size hp modular for dhw "
        + str(original_hp_modular_thermal_power_in_watt_for_dhw)
    )
    log.information(
        "original size storage modular for dwh in liter "
        + str(original_storage_modular_size_in_liter_for_dhw)
        + "\n"
    )

    # calculate sizes and respective scaling factor when
    # the rooftop and floor area are scaled with factor=5
    (
        number_of_apartments,
        scaled_pv_electricity_output_in_watt,
        scaled_hplib_thermal_outout_power_in_watt,
        scaled_storage_size_for_space_heating_in_liter,
        scaled_battery_size_in_kilowatt_hours,
        scaled_hp_modular_thermal_power_in_watt_for_dhw,
        scaled_storage_modular_size_in_liter_for_dhw,
    ) = simulation_for_one_timestep(
        scaling_factor_for_absolute_conditioned_floor_area=5,
        scaling_factor_for_rooftop_area=5,
    )

    log.information(
        "original size pv in watt " + str(scaled_pv_electricity_output_in_watt)
    )
    log.information(
        "original size hplib in watt " + str(scaled_hplib_thermal_outout_power_in_watt)
    )
    log.information(
        "original size storage for space heating in liter "
        + str(scaled_storage_size_for_space_heating_in_liter)
    )
    log.information(
        "original size battery in kWh " + str(scaled_battery_size_in_kilowatt_hours)
    )
    log.information(
        "original size hp modular for dhw "
        + str(scaled_hp_modular_thermal_power_in_watt_for_dhw)
    )
    log.information(
        "original size storage modular for dwh in liter "
        + str(scaled_storage_modular_size_in_liter_for_dhw)
        + "\n"
    )

    # now compare the two results and test if sizes are upscaled correctly
    np.testing.assert_allclose(
        scaled_pv_electricity_output_in_watt,
        original_pv_electricity_output_in_watt * 5,
        rtol=0.01,
    )

    np.testing.assert_allclose(
        scaled_hplib_thermal_outout_power_in_watt,
        original_hplib_thermal_outout_power_in_watt * 5,
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

    # hp modular for dhw scales with number of apartments
    np.testing.assert_allclose(
        scaled_hp_modular_thermal_power_in_watt_for_dhw,
        original_hp_modular_thermal_power_in_watt_for_dhw * number_of_apartments,
        rtol=0.01,
    )

    # storage modular for dhw scales with number of apartments
    np.testing.assert_allclose(
        scaled_storage_modular_size_in_liter_for_dhw,
        original_storage_modular_size_in_liter_for_dhw * number_of_apartments,
        rtol=0.01,
    )


def simulation_for_one_timestep(
    scaling_factor_for_absolute_conditioned_floor_area: int,
    scaling_factor_for_rooftop_area: int,
) -> Tuple[Any, Any]:
    """Test function for the example house for one timestep."""

    # Set simu params
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    # Set building inputs
    absolute_conditioned_floor_area_in_m2 = (
        121.2 * scaling_factor_for_absolute_conditioned_floor_area
    )
    rooftop_area_in_m2 = 168.9 * scaling_factor_for_rooftop_area

    # Set Residence
    my_residence_config = (
        building.BuildingConfig.get_default_german_single_family_home()
    )
    my_residence_config.absolute_conditioned_floor_area_in_m2 = (
        absolute_conditioned_floor_area_in_m2
    )
    my_residence = building.Building(
        config=my_residence_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # my_residence.set_sim_repo(repo)
    # my_residence.i_prepare_simulation()
    number_of_apartments = SingletonSimRepository().get_entry(
        key=SingletonDictKeyEnum.NUMBEROFAPARTMENTS
    )
    heating_load_in_watt = SingletonSimRepository().get_entry(
        key=SingletonDictKeyEnum.MAXTHERMALBUILDINGDEMAND
    )
    log.information("Building code" + str(my_residence_config.building_code))
    log.information("Rooftop area " + str(rooftop_area_in_m2))
    log.information("Floor area " + str(absolute_conditioned_floor_area_in_m2))

    log.information("Heating load of building in W " + str(heating_load_in_watt))
    log.information("Number of apartmens in building " + str(number_of_apartments))

    # Set Occupancy
    my_occupancy_config = (
        loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
    )
    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Set PV
    my_pv_config = generic_pv_system.PVSystemConfig.get_scaled_PV_system(
        rooftop_area_in_m2=rooftop_area_in_m2
    )

    # Set hplib
    my_hplib_config = (
        advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib()
    )

    # Set Hot Water Storage
    my_simple_hot_water_storage_config = (
        simple_hot_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage()
    )

    # Set Battery
    my_battery_config = advanced_battery_bslib.BatteryConfig.get_scaled_battery(
        total_pv_power_in_watt_peak=my_pv_config.power
    )

    # Set DHW Heat Pump Modular
    my_hp_for_dhw_config = (
        generic_heat_pump_modular.HeatPumpConfig.get_scaled_waterheating()
    )

    # Set DHW Storage modular
    my_storage_for_dhw_config = (
        generic_hot_water_storage_modular.StorageConfig.get_default_config_for_boiler_scaled()
    )

    # Energy system sizes
    pv_power_in_watt = my_pv_config.power
    hplib_thermal_power_in_watt = my_hplib_config.set_thermal_output_power_in_watt
    simple_hot_water_storage_size_in_liter = (
        my_simple_hot_water_storage_config.volume_heating_water_storage_in_liter
    )
    battery_capacity_in_kilowatt_hours = (
        my_battery_config.custom_battery_capacity_generic_in_kilowatt_hour
    )
    hp_for_dhw_thermal_power_in_watt = my_hp_for_dhw_config.power_th
    water_storage_size_for_dhw_in_liter = my_storage_for_dhw_config.volume

    return (
        number_of_apartments,
        pv_power_in_watt,
        hplib_thermal_power_in_watt,
        simple_hot_water_storage_size_in_liter,
        battery_capacity_in_kilowatt_hours,
        hp_for_dhw_thermal_power_in_watt,
        water_storage_size_for_dhw_in_liter,
    )
