pylint /fast/home/k-rieck/repositories/HiSim/setup.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/air_conditioned_house_b_with_pid_controller.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/simple_system_setup_one.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_reference_gas_heater_diesel_car.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/default_connections.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_2_advanced_hp_diesel_car_pv.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_4a_with_car_priority_advanced_hp_ev_pv.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/air_conditioned_house_a_with_mpc_controller.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/automatic_default_connections.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/dynamic_components.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/electrolyzer_with_renewables.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/simple_system_setup_two.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/air_conditioned_house_c_with_onoff_controller.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_cluster_advanced_hp_pv_battery_ems.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_3_advanced_hp_diesel_car_pv_battery.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_5a_with_car_priority_advanced_hp_ev_pv_battery.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/basic_household.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/power_to_x_transformation_battery_electrolyzer_grid.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_4b_with_heatpump_priority_advanced_hp_ev_pv.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/basic_household_only_heating.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/decentralized_energy_netw_pv_bat_hydro_system_hp_rsoc.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_cluster_reference_advanced_hp.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_1_advanced_hp_diesel_car.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_5b_with_battery_priority_advanced_hp_ev_pv_battery.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/modular_example.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/household_with_advanced_hp_hws_hds_pv_battery_ems.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/system_setups/power_to_x_transformation_battery_electrolyzer_no_grid.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_house_with_pyam_postprocessingoption.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_example_storage.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_household_5b_with_battery_priority.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_generic_gas_heater.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_generic_fuel_cell.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_sizing_energy_systems.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_electricity_meter.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_household_with_advanced_hp_hws_hds_pv_battery_ems.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_occupancy_scalability.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_household_1_advanced_hp_diesel_car.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_building_heating_demand.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_household_4b_with_heatpump_priority.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_example_transformer.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_advanced_fuel_cell.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_household_2_advanced_hp_diesel_car_pv.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_basic_household.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_household_5a_with_car_priority.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_household_4a_with_car_priority.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_basic_household_network_chart.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_example_template.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_air_conditioned_house.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_building_theoretical_thermal_demand.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_household_reference_gas_heater_diesel_car.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_building_manual_calculation_thermal_conductances.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_advanced_battery_bslib.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_household_3_advanced_hp_diesel_car_pv_battery.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_building_scalability_with_factor.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_system_setups_basic_household_with_all_resultfiles.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_singleton_sim_repository.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_simple_hot_water_storage.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/functions_for_testing.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_generic_rsoc.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_example_component.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/tests/test_building.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/household_with_gas_heater_with_controller.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/generic_gas_heater_with_controller.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/household_with_gas_heater.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/test_system_setups_household_with_gas_heater_with_controller.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/household_with_advanced_hp_hws_hds_pv.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/test_system_setups_household_with_gas_heater.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/test_system_setups_household_with_heatpump_and_pv.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/test_system_setups_household_with_advanced_hp_hws_hds_pv.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/generic_heat_pump_for_house_with_hds.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/obsolete_compute_kpis.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/basic_household_with_new_hp_hds_hws_and_pv.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/test_system_setups_household_with_gas_heater_with_new_controller.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/obsolete/household_with_heatpump_and_pv.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/simulationparameters.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/system_setup_configuration.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/utils.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/log.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/sim_repository_singleton.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/hisim_with_profiler.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/sim_repository.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/dynamic_component.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/hisim_main.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/project_code_overview_generator.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/component_wrapper.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/json_generator.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/json_executor.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/loadtypes.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessingoptions.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/result_path_provider.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/simulator.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/component.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/modular_household/component_connections.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/modular_household/interface_configs/modular_household_config.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/modular_household/interface_configs/system_config.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/postprocessing_main.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/reportgenerator.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/postprocessing_datatransfer.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/system_chart.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/chartbase.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/investment_cost_co2.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/generate_csv_for_housing_database.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/charts.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/compute_kpis.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/chart_singleday.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/scenario_evaluation/scenario_analysis_complete.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/postprocessing/scenario_evaluation/result_data_collection.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/heat_distribution_system.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l2_ptx_energy_management_system.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/air_conditioner.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_electrolyzer_h2.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/sumbuilder.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/simple_hot_water_storage.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_chp.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/advanced_battery_bslib.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/advanced_heat_pump_hplib.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_hydrogen_storage.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/configuration.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/advanced_fuel_cell.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_example_controller.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_pid.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_car.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/electricity_meter.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l2_xtp_fuel_cell_ems.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/example_transformer.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_ev_charger.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_rsoc.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/example_template.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_heat_pump_modular.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_building_heating.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_electrolyzer_h2.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_heat_source.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_generic_ev_charge.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/weather.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_pv_system.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/transformer_rectifier.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/advanced_ev_battery_bslib.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l2_generic_heat_clever_simple.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/loadprofilegenerator_connector.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_heatpump.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_hot_water_storage_modular.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_generic_runtime.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_battery.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l2_energy_management_system.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l2_rsoc_battery_system.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/idealized_electric_heater.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_price_signal.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_heat_pump.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/building.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_heat_water_storage.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_generic_gas_heater.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_heat_old.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/loadprofilegenerator_utsp_connector.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_fuel_cell.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/advanced_fuel_cell_controller.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_fuel_cell.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_electrolyzer_and_h2_storage.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_smart_device.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_chp.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_mpc.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/generic_gas_heater.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_rsoc.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l1_electrolyzer.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/csvloader.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/example_storage.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/random_numbers.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/example_component.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l2_smart_controller.py
if %errorlevel% neq 0 exit /b
pylint /fast/home/k-rieck/repositories/HiSim/hisim/components/controller_l2_generic_heat_simple.py
if %errorlevel% neq 0 exit /b
