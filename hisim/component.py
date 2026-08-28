"""Defines the component class and helpers.

The component class is the base class for all other components.

The configuration base classes this module used to define — ``ComponentID``,
``ConfigBase`` and ``DisplayConfig`` — now live one layer below, in the
:mod:`hisim.config` package, and are used here through the ``cfg`` module alias. No
compatibility alias is left behind on purpose: ``hisim.component`` is the component
*runtime*, and code that needs a configuration base class imports it from
``hisim.config`` directly, which makes the layering visible at every call site.
"""

# clean

from __future__ import annotations
import os
import dataclasses as dc
import typing
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Optional
import json
import pandas as pd

from hisim import config as cfg
from hisim import loadtypes as lt
from hisim import log
from hisim.sim_repository import SimRepository
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass

# Package


@dataclass
class ComponentConnection:
    """Used in the component class for defining a connection."""

    target_input_name: str
    source_class_name: str
    source_output_name: str
    source_instance_name: Optional[str] = None


class ComponentOutput:  # noqa: too-few-public-methods
    """Used in the component class for defining an output."""

    def __init__(
        self,
        object_name: str,
        field_name: str,
        load_type: lt.LoadTypes,
        unit: lt.Units,
        postprocessing_flag: Optional[List[Any]] = None,
        sankey_flow_direction: Optional[bool] = None,
        output_description: Optional[str] = None,
        source_component_class: Optional[str] = None,
        *,
        component_id: cfg.ComponentID,
    ):
        """Defines a component output.

        Besides the display and load-type metadata, an output carries the structured identity
        of the component that produced it. Postprocessing needs to know which building object
        an output belongs to, and it must not obtain that by taking the runtime name apart, so
        the owning :py:class:`~hisim.config.ComponentID` is stored alongside the derived ``component_name``.
        The identity is deliberately REQUIRED (keyword-only): an output without an owner would
        silently fall into the default building group and corrupt per-building KPIs, so a
        missing identity must fail at construction rather than at aggregation.
        """
        self.full_name: str = object_name + " # " + field_name
        self.component_name: str = object_name
        self.field_name: str = field_name
        self.display_name: str = field_name
        self.load_type: lt.LoadTypes = load_type
        self.unit: lt.Units = unit
        self.global_index: int = -1
        self.postprocessing_flag: Optional[List[Any]] = postprocessing_flag
        self.sankey_flow_direction: Optional[bool] = sankey_flow_direction
        self.output_description: Optional[str] = output_description
        self.source_component_class: Optional[str] = source_component_class
        self.component_id: cfg.ComponentID = component_id

    @property
    def building_label(self) -> str:
        """Returns the building object this output is grouped under in postprocessing.

        Delegates to the owning component's identity: the component's building when one is
        set, and the default single-building label otherwise. Identity is mandatory on every
        output, so there is no fallback path for ownerless outputs.
        """
        return self.component_id.building_label

    def get_pretty_name(self) -> str:
        """Gets a pretty name for a component output."""
        return self.component_name + " - " + self.display_name + " [" + self.load_type + " - " + self.unit + "]"


class ComponentInput:  # noqa: too-few-public-methods
    """Used in the component class for defining an input."""

    def __init__(
        self,
        object_name: str,
        field_name: str,
        load_type: lt.LoadTypes,
        unit: lt.Units,
        mandatory: bool,
        allow_unconnected_mandatory: bool = False,
    ):
        """Initializes a component input.

        Args:
            object_name: Name of the component that owns this input.
            field_name: Name of the input field.
            load_type: Physical load type of the input.
            unit: Unit of the input.
            mandatory: Whether this input must be connected to a source output.
            allow_unconnected_mandatory: When True and the input is
                mandatory, :meth:`ComponentWrapper.connect_inputs` logs a
                warning instead of raising if no matching output is found. Use
                this for mandatory inputs whose source output may legitimately
                not exist (e.g. a heat pump's DHW electrical power output when
                domestic hot water preparation is disabled).
        """
        self.fullname: str = object_name + " # " + field_name
        self.component_name: str = object_name
        self.field_name: str = field_name
        self.loadtype: lt.LoadTypes = load_type
        self.unit: lt.Units = unit
        self.global_index: int = -1
        self.src_object_name: Optional[str] = None
        self.src_field_name: Optional[str] = None
        self.source_output: Optional[ComponentOutput] = None
        self.is_mandatory = mandatory
        self.allow_unconnected_mandatory: bool = allow_unconnected_mandatory


