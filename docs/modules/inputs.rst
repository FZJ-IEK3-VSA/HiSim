Inputs Module
=============

The ``hisim.inputs`` package is the central data repository for HiSim
simulations. It provides the static reference datasets — weather records,
building-stock characteristics, household load profiles, component
configurations, and cost/emission factors — that the simulator reads at
runtime.

Working Principle
-----------------

Input data is organised by domain into subdirectories. Each domain folder
typically contains raw sources alongside processed derivatives (in a
``data_processed/`` subdirectory). The path constants are exposed through
:mod:`hisim.utils` (``HISIMPATH`` and ``utils.hisim_inputs``) so that
components can locate files relative to the installed package root without
depending on the current working directory.

Data Domains
~~~~~~~~~~~~

* **weather/** — European Test Reference Years (TRY), DWD observations, and
  NSRDB solar datasets for multiple locations:

  * ``test-reference-years_1995-2012_1-location/`` — TRY data for a single
    location (Aachen).
  * ``test-reference-years_2015-2045_15-locations/`` — TRY data for 15
    locations across 2015–2045.
  * ``dwd_10min_data/`` — German Weather Service (DWD) 10-minute
    observations.
  * ``dwd_15min_data/`` — German Weather Service (DWD) 15-minute
    observations.
  * ``NSRDB/`` — National Solar Radiation Database hourly data.
  * ``NSRDB_15min/`` — National Solar Radiation Database 15-minute data.

* **housing/** — TABULA building-stock reference data
  (``episcope-tabula.csv``), heating-system efficiency tables
  (``heater_efficiencies.csv``), and location-based heating reference
  temperatures (``heating_reference_temperature_per_location.csv``),
  processed from EPiScope sources.

* **loadprofiles/** — WHY-project household electricity and hot-water load
  profiles (``WHY_reference_data/``), plus LPG (Load Profile Generator)
  reference scalings and a predefined CHR01 household with bodily activity
  levels (``predefined_lpg_household_chr01/``).

* **photovoltaic/** — CEC and Sandia module and inverter databases, together
  with the selection logic in
  :mod:`hisim.inputs.photovoltaic.module_selection` that chooses a default
  Trina Solar PV module and a matching inverter.

* **costs_and_emissions/why_project/** — Device CAPEX/OPEX and emission-factor
  CSVs for both components and fuels, used by the post-processing cost and
  emission computations.

* **smart_devices/** — Parameterised smart-load appliance definitions in JSON
  format.

* **chp_system/** — CHP efficiency maps (Excel).

* **price_signal/** — Electricity purchase prices and feed-in tariff time
  series.

Component Configuration Files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The top-level ``hisim/inputs/`` directory also carries JSON configuration
files for specific component models:

* **electrolyzer_manufacturer_config.json** — manufacturer parameters for the
  electrolyzer component.
* **electrolyzer_polarization_curve_data.json** — polarisation curve data for
  electrolysers.
* **polarization_curve_data_fc.json** — polarisation curve data for fuel cells.
* **rSOC_efficiency_curve_data.json** — round-trip state-of-charge efficiency
  curves for battery storage.

Additional time-series data (e.g. ``wind_generated_power_1_min.csv``) is
stored alongside these configuration files.

Photovoltaic Module Selection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The :mod:`hisim.inputs.photovoltaic.module_selection` module reads the CEC
CSV databases at import time and applies deterministic filtering rules to
select a representative PV module and inverter pair. The selection functions
(:func:`~hisim.inputs.photovoltaic.module_selection.select_pv_module` and
:func:`~hisim.inputs.photovoltaic.module_selection.select_inverter`) are
exported for use in setup scripts and can also be exercised with synthetic
data in unit tests. The chosen module and inverter are available as the
module-level variables ``default_module`` and ``selected_inverter``.

API Reference
-------------

The ``hisim.inputs`` package itself exports no runtime API; it is a data
container. The photovoltaic selection logic is documented below.

.. automodule:: hisim.inputs.photovoltaic.module_selection
   :members:
   :undoc-members:
   :show-inheritance:
