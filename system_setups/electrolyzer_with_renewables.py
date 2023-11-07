"""Simple Electrolyzer Example."""

# clean

# Generic
from typing import Optional

# Owned
from hisim import log
from hisim.simulator import Simulator
from hisim.simulationparameters import SimulationParameters
from hisim.components.generic_electrolyzer_h2 import (
    Electrolyzer,
    ElectrolyzerConfig,
)

from hisim import loadtypes as lt

# CSV lib
from hisim.components.csvloader import CSVLoader, CSVLoaderConfig
from hisim.components.transformer_rectifier import Transformer, TransformerConfig

# import controller
from hisim.components.controller_l1_electrolyzer_h2 import (
    ElectrolyzerController,
    ElectrolyzerControllerConfig,
)

__authors__ = "Franz Oldopp"
__copyright__ = "Copyright 2023, FZJ-IEK-3"
__credits__ = ["Franz Oldopp"]
__license__ = "-"
__version__ = "2.0"
__maintainer__ = "Franz Oldopp"
__status__ = "development"


def electrolyzer_example(
    my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]
) -> None:
    """Electrolyzer Example.

    In this example, a power input from a csv time series file is transformed
    into a hydrogen mass flow as an output.

    The result is a time series (my_transformer).
    """
    log.information("Starting basic electrolyzer example")

    # =================================================================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set CSV Parameters
    component_name = "CSV Loader"
    csv_filename = "wind_generated_power_1_min.csv"
    column = 1  # The column number in the CSV file containing the load profile data
    loadtype = lt.LoadTypes.ELECTRICITY  # Replace with the desired load type
    unit = lt.Units.KILOWATT  # Replace with the desired unit
    column_name = "generated_power"
    sep = ","  # Separator used in the CSV file (e.g., "," or ";")
    decimal = "."  # Decimal indicator used in the CSV file (e.g., "." or ",")
    multiplier = 1  # Multiplier factor for amplification (if needed)

    # Set transformer and rectifier parameter
    name = "Standard transformer and rectifier unit"
    efficiency = 0.95  # from literature
    loadtype = lt.LoadTypes.ELECTRICITY
    unit = lt.Units.KILOWATT

    # Set controller parameter
    electrolyzer_name = "HTecME450"

    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )  # use a full year for testing
    my_sim.set_simulation_parameters(my_simulation_parameters)
    # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_LINE)

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

    # Setup the transformer and rectifier unit
    my_transformer = Transformer(
        my_simulation_parameters=my_simulation_parameters,
        config=TransformerConfig(
            name=name, efficiency=efficiency,
        ),
    )

    # Setup the controller
    my_controller = ElectrolyzerController(
        config=ElectrolyzerControllerConfig.control_electrolyzer(electrolyzer_name),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Setup the electrolyzer
    my_electrolyzer = Electrolyzer(
        config=ElectrolyzerConfig.config_electrolyzer(electrolyzer_name),
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    # Connect output of csv_loader to input of the transformer
    my_transformer.connect_input(
        my_transformer.TransformerInput, csv_loader.component_name, csv_loader.Output1
    )

    my_controller.connect_input(
        my_controller.ProvidedLoad,
        my_transformer.component_name,
        my_transformer.TransformerOutput,
    )

    my_electrolyzer.connect_input(
        my_electrolyzer.InputState,
        my_controller.component_name,
        my_controller.CurrentMode,
    )

    my_electrolyzer.connect_input(
        my_electrolyzer.LoadInput,
        my_controller.component_name,
        my_controller.DistributedLoad,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_transformer)
    my_sim.add_component(csv_loader)  # Add csv_loader to simulator
    my_sim.add_component(my_controller)  # Add my_controller to simulator
    my_sim.add_component(my_electrolyzer)


# python ../hisim/hisim_main.py electrolyzer_example_1_min_controller_test.py electrolyzer_example

# python ../hisim/hisim_main.py electrolyzer_with_renewables.py electrolyzer_example
