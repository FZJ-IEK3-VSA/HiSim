"""The configuration layer of HiSim: config base classes and the sizing machinery.

This package is the *bottom* layer of HiSim. It holds everything a component
configuration is made of, deliberately separated from the component runtime so that the
dependency direction is unambiguous — ``hisim/component.py`` imports this package, never
the other way round:

    - :mod:`hisim.config.base` — ``ComponentID``, ``ConfigBase`` and ``DisplayConfig``,
      the three classes every component configuration is built from.
    - :mod:`hisim.config.presets` — the ``@preset`` and ``@constructor`` decorators
      giving a config class its typed named defaults and named constructors, their
      registries (``presets_of``, ``constructors_of``) and the preset-provenance stamp.
    - :mod:`hisim.config.laws` — the ``SizingLaw`` expression algebra (including the
      ``Self`` sibling term and the ``Many`` cardinality hook) and the sizing errors:
      *how* a sized value derives from the surrounding system.
    - :mod:`hisim.config.context` — the ``SizingContext`` fact snapshot and the
      ``Size`` term vocabulary over exactly its fields: *what* laws may read.
    - :mod:`hisim.config.contributions` — ``FactContribution``: what a config class
      declares it computes for the other configs of the scenario.
    - :mod:`hisim.config.sizing` — the sizable field machinery: the ``AUTO`` sentinel,
      ``sized_field`` and the ``resolve_config`` resolver.
    - :mod:`hisim.config.report` — the ``ResolutionReport``: the structured record of
      every decision one engine run makes, for tests, logs and the audit artifact.
    - :mod:`hisim.config.engine` — the sizing-fact engine, resolving cross-component
      sizing to a fixed point over a scenario's configs.
    - :mod:`hisim.config.introspection` — ``describe_config``: the machine-readable
      description of a config class, for schema exporters, tools and tests.
    - :mod:`hisim.config.channels` — the channels an aggregating component declares it accepts
      and the record of one feed resolved onto them: the second kind of class-level declaration
      a component makes, and the only module here that reads ``hisim.loadtypes``.

The submodules are listed in dependency order, which is also the order this ``__init__``
imports them: ``presets``, ``laws`` and ``report`` are leaves, ``context`` builds its
``Size`` terms from ``laws``, ``sizing`` uses ``laws`` and ``presets`` (to normalize field
rules and carry the preset stamp onto a resolved copy), ``base`` uses ``context``,
``sizing`` and ``presets`` (whose builder-name check it runs from
``ConfigBase.__init_subclass__``), ``engine`` uses all three sizing modules plus the
report, and ``introspection`` reads all of the declaration modules without being read by
any of them.

**Layering rule.** No module in this package imports anything from the rest of HiSim at
module level, with three sanctioned exceptions, each of them a module that imports nothing
of HiSim itself and therefore cannot close a cycle. First, ``hisim.log``: consistent logging
across the whole application beats a package-local workaround, and ``hisim/log.py``
imports only the standard library. Second, ``hisim.loadtypes``, which
:mod:`hisim.config.channels` reads: a channel declaration is made of load types, units and
tags, that module is likewise pure standard library, and duplicating its enumerations here
would give HiSim two vocabularies for one thing. Third,
:meth:`hisim.config.context.SizingContext.for_building` imports the building package
inside the method body: the building physics is what turns a ``BuildingConfig`` into
sizing facts, and the building package necessarily imports ``ConfigBase`` from here,
so a module-level import in the other direction would close an import cycle. A fourth
appearance is not an import at all: :mod:`hisim.config.channels` names ``hisim.component``
in annotations under ``TYPE_CHECKING``, which no runtime import follows.

Importing the names from this package — ``from hisim.config import ConfigBase`` — is the
canonical spelling; the submodules are equally importable for code that prefers the
fully-qualified path.
"""

# clean

from hisim.config.base import ComponentID, ConfigBase, DisplayConfig
from hisim.config.presets import (
    BuilderKind,
    ConfigBuilder,
    canonical_preset,
    check_builder_declarations,
    constructor,
    constructors_of,
    preset,
    preset_provenance,
    presets_of,
)
from hisim.config.laws import (
    Cardinality,
    ConfigSizingError,
    Many,
    NothingToSizeError,
    Self,
    SizingError,
    SizingLaw,
    law,
)
from hisim.config.context import Size, SizingContext
from hisim.config.sizing import (
    AUTO,
    OwnFields,
    Sizable,
    SizingRecordEntry,
    auto_fields,
    concrete,
    describe_auto_fields,
    field_notes,
    resolve_config,
    sizable_fields,
    sized_field,
)
from hisim.config.report import ResolutionReport
from hisim.config.contributions import FactContribution
from hisim.config.engine import SizingFactEngine, resolve_all
from hisim.config.channels import (
    ChannelDeclarationError,
    ConnectionTag,
    DispatchRule,
    DynamicConnectionChannel,
    ResolvedDispatch,
    ResolvedDynamicConnection,
    ResolvedDynamicWire,
)
from hisim.config.introspection import (
    ConfigDescription,
    ConstructorInfo,
    FieldInfo,
    ParameterInfo,
    PresetInfo,
    SizableFieldInfo,
    SizableFieldKind,
    describe_config,
)

__all__ = [
    "AUTO",
    "BuilderKind",
    "Cardinality",
    "ChannelDeclarationError",
    "ComponentID",
    "ConfigBase",
    "ConfigBuilder",
    "ConfigDescription",
    "ConfigSizingError",
    "ConnectionTag",
    "ConstructorInfo",
    "DispatchRule",
    "DisplayConfig",
    "DynamicConnectionChannel",
    "FactContribution",
    "FieldInfo",
    "Many",
    "NothingToSizeError",
    "OwnFields",
    "ParameterInfo",
    "PresetInfo",
    "ResolutionReport",
    "ResolvedDispatch",
    "ResolvedDynamicConnection",
    "ResolvedDynamicWire",
    "Self",
    "Sizable",
    "SizableFieldInfo",
    "SizableFieldKind",
    "Size",
    "SizingContext",
    "SizingError",
    "SizingFactEngine",
    "SizingLaw",
    "SizingRecordEntry",
    "auto_fields",
    "canonical_preset",
    "check_builder_declarations",
    "concrete",
    "constructor",
    "constructors_of",
    "describe_auto_fields",
    "describe_config",
    "field_notes",
    "law",
    "preset",
    "preset_provenance",
    "presets_of",
    "resolve_all",
    "resolve_config",
    "sizable_fields",
    "sized_field",
]
