""" Dynamic components are able to have an arbitrary number of inputs and outputs. """
# clean

from dataclasses import dataclass
from typing import ClassVar, List, Union, Dict, Tuple, cast, Optional
import dataclasses as dc
import hisim.loadtypes as lt
from hisim import log
from hisim.component import Component, ComponentInput, ComponentOutput
from hisim.config import ConfigBase, DisplayConfig
from hisim.config.channels import (
    ChannelDeclarationError,
    DynamicConnectionChannel,
    ResolvedDynamicConnection,
)
from hisim.simulationparameters import SimulationParameters


@dataclass
class DynamicComponentConnection:

    """Used in the dynamic component class for defining a dynamic connection."""

    source_component_class: type[Component]
    source_class_name: str
    source_component_field_name: str
    source_load_type: lt.LoadTypes
    source_unit: lt.Units
    source_tags: List[Union[lt.ComponentType, lt.InandOutputType]]
    source_weight: int
    source_instance_name: Optional[str] = None
    allow_unconnected_mandatory: bool = False


@dataclass
class DynamicConnectionInput:

    """Class for describing a single component input."""

    source_component_class: str
    source_component_field_name: str
    source_load_type: lt.LoadTypes
    source_unit: lt.Units
    source_tags: List[Union[lt.ComponentType, lt.InandOutputType]]
    source_weight: int


@dataclass
class DynamicConnectionOutput:

    """Describes a single component output for dynamic component."""

    source_component_label: str
    source_output_field_name: str
    source_tags: List[Union[lt.ComponentType, lt.InandOutputType]]
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


class DuplicateComponentFeedError(ValueError):
    """Raised when one source output is wired into one dynamic component more than once.

    A dynamic component sums the inputs that carry a given tag, so feeding it the same output twice
    makes it count that flow twice. Nothing about the result looks wrong: the simulation completes
    and every series is plausible. ``household_gas_solar_thermal`` did exactly this -- the occupancy
    was wired to the electricity meter by hand *and* by the meter's own default connection -- and
    reported a grid import of 21.72 kWh against a total consumption of 10.9. The only thing that
    ever noticed was a derived percentage going above 100, and the setup sat broken for months
    filed as a bug in the KPI layer.

    It is a distinct type rather than a bare ``ValueError`` so a caller that genuinely wants to
    detect and skip the second feed can tell it from the other ways a connection can be rejected.
    Nothing in HiSim does that today.
    """