class SingleTimeStepValues:
    """Contains the values for a single time step."""

    def __init__(self, number_of_values: int):
        """Initializes a new single time step values class."""
        self.values = [0.0] * number_of_values

    def copy_values_from_other(self, other):
        """Copy all values from a single time step values."""
        self.values = other.values[:]

    def clone(self):
        """Makes a copy of the current object."""
        newstsv = SingleTimeStepValues(len(self.values))
        newstsv.values = self.values[:]
        return newstsv

    def get_input_value(self, component_input: ComponentInput) -> float:
        """Gets a value for an input from the single time step values."""
        if component_input.source_output is None:
            return 0
        return self.values[component_input.source_output.global_index]

    def set_output_value(self, output: ComponentOutput, value: float) -> None:
        """Sets a single output value in the single time step values array."""
        self.values[output.global_index] = value

    def is_close_enough_to_previous(self, previous_values: "SingleTimeStepValues") -> bool:
        """Checks if the values are sufficiently similar to another array."""
        count = len(self.values)
        for i in range(count):
            if abs(previous_values.values[i] - self.values[i]) > 0.0001:
                return False
        return True

    def get_differences_for_error_msg(self, previous_values: Any, outputs: List[ComponentOutput]) -> str:
        """Gets a pretty error message for the differences between two time steps."""
        count = len(self.values)
        error_msg = ""
        for i in range(count):
            if abs(previous_values.values[i] - self.values[i]) > 0.0001:
                error_msg += (
                    outputs[i].get_pretty_name()
                    + " previously: "
                    + f"{previous_values.values[i]:4.2f}"
                    + " currently: "
                    + f"{self.values[i]:4.2f}"
                    + " | "
                )
        return error_msg


