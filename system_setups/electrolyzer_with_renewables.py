"""Simple Electrolyzer system setup."""

# Generic
from typing import Optional

# Owned
from hisim import log
from hisim.config import SizingContext
from hisim.simulator import Simulator
from hisim.simulationparameters import SimulationParameters
from hisim.components.generic_electrolyzer_h2 import (
    Electrolyzer,
    ElectrolyzerConfig,
)

from hisim import loadtypes as lt

# CSV lib
from hisim.components.csvloader import CSVLoader, CSVLoaderConfig
from hisim.components.electricity_meter import ElectricityMeter, ElectricityMeterConfig
from hisim.components.gas_meter import GasMeter, GasMeterConfig
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


def setup_function(my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]) -> None:
    """Electrolyzer system setup: a grid-connected electrolyzer plant fed by a wind turbine.

    A 5 MW wind turbine's output, read from a csv time series in kW, feeds a transformer and rectifier
    rated to the turbine's 5,000 kW, so no wind is curtailed. The electrolyzer turns the electricity
    it takes into hydrogen. The electricity meter reads both devices: what the electrolyzer does not
    take is exported to the grid, and what it takes beyond the delivery is imported. The hydrogen
    meter reads the hydrogen produced and passes it out of the system (owner decisions of 2026-10-28
    on bead hisim-component-physics-4g9.69).
    """
    log.information("Starting basic electrolyzer system setup")

    # =================================================================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set CSV Parameters
    csv_filename = "wind_generated_power_1_min.csv"
    csv_data_column = 1  # The column number in the CSV file containing the load profile data
    # The column holding each row's time label: a run from any date reads its own rows, and a run coarser
    # than the file's minutes reads the mean of the minutes in each timestep.
    csv_time_column = 0
    loadtype = lt.LoadTypes.ELECTRICITY  # Replace with the desired load type
    unit = lt.Units.KILOWATT  # Replace with the desired unit
    column_name = "generated_power"
    sep = ","  # Separator used in the CSV file (e.g., "," or ";")
    decimal = "."  # Decimal indicator used in the CSV file (e.g., "." or ",")
    multiplier = 1  # Multiplier factor for amplification (if needed)

    # Set transformer and rectifier parameter
    name = "StandardTransformerAndRectifier"
    # The rated power of the wind turbine the series stands for, which is also the series' peak: the transformer and
    # rectifier is rated to it, so it carries all the wind and none is curtailed.
    wind_turbine_rated_power_in_kilowatt = 5000.0
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

    # =================================================================================================================================
    # Build Components

    # Setup new CSV loader object
    my_csv_loader = CSVLoaderConfig.for_csv_file(
        "CSV",
        csv_filename=csv_filename,
        column=csv_data_column,  # The column number in the CSV file containing the load profile data
        loadtype=loadtype,  # Replace with the desired load type
        unit=unit,  # Replace with the desired unit
        column_name=column_name,
        sep=sep,  # Separator used in the CSV file (e.g., "," or ";")
        decimal=decimal,  # Decimal indicator used in the CSV file (e.g., "." or ",")
        multiplier=multiplier,  # Multiplier factor for amplification (if needed)
        time_column=csv_time_column,
    )

    # Create new CSV loader object
    csv_loader = CSVLoader(my_csv_loader, my_simulation_parameters=my_simulation_parameters)

    my_electrolyzer_config = ElectrolyzerConfig.for_device("Electrolyzer", electrolyzer_name=electrolyzer_name)

    # Setup the transformer and rectifier unit
    # The preset's 95 % efficiency is the figure from the literature this setup runs on.
    my_transformer_config = TransformerConfig.preset_standard(name)
    # Rated to carry the whole turbine, not only the electrolyzer's maximum load: what the electrolyzer does not take
    # is exported to the grid (owner decision of 2026-10-28). The investment cost scales with this rating.
    my_transformer_config.rated_power_in_kilowatt = wind_turbine_rated_power_in_kilowatt
    # The series is the output of a wind turbine the setup does not model: the unit receives it from the
    # "wind turbine" boundary node, and only what it delivers passes the electricity meter.
    my_transformer_config.received_carrier = lt.EnergyBalanceCarrier.WIND_ELECTRICITY
    my_transformer = Transformer(
        my_simulation_parameters=my_simulation_parameters,
        config=my_transformer_config,
    )

    # Setup the controller
    my_controller = ElectrolyzerController(
        config=ElectrolyzerControllerConfig.for_device("L1ElectrolyzerController", electrolyzer_name),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Setup the electrolyzer
    my_electrolyzer = Electrolyzer(
        config=my_electrolyzer_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # The grid connection: it reads the electricity the transformer delivers and the electrolyzer draws, by their
    # roles, and exports or imports the difference.
    my_electricity_meter = ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=ElectricityMeterConfig.preset_standard("ElectricityMeter"),
    )

    # The hydrogen the plant produces leaves the system through this meter.
    my_hydrogen_meter = GasMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=GasMeterConfig.preset_standard("HydrogenMeter").resolve(
            SizingContext(energy_carrier=lt.LoadTypes.GREEN_HYDROGEN)
        ),
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    # Connect output of csv_loader to input of the transformer
    my_transformer.connect_input(my_transformer.TransformerInput, csv_loader.component_name, csv_loader.Output1)

    my_controller.connect_input(
        my_controller.ProvidedLoadInWatt,
        my_transformer.component_name,
        my_transformer.ElectricityDeliveredInWatt,
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

    my_hydrogen_meter.add_component_input_and_connect(
        source_object_name=my_electrolyzer.component_name,
        source_component_output=my_electrolyzer.CurrentHydrogenFlowRate,
        source_load_type=lt.LoadTypes.GREEN_HYDROGEN,
        source_unit=lt.Units.KG_PER_SEC,
        source_tags=[lt.InandOutputType.GAS_PRODUCTION, lt.InandOutputType.MASS_FLOW],
        source_weight=999,
    )
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
    my_sim.add_component(my_hydrogen_meter)


# python ../hisim/hisim_main.py electrolyzer_with_renewables.py setup_function
