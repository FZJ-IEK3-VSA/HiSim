:orphan:

Architecture Overview
=====================

ETHOS.HiSim is structured around four architectural pillars: the **simulation
engine** (``hisim/simulator.py``), the **component model**
(``hisim/component.py``), the **component graph** (``hisim/component_wrapper.py``),
and the **per-timestep data flow** (``hisim/component.py``, class
:py:class:`~hisim.component.SingleTimeStepValues`).  Extension points are
exposed through the :py:class:`~hisim.component.Component` base class,
:py:class:`~hisim.dynamic_component.DynamicComponent` for components with
arbitrary I/O, and the postprocessing pipeline
(``hisim/postprocessing/``).

The sections below ground every claim on the current source.  Cross-references
link to the :doc:`modules/components` and :doc:`modules/postprocessing` pages.

Simulation Engine
-----------------

The :py:class:`~hisim.simulator.Simulator` class is the core orchestrator.
It owns the component registry (``wrapped_components``), the global output
list (``all_outputs``), and drives the main loop through
:meth:`~hisim.simulator.Simulator.run_all_timesteps`.

Key responsibilities of the engine:

1. **Result directory preparation** —
   :meth:`~hisim.simulator.Simulator.prepare_simulation_directory` creates the
   output directory and configures the logger.

2. **Component registration and wiring** —
   :meth:`~hisim.simulator.Simulator.connect_all_components` iterates over
   every wrapped component, runs automatic connection resolution when
   ``connect_automatically`` is set (see :ref:`component-graph`), and calls
   :meth:`~hisim.component_wrapper.ComponentWrapper.prepare_calculation` on
   each wrapper so that inputs are populated with their source outputs.

3. **Main timestep loop** —
   :meth:`~hisim.simulator.Simulator.run_all_timesteps` iterates over the
   configured number of timesteps (derived from the start/end dates and
   ``seconds_per_timestep`` in
   :py:class:`~hisim.simulationparameters.SimulationParameters`), calling
   :meth:`~hisim.simulator.Simulator.process_one_timestep` for each step.
   After every timestep the resulting values are appended to
   ``all_result_lines`` and later handed to the
   :py:class:`~hisim.postprocessing.postprocessing_main.PostProcessor`.

4. **Post-processing dispatch** — once all timesteps finish, the simulator
   instantiates a :py:class:`~hisim.postprocessing.postprocessing_main.PostProcessor`,
   passes a :py:class:`~hisim.postprocessing.postprocessing_datatransfer.PostProcessingDataTransfer`
   object containing the results dataframe, and invokes
   :meth:`~hisim.postprocessing.postprocessing_main.PostProcessor.run`.

Per-Timestep Data and Results Flow
-----------------------------------

The engine stores every scalar output in a flat list inside a
:py:class:`~hisim.component.SingleTimeStepValues` instance.  The list length
equals the number of registered :py:class:`~hisim.component.ComponentOutput`
objects across the entire simulation.

During :meth:`~hisim.simulator.Simulator.process_one_timestep`:

1. **State preservation** — each
   :py:class:`~hisim.component_wrapper.ComponentWrapper` calls
   ``save_state()`` on its wrapped component so that the converged state can
   be restored if oscillation occurs.

2. **Value cloning** — the previous converged
   :py:class:`~hisim.component.SingleTimeStepValues` is cloned into ``stsv``
   (current values) and ``previous_values`` (buffer).

3. **Iteration loop** — the engine loops over every wrapped component in
   registration order, calling ``restore_state()`` then
   :meth:`~hisim.component_wrapper.ComponentWrapper.calculate_component`,
   which in turn dispatches to
   :meth:`~hisim.component.Component.i_simulate` on the underlying component.

4. **Convergence check** — after all components have executed,
   :py:meth:`~hisim.component.SingleTimeStepValues.is_close_enough_to_previous`
   compares ``stsv`` against ``previous_values`` element-wise using a fixed
   threshold of ``0.0001``.  If all values are within this tolerance the loop
   exits; otherwise ``previous_values`` is updated and the loop continues.

5. **Anti-oscillation** — if more than 10 iterations elapse without
   convergence, ``force_convergence`` is set to ``True``.  Components honour
   this flag in their ``i_simulate`` implementations to clamp outputs to the
   last iterated value.

6. **Result collection** — the converged ``stsv.values`` list is appended to
   ``all_result_lines``.  After the final timestep the engine constructs a
   :py:class:`pandas.DataFrame` with one column per output and writes it into
   the :py:class:`~hisim.postprocessing.postprocessing_datatransfer.PostProcessingDataTransfer`.

.. _component-graph:

Component Graph
---------------