class Component:
    """Base class for all components."""

    @classmethod
    def get_classname(cls):
        """Gets the class name. Helper function for default connections."""
        return cls.__name__

    @classmethod
    def get_full_classname(cls) -> str:
        """Gets the class name. Helper function for default connections."""
        return cls.__module__ + "." + cls.__name__

    def __init__(
        self,
        name: str,
        my_simulation_parameters: SimulationParameters,
        my_config: cfg.ConfigBase,
        my_display_config: cfg.DisplayConfig,
    ) -> None:
        """Initializes the component class.

        Args:
            name: The unique runtime name of this component, normally
                ``config.component_id.key``. It becomes the prefix of every output name and
                therefore of every result column, and it is the key a declarative
                energy-system file addresses this component by, so it has to be a plain
                identifier.
            my_simulation_parameters: The simulation-wide parameters (time range, resolution,
                post-processing options) the component reads and is stepped with.
            my_config: The component's own configuration; it must be a
                :class:`~hisim.config.ConfigBase` and must no longer carry any unresolved
                ``AUTO`` field.
            my_display_config: How this component is presented in postprocessing, the report
                and the webtool.

        Raises:
            ValueError: If ``name`` is not a usable identifier, if ``my_simulation_parameters``
                is ``None``, or if ``my_config`` is not a ``ConfigBase``.
            ConfigSizingError: If ``my_config`` still has fields awaiting sizing.
        """
        # The single choke point where a component's runtime name becomes real. Enforcing the
        # identifier rule here catches a name typed in a setup and a name that arrived from a
        # config class's own default alike, which a check on the declarative file's keys would
        # never see: a defaulted identity is one nobody has to write down.
        cfg.NameSyntax.require_identifier(name, "component")
        self.component_name: str = name
        self.inputs: List[ComponentInput] = []
        self.outputs: List[ComponentOutput] = []
        self.outputs_initialized: bool = False
        self.inputs_initialized: bool = False
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        if my_simulation_parameters is None:
            raise ValueError("My Simulation parameters was None.")
        self.simulation_repository: SimRepository
        # self.singleton_simulation_repository: SingletonSimRepository
        self.default_connections: Dict[str, List[ComponentConnection]] = {}
        if isinstance(my_config, cfg.ConfigBase):
            # The central sizing check: a config that still
            # carries the AUTO sentinel anywhere must never reach a running component, no
            # matter whether it came from a preset, a scenario file or manual construction.
            # The error prints each unresolved field with its declared law, so the fix
            # (call .resolve(ctx) or assign a concrete value) is obvious from the message.
            unresolved = cfg.auto_fields(my_config)
            if unresolved:
                raise cfg.ConfigSizingError(
                    f"The config of component '{my_config.component_id.key}' "
                    f"({type(my_config).__name__}) still requires sizing in "
                    f"{len(unresolved)} field(s):\n{cfg.describe_auto_fields(my_config)}\n"
                    "Call .resolve(ctx) with a SizingContext or set the fields explicitly."
                )
            # Subclasses read their concrete config's fields off this base-typed slot; that
            # works for the type checker because ConfigBase carries a checking-only
            # __getattr__/__setattr__ escape hatch (see there).
            self.config: cfg.ConfigBase = my_config
        else:
            raise ValueError(
                "The argument my_config is not a ConfigBase object.",
                "Please check your components' configuration classes and inherit from ConfigBase class according to hisim/components/example_component.py.",
            )
        self.my_display_config: cfg.DisplayConfig = my_display_config
        self.log_connections: List[Any] = []
        self.enable_logging = my_simulation_parameters.log_connections

    @property
    def component_id(self) -> cfg.ComponentID:
        """Returns the structured identity of this component.

        This is a convenience shortcut for ``self.config.component_id`` so that component code
        and postprocessing can reach the building, the unit and the plain name of a component
        without going through the configuration object every time. The identity is owned by
        the configuration, which stays the single source of truth.
        """
        return self.config.component_id

    def get_component_name(self) -> str:
        """Creates the unique runtime name of this component.

        The name is derived purely from the structured identity on the configuration, i.e. it
        is :py:attr:`~hisim.config.ComponentID.key`. Unlike the previous implementation this no longer
        consults the ``multiple_buildings`` simulation parameter: whether a building appears in
        the name is decided by whether the configuration carries a building at all, which makes
        the runtime name a property of the component itself rather than of a global flag.
        """
        return self.config.component_id.key

    def add_default_connections(self, connections: List[ComponentConnection]) -> None:
        """Adds a default connection list definition."""

        if not connections:
            raise ValueError(
                f"connections list is empty for component {self.component_name}. "
                "Cannot add default connections from an empty list."
            )
        component_name = connections[0].source_class_name
        for connection in connections:
            if connection.source_class_name != component_name:
                raise ValueError("Trying to add connections to different components in one go.")
        self.default_connections[component_name] = connections
        log.trace(
            "added default connections for connections from : " + component_name + "\n" + str(self.default_connections)
        )

    def i_prepare_simulation(self) -> None:
        """Gets called before the simulation to prepare the calculation."""
        raise NotImplementedError(
            "Simulation preparation is missing for " + self.component_name + " (" + self.get_full_classname() + ")"
        )

    def set_sim_repo(self, simulation_repository: SimRepository) -> None:
        """Sets the SimRepository."""
        if simulation_repository is None:
            raise ValueError("simulation repository was none")
        self.simulation_repository = simulation_repository

    def add_input(
        self,
        object_name: str,
        field_name: str,
        load_type: lt.LoadTypes,
        unit: lt.Units,
        mandatory: bool,
    ) -> ComponentInput:
        """Adds an input definition."""
        myinput = ComponentInput(object_name, field_name, load_type, unit, mandatory)
        self.inputs.append(myinput)
        return myinput

    def add_output(
        self,
        object_name: str,
        field_name: str,
        load_type: lt.LoadTypes,
        unit: lt.Units,
        postprocessing_flag: Optional[List[Any]] = None,
        sankey_flow_direction: Optional[bool] = None,
        output_description: Optional[str] = None,
    ) -> ComponentOutput:
        """Adds an output definition."""
        if output_description is None:
            raise ValueError("Missing an output description for " + object_name + " - " + field_name)
        log.debug("adding output: " + field_name + " to component " + object_name)
        outp = ComponentOutput(
            object_name,
            field_name,
            load_type,
            unit,
            postprocessing_flag,
            sankey_flow_direction,
            output_description,
            component_id=self.config.component_id,
        )
        self.outputs.append(outp)
        return outp

    def connect_input(self, input_fieldname: str, src_object_name: str, src_field_name: str) -> None:
        """Connecting an input to an output."""
        if len(self.inputs) == 0:
            raise ValueError("The component " + self.component_name + " has no inputs.")
        if self.enable_logging:
            self.log_connections += [{
                "input_fieldname": input_fieldname,
                "src_object_name": src_object_name,
                "src_field_name": src_field_name,
            }]

        component_input: ComponentInput
        input_to_set = None
        for component_input in self.inputs:
            if component_input.field_name == input_fieldname:
                if input_to_set is not None:
                    raise ValueError(
                        "The input "
                        + input_fieldname
                        + " of the component "
                        + self.component_name
                        + " was already set."
                    )
                input_to_set = component_input
        if input_to_set is None:
            raise ValueError("The component " + self.component_name + " has no input with the name " + input_fieldname)
        input_to_set.src_object_name = src_object_name
        input_to_set.src_field_name = src_field_name

        if self.enable_logging:
            # write input and output connection to json file
            file_name = os.path.join(self.my_simulation_parameters.result_directory, "component_connections.json")

            dict_with_connection_information = {
                "From": {"Component": input_to_set.src_object_name, "Field": input_to_set.src_field_name},
                "To": {"Component": input_to_set.component_name, "Field": input_to_set.field_name},
            }

            try:
                # Validate that result_directory exists, create if it doesn't
                if not os.path.exists(self.my_simulation_parameters.result_directory):
                    os.makedirs(self.my_simulation_parameters.result_directory, exist_ok=True)

                if os.path.exists(file_name):
                    with open(file_name, mode="r+", encoding="utf-8") as file:
                        file.seek(os.stat(file_name).st_size - 1)
                        file.write(f",{json.dumps(dict_with_connection_information)}]")
                else:
                    with open(file_name, "a", encoding="utf-8") as file:
                        json.dump([dict_with_connection_information], file)
            except (OSError, IOError) as e:
                # Log warning instead of crashing
                log.warning(
                    f"Failed to write component connections to {file_name}: {e}. "
                    "Component connections will not be logged to file."
                )

    def connect_dynamic_input(self, input_fieldname: str, src_object: ComponentOutput) -> None:
        """For connecting an input to a dynamic output."""
        src_object_name = src_object.component_name
        src_field_name = src_object.field_name
        self.connect_input(
            input_fieldname=input_fieldname,
            src_object_name=src_object_name,
            src_field_name=src_field_name,
        )

    # added variable input length and loop to be able to set default connections in one line in system_setups
    def connect_only_predefined_connections(self, *source_components):
        """Wrapper for default connections and connect with connections list."""
        for source_component in source_components:
            connections = self.get_default_connections(source_component)
            self.connect_with_connections_list(connections)

    def connect_with_connections_list(self, connections: List[ComponentConnection]) -> None:
        """Connect all inputs based on a connections list."""
        for connection in connections:
            src_name: str = typing.cast(str, connection.source_instance_name)
            self.connect_input(connection.target_input_name, src_name, connection.source_output_name)

    def get_default_connections(self, source_component: Component) -> List[ComponentConnection]:
        """Gets the default connections for this component."""
        source_classname: str = source_component.get_classname()
        target_classname: str = self.get_classname()

        if source_classname not in self.default_connections:
            raise ValueError(
                "No default connections for "
                + source_classname
                + " in the connections for "
                + target_classname
                + ". content:\n"
                + str(self.default_connections)
            )
        connections = self.default_connections[source_classname]
        new_connections: List[ComponentConnection] = []
        for connection in connections:
            connection_copy = dc.replace(connection)
            connection_copy.source_instance_name = source_component.component_name
            new_connections.append(connection_copy)
        return new_connections

    def get_input_definitions(self) -> List[ComponentInput]:
        """Gets the input definitions."""
        return self.inputs

    def get_outputs(self) -> List[ComponentOutput]:
        """Delivers a list of outputs."""
        if len(self.outputs) == 0:
            raise ValueError("Error: Component " + self.component_name + " has no outputs defined")
        return self.outputs

    #: Set on a component that models no physical device -- a signal generator, a summation
    #: node, a demonstration transformer. Such a component has nothing to buy, nothing to run
    #: and no indicators of its own, so the three methods below answer for it instead of
    #: refusing. Never set it on a component whose cost or KPI model has simply not been
    #: written: the default is to refuse, so a real device that nobody has modelled stops the
    #: run rather than being summed into the totals as zero and read as an answer.
    #: That distinction is the whole point of the flag. The default below still raises, so a device
    #: with real costs that nobody has modelled fails the run loudly instead of being summed into
    #: the system total as zero, which would understate it silently and look like an answer.
    MODELS_NO_DEVICE: ClassVar[bool] = False

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculates operational cost, operational co2 footprint and consumption in kWh (for Diesel in l) during simulation time frame.

        Raises unless the component has declared :attr:`MODELS_NO_DEVICE`. Half the component
        library does not implement this method, so enabling COMPUTE_OPEX used to be safe only for a
        system built entirely from the half that does -- and the failure named the component without
        saying whether its costs were nil or merely unwritten. A component that has none now says so
        and returns zeros; everything else still stops the run, which is the right outcome for a
        cost that exists and is missing.
        """
        if self.MODELS_NO_DEVICE:
            return OpexCostDataClass.get_default_opex_cost_data_class()
        raise NotImplementedError(f"{self.component_name} has no opex costs implemented.")

    @staticmethod
    def get_cost_capex(config: cfg.ConfigBase, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        # pylint: disable=unused-argument
        """Calculates lifetime, total capital expenditure cost and total co2 footprint of production of device."""
        raise NotImplementedError(f"{config.get_main_classname()} has no capex costs implemented.")

    def get_component_kpi_entries(
        self,
        all_outputs: List,  # pylint: disable=unused-argument
        postprocessing_results: pd.DataFrame,  # pylint: disable=unused-argument
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list.

        Refuses unless the component has declared :attr:`MODELS_NO_DEVICE`, in which case it has no
        indicators of its own and contributes none. The comment this replaces said the empty list
        was the intent for an unimplemented method, and the code beneath it raised -- the two had
        disagreed long enough that setups built from indicator-less components could not compute
        KPIs at all.
        """
        if self.MODELS_NO_DEVICE:
            return []
        raise NotImplementedError(f"{self.component_name} has no kpis implemented.")

    def capital_cost_data(
        self, simulation_parameters: Optional[SimulationParameters] = None
    ) -> CapexCostDataClass:
        """Return this component's capital cost, or zeros when it models no device.

        Callers use this rather than :meth:`get_cost_capex` directly, because the flag that says a
        component has no costs cannot be read from inside that method: it is a ``staticmethod`` that
        receives only the configuration, and some thirty components override it as one. Changing
        that signature to let the base class see the class attribute would mean touching every one
        of those overrides for the sake of the handful that declare no costs, so the check lives
        here, on the instance, where the flag is in scope.

        Args:
            simulation_parameters: the parameters to cost against; the component's own when omitted.

        Returns:
            CapexCostDataClass: the component's capital cost, or a zeroed instance.
        """
        if self.MODELS_NO_DEVICE:
            return CapexCostDataClass.get_default_capex_cost_data_class()
        return self.get_cost_capex(
            config=self.config,
            simulation_parameters=simulation_parameters or self.my_simulation_parameters,
        )

    def calc_maintenance_cost(self) -> float:
        """Calc maintenance_cost per simulated period as share of capex of component."""

        maintenance_cost_per_simulated_period_in_euro = (
            self.capital_cost_data().maintenance_cost_per_simulated_period_in_euro
        )

        return maintenance_cost_per_simulated_period_in_euro

    def i_save_state(self) -> None:
        """Abstract. Gets called at the beginning of a timestep to save the state."""
        raise NotImplementedError()

    def i_restore_state(self) -> None:
        """Abstract. Restores the state of the component. Can be called many times while iterating."""
        raise NotImplementedError()

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Performs the actual calculation."""
        raise NotImplementedError()

    def write_to_report(self) -> Any:
        """Abstract function for writing the report entry for this component."""
        raise NotImplementedError("In " + self.component_name)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Abstract. Gets called after the iterations are finished at each time step for potential debugging purposes."""
        pass  # noqa


