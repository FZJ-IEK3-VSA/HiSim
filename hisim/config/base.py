"""Configuration base classes: component identity, config base, display config.

Part of the layered ``hisim.config`` package (see the package ``__init__`` for the
layering rule). Holds the three classes every component configuration is built from,
moved verbatim out of ``hisim/component.py``:

    1. class ComponentID - the structured, immutable identity of one component instance.
    2. class ConfigBase - the base class of every component configuration dataclass.
    3. class DisplayConfig - how a component is presented in postprocessing.

The module imports nothing from outside the ``hisim.config`` package, which is what lets
the sizing machinery be reachable from ``ConfigBase`` (``resolve``/``auto_fields``
delegate to :mod:`hisim.config.sizing`) without closing an import cycle through
``hisim/component.py``.
"""

# clean

from __future__ import annotations

import copy
import dataclasses as dc
import enum
import sys
import types
import typing
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Dict, List, Optional, Tuple, TypeVar

from dataclasses_json import dataclass_json

# Imported from the submodules rather than through the package, so that this module
# stays importable while ``hisim/config/__init__.py`` is still executing its own first
# line. The aliases keep the module-level functions reachable from the identically named
# ``ConfigBase`` methods that delegate to them.
from hisim.config.context import SizingContext
from hisim.config.presets import check_builder_declarations
from hisim.config.sizing import (
    SizedFieldMetadata,
    _sizable_decoder,
    auto_fields as sizing_auto_fields,
    resolve_config as sizing_resolve_config,
)


def _field_names_under_construction(config_class: type) -> List[str]:
    """Names that will become dataclass fields of a class whose body has just executed.

    ``dataclasses.fields`` cannot be used yet — the ``@dataclass`` decorator has not run
    when ``__init_subclass__`` fires — so the names are read from the class's own
    annotations plus the fields its bases already contributed. ``ClassVar`` annotations are
    skipped because they never become fields; they are recognised textually, since with
    postponed evaluation an annotation is a string and resolving it here would import every
    module a config's annotations mention.
    """
    inherited = list(getattr(config_class, "__dataclass_fields__", {}))
    own = config_class.__dict__.get("__annotations__", {})
    declared = [
        name for name, annotation in own.items() if not str(annotation).replace("typing.", "").startswith("ClassVar")
    ]
    return inherited + declared


def _sizable_enum_type(annotation: Any, owner_module: Optional[types.ModuleType]) -> Optional[type]:
    """Reads the enum a ``Sizable[SomeEnum]`` annotation names, or ``None`` for anything else.

    The annotation of a field is either the live object — ``Sizable[X]`` expands to a union
    containing ``X`` — or, in a module with postponed evaluation, the source text of it. Both
    forms occur across HiSim's component modules, so both are read: the live form by walking
    the union's members, the textual form by looking the inner name up in the module that
    declared the field, where the enum is necessarily already defined or imported.

    Args:
        annotation: The annotation as it appears in the class's own ``__annotations__``.
        owner_module: The module the config class is being defined in, or ``None`` when it
            cannot be located, in which case a textual annotation cannot be resolved.

    Returns:
        The enum class the field holds, or ``None`` when the field is not an enum-typed
        sizable field or the annotation cannot be resolved.
    """
    if not isinstance(annotation, str):
        for argument in typing.get_args(annotation):
            if isinstance(argument, type) and issubclass(argument, enum.Enum):
                return argument
        return None
    text = annotation.strip()
    prefix, suffix = "Sizable[", "]"
    if not text.startswith(prefix) or not text.endswith(suffix) or owner_module is None:
        return None
    parts = text[len(prefix): -len(suffix)].strip().split(".")
    resolved: Any = getattr(owner_module, parts[0], None)
    for part in parts[1:]:
        resolved = getattr(resolved, part, None)
    return resolved if isinstance(resolved, type) and issubclass(resolved, enum.Enum) else None


