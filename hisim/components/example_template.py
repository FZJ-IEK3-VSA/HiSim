"""The ``template`` module.

It serves as a template for creating new component modules.
It shows with a simplified example which steps are necessary to create a new component.
Additionally it contains examples for doc strings according to the sphinx format.

Steps to build a component, in the order they appear below:

1. Write the configuration dataclass (:class:`ComponentNameConfig`). Every parameter of
   the device is a field of it.
2. For every parameter whose value is *not* the author's choice but follows from the
   surrounding system — the size of the building, the load it has to cover, the power of
   the device next to it — declare a **sizing law** at the field instead of writing a
   number: ``sized_field(rule=...)``, see ``rated_power_in_watt`` below. The law is an
   expression over ``Size.*`` terms, which are the *facts* the surrounding system
   provides (``hisim/config/context.py`` holds the whole vocabulary).
3. Write the factory / preset. A sized field is spelled :data:`~hisim.config.AUTO` there:
   the factory says "this one is computed", not what it computes to.
4. Where a contribution is declared: if the component is itself a *source* of facts — a
   weather file contributing its identity, a building contributing its heating load, a
   boiler contributing its power band — that goes into ``SIZING_CONTRIBUTIONS``. Shown as
   a comment block below, since the template models no real fact; ``weather.py`` holds a
   real one.
5. Write the component class: declare inputs and outputs, implement the lifecycle methods
   the ``Simulator`` calls, in the order they appear below:

   * ``i_prepare_simulation``: called once, before the first timestep. The base class
     raises rather than doing nothing, so a component that has nothing to prepare still
     has to define it — see the no-op below.
   * ``i_save_state``: caches the current state at the start of a timestep.
   * ``i_restore_state``: puts that cached state back at the start of every iteration.
   * ``i_doublecheck``: optional check once a timestep has converged.
   * ``i_simulate``: one iteration — read the inputs, write the outputs.

   By the time a config reaches the constructor the sizing kernel has already
   turned every ``AUTO`` into a number (``Component.__init__`` refuses anything else), so
   the component reads a sized field like any other value — through
   :func:`~hisim.config.concrete`, which says exactly that to the type checker.

Who runs the sizing: the energy-system executor resolves every config of a system against
the facts the system provides, before any component is constructed. A hand-written setup
or a test does the same explicitly with ``config.resolve(SizingContext(...))``.

"""

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from copy import deepcopy
from dataclasses import dataclass
from typing import Optional
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim.component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues
from hisim.config import AUTO, ConfigBase, ComponentID, DisplayConfig, Sizable, Size, concrete, sized_field
from hisim import loadtypes
from hisim.simulationparameters import SimulationParameters
from hisim.economics.facts import CostRelevance

__authors__ = "Tjarko Tjaden, Kai Rösken"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

#: Rated electrical power of this fictitious device per square metre of conditioned floor
#: area, in W/m². It is the constant half of the ``rated_power_in_watt`` sizing law below;
#: the other half is a fact about the surrounding system. A real component cites a source
#: for such a constant (a standard, a datasheet) in the field's ``note``; this one is
#: invented, because the template models no real device.
SPECIFIC_RATED_POWER_IN_WATT_PER_M2: float = 2.0


