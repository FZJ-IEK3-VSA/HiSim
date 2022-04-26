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
from hisim import loadtypes as lt




def basic_household_explicit(my_sim, my_simulation_parameters: Optional[SimulationParameters] = None):
    year = 2018
    seconds_per_timestep = 60 * 15
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)
    my_sim.SimulationParameters = my_simulation_parameters
    # Build occupancy
    in_and_output_testing = generic_in_and_output_testing.Test_InandOutputs(my_simulation_parameters=my_simulation_parameters)


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

    my_rn3 = RandomNumbers(name="Random numbers 5-200",
                           timesteps=my_simulation_parameters.timesteps,
                           minimum=5,
                           maximum=200, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_rn3)

    in_and_output_testing.connect_input(in_and_output_testing.TempInput,
                                         my_rn3.ComponentName,
                                         my_rn3.RandomOutput)

    in_and_output_testing.add_component_input( source_component_class=my_rn1,
                                               source_component_output=my_rn1.RandomOutput,
                                               source_load_type= lt.LoadTypes.Any,
                                               source_unit= lt.LoadTypes.Any)
    in_and_output_testing.add_component_input( source_component_class=my_rn2,
                                               source_component_output=my_rn2.RandomOutput,
                                               source_load_type= lt.LoadTypes.Any,
                                               source_unit= lt.LoadTypes.Any)


    my_sim.add_component(in_and_output_testing)

