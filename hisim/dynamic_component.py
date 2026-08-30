""" Dynamic components are able to have an arbitrary number of inputs and outputs. """
# clean

from dataclasses import dataclass
from typing import ClassVar, List, Union, Dict, Tuple, cast, Optional
import dataclasses as dc
import enum
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


@enum.unique
class DynamicFeedSite(str, enum.Enum):

    """Where a feed into an aggregator was registered, in words a message can quote.

    A dynamic feed can enter an aggregator from three places, and when the same flow arrives from
    two of them the aggregator silently sums it twice. Whoever has to repair such a duplicate needs
    to know which two places registered it, because the repair is always to delete one of them, and
    the two are deleted in very different ways. Holding the wording here rather than at the raise
    site keeps the phrasing of the three sites identical everywhere they are named.

    The values are the sentence fragments themselves so that a message can read "... was registered
    explicitly in the system setup and again through the default connections", which names both
    registration sites in the order they happened.
    """

    #: A setup called the add-API on the aggregator by hand.
    EXPLICIT = "explicitly in the system setup"

    #: ``connect_automatically=True`` applied one of the aggregator's own default connections.
    DEFAULT_CONNECTIONS = "through the default connections"

    #: An energy-system file addressed a feed at the aggregator.
    ENERGY_SYSTEM_FILE = "through an energy-system file"


