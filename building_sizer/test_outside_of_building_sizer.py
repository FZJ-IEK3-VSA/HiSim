# -*- coding: utf-8 -*-
from building_sizer import system_config
import csv
import random
from building_sizer.evolutionary_algorithm import evolution, selection

# """ Initializes option dataclass"""
# populations_size: int = 5  # number of individuals to be created
# options = system_config.get_default_sizing_options()
# initial_hisim_configs = []  # initialize system_configs
# for i in range(populations_size):  # create five individuals in population
#     individual = system_config.Individual()
#     individual.create_random_individual(options=options)
#     initial_hisim_configs.append(
#         system_config.create_from_individual(individual).to_json()  # type: ignore
#     )
# print(initial_hisim_configs)

# """ Initialize boolean vector. """
# probabilities = [0.8, 0.4]  # probabilites to create PV and battery respectively

# parents = [] # initialize generation
# for i in range(5):  # create five individuals in population
#     individual = system_config.Individual()
#     individual.create_random_individual(probabilities=probabilities)
#     parents.append(individual)

# """ Creoate children, combine, and sort out. """
# # create children
# children = evolution(parents=parents, r_cross=0.3, r_mut=0.4)

# # combine new_vectors and rated_individuals (combine parents and children)
# for elem in parents:
#     children.append(elem)

# # delete duplicates
# children = unique(individuals=children)

# """ Reads in result from csv. """
# # self_consumption = get_kpi_from_csv('KPIs.csv')

# """ Initialize RadtedIndividual and select best. """
# # probabilities = [0.8, 0.4]  # probabilites to create PV and battery respectively

# # rated_individuals = [] # initialize generation
# # for i in range(10):  # create five individuals in population
# #     individual = system_config.Individual()
# #     individual.create_random_individual(probabilities=probabilities)
# #     rated_individual = RatedIndividual(individual=individual, rating=random.uniform(0, 1))
# #     rated_individuals.append(rated_individual)

# # selected_rated_individuals = selection(rated_individuals=rated_individuals, population_size=5)

# """ Creates first round of system_configs. """
# probabilities: List[float] = [0.8, 0.4]  # probabilites to create PV and battery respectively
# populations_size: int = 5  # number of individuals to be created

# initial_hisim_configs = [] # initialize system_configs
# for i in range(populations_size):  # create five individuals in population
#     individual = system_config.Individual()
#     individual.create_random_individual(probabilities=probabilities)
#     initial_hisim_configs.append(system_config.SystemConfig.create_from_individual(individual).to_json())

# probabilities: List[float] = [0.8, 0.4]  # probabilites to create PV and battery respectively
# populations_size: int = 5  # number of individuals to be created

# initial_hisim_configs = [] # initialize system_configs
# for i in range(populations_size):  # create five individuals in population
#     individual = system_config.Individual()
#     individual.create_random_individual(probabilities=probabilities)
#     initial_hisim_configs.append(system_config.SystemConfig.create_from_individual(individual).to_json())