class DynamicComponent(Component):

    """Class for components with a dynamic number of inputs and outputs.

    A dynamic component is an *aggregator*: it grows one input per participant handed to it
    rather than declaring a fixed port per source. Two ways of handing participants over live
    side by side. A Python setup calls the imperative add-API below, which names the created
    port after the participant and a running counter. A declarative energy-system file instead
    produces resolved feeds, which :meth:`resolve_dynamic_connections` turns into ports named by
    the format's derived templates. Both paths fill the same ``my_component_inputs`` and
    ``my_component_outputs`` bookkeeping, so every tag-based runtime lookup behaves identically
    on a component wired either way and only the port names differ.

    A subclass that means to accept declarative feeds declares its accepted flows in
    :attr:`CHANNELS`; one that leaves the tuple empty accepts none, and a file addressing a feed
    at it is rejected with a message saying so rather than with a confusing tag mismatch.
    """

    #: The flows this aggregator understands, as class-level data. Empty on the base class:
    #: declaring channels is how a subclass states that it can classify participants at all, and
    #: a subclass that has not declared them yet keeps working through the imperative add-API.
    CHANNELS: ClassVar[Tuple[DynamicConnectionChannel, ...]] = ()

    def __init__(
        self,
        my_component_inputs: List[DynamicConnectionInput],
        my_component_outputs: List[DynamicConnectionOutput],
        name: str,
        my_simulation_parameters: SimulationParameters,
        my_config: ConfigBase,
        my_display_config: DisplayConfig,
    ) -> None:
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
        source_tags: List[Union[lt.ComponentType, lt.InandOutputType]],
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
            source_component_class=source_component_class,
            component_id=self.config.component_id,
        )
        self.outputs.append(myoutput)
        setattr(self, label, myoutput)

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

    @classmethod
    def get_channel(cls, key: str) -> DynamicConnectionChannel:
        """Looks one of this aggregator's declared channels up by its key.

        Simulation code is meant to ask for participants by channel key rather than by a
        hard-coded tag list, so that the declaration is the single source of truth for both file
        validation and simulation and the two cannot drift apart. This accessor is that lookup.

        Args:
            key: The stable channel identifier, for instance ``"production"``.

        Returns:
            The declared channel.

        Raises:
            ChannelDeclarationError: If this class declares no channel of that key. Only the
                aggregator's own simulation code asks, always with a key from its own
                declaration, so an unknown key is a defect of that class and never of a file.
        """
        for channel in cls.CHANNELS:
            if channel.key == key:
                return channel
        raise ChannelDeclarationError(
            f"{cls.get_full_classname()} declares no dynamic-connection channel '{key}'; "
            f"its channels are {[channel.key for channel in cls.CHANNELS]}."
        )

    def get_channel_inputs(self, key: str) -> List[ComponentInput]:
        """Returns the dynamic inputs whose tags place them on one declared channel.

        The channel-key form of :meth:`get_dynamic_inputs`: it looks the tag set up in the
        declaration instead of repeating it at the call site. The matching itself is unchanged —
        an input belongs to the channel when it carries all of the channel's tags — so replacing
        a hard-coded query by this call preserves behaviour by construction.

        Args:
            key: The stable channel identifier.

        Returns:
            The dynamic inputs on that channel, in creation order.
        """
        channel = self.get_channel(key)
        return self.get_dynamic_inputs(tags=sorted(channel.tags, key=lambda tag: tag.name))

    def resolve_dynamic_connections(self, connections: List[ResolvedDynamicConnection]) -> None:
        """Creates the ports of the feeds an energy-system file addressed at this aggregator.

        One input per resolved feed and one output per dispatch block, named by the format's
        derived templates and registered through exactly the bookkeeping the imperative add-API
        fills. No wire is made here: the wiring stage connects the ports afterwards, so that a
        wire born from a feed passes through the same final checks as one an author wrote.

        Args:
            connections: The resolved feeds, already validated against this component's
                channels and sorted deterministically by the resolver.
        """
        for connection in connections:
            self.add_resolved_dynamic_input(connection)
            if connection.dispatch is not None:
                self.add_resolved_dispatch_output(connection)

    def add_resolved_dynamic_input(self, connection: ResolvedDynamicConnection) -> ComponentInput:
        """Creates the aggregator input of one resolved feed.

        The port's name comes from the derived template and doubles as the attribute label the
        bookkeeping finds it under, which removes the positional ``Input_<source>_<field>_<n>``
        naming and with it a whole class of insertion-order bugs. Load type and unit come from
        the matched channel rather than from the participant's port, because the channel is the
        aggregator's declared reading of the flow — and the two were already checked to agree.

        Args:
            connection: The resolved feed to create the input for.

        Returns:
            The created input port.
        """
        label = connection.aggregator_input_name
        created_input = ComponentInput(
            self.component_name,
            label,
            connection.channel.load_type,
            connection.channel.unit,
            True,
        )
        self.inputs.append(created_input)
        setattr(self, label, created_input)
        self.my_component_inputs.append(
            DynamicConnectionInput(
                source_component_class=label,
                source_component_field_name=connection.source_output,
                source_load_type=connection.channel.load_type,
                source_unit=connection.channel.unit,
                source_tags=list(connection.tags),
                source_weight=connection.weight,
            )
        )
        log.trace(f"Resolved dynamic input {label} on {self.component_name}")
        return created_input

    def add_resolved_dispatch_output(self, connection: ResolvedDynamicConnection) -> ComponentOutput:
        """Creates the dispatch output of one resolved feed.

        The output carries the tags the channel dispatches with plus the participant's own
        component type, and the feed's weight; together these are exactly what the tag-and-weight
        runtime lookups search for, which is how an aggregator built from a file satisfies
        pairing invariants such as "every ranked input needs a target output of the same weight".

        Args:
            connection: The resolved feed whose dispatch block to create.

        Returns:
            The created output port.

        Raises:
            ValueError: If the feed carries no dispatch block, which the resolver never allows.
        """
        if connection.dispatch is None:
            raise ValueError(
                f"The resolved connection {connection.describe()} carries no dispatch block, "
                "so no dispatch output can be created for it."
            )
        label = connection.dispatch_output_name
        assert label is not None  # nosec - a dispatch block always derives a name
        created_output = ComponentOutput(
            object_name=self.component_name,
            field_name=label,
            load_type=connection.channel.load_type,
            unit=connection.channel.unit,
            sankey_flow_direction=True,
            output_description=(
                f"Dispatch signal the aggregator sends to '{connection.source_name}' on the "
                f"channel '{connection.channel.key}'."
            ),
            component_id=self.config.component_id,
        )
        self.outputs.append(created_output)
        setattr(self, label, created_output)
        self.my_component_outputs.append(
            DynamicConnectionOutput(
                source_component_label=label,
                source_component_class=None,
                source_output_field_name=label,
                source_tags=list(connection.dispatch.tags),
                source_load_type=connection.channel.load_type,
                source_unit=connection.channel.unit,
                source_weight=connection.weight,
            )
        )
        log.trace(f"Resolved dispatch output {label} on {self.component_name}")
        return created_output

    def add_component_input_and_connect(
        self,
        source_component_output: str,
        source_object_name: str,
        source_load_type: lt.LoadTypes,
        source_unit: lt.Units,
        source_tags: List[Union[lt.ComponentType, lt.InandOutputType]],
        source_weight: int,
        allow_unconnected_mandatory: bool = False,
    ) -> None:
        """Adds a component input and connects it at once.

        Raises:
            DuplicateComponentFeedError: if this component is already fed by that source output.
        """
        # Refuse a second feed of the same source output before creating anything. The two ways of
        # wiring a dynamic component -- by hand here, and through the default connections that
        # connect_automatically applies -- do not know about each other, so a setup that uses both
        # for one source silently doubles it. Checking the inputs rather than my_component_inputs is
        # deliberate: these are the objects connection resolution actually reads.
        for existing_input in self.inputs:
            if (
                existing_input.src_object_name == source_object_name
                and existing_input.src_field_name == str(source_component_output)
            ):
                raise DuplicateComponentFeedError(
                    f"'{self.component_name}' is already fed by "
                    f"'{source_object_name}.{source_component_output}' through input "
                    f"'{existing_input.field_name}', and something is adding it a second time. A "
                    f"dynamic component sums what it is given, so the second feed would count that "
                    f"flow twice and every total derived from it would be wrong without looking "
                    f"wrong. The usual cause is a setup wiring a source by hand and also passing "
                    f"connect_automatically=True, which applies the component's own default "
                    f"connection for the same source: do one or the other, not both."
                )

        # Label Input and generate variable
        num_inputs = len(self.inputs)
        label = f"Input_{source_object_name}_{source_component_output}_{num_inputs}"
        vars(self)[label] = label

        log.trace(f"Added component input and connection {label}")
        # Define Input as Component Input and add it to inputs
        myinput = ComponentInput(
            self.component_name,
            label,
            source_load_type,
            source_unit,
            True,
            allow_unconnected_mandatory=allow_unconnected_mandatory,
        )
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
                    log.trace(f"Added component inputs and connection {label}")
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
                allow_unconnected_mandatory=connection.allow_unconnected_mandatory,
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
    ) -> Optional[ComponentOutput]:
        """Sets all output values with given component type and weight."""

        # check if component of component type is available

        for element in self.my_component_outputs:  # loop over all outputs
            if search_and_compare(
                weight_to_search=weight_counter,
                weight_of_component=element.source_weight,
                tags_to_search=tags,
                tags_of_component=element.source_tags,
            ):
                return getattr(self, element.source_component_label)  # type: ignore[no-any-return]

        return None

    def get_all_dynamic_outputs(
        self, tags: List[Union[lt.ComponentType, lt.InandOutputType]], weight_counter: int
    ) -> List[ComponentOutput]:
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
