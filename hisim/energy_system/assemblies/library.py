"""The library check: every rule an assembly file must obey, every problem listed at once.

``assemblies_spec.md`` §2.6, §3.3 (the contract check), §9.3 and §9.4. The reader refuses a file
whose shape is wrong at its first problem; this check takes a well-formed file and lists **every**
problem it has, each with its file and line, and refuses the file as a whole (``EF-75``). The library
test and the expansion run the same check: the expansion runs it on every assembly it imports.

It covers the names (the file's ``name`` is its library path, no parameter takes a reserved name),
the parameters (description, default, and for a number a unit of ``lt.Units`` and a range), the units
of the parameters against the config fields they feed (D16 b: ``EF-78`` for a mismatch, ``EF-79``
for a fed field declaring none), that every parameter does something and every enum value selects
something of its own, the ``exactly_one_of`` constraints, that the variants partition their selector,
that every port names members that exist in every option where it can be active, the placeholders,
the display templates, and the test contract. What needs the constructed components — default
connections, inputs, outputs, KPIs — is checked where they are constructed, by the wiring stage.
"""

from __future__ import annotations

import string
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from hisim import loadtypes as lt
from hisim.config.sizing import declared_field_unit
from hisim.energy_system.assemblies.model import AssemblyFile, MemberTemplate, ParameterType
from hisim.energy_system.assemblies.parameters import ParameterChecks
from hisim.energy_system.assemblies.resolver import ResolvedAssembly
from hisim.energy_system.classes import ClassBinder
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.imports_model import ImportEntry, ObservesPlaceholder, ParameterReference, Port, PortKind


