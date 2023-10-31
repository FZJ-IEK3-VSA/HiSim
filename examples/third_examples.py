"""Cell4Life full model"""

# clean

# Generic
from typing import Optional

# Owned
import copy
import graphviz
import graphlib
import dot_parser
import pydot

from hisim import log
from hisim.simulator import Simulator
from hisim.simulationparameters import SimulationParameters
from hisim.components.random_numbers import RandomNumbers, RandomNumbersConfig
from hisim.components.example_transformer import (
    ExampleTransformer,
    ExampleTransformerConfig,
)
from hisim.components.sumbuilder import SumBuilderForTwoInputs, SumBuilderConfig
from hisim.components.csvloader import CSVLoader, CSVLoaderConfig
from hisim.components import electricity_meter
from hisim.components import generic_pv_system
from hisim import loadtypes
from hisim import postprocessingoptions


# Christof:
def third_example(
    my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]
) -> None:
    """Third Example.

    
    Integration of csvload 
    Loading of Project Data

    """
    log.information("Starting Third example: ")

    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(
            year=2021, seconds_per_timestep=3600
        )
     
    # testmy_sim = copy.deepcopy(my_sim)
    my_sim.set_simulation_parameters(my_simulation_parameters)
   
    # Postprocessing Options
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.EXPORT_TO_CSV)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.PLOT_LINE)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.MAKE_NETWORK_CHARTS)
    
    
    # Integration of csvloader 
    # PV Output in kW

    
    my_photovoltaic_systemConfig = CSVLoaderConfig("PV", "PVComponent", "Simulationsdaten_Pilzgasse_230705-Input-HiSim.csv", 6, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Photovoltaik", ";", ",",1000, "OutputPVinW")
    my_photovoltaic_system = CSVLoader(my_photovoltaic_systemConfig, my_simulation_parameters)
  
    # Electricity consumption in W per m2 NGF
    NGFm2 = 26804.8 
    print(NGFm2, " m2")

    my_electricityconsumptionConfig = CSVLoaderConfig("Current total", "CurrentConsumptionper total", "Simulationsdaten_Pilzgasse_230705-Input-HiSim.csv", 1, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Strom", ";", "," ,NGFm2, "CurrentConspumtioninWperm2NGF")
    my_electricityconsumption = CSVLoader(my_electricityconsumptionConfig, my_simulation_parameters)
    

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )


    #------------
    #Connect Component Inputs with Outputs

   
    # Electricity Grid
    my_electricity_meter.add_component_input_and_connect(
        source_component_class=my_photovoltaic_system,
        source_component_output=my_photovoltaic_system.Output1,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[
            # loadtypes.ComponentType.PV,
            loadtypes.InandOutputType.ELECTRICITY_PRODUCTION,
        ],
        source_weight=999,
    )
    print(my_photovoltaic_system.Output1)
    my_electricity_meter.add_component_input_and_connect(
        source_component_class=my_electricityconsumption,
        source_component_output=my_electricityconsumption.Output1,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )

    # Add Components to Simulation Parameters

    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_electricityconsumption)
    my_sim.add_component(my_electricity_meter)
    print(my_electricity_meter.inputs)
