"""The configuration layer of HiSim: config base classes and the sizing machinery.

This package is the *bottom* layer of HiSim. It holds everything a component
configuration is made of, deliberately separated from the component runtime so that the
dependency direction is unambiguous — ``hisim/component.py`` imports this package, never
the other way round:

    - :mod:`hisim.config.base` — ``ComponentID``, ``ConfigBase`` and ``DisplayConfig``,
      the three classes every component configuration is built from.
    - :mod:`hisim.config.presets` — the ``Catalog`` helper giving a config class its
      named default presets, plus the preset-provenance stamp.
    - :mod:`hisim.config.laws` — the ``SizingLaw`` expression algebra and the sizing
      errors: *how* a sized value derives from the surrounding system.
    - :mod:`hisim.config.context` — the ``SizingContext`` fact snapshot and the
      ``Size`` term vocabulary over exactly its fields: *what* laws may read.
    - :mod:`hisim.config.contributions` — ``FactContribution``: what a config class
      declares it computes for the scenario-wide fact pool.
    - :mod:`hisim.config.sizing` — the sizable field machinery: the ``AUTO`` sentinel,
      ``sized_field`` and the ``resolve_config`` resolver.
    - :mod:`hisim.config.report` — the ``ResolutionReport``: the structured record of
      every decision one engine run makes, for tests, logs and the audit artifact.
    - :mod:`hisim.config.engine` — the sizing-fact engine, resolving cross-component
      sizing to a fixed point over a scenario's configs.

The submodules are listed in dependency order, which is also the order this ``__init__``
imports them: ``base``, ``presets``, ``laws`` and ``report`` are leaves, ``context``
builds its ``Size`` terms from ``laws``, ``sizing`` uses ``laws`` and ``presets`` (to
normalize field rules and carry the preset stamp onto a resolved copy), and ``engine``
uses all three sizing modules plus the report.

**Layering rule.** No module in this package imports anything from the rest of HiSim at
module level, with two sanctioned exceptions. First, ``hisim.log``: consistent logging
across the whole application beats a package-local workaround, and ``hisim/log.py``
imports only the standard library, so no cycle is possible through it. Second,
:meth:`hisim.config.context.SizingContext.for_building` imports the building package
inside the method body: the building physics is what turns a ``BuildingConfig`` into
sizing facts, and the building package necessarily imports ``ConfigBase`` from here,
so a module-level import in the other direction would close an import cycle.

Importing the names from this package — ``from hisim.config import ConfigBase`` — is the
canonical spelling; the submodules are equally importable for code that prefers the
fully-qualified path.
"""

# clean

from hisim.config.base import ComponentID, ConfigBase, DisplayConfig
from hisim.config.presets import Catalog, preset_provenance
from hisim.config.laws import (
    ConfigSizingError,
    NothingToSizeError,
    SizingError,
    SizingLaw,
    law,
)
from hisim.config.context import Size, SizingContext
from hisim.config.sizing import (
    AUTO,
    Sizable,
    SizingRecordEntry,
    auto_fields,
    concrete,
    describe_auto_fields,
    resolve_config,
    sizable_fields,
    sized_field,
)
from hisim.config.report import ResolutionReport
from hisim.config.contributions import FactContribution, FactScope
from hisim.config.engine import SizingFactEngine, resolve_all

__all__ = [
    "AUTO",
    "Catalog",
    "ComponentID",
    "ConfigBase",
    "ConfigSizingError",
    "DisplayConfig",
    "FactContribution",
    "FactScope",
    "NothingToSizeError",
    "ResolutionReport",
    "Sizable",
    "Size",
    "SizingContext",
    "SizingError",
    "SizingFactEngine",
    "SizingLaw",
    "SizingRecordEntry",
    "auto_fields",
    "concrete",
    "describe_auto_fields",
    "law",
    "preset_provenance",
    "resolve_all",
    "resolve_config",
    "sizable_fields",
    "sized_field",
]
