"""The binding rules of an energy-system file: what its entries must obey against the classes they name.

Declarative mode only, stage ``BUILD``. Two subjects. ``BoundDocument`` is the expanded file with its entries bound to
their classes (``hisim/energy_system/classes.py:ClassBinder``), handed over before any configuration is built: a class
path that imports a configurable component (``EF-10``), a declared preset (``EF-13``) or constructor (``EF-15``), the
constructor's arguments (``EF-16``), the ``config`` keys (``EF-17``), ``AUTO`` only on a sized field (``EF-1B``) and the
``sizing_sources`` facts (``EF-43``). ``ConfiguredDocument`` is the file with its configurations built, handed over
before the sizing rules judge them: a value that decodes into its field (``EF-1A``) and a path variable that is
registered (``EF-04``). The binder and the configurator resolve and record; these rules judge what they recorded and
what the classes declare.

Every fault keeps the catalogue message the binding error carried before the checks moved, ``EF-nn at <location>:
<problem>``, with the source of a component an import produced, and the registry raises them together as
``EnergySystemClassBindingError``, a binding error. A rule of one entry checks only the entries its question has an
answer for, so that the faults of every entry come out at once: an entry whose class did not resolve has no binding
and is ``EF-10``'s alone, and the arguments of a constructor the class does not declare are not checked.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, List, Mapping, Optional, Sequence, Tuple, Type

from hisim.config.introspection import ConstructorInfo
from hisim.config.presets import constructors_of, presets_of
from hisim.config.sizing import _AutoSize, sizable_fields
from hisim.energy_system.bindings import facts_read_by
from hisim.energy_system.errors import CatalogueRefusal, EnergySystemClassBindingError, EnergySystemErrorId
from hisim.setup_rules.rule import Fault, RuleStage, SetupRule
from hisim.setup_rules.subjects import BoundDocument, ConfiguredDocument

if TYPE_CHECKING:
    from hisim.energy_system.assemblies.record import SourceMap
    from hisim.energy_system.bindings import ClassBinding


class CatalogueFaults:
    """How a rule of the energy-system format turns a catalogue message at a location into a fault.

    Shared by the binding rules of both subjects and the sizing rules (``hisim/setup_rules/sizing.py``): each holds a
    message of the error catalogue and the key path it names, and the fault carries the message with the source of a
    component an import produced, the component and the key path.
    """

    @staticmethod
    def fault(
        rule: Type[SetupRule[Any]],
        message: str,
        location: str,
        file: Optional[str],
        source_map: Optional[SourceMap],
        component: Optional[str] = None,
    ) -> Fault:
        """Return the fault of one catalogue message at a dotted location, from the rule given.

        Example: ``EF-13`` with the message ``EF-13 at components.boiler.preset: ...`` and the location
        ``components.boiler.preset`` gives a fault of ``component="boiler"``, ``key_path=("components", "boiler",
        "preset")`` and the message followed by the source of ``boiler`` when an import produced it, then the pointer
        to the rule. A location outside the components block, such as the stored path ``${nowhere}/weather.csv`` of an
        unregistered path variable, keeps an empty key path unless ``component`` names the entry.

        Args:
            rule: The rule whose fault it is.
            message: The catalogue message.
            location: The dotted location the message names.
            file: The file's path.
            source_map: Which import produced a component; ``None`` for a file without imports.
            component: The entry the fault is about, when the location does not name it.

        Returns:
            The fault.
        """
        if source_map is not None:
            message += source_map.suffix(location)
        steps = tuple(location.split("."))
        if len(steps) > 1 and steps[0] == "components":
            return rule.fault(message, component=steps[1], key_path=steps, file=file)
        key_path: Tuple[str, ...] = ("components", component) if component is not None else ()
        return rule.fault(message, component=component, key_path=key_path, file=file)


class BindingRule(SetupRule[BoundDocument]):
    """The shared base of the binding rules of the bound file: the subject they read, the error they raise and how a refusal becomes a fault.

    It declares no ``CODE``, so it is no rule itself.
    """

    VIOLATION = EnergySystemClassBindingError

    @classmethod
    def refuse(cls, subject: BoundDocument, refusal: CatalogueRefusal) -> Fault:
        """Return the fault of one refusal, ``EF-13 at components.boiler.preset: ...`` located at ``boiler``."""
        return CatalogueFaults.fault(cls, refusal.message, refusal.location, subject.file, subject.source_map)

    @staticmethod
    def location_of(binding: ClassBinding) -> str:
        """Return the key path of an entry, ``components.boiler`` for the binding of ``boiler``."""
        return f"components.{binding.name}"


class ClassImportable(BindingRule):
    """What it checks: every entry's class path imports a HiSim component whose constructor annotates its ``config`` parameter with a configuration dataclass.

    Why: a dotted path is data in a file, so a wrong one has to be a rejection naming the entry rather than a bare
    ``ModuleNotFoundError`` from inside an import; and an entry names the component, never its configuration class,
    whose annotation is the one place the pairing is written down.

    Example of a fault: ``EF-10 at components.ghost.class: the module 'hisim.components.no_such_module' of 'ghost'
    cannot be imported: No module named 'hisim.components.no_such_module'.``.

    How to fix: name a component class of an existing module (the message lists the component classes of a module
    that does import); a component without an annotated configuration needs that annotation in its code.
    """

    CODE = "EF-10"
    NAME = "class-importable"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BoundDocument) -> Sequence[Fault]:
        """Return a fault per entry whose class the binder could not resolve, in file order."""
        return [self.refuse(subject, refusal) for refusal in subject.bindings.refusals if refusal.error_id.value == self.CODE]


class KnownPreset(BindingRule):
    """What it checks: the preset an entry names is declared by its configuration class.

    Why: a preset is a named default of the class; a name it does not declare configures nothing, and the classes
    that declare no preset at all are the ones still waiting for their conversion.

    Example of a fault: ``EF-13 at components.boiler.preset: 'boiler' names the preset 'condensing_gaz', which
    GenericBoilerConfig does not declare. Did you mean: condensing_gas? Valid presets of GenericBoilerConfig: ...``.

    How to fix: name a preset the message lists, or write a complete ``config`` block when the class declares none.
    """

    CODE = "EF-13"
    NAME = "known-preset"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BoundDocument) -> Sequence[Fault]:
        """Return a fault per entry naming a preset its configuration class does not declare."""
        faults = []
        for binding in subject.bindings:
            preset = binding.entry.preset
            if preset is None or preset in presets_of(binding.config_class):
                continue
            class_name = binding.config_class.__name__
            known = tuple(info.name for info in binding.description.presets)
            refusal = CatalogueRefusal(
                EnergySystemErrorId.UNKNOWN_PRESET,
                f"{self.location_of(binding)}.preset",
                f"'{binding.name}' names the preset '{preset}', which "
                f"{class_name} does not declare"
                + ("." if known else "; the class declares no preset at all."),
                alternatives=known,
                alternatives_label=f"presets of {class_name}",
                offending_value=preset,
                remedy=None if known else "Write a complete 'config' block instead.",
            )
            faults.append(self.refuse(subject, refusal))
        return faults


class KnownConstructor(BindingRule):
    """What it checks: the named constructor an entry calls is declared by its configuration class.

    Why: a constructor is a named builder of the class with parameters; a name it does not declare builds nothing.

    Example of a fault: ``EF-15 at components.building.constructor: 'building' calls the constructor 'for_tabula',
    which BuildingConfig does not declare. Did you mean: for_tabula_code? Valid constructors of BuildingConfig:
    ...``.

    How to fix: call a constructor the message lists, or use a preset or a complete ``config`` block when the class
    declares none.
    """

    CODE = "EF-15"
    NAME = "known-constructor"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BoundDocument) -> Sequence[Fault]:
        """Return a fault per entry calling a constructor its configuration class does not declare."""
        faults = []
        for binding in subject.bindings:
            call = binding.entry.constructor
            if call is None or call.name in constructors_of(binding.config_class):
                continue
            class_name = binding.config_class.__name__
            declared = binding.description.constructors
            refusal = CatalogueRefusal(
                EnergySystemErrorId.UNKNOWN_CONSTRUCTOR,
                f"{self.location_of(binding)}.constructor",
                f"'{binding.name}' calls the constructor '{call.name}', which "
                f"{class_name} does not declare"
                + ("." if declared else "; the class declares none at all."),
                alternatives=tuple(info.name for info in declared),
                alternatives_label=f"constructors of {class_name}",
                offending_value=call.name,
                remedy=None if declared else "Use a preset or a complete 'config' block.",
            )
            faults.append(self.refuse(subject, refusal))
        return faults


class ConstructorArguments(BindingRule):
    """What it checks: a call of a declared constructor passes no argument the constructor lacks and every one it requires.

    Why: the arguments go straight into a Python call; an unknown one would be a ``TypeError`` from inside the
    builder, and a missing one a builder that cannot run, neither naming the entry or the parameters it has.

    Example of a fault: ``EF-16 at components.building.constructor.for_tabula_code: the constructor 'for_tabula_code'
    of 'building' got the unknown argument tabula_code. Did you mean: building_code? Valid parameters: building_code:
    str, ...``.

    How to fix: pass the parameters the message lists, by those names, and every one without a default.
    """

    CODE = "EF-16"
    NAME = "constructor-arguments"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BoundDocument) -> Sequence[Fault]:
        """Return a fault per entry passing an unknown argument, and one per entry leaving out a required one."""
        faults: List[Fault] = []
        for binding in subject.bindings:
            call = binding.entry.constructor
            if call is None or call.name not in constructors_of(binding.config_class):
                continue
            info = next(item for item in binding.description.constructors if item.name == call.name)
            location = f"{self.location_of(binding)}.constructor.{call.name}"
            faults.extend(self.refuse(subject, refusal) for refusal in self.refusals(call.name, dict(call.arguments), info, location, binding.name))
        return faults

    @classmethod
    def refusals(cls, constructor_name: str, arguments: Mapping[str, Any], info: ConstructorInfo, location: str, name: str) -> List[CatalogueRefusal]:
        """Return the refusals of one constructor call: one for its unknown arguments, one for its missing required ones.

        Example: ``for_tabula_code`` of ``building`` called with ``tabula_code`` only gives two refusals, the unknown
        ``tabula_code`` and the missing ``building_code``, each listing the parameters with their types and defaults.

        Args:
            constructor_name: The wire name of the constructor being called.
            arguments: The arguments the file passes.
            info: The described parameters of that constructor.
            location: The call's key path, for the message.
            name: The component's name, for the message.

        Returns:
            The refusals; empty for a call that fits the parameters.
        """
        parameters = {parameter.name for parameter in info.parameters}
        rendered = cls.render_parameters(info)
        refusals = []
        unknown = [argument for argument in arguments if argument not in parameters]
        if unknown:
            refusals.append(
                CatalogueRefusal(
                    EnergySystemErrorId.CONSTRUCTOR_ARGUMENT,
                    location,
                    f"the constructor '{constructor_name}' of '{name}' got the unknown "
                    f"argument{'s' if len(unknown) > 1 else ''} {', '.join(sorted(unknown))}.",
                    alternatives=rendered,
                    alternatives_label="parameters",
                    offending_value=unknown[0],
                )
            )
        missing = [
            parameter.name
            for parameter in info.parameters
            if parameter.default is dataclasses.MISSING and parameter.name not in arguments
        ]
        if missing:
            refusals.append(
                CatalogueRefusal(
                    EnergySystemErrorId.CONSTRUCTOR_ARGUMENT,
                    location,
                    f"the constructor '{constructor_name}' of '{name}' needs the "
                    f"argument{'s' if len(missing) > 1 else ''} {', '.join(missing)}, which the "
                    "entry does not pass.",
                    alternatives=rendered,
                    alternatives_label="parameters",
                )
            )
        return refusals

    @staticmethod
    def render_parameters(info: ConstructorInfo) -> Tuple[str, ...]:
        """Renders a constructor's parameters as ``name: type`` or ``name: type = default``.

        Example: ``building_code: str`` for a parameter without a default, ``year: int = 2021`` for one with.

        Args:
            info: The described constructor.

        Returns:
            One rendered parameter per entry, in declaration order.
        """
        rendered: List[str] = []
        for parameter in info.parameters:
            text = f"{parameter.name}: {parameter.type_name}"
            if parameter.default is not dataclasses.MISSING:
                text += f" = {parameter.default!r}"
            rendered.append(text)
        return tuple(rendered)


class KnownConfigField(BindingRule):
    """What it checks: every key of an entry's ``config`` block is a settable field of its configuration class, and ``component_id`` is not set there.

    Why: a key that names no field would be ignored or crash the class; ``component_id`` is a field but not the
    file's to write, since the entry's key is the component's whole identity and a second spelling would make the
    file contradict itself.

    Example of a fault: ``EF-17 at components.boiler.config.seasonal_eficiency: 'boiler' sets 'seasonal_eficiency',
    which is no field of GenericBoilerConfig. Did you mean: seasonal_efficiency? Valid fields of GenericBoilerConfig:
    ...``.

    How to fix: name a field the message lists; delete a ``component_id`` line, the identity comes from the entry key.
    """

    CODE = "EF-17"
    NAME = "known-config-field"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BoundDocument) -> Sequence[Fault]:
        """Return a fault per ``config`` key that is ``component_id`` or no settable field, in file order."""
        faults = []
        for binding in subject.bindings:
            class_name = binding.config_class.__name__
            settable = tuple(info.name for info in binding.description.fields if info.name != "component_id")
            location = self.location_of(binding)
            for key in binding.entry.config:
                if key == "component_id":
                    refusal = CatalogueRefusal(
                        EnergySystemErrorId.UNKNOWN_CONFIG_FIELD,
                        f"{location}.config.component_id",
                        f"'{binding.name}' sets 'component_id' in its config block; the entry's key is "
                        "the component's identity and the only place it is written.",
                        remedy="Delete the line; the identity comes from the entry key.",
                    )
                elif key not in settable:
                    refusal = CatalogueRefusal(
                        EnergySystemErrorId.UNKNOWN_CONFIG_FIELD,
                        f"{location}.config.{key}",
                        f"'{binding.name}' sets '{key}', which is no field of {class_name}.",
                        alternatives=settable,
                        alternatives_label=f"fields of {class_name}",
                        offending_value=key,
                    )
                else:
                    continue
                faults.append(self.refuse(subject, refusal))
        return faults


class AutoOnlyOnASizedField(BindingRule):
    """What it checks: ``AUTO`` is written only on a field its configuration class sizes by a law.

    Why: ``AUTO`` re-opens a field for the sizing to fill in, which only means something where a law can close it
    again; on any other field nothing would ever fill it in.

    Example of a fault: ``EF-1B at components.boiler.config.seasonal_efficiency: 'boiler' sets 'seasonal_efficiency'
    to AUTO, but GenericBoilerConfig declares no sizing law for that field, so nothing would ever fill it in. Valid
    sizable fields of GenericBoilerConfig: maximal_thermal_power_in_watt, ...``.

    How to fix: write a value on that field, or ``AUTO`` on one of the sizable fields the message lists.
    """

    CODE = "EF-1B"
    NAME = "auto-only-on-a-sized-field"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BoundDocument) -> Sequence[Fault]:
        """Return a fault per settable ``config`` key set to ``AUTO`` without a sizing law, in file order."""
        faults = []
        for binding in subject.bindings:
            class_name = binding.config_class.__name__
            settable = {info.name for info in binding.description.fields if info.name != "component_id"}
            laws = sizable_fields(binding.config_class)
            for key, value in binding.entry.config.items():
                if key not in settable or value != _AutoSize.WIRE_SPELLING or key in laws:
                    continue
                refusal = CatalogueRefusal(
                    EnergySystemErrorId.AUTO_ON_CONCRETE_FIELD,
                    f"{self.location_of(binding)}.config.{key}",
                    f"'{binding.name}' sets '{key}' to AUTO, but {class_name} declares no "
                    f"sizing law for that field, so nothing would ever fill it in.",
                    alternatives=tuple(laws),
                    alternatives_label=f"sizable fields of {class_name}",
                    offending_value=key,
                )
                faults.append(self.refuse(subject, refusal))
        return faults


class SizingFactIsRead(BindingRule):
    """What it checks: every key of an entry's ``sizing_sources`` block is a fact its configuration class reads.

    Why: a source line answers where a component takes a fact from; for a fact the class never reads it would be
    silently ignored, and the author would keep believing the number came from where the line says. The facts a
    class reads are those its sizing laws read and those its fact contributions need.

    Example of a fault: ``EF-43 at components.boiler.sizing_sources.conditioned_floor_area_in_m2: 'boiler' names a
    source for 'conditioned_floor_area_in_m2', which GenericBoilerConfig never reads. Valid facts read by
    GenericBoilerConfig: heating_load_in_watt, number_of_apartments.``.

    How to fix: name a fact the message lists, or delete the line (the block, when the class reads no fact).
    """

    CODE = "EF-43"
    NAME = "sizing-fact-is-read"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BoundDocument) -> Sequence[Fault]:
        """Return a fault per ``sizing_sources`` key naming a fact the class does not read, in file order."""
        faults = []
        for binding in subject.bindings:
            class_name = binding.config_class.__name__
            readable = facts_read_by(binding.config_class)
            for fact in binding.entry.sizing_sources:
                if fact in readable:
                    continue
                refusal = CatalogueRefusal(
                    EnergySystemErrorId.FACT_NOT_READ,
                    f"{self.location_of(binding)}.sizing_sources.{fact}",
                    f"'{binding.name}' names a source for '{fact}', which {class_name} "
                    "never reads"
                    + ("." if readable else "; the class reads no sizing fact at all."),
                    alternatives=readable,
                    alternatives_label=f"facts read by {class_name}",
                    offending_value=fact,
                    remedy=None if readable else "Delete the block.",
                )
                faults.append(self.refuse(subject, refusal))
        return faults


class ConfigurationRule(SetupRule[ConfiguredDocument]):
    """The shared base of the binding rules of the configured file: they refuse the entries whose configuration could not be built.

    It declares no ``CODE``, so it is no rule itself. Building an entry's configuration stops at the entry's first
    value that cannot be decoded or path that cannot be expanded, and the configurator records that error instead of
    raising it (``hisim/energy_system/configure.py:EntryRefusal``); each rule refuses the recorded errors of its code.
    """

    VIOLATION = EnergySystemClassBindingError

    def check(self, subject: ConfiguredDocument) -> Sequence[Fault]:
        """Return a fault per entry whose configuration could not be built for this rule's reason, in file order."""
        return [
            CatalogueFaults.fault(type(self), str(refusal.error), refusal.error.location, subject.file, subject.source_map, component=refusal.name)
            for refusal in subject.outcome.refusals
            if refusal.error.error_id.value == self.CODE
        ]