class DuplicateDynamicFeedError(Exception):

    """The same source output was fed into one aggregator twice.

    A defect of a *system setup* or an energy-system file, never of a component class, and never
    something a simulation should be allowed to run with: an aggregator sums one input per feed, so
    two feeds carrying the same physical flow count that flow twice and every energy balance built
    on the aggregator's total is wrong by exactly that flow. The failure is silent otherwise — the
    numbers stay plausible — which is why this is raised at wiring time rather than checked later.

    Kept separate from :class:`~hisim.config.channels.ChannelDeclarationError`, which reports the
    other direction: a component class that declared something impossible. The two are repaired by
    different people in different files and a caller may well want to catch one and not the other.
    """


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
        self.dynamic_feed_sites: Dict[Tuple[str, str], DynamicFeedSite] = {}

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

        One input per resolved feed and one output per dispatch block that did not adopt a port
        this component already publishes, named by the format's derived templates and registered
        through exactly the bookkeeping the imperative add-API fills. No wire is made here: the
        wiring stage connects the ports afterwards, so that a wire born from a feed passes through
        the same final checks as one an author wrote.

        Args:
            connections: The resolved feeds, already validated against this component's
                channels, sorted deterministically and assigned their control ports by the
                resolver.
        """
        for connection in connections:
            self.add_resolved_dynamic_input(connection)
            if connection.created_dispatch_output_name is not None:
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

        Raises:
            DuplicateDynamicFeedError: If that source output already feeds this aggregator, whether
                a file wrote the first feed or a setup did.
        """
        self.record_dynamic_feed(
            source_object_name=connection.source_name,
            source_component_output=connection.source_output,
            site=DynamicFeedSite.ENERGY_SYSTEM_FILE,
        )
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
            ValueError: If the feed carries no dispatch block of its own to create, which the
                resolver never allows: it calls this only for a block that adopted no port.
        """
        label = connection.created_dispatch_output_name
        if connection.dispatch is None or label is None:
            raise ValueError(
                f"The resolved connection {connection.describe()} has no dispatch output to "
                "create, either because it carries no dispatch block or because its block "
                "adopted a port this aggregator already publishes."
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
            )
        )
        log.trace(f"Resolved dispatch output {label} on {self.component_name}")
        return created_output

    def record_dynamic_feed(
        self,
        source_object_name: str,
        source_component_output: str,
        site: DynamicFeedSite,
    ) -> None:
        """Books one feed into this aggregator, and refuses a second feed of the same output.

        Every path that creates a dynamic input passes through here first, which is what makes a
        duplicate impossible rather than merely unlikely: the imperative add-API a Python setup
        calls, the default connections ``connect_automatically=True`` applies, and the feeds an
        energy-system file resolves all book their feed here before any port exists. Because the
        booking happens before the port is created, a refusal leaves the aggregator exactly as it
        was rather than half-wired.

        The identity of a feed is the pair (source component, source output) and deliberately
        nothing else. Two feeds of *different* outputs of one source are perfectly ordinary — a
        heat pump reports its space-heating and its domestic-hot-water electricity separately and a
        meter wants both — so the source alone is not the key. Two feeds of the *same* output are
        refused even when their tags or their weights differ, which is the conservative reading:
        the tags say how the aggregator should classify a flow and the weight says how it should
        rank it, but neither changes the fact that the flow itself is one flow measured once, so
        summing it twice is wrong however the two copies are labelled. An aggregator that genuinely
        needs one flow to appear under two classifications has to gain a way of saying so, rather
        than getting it as a side effect of being wired twice.

        That is deliberately the same rule, on the same key, that the energy-system format has
        always enforced on the feeds of one file --
        :meth:`~hisim.energy_system.aggregator_ports.AggregatorPortChecker.check_participant_ports_are_unique`,
        reported as ``EF-25`` with the remedy "a participant's output feeds one aggregator at most
        once". Only the imperative path was missing it, which is why a hand-wired duplicate could
        survive while the very same wiring written in a file was refused.

        Args:
            source_object_name: Instance name of the component the flow comes from.
            source_component_output: Field name of the output on that component.
            site: Where this registration comes from, used to name both sites in the message.

        Raises:
            DuplicateDynamicFeedError: If that source output has already been fed into this
                aggregator, naming the aggregator, the source, the output and both sites.
        """
        feed = (source_object_name, source_component_output)
        first_site = self.dynamic_feed_sites.get(feed)
        if first_site is not None:
            raise DuplicateDynamicFeedError(
                f"The aggregator '{self.component_name}' is fed twice from the same output: "
                f"'{source_object_name}' output '{source_component_output}' was registered "
                f"{first_site.value} and again {site.value}. The aggregator adds up one input per "
                f"feed, so this counts that flow twice and every total built on it is wrong by "
                f"exactly that amount. Delete one of the two registrations: either the explicit "
                f"add_component_input_and_connect(...) call in the setup, or the automatic one by "
                f"adding '{self.component_name}' with connect_automatically=False -- but then wire "
                f"its other default connections by hand."
            )
        self.dynamic_feed_sites[feed] = site

    def add_component_input_and_connect(
        self,
        source_component_output: str,
        source_object_name: str,
        source_load_type: lt.LoadTypes,
        source_unit: lt.Units,
        source_tags: List[Union[lt.ComponentType, lt.InandOutputType]],
        source_weight: int,
        allow_unconnected_mandatory: bool = False,
        site: DynamicFeedSite = DynamicFeedSite.EXPLICIT,
    ) -> None:
        """Adds a component input and connects it at once.

        The imperative half of the add-API: a setup hands one participant's output over and gets a
        port named after it. The feed is booked with :meth:`record_dynamic_feed` before anything is
        created, so handing the same output over twice fails here instead of quietly doubling that
        flow in every total the aggregator produces.

        Args:
            source_component_output: Field name of the output to draw from.
            source_object_name: Instance name of the component that output belongs to.
            source_load_type: Load type of the flow, from the loadtypes registry.
            source_unit: Unit of the flow, from the loadtypes registry.
            source_tags: Tags classifying the participant and the flow for the runtime lookups.
            source_weight: Rank of this feed among the aggregator's inputs.
            allow_unconnected_mandatory: Whether the created port may stay unconnected.
            site: Where this registration comes from; the default says a setup asked for it by
                hand, and the default-connection path overrides it so a duplicate can name both.

        Raises:
            DuplicateDynamicFeedError: If this source output already feeds this aggregator.
        """
        self.record_dynamic_feed(
            source_object_name=source_object_name,
            source_component_output=source_component_output,
            site=site,
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
                    self.record_dynamic_feed(
                        source_object_name=component.component_name,
                        source_component_output=source_component_output,
                        site=DynamicFeedSite.EXPLICIT,
                    )

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
        """Connects all inputs of one default-connection list into this aggregator.

        The path ``connect_automatically=True`` takes: the simulator looks this aggregator's
        default connections up for each registered source and hands the matching list here. Every
        feed is marked as coming from the default connections, so that a setup which also wired one
        of them by hand gets a message naming both sites rather than a silently doubled flow.

        Args:
            dynamic_component_connections: The default connections resolved for one source.

        Raises:
            DuplicateDynamicFeedError: If one of these feeds was already registered by hand.
        """
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
                site=DynamicFeedSite.DEFAULT_CONNECTIONS,
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
