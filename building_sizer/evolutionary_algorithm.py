# -*- coding: utf-8 -*-
from building_sizer import system_config

from typing import List, Tuple
import random


def unique(
    individuals: List[system_config.Individual],
) -> List[system_config.Individual]:
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
        if i not in delete_index:
            filtered_individuals.append(individuals[i])
    return filtered_individuals


def selection(
    rated_individuals: List[system_config.RatedIndividual], population_size: int
) -> List[system_config.RatedIndividual]:
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
    # Sort individuals decendingly using their rating
    individuals = sorted(rated_individuals, key=lambda ri: ri.rating, reverse=True)
    # Only select the best individuals, adhering to the population size
    individuals = individuals[:population_size]
    # shuffle the selected individuals to allow more variation during crossover
    random.shuffle(individuals)
    return individuals


def complete_population(
    original_parents: List[system_config.Individual],
    population_size: int,
    options: system_config.SizingOptions,
) -> List[system_config.Individual]:
    len_parents = len(original_parents)
    for _ in range(population_size - len_parents):
        individual = system_config.Individual()
        individual.create_random_individual(options=options)
        original_parents.append(individual)
    return original_parents


def crossover_conventional(
    parent1: system_config.Individual, parent2: system_config.Individual
) -> Tuple[system_config.Individual, system_config.Individual]:
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
    child_discrete_1 = vector_discrete_1[:pt] + vector_discrete_2[pt:]
    child_discrete_2 = vector_discrete_2[:pt] + vector_discrete_1[pt:]
    child1 = system_config.Individual(
        bool_vector=child_bool_1, discrete_vector=child_discrete_1
    )
    child2 = system_config.Individual(
        bool_vector=child_bool_2, discrete_vector=child_discrete_2
    )

    return child1, child2


def mutation_bool(parent: system_config.Individual) -> system_config.Individual:
    """
    Mutation: changing bit value at one position in boolean vector.

    Parameters
    ----------
    parent : system_config.Individual
        Encoding of parent used for mutation.

    Returns
    -------
    child : system_config.RatedIndividual
        Encoding of first resulting child from cross over.
    """
    vector_bool = parent.bool_vector[:]
    bit = random.randint(0, len(vector_bool) - 1)
    vector_bool[bit] = not vector_bool[bit]
    child = system_config.Individual(
        bool_vector=vector_bool, discrete_vector=parent.discrete_vector
    )
    return child


def mutation_discrete(
    parent: system_config.Individual, options: system_config.SizingOptions
) -> system_config.Individual:
    """
    Mutation: changing bit value at one position in discrete vector.

    Parameters
    ----------
    parent : system_config.Individual
        Encoding of parent for mutation.
    options : system_config.SizingOptions
        Instance of dataclass sizing options.
        It contains a list of all available options for sizing of each component.

    Returns
    -------
    child : system_config.RatedIndividual
        Encoding of first resulting child from cross over.
    """
    vector_discrete = parent.discrete_vector[:]
    bit = random.randint(0, len(vector_discrete) - 1)

    vector_discrete[bit] = random.choice(getattr(options, options.translation[bit]))
    child = system_config.Individual(
        bool_vector=parent.bool_vector, discrete_vector=vector_discrete
    )
    return child


def evolution(
    parents: List[system_config.Individual],
    r_cross: float,
    r_mut: float,
    mode: str,
    options: system_config.SizingOptions,
) -> List[system_config.Individual]:
    """
    evolution step of the genetic algorithm

    Parameters
    ----------
    parents : List[system_config.RatedIndividual]
        List of rated individuals.
    r_cross : float
        Cross over probability.
    r_mut : float
        Mutation probability.
    mode : str
        Mode 'bool' for boolean variation and 'discrete' for discrete variation.

    Returns
    -------
    Children : List[system_config.Individual]
        List of individuals unrated individuals.

    """
    # get array length
    len_parents = len(parents)
    # index to randomly select parents
    # maybe remove sel part because parents are already shuffeled (sel=0)
    sel = random.randint(0, len_parents - 1)
    # initialize new population
    children = []
    # initialize while loop
    pop = 0

    while pop < len_parents:
        # randomly generate number which indicates if cross over will happen or not...
        o = random.random()

        if o < r_cross:
            # initilize parents
            parent1 = parents[(sel + pop) % len_parents]
            parent2 = parents[(sel + pop + 1) % len_parents]
            # cross over: two children resulting from cross over are added to the family
            child1, child2 = crossover_conventional(parent1=parent1, parent2=parent2)
            # append children to new population
            children.append(child1)
            children.append(child2)
            pop = pop + 2

        elif o < (r_cross + r_mut):
            # choose individual for mutation
            parent = parents[(sel + pop) % len_parents]
            # mutation
            if mode == "bool":
                child = mutation_bool(parent=parent)
            elif mode == "discrete":
                child = mutation_discrete(parent=parent, options=options)
            else:
                raise Exception(
                    "variable for mode is not defined, choose either discrete or bool."
                )
            children.append(child)
            pop = pop + 1

        else:
            pop = pop + 1

    return children
