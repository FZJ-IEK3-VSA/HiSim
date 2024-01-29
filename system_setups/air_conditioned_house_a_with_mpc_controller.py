"""Air-conditioned household."""

# clean
from typing import Optional

from hisim.simulator import SimulationParameters
from hisim.simulator import Simulator
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import controller_pid
from hisim.components import air_conditioner
from hisim.components import controller_mpc  # mpc
from hisim.components import generic_battery  # mpc
from hisim.components import generic_price_signal  # mpc


__authors__ = "Marwa Alfouly, Sebastian Dickler"
__copyright__ = "Copyright 2023, HiSim - Household Infrastructure and Building Simulator"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Sebastian Dickler"
__email__ = "s.dickler@fz-juelich.de"
__status__ = "development"


def setup_function(my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters] = None) -> None:
    """Simulates household with air-conditioner with MPC controller."""

    air_conditioned_house(my_sim, "MPC", my_simulation_parameters)


def air_conditioned_house(
    my_sim: Simulator,
    control: str,
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:
    """Household Model.

    This setup function emulates an air-conditioned house.
    Here the residents have their electricity covered by a photovoltaic system, a battery, and the electric grid.
    Thermal load of the building is covered with an air conditioner.

    Connected components are:
        - Occupancy (Residents' Demands)
        - Weather
        - Price signal
        - Photovoltaic System
        - Building
        - Air conditioner
        - Controllers: Three options are available: on-off control / PID controller / model predictive controller.
        Please adjust the variable "control = .... " accordingly

    Analyzed region: Southern Europe.

    Representative locations selected according to climate zone with
    focus on the Mediterranean climate as it has the highest cooling demand.

    TABULA code for the exemplary single-family household is chosen according to the construction year class:

        1. Seville: ES.ME.SFH.05.Gen.ReEx.001.001 , construction year: 1980 - 2006 okay
        2. Madrid:  ES.ME.SFH.05.Gen.ReEx.001.001 , construction year: 1980 - 2006 okay
        3. Milan: IT.MidClim.SFH.06.Gen.ReEx.001.001 , construction year: 1976 - 1990 okay
        4. Belgrade: RS.N.SFH.06.Gen.ReEx.001.002, construction year: 1981-1990 okay
        5. Ljubljana: SI.N.SFH.04.Gen.ReEx.001.003, construction year: 1981-2001   okay

        For the following two locations building were selected for a different construction class, because at earlier
        years the building are of old generation (compared to the other 5 locations)

        6. Athens: GR.ZoneB.SFH.03.Gen.ReEx.001.001 , construction year: 2001 - 2010
        7. Cyprus: CY.N.SFH.03.Gen.ReEx.001.003, , construction year: 2007 - 2013

    Remarks about the model predictive controller (MPC):

        You need to install HSL solvers. Please refer to https://www.hsl.rl.ac.uk/ipopt/

        MPC applies a moving horizon principle where the optimization is executed at each timestep. This leads to a very high
        simulation time. Therefore, there are two options to get the optimal solution (adjust the variable "mpc_scheme =...."
        to choose your desired approach):

            1. 'optimization_once_aday_only': The optimization is done once each 24 hours and the optimal solution is
            applied for the next 24 hours --> Simulation time for building with PV and Battery is 10 min for a time_step size = 20 min

            Note: for this option it is possible to simulate HiSim with timestep size of 1 minutes and adjust the sampling
            time for optimization to a higher value (e.g., 15) to reduce the computational complexity. Disadvantage
            of this option is that temperature may deviate by around 0.02 °C from the set point. To use this feature please
            adjust the sampling_rate accordingly.

            2. 'moving_horizon_control': Optimization is done each timestep. Pros: can react to sudden disturbance,
            cons: very high simulation time (2 steps/s if timestep size is 20 min)

        You can run the optimization for:
            1. a basic case that only includes building - air conditioning. No PV or battery
            2. a building with PV generation installed
            3. a building with PV and battery.
            Please adjust the variable "flexibility_element = ... " accordingly

        To investigate the impact of Demand response programs. Fixed and dynamic price signal are available.
        Please adjust the variable "pricing_scheme=..." accordingly

        Future work: Improving the implementation with automatic scaling of the optimal control problem formulation.
        This could make/makes a moving horizon scheme possible in reasonable time.
    """

    # Delete all files in cache:
    # dir_cache = "..//hisim//inputs//cache"
    # if os.path.isdir(dir_cache):
    #     for file in os.listdir(dir_cache):
    #         os.remove(os.path.join(dir_cache, file))

    # System parameters

    # Set general simulation parameters
    year = 2021
    seconds_per_timestep = 60

    # Set general controller parameters #mpc
    # Temperature comfort range in °C
    min_comfort_temp = 21.0
    max_comfort_temp = 24.0

    # Set prediction
    predictive = True
    predictive_control = True
    prediction_horizon = 24 * 3600

    # Set weather
    location = "Seville"

    # Set PV system
    time = 2019
    power = 4e3
    load_module_data = False
    module_name = "Hanwha HSL60P6-PA-4-250T [2013]"
    integrate_inverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    name = "PVSystem"
    azimuth = 180
    tilt = 30
    source_weight = -1
    pv_co2_footprint = power * 1e-3 * 130.7
    pv_cost = power * 1e-3 * 535.81
    pv_maintenance_cost_as_percentage_of_investment = 0.01
    pv_lifetime = 25

    # Set building
    building_code = "CY.N.SFH.03.Gen.ReEx.001.003"
    building_class = "medium"
    initial_temperature = 21
    heating_reference_temperature = -14
    absolute_conditioned_floor_area_in_m2 = None
    total_base_area_in_m2 = None
    number_of_apartments = None
    enable_opening_windows: bool = False

    # Set on/off controller #onoff
    # t_air_heating = min_comfort_temp
    # t_air_cooling = max_comfort_temp
    # offset = 0.5

    # Set MPC controller #mpc
    mpc_scheme = (
        "optimization_once_aday_only"  # The two options are: 'optimization_once_aday_only' or 'moving_horizon_control'
    )
    flexibility_element = (
        "PV_and_Battery"  # The three options are: 'basic_buidling_configuration' or 'PV_only' or 'PV_and_Battery'
    )
    pricing_scheme = "dynamic"  # The two options are: 'dynamic' or 'fixed'
    optimizer_sampling_rate = 1
    # prediction_horizon = 24*3600
    # mpc_battery_capacity = 5e3

    # Set air conditioner
    ac_manufacturer = "Samsung"  # Other option: "Panasonic" , Further options are avilable in the smart_devices file
    ac_model = "AC120HBHFKH/SA - AC120HCAFKH/SA"  # "AC120HBHFKH/SA - AC120HCAFKH/SA"     #Other option: "CS-TZ71WKEW + CU-TZ71WKE"#
    hp_min_operation_time = 900  # Unit: seconds
    hp_min_idle_time = 300  # Unit: seconds
    # control = "PID"  # Avialable options are: PID or on_off or MPC

    # set battery #mpc
    batt_manufacturer = "sonnen"
    batt_model = "sonnenBatterie 10 - 5,5 kWh"
    batt_soc = 0.5 * 5000

    # Set simulation parameters
    # seconds_per_timestep = 60 #mpc#pid#onoff
    if control == "MPC":
        seconds_per_timestep = (
            60 * 20
        )  # multiply seconds_per_timestep with factor (e.g., 20) to run MPC with bigger sampling time
    else:
        seconds_per_timestep = 60

    # Build components

    """System parameters"""

    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.one_day_only(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.one_day_only_with_only_plots(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.one_day_only_with_all_options(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.one_week_only(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.one_week_with_only_plots(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.three_months_only(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.three_months_with_plots_only(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.full_year_all_options(year, seconds_per_timestep)

        my_simulation_parameters.enable_all_options()

    my_sim.set_simulation_parameters(my_simulation_parameters)

    """Building (1/2)"""
    my_building_config = building.BuildingConfig(
        name="Building1",
        building_code=building_code,
        building_heat_capacity_class=building_class,
        initial_internal_temperature_in_celsius=initial_temperature,
        heating_reference_temperature_in_celsius=heating_reference_temperature,
        set_heating_temperature_in_celsius=min_comfort_temp,
        set_cooling_temperature_in_celsius=max_comfort_temp,
        absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
        total_base_area_in_m2=total_base_area_in_m2,
        number_of_apartments=number_of_apartments,
        predictive=predictive,
        enable_opening_windows=enable_opening_windows,
    )
    my_building = building.Building(
        config=my_building_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    """ Occupancy Profile """
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy_config.data_acquisition_mode = (
        loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE
    )
    my_occupancy_config.predictive_control = predictive_control
    my_occupancy_config.predictive = predictive

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_occupancy)

    """Weather"""
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.SEVILLE)
    my_weather_config.predictive_control = predictive_control

    my_weather = weather.Weather(
        config=my_weather_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_weather)

    """Photovoltaic System"""
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig(
        time=time,
        location=location,
        power_in_watt=power,
        load_module_data=load_module_data,
        module_name=module_name,
        integrate_inverter=integrate_inverter,
        module_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,
        inverter_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,
        tilt=tilt,
        azimuth=azimuth,
        inverter_name=inverter_name,
        source_weight=source_weight,
        name=name,
        co2_footprint=pv_co2_footprint,
        cost=pv_cost,
        maintenance_cost_as_percentage_of_investment=pv_maintenance_cost_as_percentage_of_investment,
        lifetime=pv_lifetime,
        predictive=predictive,
        prediction_horizon=prediction_horizon,
        predictive_control=predictive_control,
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    my_sim.add_component(my_photovoltaic_system)

    """Building (2/2)"""
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_sim.add_component(my_building)

    """Price signal"""  # mpc
    my_price_signal_config = generic_price_signal.PriceSignalConfig(
        name="PriceSignal",
        country="Spain",
        pricing_scheme=pricing_scheme,
        installed_capacity=power,
        price_signal_type="dummy",
        fixed_price=[],
        static_tou_price=[],
        price_injection=0.0,
        prediction_horizon=prediction_horizon,
        predictive_control=predictive_control,
    )
    my_price_signal = generic_price_signal.PriceSignal(
        config=my_price_signal_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_price_signal)

    """Air Conditioner"""
    my_air_conditioner_config = air_conditioner.AirConditionerConfig(
        name="AirConditioner",
        model_name=ac_model,
        manufacturer=ac_manufacturer,
        min_operation_time=hp_min_operation_time,
        min_idle_time=hp_min_idle_time,
        control=control,
    )
    my_air_conditioner = air_conditioner.AirConditioner(
        config=my_air_conditioner_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_air_conditioner.connect_input(
        my_air_conditioner.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )
    my_air_conditioner.connect_input(
        my_air_conditioner.TemperatureMean,
        my_building.component_name,
        my_building.TemperatureMeanThermalMass,
    )
    my_sim.add_component(my_air_conditioner)

    """Generic Battery"""  # mpc
    if control == "MPC":
        my_battery_config = generic_battery.GenericBatteryConfig(
            manufacturer=batt_manufacturer,
            model=batt_model,
            soc=batt_soc,
            name="Generic Battery",
            base=False,
            predictive=predictive,
        )
        my_battery = generic_battery.GenericBattery(
            config=my_battery_config,
            my_simulation_parameters=my_simulation_parameters,
        )
        my_sim.add_component(my_battery)

    """Model Predictive Controller"""  # mpc
    if control == "MPC":
        my_mpc_controller_config = controller_mpc.MpcControllerConfig(
            mpc_scheme=mpc_scheme,
            min_comfort_temp=min_comfort_temp,
            max_comfort_temp=max_comfort_temp,
            optimizer_sampling_rate=optimizer_sampling_rate,
            initial_temeperature=initial_temperature,
            flexibility_element=flexibility_element,
            initial_state_of_charge=batt_soc,
            name="MpcController",
            temp_forecast=[],
            phi_m_forecast=[],
            phi_st_forecast=[],
            phi_ia_forecast=[],
            pv_forecast_yearly=[],
            maximum_storage_capacity=0.0,
            minimum_storage_capacity=0.0,
            maximum_charging_power=0.0,
            maximum_discharging_power=0.0,
            battery_efficiency=0.0,
            inverter_efficiency=0.0,
            temperature_forecast_24h_1min=[],
            phi_m_forecast_24h_1min=[],
            phi_ia_forecast_24h_1min=[],
            phi_st_forecast_24h_1min=[],
            pv_forecast_24h_1min=[],
            price_purchase_forecast_24h_1min=[],
            price_injection_forecast_24h_1min=[],
            optimal_cost=[],
            revenues=[],
            air_conditioning_electricity=[],
            cost_optimal_temperature_set_point=[],
            pv2load=[],
            electricity_from_grid=[],
            electricity_to_grid=[],
            battery_to_load=[],
            pv_to_battery_timestep=[],
            battery_power_flow_timestep=[],
            battery_control_state=[],
            batt_soc_actual_timestep=[],
            batt_soc_normalized_timestep=[],
            h_tr_w=0.0,
            h_tr_ms=0.0,
            h_tr_em=0.0,
            h_ve_adj=0.0,
            h_tr_is=0.0,
            c_m=0.0,
            cop_coef=[0] * 2,
            eer_coef=[0] * 2,
            predictive=predictive,
            prediction_horizon=prediction_horizon,
        )

        my_mpc_controller = controller_mpc.MpcController(
            config=my_mpc_controller_config,
            my_simulation_parameters=my_simulation_parameters,
        )
        my_mpc_controller.connect_input(
            my_mpc_controller.TemperatureMean,
            my_building.component_name,
            my_building.TemperatureMeanThermalMass,
        )
        my_sim.add_component(my_mpc_controller)
        my_battery.connect_input(
            my_battery.State,
            my_mpc_controller.component_name,
            my_mpc_controller.BatteryControlState,
        )
        my_battery.connect_input(
            my_battery.ElectricityInput,
            my_mpc_controller.component_name,
            my_mpc_controller.BatteryChargingDischargingPower,
        )
        my_battery.connect_input(
            my_battery.ElectricityInput,
            my_mpc_controller.component_name,
            my_mpc_controller.Battery2Load,
        )

    """PID controller"""  # pid
    if control == "PID":
        my_pid_controller_config = controller_pid.PIDControllerConfig.get_default_config()
        pid_controller = controller_pid.PIDController(
            config=my_pid_controller_config,
            my_simulation_parameters=my_simulation_parameters,
        )
        pid_controller.connect_input(
            pid_controller.TemperatureMean,
            my_building.component_name,
            my_building.TemperatureMeanThermalMass,
        )
        pid_controller.connect_input(
            pid_controller.HeatFluxThermalMassNode,
            my_building.component_name,
            my_building.HeatFluxThermalMassNode,
        )
        pid_controller.connect_input(
            pid_controller.HeatFluxWallNode,
            my_building.component_name,
            my_building.HeatFluxWallNode,
        )
        my_air_conditioner.connect_input(
            my_air_conditioner.FeedForwardSignal,
            pid_controller.component_name,
            pid_controller.FeedForwardSignal,
        )
        my_air_conditioner.connect_input(
            my_air_conditioner.ThermalPowerPID,
            pid_controller.component_name,
            pid_controller.ThermalPowerPID,
        )
        my_sim.add_component(pid_controller)

    """Air conditioner on-off controller"""  # onoff
    # if control=="on_off":
    # my_air_conditioner_controller_config = air_conditioner.AirConditionerControllerConfig(
    #   t_air_heating=t_air_heating,
    #   t_air_cooling=t_air_cooling,
    #   offset=offset,
    #   name="AirConditioner",
    # )
    # my_air_conditioner_controller=air_conditioner.AirConditionerController(
    #   config = my_air_conditioner_controller_config,
    #   my_simulation_parameters = my_simulation_parameters,
    # )

    # my_air_conditioner_controller.connect_input(
    #   my_air_conditioner_controller.TemperatureMean,
    #   my_building.component_name,
    #   my_building.TemperatureMeanThermalMass,
    # )

    # my_sim.add_component(my_air_conditioner_controller)

    # my_air_conditioner.connect_input(my_air_conditioner.State,
    # my_air_conditioner_controller.component_name,
    # my_air_conditioner_controller.State)

    # if control == "MPC":
    # my_air_conditioner.connect_input(
    #   my_air_conditioner.OperatingMode,
    #   my_mpc_controller.component_name,
    #   my_mpc_controller.OperatingMode
    # )
    # my_air_conditioner.connect_input(
    #   my_air_conditioner.GridImport,
    #   my_mpc_controller.component_name,
    #   my_mpc_controller.GridImport
    # )
    # my_air_conditioner.connect_input(
    #   my_air_conditioner.PV2load,
    #   my_mpc_controller.component_name,
    #   my_mpc_controller.PV2load
    # )
    # my_air_conditioner.connect_input(
    #   my_air_conditioner.Battery2Load,
    #   my_mpc_controller.component_name,
    #   my_mpc_controller.Battery2Load
    # )

    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_air_conditioner.component_name,
        my_air_conditioner.ThermalEnergyDelivered,
    )
