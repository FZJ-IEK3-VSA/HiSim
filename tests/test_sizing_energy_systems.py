"""Test for scaling of the energy system.

Depending on building properties like rooftop area, floor area, number of apartments and heating load the energy system components,
such as pv system, battery, heat pumps, water storage, etc. need to be scaled up.
"""
# clean

from typing import Dict
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
    capacity scale by ~5×, while the DHW storage scales with the
    number of apartments (within 1 % relative tolerance).
    """

    scaling_factor = 5

    # calculate energy system sizes for original case (scaling factors = 1)
    original = simulation_for_one_timestep(
        scaling_factor_for_absolute_conditioned_floor_area=1,
    )

    # calculate sizes and respective scaling factor when
    # the rooftop and floor area are scaled with factor=5
    scaled = simulation_for_one_timestep(
        scaling_factor_for_absolute_conditioned_floor_area=scaling_factor,
    )

    # DHW storage is modular and scales with the number of apartments in the
    # scaled building; all other components scale with the floor-area factor.
    number_of_apartments = scaled["number_of_apartments"]

    # Declarative expectation table: (component name, expected scaling factor).
    # Adding a component means adding one construction line in
    # simulation_for_one_timestep and one entry here -- no other edits needed.
    expected_scalings = [
        ("pv", scaling_factor),
        ("hplib", scaling_factor),
        ("sh_storage", scaling_factor),
        ("battery", scaling_factor),
        ("dhw_storage", number_of_apartments),
    ]

    # log original and scaled sizes for each component
    for name, _ in expected_scalings:
        log.information(f"original size {name}: {original[name]}")
    log.information("")

    for name, _ in expected_scalings:
        log.information(f"scaled size {name}: {scaled[name]}")
    log.information("")

    # now compare the two results and test if sizes are upscaled correctly
    for name, factor in expected_scalings:
        np.testing.assert_allclose(
            scaled[name],
            original[name] * factor,
            rtol=0.01,
        )


def _log_building_properties(
    config: building.BuildingConfig,
    info: building.BuildingInformation,
) -> None:
    """Emit diagnostic building properties used while sizing the energy system.

    Kept separate from the sizing/construction logic so that changes to the logged
    property set or message format do not require editing the sizing function.
    """
    log.information("Building code" + str(config.building_code))
    log.information("Rooftop area " + str(info.roof_area_in_m2))
    log.information(
        "Floor area " + str(info.scaled_conditioned_floor_area_in_m2)
    )
    log.information(
        "Heating load of building in W "
        + str(info.max_thermal_building_demand_in_watt)
    )
    log.information(
        "Number of apartmens in building " + str(info.number_of_apartments)
    )


def simulation_for_one_timestep(
    scaling_factor_for_absolute_conditioned_floor_area: int,
) -> Dict[str, float]:
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
        A dict mapping component names to their sized values:
          "number_of_apartments": Number of apartments in the scaled building.
          "pv": Rated electric power of the scaled PV system [W].
          "hplib": Thermal output power set for the hplib heat pump [W].
          "sh_storage": Volume of the space-heating water storage [L].
          "battery": Battery capacity [kWh].
          "dhw_storage": Volume of the DHW storage [L].
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

    _log_building_properties(my_residence_config, my_residence_information)

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

    return {
        "number_of_apartments": my_residence_information.number_of_apartments,
        "pv": pv_power_in_watt,
        "hplib": hplib_thermal_power_in_watt.value,
        "sh_storage": simple_hot_water_storage_size_in_liter,
        "battery": battery_capacity_in_kilowatt_hours,
        "dhw_storage": water_storage_size_for_dhw_in_liter,
    }