class StatelessComponent(Component):
    """Base class for components that hold no mutable per-timestep state.

    A :class:`StatelessComponent` has no internal state that changes across the
    save/restore cycle of the convergence loop, so the state-management hooks
    :meth:`Component.i_save_state` and :meth:`Component.i_restore_state` are
    no-ops.  Subclasses that introduce mutable state should inherit from
    :class:`Component` directly and implement those hooks explicitly.

    Because stateless components also typically have nothing to precompute,
    :meth:`Component.i_prepare_simulation` is overridden with a no-op default
    here; a subclass that needs pre-simulation setup should override it.  The
    optional :meth:`Component.i_doublecheck` hook already has a no-op default
    in :class:`Component` and is inherited unchanged.
    """

    def i_save_state(self) -> None:
        """No-op: a stateless component has no internal state to cache."""

    def i_restore_state(self) -> None:
        """No-op: a stateless component has no internal state to restore."""

    def i_prepare_simulation(self) -> None:
        """No-op: a stateless component performs no pre-simulation setup by default."""


@dataclass
class OpexCostDataClass:
    """Return element of type OpexCostDataClass in function get_cost_opex from Component."""

    opex_energy_cost_in_euro: float
    opex_maintenance_cost_in_euro: float
    co2_footprint_in_kg: float
    total_consumption_in_kwh: float
    loadtype: lt.LoadTypes
    consumption_for_space_heating_in_kwh: float = 0.0
    consumption_for_domestic_hot_water_in_kwh: float = 0.0
    kpi_tag: Optional[KpiTagEnumClass] = None

    @classmethod
    def get_default_opex_cost_data_class(cls) -> OpexCostDataClass:
        """Return the Default for all Components without Opex Costs."""
        return OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=0,
            consumption_for_space_heating_in_kwh=0,
            consumption_for_domestic_hot_water_in_kwh=0,
            loadtype=lt.LoadTypes.ANY,
            kpi_tag=None,
        )


