"""What a schema may say about a component class: which classes exist, and what a field holds.

Two questions stand between HiSim's declarations and a JSON Schema of the energy-system format,
and neither belongs to the assembly of the schema document itself. The first is *which* component
classes a file may name at all: not a maintained list, but every component whose configuration
class declares a preset or a named constructor, because that declaration is exactly what makes a
class configurable from a file. The second is what a single configuration field accepts, which
means translating a Python annotation into the JSON Schema that admits the same values.

Both answers are useful beyond the schema — a command line listing what can be configured asks
the first, and any renderer of a configuration asks the second — so they live here rather than
inside the exporter, and the exporter next door
(:mod:`hisim.energy_system.schema_export`) is left with the assembly alone.

This module imports HiSim's component tree, which the rest of the reading and validating path
deliberately does not. That is inherent to what it does: the set of configurable classes cannot
be known without importing them. It is therefore never imported by the loader, the model or the
structural validator, and never re-exported from the package.
"""

# clean

from __future__ import annotations

import dataclasses
import enum
import importlib
import inspect
import pkgutil
import sys
import typing
from types import NoneType
from typing import Any, Dict, List, Optional, Tuple

from hisim.config.introspection import describe_config
from hisim.config.sizing import _AutoSize


class ComponentClassScan:
    """Finds the component classes an energy-system file may name, and their config classes.

    The set is not a list somebody maintains: it is every component in :mod:`hisim.components`
    whose configuration class declares at least one preset or named constructor, because that
    declaration is exactly what makes a class configurable from a file. Discovering it by import
    means the schema follows the conversion automatically — a class that gains its first preset
    appears in the next export, and no second registry can fall out of step with the first.

    A module that cannot be imported at all (an optional third-party dependency missing on this
    machine, typically) is skipped rather than fatal, because an export must still produce a
    usable schema on a partial installation; the classes such a module holds are simply absent
    from it, exactly as they are absent from that installation.
    """

    #: The package every component of HiSim lives in, walked recursively.
    COMPONENTS_PACKAGE: str = "hisim.components"

    #: The constructor parameter whose annotation names a component's configuration class. It is
    #: the one place in HiSim where the pairing between a component and its configuration is
    #: written down, so it is the one place read here.
    CONFIG_PARAMETER: str = "config"

    @classmethod
    def collect(cls) -> Tuple[type, ...]:
        """Imports every component module and returns the classes a file may name.

        Returns:
            The component classes whose configuration declares a preset or a named constructor,
            ordered by their dotted path so that two exports of the same repository state
            produce byte-identical files.
        """
        from hisim.component import Component  # noqa: PLC0415  (component tree, imported on demand)

        package = importlib.import_module(cls.COMPONENTS_PACKAGE)
        found: Dict[str, type] = {}
        for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            try:
                module = importlib.import_module(info.name)
            except Exception:  # pylint: disable=broad-except  # a partial installation still exports
                continue
            for _name, candidate in inspect.getmembers(module, inspect.isclass):
                if not issubclass(candidate, Component) or candidate.__module__ != module.__name__:
                    continue
                config_class = cls.config_class_of(candidate)
                if config_class is None:
                    continue
                description = describe_config(config_class)
                if description.presets or description.constructors:
                    found[cls.path_of(candidate)] = candidate
        return tuple(found[key] for key in sorted(found))

    @classmethod
    def config_class_of(cls, component_class: type) -> Optional[type]:
        """Returns the configuration dataclass a component takes, or ``None`` when it takes none.

        Read from the annotation of the constructor's ``config`` parameter, which is how every
        HiSim component declares the pairing and how the class-bound validator finds it. A class
        whose annotations do not resolve — a forward reference to something the module does not
        import at runtime — answers ``None``: it cannot be configured from a file either, so it
        has no place in the schema.

        Args:
            component_class: The component class to inspect.

        Returns:
            The configuration dataclass, or ``None``.
        """
        try:
            hints = typing.get_type_hints(component_class.__init__)
        except Exception:  # pylint: disable=broad-except  # an unresolvable annotation is a "no"
            return None
        config_class = hints.get(cls.CONFIG_PARAMETER)
        if isinstance(config_class, type) and dataclasses.is_dataclass(config_class):
            return config_class
        return None

    @classmethod
    def paths_of(cls, component_class: type) -> Tuple[str, ...]:
        """Returns every dotted path under which a file may name this component class.

        A component defined inside a sub-package is normally also re-exported by that package,
        and both spellings import the same class, so both are legal in a file — the mockups use
        the shorter one (``hisim.components.building.Building``) while the class itself lives in
        ``hisim.components.building.building``. A schema that offered only one of them would
        underline a perfectly valid file, so every reachable spelling is collected and the
        shortest comes first, which is the one a completion list should offer.

        Args:
            component_class: The component class.

        Returns:
            The paths, shortest first, always at least the defining module's own.
        """
        name = component_class.__qualname__
        parts = component_class.__module__.split(".")
        paths = [f"{component_class.__module__}.{name}"]
        for depth in range(len(parts) - 1, 0, -1):
            package_name = ".".join(parts[:depth])
            package = sys.modules.get(package_name)
            if package is not None and getattr(package, name, None) is component_class:
                paths.append(f"{package_name}.{name}")
        return tuple(sorted(set(paths), key=lambda path: (len(path), path)))

    @classmethod
    def path_of(cls, component_class: type) -> str:
        """Returns the one dotted path that names this component class in a message or a key.

        Args:
            component_class: The component class.

        Returns:
            The shortest reachable spelling, which is the one an author writes.
        """
        return cls.paths_of(component_class)[0]


