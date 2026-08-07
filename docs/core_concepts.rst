:orphan:

Core Simulation Concepts
========================

This page explains how the HiSim simulation engine advances through time,
resolves circular component dependencies, and wires data between components.
All behaviour described here is grounded in the source at
:py:mod:`hisim.simulator` and :py:mod:`hisim.component`.

For a catalogue of available components, see the :doc:`modules/components`
page.

Time-Step Iteration
-------------------

The :py:class:`~hisim.simulator.Simulator` drives the main loop via
:py:meth:`~hisim.simulator.Simulator.run_simulation`. For each of the
configurable number of timesteps it calls
:py:meth:`~hisim.simulator.Simulator.process_one_timestep`, which is
responsible for computing converged values for that single step before
moving on.

Within one timestep the simulator:

#. **Saves state** — every
   :py:class:`~hisim.component_wrapper.ComponentWrapper` delegates to
   the component's :py:meth:`~hisim.component.Component.i_save_state` so
   that the current (previously converged) state is preserved.

#. **Clones values** — the
   :py:class:`~hisim.component.SingleTimeStepValues` container from the
   prior step is cloned; a second clone serves as ``previous_values`` for
   the convergence check.

#. **Iterates until convergence** — a ``while`` loop repeatedly
   restores each component's state (via
   :py:meth:`~hisim.component.Component.i_restore_state`), then calls
   :py:meth:`~hisim.component.Component.i_simulate` on every wrapped
   component in order. After one full sweep the simulator compares
   current and previous values through
   :py:meth:`~hisim.component.SingleTimeStepValues.is_close_enough_to_previous`,
   which checks that *every* output differs by at most ``0.0001``.

#. **Forces convergence if needed** — after more than 10 iteration
   attempts ``force_convergence`` is set to ``True``, which causes
   ``i_simulate`` to skip its normal L2-signal evaluation and align
   outputs to the saved state instead. If more than 100 iterations are
   reached the simulation raises a ``ValueError`` with a list of the
   still-changing outputs.

#. **Commits the result** — the converged values are appended to the
   global result buffer and become the ``previous_stsv`` for the next
   timestep.

SingleTimeStepValues Flow
-------------------------

:py:class:`~hisim.component.SingleTimeStepValues` (``stsv``) is the sole
data container that carries all input and output values during one
timestep. It is a flat list of floats indexed by each
:py:class:`~hisim.component.ComponentOutput`'s ``global_index``.

During a simulation sweep:

* **Reading inputs** — inside
  :py:meth:`~hisim.component.Component.i_simulate` a component reads
  values through its
  :py:class:`~hisim.component.ComponentInput` objects. Each input
  resolves to ``stsv.values[source_output.global_index]`` via
  :py:meth:`~hisim.component.SingleTimeStepValues.get_input_value`.

* **Writing outputs** — the component writes results back through its
  :py:class:`~hisim.component.ComponentOutput` objects by calling
  :py:meth:`~hisim.component.SingleTimeStepValues.set_output_value`,
  which assigns ``stsv.values[output.global_index] = value``.

Because the STSV is shared across *all* components in a single sweep,
changes written by one component are immediately visible to the next
component in the ordering. This is the mechanism that allows iterative
convergence on circular dependencies.

Component Ordering
------------------

Components are stored in the simulator's
:py:attr:`~hisim.simulator.Simulator.wrapped_components` list, which
determines the evaluation order within each sweep. The order is the
order in which components were instantiated in the setup function.

Key properties:

* **Downstream visibility** — because the STSV is updated in-place, a
  component evaluated later in the list sees the most recent outputs of
  earlier components. Placing a component *after* its upstream sources
  reduces the number of iterations needed for convergence.

* **Circular dependencies** — when two or more components depend on each
  other (e.g. a building and a heat pump), no single ordering eliminates
  the dependency. The convergence loop resolves these by iterating until
  all outputs stabilise, regardless of order.

* **No automatic topological sort** — the simulator does *not* reorder
  components. The responsibility lies with the model author to provide a
  sensible instantiation order that minimises unnecessary iterations.

For details on how individual components declare their inputs, outputs,
and default connections, see the :doc:`modules/components` reference.
