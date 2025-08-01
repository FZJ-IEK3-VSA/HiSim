""" Dynamic components are able to have an arbitrary number of inputs and outputs. """
# clean

from dataclasses import dataclass
from typing import Any, List, Union, Dict, cast, Optional
import dataclasses as dc
import hisim.loadtypes as lt
from hisim import log
from hisim.component import Component, ComponentInput, ComponentOutput, ConfigBase, DisplayConfig
from hisim.simulationparameters import SimulationParameters


@dataclass
class DynamicComponentConnection:

    """Used in the dynamic component class for defining a dynamic connection."""

    source_component_class: Any  # Component
    source_class_name: str
    source_component_field_name: str
    source_load_type: lt.LoadTypes
    source_unit: lt.Units
    source_tags: List[Union[lt.ComponentType, lt.InandOutputType]]
    source_weight: int
    source_instance_name: Optional[str] = None


@dataclass
class DynamicConnectionInput:

    """Class for describing a single component input."""

    source_component_class: str
    source_component_field_name: str
    source_load_type: lt.LoadTypes
    source_unit: lt.Units
    source_tags: list
    source_weight: int


@dataclass
class DynamicConnectionOutput:

    """Describes a single component output for dynamic component."""

    source_component_label: str
    source_output_field_name: str
    source_tags: list
    source_weight: int
    source_load_type: lt.LoadTypes
    source_unit: lt.Units  # noqa
    source_component_class: Optional[str]


def search_and_compare(
    weight_to_search: int,
    weight_of_component: int,
    tags_to_search: List[Union[lt.ComponentType, lt.InandOutputType]],
    tags_of_component: List[Union[lt.ComponentType, lt.InandOutputType]],
) -> bool:
    """Compares weight and tags of component inputs and outputs."""

    if weight_to_search != weight_of_component:
        return False

    for tag_search in tags_to_search:
        if tag_search not in tags_of_component:
            return False

    return True


def tags_search_and_compare(
    tags_to_search: List[Union[lt.ComponentType, lt.InandOutputType]],
    tags_of_component: List[Union[lt.ComponentType, lt.InandOutputType]],
) -> bool:
    """Compares tags of component inputs and outputs."""
    for tag_search in tags_to_search:
        if tag_search not in tags_of_component:
            return False
    return True


