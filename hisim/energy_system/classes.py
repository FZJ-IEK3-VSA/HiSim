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

The binder here resolves; it judges nothing. What it cannot resolve — a class path that does not
import to a configurable component — it records, and the binding rules of the setup rules
(:mod:`hisim.setup_rules.bindings`) refuse it together with every preset, constructor, argument,
``config`` key and sizing fact the classes do not declare, every fault of the file at once.

The stage returns what it learned rather than throwing it away. A :class:`ClassBindings`
holds, per entry, the component class, the configuration class, its machine-readable
description and the builder the entry named, so the stages that follow — building the
configurations, resolving the sizing, constructing the components — never import or
introspect the same class twice.
"""

from __future__ import annotations

import dataclasses
import importlib
import typing
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from hisim.component import Component
from hisim.config.introspection import describe_config
from hisim.config.presets import constructors_of, presets_of
from hisim.energy_system.bindings import (
    BindingFailure,
    ClassBinding,
    ClassBindings,
)
from hisim.energy_system.errors import (
    CatalogueRefusal,
    EnergySystemBindingError,
    EnergySystemErrorId,
)
from hisim.energy_system.model import ComponentEntry, EnergySystemFile
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleStage, SetupRuleViolation
from hisim.setup_rules.subjects import BoundDocument

if TYPE_CHECKING:
    from hisim.energy_system.assemblies.record import SourceMap


class ClassBinder:
    """Resolves the classes of one energy-system file, entry by entry, and records what it cannot resolve.

    Built for a single file and used once. Per entry it imports the component class, finds the
    configuration class the component takes, describes it and looks up the preset or constructor the
    entry names. An entry whose class path does not resolve to a configurable component gets no
    binding: its refusal (``EF-10``) is recorded instead of raised, and the binding rules
    (:mod:`hisim.setup_rules.bindings`) refuse it with every other fault of the file. A preset or a
    constructor the class does not declare is left unresolved, for the same rules to name.
    """

    def __init__(self, model: EnergySystemFile) -> None:
        """Prepares the binder for one file.

        Args:
            model: The energy system to bind, normally after group expansion so that the
                classes of a switched-off add-on are never imported.
        """
        self.model = model

    def bind(self) -> ClassBindings:
        """Resolves every entry and returns the bindings, with the refusals of the entries that did not resolve.

        Returns:
            The bindings in file order, and the refusals in file order.
        """
        bindings: Dict[str, ClassBinding] = {}
        refusals: List[CatalogueRefusal] = []
        for name, entry in self.model.all_components().items():
            bound = self.bind_entry(name, entry)
            if isinstance(bound, CatalogueRefusal):
                refusals.append(bound)
            else:
                bindings[name] = bound
        return ClassBindings(bindings, refusals)

    def bind_entry(self, name: str, entry: ComponentEntry) -> Union[ClassBinding, CatalogueRefusal]:
        """Resolves one entry against its component and configuration classes.

        Example: ``boiler`` naming ``hisim.components.generic_boiler.GenericBoiler`` with the preset
        ``condensing_gas`` gives a binding whose ``preset`` is that builder; with the preset
        ``condensing_gaz`` its ``preset`` is ``None``, which ``EF-13`` refuses.

        Args:
            name: The component's name, which is the entry's key and its identity.
            entry: The entry to resolve.

        Returns:
            The binding for that entry, or the refusal (``EF-10``) when its class path does not
            import to a component whose constructor annotates a configuration dataclass.
        """
        classes = self.resolve_classes(name, entry)
        if isinstance(classes, CatalogueRefusal):
            return classes
        component_class, config_class = classes
        return ClassBinding(
            name=name,
            entry=entry,
            component_class=component_class,
            config_class=config_class,
            description=describe_config(config_class),
            preset=presets_of(config_class).get(entry.preset) if entry.preset is not None else None,
            constructor=constructors_of(config_class).get(entry.constructor.name) if entry.constructor is not None else None,
        )

    @classmethod
    def config_class_of(cls, name: str, entry: ComponentEntry) -> type:
        """Resolves the configuration class one entry's component takes, and checks nothing else.

        :meth:`bind_entry` answers the same question on the way to a great deal more — presets,
        constructors, descriptions — which the binding rules then judge. A caller that only wants
        to know how the configuration class declares its fields, such as the realizer restoring a
        patched ``config`` block to the order every writer emits it in, should neither pay for
        those steps nor be stopped by a rule, so the two steps that answer it alone are exposed here.

        Args:
            name: The component's name, which is the entry's key, for the message.
            entry: The entry naming the component class.

        Returns:
            The configuration dataclass the component's ``__init__`` annotates.

        Raises:
            EnergySystemBindingError: ``EF-10`` when the class path does not import to a component,
                or the component does not annotate its ``config`` parameter with a configuration
                dataclass.
        """
        classes = cls.resolve_classes(name, entry)
        if isinstance(classes, CatalogueRefusal):
            raise classes.error(EnergySystemBindingError) from classes.cause
        return classes[1]

    @classmethod
    def resolve_classes(cls, name: str, entry: ComponentEntry) -> Union[Tuple[type, type], CatalogueRefusal]:
        """Returns the component class an entry names and the configuration class it takes, or the refusal of either.

        Args:
            name: The component's name, for the message.
            entry: The entry naming the component class.

        Returns:
            The component class and the configuration class, or the ``EF-10`` refusal.
        """
        location = f"components.{name}"
        component_class = cls._import_component_class(entry.class_path, location, name)
        if isinstance(component_class, CatalogueRefusal):
            return component_class
        config_class = cls._config_class_of(component_class, location, name)
        if isinstance(config_class, CatalogueRefusal):
            return config_class
        return component_class, config_class

    @classmethod
    def _import_component_class(cls, class_path: str, location: str, name: str) -> Union[type, CatalogueRefusal]:
        """Imports the dotted class path of one entry and checks that it names a component.

        Args:
            class_path: The dotted path written under ``class``.
            location: The entry's key path, for the message.
            name: The component's name, for the message.

        Returns:
            The imported class, or the refusal ``EF-10`` if the path has no module part, if the
            module cannot be imported, if the module has no such attribute, or if the attribute is
            not a :class:`~hisim.component.Component` subclass.
        """
        if "." not in class_path:
            return CatalogueRefusal(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"'{class_path}' is not a dotted path; a class is named "
                "'<module>.<ClassName>'.",
            )
        module_path, class_name = class_path.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
        except ImportError as error:
            return CatalogueRefusal(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"the module '{module_path}' of '{name}' cannot be imported: {error}.",
                cause=error,
            )
        imported = getattr(module, class_name, None)
        if imported is None:
            return CatalogueRefusal(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"the module '{module_path}' has no class '{class_name}'.",
                alternatives=cls._component_classes_of(module),
                alternatives_label="component classes in that module",
                offending_value=class_name,
            )
        if not (isinstance(imported, type) and issubclass(imported, Component)):
            return CatalogueRefusal(
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
    def _config_class_of(cls, component_class: type, location: str, name: str) -> Union[type, CatalogueRefusal]:
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
            The configuration class, or the refusal ``EF-10`` if the annotation is missing or
            unresolvable.
        """
        try:
            hints = typing.get_type_hints(component_class.__init__)
        except Exception as error:  # pylint: disable=broad-except
            return CatalogueRefusal(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"the constructor annotations of {component_class.__name__} cannot be "
                f"resolved, so the configuration class of '{name}' is unknown: {error}.",
                cause=error,
            )
        config_class = hints.get("config")
        if not isinstance(config_class, type) or not dataclasses.is_dataclass(config_class):
            return CatalogueRefusal(
                EnergySystemErrorId.CLASS_NOT_IMPORTABLE,
                f"{location}.class",
                f"{component_class.__name__} does not annotate its constructor's 'config' "
                f"parameter with a configuration dataclass, so '{name}' cannot be "
                "configured from a file.",
            )
        return config_class


