""" RES to buffer battery to electrolyzer. """

# clean

# Generic
from typing import Optional

# Owned
from hisim import log
from hisim.simulator import Simulator
from hisim.simulationparameters import SimulationParameters

from hisim import loadtypes as lt
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum

# CSV lib
from hisim.components.csvloader import CSVLoader, CSVLoaderConfig
from hisim.components.transformer_rectifier import Transformer, TransformerConfig


# import controller
from hisim.components.controller_l2_ptx_energy_management_system import (
    PTXController,
    PTXControllerConfig,
)
from hisim.components.controller_l1_electrolyzer_h2 import (
    ElectrolyzerController,
    ElectrolyzerControllerConfig,
)
from hisim.components.generic_electrolyzer_h2 import (
    Electrolyzer,
    ElectrolyzerConfig,
)
from hisim.postprocessingoptions import PostProcessingOptions

__authors__ = "Franz Oldopp"
__copyright__ = "Copyright 2023, FZJ-IEK-3"
__credits__ = ["Franz Oldopp"]
__license__ = "-"
__version__ = "2.0"
__maintainer__ = "Franz Oldopp"
__status__ = "development"


def ptx_trans_bat_ec_no_grid_pv_final(
    my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]
) -> None:
    """Setup function."""
    log.information("Starting basic electrolyzer example")
    # =================================================================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set CSV Parameters
    component_name = "CSVLoader"
    csv_filename = "PV_power_capped_2500kW_1min_FINAL.csv"
    column = 1  # The column number in the CSV file containing the load profile data !!!!!!!!!!
    loadtype = lt.LoadTypes.ELECTRICITY  # Replace with the desired load type
    unit = lt.Units.KILOWATT  # Replace with the desired unit
    column_name = "power"
    sep = ";"  # Separator used in the CSV file (e.g., "," or ";")
    decimal = "."  # Decimal indicator used in the CSV file (e.g., "." or ",")
    multiplier = 1  # Multiplier factor for amplification (if needed)

    # Set controller parameter
    electrolyzer_name = "FuelCellEnergySOEC"
    """
    "HTecME450"
    "McPhyMcLyzer20030"
    "FuelCellEnergySOEC"
    """
    operation_mode = "StandbyandOffLoad"
    """
    "MinimumLoad"
    "StandbyLoad"
    "StandbyandOffLoad"
    """

    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(
            year=year, seconds_per_timestep=seconds_per_timestep
        )  # use a full year for testing
    my_sim.set_simulation_parameters(my_simulation_parameters)
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PLOT_LINE
    )
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PLOT_SINGLE_DAYS
    )
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PLOT_CARPET
    )
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PLOT_MONTHLY_BAR_CHARTS
    )
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.MAKE_NETWORK_CHARTS
    )
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION_WITH_PYAM
    )
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.GENERATE_PDF_REPORT
    )
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.WRITE_ALL_OUTPUTS_TO_REPORT
    )
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT
    )
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT
    )
    # =================================================================================================================================
    # Build Components

    # Setup new CSV loader object
    my_csv_loader = CSVLoaderConfig(
        name="CSV",
        component_name=component_name,
        csv_filename=csv_filename,
        column=column,  # The column number in the CSV file containing the load profile data
        loadtype=loadtype,  # Replace with the desired load type
        unit=unit,  # Replace with the desired unit
        column_name=column_name,
        sep=sep,  # Separator used in the CSV file (e.g., "," or ";")
        decimal=decimal,  # Decimal indicator used in the CSV file (e.g., "." or ",")
        multiplier=multiplier,  # Multiplier factor for amplification (if needed)
        output_description="Values from CSV"
    )

    # Create new CSV loader object
    csv_loader = CSVLoader(
        my_csv_loader, my_simulation_parameters=my_simulation_parameters
    )

    my_transformer = Transformer(
        my_simulation_parameters=my_simulation_parameters,
        config=TransformerConfig.get_default_transformer(),
    )
    # Setup the battery
    """
    my_battery = Battery(
        my_simulation_parameters=my_simulation_parameters,
        config=BatteryConfig(
        name="test buffer battery",
        source_weight=1,
        system_id="SG1",
        custom_pv_inverter_power_generic_in_watt=100000.0,
        custom_battery_capacity_generic_in_kilowatt_hour=5000.0,
        charge_in_kwh=0.0,
        discharge_in_kwh=0.0,
        co2_footprint=0.0,
        cost=0.0,
        lifetime=10.0,
        lifetime_in_cycles=30000.0,
        maintenance_cost_as_percentage_of_investment=0.04,
        )
    )
    """

    my_ptx_controller = PTXController(
        my_simulation_parameters=my_simulation_parameters,
        config=PTXControllerConfig.control_electrolyzer(
            electrolyzer_name, operation_mode
        ),  # "Nominal Load", "Minimum Load", "Standby Load"
    )
    my_electrolyzer_controller = ElectrolyzerController(
        my_simulation_parameters=my_simulation_parameters,
        config=ElectrolyzerControllerConfig.control_electrolyzer(electrolyzer_name),
    )
    my_electrolyzer = Electrolyzer(
        my_simulation_parameters=my_simulation_parameters,
        config=ElectrolyzerConfig.config_electrolyzer(electrolyzer_name),
    )
    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    # Connect output of csv_loader to input of the transformer (wind to battery)
    my_transformer.connect_input(
        my_transformer.TransformerInput, csv_loader.component_name, csv_loader.Output1
    )

    my_ptx_controller.connect_input(
        my_ptx_controller.RESLoad,
        my_transformer.component_name,
        my_transformer.TransformerOutput,
    )
    """
    my_ptx_controller.connect_input(
        my_ptx_controller.StateOfCharge,
        my_battery.component_name,
        my_battery.StateOfCharge
    )

    my_battery.connect_input(
        my_battery.LoadingPowerInput,
        my_ptx_controller.component_name,
        my_ptx_controller.PowerToThird
    )
    """
    my_electrolyzer_controller.connect_input(
        my_electrolyzer_controller.ProvidedLoad,
        my_ptx_controller.component_name,
        my_ptx_controller.PowerToSystem,
    )

    my_electrolyzer.connect_input(
        my_electrolyzer.LoadInput,
        my_electrolyzer_controller.component_name,
        my_electrolyzer_controller.DistributedLoad,
    )

    my_electrolyzer.connect_input(
        my_electrolyzer.InputState,
        my_electrolyzer_controller.component_name,
        my_electrolyzer_controller.CurrentMode,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(csv_loader)  # Add csv_loader to simulator
    my_sim.add_component(my_transformer)
    # my_sim.add_component(my_battery)
    my_sim.add_component(my_ptx_controller)
    my_sim.add_component(my_electrolyzer_controller)
    my_sim.add_component(my_electrolyzer)

    # Set Results Path
    ResultPathProviderSingleton().set_important_result_path_information(
        module_directory=my_sim.module_directory,
        model_name=my_sim.setup_function,
        variant_name=f"{my_simulation_parameters.duration.days}d_{my_simulation_parameters.seconds_per_timestep}s_{electrolyzer_name}_{operation_mode}",
        hash_number=None,
        sorting_option=SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION,
    )

    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME,
        entry=f"{my_simulation_parameters.duration.days}d_{my_simulation_parameters.seconds_per_timestep}s_{electrolyzer_name}",
    )


# python ../hisim/hisim_main.py ptx_transf_bat_ec_gird.py ptx_trans_bat_ec_grid_final
