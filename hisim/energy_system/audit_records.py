"""The typed record of why a run produced the values it produced.

These are the shapes the audit is made of, kept apart from the code that collects them for the
same reason the wiring plan is kept apart from the planner: two very different consumers read
them. One serializes the audit into the file written next to a record; the other renders the same
facts as the end-of-line comments of the record itself. Both have to say the same thing, which
they do by construction as long as there is one definition of what there is to say.

The shapes answer four questions about a component, in the order a reader asks them. Where did its
configuration come from — a named default of the class, a named constructor with arguments, or a
block the file spelled out. What did the file change about it, and against which default. Which of
its fields did a law compute, from which law, reading which value from which component. And, for
an aggregator, which participants it ended up with, including the port names resolution derived
that appear in no file at all.

Every one of them renders itself into plain data. The audit is machine-facing above all — a
webtool, a diff, a reviewer's script — so nothing in it is prose that has to be parsed back.
"""

# clean

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class BuiltFrom:
    """Where one component's configuration came from before anything was overridden.

    The three kinds are genuinely different claims and a reader has to be able to tell them
    apart: a *preset* is a named default of the class, a *constructor* is a named classmethod the
    file called with arguments, and a *config* block is the file spelling every field itself.
    Only the first two carry a name, and only a constructor carries arguments.

    The kind is read from what the entry selected, never from the values it ended up with: a
    complete block whose numbers happen to coincide with a preset is not a preset entry, and
    saying otherwise would make the audit claim a link that does not exist.
    """

    #: Kind of a configuration built by calling a named default of the class.
    PRESET: ClassVar[str] = "preset"

    #: Kind of a configuration built by calling a named classmethod with arguments.
    CONSTRUCTOR: ClassVar[str] = "constructor"

    #: Kind of a configuration the file spelled out field by field.
    CONFIG: ClassVar[str] = "config"

    kind: str
    name: Optional[str] = None
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def to_document(self) -> Dict[str, Any]:
        """Renders the origin as the mapping the audit file carries.

        Returns:
            The kind, the name where there is one, and the arguments where there are any.
        """
        document: Dict[str, Any] = {"kind": self.kind, "name": self.name}
        if self.arguments:
            document["arguments"] = dict(self.arguments)
        return document


@dataclass(frozen=True)
class SizedField:
    """One field a law computed, with everything needed to check the number by hand.

    ``inputs`` is the part that makes the record diagnosable rather than merely traceable: it
    carries the values the law actually read, each under the qualified name of the component
    that provided it, or under ``self.<field>`` for a sibling of the same configuration. The
    facts alone would name the ingredients without saying what they were, which is exactly the
    information a wrong result needs.

    ``kind`` separates a real derivation from an author's constant written as a law, so that a
    reader is never told that a hard-coded default was computed from the system.
    """

    field: str
    law: str
    kind: str
    inputs: Tuple[Tuple[str, Any], ...]
    value: Any

    def to_document(self) -> Dict[str, Any]:
        """Renders the sized field as the mapping the audit file carries.

        Returns:
            The field, its law, its kind, its inputs as ``[source, value]`` pairs and the
            value the law produced.
        """
        return {
            "field": self.field,
            "law": self.law,
            "kind": self.kind,
            "inputs": [[source, value] for source, value in self.inputs],
            "value": self.value,
        }


@dataclass(frozen=True)
class OverriddenField:
    """One field the file set on top of the preset or constructor it selected.

    Both values are kept, because an override is only readable next to what it replaced: a line
    setting a storage position to the same value the preset already had is noise, and one moving
    a boiler's efficiency is the most important line in the entry. The audit does not judge which
    is which; it puts the two side by side.
    """

    field: str
    preset_default: Any
    value: Any

    def to_document(self) -> Dict[str, Any]:
        """Renders the override as the mapping the audit file carries.

        Returns:
            The field, the value the origin had produced and the value the file wrote.
        """
        return {"field": self.field, "preset_default": self.preset_default, "value": self.value}


