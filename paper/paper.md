
---
title: 'HiSim: Household Infrastructure and Building Energy Simulation'
tags:
  - Python
  - Household Infrastructure
  - Building Demand Side Management
  - Load Profile
  - Photovoltaics system
  - Heat Pump
  - Seasonal Storage
authors:
  - name: Vitor Hugo Bellotto Zago
    orcid: 0000-0002-3791-4557
    affiliation: 1
  - name: Dr. Noah Pflugradt
    orcid: 0000-0002-1982-8794
    affiliation: 1
  - name: Maximilian Hillen
    orcid: 0000-0002-8171-2661
    affiliation: 1
  - name: Lukas Langenberg
    orcid: 0000-0001-6713-9291
    affiliation: 1
  - name: Tjarko Tjaden
    orcid: 0000-0002-5074-6527
    affiliation: 2
  - name: Leander Kotzur
    orcid: 0000-0001-5856-2311
    affiliation: 1
  - name: Detlef Stolten 
    orcid: 0000-0002-1671-3262
    affiliation: 1
affiliations:
  - name: Forschungszentrum Jülich, Jülich, Germany
    index: 1
  - name: Hochschule Emden/Leer, Emden, Germany
    index: 2
date: 1 October 2021
bibliography: paper.bib
---
# Summary

High volatility and low availability of renewables are the major hurdles in combating climate change. Modern buildings and their occupants employ a variety of devices to suffice their basic needs. Given the house structure and the occupants' behavior, the overall usage of these devices during a certain period of time are represented by electricity consumption and thermal demand profiles.  Due to scale and complexity, building energy systems require a sophisticated simulation environment.

In the last year, the Python package ``Household Infrastructure and Building Energy Simulator``, for short ``HiSim``, was developed in Forschungszentrum Jülich under the MIT-License with a generic, extendable framework to enable the simulation of a building using a list of connected components, e.g., photovoltaic systems, thermal storages, heat pumps, electric car charging stations and others. 

For the first release, ``HiSim`` comes with a limited number of predefined components that can easily be extended by the user. The framework enables the user to connect new components to existing systems and simulate the resulting system at a high temporal resolution. In other words, ``HiSim`` provides means to determine electricity, heating and cooling demands for arbitrary building and occupant configurations. The main aim of the ``HiSim`` development is an extendable, fast and straightforward tool for parameter studies, with possible parallelization for large scale simulations on clusters, supported by a backend in a webservice and other applications.

# Method

``HiSim`` framework is based on the idea of independent, object-oriented components with inputs and outputs that can be connected in arbitrary configurations to create a specific energy system. The user arranges a house energy system through a setup function, where the components and connections are defined. This setup function is passed to the simulator in ``HiSim``, which performs all the connections and compatibility checks among the components as shown in the figure below.

![Framework [@hisimframework]](./img/framework_diagram.png)

The simulator proceeds to run the calculations, employing a simple substitution solver to tackle circular dependencies among the components and to approximate numerical solutions. Finally, the postprocessing automatically outputs carpet, sankey, line plots and generate a final report of the simulation run.

# Development Status

The software grew out of a research project to create guidelines for proper energy storage sizing. It is currently in a beta version and, in the case of some components, still being validated, but can already be used. The following components are already implemented:

- Popular photovoltaic Python library pvlib [@holmgren2018pvlib], to simulate PV system electricity generation (https://doi.org/10.5281/zenodo.5366883) with supported functions from tsib [@kotzur2019future]
- European building stock database by EPISCOPE/TABULA [@loga2016tabula], covering the most common houses from multiple European countries.
- Thermal Building 5R1C method [@jayathissa2017optimising,ISO13790] to calculate household heating and cooling demands for an entire year.
- LoadProfileGenerator Connector for using the output of the LoadProfileGenerator.de, a behavior simulator that generates electricity and warm water load profiles.
- Database for appliances on version 0.1:
    - Heat pumps [@hplib]
    - Batteries
    - EV chargers
    - Washing machines
    - Thermal energy storages
    - Dishwashers
    - Electric vehicles

# A statement of need

While some software packages overlap slightly with ``HiSim`` purposes, e.g., Polysun [@polysunsoftware], PVSol [@pvsolsoftware], Homer [@homersoftware], EnergyPlus [@energyplussoftware], TRNSYS [@trnsyssoftware], those have very different approaches.

Polysun and PVSol aim primarily for craftsmen to specific size systems using a detailed and user-friendlily graphical interface. The lack of a command-line interface to use them for large simulations and parameter studies on a cluster can be cumbersome or even impractical. TRNSYS is also on home energy system software, that due to historical reasons has been implemented in FORTRAN with extensive customization and high optimization for memory usage, all of which requiring from the user a steep learning curve. Being a commercial tool targeted for single processors, TRNSYS is limited both in the extendability and the ability for employment in parameter studies on a large cluster. Although EnergyPlus is a free, open-source, cross-platform software, it comes with similar drawbacks as TRNSYS. EnergyPlus has a strong basis on thermal building analysis, but lacks any type of simulation regarding electricity consumption by home appliances. Being highly customizable, EnergyPlus requires extensive experience just to obtain the first significant results. 
Finally, Homer Pro provides the best means for microgrid design optimization, but does not support high resolution consumer energy load profiles.

``HiSim`` bundles together Python libraries, building stock database, thermal building model based on ISO Standards as well as commercial data from the latest appliances to compile a one workflow for a household energy simulator. Its framework allows an easy and fast implementation of new components, and automatically generate the plots for the outputs as well as the results report.

# Target audience
The scientific community involved in household energy management and building optimization can find here a great tool
to investigate different configurations for a transition to a future of low carbon emission households.

# Acknowledgements

This work is supported by the Helmholtz Association under the Joint Initiative "Energy System 2050 A Contribution of the Research Field Energy" and developed by Forschungszentrum Jülich, with later introduction of Hochschule Emden/Leer through PIEG-Strom project, funded by the German Ministry of Economy and Energy.

# References
