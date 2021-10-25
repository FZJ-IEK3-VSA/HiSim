.. House Infrastructure Simulator documentation master file, created by
   sphinx-quickstart on Thu Oct  7 17:06:08 2021.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to HiSim's documentation!
==========================================================

What is HiSim?

It is a Python-package for simulation in building energy systems, similarly to Polysun and TRNSYS, and was developed as part of the project PiegStrom. It contains a time step simulation engine. ``HiSim`` has an extendable framework for integration of any type of new component the user wishes to implement.

Time Step Simulation Principle:
-------------------------------

A building energy system is composed of multiple components, e.g., building, thermal energy storage (TES), heat pump, battery, photovoltaic system or electric vehicle. In this system, the components are interconnected and exchange information, mass fluxes or energy within every time step.
The connection among them occurs through inputs and outputs of every component. In a chain of connections, a building energy system might have a circular
connection as seen by :numref:`timestepsimulation`:

.. _timestepsimulation:

.. figure:: img/time_step_simulation.svg
   :width: 400
   :align: center
   :alt: Alternative text

   Time Step Simulation

Inputs and outputs are nothing but shared parameters among the components. In other words, once a component performed its internal calculations, all its outputs are updated, and so are the inputs of other components that they are connected to. The updated input values are used for the internal calculation, which themselves generate new output values, and so going progressively through all components :numref:`circularsubstitution`.

HiSim is using currently the simplest convergence method: Circular Substitution with an Anti-Oscillation-Switch.


* Split the time into discrete steps
* Run the calculation for each time step
* Each component is called with the input values from the previous component.
* But there are frequently circular dependencies.
* Those require a solver inside a step.
* The solver needs to iterate until a solution is reached.

Circular Substitution with an Anti-Oscillation-Switch Method:
-------------------------------------------------------------

Circular Substitution works in an iterative manner within one single time step, overwriting common shared inputs and outputs among the components. To illustrate this concept, take a home energy system containing a building, a heat pump controller, a heat pump and a thermal energy storage depicted in :numref:`circularsubstitution`.

Under the simulator command, the building perform its internal calculations and update the output values.

* Call first component
* Feed Output of the first component to the second component
* All values that are not available yet stay 0
* Then feed output of component 1 and 2 to the 3rd component
* Repeat in a circle until values stop changing (convergence)

If after 10 iterations no convergence has been reached, tell all oscillating components to just stick to the last value. (Anti-Oscillation-Switch)

.. _circularsubstitution:

.. figure:: img/hisim_iterations.svg
  :width: 800
  :align: center
  :alt: Alternative text

  Circular Substitution

Installation
---------------------------------------------------------

Please see the :ref:`installation`

License
=========================================================
HiSim is distributed under `MIT License <https://github.com/FZJ-IEK3-VSA/HiSim/blob/main/LICENSE>`_ .

Modules
==========================================================

.. toctree::
   :maxdepth: 1
   :caption: Contents:

   modules
   installation
   tutorial

Contribution
============================================================

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`