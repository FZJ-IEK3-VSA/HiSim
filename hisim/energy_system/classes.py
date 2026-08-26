"""Class-bound validation: checking an energy-system file against the classes it names.

Reading a file and checking its shape says nothing about whether the system it describes can
exist. The names under ``class:`` are dotted paths into HiSim's component tree, the preset
and constructor names are declarations on the matching configuration classes, the keys of a
``config`` block are that class's fields and the keys of a ``sizing_sources`` block are facts
the class's laws actually read. All of that is decidable, but only once the classes are in
memory — which is why it lives in its own module and its own lifecycle stage, after the
document has been read, its groups expanded and its structure checked.

This is the first stage that imports component modules, and it is deliberately the only one
besides the stage that builds configurations. Everything before it works on the document
alone, so an editor plug-in, a schema exporter or a batch-authoring tool can check a file
without pulling in HiSim's component tree, and a file may be verified for shape before the
classes it names have even been written. The split also makes the current state of the
conversion legible: while a class still lacks presets and laws, a perfectly well-formed file
fails here and only here, with a message naming what the class does offer.

The stage returns what it learned rather than throwing it away. A :class:`ClassBindings`
holds, per entry, the component class, the configuration class, its machine-readable
description and the builder the entry named, so the stages that follow — building the
configurations, resolving the sizing, constructing the components — never import or
introspect the same class twice.
"""

# clean

from __future__ import annotations

import dataclasses
import importlib
import typing
from typing import Any, Dict, List, Tuple

from hisim.component import Component
from hisim.config.introspection import describe_config
from hisim.energy_system.bindings import (
    BindingFailure,
    ClassBinding,
    ClassBindings,
    ConfigOriginChecker,
)
from hisim.energy_system.errors import (
    EnergySystemBindingError,
    EnergySystemCatalogueError,
    EnergySystemErrorId,
)
from hisim.energy_system.model import ComponentEntry, EnergySystemFile


class ClassBinder:
    """Resolves and checks the classes of one energy-system file, entry by entry.

    Built for a single file and used once. The checks run per entry in file order and each
    one fails hard at the first violation, following the rule the whole format follows: a
    file that is wrong in any way is rejected with a message that names the offending entry
    and, wherever the set of valid values is closed, lists it.

    The order inside one entry is the order in which a failure explains the most: the class
    first, since nothing else can be checked without it, then how the entry is configured,
    then its overrides, then its sizing sources.
    """

    def __init__(self, model: EnergySystemFile) -> None:
        """Prepares the binder for one file.

        Args:
            model: The energy system to bind, normally after group expansion so that the
                classes of a switched-off add-on are never imported.
        """
        self.model = model

    def bind(self) -> ClassBindings:
        """Resolves every entry and returns the bindings.

        Returns:
            The bindings in file order.

        Raises:
            EnergySystemBindingError: On the first entry that contradicts its classes.
        """
        bindings: Dict[str, ClassBinding] = {}
        for name, entry in self.model.all_components().items():
            bindings[name] = self.bind_entry(name, entry)
        return ClassBindings(bindings)

    def bind_entry(self, name: str, entry: ComponentEntry) -> ClassBinding:
        """Resolves and checks one entry against its component and configuration classes.

        Args:
            name: The component's name, which is the entry's key and its identity.
            entry: The entry to check.

        Returns:
            The binding for that entry.

        Raises:
            EnergySystemBindingError: For an unusable class path, an unknown preset or
                constructor, a wrong constructor argument, an unknown ``config`` key, an
                ``AUTO`` on a field no law sizes, or a sizing source for a fact the class
                does not read.
        """
        location = f"components.{name}"
        component_class = self._import_component_class(entry.class_path, location, name)
        config_class = self._config_class_of(component_class, location, name)
        description = describe_config(config_class)
        preset = ConfigOriginChecker.resolve_preset(entry, config_class, description, location, name)
        constructor = ConfigOriginChecker.resolve_constructor(entry, config_class, description, location, name)
        ConfigOriginChecker.check_config_keys(entry, config_class, description, location, name)
        ConfigOriginChecker.check_sizing_facts(entry, config_class, location, name)
        return ClassBinding(
            name=name,
            entry=entry,
            component_class=component_class,
            config_class=config_class,
            description=description,
            preset=preset,
            constructor=constructor,
        )

    @classmethod
    def _import_component_class(cls, class_path: str, location: str, name: str) -> type:
        """Imports the dotted class path of one entry and checks that it names a component.

        Args:
            class_path: The dotted path written under ``class``.
            location: The entry's key path, for the message.
            name: The component's name, for the message.

        Returns:
            The imported class.

        Raises:
            EnergySystemBindingError: ``EF-10`` if the path has no module part, if the module
                cannot be imported, if the module has no such attribute, or if the attribute
                is not a :class:`~hisim.component.Component` subclass.
        """
        if "." not in class_path:
            raise EnergySystemBindingError(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"'{class_path}' is not a dotted path; a class is named "
                "'<module>.<ClassName>'.",
            )
        module_path, class_name = class_path.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
        except ImportError as error:
            raise EnergySystemBindingError(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"the module '{module_path}' of '{name}' cannot be imported: {error}.",
            ) from error
        imported = getattr(module, class_name, None)
        if imported is None:
            raise EnergySystemBindingError(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"the module '{module_path}' has no class '{class_name}'.",
                alternatives=cls._component_classes_of(module),
                alternatives_label="component classes in that module",
                offending_value=class_name,
            )
        if not (isinstance(imported, type) and issubclass(imported, Component)):
            raise EnergySystemBindingError(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"'{class_path}' is not a HiSim component class; an entry names the "
                "component, never its configuration class.",
                alternatives=cls._component_classes_of(module),
                alternatives_label="component classes in that module",
                offending_value=class_name,
            )
        return imported

    @classmethod
    def _component_classes_of(cls, module: Any) -> Tuple[str, ...]:
        """Lists the component classes a module defines, for a "did you mean" message.

        Only classes the module defines itself are listed: a component module imports plenty
        of others, and offering those as alternatives would send the author to the wrong file.

        Args:
            module: The imported module.

        Returns:
            The names of the component classes defined in it, sorted.
        """
        return tuple(
            sorted(
                attribute_name
                for attribute_name, attribute in vars(module).items()
                if isinstance(attribute, type)
                and issubclass(attribute, Component)
                and attribute.__module__ == module.__name__
            )
        )

    @classmethod
    def _config_class_of(cls, component_class: type, location: str, name: str) -> type:
        """Finds the configuration class a component takes, from its constructor annotation.

        Every HiSim component declares its configuration as the annotated ``config``
        parameter of ``__init__``, and that annotation is the one place the pairing is
        written down, so it is also the one place read here. A component that does not
        declare it cannot be configured from a file at all, which is a defect in the
        component rather than in the file — but it is reported against the entry, because
        that is what the author can act on.

        Args:
            component_class: The imported component class.
            location: The entry's key path, for the message.
            name: The component's name, for the message.

        Returns:
            The configuration class.

        Raises:
            EnergySystemBindingError: ``EF-10`` if the annotation is missing or unresolvable.
        """
        try:
            hints = typing.get_type_hints(component_class.__init__)
        except Exception as error:  # pylint: disable=broad-except
            raise EnergySystemBindingError(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"the constructor annotations of {component_class.__name__} cannot be "
                f"resolved, so the configuration class of '{name}' is unknown: {error}.",
            ) from error
        config_class = hints.get("config")
        if not isinstance(config_class, type) or not dataclasses.is_dataclass(config_class):
            raise EnergySystemBindingError(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"{component_class.__name__} does not annotate its constructor's 'config' "
                f"parameter with a configuration dataclass, so '{name}' cannot be "
                "configured from a file.",
            )
        return config_class