class LibraryChecker:
    """Lists every problem of one assembly file."""

    #: A value of each parameter type, which a ``display:`` template's format spec must accept.
    DISPLAY_SAMPLES: ClassVar[Mapping[ParameterType, Any]] = {
        ParameterType.FLOAT: 1.5,
        ParameterType.INT: 1,
        ParameterType.BOOL: True,
        ParameterType.STRING: "text",
    }

    def __init__(self, assembly: ResolvedAssembly) -> None:
        """Prepares the check of one resolved assembly."""
        self.assembly = assembly
        self.model: AssemblyFile = assembly.model
        self.problems: List[str] = []
        self._configs: Dict[str, Optional[type]] = {}
        #: Parameter to how it is used: ``value`` (passed into a field, a preset or a display),
        #: ``variant:<name>`` or ``condition:<port>.<key>``.
        self.uses: Dict[str, Set[str]] = {name: set() for name in self.model.parameters}
        #: Variant to the members the options of every other variant write.
        self.elsewhere: Dict[str, Set[str]] = {
            variant.name: {
                name
                for other in self.model.variants.values()
                if other is not variant
                for option in other.options.values()
                for name in option.components
            }
            for variant in self.model.variants.values()
        }

    def add(self, path: Sequence[Any], problem: str) -> None:
        """Records one problem at a key path of the file."""
        self.problems.append(f"{self.assembly.lines.location(*path).text}: {problem}")

    def check(self) -> List[str]:
        """Runs every rule and returns the problems."""
        if self.model.name != self.assembly.path:
            self.add(("name",), f"the assembly is named '{self.model.name}' but lives at '{self.assembly.path}'.")
        self._parameters()
        self._constraints()
        self._variants()
        self._members()
        self._ports()
        self._dead_parameters()
        self._tests()
        return self.problems

    def config_class(self, member: MemberTemplate) -> Optional[type]:
        """The configuration class of a member's class, or ``None`` after recording why it does not import."""
        class_path = member.entry.class_path
        if class_path not in self._configs:
            try:
                self._configs[class_path] = ClassBinder.config_class_of(member.name, member.entry)
            # A module may raise anything at import; it is one problem of the file, and the listing goes on.
            except Exception as error:  # pylint: disable=broad-exception-caught
                self.add(member.source_path + ("class",), f"the class of '{member.name}' does not load: {error}")
                self._configs[class_path] = None
        return self._configs[class_path]

    def _parameters(self) -> None:
        """Types, units, values, ranges, defaults and descriptions (§2.6, D24)."""
        for name, declaration in self.model.parameters.items():
            path = ("parameters", name)
            numeric = declaration.type.is_numeric
            if name in ImportEntry.RESERVED_KEYS:
                self.add(path, f"'{name}' is reserved beside an instance's parameters and cannot name a parameter.")
            if not declaration.description:
                self.add(path, f"the parameter '{name}' has no description.")
            if not declaration.has_default:
                self.add(path, f"the parameter '{name}' has no default (D24: every parameter has one).")
            elif ParameterChecks.problem(declaration, declaration.default) is not None:
                self.add(
                    path + ("default",),
                    f"the default of '{name}' is {ParameterChecks.problem(declaration, declaration.default)}.",
                )
            if numeric and declaration.unit is None:
                self.add(path, f"the numeric parameter '{name}' has no unit; write a member of lt.Units.")
            if declaration.unit is not None and declaration.unit not in lt.Units.__members__:
                self.add(path + ("unit",), f"the unit '{declaration.unit}' of '{name}' is no member of lt.Units.")
            if numeric and declaration.range is None:
                self.add(path, f"the numeric parameter '{name}' has no range (D24: the box it is tested over).")
            if not numeric and (declaration.unit is not None or declaration.range is not None):
                self.add(
                    path,
                    f"the {declaration.type.value} parameter '{name}' carries a unit or a range; only a number does.",
                )
            if (declaration.type == ParameterType.ENUM) != (declaration.values is not None):
                self.add(path, f"the parameter '{name}': an enum, and only an enum, declares its 'values'.")
            for value in declaration.values or ():
                if not isinstance(value, str):
                    self.add(path + ("values",), f"the value {value!r} of the enum '{name}' is not a string.")
                elif declaration.type.written(value) is None:
                    self.add(path + ("values",), f"the enum '{name}' lists 'none', which spells no value.")

    def _constraints(self) -> None:
        """Every ``exactly_one_of`` names declared parameters, and the defaults satisfy it."""
        defaults = {name: declaration.default for name, declaration in self.model.parameters.items()}
        for index, names in enumerate(self.model.exactly_one_of):
            unknown = [name for name in names if name not in self.model.parameters]
            if unknown:
                self.add(("constraints", index), f"exactly_one_of names {', '.join(unknown)}, which are no parameters.")
            elif ParameterChecks.violation(names, defaults) is not None:
                self.add(
                    ("constraints", index), f"the defaults violate it: {ParameterChecks.violation(names, defaults)}."
                )

    def _variants(self) -> None:
        """Every internal variant partitions its selector's values; one member name, one class (§2.6)."""
        classes: Dict[str, str] = {name: member.entry.class_path for name, member in self.model.components.items()}
        variant_of: Dict[str, str] = {}
        for name, variant in self.model.variants.items():
            path = ("variants", name)
            declaration = self.model.parameters.get(variant.selected_by)
            allowed = declaration.allowed_values if declaration is not None else None
            if declaration is None or allowed is None:
                self.add(
                    path + ("selected_by",),
                    f"the variant '{name}' is selected by '{variant.selected_by}', which is no enum or bool parameter.",
                )
                continue
            if declaration.default is None or declaration.default == ParameterChecks.AUTO_SPELLING:
                self.add(
                    ("parameters", variant.selected_by, "default"),
                    f"the parameter '{variant.selected_by}' selects the variant '{name}', so its default is one of "
                    f"its values, not {declaration.default!r}.",
                )
            self.uses[variant.selected_by].add(f"variant:{name}")
            covered: Dict[Any, str] = {}
            for option_name, option in variant.options.items():
                for value in option.when:
                    if declaration.admits(value) and value not in covered:
                        covered[value] = option_name
                    else:
                        self.add(
                            path + ("options", option_name, "when"),
                            f"{variant.selected_by}={value!r} is not allowed or covered twice.",
                        )
                for member_name, member in option.components.items():
                    if member_name in self.model.components:
                        self.add(
                            member.source_path,
                            f"the option '{option_name}' writes '{member_name}', a member outside the variant.",
                        )
                    elif variant_of.setdefault(member_name, name) != name:
                        self.add(
                            member.source_path,
                            f"'{member_name}' is written in the options of the variants '{variant_of[member_name]}' "
                            f"and '{name}'; one name lives in one variant, or two selected options would make one "
                            "component of two.",
                        )
                    elif classes.setdefault(member_name, member.entry.class_path) != member.entry.class_path:
                        self.add(
                            member.source_path,
                            f"'{member_name}' is written with two classes; an option that swaps a member's class "
                            "names its own member.",
                        )
            missing = [value for value in allowed if value not in covered]
            if missing:
                self.add(
                    path,
                    f"no option covers {variant.selected_by}={', '.join(repr(value) for value in missing)}; the "
                    "when: lists partition its values.",
                )

    def _members(self) -> None:
        """References between members, parameter references, units, presets and display templates."""
        names = set(self.model.member_names())
        for member in self.model.all_members():
            entry, path = member.entry, member.source_path
            for item in entry.inputs:
                if item.source not in names:
                    self.add(
                        path + ("inputs",),
                        f"'{member.name}' takes an input from '{item.source}', which is no member; what crosses the "
                        "boundary crosses through a port.",
                    )
            for fact, reference in entry.sizing_references():
                if reference.component not in names:
                    self.add(
                        path + ("sizing_sources", fact),
                        f"'{member.name}' takes '{fact}' from '{reference.component}', which is no member.",
                    )
            trees = {
                "config": dict(entry.config),
                "constructor": dict(entry.constructor.arguments) if entry.constructor else {},
            }
            for root, tree in trees.items():
                for parameter, value_path in ParameterReference.walk(tree):
                    if parameter not in self.model.parameters:
                        self.add(
                            path + (root,) + value_path,
                            f"'{member.name}' refers to '{parameter}', which is no parameter.",
                        )
                        continue
                    self.uses[parameter].add("value")
                    if self.model.parameters[parameter].type.is_numeric:
                        self._unit(member, parameter, root, value_path)
            if member.preset_parameter is not None:
                declaration = self.model.parameters.get(member.preset_parameter)
                if declaration is None or declaration.type not in (ParameterType.ENUM, ParameterType.STRING):
                    self.add(
                        path + ("preset",),
                        f"'{member.name}' takes its preset from '{member.preset_parameter}', which is no enum or "
                        "string parameter.",
                    )
                else:
                    self.uses[member.preset_parameter].add("value")
            if member.display is not None:
                self._display(member)
            self.config_class(member)

    def _unit(self, member: MemberTemplate, parameter: str, root: str, value_path: Tuple[str, ...]) -> None:
        """A numeric parameter's unit against the config field it feeds (D16 b): ``EF-78``, ``EF-79``."""
        location = member.source_path + (root,) + value_path
        without = EnergySystemErrorId.FED_FIELD_WITHOUT_UNIT.value
        if root != "config" or len(value_path) != 1:
            self.add(
                location,
                f"{without}: the numeric parameter '{parameter}' feeds '{root}.{'.'.join(value_path)}' of "
                f"'{member.name}', which declares no unit; feed a top-level config field that declares one.",
            )
            return
        config_class = self.config_class(member)
        if config_class is None:
            return
        field_name = value_path[0]
        if field_name not in getattr(config_class, "__dataclass_fields__", {}):
            self.add(location, f"'{member.name}' sets '{field_name}', which is no field of {config_class.__name__}.")
            return
        unit = declared_field_unit(config_class, field_name)
        if unit is None:
            self.add(
                location,
                f"{without}: the parameter '{parameter}' feeds {config_class.__name__}.{field_name} (member "
                f"'{member.name}'), which declares no unit; declare it: sized_field(..., unit=lt.Units.<UNIT>) or "
                f"field(metadata={{'unit': lt.Units.<UNIT>}}).",
            )
        elif self.model.parameters[parameter].unit != unit.name:
            self.add(
                location,
                f"{EnergySystemErrorId.PARAMETER_UNIT_MISMATCH.value}: the parameter '{parameter}' is in "
                f"{self.model.parameters[parameter].unit}, but {config_class.__name__}.{field_name} (member "
                f"'{member.name}') is in {unit.name}; a unit is never converted.",
            )

    def _display(self, member: MemberTemplate) -> None:
        """A ``display:`` template names declared parameters and formats each by a spec its type accepts (§2.4)."""
        formatter = string.Formatter()
        path = member.source_path + ("display",)
        try:
            fields = [
                (field, spec, conversion) for _text, field, spec, conversion in formatter.parse(member.display or "")
            ]
        except ValueError as error:
            self.add(path, f"the display template of '{member.name}' is no format string: {error}.")
            return
        for field, spec, conversion in fields:
            if field is None:
                continue
            declaration = self.model.parameters.get(field)
            if declaration is None:
                self.add(path, f"the display template of '{member.name}' names '{field}', which is no parameter.")
                continue
            self.uses[field].add("value")
            sample = declaration.values[0] if declaration.values else self.DISPLAY_SAMPLES[declaration.type]
            try:
                formatter.format_field(formatter.convert_field(sample, conversion), spec or "")
            except (TypeError, ValueError) as error:
                self.add(
                    path,
                    f"the display template of '{member.name}' formats the {declaration.type.value} '{field}' with "
                    f"'{spec}', which does not fit it: {error}.",
                )

    def _ports(self) -> None:
        """Every port names members that exist where it can be active, and every placeholder names a port into it."""
        members = {member.name for member in self.model.all_members()}
        for name, port in self.model.ports.items():
            path: Tuple[Any, ...] = ("interface", port.section, name)
            for key in ("required_when", "active_when"):
                for parameter, values in getattr(port, key).items():
                    declaration = self.model.parameters.get(parameter)
                    if declaration is None or declaration.allowed_values is None:
                        self.add(
                            path + (key,),
                            f"the port '{name}' {key} names '{parameter}', which is no enum or bool parameter.",
                        )
                        continue
                    self.uses[parameter].add(f"condition:{name}.{key}")
                    for value in values:
                        if not declaration.admits(value):
                            self.add(
                                path + (key,),
                                f"the port '{name}' {key} lists {parameter}={value!r}, which it does not allow.",
                            )
            named = (
                port.into
                if port.kind == PortKind.NEED
                else (port.output_member,) if port.kind == PortKind.PROVIDED else port.members
            )
            for member_name in named:
                if member_name not in members:
                    self.add(path, f"the port '{name}' names '{member_name}', which is no member.")
                elif port.kind in (PortKind.NEED, PortKind.PROVIDED):
                    self._present_where_active(path, port, member_name)
        for member in self.model.all_members():
            for placed in member.entry.placeholders:
                placeholder = placed.placeholder
                port_name = placeholder.observer if isinstance(placeholder, ObservesPlaceholder) else placeholder.port
                target = self.model.ports.get(port_name)
                kind = PortKind.OBSERVER if isinstance(placeholder, ObservesPlaceholder) else PortKind.NEED
                if target is None or target.kind != kind or member.name not in (target.into or target.members):
                    self.add(
                        member.source_path + ("inputs", placed.position),
                        f"'{member.name}' carries a placeholder for '{port_name}', which is no {kind.value} port "
                        "lowering into it.",
                    )
        for name, port in self.model.ports.items():
            if port.kind == PortKind.NEED:
                for into in port.into:
                    holders = [m for m in self.model.all_members() if m.name == into]
                    if holders and not any(
                        any(getattr(p.placeholder, "port", None) == name for p in m.entry.placeholders) for m in holders
                    ):
                        self.add(
                            ("interface", port.section, name),
                            f"the port '{name}' lowers into '{into}', which carries no '{{$port: {name}}}' "
                            "placeholder.",
                        )

    def _present_where_active(self, path: Tuple[Any, ...], port: Port, member: str) -> None:
        """A port's member (a need's, a provided output's) exists in every option where the port can be active."""
        if member in self.model.components:
            return
        for variant in self.model.variants.values():
            elsewhere = self.elsewhere[variant.name]
            for option in variant.options.values():
                conditions = {**port.required_when, **port.active_when}
                values = conditions.get(variant.selected_by)
                active = values is None or any(value in option.when for value in values)
                if active and member not in option.components and member not in elsewhere:
                    self.add(
                        path,
                        f"the port '{port.name}' names '{member}', which the option '{option.name}' of the "
                        f"variant '{variant.name}' does not have, while the port can be active there; switch it off "
                        "with active_when.",
                    )

    def _dead_parameters(self) -> None:
        """Every parameter does something, and every value of a closed set selects something of its own (§2.6)."""
        for name, uses in self.uses.items():
            declaration = self.model.parameters[name]
            if not uses:
                self.add(
                    ("parameters", name),
                    f"the parameter '{name}' feeds no field, preset, variant, port condition or display template; a "
                    "knob with no effect is refused.",
                )
                continue
            if "value" in uses or declaration.allowed_values is None:
                continue
            seen: Dict[Tuple[Any, ...], Any] = {}
            for value in declaration.allowed_values:
                signature = tuple(self._effect(name, use, value) for use in sorted(uses))
                if signature in seen:
                    self.add(
                        ("parameters", name),
                        f"the value {value!r} of '{name}' selects nothing {seen[signature]!r} does not; a value with "
                        "no effect of its own is refused.",
                    )
                seen.setdefault(signature, value)

    def _effect(self, parameter: str, use: str, value: Any) -> Any:
        """What one use of a parameter makes of one of its values: the option selected, or the condition's truth."""
        kind, _, target = use.partition(":")
        if kind == "variant":
            option = self.model.variants[target].option_for(value)
            return option.name if option is not None else None
        port, _, key = target.partition(".")
        return value in getattr(self.model.ports[port], key)[parameter]

    def _tests(self) -> None:
        """The test contract (§9.4, D24): present, names that resolve, units of ``lt.Units``, a monotone entry."""
        tests = self.model.tests
        if tests is None:
            self.add(("kind",), "the assembly carries no test contract ('tests:' with bounds and monotone, §9.4).")
            return
        members = {member.name for member in self.model.all_members()}
        for index, bounds in enumerate(tests.bounds):
            path = ("tests", "bounds", index)
            if bounds.unit is not None and bounds.unit not in lt.Units.__members__:
                self.add(path, f"the unit '{bounds.unit}' is no member of lt.Units.")
            member = bounds.output.split(".", 1)[0] if bounds.output is not None else bounds.member
            if member is not None and member not in members:
                self.add(path, f"the bounds entry names the member '{member}', which does not exist.")
        numeric = [name for name, declaration in self.model.parameters.items() if declaration.type.is_numeric]
        if numeric and not tests.monotone:
            self.add(
                ("tests",),
                f"no monotone entry, but the assembly has the numeric parameters {', '.join(numeric)}; at least one "
                "is required.",
            )
        for index, monotone in enumerate(tests.monotone):
            path = ("tests", "monotone", index)
            declaration = self.model.parameters.get(monotone.parameter)
            if declaration is None or not declaration.type.is_numeric:
                self.add(path, f"the monotone entry moves '{monotone.parameter}', which is no numeric parameter.")
            if monotone.member is not None and monotone.member not in members:
                self.add(path, f"the monotone entry names the member '{monotone.member}', which does not exist.")
        for index, expect in enumerate(tests.expect):
            if expect.member is not None and expect.member not in members:
                self.add(
                    ("tests", "expect", index),
                    f"the expect entry names the member '{expect.member}', which does not exist.",
                )


def check_assembly(assembly: ResolvedAssembly) -> List[str]:
    """Lists every problem of one assembly; empty when it has none."""
    return LibraryChecker(assembly).check()


def require_valid(assembly: ResolvedAssembly) -> None:
    """Refuses an assembly with any problem, listing all of them (``EF-75``)."""
    problems = check_assembly(assembly)
    if problems:
        raise EnergySystemAssemblyError(
            EnergySystemErrorId.ASSEMBLY_LIBRARY_CHECK,
            assembly.label,
            f"the assembly '{assembly.path}' has {len(problems)} problem{'s' if len(problems) != 1 else ''}: "
            + " | ".join(problems),
        )