@dataclass
class CapexCostDataClass:
    """Return element of type CapexCostDataClass in function get_capex_cost from Component."""

    capex_investment_cost_in_euro: float
    device_co2_footprint_in_kg: float
    lifetime_in_years: float
    capex_investment_cost_for_simulated_period_in_euro: float
    device_co2_footprint_for_simulated_period_in_kg: float
    maintenance_costs_in_euro: float = 0.0
    maintenance_cost_per_simulated_period_in_euro: float = 0.0
    subsidy_as_percentage_of_investment_costs: float = 0.0
    kpi_tag: Optional[KpiTagEnumClass] = None

    @classmethod
    def get_default_capex_cost_data_class(cls) -> CapexCostDataClass:
        """Return the Default for all Components without Capex Costs."""
        return CapexCostDataClass(
            capex_investment_cost_in_euro=0,
            device_co2_footprint_in_kg=0,
            lifetime_in_years=1,
            capex_investment_cost_for_simulated_period_in_euro=0,
            device_co2_footprint_for_simulated_period_in_kg=0,
            maintenance_costs_in_euro=0,
            maintenance_cost_per_simulated_period_in_euro=0,
            subsidy_as_percentage_of_investment_costs=0,
            kpi_tag=None,
        )


@dataclass
class Coordinates:
    """Geographic coordinates of a location.

    Both fields are angular positions expressed in decimal degrees.
    """

    latitude_in_degrees: float
    longitude_in_degrees: float