Components declare their data requirements and results through
:py:class:`~hisim.component.ComponentInput` and
:py:class:`~hisim.component.ComponentOutput`.  Each input and output carries
a :py:class:`~hisim.loadtypes.LoadTypes` tag and a
:py:class:`~hisim.loadtypes.Units` tag, which together determine whether two
signals are compatible.

**Connections** are described by :py:class:`~hisim.component.ComponentConnection`
objects, which link a source component's output field to a target component's
input field.  A component's ``default_connections`` dictionary maps source
class names to lists of these connections.

The :py:class:`~hisim.component_wrapper.ComponentWrapper` class mediates
between the simulator and the component model:

* :meth:`~hisim.component_wrapper.ComponentWrapper.register_component_outputs`
  registers each component's outputs into the global ``all_outputs`` list and
  assigns a ``global_index`` that serves as the column position in
  :py:class:`~hisim.component.SingleTimeStepValues`.

* :meth:`~hisim.component_wrapper.ComponentWrapper.connect_component` matches
  inputs to outputs by comparing ``src_object_name`` and ``src_field_name``.
  When ``connect_automatically`` is enabled, the simulator calls
  :meth:`~hisim.simulator.Simulator.connect_everything_automatically`, which
  resolves connections by looking up source class names in a target
  component's ``default_connections`` dictionary.

* :meth:`~hisim.component_wrapper.ComponentWrapper.prepare_calculation` walks
  over all inputs and resolves their ``source_output`` references so that
  :py:meth:`~hisim.component.SingleTimeStepValues.get_input_value` can fetch
  the correct value from the shared array.

**Unit validation** — during connection the wrapper verifies that input and
output units match (with ``Units.ANY`` acting as a wildcard that triggers a
warning rather than an error).  Mandatory inputs without a resolved source
raise a :py:class:`ValueError`.

For a complete inventory of available components and their categories, see the
:doc:`modules/components` page.

Extensibility Seams
-------------------

HiSim exposes several extension points for adding new components or custom
behaviour:

1. **New standard components** — subclass
   :py:class:`~hisim.component.Component`, implement the abstract methods
   (``get_config``, ``get_outputs``, ``get_inputs``, ``i_prepare_component``,
   ``i_simulate``, ``write_to_report``), and define ``default_connections`` if
   automatic wiring is desired.  See the
   :py:mod:`~hisim.components.example_component` module for a minimal template.

2. **Dynamic components** — subclass
   :py:class:`~hisim.dynamic_component.DynamicComponent` to declare an
   arbitrary number of inputs and outputs resolved at runtime by tag and
   weight matching (see :py:class:`~hisim.dynamic_component.DynamicConnectionInput`,
   :py:class:`~hisim.dynamic_component.DynamicConnectionOutput`, and
   :py:class:`~hisim.dynamic_component.DynamicComponentConnection`).  The
   :py:mod:`~hisim.components.controller_l2_energy_management_system`
   controller is a prominent example.

3. **Component state** — persist per-component state across iterations by
   implementing ``save_state`` and ``restore_state`` (the base class provides
   no-op defaults).  State is the mechanism that lets storage devices, for
   example, carry energy content from one iteration to the next.

4. **Postprocessing plugins** — extend the
   :py:class:`~hisim.postprocessing.postprocessing_main.PostProcessor` by
   adding new :py:class:`~hisim.postprocessingoptions.PostProcessingOptions`
   flags and corresponding analysis routines.  The
   :doc:`modules/postprocessing` page documents the existing sub-packages
   (charts, KPIs, cost/emission computation, scenario evaluation, CSV export,
   PDF report generation).

5. **JSON-based simulation** — the
   :py:mod:`~hisim.json_generator` module can serialise a running simulation
   into ``.scenario.json`` and ``.simulation.json`` files;
   :py:mod:`~hisim.json_executor` can reconstruct and replay it.  This
   provides a language-agnostic configuration seam for external tools.

Module Index
------------

* :py:mod:`hisim.simulator` — engine main loop and component orchestration
* :py:mod:`hisim.component` — component base class, I/O types, and
  :py:class:`~hisim.component.SingleTimeStepValues`
* :py:mod:`hisim.component_wrapper` — wrapper that mediates between simulator
  and components
* :py:mod:`hisim.dynamic_component` — dynamic I/O components with tag-based
  matching
* :py:mod:`hisim.loadtypes` — ``LoadTypes`` and ``Units`` enums that gate
  compatibility checks
* :py:mod:`hisim.simulationparameters` — ``SimulationParameters`` dataclass
  controlling time span, resolution, and options
* :py:mod:`hisim.postprocessingoptions` — bitmask flags for postprocessing
* :py:mod:`hisim.postprocessing.postprocessing_main` — postprocessing entry
  point

Detailed API documentation for every module is available via autodoc on the
:doc:`modules/components` and :doc:`modules/postprocessing` pages, as well as
the :doc:`simulator` and :doc:`component` pages.
