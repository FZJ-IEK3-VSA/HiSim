"""Post-processing pipeline for finished HiSim simulations.

This package transforms the data collected during a HiSim simulation run
into analysis artefacts: charts, key performance indicators (KPIs), cost
and emission summaries, CSV/Pickle exports, and a structured PDF report.
It is executed once the :mod:`hisim.simulator` has completed every time
step and converged all component outputs.  Results from multiple runs
can be compared and visualised with the
:mod:`hisim.postprocessing.scenario_evaluation` sub-package.

Inputs
------
The pipeline consumes a
:class:`~hisim.postprocessing.postprocessing_datatransfer.PostProcessingDataTransfer`
object that bundles the simulation results (time-series outputs, component
metadata, and global parameters) together with the active
:class:`~hisim.postprocessingoptions.PostProcessingOptions` flags that
select which artefacts to produce. The simulator assembles this object and
hands it to the post-processor.

Outputs
-------
Depending on the enabled options the package writes the following artefacts
into the results directory:

* **Charts** -- line plots, carpet plots, single-day plots, and monthly bar
  charts (:mod:`hisim.postprocessing.charts`,
  :mod:`hisim.postprocessing.chart_singleday`).
* **System flow charts** -- component network diagrams
  (:mod:`hisim.postprocessing.system_chart`).
* **KPIs** -- consumption, production, self-consumption, and
  self-sufficiency indicators
  (:mod:`hisim.postprocessing.kpi_computation`).
* **Costs and emissions** -- CAPEX and OPEX summaries
  (:mod:`hisim.postprocessing.cost_and_emission_computation`).
* **CSV / Pickle export** -- raw time-series data and housing-database
  exports (:mod:`hisim.postprocessing.generate_csv_for_housing_database`).
* **PDF report** -- a structured report assembling charts and KPI tables
  (:mod:`hisim.postprocessing.reportgenerator`).

Invocation
----------
Post-processing is normally triggered by the simulator after the time-step
loop completes. The entry point is the
:class:`~hisim.postprocessing.postprocessing_main.PostProcessor` class::

    from hisim.postprocessing import postprocessing_main as pp

    my_post_processor = pp.PostProcessor()
    my_post_processor.run(ppdt=postprocessing_datatransfer, simulator=simulator)

The :meth:`~hisim.postprocessing.postprocessing_main.PostProcessor.run`
method iterates over the enabled option flags and dispatches each one to
the corresponding sub-routine.
"""
