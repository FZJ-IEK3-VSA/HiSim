"""The audit companion: the machine-readable account of why the record says what it says.

The realized record states what was built. It deliberately does not state *why*: a record is a
re-executable file, and every explanation written into its data would be one more thing a second
run has to reproduce. The explanations therefore live next to it, in a file nothing ever
executes, where they can be as detailed as a reviewer needs.

What a reader wants to know about a run divides into four questions, and the audit answers them
in that order. Where did each component's configuration come from — which preset, which named
constructor with which arguments, or a block the file wrote out in full — and which of its
fields did the file override, against what default. Which fields did a law compute, from which
law, reading which value from which component, and what did it produce. What did the off switches
remove: which groups, which components with them, which input items and which sizing lists that
shrank rather than vanished. And what did the aggregators end up with: one entry per resolved
feed, with the weight, the tags and the port names resolution derived rather than any file wrote.

A third artifact travels with the two: the flat wire log, one entry per connection in the same
``From``/``To`` shape the older machinery appends when connection logging is switched on. It is
written here in one deterministic pass instead of wire by wire, so that downstream tooling reads
one format regardless of which executor produced the run.

What an audit *is* — the shapes of its four answers — lives next door in
:mod:`hisim.energy_system.audit_records`, because the comment renderer of a record reads the same
shapes and must not have to reach through the collector to get at them. This module is the
collecting and the writing: nothing here is recomputed and no stage is run twice, which is what
makes the audit a description of the run rather than a second opinion about it.
"""

# clean

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Tuple

import yaml

from hisim import log
from hisim.config.introspection import SizableFieldKind
from hisim.config.sizing import AUTO, _AutoSize  # pylint: disable=protected-access
from hisim.config.laws import SizingLaw
from hisim.energy_system.audit_records import (
    AuditRecord,
    BuiltFrom,
    ComponentAudit,
    FeedAudit,
    OverriddenField,
    SizedField,
)
from hisim.energy_system.record import ConfigBlockWriter, SizingSourceWriter

if TYPE_CHECKING:  # pragma: no cover - imported for type checking only
    from hisim.energy_system.bindings import ClassBinding
    from hisim.energy_system.executor import BuiltEnergySystem