def _complete_sizable_enum_codecs(config_class: type) -> None:
    """Fills in the wire decoder of every enum-typed sizable field that did not declare one.

    A sizable field replaces the serialization layer's own decoder with one that understands
    the ``AUTO`` sentinel, and that replacement is what makes an enum-typed sizable field a
    trap: unless the declaration also passes ``value_type``, the member name read from a file
    stays a plain string. Because HiSim's config enums derive from ``str``, the mistake is
    invisible to every equality comparison and shows up only where a component compares by
    identity — so the declaration is completed here instead of being left to be remembered.

    Runs while the subclass object is created, before its ``@dataclass`` decorator has turned
    the field descriptors into fields, which is the last moment at which the descriptor can
    still be edited. A field whose annotation cannot be resolved is left alone: the check can
    only strengthen a declaration it understands, never weaken one it does not.

    Args:
        config_class: The configuration class whose body has just executed.
    """
    owner_module = sys.modules.get(config_class.__module__)
    for field_name, annotation in config_class.__dict__.get("__annotations__", {}).items():
        descriptor = config_class.__dict__.get(field_name)
        if not isinstance(descriptor, dc.Field):
            continue
        metadata = dict(descriptor.metadata)
        if SizedFieldMetadata.LAW not in metadata or SizedFieldMetadata.VALUE_TYPE in metadata:
            continue
        enum_type = _sizable_enum_type(annotation, owner_module)
        if enum_type is None:
            continue
        json_config = dict(metadata.get("dataclasses_json", {}))
        json_config["decoder"] = _sizable_decoder(enum_type)
        metadata["dataclasses_json"] = json_config
        metadata[SizedFieldMetadata.VALUE_TYPE] = enum_type
        descriptor.metadata = MappingProxyType(metadata)


@dataclass_json
@dataclass(frozen=True)
class ComponentID:
    """Structured, first-class identity of one component instance in a simulation.

    A component used to be identified by a loose pair of strings on its configuration
    (``name`` plus ``building_name``) that was collapsed into a runtime name by
    ``Component.get_component_name()`` depending on the ``multiple_buildings`` simulation
    parameter. ``ComponentID`` replaces that arrangement with a single immutable value
    object: ``name`` says what the component is, ``building`` says which building object it
    belongs to (``None`` for a plain single-building simulation), and ``unit`` says which
    sub-unit of that building (for example an apartment) owns it. Only ``name`` is required;
    the optional fields are simply absent when the surrounding simulation has no need for
    them.

    The unique runtime identifier is derived by the :py:attr:`key` property, which joins the
    fields that are actually present with underscores in the order *building*, *unit*,
    *name*. Absent fields contribute nothing at all, so ``ComponentID("Weather").key`` is
    ``"Weather"``, ``ComponentID("Weather", building="BUI1").key`` is ``"BUI1_Weather"`` and
    ``ComponentID("HeatPump", building="BUI1", unit="APT2").key`` is
    ``"BUI1_APT2_HeatPump"``. The key is derived-only: it is written into component names,
    output names and result columns, but it is never parsed back into its parts anywhere in
    HiSim. Code that needs to know the building or the unit of a component reads the
    structured fields instead.

    Because the key merely concatenates the present fields, two different tuples can in
    principle produce the same key (for example ``ComponentID("Pump", building="BUI1_APT2")``
    and ``ComponentID("Pump", building="BUI1", unit="APT2")``). No new mechanism is needed to
    catch that: such a collision shows up as two components claiming the same runtime name,
    and the existing duplicate-component-name check performed when outputs are registered
    with the simulator (see ``Simulator.add_component``) raises on it just as it does for any
    other accidental name clash.

    The class is frozen, so instances are hashable and safe to share between a configuration
    and everything derived from it; use :py:func:`dataclasses.replace` to obtain a variant.
    """

    name: str
    building: Optional[str] = None
    unit: Optional[str] = None

    #: Building label used for grouping when a component carries no explicit building.
    #: Historically every configuration defaulted to the decorative string ``"BUI1"``, and
    #: postprocessing (KPI collection, OPEX/CAPEX tables, the webtool result JSON) keys its
    #: per-building groups by that string. Keeping the same label for building-less
    #: components means the grouping output of a single-building simulation is byte-for-byte
    #: what it was before ``ComponentID`` existed.
    DEFAULT_BUILDING_LABEL: ClassVar[str] = "BUI1"

    def __post_init__(self) -> None:
        """Validates the identity right after construction.

        The name is the only mandatory part of a component identity, and an empty or
        whitespace-only name would silently produce an empty or malformed key later on.
        Rejecting it here turns a confusing downstream naming problem into an immediate,
        clearly attributable error.
        """
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"A ComponentID needs a non-empty name, but got {self.name!r}.")

    @property
    def key(self) -> str:
        """Derives the unique runtime identifier of this component.

        The key is the string that HiSim uses as the component name, as the prefix of every
        output's full name and therefore as the prefix of every result column. It is built by
        joining the present fields with ``"_"`` in the order building, unit, name; fields that
        are ``None`` simply do not appear. The key is derived-only and is never parsed back
        into building/unit/name anywhere in the code base.
        """
        parts = [part for part in (self.building, self.unit, self.name) if part is not None]
        return "_".join(parts)

    @property
    def building_label(self) -> str:
        """Returns the building this component is grouped under in postprocessing.

        Postprocessing aggregates results per building object (KPI collections, OPEX and
        CAPEX tables, the building-sizer and webtool JSON exports), and those groups need a
        string key even for components that carry no building of their own. This property
        returns :py:attr:`building` when it is set and :py:attr:`DEFAULT_BUILDING_LABEL`
        otherwise, which reproduces the historical behaviour where every configuration
        carried the decorative default building name.
        """
        return self.building if self.building is not None else self.DEFAULT_BUILDING_LABEL


