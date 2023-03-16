pylint setup.py
if %errorlevel% neq 0 exit /b
pylint examples\basic_household.py
if %errorlevel% neq 0 exit /b
pylint examples\basic_household_only_heating.py
if %errorlevel% neq 0 exit /b
pylint examples\basic_household_with_new_hp_hds_hws_and_pv.py
if %errorlevel% neq 0 exit /b
pylint examples\default_connections.py
if %errorlevel% neq 0 exit /b
pylint examples\dynamic_components.py
if %errorlevel% neq 0 exit /b
pylint examples\household_with_gas_heater.py
if %errorlevel% neq 0 exit /b
pylint examples\household_with_gas_heater_with_controller.py
if %errorlevel% neq 0 exit /b
pylint examples\household_with_heatpump_and_pv.py
if %errorlevel% neq 0 exit /b
pylint examples\simple_examples.py
if %errorlevel% neq 0 exit /b
pylint hisim\component.py
if %errorlevel% neq 0 exit /b
pylint hisim\component_wrapper.py
if %errorlevel% neq 0 exit /b
pylint hisim\dynamic_component.py
if %errorlevel% neq 0 exit /b
pylint hisim\hisim_main.py
if %errorlevel% neq 0 exit /b
pylint hisim\hisim_with_profiler.py
if %errorlevel% neq 0 exit /b
pylint hisim\json_executor.py
if %errorlevel% neq 0 exit /b
pylint hisim\json_generator.py
if %errorlevel% neq 0 exit /b
pylint hisim\loadtypes.py
if %errorlevel% neq 0 exit /b
pylint hisim\log.py
if %errorlevel% neq 0 exit /b
pylint hisim\postprocessingoptions.py
if %errorlevel% neq 0 exit /b
pylint hisim\project_code_overview_generator.py
if %errorlevel% neq 0 exit /b
pylint hisim\simulationparameters.py
if %errorlevel% neq 0 exit /b
pylint hisim\simulator.py
if %errorlevel% neq 0 exit /b
pylint hisim\sim_repository.py
if %errorlevel% neq 0 exit /b
pylint hisim\utils.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\building.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\controller_l1_building_heating.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\controller_l1_generic_runtime.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\controller_l1_heatpump.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\controller_l2_energy_management_system.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\controller_l2_generic_heat_simple.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\controller_l2_smart_controller.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\example_component.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\example_storage.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\example_template.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\example_transformer.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\generic_gas_heater.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\generic_gas_heater_with_controller.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\generic_heat_pump.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\generic_heat_pump_for_house_with_hds.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\generic_hot_water_storage_modular.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\heat_distribution_system.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\idealized_electric_heater.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\simple_hot_water_storage.py
if %errorlevel% neq 0 exit /b
pylint hisim\components\sumbuilder.py
if %errorlevel% neq 0 exit /b
pylint hisim\modular_household\component_connections.py
if %errorlevel% neq 0 exit /b
pylint hisim\modular_household\interface_configs\system_config.py
if %errorlevel% neq 0 exit /b
pylint hisim\postprocessing\chartbase.py
if %errorlevel% neq 0 exit /b
pylint hisim\postprocessing\charts.py
if %errorlevel% neq 0 exit /b
pylint hisim\postprocessing\chart_singleday.py
if %errorlevel% neq 0 exit /b
pylint hisim\postprocessing\compute_kpis.py
if %errorlevel% neq 0 exit /b
pylint hisim\postprocessing\generate_csv_for_housing_database.py
if %errorlevel% neq 0 exit /b
pylint hisim\postprocessing\postprocessing_datatransfer.py
if %errorlevel% neq 0 exit /b
pylint hisim\postprocessing\postprocessing_main.py
if %errorlevel% neq 0 exit /b
pylint hisim\postprocessing\reportgenerator.py
if %errorlevel% neq 0 exit /b
pylint hisim\postprocessing\system_chart.py
if %errorlevel% neq 0 exit /b
pylint tests\functions_for_testing.py
if %errorlevel% neq 0 exit /b
pylint tests\test_advanced_battery_bslib.py
if %errorlevel% neq 0 exit /b
pylint tests\test_building.py
if %errorlevel% neq 0 exit /b
pylint tests\test_building_heating_demand.py
if %errorlevel% neq 0 exit /b
pylint tests\test_building_scalability_with_factor.py
if %errorlevel% neq 0 exit /b
pylint tests\test_building_theoretical_thermal_demand.py
if %errorlevel% neq 0 exit /b
pylint tests\test_examples_basic_household.py
if %errorlevel% neq 0 exit /b
pylint tests\test_examples_basic_household_network_chart.py
if %errorlevel% neq 0 exit /b
pylint tests\test_examples_basic_household_with_all_resultfiles.py
if %errorlevel% neq 0 exit /b
pylint tests\test_examples_household_with_heatpump_and_pv.py
if %errorlevel% neq 0 exit /b
pylint tests\test_example_component.py
if %errorlevel% neq 0 exit /b
pylint tests\test_example_storage.py
if %errorlevel% neq 0 exit /b
pylint tests\test_example_template.py
if %errorlevel% neq 0 exit /b
pylint tests\test_example_transformer.py
if %errorlevel% neq 0 exit /b
pylint tests\test_gas_heater.py
if %errorlevel% neq 0 exit /b
pylint tests\test_generic_gas_heater.py
if %errorlevel% neq 0 exit /b
pylint tests\test_simple_hot_water_storage.py
if %errorlevel% neq 0 exit /b
