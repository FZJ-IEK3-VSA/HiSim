"""Defines the component class and helpers.

The component class is the base class for all other components.
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
from dataclasses_json import dataclass_json
from dataclass_wizard import JSONWizard

from hisim import loadtypes as lt
from hisim import log
from hisim.sim_repository import SimRepository
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass

# Package


@dataclass_json
@dataclass(frozen=True)
class ComponentID:
    """Structured, first-class identity of one component instance in a simulation.

    A component used to be identified by a loose pair of strings on its configuration
    (``name`` plus ``building_name``) that was collapsed into a runtime name by
    ``Component.get_component_name()`` depending on the ``multiple_buildings`` simulation
    parameter. ``ComponentID`` replaces that arrangement with a single immutable value
    object: ``name`` says what the component is, ``building`` says which building object it
    belongs to (``None`` for a plain single-building simulation), and ``unit`` says which
    sub-unit of that building (for example an apartment) owns it. Only ``name`` is required;
    the optional fields are simply absent when the surrounding simulation has no need for
    them.

    The unique runtime identifier is derived by the :py:attr:`key` property, which joins the
    fields that are actually present with underscores in the order *building*, *unit*,
    *name*. Absent fields contribute nothing at all, so ``ComponentID("Weather").key`` is
    ``"Weather"``, ``ComponentID("Weather", building="BUI1").key`` is ``"BUI1_Weather"`` and
    ``ComponentID("HeatPump", building="BUI1", unit="APT2").key`` is
    ``"BUI1_APT2_HeatPump"``. The key is derived-only: it is written into component names,
    output names and result columns, but it is never parsed back into its parts anywhere in
    HiSim. Code that needs to know the building or the unit of a component reads the
    structured fields instead.

    Because the key merely concatenates the present fields, two different tuples can in
    principle produce the same key (for example ``ComponentID("Pump", building="BUI1_APT2")``
    and ``ComponentID("Pump", building="BUI1", unit="APT2")``). No new mechanism is needed to
    catch that: such a collision shows up as two components claiming the same runtime name,
    and the existing duplicate-component-name check performed when outputs are registered
    with the simulator (see ``Simulator.add_component``) raises on it just as it does for any
    other accidental name clash.

    The class is frozen, so instances are hashable and safe to share between a configuration
    and everything derived from it; use :py:func:`dataclasses.replace` to obtain a variant.
    """

    name: str
    building: Optional[str] = None
    unit: Optional[str] = None

    #: Building label used for grouping when a component carries no explicit building.
    #: Historically every configuration defaulted to the decorative string ``"BUI1"``, and
    #: postprocessing (KPI collection, OPEX/CAPEX tables, the webtool result JSON) keys its
    #: per-building groups by that string. Keeping the same label for building-less
    #: components means the grouping output of a single-building simulation is byte-for-byte
    #: what it was before ``ComponentID`` existed.
    DEFAULT_BUILDING_LABEL: ClassVar[str] = "BUI1"

    def __post_init__(self) -> None:
        """Validates the identity right after construction.

        The name is the only mandatory part of a component identity, and an empty or
        whitespace-only name would silently produce an empty or malformed key later on.
        Rejecting it here turns a confusing downstream naming problem into an immediate,
        clearly attributable error.
        """
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"A ComponentID needs a non-empty name, but got {self.name!r}.")

    @property
    def key(self) -> str:
        """Derives the unique runtime identifier of this component.

        The key is the string that HiSim uses as the component name, as the prefix of every
        output's full name and therefore as the prefix of every result column. It is built by
        joining the present fields with ``"_"`` in the order building, unit, name; fields that
        are ``None`` simply do not appear. The key is derived-only and is never parsed back
        into building/unit/name anywhere in the code base.
        """
        parts = [part for part in (self.building, self.unit, self.name) if part is not None]
        return "_".join(parts)

    @property
    def building_label(self) -> str:
        """Returns the building this component is grouped under in postprocessing.

        Postprocessing aggregates results per building object (KPI collections, OPEX and
        CAPEX tables, the building-sizer and webtool JSON exports), and those groups need a
        string key even for components that carry no building of their own. This property
        returns :py:attr:`building` when it is set and :py:attr:`DEFAULT_BUILDING_LABEL`
        otherwise, which reproduces the historical behaviour where every configuration
        carried the decorative default building name.
        """
        return self.building if self.building is not None else self.DEFAULT_BUILDING_LABEL


@dataclass
class ConfigBase(JSONWizard):
    """Base class for all configurations.

    Every component configuration derives from this class and therefore carries exactly one
    identity field, :py:attr:`component_id`, which replaces the former ``name`` plus
    ``building_name`` string pair. In serialized form the identity is a nested object, i.e.
    ``{"component_id": {"name": ..., "building": ..., "unit": ...}}``.
    """

    component_id: ComponentID

    def __init__(self, component_id: ComponentID):
        """Initializes a bare configuration from its structured identity.

        Only :py:class:`ConfigBase` itself uses this constructor; every concrete subclass is a
        dataclass and generates its own ``__init__`` that takes ``component_id`` as its first
        field followed by the component-specific parameters.
        """
        self.component_id = component_id

    @classmethod
    def get_main_classname(cls):
        """Returns the fully qualified class name for the class that is getting configured. Used for Json."""
        raise NotImplementedError("Missing a definition of the ")

    @classmethod
    def get_config_classname(cls):
        """Gets the class name. Helper function for default connections."""
        return cls.__module__ + "." + cls.__name__

    def get_string_dict(self) -> List[str]:
        """Turns the config into a str list for the report."""
        my_dict = self.to_dict()
        my_list = []
        if len(my_dict) > 0:
            for entry in my_dict.items():
                first_entry = entry[0].rsplit("_")
                first_entry = " ".join(first_entry)
                first_entry = first_entry.capitalize()
                my_list.append(first_entry + ": " + str(entry[1]))
        return my_list


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
        component_id: ComponentID,
    ):
        """Defines a component output.

        Besides the display and load-type metadata, an output carries the structured identity
        of the component that produced it. Postprocessing needs to know which building object
        an output belongs to, and it must not obtain that by taking the runtime name apart, so
        the owning :py:class:`ComponentID` is stored alongside the derived ``component_name``.
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
        self.component_id: ComponentID = component_id

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


