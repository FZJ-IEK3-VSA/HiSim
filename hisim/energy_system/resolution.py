"""What one resolved aggregator feed is, and the port names it derives.

An aggregating component — an electricity meter, an energy management system — has no input per
participant. It grows one when a file hands it a participant, and this module holds the record
of a feed that has been fully classified: the participant and the port of it that is measured,
the tags and weight the file declared, the channel those tags selected, and the optional
back-channel the aggregator sends its dispatch signal on.

The one piece of logic here is the **derived port names**, and they matter more than their size
suggests. An aggregator input is ``{output}From{source}`` and a dispatch output is
``DispatchTo{source}_{input}``; neither disambiguates by appending a counter, because a
participant's ``(name, output)`` pair is unique per aggregator, and because these names end up in
result files and key-performance-indicator lookups, where a positional counter would silently
change meaning whenever a participant is added. They are properties rather than stored fields so
that they cannot drift from the templates.

The module imports no component module at all — the component types appear only in type
annotations — which is what lets :mod:`hisim.dynamic_component` import the record from here
without closing an import cycle. The machinery that produces these records lives in
:mod:`hisim.energy_system.feed_resolution`.
"""

# clean

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, List, Optional, Tuple

from hisim import loadtypes as lt
from hisim.energy_system.channels import ConnectionTag, DynamicConnectionChannel

if TYPE_CHECKING:  # pragma: no cover - for type checking only, never imported at runtime
    from hisim.component import Component, ComponentOutput


@dataclass(frozen=True)
class ResolvedDispatch:
    """The back-channel half of a resolved feed: the signal an aggregator sends a participant.

    A participant an aggregator merely measures needs no back-channel; one the aggregator
    controls does, because the aggregator has to publish the power it wants that participant to
    draw. The target input is optional on purpose: an energy management system ranks several
    participants whose dispatch output nothing reads back — it is recorded for postprocessing
    while the participant's controller receives its signal over an ordinary wire — and making
    the input optional expresses that without inventing a fake port.

    The tags are the fully resolved ones the created output will carry: the channel's dispatch
    tags plus the participant's own component type.
    """

    target_input: Optional[str]
    tags: Tuple[ConnectionTag, ...]


@dataclass(frozen=True)
class ResolvedDynamicConnection:
    """One fully classified feed, ready for an aggregator to create its ports from.

    Everything an aggregator needs is present and already checked: the participant and the port
    of it that is measured, the tags and the weight verbatim as the file declared them, the
    channel the tags selected — which supplies the load type and the unit — and the optional
    back-channel. The derived port names are properties rather than stored fields, so that they
    can never drift from the templates.

    The participant's kind and the flow's semantics are stored in the two separate fields the
    file uses, and the combined :attr:`tags` list an aggregator stores on the created port is
    rebuilt from them as the component type followed by the flow tags. That makes the list
    component-type-first by construction, which is what the existing tag-based runtime lookups
    rely on, without any author having to remember the order.
    """

    #: Template for the aggregator input created per feed.
    AGGREGATOR_INPUT_TEMPLATE: ClassVar[str] = "{source_output}From{source_name}"

    #: Template for the dispatch output of a feed that names a target input.
    DISPATCH_OUTPUT_TEMPLATE: ClassVar[str] = "DispatchTo{source_name}_{target_input}"

    #: Template for the dispatch output of a feed without a target input, whose signal is
    #: recorded for postprocessing rather than read by another component.
    RECORDED_DISPATCH_OUTPUT_TEMPLATE: ClassVar[str] = "DispatchFor{source_name}_{source_output}"

    source_name: str
    source_component: "Component"
    source_output: str
    source_port: "ComponentOutput"
    target_name: str
    component_type: Optional[lt.ComponentType]
    flow_tags: Tuple[lt.InandOutputType, ...]
    weight: int
    channel: DynamicConnectionChannel
    origin: str
    dispatch: Optional[ResolvedDispatch] = None

    @property
    def tags(self) -> Tuple[ConnectionTag, ...]:
        """The combined tag list stored on the created port, component type first.

        Returns:
            The participant's component type, when it has one, followed by the flow tags.
        """
        combined: List[ConnectionTag] = []
        if self.component_type is not None:
            combined.append(self.component_type)
        combined.extend(self.flow_tags)
        return tuple(combined)

    @property
    def aggregator_input_name(self) -> str:
        """Name of the aggregator input this connection creates.

        Returns:
            The derived name, for example ``ElectricityOutputFromBoiler``.
        """
        return self.AGGREGATOR_INPUT_TEMPLATE.format(
            source_output=self.source_output, source_name=self.source_name
        )

    @property
    def dispatch_output_name(self) -> Optional[str]:
        """Name of the dispatch output, or ``None`` when there is no back-channel.

        Two templates are in play. A dispatch block naming a target input produces
        ``DispatchTo{source}_{input}``; one without a target input — the recorded but unread
        signal — produces ``DispatchFor{source}_{output}``, which stays collision-free for the
        same reason the input names do.

        Returns:
            The derived output name, or ``None``.
        """
        if self.dispatch is None:
            return None
        if self.dispatch.target_input is not None:
            return self.DISPATCH_OUTPUT_TEMPLATE.format(
                source_name=self.source_name, target_input=self.dispatch.target_input
            )
        return self.RECORDED_DISPATCH_OUTPUT_TEMPLATE.format(
            source_name=self.source_name, source_output=self.source_output
        )

    def sort_key(self) -> Tuple[int, str, str]:
        """The deterministic ordering key of a target's connections.

        Returns:
            Weight, participant name and participant output, in that priority.
        """
        return (self.weight, self.source_name, self.source_output)

    def describe(self) -> str:
        """Builds the one-line rendering error messages and the wire log name it by.

        Returns:
            A string such as ``'boiler.ElectricityOutput' -> 'meter' (weight 999)``.
        """
        return (
            f"'{self.source_name}.{self.source_output}' -> '{self.target_name}' "
            f"(weight {self.weight}, from {self.origin})"
        )


@dataclass(frozen=True)
class ResolvedDynamicWire:
    """One field-level wire that feed resolution asks the wiring stage to make.

    Resolution creates ports; it does not connect them, so that a wire born from a feed passes
    through the same port-existence, type-agreement and duplicate-feed checks as every wire an
    author wrote. This small record is the hand-over, which is why it carries names rather than
    component objects.
    """

    source_name: str
    source_output: str
    target_name: str
    target_input: str
    origin: str
