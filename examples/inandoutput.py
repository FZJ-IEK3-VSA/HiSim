from typing import Optional, List, Union

from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import generic_price_signal
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import controller_l3_predictive
from hisim.components import generic_smart_device_2
from hisim.components import building
from hisim.components import generic_in_and_output_testing
from hisim.components import generic_dhw_boiler
from hisim.components.random_numbers import RandomNumbers
from hisim.components.example_transformer import Transformer
from hisim.components.set_in_and_outputs import DynamicComponent

def basic_household_explicit(my_sim, my_simulation_parameters: Optional[SimulationParameters] = None):
    year = 2018
    seconds_per_timestep = 60 * 15
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)
    my_sim.SimulationParameters = my_simulation_parameters
    # Build occupancy
    in_and_output_testing = generic_in_and_output_testing.Test_InandOutputs(my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(in_and_output_testing)

    my_rn1 = RandomNumbers(name="Random numbers 100-200",
                           timesteps=my_simulation_parameters.timesteps,
                           minimum=100,
                           maximum=200, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_rn1)

    # Create second RandomNumbers object and adds to simulator
    my_rn2 = RandomNumbers(name="Random numbers 10-20",
                           timesteps=my_simulation_parameters.timesteps,
                           minimum=10,
                           maximum=20, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_rn2)

    rn_input=DynamicComponent(name="RandomNumbers",
                              my_simulation_parameters=my_simulation_parameters)
    rn_input.add_component_input(my_rn1.ComponentName, ["Number"])
    rn_input.add_component_input(my_rn2.ComponentName, ["Number"])
    rn_input.ad
    in_and_output_testing.connect_input(input_fieldname=in_and_output_testing.MassflowOutput,
                         src_object_name=rn_input.ComponentName,
                         src_field_name=rn_input.RandomOutput)