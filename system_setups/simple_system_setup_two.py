"""Simple System Setup Module."""

# clean

# Generic
from typing import Optional

# Owned
from hisim import log
from hisim.simulator import Simulator
from hisim.simulationparameters import SimulationParameters
from hisim.components.random_numbers import RandomNumbers, RandomNumbersConfig
from hisim.components.example_transformer import (
    ExampleTransformer,
    ExampleTransformerConfig,
)
from hisim.components.sumbuilder import SumBuilderForTwoInputs, SumBuilderConfig


def setup_function(my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]) -> None:
    """Second system setup.

    In this second system setup, two series (my_rn1 and my_transformer) are summed up.

    The first series (my_rn1) is a series of random numbers in a range between 100 and 200.
    The second series (my_transformer) is the result from a series (my_rn2) with random
    values between 10 and 20 after being applied a transformer. The transformer (my_transformer)
    amplifies the input values by 5 times. Hence, the second series has random values between 50 and 100.

    The result is a series (my_sum) with random values between 150 and 300.
    """
    log.information("Starting second system setup")

    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(
            year=2021, seconds_per_timestep=60
        )  # use a full year for testing
    my_sim.set_simulation_parameters(my_simulation_parameters)
    # Create first RandomNumbers object and adds to simulator
    my_rn1 = RandomNumbers(
        config=RandomNumbersConfig(
            building_name="BUI1",
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
            building_name="BUI1",
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
        config=ExampleTransformerConfig.get_default_transformer(), my_simulation_parameters=my_simulation_parameters,
    )
    my_transformer.connect_input(
        input_fieldname=my_transformer.TransformerInput,  # Connect input from my transformer
        src_object_name=my_rn2.component_name,  # to output of second random number object
        src_field_name=my_rn2.RandomOutput,
    )
    my_sim.add_component(my_transformer)  # Add my transformer to simulator

    # Create sum builder object
    my_sum = SumBuilderForTwoInputs(
        config=SumBuilderConfig.get_sumbuilder_default_config(), my_simulation_parameters=my_simulation_parameters,
    )
    # Connect inputs from sum object to both previous outputs
    my_sum.connect_input(
        input_fieldname=my_sum.SumInput1, src_object_name=my_rn1.component_name, src_field_name=my_rn1.RandomOutput,
    )
    my_sum.connect_input(
        input_fieldname=my_sum.SumInput2,
        src_object_name=my_transformer.component_name,
        src_field_name=my_transformer.TransformerOutput,
    )
    my_sim.add_component(my_sum)
