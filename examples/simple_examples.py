"""Simple Examples Module."""

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


def first_example(
    my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]
) -> None:
    """First Example.

    In this first example, a series (my_rn1) of random numbers in a range between 100 and 200 is
    summed up with a series (my_rn2) of random numbers in a range between 10 and 20. The result is
    a series (my_sum) with values between 110 and 220.
    """
    log.information("Starting first example: ")

    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(
            year=2021, seconds_per_timestep=60
        )
    # testmy_sim = copy.deepcopy(my_sim)
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Create first RandomNumbers object and adds to simulator
    my_rn1 = RandomNumbers(
        config=RandomNumbersConfig(
            name="Random numbers 100-200",
            timesteps=my_simulation_parameters.timesteps,
            minimum=100,
            maximum=200,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_rn1)

    # Create second RandomNumbers object and adds to simulator
    my_rn2 = RandomNumbers(
        config=RandomNumbersConfig(
            name="Random numbers 10-20",
            timesteps=my_simulation_parameters.timesteps,
            minimum=10,
            maximum=20,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_rn2)

    # Create sum builder object
    my_sum = SumBuilderForTwoInputs(
        config=SumBuilderConfig.get_sumbuilder_default_config(),
        my_simulation_parameters=my_simulation_parameters,
    )
    # Connect inputs from sum object to both previous outputs
    my_sum.connect_input(
        input_fieldname=my_sum.SumInput1,
        src_object_name=my_rn1.component_name,
        src_field_name=my_rn1.RandomOutput,
    )
    my_sum.connect_input(
        input_fieldname=my_sum.SumInput2,
        src_object_name=my_rn2.component_name,
        src_field_name=my_rn2.RandomOutput,
    )
    my_sim.add_component(my_sum)


def second_example(
    my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]
) -> None:
    """Second Example.

    In this second example, two series (my_rn1 and my_transformer) are summed up.

    The first series (my_rn1) is a series of random numbers in a range between 100 and 200.
    The second series (my_transformer) is the result from a series (my_rn2) with random
    values between 10 and 20 after being applied a transformer. The transformer (my_transformer)
    amplifies the input values by 5 times. Hence, the second series has random values between 50 and 100.

    The result is a series (my_sum) with random values between 150 and 300.
    """
    log.information("Starting second example")

    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(
            year=2021, seconds_per_timestep=60
        )  # use a full year for testing
    my_sim.set_simulation_parameters(my_simulation_parameters)
    # Create first RandomNumbers object and adds to simulator
    my_rn1 = RandomNumbers(
        config=RandomNumbersConfig(
            name="Random numbers 100-200",
            timesteps=my_simulation_parameters.timesteps,
            minimum=100,
            maximum=200,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_rn1)

    # Create second RandomNumbers object and adds to simulator
    my_rn2 = RandomNumbers(
        config=RandomNumbersConfig(
            name="Random numbers 10-20",
            timesteps=my_simulation_parameters.timesteps,
            minimum=10,
            maximum=20,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_rn2)

    # Create new Transformer object
    # my_transformer = Transformer(name="MyTransformer", my_simulation_parameters=my_simulation_parameters)
    my_transformer = ExampleTransformer(
        config=ExampleTransformerConfig.get_default_transformer(),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_transformer.connect_input(
        input_fieldname=my_transformer.TransformerInput,  # Connect input from my transformer
        src_object_name=my_rn2.component_name,  # to output of second random number object
        src_field_name=my_rn2.RandomOutput,
    )
    my_sim.add_component(my_transformer)  # Add my transformer to simulator

    # Create sum builder object
    my_sum = SumBuilderForTwoInputs(
        config=SumBuilderConfig.get_sumbuilder_default_config(),
        my_simulation_parameters=my_simulation_parameters,
    )
    # Connect inputs from sum object to both previous outputs
    my_sum.connect_input(
        input_fieldname=my_sum.SumInput1,
        src_object_name=my_rn1.component_name,
        src_field_name=my_rn1.RandomOutput,
    )
    my_sum.connect_input(
        input_fieldname=my_sum.SumInput2,
        src_object_name=my_transformer.component_name,
        src_field_name=my_transformer.TransformerOutput,
    )
    my_sim.add_component(my_sum)


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


    # # Create first RandomNumbers object and adds to simulator
    # my_rn1 = RandomNumbers(
    #     config=RandomNumbersConfig(
    #         name="Random numbers 100-200",
    #         timesteps=my_simulation_parameters.timesteps,
    #         minimum=100,
    #         maximum=200,
    #     ),
    #     my_simulation_parameters=my_simulation_parameters,
    # )
    # my_sim.add_component(my_rn1)

    # # Create second RandomNumbers object and adds to simulator
    # my_rn2 = RandomNumbers(
    #     config=RandomNumbersConfig(
    #         name="Random numbers 10-20",
    #         timesteps=my_simulation_parameters.timesteps,
    #         minimum=10,
    #         maximum=20,
    #     ),
    #     my_simulation_parameters=my_simulation_parameters,
    # )
    # my_sim.add_component(my_rn2)

    # # Create sum builder object
    # my_sum = SumBuilderForTwoInputs(
    #     config=SumBuilderConfig.get_sumbuilder_default_config(),
    #     my_simulation_parameters=my_simulation_parameters,
    # )
    # # Connect inputs from sum object to both previous outputs
    # my_sum.connect_input(
    #     input_fieldname=my_sum.SumInput1,
    #     src_object_name=my_rn1.component_name,
    #     src_field_name=my_rn1.RandomOutput,
    # )
    # my_sum.connect_input(
    #     input_fieldname=my_sum.SumInput2,
    #     src_object_name=my_rn2.component_name,
    #     src_field_name=my_rn2.RandomOutput,
    # )
    # my_sim.add_component(my_sum)