@dataclass_json
@dataclass
class ComponentNameConfig(ConfigBase):
    """Configuration of the ComponentName.

    A configuration holds two kinds of parameter, and the difference is the point of this
    class: ``loadtype`` and ``unit`` are the *author's* choices and are written as plain
    fields with plain values, while ``rated_power_in_watt`` *follows from the system the
    component is placed in* and is therefore declared with :func:`~hisim.config.sized_field`
    — a law at the field, evaluated once by the sizing kernel, instead of a number repeated
    in every setup that uses the component.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return ComponentName.get_full_classname()

    component_id: ComponentID
    loadtype: loadtypes.LoadTypes
    unit: loadtypes.Units
    #: How the sizing mechanism is declared, in one field:
    #:
    #: * ``Sizable[float]`` is the field's type — a float, or, until it is resolved, the
    #:   ``AUTO`` sentinel (or a per-preset law overriding the one declared here).
    #: * ``rule=`` is the **law**: an expression over ``Size.*`` terms. Each term is one
    #:   *fact* about the surrounding system; the complete vocabulary of facts, and the
    #:   ``SizingContext`` that carries them, live in ``hisim/config/context.py``. Laws
    #:   can be scaled, rounded (``.rounded(2)``), clamped (``.at_least(...)``), read a
    #:   sibling field (``Self("other_field")``) or be a plain function of the context.
    #:   Real ones: ``heat_distribution_system.py`` rounds a fact
    #:   (``Size.WATER_MASS_FLOW_RATE_IN_KG_PER_SECOND.rounded(2)``) and ``generic_boiler.py``
    #:   scales a sibling (``Self("maximal_thermal_power_in_watt") * (1 / 12)``); no component
    #:   clamps today, so ``.at_least(...)`` is shown by ``tests/test_sizing.py`` only.
    #: * ``value_type=`` names the concrete type the field's *wire* value is coerced into,
    #:   so a ``"2.0"`` written in a JSON or YAML file arrives as a float instead of a string.
    #: * ``note=`` records where the number comes from. ``hisim energy-system describe``
    #:   prints it under the field, so a hard-coded constant can cite its source. (The
    #:   audit written next to a run's results carries the law, the inputs and the value,
    #:   but not the note.)
    #:
    #: The facts themselves are provided by the other components of the system (see the
    #: note on ``SIZING_CONTRIBUTIONS`` below) and the value is computed by the executor,
    #: before any component is constructed.
    rated_power_in_watt: Sizable[float] = sized_field(
        rule=Size.CONDITIONED_FLOOR_AREA_IN_M2 * SPECIFIC_RATED_POWER_IN_WATT_PER_M2,
        value_type=float,
        note=(
            f"{SPECIFIC_RATED_POWER_IN_WATT_PER_M2} W per m² of conditioned floor area"
            " (invented: the template models no real device)"
        ),
    )

    @classmethod
    def get_default_template_component(
        cls,
        component_id: Optional[ComponentID] = None,
    ) -> "ComponentNameConfig":
        """Gets a default ComponentName."""
        if component_id is None:
            component_id = ComponentID(name="ComponentNameDefault")
        return ComponentNameConfig(
            component_id=component_id,
            loadtype=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
            # A sized field is spelled AUTO in a factory or a preset: the value is the
            # law's to compute, so the factory only says that it is not pinned here.
            # (It is also the field's declared default, so it could be omitted entirely;
            # it is written out once here because this file is read as a tutorial.)
            rated_power_in_watt=AUTO,
        )


# Step 4 -- contributing facts, the other half of the sizing mechanism.
#
# A component that is a *source* of facts declares that next to its config class:
#
#     ComponentNameConfig.SIZING_CONTRIBUTIONS = (
#         FactContribution(facts=("<fact name>",), compute=ComponentNameConfig.<method>),
#     )
#
# ``compute`` receives the (by then resolved) config and the context, and returns exactly
# the declared facts; the engine then hands them to whichever sibling config declares a law
# reading them. ``weather.py`` and ``loadprofilegenerator_utsp_connector.py`` hold the two
# smallest real examples, ``building/information.py`` the largest.
#
# The template declares no contribution of its own, because a contributed fact name must
# today be a field of ``SizingContext`` (``FactContribution.__post_init__`` refuses any
# other name) and inventing a template-only fact in that shared vocabulary would put a
# fact nothing reads into every energy system. A real component either contributes one of
# the existing facts or adds its fact to ``hisim/config/context.py`` together with its
# ``Size`` term.


class ComponentName(Component):
    """Example template component showing how to build a new HiSim component.

    This class is a simplified template demonstrating the steps required to
    create a new component module (config, inputs/outputs, state, simulate).
    It has no functional purpose in HiSim beyond serving as a reference.

    Attributes:
        InputFromOtherComponent: Name of the input field read from another component.
        OutputWithState: Name of the output field whose value is held in state.
        OutputWithoutState: Name of the stateless output field, a power in watts capped
            at the sized ``rated_power_in_watt``.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    InputFromOtherComponent: str = "InputFromState"

    # Outputs
    OutputWithState: str = "OutputWithState"
    OutputWithoutState: str = "OutputWithoutState"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ComponentNameConfig,
        my_display_config: Optional[DisplayConfig] = None,
    ) -> None:
        """Initialize the ComponentName template component.

        Args:
            my_simulation_parameters: Simulation parameters for the current run.
            config: :py:class:`ComponentNameConfig` providing name, loadtype and unit.
            my_display_config: Optional display configuration; defaults to a new
                :py:class:`DisplayConfig` when ``None``.
        """
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.componentnameconfig: ComponentNameConfig = config
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: ComponentNameConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # If a component requires states, this can be implemented here.
        self.state: "ComponentNameState" = ComponentNameState()
        self.previous_state: "ComponentNameState" = deepcopy(self.state)
        # Initialized variables
        self.factor_in_w: float = 1.0
        # A sized field is read like any other config value: by the time a config reaches
        # a component, ``Component.__init__`` has already refused every config that still
        # carried AUTO, so this is a number. ``concrete`` states that for the type checker.
        self.rated_power_in_watt: float = concrete(config.rated_power_in_watt)

        self.input_from_other_component: ComponentInput = self.add_input(
            object_name=self.componentnameconfig.component_id.name,
            field_name=self.InputFromOtherComponent,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
            mandatory=True,
        )

        self.output_with_state: ComponentOutput = self.add_output(
            object_name=self.componentnameconfig.component_id.name,
            field_name=self.OutputWithState,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT_HOUR,
            output_description="Output with State",
        )

        self.output_without_state: ComponentOutput = self.add_output(
            object_name=self.componentnameconfig.component_id.name,
            field_name=self.OutputWithoutState,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
            output_description="Output without State",
        )

    def i_prepare_simulation(self) -> None:
        """No-op: this template has nothing to prepare before the first timestep.

        The ``Simulator`` calls this once on every component, before the first timestep,
        and this is where a real component does the work that is done once rather than
        every step: open a data file and read the profile it drives, precompute a table
        the timesteps only look up, or read a fact another component wrote into the
        simulation repository (``self.simulation_repository``) while the system was built.

        A component with nothing to prepare still has to define the method. The base
        class, :meth:`hisim.component.Component.i_prepare_simulation`, raises
        ``NotImplementedError`` rather than doing nothing, so a component that leaves it
        out fails at the first thing a run does. (The one exception is
        :class:`hisim.component.StatelessComponent`, which carries a no-op override of its
        own.)
        """

    def i_save_state(self) -> None:
        """Saves the current state."""
        self.previous_state = ComponentNameState(output_with_state_in_wh=self.state.output_with_state_in_wh)

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = ComponentNameState(output_with_state_in_wh=self.previous_state.output_with_state_in_wh)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """No-op hook for optional post-simulation consistency checks.

        Args:
            timestep: Current simulation timestep index.
            stsv: Single-time-step values for the current timestep.
        """
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Compute outputs for the current timestep.

        Reads the external input and the previous in-state output, computes the
        two outputs (one accumulated into state, one stateless power capped at the
        sized rated power) and writes them back to ``stsv`` and to :py:attr:`state`.

        Args:
            timestep: Current simulation timestep index.
            stsv: Container to read inputs from and write outputs to.
            force_convergence: Whether to force convergence (unused in this template).
        """
        # define local variables
        input_1_in_w = stsv.get_input_value(self.input_from_other_component)
        input_2_in_wh = self.state.output_with_state_in_wh

        # do your calculations
        # NOTE: the stateful branch is dimensionally inconsistent with its declared unit:
        # input_1_in_w * seconds_per_timestep is W·s (J), not Wh. Suffixes follow the
        # declared channel units; this is a known template limitation.
        output_1_in_wh = input_2_in_wh + input_1_in_w * self.my_simulation_parameters.seconds_per_timestep
        # The sized field is used here like any other number: the device cannot deliver
        # more than the power it was sized for. Both sides of the min are watts, which is
        # why ``OutputWithoutState`` is declared in WATT.
        output_2_in_w = min(input_1_in_w + self.factor_in_w, self.rated_power_in_watt)

        # write values for output time series
        stsv.set_output_value(self.output_with_state, output_1_in_wh)
        stsv.set_output_value(self.output_without_state, output_2_in_w)

        # write values to state
        self.state.output_with_state_in_wh = output_1_in_wh


@dataclass
class ComponentNameState:
    """The data class saves the state of the simulation results.

    Parameters
    ----------
    output_with_state_in_wh : float
        Stores the accumulated energy (in Wh) of the ``OutputWithState``
        output channel from :py:class:`~hisim.component.ComponentName`.

    """

    output_with_state_in_wh: float = 0