class AuditBuilder:
    """Collects the audit of one built energy system from what the build already holds.

    Nothing is recomputed and no stage is run again: the origins and the resolved configurations
    come from the configuring stage, the provenance of every computed number from the sizing
    record each configuration carries, the removals from the expansion record and the feeds from
    the wiring plan. That is what makes the audit a description of the run rather than a second
    opinion about it.
    """

    #: Phrase describing a law that reads nothing at all, taken from the introspection surface
    #: so that the audit and ``describe`` call the same thing by the same name.
    CONSTANT_EXPLANATION: ClassVar[str] = SizableFieldKind.CONSTANT.explain()

    #: Name the resolution report is reduced under. The report carries fact values straight from
    #: the kernel — enum members among them — so it goes through the same reduction to plain data
    #: as a configuration block, and this label is what a failure there is reported against.
    RESOLUTION_LABEL: ClassVar[str] = "resolution"

    def __init__(self, built: "BuiltEnergySystem") -> None:
        """Prepares the builder over one finished build.

        Args:
            built: The finished build, carrying the expanded file, the bindings, the resolved
                configurations, the wiring and the warnings.
        """
        self.built = built
        self.sources = SizingSourceWriter(built.configured.report.lookups)

    def build(self) -> AuditRecord:
        """Builds the audit of the run.

        Returns:
            The complete audit, in file order for the components and aggregator order for the
            feeds.
        """
        expansion = self.built.expansion
        return AuditRecord(
            system=self.built.model.name,
            components=tuple(
                self.component(name) for name in self.built.model.all_components()
            ),
            disabled_groups=expansion.disabled_groups,
            dropped_components=expansion.dropped_components,
            dropped_input_items=tuple(
                {
                    "consumer": item.consumer,
                    "source": item.source,
                    "index": item.index,
                    "item_kind": item.item_kind,
                }
                for item in expansion.dropped_input_items
            ),
            shrunk_sizing_lists=tuple(
                {
                    "consumer": item.consumer,
                    "fact": item.fact,
                    "before": list(item.before),
                    "after": list(item.after),
                }
                for item in expansion.shrunk_sizing_lists
            ),
            resolved_feeds=self.feeds(),
            warnings=tuple(self.built.warnings),
            resolution=ConfigBlockWriter.plain(
                self.built.configured.report.to_dict(), self.RESOLUTION_LABEL, self.RESOLUTION_LABEL
            ),
        )

    def component(self, name: str) -> ComponentAudit:
        """Builds the audit of one component.

        Args:
            name: The component's name, which is its key in the file.

        Returns:
            Its class, origin, overrides and sized fields.
        """
        binding = self.built.bindings[name]
        config = self.built.configured.config_of(name)
        return ComponentAudit(
            name=name,
            class_path=binding.entry.class_path,
            built_from=self.origin(binding),
            overrides=self.overrides(name, binding),
            sized_fields=self.sized_fields(name, config),
            added_sizing_sources=self.sources.added_facts(binding.entry),
        )

    @classmethod
    def origin(cls, binding: "ClassBinding") -> BuiltFrom:
        """Reads which builder an entry selected, as the class-bound stage resolved it.

        Args:
            binding: The entry resolved against its classes.

        Returns:
            The origin, one of the three kinds.
        """
        if binding.preset is not None:
            return BuiltFrom(kind=BuiltFrom.PRESET, name=binding.preset.name, arguments={})
        if binding.constructor is not None and binding.entry.constructor is not None:
            return BuiltFrom(
                kind=BuiltFrom.CONSTRUCTOR,
                name=binding.constructor.name,
                arguments=dict(binding.entry.constructor.arguments),
            )
        return BuiltFrom(kind=BuiltFrom.CONFIG, name=None, arguments={})

    def overrides(self, name: str, binding: "ClassBinding") -> Tuple[OverriddenField, ...]:
        """Lists the fields the file set on top of the preset or constructor it selected.

        An entry configured by a complete ``config`` block overrides nothing: the block *is* the
        origin, so reporting every one of its fields as an override would say that the author
        changed something they merely wrote down.

        Args:
            name: The component's name.
            binding: The entry resolved against its classes.

        Returns:
            One record per overridden field, in the order the file writes them.
        """
        if binding.preset is None and binding.constructor is None:
            return ()
        origin = self.built.configured.origin_of(name)
        final = self.built.configured.config_of(name)
        return tuple(
            OverriddenField(
                field=field,
                preset_default=self.origin_value(getattr(origin, field, None), name, field),
                value=ConfigBlockWriter.plain(getattr(final, field, None), name, field),
            )
            for field in binding.entry.config
        )

    @classmethod
    def origin_value(cls, value: Any, name: str, field: str) -> Any:
        """Renders what a preset or a constructor had put in one field before the file overrode it.

        The value a record writes is always concrete, but the value it *replaced* need not be: a
        preset routinely leaves a field for a law to compute, and an entry that pins such a field
        is exactly the interesting kind of override. Writing that as the sentinel's own spelling
        says "the preset left this open" instead of refusing to describe the entry at all, which is
        what the plain-data writer does when it meets a sentinel it may not put into a record.

        Args:
            value: The value the origin produced for the field.
            name: The component's name, for the message when the value is not plain data.
            field: The field's name, likewise.

        Returns:
            The sentinel's wire spelling for a value awaiting sizing, the law's own description for
            a law, and the plain form of anything else.
        """
        if value is AUTO:
            return _AutoSize.WIRE_SPELLING
        if isinstance(value, SizingLaw):
            return value.describe()
        return ConfigBlockWriter.plain(value, name, field)

    @classmethod
    def sized_fields(cls, name: str, config: Any) -> Tuple[SizedField, ...]:
        """Reads the provenance of every computed field off one resolved configuration.

        Args:
            name: The component's name, for the message when a value is not plain data.
            config: The resolved configuration, carrying its sizing record.

        Returns:
            One record per field a law computed, in the order the laws were evaluated; empty
            for a configuration in which nothing had to be decided.
        """
        return tuple(
            SizedField(
                field=entry.field,
                law=entry.law,
                kind=SizableFieldKind.CONSTANT.value if not entry.inputs else SizableFieldKind.LAW.value,
                inputs=tuple(
                    (source, ConfigBlockWriter.plain(value, name, entry.field))
                    for source, value in entry.inputs
                ),
                value=ConfigBlockWriter.plain(entry.value, name, entry.field),
            )
            for entry in getattr(config, "sizing_record", ()) or ()
        )

    def feeds(self) -> Tuple[Tuple[str, Tuple[FeedAudit, ...]], ...]:
        """Renders every resolved aggregator feed of the run.

        Returns:
            One entry per aggregator that received feeds, each holding its connections in the
            order they were applied.
        """
        return tuple(
            (
                aggregator,
                tuple(
                    FeedAudit(
                        aggregator=aggregator,
                        source=connection.source_name,
                        output=connection.source_output,
                        component_type=(
                            connection.component_type.name
                            if connection.component_type is not None
                            else None
                        ),
                        tags=tuple(tag.name for tag in connection.flow_tags),
                        weight=connection.weight,
                        aggregator_input=connection.aggregator_input_name,
                        dispatch_output=connection.dispatch_output_name,
                        dispatch_target_input=(
                            connection.dispatch.target_input if connection.dispatch else None
                        ),
                        origin=connection.origin,
                    )
                    for connection in connections
                ),
            )
            for aggregator, connections in self.built.wired.resolved_feeds
        )


class AuditWriter:
    """Writes the audit companion and the flat wire log into a results directory.

    Both are machine-facing, so both are written plainly: the audit as YAML through the safe
    dumper, because it is read next to a YAML record, and the wire log as JSON in the shape the
    older machinery already produces, because tools downstream of a run read that shape and have
    no reason to learn a second one.

    Existing files of the same names are overwritten. Within one results directory the artifacts
    describe this run, and a leftover from an aborted earlier run in the same directory would be
    worse than no artifact at all.
    """

    #: File name of the audit companion, written next to the record it explains.
    AUDIT_FILENAME: ClassVar[str] = "realized.audit.yaml"

    #: File name of the flat wire log. The name and the entry shape are shared with the older
    #: machinery on purpose: one artifact, one format, whichever executor produced the run.
    CONNECTIONS_FILENAME: ClassVar[str] = "component_connections.json"

    #: Indentation of the wire log, matching what the older writer produces.
    JSON_INDENT: ClassVar[int] = 4

    #: Column at which the audit's YAML wraps; wide, because a law renders long.
    LINE_WIDTH: ClassVar[int] = 1_000_000

    @classmethod
    def write_audit(cls, audit: AuditRecord, path: str) -> str:
        """Writes one audit record to a file.

        Args:
            audit: The audit of the run.
            path: Where to write it.

        Returns:
            The path written.
        """
        text = yaml.safe_dump(
            audit.to_document(),
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
            width=cls.LINE_WIDTH,
        )
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(str(text))
        return path

    @classmethod
    def wire_log(cls, built: "BuiltEnergySystem") -> List[Dict[str, Any]]:
        """Builds the flat wire log of one built system.

        The entry shape — ``{"From": {"Component", "Field"}, "To": {"Component", "Field"}}`` —
        and the component names are the runtime ones, so the log describes the same wires the
        older per-connection writer would have appended for the same run. The order is plan
        order: the written and default-expanded items first, then the wires feed resolution
        derived.

        Args:
            built: The finished build.

        Returns:
            One entry per applied connection.
        """
        return [
            {
                "From": {"Component": wire.source_runtime_name, "Field": wire.source_output},
                "To": {"Component": wire.target_runtime_name, "Field": wire.target_input},
            }
            for wire in built.wired.wires
        ]

    @classmethod
    def write_wire_log(cls, built: "BuiltEnergySystem", path: str) -> str:
        """Writes the flat wire log of one built system to a file.

        Args:
            built: The finished build.
            path: Where to write it.

        Returns:
            The path written.
        """
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(cls.wire_log(built), stream, indent=cls.JSON_INDENT)
            stream.write("\n")
        return path


def build_audit(built: "BuiltEnergySystem") -> AuditRecord:
    """Builds the audit companion of one built energy system.

    Args:
        built: The finished build, as
            :func:`hisim.energy_system.executor.build_energy_system` returns it.

    Returns:
        The audit: per component its origin, its overrides and every field a law computed; what
        the off switches removed; every resolved aggregator feed; the warnings; the resolution
        report of the sizing kernel.
    """
    return AuditBuilder(built).build()


def write_audit(built: "BuiltEnergySystem", result_directory: str) -> Tuple[str, str]:
    """Writes the audit companion and the flat wire log of one run.

    Args:
        built: The finished build.
        result_directory: The run's results directory; created when it does not exist.

    Returns:
        The paths of the audit file and of the wire log.
    """
    os.makedirs(result_directory, exist_ok=True)
    audit_path = AuditWriter.write_audit(
        build_audit(built), os.path.join(result_directory, AuditWriter.AUDIT_FILENAME)
    )
    wire_path = AuditWriter.write_wire_log(
        built, os.path.join(result_directory, AuditWriter.CONNECTIONS_FILENAME)
    )
    log.debug(f"Wrote the run audit to '{audit_path}' and the wire log to '{wire_path}'.")
    return audit_path, wire_path