def validate_classes(model: EnergySystemFile, *, file: Optional[str] = None, source_map: Optional[SourceMap] = None) -> ClassBindings:
    """Checks an energy-system file against the component classes it names.

    The class-bound half of validation and the fourth stage of the lifecycle: it imports every
    entry's component class, finds the configuration class that component takes, and runs the
    binding rules of the setup rules (:mod:`hisim.setup_rules.bindings`) on the result, which
    verify that the preset, the named constructor and its arguments, the ``config`` keys and the
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
        file: The file's path, for the faults to name; ``None`` for a model built in Python.
        source_map: Which import produced a component, for a fault to name it; ``None`` for a file
            without imports.

    Returns:
        The bindings, in file order, for the stages that follow.

    Raises:
        EnergySystemClassBindingError: Listing every fault of every entry that contradicts its
            classes, each naming the entry, the class and — wherever the set of valid values is
            closed — that set; raised from the ``ImportError`` of the first class path that did
            not import, if one did not.
    """
    bindings = ClassBinder(model).bind()
    try:
        SetupRuleRegistry.collect().run(RuleStage.BUILD, BoundDocument(model=model, bindings=bindings, file=file, source_map=source_map), model.setup_kind)
    except SetupRuleViolation as violation:
        cause = next((refusal.cause for refusal in bindings.refusals if refusal.cause is not None), None)
        if cause is None:
            raise
        raise violation from cause
    return bindings


def collect_class_failures(model: EnergySystemFile) -> Tuple[BindingFailure, ...]:
    """Binds every entry and returns, per entry that the binding rules refuse, its first fault, in file order.

    Where :func:`validate_classes` refuses the file with every fault, this reports one verdict
    per entry. That is what a discoverability surface wants — "these six components of your
    file cannot be built yet, and here is why each one cannot" — and it is what lets a test pin
    the exact set of entries a file currently fails on, so that the set shrinks visibly as
    component classes gain their presets, constructors and laws.

    The verdict of an entry is its fault of the lowest code: an entry whose class does not
    import (``EF-10``) has nothing else worth saying about it, and an unknown preset (``EF-13``)
    explains more than a sizing fact the class does not read (``EF-43``).

    Args:
        model: The energy system to check, normally the expanded one.

    Returns:
        One failure per failing entry, in file order; empty when the whole file binds.
    """
    bindings = ClassBinder(model).bind()
    findings = SetupRuleRegistry.collect().findings(RuleStage.BUILD, BoundDocument(model=model, bindings=bindings), model.setup_kind)
    first: Dict[str, BindingFailure] = {}
    for fault in findings.faults:
        if fault.component is not None and fault.component not in first:
            first[fault.component] = BindingFailure(name=fault.component, error_id=EnergySystemErrorId(fault.code), message=fault.message)
    return tuple(first[name] for name in model.all_components() if name in first)