class DynamicComponent(Component):

    """Class for components with a dynamic number of inputs and outputs."""

    def __init__(
        self,
        my_component_inputs: List[DynamicConnectionInput],
        my_component_outputs: List[DynamicConnectionOutput],
        name: str,
        my_simulation_parameters: SimulationParameters,
        my_config: ConfigBase,
        my_display_config: DisplayConfig,
    ):
        """Initializes a dynamic component."""
        super().__init__(
            name=name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=my_config,
            my_display_config=my_display_config,
        )

        self.my_component_inputs = my_component_inputs
        self.my_component_outputs = my_component_outputs
        self.dynamic_default_connections: Dict[str, List[DynamicComponentConnection]] = {}

    def add_component_output(
        self,
        source_output_name: str,
        source_tags: list,
        source_load_type: lt.LoadTypes,
        source_unit: lt.Units,
        source_weight: int,
        output_description: str,
        source_component_class: Optional[str] = None,
    ) -> ComponentOutput:
        """Adds an output channel to a component."""
        # Label Output and generate variable
        num_inputs = len(self.outputs)
        # label = f"{source_weight}"
        label = f"Output{num_inputs + 1}"
        vars(self)[label] = label

        # Define Output as Component Input and add it to inputs
        myoutput = ComponentOutput(
            object_name=self.component_name,
            field_name=source_output_name + label,
            load_type=source_load_type,
            unit=source_unit,
            sankey_flow_direction=True,
            output_description=output_description,
            source_component_class=source_component_class
        )
        self.outputs.append(myoutput)
        setattr(self, label, myoutput)

        # Define Output as DynamicConnectionOutput
        # if source_component_class is None:
        #     source_component_class = label
        # else:
        #     source_component_class = source_component_class + label
        self.my_component_outputs.append(
            DynamicConnectionOutput(
                source_component_label=label,
                source_component_class=source_component_class,
                source_output_field_name=source_output_name + label,
                source_tags=source_tags,
                source_load_type=source_load_type,
                source_unit=source_unit,
                source_weight=source_weight,
            )
        )
        return myoutput

    def add_component_input_and_connect(
        self,
        source_component_output: str,
        source_object_name: str,
        source_load_type: lt.LoadTypes,
        source_unit: lt.Units,
        source_tags: List[Union[lt.ComponentType, lt.InandOutputType]],
        source_weight: int,
    ) -> None:
        """Adds a component input and connects it at once."""
        # Label Input and generate variable
        num_inputs = len(self.inputs)
        label = f"Input_{source_object_name}_{source_component_output}_{num_inputs}"
        vars(self)[label] = label

        log.trace("Added component input and connect: " + source_object_name + " - " + source_component_output)
        # Define Input as Component Input and add it to inputs
        myinput = ComponentInput(self.component_name, label, source_load_type, source_unit, True)
        self.inputs.append(myinput)
        myinput.src_object_name = source_object_name
        myinput.src_field_name = str(source_component_output)
        setattr(self, label, myinput)

        # Connect Input and define it as DynamicConnectionInput
        self.connect_input(label, source_object_name, source_component_output)
        self.my_component_inputs.append(
            DynamicConnectionInput(
                source_component_class=label,
                source_component_field_name=source_component_output,
                source_load_type=source_load_type,
                source_unit=source_unit,
                source_tags=source_tags,
                source_weight=source_weight,
            )
        )

    def add_component_inputs_and_connect(
        self,
        source_component_classes: List[Component],
        source_component_field_name: str,
        source_load_type: lt.LoadTypes,
        source_unit: lt.Units,
        source_tags: List[Union[lt.ComponentType, lt.InandOutputType]],
        source_weight: int,
    ) -> None:
        """Adds and connects inputs.

        Finds all outputs of listed components containing outputstring in outputname,
        adds inputs to dynamic component and connects the outputs.
        """

        # Label Input and generate variable
        num_inputs = len(self.inputs)

        # Connect Input and define it as DynamicConnectionInput
        for component in source_component_classes:
            for output_var in component.outputs:
                if source_component_field_name in output_var.display_name:
                    source_component_output = output_var.display_name

                    label = label = f"Input_{component.component_name}_{source_component_output}_{num_inputs}"
                    vars(self)[label] = label

                    # Define Input as Component Input and add it to inputs
                    myinput = ComponentInput(self.component_name, label, source_load_type, source_unit, True)
                    self.inputs.append(myinput)
                    myinput.src_object_name = component.component_name
                    myinput.src_field_name = str(source_component_output)
                    setattr(self, label, myinput)
                    num_inputs += 1
                    log.trace(
                        "Added component inputs and connect: "
                        + myinput.src_object_name
                        + " - "
                        + myinput.src_field_name
                    )
                    self.connect_input(label, component.component_name, output_var.field_name)
                    self.my_component_inputs.append(
                        DynamicConnectionInput(
                            source_component_class=label,
                            source_component_field_name=source_component_output,
                            source_load_type=source_load_type,
                            source_unit=source_unit,
                            source_tags=source_tags,
                            source_weight=source_weight,
                        )
                    )

    def connect_with_dynamic_connections_list(
        self, dynamic_component_connections: List[DynamicComponentConnection]
    ) -> None:
        """Connect all inputs based on a dynamic component connections list."""
        for connection in dynamic_component_connections:
            src_name: str = cast(str, connection.source_instance_name)

            self.add_component_input_and_connect(
                source_component_output=connection.source_component_field_name,
                source_load_type=connection.source_load_type,
                source_unit=connection.source_unit,
                source_tags=connection.source_tags,
                source_weight=connection.source_weight,
                source_object_name=src_name,
            )

    def add_dynamic_default_connections(self, connections: List[DynamicComponentConnection]) -> None:
        """Adds a dynamic default connection list definition."""

        source_component_name = connections[0].source_class_name

        for connection in connections:
            if connection.source_class_name != source_component_name:
                raise ValueError("Trying to add dynamic connections to different components in one go.")
        self.dynamic_default_connections[source_component_name] = connections
        log.trace(
            "added dynamic default connections for connections from : "
            + source_component_name
            + "\n"
            + str(self.dynamic_default_connections)
        )

    def get_dynamic_default_connections(self, source_component: Component) -> List[DynamicComponentConnection]:
        """Gets the dynamic default connections for this component."""
        source_classname: str = source_component.get_classname()

        target_classname: str = self.get_classname()

        if source_classname not in self.dynamic_default_connections:
            raise ValueError(
                "No dynamic default connections for "
                + source_classname
                + " in the connections for "
                + target_classname
                + ". content:\n"
                + str(self.dynamic_default_connections)
            )
        connections = self.dynamic_default_connections[source_classname]
        new_connections: List[DynamicComponentConnection] = []
        for connection in connections:
            connection_copy = dc.replace(connection)
            connection_copy.source_instance_name = source_component.component_name
            new_connections.append(connection_copy)
        return new_connections

    def get_dynamic_inputs(self, tags: List[Union[lt.ComponentType, lt.InandOutputType]]) -> List[ComponentInput]:
        """Returns inputs from all dynamic inputs with component type and weight."""
        inputs = []

        # check if component of component type is available
        for _, element in enumerate(self.my_component_inputs):  # loop over all inputs
            if tags_search_and_compare(tags_to_search=tags, tags_of_component=element.source_tags):
                inputs.append(getattr(self, element.source_component_class))
            else:
                continue
        return inputs

    def get_first_dynamic_output(
        self,
        tags: List[Union[lt.ComponentType, lt.InandOutputType]],
        weight_counter: int,
    ) -> Any:
        """Sets all output values with given component type and weight."""

        # check if component of component type is available

        for _, element in enumerate(self.my_component_outputs):  # loop over all outputs
            if search_and_compare(
                weight_to_search=weight_counter,
                weight_of_component=element.source_weight,
                tags_to_search=tags,
                tags_of_component=element.source_tags,
            ):
                return getattr(self, element.source_component_label)

        return None

    def get_all_dynamic_outputs(
        self, tags: List[Union[lt.ComponentType, lt.InandOutputType]], weight_counter: int
    ) -> Any:
        """Sets all output values with given component type and weight."""
        outputs = []

        for _, element in enumerate(self.my_component_outputs):  # loop over all outputs
            if search_and_compare(
                weight_to_search=weight_counter,
                weight_of_component=element.source_weight,
                tags_to_search=tags,
                tags_of_component=element.source_tags,
            ):
                outputs.append(getattr(self, element.source_component_label))
            else:
                continue

        return outputs