@dataclass(frozen=True)
class ComponentAudit:
    """Everything the audit says about one component of the system.

    Ordered the way a reader asks: what it is, where its configuration came from, what the file
    changed about it and what a law computed for it. A component with no preset, no override and
    no sized field still gets an entry, because its absence would read as "this component was not
    part of the run" rather than as "nothing had to be decided about it".
    """

    name: str
    class_path: str
    built_from: BuiltFrom
    overrides: Tuple[OverriddenField, ...]
    sized_fields: Tuple[SizedField, ...]
    added_sizing_sources: Tuple[str, ...] = ()

    def to_document(self) -> Dict[str, Any]:
        """Renders the component as the mapping the audit file carries.

        Returns:
            The class, the origin, the overrides, the sized fields and the sizing sources the
            record wrote although the author did not have to.
        """
        return {
            "class": self.class_path,
            "built_from": self.built_from.to_document(),
            "overrides": [override.to_document() for override in self.overrides],
            "sized_fields": [sized.to_document() for sized in self.sized_fields],
            "added_sizing_sources": list(self.added_sizing_sources),
        }


@dataclass(frozen=True)
class FeedAudit:
    """One resolved aggregator feed, including the port names nothing in the file wrote.

    An aggregator has no input per participant; it grows one when a feed is resolved, and the
    name it grows is derived from the participant and the port being measured. Those derived
    names are the part of a run that is invisible in both the file and the record, so they are
    written here — with the weight and the tags that decided the channel, and the dispatch output
    where the aggregator also sends a signal back.
    """

    aggregator: str
    source: str
    output: str
    component_type: Optional[str]
    tags: Tuple[str, ...]
    weight: int
    aggregator_input: str
    dispatch_output: Optional[str]
    dispatch_target_input: Optional[str]
    origin: str

    def to_document(self) -> Dict[str, Any]:
        """Renders the feed as the mapping the audit file carries.

        Returns:
            The participant, the port, the tags, the weight, the derived names and where the
            feed came from.
        """
        return {
            "source": self.source,
            "output": self.output,
            "component_type": self.component_type,
            "tags": list(self.tags),
            "weight": self.weight,
            "aggregator_input": self.aggregator_input,
            "dispatch_output": self.dispatch_output,
            "dispatch_target_input": self.dispatch_target_input,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class AuditRecord:
    """The whole audit of one run, as data before it is a file.

    Kept as a value rather than written straight to disk because two very different consumers
    read it: the writer that serializes it next to the record, and the comment renderer that
    turns the same facts into the end-of-line annotations of the record itself. Both must say the
    same thing, which they do by construction when there is one source for both.
    """

    system: str
    components: Tuple[ComponentAudit, ...]
    disabled_groups: Tuple[str, ...]
    dropped_components: Tuple[str, ...]
    dropped_input_items: Tuple[Dict[str, Any], ...]
    shrunk_sizing_lists: Tuple[Dict[str, Any], ...]
    resolved_feeds: Tuple[Tuple[str, Tuple[FeedAudit, ...]], ...]
    warnings: Tuple[str, ...]
    resolution: Dict[str, Any]

    def component(self, name: str) -> Optional[ComponentAudit]:
        """Returns the audit of one component, or ``None`` when the run had none of that name.

        Args:
            name: The component's name, which is its key in the file.

        Returns:
            The component's audit, or ``None``.
        """
        for entry in self.components:
            if entry.name == name:
                return entry
        return None

    def to_document(self) -> Dict[str, Any]:
        """Renders the whole audit as the nested mapping the file carries.

        Returns:
            The document, with the components keyed by name and everything else in the order
            the audit is read in.
        """
        return {
            "system": self.system,
            "components": {entry.name: entry.to_document() for entry in self.components},
            "expansion": {
                "disabled_groups": list(self.disabled_groups),
                "dropped_components": list(self.dropped_components),
                "dropped_input_items": [dict(item) for item in self.dropped_input_items],
                "shrunk_sizing_lists": [dict(item) for item in self.shrunk_sizing_lists],
            },
            "resolved_feeds": {
                aggregator: [feed.to_document() for feed in feeds]
                for aggregator, feeds in self.resolved_feeds
            },
            "warnings": list(self.warnings),
            "resolution": self.resolution,
        }
