"""The declarative energy-system format: reading, checking and writing household systems.

An energy-system file is a YAML document that describes one simulated household — its
components, how each is configured, where each takes its inputs from and, where that is
ambiguous, where each takes its sizing facts from — without a line of Python. This package
owns that format: the in-memory model of a file, the reader that produces it, the writer
that puts it back on disk in one canonical style, and the validator that decides whether a
file makes sense. Later stages of the same package turn a valid file into a running
simulation and record what that run actually realized.

The modules, in dependency order:

    - :mod:`hisim.energy_system.errors` — the ``EF-`` catalogue of hard errors and the two
      exception classes every rejection is raised through.
    - :mod:`hisim.energy_system.model` — the frozen models of a file: components, groups,
      the three input shapes, sizing source references, plus the format's name grammar.
    - :mod:`hisim.energy_system.document` — the YAML layer: which suffixes are read, the
      duplicate-key rule, the restricted boolean resolver, and typed value accessors.
    - :mod:`hisim.energy_system.emitter` — the canonical writer, shared by the plain
      round trip and by the annotated writer of a run record.
    - :mod:`hisim.energy_system.validation` — the class-independent rules: names, groups,
      a closed reference graph, configuration origin, input consistency, portable paths.
    - :mod:`hisim.energy_system.loader` — the public read and write entry points that put
      the four together.
    - :mod:`hisim.energy_system.path_resolver` — the ``${var}`` registry that keeps the
      filesystem locations in a file portable between machines.

**Layering rule.** These modules import :mod:`hisim.config`, the standard library and
PyYAML, and nothing else of HiSim. In particular no component module is imported while a
file is read, modelled or structurally validated: an editor, a schema exporter or a
batch-authoring tool can therefore check a file without pulling in HiSim's component tree,
and a file may be verified for shape before the classes it names have even been written.
Component classes are imported only by the executor stage, which turns a validated file
into objects.

Importing the names from this package — ``from hisim.energy_system import
load_energy_system`` — is the canonical spelling; the submodules are equally importable
for code that prefers the fully-qualified path.
"""

# clean

from hisim.energy_system.errors import (
    EnergySystemError,
    EnergySystemErrorId,
    EnergySystemFormatError,
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
    "EnergySystemEmitter",
    "EnergySystemError",
    "EnergySystemErrorId",
    "EnergySystemFile",
    "EnergySystemFormatError",
    "EnergySystemReader",
    "ExplicitWire",
    "Group",
    "InputItem",
    "NameRules",
    "PathFieldCodec",
    "PathResolver",
    "PathVariable",
    "SourceReference",
    "StructuralValidator",
    "dump_energy_system",
    "load_energy_system",
    "parse_energy_system",
    "validate_structure",
]
