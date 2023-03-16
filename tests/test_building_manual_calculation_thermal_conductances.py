"""Test for manual calculation of thermal conductances H_tr in building module.

The aim is to implement scalability in the building module by scaling up the area of the building. 
Therefore some functions must be adjusted which are tested here before."""

import numpy as np
from hisim.components import building
from hisim.simulationparameters import SimulationParameters
from hisim import utils
import pytest

# # in case you want to check on all TABULA buildings -> run test over all building_codes
# d_f = pd.read_csv(
#     utils.HISIMPATH["housing"],
#     decimal=",",
#     sep=";",
#     encoding="cp1252",
#     low_memory=False,
# )

# for building_code in d_f["Code_BuildingVariant"]:
#     if isinstance(building_code, str):
#         my_residence_config.building_code = building_code

#         my_residence = building.Building(
#             config=my_residence_config, my_simulation_parameters=my_simulation_parameters)
#         log.information(building_code)
@pytest.mark.buildingtest
@utils.measure_execution_time
def test_building_thermal_conductance_calculation():
    """Test function for some functions of the building module."""

    building_code = "DE.N.SFH.05.Gen.ReEx.001.001"
    building_heat_capacity_class = "medium"
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    # Set Residence
    my_residence_config = (
        building.BuildingConfig.get_default_german_single_family_home()
    )
    my_residence_config.building_code = building_code
    my_residence_config.building_heat_capacity_class = building_heat_capacity_class
    my_residence = building.Building(
                config=my_residence_config, my_simulation_parameters=my_simulation_parameters)


    # Test calculation of the thermal conductances H_Transmission (H_tr) given by TABULA
    # building function: get_thermal_conductance_between_exterior_and_windows_and_door_in_watt_per_kelvin
    w_s = [
        "Window_1",
        "Window_2",
        "Door_1",
    ]
    list_H_tr_window = []
    list_H_tr_window_calculated = []

    k = 0
    for w_i in w_s:
        list_H_tr_window.append(
            my_residence.buildingdata["H_Transmission_" + w_i].values[0]
        )
        # with H_Tr = U * A * b_tr [W/K] -> by calculating H_tr manually one can later scale this up by scaling up A_Calc
        H_tr_i = (
            my_residence.buildingdata["U_Actual_" + w_i].values[0]
            * my_residence.buildingdata["A_" + w_i].values[0]
            * 1.0
        )
        list_H_tr_window_calculated.append(H_tr_i)
        k = k + 1

    # check if calculated H_tr is equal to H_tr which was read from buildingdata directly
    np.testing.assert_allclose(list_H_tr_window, list_H_tr_window_calculated, atol=0.02)

    # builing function: get_thermal_conductance_of_opaque_surfaces_in_watt_per_kelvin
    opaque_walls = [
        "Wall_1",
        "Wall_2",
        "Wall_3",
        "Roof_1",
        "Roof_2",
        "Floor_1",
        "Floor_2",
    ]
    list_H_tr_opaque = []
    list_H_tr_opaque_calculated = []
    k = 0
    for o_p in opaque_walls:
        list_H_tr_opaque.append(
            my_residence.buildingdata["H_Transmission_" + o_p].values[0]
        )
        # with H_Tr = U * A * b_tr [W/K] -> by calculating H_tr manually one can later scale this up by scaling up A_Calc
        H_tr_i = (my_residence.buildingdata["U_Actual_" + o_p].values[0]
            * my_residence.buildingdata["A_" + o_p].values[0]
            * my_residence.buildingdata["b_Transmission_" + o_p].values[0]
        )

        list_H_tr_opaque_calculated.append(H_tr_i)
        k = k + 1

    # check if calculated H_tr is equal to H_tr which was read from buildingdata directly
    np.testing.assert_allclose(list_H_tr_window, list_H_tr_window_calculated, atol=0.02)
