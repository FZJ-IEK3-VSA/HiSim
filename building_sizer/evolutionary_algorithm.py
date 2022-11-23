# -*- coding: utf-8 -*-
from building_sizer import system_config

from typing import List
import random

def unique(individuals: List[system_config.Individual]):
    """
    Compares all individuals and deletes duplicates.

    Parameters
    ----------
    rated_individuals : List[system_config.RatedIndividual]
        List of all individuals
    population_size : int
        Amount of individuals to be selected

    Returns
    -------
    selected_individuals : List[system_config.RatedIndividual]

    """
    len_individuals = len(individuals)

    # get index of all duplicates
    delete_index = []
    for i in range(len_individuals):
        for j in range(i + 1, len_individuals):
            if individuals[i] == individuals[j]:
                delete_index.append(j)

    # select not duplicated values
    filtered_individuals = []
    for i in range(len_individuals):
        if i in delete_index:
            pass
        else:
            filtered_individuals.append(individuals[i])
    return filtered_individuals



def selection(rated_individuals: List[system_config.RatedIndividual], population_size: int):
    """
    Selects best individuals.

    Parameters
    ----------
    rated_individuals : List[system_config.RatedIndividual]
        List of all individuals
    population_size : int
        Amount of individuals to be selected

    Returns
    -------
    selected_individuals : List[system_config.RatedIndividual]

    """
    # get list of scores
    scores = [elem.rating for elem in rated_individuals]

    # get position of best scores
    len_scores = len(scores)
    sorted_scores_indices = sorted(range(len_scores), key=lambda k: scores[k])

    # select individuals by positions
    selected_individuals = []
    for i in range(len_scores):
        if i in sorted_scores_indices:
            selected_individuals.append(rated_individuals[i])
    return random.shuffle(selected_individuals)

def crossover_conventional(parent1: system_config.Individual, parent2: system_config.Individual):
    """
    cross over: exchange parts of bitstring by randomly generated index

    Parameters
    ----------
    parent1 : system_config.RatedIndividual
        Encoding of first parent used for cross over.
    parent2 : system_config.RatedIndividual
        Encoding of second parent used for cross over.

    Returns
    -------
    child1 : system_config.RatedIndividual
        Encoding of first resulting child from cross over.
    child2 : system_config.RatedIndividual
        Encoding of second resulting child from cross over.
    """
    vector_bool_1 = parent1.bool_vector[:]  # cloning all relevant lists
    vector_discrete_1 = parent1.discrete_vector[:]
    vector_bool_2 = parent2.bool_vector[:]  # cloning all relevant lists
    vector_discrete_2 = parent2.discrete_vector[:]

    # select cross over point, which is not exactly the end or the beginning of the string
    pt = random.randint(1, len(vector_bool_1) - 1)

    # create children by cross over
    child_bool_1 = vector_bool_1[:pt] + vector_bool_2[pt:]
    child_bool_2 = vector_bool_2[:pt] + vector_bool_1[pt:]
    child1 = system_config.Individual(bool_vector=child_bool_1)
    child2 = system_config.Individual(bool_vector=child_bool_1)

    return child1, child2

def mutation_bool(parent):
    """
    Mutation: changing bit value at one position

    Parameters
    ----------
    parent : system_config.RatedIndividual
        Encoding of first parent used for cross over.

    Returns
    -------
    child : system_config.RatedIndividual
        Encoding of first resulting child from cross over.
    """
    vector_bool = parent.bool_vector[:]
    bit = random.randint(0, len(vector_bool) - 1)
    vector_bool[bit] = not vector_bool[bit]
    child = system_config.Individual(bool_vector=vector_bool, discrete_vector=parent.discrete_vector)
    return child

def evolution(parents: List[system_config.Individual], population_size: int, r_cross: float, r_mut: float):
    """
    evolution step of the genetic algorithm

    Parameters
    ----------
    paraents : List[system_config.RatedIndividual]
        List of rated individuals.
    r_cross : float
        Cross over probability.
    r_mut : float
        Mutation probability.

    Returns
    -------
    Children : List[system_config.Individual]
        List of individuals unrated individuals.

    """
    #index to randomly select parents
    sel = random.randint(0, population_size - 1)
    #initialize new population
    children = []
    #initialize while loop
    pop = 0
    while pop < population_size:
        # randomly generate number which indicates if cross over will happen or not...
        o = random.random()

        if o < r_cross:
            #initilize parents
            parent1 = parents[(sel + pop) % population_size]
            parent2 = parents[(sel + pop + 1) % population_size]
            #cross over: two children resulting from cross over are added to the family
            child1, child2 = crossover_conventional(parent1=parent1, parent2=parent2)
            #append children to new population
            children.append(child1)
            children.append(child2)
            pop = pop + 2

        elif o < ( r_cross + r_mut ):
            #choose individual for mutation
            parent = parents[(sel + pop) % population_size]
            #mutation
            child = mutation_bool(parent=parent)
            children.append(child)
            pop = pop + 1

        else:
            pop = pop + 1

    return children