def validate_classes(model: EnergySystemFile) -> ClassBindings:
    """Checks an energy-system file against the component classes it names.

    The class-bound half of validation and the fourth stage of the lifecycle: it imports every
    entry's component class, finds the configuration class that component takes, and verifies
    that the preset, the named constructor and its arguments, the ``config`` keys and the
    ``sizing_sources`` keys all exist on that class. Nothing is constructed and nothing is
    resolved — a file that passes here is known to *describe* something the classes can
    provide, which is what the configuring stage then goes and builds.

    Ports are not checked here. Whether a component declares a given input or output is only
    knowable from a constructed instance in HiSim, because outputs are registered in the
    component's own ``__init__``, so wire-level checks belong to the stage that builds
    components and plans the wiring.

    Args:
        model: The energy system to check, normally the expanded one so that the classes of a
            switched-off add-on are never imported.

    Returns:
        The bindings, in file order, for the stages that follow.

    Raises:
        EnergySystemBindingError: On the first entry that contradicts its classes, naming the
            entry, the class and — wherever the set of valid values is closed — that set.
    """
    return ClassBinder(model).bind()


def collect_class_failures(model: EnergySystemFile) -> Tuple[BindingFailure, ...]:
    """Binds every entry independently and returns the ones that failed, in file order.

    Where :func:`validate_classes` stops at the first problem, this walks the whole file and
    reports one verdict per entry. That is what a discoverability surface wants — "these six
    components of your file cannot be built yet, and here is why each one cannot" — and it is
    what lets a test pin the exact set of entries a file currently fails on, so that the set
    shrinks visibly as component classes gain their presets, constructors and laws.

    Only the first failure of each entry is reported, because the checks inside an entry
    build on one another: an entry whose class does not import has nothing else worth saying
    about it.

    Args:
        model: The energy system to check, normally the expanded one.

    Returns:
        One failure per failing entry, in file order; empty when the whole file binds.
    """
    binder = ClassBinder(model)
    failures: List[BindingFailure] = []
    for name, entry in model.all_components().items():
        try:
            binder.bind_entry(name, entry)
        except EnergySystemCatalogueError as error:
            failures.append(BindingFailure(name=name, error_id=error.error_id, message=str(error)))
    return tuple(failures)
