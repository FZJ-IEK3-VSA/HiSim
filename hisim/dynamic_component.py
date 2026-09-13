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
class DynamicComponentTargetOutput:

    """The dispatch output an aggregator grows for the participant one default connection describes.

    A default connection says where a participant's measurement comes *from*; this says what the
    aggregator sends *back* to that same participant. The two belong together, so they are
    described together and created together: the output is materialised beside the input, in
    :meth:`DynamicComponent.connect_with_dynamic_connections_list`, and only for a source
    component the system setup actually built. Describing a connection creates nothing.

    Only the three fields the output does not share with its connection live here. Load type,
    unit, weight and the source class name are read off the connection itself, because a target
    output that disagreed with its own feed about any of them could never be paired with it by
    the tag-and-weight lookups that do the dispatching.

    The tags are the one thing a target output does restate, and restating invites drift: the
    dispatch pairs an input to an output by the input's component type and weight, so a target
    naming a different component type than its own feed would either find nothing or answer for
    somebody else's participant. :meth:`DynamicComponent.connect_with_dynamic_connections_list`
    therefore refuses to grow a target whose tags do not carry the connection's component type.
    """

    #: Name prefix of the port, saying what the port is — for instance
    #: ``"ElectricityToOrFromGridOfSHMoreAdvancedHeatPumpHPLib_"``. The weight is appended to it.
    source_output_name: str

    #: The tags the dispatch carries, which is what the aggregator's runtime lookup searches for.
    source_tags: List[Union[lt.ComponentType, lt.InandOutputType]]

    #: Human-readable description of the port, shown in reports.
    output_description: str


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
    #: The component this declaration was resolved against, set together with
    #: ``source_instance_name`` by :meth:`DynamicComponent.get_dynamic_default_connections` when
    #: the simulator matches the declaration to a component of the run. ``None`` on the
    #: declaration itself and on a connection list a system setup builds by hand, which speaks
    #: for the ports it names.
    source_component_instance: Optional[Component] = None
    allow_unconnected_mandatory: bool = False
    #: The dispatch output this connection asks for, if the aggregator steers this participant
    #: rather than only measuring it. ``None`` for a pure measurement — a PV system's production,
    #: say — and for a participant whose target port the system setup creates by hand.
    target_output: Optional[DynamicComponentTargetOutput] = None


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

    """Describes a single component output for dynamic component.

    Besides the port itself, the entry records the two facts about how the port came to be that
    nothing can be read back off its name: the prefix it was named from, and whether a default
    connection grew it while the simulator was wiring the run. The scenario-JSON writer needs
    both — it writes the ports a system setup added by hand and must leave out the ones the JSON
    executor will grow again from the same default connections — and reading them here is what
    keeps it from re-deriving the naming rule by cutting the weight off the end of a name.
    """

    source_component_label: str
    source_output_field_name: str
    source_tags: List[Union[lt.ComponentType, lt.InandOutputType]]
    source_weight: int
    source_load_type: lt.LoadTypes
    source_unit: lt.Units  # noqa
    source_component_class: Optional[str]
    #: The prefix the port was named from, the name without its weight. ``None`` for a port
    #: named by one of the declarative format's dispatch templates, which has no such form.
    source_output_name_prefix: Optional[str] = None
    #: True when :meth:`DynamicComponent.connect_with_dynamic_connections_list` grew this port
    #: from a default connection, false for a port a system setup created by hand.
    grown_by_a_default_connection: bool = False