@dataclass
class DisplayConfig:
    """Configure how to display this component in postprocessing."""

    pretty_name: str | None = None
    display_in_webtool: bool = False

    @classmethod
    def show(cls, pretty_name):
        """Shortcut for showing in webtool with a specified name."""
        return DisplayConfig(pretty_name, display_in_webtool=True)


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
        my_config: ConfigBase,
        my_display_config: DisplayConfig,
    ) -> None:
        """Initializes the component class."""
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
        if isinstance(my_config, ConfigBase):
            self.config = my_config
        else:
            raise ValueError(
                "The argument my_config is not a ConfigBase object.",
                "Please check your components' configuration classes and inherit from ConfigBase class according to hisim/components/example_component.py.",
            )
        self.my_display_config: DisplayConfig = my_display_config
        self.log_connections: List[Any] = []
        self.enable_logging = my_simulation_parameters.log_connections

    @property
    def component_id(self) -> ComponentID:
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
        is :py:attr:`ComponentID.key`. Unlike the previous implementation this no longer
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

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculates operational cost, operational co2 footprint and consumption in kWh (for Diesel in l) during simulation time frame."""
        raise NotImplementedError(f"{self.component_name} has no opex costs implemented.")

    @staticmethod
    def get_cost_capex(config: ConfigBase, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        # pylint: disable=unused-argument
        """Calculates lifetime, total capital expenditure cost and total co2 footprint of production of device."""
        raise NotImplementedError(f"{config.get_main_classname()} has no capex costs implemented.")

    def get_component_kpi_entries(
        self,
        all_outputs: List,  # pylint: disable=unused-argument
        postprocessing_results: pd.DataFrame,  # pylint: disable=unused-argument
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        # if the method is not implemented in the component return an empty list
        raise NotImplementedError(f"{self.component_name} has no kpis implemented.")

    def calc_maintenance_cost(self) -> float:
        """Calc maintenance_cost per simulated period as share of capex of component."""

        maintenance_cost_per_simulated_period_in_euro = self.get_cost_capex(
            config=self.config, simulation_parameters=self.my_simulation_parameters
        ).maintenance_cost_per_simulated_period_in_euro

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
