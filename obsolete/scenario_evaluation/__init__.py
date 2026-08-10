"""Scenario comparison across multiple HiSim simulation runs.

This sub-package collects, aggregates, and visualizes the per-step result
data that HiSim writes under ``system_setups/results`` so that several
scenarios (e.g. different building codes or controller configurations) can
be compared against each other and against a default configuration.

Comparison methodology
----------------------

Scenarios are compared by collecting the aggregated output that each
simulation run writes under ``system_setups/results``, concatenating it into
a single pandas DataFrame keyed by the scenario parameters, and then
filtering and plotting that DataFrame per output variable.  Two processing
modes are supported (see
:class:`~hisim.postprocessing.scenario_evaluation.result_data_processing.ResultDataProcessingModeEnum`):

* ``PROCESS_ALL_DATA`` -- compare results across all varied parameters.
* ``PROCESS_FOR_DIFFERENT_BUILDING_CODES`` -- group and compare results by
  building code.

When a path to a default module configuration is supplied, each scenario's
configuration is read and compared against the default so that parameter
deviations can be identified and the default run is included alongside every
scenario for reference.

Aggregated metrics
------------------

The output variables compared across scenarios are selected through
:class:`~hisim.postprocessing.scenario_evaluation.result_data_processing.OutputVariableEnumClass`
and span two categories:

* **KPI data** -- scalar key-performance indicators per simulation run,
  such as total electricity consumption, PV production, energy exchanged
  with the grid, autarky rate, investment and operational costs, CO2
  emissions, conditioned floor area, specific heating demand, and heat-pump
  electricity use and flow/return temperatures.
* **Time-series data** -- per-step output from individual components,
  including electricity flows (PV, heat pumps, electric vehicles,
  occupancy), heating-system flow and return temperatures, and indoor-air
  temperature.

Data aggregation steps
----------------------

The pipeline runs in four stages:

1. **Collection** --
   :class:`~hisim.postprocessing.scenario_evaluation.result_data_collection.ResultDataCollection`
   scans the result folders, reads each scenario's aggregated CSV or Excel
   output, and concatenates everything into one DataFrame.  The data may be
   yearly or time-series (hourly, daily, monthly; see
   :class:`~hisim.postprocessing.scenario_evaluation.result_data_processing.ResultDataTypeEnum`).
2. **Processing** --
   :class:`~hisim.postprocessing.scenario_evaluation.result_data_processing.ScenarioDataProcessing`
   filters the aggregated DataFrame by output variable and scenario, renames
   scenarios for readability, and computes mean values across time steps for
   each scenario.
3. **Statistics and plotting** --
   :class:`~hisim.postprocessing.scenario_evaluation.result_data_plotting.ScenarioChartGeneration`
   computes descriptive statistics for each scenario (via pandas
   ``DataFrame.describe``), writes them to an Excel summary, and renders the
   comparison charts.  Yearly data yields bar, box, histogram, scatter, and
   stacked-bar plots; time-series data yields line and line-scatter plots.
4. **Configuration** --
   :class:`~hisim.postprocessing.scenario_evaluation.scenario_analysis_complete_with_config.ScenarioAnalysisWithConfig`
   drives the whole flow from a JSON configuration
   (:class:`~hisim.postprocessing.scenario_evaluation.scenario_analysis_complete_with_config.ScenarioAnalysisConfig`),
   wiring the collection, processing, and plotting stages together.

Submodules
----------

* :mod:`~hisim.postprocessing.scenario_evaluation.result_data_collection`
  (:class:`~hisim.postprocessing.scenario_evaluation.result_data_collection.ResultDataCollection`)
  scans the result folders, loads each scenario's aggregated output, and
  concatenates it into a single data set keyed by the scenario parameters.
* :mod:`~hisim.postprocessing.scenario_evaluation.result_data_processing`
  (:class:`~hisim.postprocessing.scenario_evaluation.result_data_processing.ScenarioDataProcessing`)
  filters and reshapes the aggregated data (by output variable, scenario,
  and processing mode) into the pandas structures the plotting layer
  consumes; also defines the
  :class:`~hisim.postprocessing.scenario_evaluation.result_data_processing.ResultDataTypeEnum`,
  :class:`~hisim.postprocessing.scenario_evaluation.result_data_processing.ResultDataProcessingModeEnum`,
  and
  :class:`~hisim.postprocessing.scenario_evaluation.result_data_processing.DataFormatEnum`
  enumerations.
* :mod:`~hisim.postprocessing.scenario_evaluation.result_data_plotting`
  (:class:`~hisim.postprocessing.scenario_evaluation.result_data_plotting.ScenarioChartGeneration`)
  computes per-scenario statistics and renders the comparison charts (line,
  bar, box, histogram, scatter, and stacked-bar) together with an Excel
  summary into a structured output directory.
* :mod:`~hisim.postprocessing.scenario_evaluation.scenario_analysis_complete_with_config`
  (:class:`~hisim.postprocessing.scenario_evaluation.scenario_analysis_complete_with_config.ScenarioAnalysisWithConfig`,
  :class:`~hisim.postprocessing.scenario_evaluation.scenario_analysis_complete_with_config.ScenarioAnalysisConfig`)
  drives the whole flow from a JSON configuration, wiring the collection,
  processing, and plotting stages together.
"""
