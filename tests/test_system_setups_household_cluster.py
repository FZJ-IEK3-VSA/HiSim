""" Tests for the cluster system setups.

These system setups can only be tested on cluster because so far they need access to a certain cluster directory.
"""
# # clean
# import os
# import pytest

# from hisim import hisim_main
# from hisim.simulationparameters import SimulationParameters
# from hisim import log
# from hisim import utils


# @pytest.mark.system_setups
# @utils.measure_execution_time
# def test_cluster_household_reference():
#     """Single day."""
#     path = "../system_setups/household_cluster_reference_advanced_hp.py"

#     mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
#     hisim_main.main(path, mysimpar)
#     log.information(os.getcwd())


# @pytest.mark.system_setups
# @utils.measure_execution_time
# def test_cluster_household_with_pv_battery_and_ems():
#     """Single day."""
#     path = "../system_setups/household_cluster_advanced_hp_pv_battery_ems.py"

#     mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
#     hisim_main.main(path, mysimpar)
#     log.information(os.getcwd())
