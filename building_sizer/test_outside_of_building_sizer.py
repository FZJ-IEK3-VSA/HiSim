# -*- coding: utf-8 -*-
import system_config
from evolutionary_algorithm import evolution

""" Initialize boolean vector. """
probabilities = [0.8, 0.4]  # probabilites to create PV and battery respectively

parents = [] # initialize generation
for i in range(5):  # create five individuals in population
    individual = system_config.Individual()
    individual.create_random_individual(probabilities=probabilities)
    parents.append(individual)
    
children = evolution(parents=parents, r_cross=0.3, r_mut=0.4)

