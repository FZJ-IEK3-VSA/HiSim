:orphan:

Getting Started with HiSim
===========================

This guide walks you through installing ETHOS.HiSim, running a bundled
simulation example, and viewing the results.

Install HiSim
-------------

Clone the repository and install the package in editable mode::

    git clone https://github.com/FZJ-IEK3-VSA/HiSim.git
    cd HiSim
    python -m venv .venv
    source .venv/bin/activate   # On Windows: .venv\Scripts\activate
    pip install -e .

Run a Simple Simulation
-----------------------

HiSim ships with bundled system setup examples in the
:file:`system_setups/` directory at the repository root.
The simplest example is :file:`simple_system_setup_one.py`, which sums two
series of random numbers.

From the :file:`system_setups/` directory, run::

    cd system_setups
    python ../hisim/hisim_main.py simple_system_setup_one.py

This produces a carpet plot in :file:`system_setups/results/`.

A second toy example, :file:`simple_system_setup_two.py`, demonstrates
chaining components through a transformer before summing.

Run a Real Household Example
----------------------------

The :file:`basic_household.py` setup models a complete household with
occupancy load profiles, weather, a photovoltaic system, a building, and
a heat pump. Run it the same way::

    cd system_setups
    python ../hisim/hisim_main.py basic_household

Run a Simulation with JSON Configuration
----------------------------------------

JSON-based setups separate the *scenario* (components and connections)
from the *simulation parameters* (time range and post-processing).
For example, to run the basic household with 15-minute timesteps and
plotting enabled::

    cd system_setups
    python ../hisim/hisim_main.py basic_household.scenario.json 2021_15minutely_plots.simulation.json

The pre-defined simulation parameter files in :file:`system_setups/`
include:

* :file:`2021_15minutely_plots.simulation.json` — 15-minute resolution with plots
* :file:`2021_minutely_plots.simulation.json` — 1-minute resolution with plots
* :file:`2021_15minutely_noplots.simulation.json` — 15-minute resolution, KPIs and CSV only
* :file:`2021_hourly_report.simulation.json` — hourly resolution with full PDF report

View the Results
----------------

After a simulation completes, results are written to a :file:`results/`
directory next to the setup file (e.g. :file:`system_setups/results/`).
Depending on the post-processing options, this directory contains:

* Carpet plots and single-day line charts (PNG)
* Sankey energy-flow diagrams
* Monthly bar charts
* CSV exports of all time-series outputs
* A PDF summary report with component descriptions and key performance indicators

All result file names include the simulation start time to avoid
overwriting previous runs.
