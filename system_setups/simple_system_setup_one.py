"""Simple System Setup Module."""

# clean

# Generic
from typing import Optional

# Owned
from hisim import log
from hisim.simulator import Simulator
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.components.random_numbers import RandomNumbers, RandomNumbersConfig
from hisim.components.sumbuilder import SumBuilderForTwoInputs, SumBuilderConfig


def setup_function(my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]) -> None:
    """First system setup.

    In this first system setup, a series (my_rn1) of random numbers in a range between 100 and 200 is
    summed up with a series (my_rn2) of random numbers in a range between 10 and 20. The result is
    a series (my_sum) with values between 110 and 220.
    """
    log.information("Starting first system setup: ")

    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_CARPET)

    # testmy_sim = copy.deepcopy(my_sim)
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