class DecodableValue(ConfigurationRule):
    """What it checks: every value an entry writes decodes into its field's or parameter's type, and the builder accepts the values.

    The values are those of the entry's ``config`` block and its constructor's arguments.

    Why: a file can only write strings, numbers and mappings; a string where a number belongs, a misspelled enum
    member or a builder refusing its arguments would otherwise surface far from the file, or as a plausible number
    nobody questions.

    Example of a fault: ``EF-1A at components.boiler.config.seasonal_efficiency: 'boiler' sets 'seasonal_efficiency'
    to 'very high', which is not a float.``.

    How to fix: write the value in the type the message names; for an enum, one of the members it lists.
    """

    CODE = "EF-1A"
    NAME = "decodable-value"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class PathVariableKnown(ConfigurationRule):
    """What it checks: every ``${name}`` in a path-valued field or argument names a path variable this machine registers.

    Why: a file spells its paths through variables so that it runs on any machine; a variable nobody registered
    leaves a path that points nowhere.

    Example of a fault: ``EF-04 at ${nowhere}/weather.csv: the path reference '${nowhere}' names no registered path
    variable. Valid path variables: cache, inputs, ...``.

    How to fix: write one of the variables the message lists, or register the variable on the path resolver before
    the file is loaded.
    """

    CODE = "EF-04"
    NAME = "path-variable-known"
    STAGE = RuleStage.BUILD
    ENFORCED = True
