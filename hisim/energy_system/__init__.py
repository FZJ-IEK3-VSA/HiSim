"""The declarative energy-system format: reading, checking and writing household systems.

An energy-system file is a YAML document that describes one simulated household — its
components, how each is configured, where each takes its inputs from and, where that is
ambiguous, where each takes its sizing facts from — without a line of Python. This package
owns that format: the in-memory model of a file, the reader that produces it, the writer
that puts it back on disk in one canonical style, and the validator that decides whether a
file makes sense. Later stages of the same package turn a valid file into a running
simulation and record what that run actually realized.

The modules, in dependency order:

    - :mod:`hisim.energy_system.errors` — the ``EF-`` catalogue of hard errors and the
      exception classes every rejection is raised through, one per lifecycle stage.
    - :mod:`hisim.energy_system.model` — the frozen models of a file: components, groups,
      the three input shapes, sizing source references, plus the format's name grammar.
    - :mod:`hisim.energy_system.document` — the YAML layer: which suffixes are read, the
      duplicate-key rule, the restricted boolean resolver, and typed value accessors.
    - :mod:`hisim.energy_system.emitter` — the canonical writer, shared by the plain
      round trip and by the annotated writer of a run record.
    - :mod:`hisim.energy_system.groups` — the off rule: what a switched-off group removes from
      a file, and the record of what it removed.
    - :mod:`hisim.energy_system.validation` — the class-independent rules: names, groups,
      a closed reference graph, configuration origin, input consistency, portable paths.
    - :mod:`hisim.energy_system.loader` — the public read and write entry points that put
      the four together.
    - :mod:`hisim.energy_system.path_resolver` — the ``${var}`` registry that keeps the
      filesystem locations in a file portable between machines.

The stages that need the component classes live in their own modules and are deliberately
*not* re-exported here, so that importing this package never drags HiSim's component tree in:

    - :mod:`hisim.energy_system.bindings` — what an entry resolves to, and the checks one
      configuration class alone can decide.
    - :mod:`hisim.energy_system.classes` — the class-bound validator: importing each entry's
      component class and checking the file against it.
    - :mod:`hisim.energy_system.codec` — decoding a written value into the type its
      configuration field holds.
    - :mod:`hisim.energy_system.sizing_bridge` — handing the file's sizing sources to the
      sizing kernel and translating its refusals.
    - :mod:`hisim.energy_system.configure` — building every configuration and resolving the
      sizing of the whole system.
    - :mod:`hisim.energy_system.wiring` — constructing the components and planning every
      connection the file's ``inputs`` items describe.
    - :mod:`hisim.energy_system.wiring_checks` — one planned connection, the checks a whole
      plan must pass and the step that applies it.
    - :mod:`hisim.energy_system.feed_resolution` — turning an aggregator's feeds into the ports
      it grows and the wires that fill them.
    - :mod:`hisim.energy_system.aggregator_ports` — the uniqueness, freedom and existence rules
      for those grown ports.
    - :mod:`hisim.energy_system.executor` — the order the stages run in, and the two entry
      points that build and run a file.
    - :mod:`hisim.energy_system.metadata` — the version, the commit and the input files a
      generated record names, and nothing that changes between two regenerations.
    - :mod:`hisim.energy_system.record` — the realized record: a run written back as a file
      that states every value it decided and therefore decides nothing when re-run.
    - :mod:`hisim.energy_system.audit_records` — the shapes of a run's provenance, read both
      by the audit writer and by the comment renderer.
    - :mod:`hisim.energy_system.audit` — collecting that provenance from a finished build, and
      writing it beside a record together with the flat log of every wire that was made.
    - :mod:`hisim.energy_system.comments` — the writer that renders the same provenance as the
      end-of-line comments of a record. It is the one module that uses a second YAML library,
      because the canonical writer cannot attach comments, and it is never on a path that
      reads a file.
    - :mod:`hisim.energy_system.schema_classes` — which component classes a file may name at all,
      and what JSON Schema accepts the values one configuration field holds.
    - :mod:`hisim.energy_system.schema_export` — the generated JSON Schema of the format, which an
      editor binds to through the ``# yaml-language-server:`` line at the top of a file.
    - :mod:`hisim.energy_system.parity` — the resolved wiring of an assembled system as plain,
      comparable data, the table translating the port names two build paths give one connection,
      and the comparison of two result frames. It is what proves that a system built from a file
      and the same system built by Python are the same system.
    - :mod:`hisim.energy_system.recording` — the only part of this package that runs the other way:
      it observes a system a Python ``setup_function`` built and writes the file describing it.

Two further modules sit outside both groups. :mod:`hisim.energy_system.channels` holds the
declaration of an aggregator's accepted flows and :mod:`hisim.energy_system.resolution` the
record of one resolved feed; both import HiSim's load types and nothing else, and both are
imported *by* component code — an aggregator declares its channels, a dynamic component creates
the ports a resolved feed asks for — so they are deliberately free of everything else in this
package. :mod:`hisim.energy_system.channel_matching` sits on top of the first and holds the rule
that picks a channel for a feed.

**Layering rule.** The re-exported modules import :mod:`hisim.config`, the standard library
and PyYAML, and nothing else of HiSim. In particular no component module is imported while a
file is read, modelled, expanded or structurally validated: an editor, a schema exporter or a
batch-authoring tool can therefore check a file without pulling in HiSim's component tree,
and a file may be verified for shape before the classes it names have even been written.
Component classes are imported only by :mod:`hisim.energy_system.classes` and the stages
built on it, and only under the dotted paths the file itself writes.

Importing the names from this package — ``from hisim.energy_system import
load_energy_system`` — is the canonical spelling; the submodules are equally importable
for code that prefers the fully-qualified path.
"""

# clean

from hisim.energy_system.errors import (
    EnergySystemBindingError,
    EnergySystemCatalogueError,
    EnergySystemError,
    EnergySystemErrorId,
    EnergySystemFormatError,
    EnergySystemRecordError,
    EnergySystemRecordingError,
    EnergySystemSizingError,
    EnergySystemWiringError,
)
from hisim.energy_system.model import (
    AggregatorFeed,
    ComponentEntry,
    ConstructorCall,
    DefaultInputs,
    DispatchSpec,
    EnergySystemFile,
    ExplicitWire,
    Group,
    InputItem,
    NameRules,
    SourceReference,
)
from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.energy_system.groups import (
    DroppedInputItem,
    ExpansionRecord,
    GroupExpander,
    ShrunkSizingList,
    enabled_component_names,
    expand_groups,
)
from hisim.energy_system.loader import (
    EnergySystemReader,
    dump_energy_system,
    load_energy_system,
    parse_energy_system,
)
from hisim.energy_system.validation import StructuralValidator, validate_structure
from hisim.energy_system.path_resolver import PathFieldCodec, PathResolver, PathVariable

__all__ = [
    "AggregatorFeed",
    "ComponentEntry",
    "ConstructorCall",
    "DefaultInputs",
    "DispatchSpec",
    "DroppedInputItem",
    "EnergySystemBindingError",
    "EnergySystemCatalogueError",
    "EnergySystemEmitter",
    "EnergySystemError",
    "EnergySystemErrorId",
    "EnergySystemFile",
    "EnergySystemFormatError",
    "EnergySystemReader",
    "EnergySystemRecordError",
    "EnergySystemRecordingError",
    "EnergySystemSizingError",
    "EnergySystemWiringError",
    "ExpansionRecord",
    "ExplicitWire",
    "Group",
    "GroupExpander",
    "InputItem",
    "NameRules",
    "PathFieldCodec",
    "PathResolver",
    "PathVariable",
    "ShrunkSizingList",
    "SourceReference",
    "StructuralValidator",
    "dump_energy_system",
    "enabled_component_names",
    "expand_groups",
    "load_energy_system",
    "parse_energy_system",
    "validate_structure",
]
