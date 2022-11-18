# -*- coding: utf-8 -*-
import system_config
import csv
import random
from evolutionary_algorithm import evolution, selection

""" Initialize boolean vector. """
# probabilities = [0.8, 0.4]  # probabilites to create PV and battery respectively

# parents = [] # initialize generation
# for i in range(5):  # create five individuals in population
#     individual = system_config.Individual()
#     individual.create_random_individual(probabilities=probabilities)
#     parents.append(individual)
    
# children = evolution(parents=parents, r_cross=0.3, r_mut=0.4)
                
""" Reads in result from csv. """           
# self_consumption = get_kpi_from_csv('KPIs.csv')

""" Initialize RadtedIndividual and select best. """
probabilities = [0.8, 0.4]  # probabilites to create PV and battery respectively

rated_individuals = [] # initialize generation
for i in range(10):  # create five individuals in population
    individual = system_config.Individual()
    individual.create_random_individual(probabilities=probabilities)
    rated_individual = RatedIndividual(individual=individual, rating=random.uniform(0, 1))
    rated_individuals.append(rated_individual)
    
selected_rated_individuals = selection(rated_individuals=rated_individuals, population_size=5)