def _source_publishes_the_measured_field(connection: DynamicComponentConnection) -> bool:
    """Says whether the participant a connection was resolved against publishes the port it measures.

    A default connection describes a class, and a class does not always publish everything it
    can: a heat pump built without domestic hot water preparation has no DHW power output, which
    is why such feeds carry ``allow_unconnected_mandatory``. The answer is read off the very
    component the simulator matched the declaration to, by the same key the connection is later
    made with — an output's field name against the field the feed names.

    A connection list built by hand carries no component, and then the answer is yes: nobody
    resolved it against a run, so it speaks for the ports it names.

    Args:
        connection: One default connection, as resolved for a component of the run.

    Returns:
        True when the participant publishes the measured field, or when no participant is
        attached to the connection.
    """
    source_component = connection.source_component_instance
    if source_component is None:
        return True
    measured_field = str(connection.source_component_field_name)
    return any(output.field_name == measured_field for output in source_component.outputs)


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
    side by side. A Python setup calls the imperative add-API below, which names a created input
    after the participant and a running counter and a created dispatch output after what it
    steers and the weight it steers it on. A declarative energy-system file instead produces
    resolved feeds, which :meth:`resolve_dynamic_connections` turns into ports named by the
    format's derived templates. Both paths fill the same ``my_component_inputs`` and
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

    def _refuse_a_dispatch_port_this_aggregator_already_has(
        self,
        label: str,
        source_tags: List[Union[lt.ComponentType, lt.InandOutputType]],
        source_weight: int,
        name_prefix: Optional[str],
        source_component_class: Optional[str],
        grown_by_a_default_connection: bool,
    ) -> None:
        """Refuses a dispatch port that a port this aggregator already publishes stands for.

        Both ways of creating a dispatch port come through here — the prefix-and-weight one a
        system setup or a default connection uses, and the template-named one the declarative
        format resolves — so one rule holds on both sides instead of one side being hardened
        alone.

        A port is a second one of an existing port by name, or by meaning. By meaning is the
        one a name check cannot see: the lookups that dispatch match a participant's component
        type and its weight and never a name, so a port carrying a component type this
        aggregator already dispatches on that weight is the same port to them however it is
        spelled. The runtime would hand the dispatch both, and the dispatch pairs its ranked
        inputs with outputs by position — so one of the two would never be written and the
        participant behind it would be steered by nothing, silently.

        Args:
            label: The name the new port would carry.
            source_tags: The tags the new port would carry.
            source_weight: The weight the new port would be dispatched on.
            name_prefix: The prefix the name was built from, or None for a template-named port.
            source_component_class: Class name of the participant the new port steers, if any.
            grown_by_a_default_connection: Whether the new port is being grown from a default
                connection, which is what makes two instances of one class diagnosable.

        Raises:
            ValueError: If this aggregator already publishes such a port. The message names the
                cause: one participant given a target twice, two participants sharing a prefix
                and a weight, two instances of one class arriving through default connections,
                or two ports of one component type and weight under different names.
        """
        for existing_output in self.outputs:
            if existing_output.field_name != label:
                continue
            existing_entry = next(
                (entry for entry in self.my_component_outputs if entry.source_output_field_name == label),
                None,
            )
            preamble = (
                f"'{self.component_name}' already publishes a dynamic output named '{label}', and "
                f"something is adding a second one. A dispatch port is named after what it is — the "
                f"prefix '{name_prefix}' — and the source weight {source_weight} it steers, so two "
                f"ports of that name would be one and the same port to every tag-and-weight lookup. "
            )
            if existing_entry is None:
                raise ValueError(
                    preamble + "That name belongs to an output this component declares itself rather "
                    "than to a dispatch port, so the prefix has to be a different one."
                )
            if (
                grown_by_a_default_connection
                and existing_entry.grown_by_a_default_connection
                and source_component_class is not None
                and existing_entry.source_component_class == source_component_class
            ):
                raise ValueError(
                    preamble + f"Two components of the class '{source_component_class}' are steered by "
                    f"this aggregator through its default connections, and a default connection names "
                    f"its port after that class and the one weight the class is dispatched on — so the "
                    f"second instance asks for the port the first already has. Two routes carry two "
                    f"devices of one kind: wire them by hand, giving each its own source weight "
                    f"(add_component_input_and_connect beside add_component_output), or describe the "
                    f"house in the declarative energy-system format, where every feed carries its own "
                    f"weight."
                )
            raise ValueError(
                preamble + "Either one participant is being given a target output twice (by hand and "
                "by a default connection, say), or two participants were given the same prefix and the "
                "same source weight."
            )

        component_types = {tag for tag in source_tags if isinstance(tag, lt.ComponentType)}
        for entry in self.my_component_outputs:
            if entry.source_weight != source_weight:
                continue
            shared_types = component_types & {
                tag for tag in entry.source_tags if isinstance(tag, lt.ComponentType)
            }
            if not shared_types:
                continue
            raise ValueError(
                f"'{self.component_name}' already publishes the dispatch port "
                f"'{entry.source_output_field_name}', which carries "
                f"{sorted(tag.value for tag in shared_types)} on source weight {source_weight}, and "
                f"something is adding '{label}' with the same component type on the same weight. The "
                f"dispatch pairs participants by their tags and their weight and never by a name, so "
                f"the two ports are one port to it: it would answer one lookup with both and write "
                f"only one of them. Give the second participant a source weight of its own."
            )

    def add_component_output(
        self,
        source_output_name: str,
        source_tags: List[Union[lt.ComponentType, lt.InandOutputType]],
        source_load_type: lt.LoadTypes,
        source_unit: lt.Units,
        source_weight: int,
        output_description: str,
        source_component_class: Optional[str] = None,
        grown_by_a_default_connection: bool = False,
    ) -> ComponentOutput:
        """Adds a dispatch output to this aggregator, named by what it is.

        The name is the caller's prefix plus the source weight, which is what the dispatch pairs
        an input and an output on — so a port's identity is what it steers rather than where it
        happens to stand in a list, and no unrelated port can move it.

        The prefix is *meant* to say what the port is and for whom
        (``ElectricityToOrFromGridOfSolarThermalSystem_``), though nothing enforces it: the
        ``dynamic_components`` system setup names all four of its targets with the generic
        ``ElectricityTarget`` and leans entirely on the weight to tell them apart.

        Args:
            source_output_name: The name prefix, saying what the port is.
            source_tags: The tags the dispatch carries, searched by the runtime lookups.
            source_load_type: The load type of the dispatched flow.
            source_unit: The unit of the dispatched flow.
            source_weight: The participant's weight; appended to the prefix to form the name.
            output_description: Human-readable description of the port.
            source_component_class: Class name of the participant this port steers, if any. An
                output naming a class no component of the run carries is dropped at registration.
            grown_by_a_default_connection: True when the port is being grown from a default
                connection while the run is wired. Recorded on the bookkeeping entry, because
                the scenario-JSON writer has to leave such a port out and cannot tell it from a
                hand-made one by its name.

        Returns:
            The created output port.

        Raises:
            ValueError: If this aggregator already publishes a port the dispatch could not tell
                this one from. See :meth:`_refuse_a_dispatch_port_this_aggregator_already_has`.
        """
        label = source_output_name + str(source_weight)
        # ``source_output_name`` may be a str-valued enum member — the dynamic_components setup
        # passes lt.InandOutputType.ELECTRICITY_TARGET — and the concatenation above is what
        # turns it into the plain string the port is named with. The prefix recorded below is
        # that same plain string, the name without its weight.
        name_prefix = label[: len(label) - len(str(source_weight))]
        self._refuse_a_dispatch_port_this_aggregator_already_has(
            label=label,
            source_tags=source_tags,
            source_weight=source_weight,
            name_prefix=name_prefix,
            source_component_class=source_component_class,
            grown_by_a_default_connection=grown_by_a_default_connection,
        )

        # Define Output as Component Input and add it to inputs
        myoutput = ComponentOutput(
            object_name=self.component_name,
            field_name=label,
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
                source_output_field_name=label,
                source_tags=source_tags,
                source_load_type=source_load_type,
                source_unit=source_unit,
                source_weight=source_weight,
                source_output_name_prefix=name_prefix,
                grown_by_a_default_connection=grown_by_a_default_connection,
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
            The dynamic inputs on that channel, in the summation order
            :meth:`get_dynamic_inputs` defines.
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
            if connection.dispatch_output_name is not None:
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
            ValueError: If the feed carries no dispatch block, which the resolver never allows:
                it calls this only for a feed that has one; or if this aggregator already
                publishes a port the dispatch could not tell this one from, which is the same
                refusal the imperative path gets, from the same guard.
        """
        label = connection.dispatch_output_name
        if connection.dispatch is None or label is None:
            raise ValueError(
                f"The resolved connection {connection.describe()} has no dispatch output to "
                "create, because it carries no dispatch block."
            )
        self._refuse_a_dispatch_port_this_aggregator_already_has(
            label=label,
            source_tags=list(connection.dispatch.tags),
            source_weight=connection.weight,
            name_prefix=None,
            source_component_class=None,
            grown_by_a_default_connection=False,
        )
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
                # A template names this port, so it has no prefix a scenario JSON could record,
                # and no default connection grew it: the file it came from describes it already.
                source_output_name_prefix=None,
                grown_by_a_default_connection=False,
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
                            # The bookkeeping stores the field name — the string the wire above
                            # actually connects and the summation key sorts on — while the label
                            # keeps embedding the display name it always has, so no port renames.
                            source_component_field_name=output_var.field_name,
                            source_load_type=source_load_type,
                            source_unit=source_unit,
                            source_tags=source_tags,
                            source_weight=source_weight,
                        )
                    )

    def connect_with_dynamic_connections_list(
        self, dynamic_component_connections: List[DynamicComponentConnection]
    ) -> None:
        """Grows the ports of one present source component and wires its measurement in.

        This is where a default connection becomes real, and it is reached once per source
        component the system setup actually built — the simulator resolves the aggregator's
        default connections against the components of the run, so a class nobody instantiated
        never gets here. Both ports of a steered participant are therefore created here: the
        input that measures it, and, when the connection describes one, the output that steers
        it back. Creating the target output anywhere earlier — inside the method that *describes*
        the connection, as it was until F-1 — gave every aggregator the target ports of every
        device it could ever meet, whether the house had them or not: a district-heated house
        carried five ports for a heat pump, an electric heater and a solar collector it does not
        have, wired to nothing and renumbering every port declared after them.

        A feed marked ``allow_unconnected_mandatory`` names a port its participant publishes only
        in some configurations — a heat pump's domestic hot water power, which is not there when
        the device prepares none. Where the participant of this run does not publish it, neither
        port is grown: not the measurement, which would read zero forever, and not the target,
        which would steer a flow nobody has. Growing one without the other is worse than growing
        both, because an aggregator that ranks its feeds pairs each one with a target of the same
        weight, and a feed left without one unbalances that pairing.

        A missing port that the connection does *not* allow to be unconnected is another matter
        and stays loud: the input is created and wired, and connecting the run refuses it by
        name, listing what the source does publish.

        Nothing here runs for an aggregator added with ``connect_automatically=False``: the
        simulator applies default connections only for a component that asked for them, so such
        an aggregator grows no target ports at all and every port it has is one its system setup
        created by hand.

        Args:
            dynamic_component_connections: The default connections of one source component,
                already carrying that component's runtime instance name.

        Raises:
            ValueError: If a connection's target output does not carry the component type the
                connection itself is tagged with. The dispatch pairs an input to an output by
                that type and the weight, so a target disagreeing with its own feed about it
                would answer for somebody else's participant or for nobody at all.
        """
        for connection in dynamic_component_connections:
            src_name: str = cast(str, connection.source_instance_name)

            if connection.allow_unconnected_mandatory and not _source_publishes_the_measured_field(connection):
                log.debug(
                    f"'{src_name}' publishes no '{connection.source_component_field_name}' in this "
                    f"configuration, so '{self.component_name}' neither measures nor steers it."
                )
                continue

            self.add_component_input_and_connect(
                source_component_output=connection.source_component_field_name,
                source_load_type=connection.source_load_type,
                source_unit=connection.source_unit,
                source_tags=connection.source_tags,
                source_weight=connection.source_weight,
                source_object_name=src_name,
                allow_unconnected_mandatory=connection.allow_unconnected_mandatory,
            )

            if connection.target_output is None:
                continue

            component_type = connection.source_tags[0]
            if component_type not in connection.target_output.source_tags:
                raise ValueError(
                    f"The default connection of '{connection.source_class_name}' to "
                    f"'{self.component_name}' is tagged {[tag.value for tag in connection.source_tags]} "
                    f"and asks for a target output tagged "
                    f"{[tag.value for tag in connection.target_output.source_tags]}, which does not "
                    f"carry '{component_type.value}'. The dispatch pairs an input with an output by "
                    f"the input's component type and weight, so this target would answer for another "
                    f"participant or for none: the two tag lists have to name the same participant."
                )

            self.add_component_output(
                source_output_name=connection.target_output.source_output_name,
                source_tags=connection.target_output.source_tags,
                source_component_class=connection.source_class_name,
                source_weight=connection.source_weight,
                source_load_type=connection.source_load_type,
                source_unit=connection.source_unit,
                output_description=connection.target_output.output_description,
                grown_by_a_default_connection=True,
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
            connection_copy.source_component_instance = source_component
            new_connections.append(connection_copy)
        return new_connections

    def get_dynamic_inputs(self, tags: List[Union[lt.ComponentType, lt.InandOutputType]]) -> List[ComponentInput]:
        """Returns the dynamic inputs carrying all of the given tags, in a path-independent order.

        Every caller of this method sums the ports it returns, so the order of the returned list
        is the order the participants are added up in. That order used to be the order the inputs
        were created in, which differs between the two ways a system can be built: a Python setup
        creates them in its own add sequence (every explicit ``add_component_input_and_connect``
        before every default connection), while the declarative executor creates them sorted by
        :meth:`hisim.config.channels.ResolvedDynamicConnection.sort_key`. The same house therefore
        summed the same participants in two different orders, and IEEE-754 addition is not
        associative, so the two runs disagreed in the last bits — enough to fail an exact
        comparison of the two paths against each other.

        The fix is to give "the order participants are summed in" a single definition on both
        paths: the tuple :meth:`hisim.config.channels.ResolvedDynamicConnection.order_key`
        declares — weight, then source component name, then source output name — applied here to
        the *runtime* component name, which the wiring stage records on the created port and the
        imperative add-API stores at creation, so both paths key on one and the same string. (The
        executor's own pre-sort keys on the file's spelling of that name, which a building or a
        unit can remap; that sort fixes the order ports are created in, while this one is what
        defines the summation.) Weight and source output live on the bookkeeping entry. Only the
        returned list is sorted — ``my_component_inputs`` keeps its creation order, because other
        code reads it positionally.

        Example: a meter fed by ``pv.ElectricityOutput`` (weight 1) and ``chp.ElectricityOutput``
        (weight 1) sums the CHP before the PV whichever way the house was written down.

        Args:
            tags: The tags an input must all carry to be returned.

        Returns:
            The matching dynamic input ports, sorted by (weight, source component name, source
            output name).
        """
        matches = [
            element
            for element in self.my_component_inputs
            if tags_search_and_compare(tags_to_search=tags, tags_of_component=element.source_tags)
        ]
        matches.sort(key=self._summation_sort_key)
        return [getattr(self, element.source_component_class) for element in matches]

    def _summation_sort_key(self, element: DynamicConnectionInput) -> Tuple[int, str, str]:
        """The ordering key of one dynamic input: the shared participant order, read at runtime.

        The tuple itself is defined once, by
        :meth:`hisim.config.channels.ResolvedDynamicConnection.order_key`; this method only
        gathers its three operands. The bookkeeping entry carries the weight and the source output
        name directly; the source component's runtime name is only on the created port, where the
        wiring stage recorded it. An input that was allowed to stay unconnected has no source name
        and sorts as the empty string — such a port reads as zero, so where those ports land among
        each other cannot change any sum.

        Args:
            element: The bookkeeping entry of one dynamic input.

        Returns:
            Weight, source component name and source output name, in that priority.
        """
        created_input: ComponentInput = getattr(self, element.source_component_class)
        return ResolvedDynamicConnection.order_key(
            element.source_weight, created_input.src_object_name or "", element.source_component_field_name
        )

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
