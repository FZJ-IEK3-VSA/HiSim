"""Configuration base classes: component identity, config base, display config.

Part of the layered ``hisim.config`` package (see the package ``__init__`` for the
layering rule). Holds the three classes every component configuration is built from,
moved verbatim out of ``hisim/component.py``:

    1. class ComponentID - the structured, immutable identity of one component instance.
    2. class ConfigBase - the base class of every component configuration dataclass.
    3. class DisplayConfig - how a component is presented in postprocessing.

The module imports nothing from the rest of HiSim, which is what lets the sizing
machinery in this package depend on ``ConfigBase`` without closing an import cycle
through ``hisim/component.py``.
"""

# clean

from __future__ import annotations

import dataclasses as dc
import typing
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Optional

from dataclasses_json import dataclass_json


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

    def to_dict(self) -> Dict[str, Any]:
        """Dumps this configuration as a plain dict of its dataclass fields.

        Every concrete config class shadows this method through its ``@dataclass_json``
        decorator, so this fallback only serves the bare base class (and would serve an
        accidentally undecorated subclass with a plain, non-JSON-normalized dataclass dump
        rather than an AttributeError).
        """
        return dc.asdict(self)

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