class JsonTypes:
    """Translating a Python annotation into the JSON Schema that accepts the same values.

    Only the annotations a file can actually spell are translated. A scalar becomes its JSON
    type, an enumeration becomes the closed list of its member names — the wire spelling this
    format uses everywhere — and an optional adds ``null``. Everything else, from a nested
    configuration object to a mapping of catalogue references, becomes the empty schema, which
    accepts anything: the executor decodes those values with the class's own deserializer, and a
    schema guessing at their shape would reject valid files for the sake of a diagnostic the
    author does not need.

    The asymmetry is deliberate. A schema that is too strict makes an editor lie about a correct
    file; one that is too loose merely leaves a mistake to the executor, which catches it with a
    better message anyway.
    """

    #: Python scalar type to the JSON Schema type name that accepts its values. ``bool`` is
    #: listed before ``int`` wherever this mapping is searched, because a bool is an int in
    #: Python and would otherwise be described as an integer.
    SCALARS: Tuple[Tuple[type, str], ...] = (
        (bool, "boolean"),
        (int, "integer"),
        (float, "number"),
        (str, "string"),
    )

    #: The schema that accepts any value, used for everything the format hands to the
    #: configuration class's own deserializer.
    ANY: Dict[str, Any] = {}

    @classmethod
    def of(cls, annotation: Any, *, sizable: bool) -> Dict[str, Any]:
        """Builds the schema for one configuration field.

        Args:
            annotation: The field's resolved annotation.
            sizable: Whether a law can size the field, which is what makes the bare word
                ``AUTO`` a legal value for it and for no other field.

        Returns:
            The schema accepting exactly the values that field admits, or the empty schema when
            the annotation is one this translation does not model.
        """
        branches = cls._branches(annotation)
        if sizable:
            branches = list(branches) + [{"const": _AutoSize.WIRE_SPELLING}]
        if not branches or any(branch == cls.ANY for branch in branches):
            return dict(cls.ANY)
        if len(branches) == 1:
            return branches[0]
        return {"anyOf": branches}

    @classmethod
    def _branches(cls, annotation: Any) -> List[Dict[str, Any]]:
        """Builds one schema per alternative an annotation admits, in declaration order.

        A union contributes one branch per member; a plain annotation contributes one. The
        sizing sentinel and the law type are dropped, because neither is ever written into a
        file: their wire form is the word ``AUTO``, which the caller adds separately.

        Args:
            annotation: The resolved annotation.

        Returns:
            The branches, possibly empty when nothing about the annotation is expressible.
        """
        branches: List[Dict[str, Any]] = []
        for candidate in cls._alternatives(annotation):
            branch = cls._single(candidate)
            if branch is not None and branch not in branches:
                branches.append(branch)
        return branches

    @classmethod
    def _alternatives(cls, annotation: Any) -> Tuple[Any, ...]:
        """Flattens a union annotation into its members, leaving anything else alone.

        Args:
            annotation: The resolved annotation.

        Returns:
            The union's members, or the annotation itself as a one-element tuple.
        """
        if typing.get_origin(annotation) is typing.Union:
            return typing.get_args(annotation)
        return (annotation,)

    @classmethod
    def _single(cls, candidate: Any) -> Optional[Dict[str, Any]]:
        """Builds the schema for one non-union alternative.

        Args:
            candidate: One member of a union, or a whole annotation.

        Returns:
            The schema, or ``None`` for the sizing sentinel and the law type, which are never
            written into a file.
        """
        if candidate is NoneType:
            return {"type": "null"}
        if not isinstance(candidate, type):
            return dict(cls.ANY)
        if candidate.__name__ in ("_AutoSize", "SizingLaw"):
            return None
        if issubclass(candidate, enum.Enum):
            return {"enum": [member.name for member in candidate]}
        for scalar, name in cls.SCALARS:
            if candidate is scalar:
                return {"type": name}
        return dict(cls.ANY)
