import unittest
from simulator import Simulator, SimulationParameters
import numpy as np

from components.controller import Controller
from components.random_numbers import RandomNumbers

def test_controller(my_sim: Simulator):
    print("test_heat_storage_with_random_numbers")
    my_sim_param: SimulationParameters = SimulationParameters.full_year(year=2021,
                                                                        seconds_per_timestep=60)  # Uses a full year as timeline
    my_sim.set_parameters(my_sim_param)                                                                 # Sets timeline to simulator

    random_numbers1=RandomNumbers(name="First random number",timesteps=my_sim_param.timesteps,minimum=19,maximum=20)
    random_numbers2=RandomNumbers(name="Second random number",timesteps=my_sim_param.timesteps,minimum=10,maximum=20)
    random_numbers3=RandomNumbers(name="Third random number",timesteps=my_sim_param.timesteps,minimum=60,maximum=70)
    random_numbers4=RandomNumbers(name="Forth random number",timesteps=my_sim_param.timesteps,minimum=100,maximum=100)

    cont=Controller(my_sim_param)

    my_sim.add_component(random_numbers1)
    my_sim.add_component(random_numbers2)
    my_sim.add_component(random_numbers3)


    cont.connect_input(input_fieldname=cont.ElectricityConsumptionBuilding,
                         src_object_name=random_numbers1.ComponentName,
                         src_field_name=random_numbers1.RandomOutput)
    cont.connect_input(input_fieldname=cont.ElectricityOutputPvs,
                         src_object_name=random_numbers1.ComponentName,
                         src_field_name=random_numbers1.RandomOutput)




    my_sim.add_component(cont)