#: The concrete configuration class ``resolve`` is called on, so the copy it returns keeps
#: that class for the type checker instead of widening to the base.
ConfigBaseT = TypeVar("ConfigBaseT", bound="ConfigBase")


@dataclass
class ConfigBase:
    """Base class for all configurations.

    Every component configuration derives from this class and therefore carries exactly one
    identity field, :py:attr:`component_id`, which replaces the former ``name`` plus
    ``building_name`` string pair. In serialized form the identity is a nested object, i.e.
    ``{"component_id": {"name": ..., "building": ..., "unit": ...}}``.

    Serialization comes from the ``@dataclass_json`` decorator that every concrete config
    class carries, which dumps and parses the dataclass field names verbatim (snake_case).
    The :py:meth:`to_dict` defined here is only the fallback for the base class itself; a
    decorated subclass shadows it with the dataclasses_json implementation.
    """

    component_id: ComponentID

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Validates the named builders a config class declares, before it is a dataclass.

        Runs while the subclass object is created, which is *before* its ``@dataclass``
        decorator processes the annotations — the earliest moment at which both the
        decorated ``@preset``/``@constructor`` methods and the field names are visible.
        Checking here is what turns a builder name that shadows a field into an immediate,
        located error instead of a dataclass silently adopting the classmethod as that
        field's default value.
        """
        super().__init_subclass__(**kwargs)
        check_builder_declarations(cls, _field_names_under_construction(cls))
        _complete_sizable_enum_codecs(cls)

    #: The sizing facts this config class contributes to the scenario-wide fact pool
    #: (resolved engine-side before components are constructed). Empty for the vast majority of
    #: config classes; a class that *is* a fact source — the building, a boiler whose
    #: controller sizes from its power band — overrides it with a tuple of
    #: :class:`~hisim.config.engine.FactContribution` declarations, usually assigned
    #: right below the class so the compute functions can be written as plain module
    #: functions. Declared here so that every config class has the attribute and the
    #: engine's contract is visible from the base class; the engine reads it by name via
    #: ``FactContribution.CLASS_ATTRIBUTE``. The element type stays ``Any`` deliberately: naming
    #: ``FactContribution`` here would either invert the package layering (the base
    #: classes importing the engine) or leave an unresolvable forward reference in an
    #: annotation that ``dataclasses_json`` evaluates from every subclass's module.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[Any, ...]] = ()

    if typing.TYPE_CHECKING:
        # Historically ConfigBase inherited dataclass_wizard.JSONWizard, whose missing type
        # stubs made the whole class hierarchy Any-based: mypy accepted any attribute on a
        # ConfigBase-typed value. A lot of code depends on that leniency (components store
        # their config in ConfigBase-typed slots and read/write subclass fields off it), so
        # these type-checker-only escape hatches preserve it deliberately. Field-name
        # correctness is enforced by scripts/check_config_attrs.py instead. Making configs
        # fully mypy-visible would mean a generic Component[TConfig] whose config slot has
        # the concrete subclass type; that is a repo-wide sweep over every component and is
        # deliberately not started here. The classmethod stubs mirror the serialization API
        # that the @dataclass_json decorator injects at runtime on every concrete config class.
        def __getattr__(self, name: str) -> Any:
            """Checking-only escape hatch: reads of undeclared fields resolve to Any."""
            raise NotImplementedError

        def __setattr__(self, name: str, value: Any) -> None:
            """Checking-only escape hatch: writes to undeclared fields are accepted."""

        def to_json(self, *args: Any, **kwargs: Any) -> str:  # pylint: disable=unused-argument
            """Stub for the JSON dump that @dataclass_json injects at runtime."""
            raise NotImplementedError

        @classmethod
        def from_dict(cls, kvs: Any, *args: Any, **kwargs: Any) -> Any:  # pylint: disable=unused-argument
            """Stub for the dict decoder that @dataclass_json injects at runtime."""
            raise NotImplementedError

        @classmethod
        def from_json(cls, data: Any, *args: Any, **kwargs: Any) -> Any:  # pylint: disable=unused-argument
            """Stub for the JSON decoder that @dataclass_json injects at runtime."""
            raise NotImplementedError

    def __init__(self, component_id: ComponentID):
        """Initializes a bare configuration from its structured identity.

        Only :py:class:`ConfigBase` itself uses this constructor; every concrete subclass is a
        dataclass and generates its own ``__init__`` that takes ``component_id`` as its first
        field followed by the component-specific parameters.
        """
        self.component_id = component_id

    @classmethod
    def get_main_classname(cls):
        """Returns the fully qualified class name for the class that is getting configured. Used for Json."""
        raise NotImplementedError("Missing a definition of the ")

    @classmethod
    def get_config_classname(cls):
        """Gets the class name. Helper function for default connections."""
        return cls.__module__ + "." + cls.__name__

    def cache_key_view(self: ConfigBaseT) -> ConfigBaseT:
        """Return the copy of this configuration that is hashed into its cache key.

        Not every field of a configuration affects the cached result. The base rule, applied to every config,
        clears ``component_id.building``: the same PV system computes the same series in every house, so the
        house must not be part of the key. Then the copy is handed to ``_clear_non_key_fields``, which a
        subclass overrides to remove its own non-key fields. The base rule always runs, whatever the subclass
        does; this method is not meant to be overridden.

        The copy is deep, so neither this method nor the hook ever modifies the live configuration.

        Returns:
            A deep copy of this configuration with the non-key fields cleared or normalised.
        """
        view = copy.deepcopy(self)
        component_id = getattr(view, "component_id", None)
        if component_id is not None:
            # The identity is frozen, hence the replacement rather than an assignment.
            setattr(view, "component_id", dc.replace(component_id, building=None))
        self._clear_non_key_fields(view)
        return view

    def _clear_non_key_fields(self: ConfigBaseT, view: ConfigBaseT) -> None:
        """Remove from ``view`` the fields of this configuration that do not decide the cached result.

        ``view`` is the deep copy that ``cache_key_view`` is about to hash, with the building already
        cleared. A subclass overrides this to clear fields that only place a run (where a result file is
        written, which worker index was used) and to make file paths portable (relative to the inputs
        directory instead of absolute), so that an entry computed on one machine can be found on another.
        The override mutates ``view`` in place and does not need to call ``super()``. The default clears
        nothing.

        Args:
            view: the copy to adjust; the live configuration is a different object.
        """

    def to_dict(self) -> Dict[str, Any]:
        """Dumps this configuration as a plain dict of its dataclass fields.

        Every concrete config class shadows this method through its ``@dataclass_json``
        decorator, so this fallback only serves the bare base class (and would serve an
        accidentally undecorated subclass with a plain, non-JSON-normalized dataclass dump
        rather than an AttributeError).
        """
        return dc.asdict(self)

    def resolve(self: ConfigBaseT, ctx: SizingContext) -> ConfigBaseT:
        """Returns a copy in which every AUTO field is computed by its declared law.

        This is the sizing entry point on every config:
        idempotent no-op (still returning a fresh copy) when sizable fields exist but none
        currently says AUTO, a ``NothingToSizeError`` when the class declares no sizable
        field at all, and a hard error naming field and law when a law cannot be
        evaluated against the given context. The returned copy carries its per-field
        provenance as the ``sizing_record`` attribute. The copy has the caller's concrete
        class, so ``BoilerConfig.preset_x(...).resolve(ctx)`` is a ``BoilerConfig`` to the
        type checker as well as at run time.
        """
        return sizing_resolve_config(self, ctx)

    def auto_fields(self) -> tuple:
        """Names the fields of this config that still carry the AUTO sentinel.

        Empty for a fully concrete config. ``Component.__init__`` uses this to reject
        configs that still require sizing before they can reach a running simulation.
        """
        return sizing_auto_fields(self)

    def get_string_dict(self) -> List[str]:
        """Turns the config into a str list for the report."""
        my_dict = self.to_dict()
        my_list = []
        if len(my_dict) > 0:
            for entry in my_dict.items():
                label = " ".join(entry[0].rsplit("_")).capitalize()
                my_list.append(label + ": " + str(entry[1]))
        return my_list


@dataclass
class DisplayConfig:
    """Configure how to display this component in postprocessing."""

    pretty_name: str | None = None
    display_in_webtool: bool = False

    @classmethod
    def show(cls, pretty_name):
        """Shortcut for showing in webtool with a specified name."""
        return DisplayConfig(pretty_name, display_in_webtool=True)
