"""The assembly rules of an energy-system file: what its imports must obey to expand into one flat file.

Declarative mode only, stage ``BUILD``. The expansion of a file's imports (``hisim/energy_system/assemblies/expansion.py``)
records what it cannot do instead of raising (``ImportRefusal``) and goes on with the next import, parameter or port;
the rules here refuse what it recorded, every fault at once, as ``EnergySystemImportsError``, an assembly error. They
read three subjects, in the order a run meets them:

- ``ImportedAssemblies``, the assemblies the imports name, once each is looked up: ``EF-71`` an assembly found
  nowhere, ``EF-72`` found twice, ``EF-75`` an assembly the library check refuses. The library check
  (:class:`LibraryChecker`) moved here whole: one rule with the 45 messages it knows, the unit problems ``EF-78`` and
  ``EF-79`` among them, as they always were.
- ``ExpandedImports``, what the selection of each import's parameters, the binding of every port and the sequence of
  the flat file recorded: ``EF-76`` and ``EF-77`` for the parameters and constraints, ``EF-7A`` to ``EF-7L`` for the
  ports, ``EF-7P`` for the evaluation order. The expansion stops after the first of its steps that recorded a refusal,
  since the next would read what it left undecided; the binding rules require the parameter rules, so a refused
  parameter lists them as not run.
- ``BuiltDocument``, the wired file: the selection of the observers' feeds runs inside the wiring, on the constructed
  components, and records ``EF-7S``, ``EF-7U`` and ``EF-7V`` with the wiring's refusals; those rules are wiring rules
  that raise the assembly error.

What decides the binding and the selection stays in ``hisim/energy_system/assemblies/`` (``binding.py``,
``parameters.py``, ``selection.py``, ``addresses.py``, ``resolver.py``); only the judgement moved. Every fault keeps
the catalogue message the expansion raised before, ``EF-7x at <location>: <problem>``, and carries the import or
component it belongs to and its key path in the energy-system file. The checks that stay where they fire are listed in
``hisim/setup_rules/inventory.py``: the readers' ``EF-70`` and ``EF-73``, a library directory that does not exist, an
import named like a component (``EF-52``).
"""

from __future__ import annotations

import string
from types import MappingProxyType
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple, Type

from hisim import loadtypes as lt
from hisim.config.contributions import declared_facts_of
from hisim.config.sizing import declared_field_unit
from hisim.energy_system.assemblies.model import AssemblyFile, ConstraintKind, MemberTemplate, ParameterType
from hisim.energy_system.assemblies.parameters import ParameterChecks
from hisim.energy_system.assemblies.resolver import ResolvedAssembly
from hisim.energy_system.errors import EnergySystemCatalogueError, EnergySystemErrorId, EnergySystemImportsError, ImportRefusal
from hisim.energy_system.imports_model import ImportEntry, ParameterReference, Port, PortKind
from hisim.setup_rules.rule import Fault, RuleStage, SetupRule
from hisim.setup_rules.subjects import BuiltDocument, ExpandedImports, ImportedAssemblies
from hisim.setup_rules.wiring import WiringRule


class LibraryChecker:
    """Lists every problem of one assembly file: the 45 problems the library check knows, every one at once.

    The rule ``EF-75`` (:class:`LibraryValid`) refuses an imported assembly with any problem, and the library test
    (``tests/assemblies/test_assembly_library.py``) runs the checker on every file of the library. It covers the names
    (the file's ``name`` is its library path, no parameter takes a reserved name), the parameters (description,
    default, and for a number a unit of ``lt.Units`` and a range), the units of the parameters against the config
    fields they feed (D16 b: ``EF-78`` for a mismatch, ``EF-79`` for a fed field declaring none, listed among the
    problems under their own codes), that every parameter does something and every enum value selects something of
    its own, the ``exactly_one_of`` and ``all_or_none_of`` constraints, that the variants partition their selector,
    that every port names members that exist in every option where it can be active, the placeholders, the display
    templates, and the test contract. What needs the constructed components -- default connections, inputs, outputs,
    KPIs -- is checked where they are constructed, by the wiring rules. Example: ``LibraryChecker(assembly).check()``
    for a file named ``test/other`` at ``test/base`` gives ``["test/base.assembly.yaml:3: the assembly is named
    'test/other' but lives at 'test/base'."]``.
    """

    #: A value of each parameter type, which a ``display:`` template's format spec must accept.
    DISPLAY_SAMPLES: ClassVar[Mapping[ParameterType, Any]] = MappingProxyType(
        {
            ParameterType.FLOAT: 1.5,
            ParameterType.INT: 1,
            ParameterType.BOOL: True,
            ParameterType.STRING: "text",
        }
    )

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
        """Runs every check of the file and returns the problems, each ``<file>:<line>: <problem>``; empty for a valid file."""
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
            # The binder imports the component base, which the rules package must not import when it is collected.
            from hisim.energy_system.classes import ClassBinder  # pylint: disable=import-outside-toplevel

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
            if (
                declaration.type == ParameterType.INT
                and declaration.range is not None
                and not all(bound.is_integer() for bound in declaration.range)
            ):
                self.add(
                    path + ("range",),
                    f"the int parameter '{name}' has the range [{declaration.range[0]:g}, {declaration.range[1]:g}], "
                    "whose bounds are not both integers.",
                )
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
        """Every constraint names declared parameters, none shared with another, and the defaults satisfy it.

        A member of an ``all_or_none_of`` must also have a value that states it — a stated default, a
        range, or a stated value among its allowed ones — or the constraint could only ever leave every
        member unstated, and the harness would have nothing to state it with (D31).
        """
        defaults = {name: declaration.default for name, declaration in self.model.parameters.items()}
        first: Dict[str, int] = {}
        for index, constraint in enumerate(self.model.constraints):
            names, kind = constraint.names, constraint.kind.value
            for name in names:
                if first.setdefault(name, index) != index:
                    self.add(
                        ("constraints", index),
                        f"'{name}' is named by the constraints {first[name]} and {index}; a parameter "
                        "belongs to one constraint at most, or stating it would decide two of them at once.",
                    )
            unknown = [name for name in names if name not in self.model.parameters]
            if unknown:
                self.add(("constraints", index), f"{kind} names {', '.join(unknown)}, which are no parameters.")
                continue
            violation = (
                ParameterChecks.violation(names, defaults)
                if constraint.kind == ConstraintKind.EXACTLY_ONE_OF
                else ParameterChecks.partly_stated(names, defaults)
            )
            if violation is not None:
                self.add(("constraints", index), f"the defaults violate it: {violation}.")
            if constraint.kind == ConstraintKind.ALL_OR_NONE_OF:
                for name in names:
                    declaration = self.model.parameters[name]
                    statable = (
                        ParameterChecks.is_stated(declaration.default)
                        or declaration.range is not None
                        or any(ParameterChecks.is_stated(value) for value in declaration.allowed_values or ())
                    )
                    if not statable:
                        self.add(
                            ("constraints", index),
                            f"'{name}' has no value that states it (a stated default, a range or a stated allowed "
                            "value), so the all_or_none_of could never be stated.",
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
                elif port.kind in (PortKind.NEED, PortKind.OBSERVER) or port.is_provision:
                    self._present_where_active(path, port, member_name)
        for member in self.model.all_members():
            for placed in member.entry.placeholders:
                port_name = placed.placeholder.port
                target = self.model.ports.get(port_name)
                if target is None or member.name not in target.landing_members:
                    self.add(
                        member.source_path + ("inputs", placed.position),
                        f"'{member.name}' carries a placeholder for '{port_name}', which is no need, circuit end or "
                        "fuel provision landing in it.",
                    )
        for name, port in self.model.ports.items():
            path = ("interface", port.section, name)
            if port.kind == PortKind.NEED or port.is_fuel_provision:
                for into in port.landing_members:
                    holders = [m for m in self.model.all_members() if m.name == into]
                    if holders and not any(
                        any(p.placeholder.port == name for p in m.entry.placeholders) for m in holders
                    ):
                        self.add(
                            path,
                            f"the port '{name}' lowers into '{into}', which carries no '{{$port: {name}}}' "
                            "placeholder.",
                        )
            via = port.controllable.get("via")
            if via is not None and (via not in self.model.ports or self.model.ports[via].kind != PortKind.NEED):
                self.add(path + ("controllable",), f"the port '{name}' is controllable via '{via}', which is no need.")
            if port.kind == PortKind.FACT and port.is_provision:
                for member in (item for item in self.model.all_members() if item.name in port.members):
                    config = self.config_class(member)
                    if config is not None and port.fact not in declared_facts_of(config):
                        self.add(
                            path,
                            f"the port '{name}' provides '{port.fact}', which {config.__name__} (member "
                            f"'{member.name}') does not declare in its SIZING_CONTRIBUTIONS.",
                        )

    def _present_where_active(self, path: Tuple[Any, ...], port: Port, member: str) -> None:
        """A need's, an observer port's or a provision's member exists in every option where the port can be active."""
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


class ImportRule(SetupRule[ExpandedImports]):
    """The shared base of the rules that refuse what the expansion of imports recorded after it found the assemblies.

    It declares no ``CODE``, so it is no rule itself. Its rules read the expanded imports and raise
    ``EnergySystemImportsError``; each refuses the refusals of its own code (:meth:`faults_of`).
    """

    VIOLATION = EnergySystemImportsError

    #: The parameter rules, which the binding rules require: an import whose parameters are refused is not bound.
    SELECTION_RULES: ClassVar[FrozenSet[str]] = frozenset({"EF-76", "EF-77"})

    @staticmethod
    def faults_of(rule: Type[SetupRule[Any]], refusals: Sequence[ImportRefusal], file: Optional[str]) -> List[Fault]:
        """Return a fault of ``rule`` for every refusal of its code: the catalogue message, the import or component, the key path.

        Example: for ``EF-71``, ``ImportRefusal(ASSEMBLY_NOT_FOUND, "imports.tank", "the assembly 'test/bse' is in
        none of the library directories (...).", key_path=("imports", "tank"), owner="tank")`` gives a fault of
        ``component="tank"`` and ``key_path=("imports", "tank")`` whose message is ``EF-71 at imports.tank: the
        assembly 'test/bse' ...; see: hisim setup-rules show EF-71``.

        Args:
            rule: The rule whose code to refuse.
            refusals: What the expansion recorded.
            file: The energy-system file's path, ``None`` for a file read from text.

        Returns:
            The faults, in the order the refusals were recorded.
        """
        return [
            rule.fault(refusal.message, component=refusal.component, key_path=refusal.key_path, file=file)
            for refusal in refusals
            if refusal.error_id.value == rule.CODE
        ]

    def check(self, subject: ExpandedImports) -> Sequence[Fault]:
        """Return a fault per refusal of this rule's code the expansion recorded."""
        return self.faults_of(type(self), subject.refusals, subject.file)


class LookupRule(SetupRule[ImportedAssemblies]):
    """The shared base of the rules that judge the assemblies a file's imports name, before anything is selected.

    It declares no ``CODE``, so it is no rule itself. Its rules read the imported assemblies and raise
    ``EnergySystemImportsError``.
    """

    VIOLATION = EnergySystemImportsError

    def check(self, subject: ImportedAssemblies) -> Sequence[Fault]:
        """Return a fault per refusal of this rule's code the lookup of the assemblies recorded."""
        return ImportRule.faults_of(type(self), subject.refusals, subject.file)


class SelectionRule(WiringRule):
    """The shared base of the rules that refuse what the selection of the observers' feeds recorded during the wiring.

    It declares no ``CODE``, so it is no rule itself. The selection runs inside the wiring, on the constructed
    components, so its rules read the built file as the wiring rules do and require ``EF-33``; they raise
    ``EnergySystemImportsError``, the error the selection raised before it moved here. The wiring rules require them
    (``WiringRule.SELECTION_RULES``): a refused selection is reported alone, as it was when it stopped the wiring.
    """

    VIOLATION = EnergySystemImportsError
    REQUIRES: ClassVar[FrozenSet[str]] = frozenset({"EF-33"})

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per refusal of this rule's code the selection recorded, with the source of an imported observer."""
        return self.recorded(subject)


class AssemblyFound(LookupRule):
    """What it checks: every import names an assembly, ``<family>/<name>``, that one of the library directories holds.

    Why: an import is the assembly it names; without the file there are no members, parameters or ports to expand,
    and the library path is the only name an assembly has.

    Example of a fault: ``EF-71 at imports.tank: the assembly 'test/bse' is in none of the library directories
    (energy_systems/assemblies). Did you mean: test/base? Valid assemblies: test/base. Add its directory to
    HISIM_ASSEMBLY_PATH, or correct the path.``.

    How to fix: correct the library path (the message suggests the near ones), or add the directory that holds the
    assembly to ``HISIM_ASSEMBLY_PATH``.
    """

    CODE = "EF-71"
    NAME = "found"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class AssemblyFoundOnce(LookupRule):
    """What it checks: no assembly an import names is found in two library directories.

    Why: a library path is never shadowed: which of two files ran must not depend on the order of
    ``HISIM_ASSEMBLY_PATH``, and the import record names one file by its hash.

    Example of a fault: ``EF-72 at imports.tank: the assembly 'test/base' is found in two places, a/test/base.assembly.yaml
    and b/test/base.assembly.yaml; one name, one file. Remove or rename one of them; a library path is never
    shadowed.``.

    How to fix: remove or rename one of the two files, or take one directory off the search path.
    """

    CODE = "EF-72"
    NAME = "found-once"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class LibraryValid(LookupRule):
    """What it checks: every imported assembly passes the library check, every problem of it listed at once.

    Why: an assembly is a contract its importers rely on without reading it: a parameter without a unit or a range,
    a variant that does not cover its selector, a port naming a member that is absent where the port is active, or a
    missing test contract would let an import expand into a system nobody checked. The check lists the 45 problems
    :class:`LibraryChecker` knows, the unit problems ``EF-78`` and ``EF-79`` among them.

    Example of a fault: ``EF-75 at test/base.assembly.yaml: the assembly 'test/base' has 2 problems:
    test/base.assembly.yaml:5: the parameter 'volume_in_liter' has no description. | test/base.assembly.yaml:13:
    EF-78: the parameter 'volume_in_liter' is in WATT, but MockTankConfig.volume_in_liter (member 'Tank') is in
    LITER; a unit is never converted.``.

    How to fix: fix each problem at the line it names in the assembly file; ``hisim energy-system describe
    <family>/<name>`` prints what the assembly declares.
    """

    CODE = "EF-75"
    NAME = "library-valid"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: ImportedAssemblies) -> Sequence[Fault]:
        """Return a fault per imported assembly with a problem, each assembly checked once, at the first import of
        it."""
        faults: List[Fault] = []
        checked: Set[str] = set()
        for key, assembly in subject.assemblies.items():
            if assembly.path in checked:
                continue
            checked.add(assembly.path)
            problems = LibraryChecker(assembly).check()
            if problems:
                faults.append(self.fault(self.message(assembly, problems), component=key, key_path=("imports", key), file=subject.file))
        return faults

    @staticmethod
    def message(assembly: ResolvedAssembly, problems: Sequence[str]) -> str:
        """Return the catalogue message of an assembly's problems, as the expansion raised it.

        Example: two problems of ``test/base`` give ``EF-75 at test/base.assembly.yaml: the assembly 'test/base' has 2
        problems: <first> | <second>``.

        Args:
            assembly: The assembly checked.
            problems: Its problems, as :meth:`LibraryChecker.check` lists them.

        Returns:
            The message.
        """
        return EnergySystemCatalogueError.build_message(
            EnergySystemErrorId.ASSEMBLY_LIBRARY_CHECK,
            assembly.label,
            f"the assembly '{assembly.path}' has {len(problems)} problem{'s' if len(problems) != 1 else ''}: " + " | ".join(problems),
        )


class ParameterValid(ImportRule):
    """What it checks: every parameter an import writes is one its assembly declares, with a value of the declared
    type, among the allowed values and in range; every variant selector resolves to a value an option covers; every
    member's preset parameter resolves to a name and its display template renders.

    Why: nothing is converted or defaulted beyond the declarations: a value that does not fit would reach a config
    field as something its law never saw, and a selector no option covers leaves the import without members.

    Example of a fault: ``EF-76 at import 'tank', house.energy_system.yaml:9: the import sets the float parameter
    'volume_in_liter' of 'test/base.assembly.yaml' to 900 lies outside the range [50.0, 500.0].``.

    How to fix: write a value the declaration allows (``hisim energy-system describe <family>/<name>`` lists the
    parameters with their types, units and ranges), or leave the parameter out to take its default.
    """

    CODE = "EF-76"
    NAME = "parameter-valid"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class ConstraintHolds(ImportRule):
    """What it checks: the resolved parameters of every import satisfy its assembly's constraints: exactly one member
    of an ``exactly_one_of`` stated, and an ``all_or_none_of`` stated whole or not at all.

    Why: a constraint names parameters that only make sense together, a size given one way or another; two stated
    members of an ``exactly_one_of`` would size one device twice, and a partly stated ``all_or_none_of`` would leave
    the rest to laws that assume none is stated.

    Example of a fault: ``EF-77 at import 'battery', house.energy_system.yaml:9: the parameters violate the
    constraint all_or_none_of [capacity_in_kwh, power_in_watt] of 'storage/battery.assembly.yaml': capacity_in_kwh =
    10 is stated but power_in_watt is not; state both or neither.``.

    How to fix: state one member of an ``exactly_one_of`` (the others then resolve to none), and every member of an
    ``all_or_none_of`` or none of them.
    """

    CODE = "EF-77"
    NAME = "constraint-holds"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class PortHasAPartner(ImportRule):
    """What it checks: every required port finds a partner in the file: a need a component of its partner class, a
    circuit end the other end of its circuit, a fact need a provider, a many-fact need at least one provider.

    Why: the format never adds a partner: a required port without one would leave a member's input open, or a
    sizing law without the fact it reads.

    Example of a fault: ``EF-7A at import 'heating', house.energy_system.yaml:12: the required port 'weather'
    (partner MockWeather) has no partner in the file; candidates: none. Add a component of class MockWeather, then
    add to the import 'heating' one of `bind: {weather: <partner>}`.``.

    How to fix: add the partner the message names to the file, and bind the port to it if it is not the only
    candidate.
    """

    CODE = "EF-7A"
    NAME = "port-has-a-partner"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = ImportRule.SELECTION_RULES


class PortUnambiguous(ImportRule):
    """What it checks: a required port with several candidates in scope, or a verb whose target holds several, carries
    a verb that names one.

    Why: the default rule binds the one candidate in scope; picking one of several would bind by position, and adding
    a component would silently rebind another import.

    Example of a fault: ``EF-7B at import 'heating', house.energy_system.yaml:12: the required port 'weather'
    (partner MockWeather) has 2 candidates, Weather, Weather2, and no verb. add to the import 'heating' one of
    `bind: {weather: Weather}`, `bind: {weather: Weather2}`.``.

    How to fix: paste one of the verb lines the message ends with into the import or the site entry.
    """

    CODE = "EF-7B"
    NAME = "port-unambiguous"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = ImportRule.SELECTION_RULES


class RequiredPortNotDeclined(ImportRule):
    """What it checks: no ``none:`` and no ``optional-bind:`` on a required port.

    Why: a required port is one its members cannot run without; declining it, or binding it so that an absent
    partner leaves it unbound, would leave a member's input open.

    Example of a fault: ``EF-7C at import 'heating', house.energy_system.yaml:12: the required port 'weather'
    (partner MockWeather) is declined with 'none:', but a required port cannot be; candidates: Weather. add to the
    import 'heating' one of `bind: {weather: Weather}`.``.

    How to fix: bind the port with ``bind:``, or change the parameters that make it required.
    """

    CODE = "EF-7C"
    NAME = "required-port-not-declined"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = ImportRule.SELECTION_RULES


class BoundPartnerExists(ImportRule):
    """What it checks: a verb's target is an import or component the file declares, present (a ``bind:`` target not
    left out by a group or a variant), with the instance and the port it names.

    Why: a verb that names nothing, or a partner the selected world leaves out, binds the port to nothing; only
    ``optional-bind:`` may leave a port unbound when its partner is absent.

    Example of a fault: ``EF-7D at import 'heating', house.energy_system.yaml:12: the required port 'weather'
    (partner MockWeather) is bound to 'Wether' with 'bind:', but the file declares no import or component 'Wether';
    a house without the partner leaves the verb out. Candidates of the port: Weather. Did you mean: Weather? Valid
    imports and components the file declares: Building, Weather, heating.``.

    How to fix: name a declared partner (the message lists them), enable the group or select the option that holds
    it, or write ``optional-bind:`` on an optional port.
    """

    CODE = "EF-7D"
    NAME = "bound-partner-exists"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = ImportRule.SELECTION_RULES


class OptionalPortDecided(ImportRule):
    """What it checks: an optional port with a candidate in scope carries a verb.

    Why: adding a partner never silently changes another import: an optional port binds only when the file says so,
    and declines only when it says so.

    Example of a fault: ``EF-7E at import 'heating', house.energy_system.yaml:12: the optional port 'ems' (partner
    MockEms) has the candidate Ems and no verb; adding a partner never silently changes another import. add to the
    import 'heating' one of `optional-bind: {ems: Ems}`, `bind: {ems: Ems}`, `none: [ems]`.``.

    How to fix: paste one of the verb lines the message ends with.
    """

    CODE = "EF-7E"
    NAME = "optional-port-decided"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = ImportRule.SELECTION_RULES


class VerbOnAnActivePort(ImportRule):
    """What it checks: no verb is written for a port its import's parameters switch off (``active_when``,
    ``required_when``).

    Why: a verb on an inactive port binds nothing; it is left over from other parameters, and reading the file one
    would believe the port bound.

    Example of a fault: ``EF-7F at import 'heater', house.energy_system.yaml:12: the port 'tank_temperature' is
    inactive with these parameters (active_when/required_when), yet the import 'heater' writes 'bind' for it. Remove
    the 'bind' line for 'tank_temperature', or change the parameters that switch it off.``.

    How to fix: remove the verb, or change the parameters that switch the port off.
    """

    CODE = "EF-7F"
    NAME = "verb-on-an-active-port"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = ImportRule.SELECTION_RULES


class KnownPort(ImportRule):
    """What it checks: every verb names a port of its import or site entry that a verb binds: a need, a circuit end or
    a fact need of one provider.

    Why: a carrier need and a many-fact need bind every provider in scope, and a provided port binds nothing; a verb
    naming one of them, or no port at all, is a typo or a misunderstanding that would otherwise be ignored.

    Example of a fault: ``EF-7G at import 'heating', house.energy_system.yaml:12: a verb names the port 'wether',
    which is no port of import 'heating', house.energy_system.yaml:12; a verb binds a need, a circuit end or a fact
    need of one provider (...). Did you mean: weather? Valid ports a verb binds: weather.``.

    How to fix: name a port the message lists, or remove the verb.
    """

    CODE = "EF-7G"
    NAME = "known-port"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = ImportRule.SELECTION_RULES


class BoundOutputIsRead(ImportRule):
    """What it checks: a need bound to a provided output of another import, ``<import>.<port>``, reads that output
    through its ``wires:``.

    Why: binding a need to one output promises a wire from it; a need whose wires read other outputs would connect
    the partner's default outputs instead, and the provided output would be read by nobody.

    Example of a fault: ``EF-7H at import 'controller', house.energy_system.yaml:12: the port 'heat' is bound to the
    provided output 'heater.heat' (Heater.ThermalPower), but it reads its default connections. Name 'ThermalPower' in
    the port's wires, or bind the port to 'heater'.``.

    How to fix: name the provided output in the need's ``wires:``, or bind the need to the import rather than to its
    output.
    """

    CODE = "EF-7H"
    NAME = "bound-output-is-read"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = ImportRule.SELECTION_RULES


class PortContract(ImportRule):
    """What it checks: every port fits the members it lowers into and the partner it binds: its partner is of its
    class, its members are present with these parameters and carry its placeholders, a fact lands once per member, a
    fuel provision meters at one placeholder, an observer has members and an import's ``observes:`` replaces exactly
    one observer port; a site entry's placeholders name its own needs, circuit ends and fuel provisions.

    Why: a port is lowered to ordinary items at its members' placeholders; a port with nowhere to land, or a partner
    of another class, would expand into a file that connects nothing, or connects the wrong thing.

    Example of a fault: ``EF-7J at components.Building.inputs[2]: 'Building' carries a placeholder for 'heat', which
    its 'ports' block does not declare as a need, a circuit end or a fuel it provides. Valid ports: weather.``.

    How to fix: declare the port, add the ``{$port: <port>}`` placeholder to the member that should receive it, or
    switch the port off with ``active_when`` where its members are absent.
    """

    CODE = "EF-7J"
    NAME = "port-contract"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class CircuitEndsMatch(ImportRule):
    """What it checks: both ends of a circuit binding name one circuit, and an end joined by the other end's verb
    carries no verb that disagrees.

    Why: a circuit is a medium shared by its two ends; binding an end of one circuit to an end of another would join
    two media, and two disagreeing verbs leave unclear which end decided.

    Example of a fault: ``EF-7K at import 'heating', house.energy_system.yaml:12: the circuit end 'flow' (circuit
    space_heating) is bound to 'dhw', whose ends are of the circuit dhw ('flow'); a circuit binds only an end of its
    own circuit, the medium.``.

    How to fix: bind the end to an end of its own circuit, and write the binding on one end, or make both ends name
    each other.
    """

    CODE = "EF-7K"
    NAME = "circuit-ends-match"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = ImportRule.SELECTION_RULES


class OneProviderPerCarrier(ImportRule):
    """What it checks: every carrier a port provides has exactly one provider, a fuel's provider has a bound consumer,
    and a required carrier need finds a provider.

    Why: one site has one supply per carrier, which the meters and the energy balance assume; a provider without a
    consumer is an idle connection, and a required need without a provider would leave its consuming outputs
    unmetered. A district or a plant with two grid connections is refused by this rule; today no setup has two.

    Example of a fault: ``EF-7L at import 'grid2', house.energy_system.yaml:15: electricity has 2 providers,
    grid.electricity, grid2.electricity; a system has exactly one provider per carrier.``.

    How to fix: import one provider per carrier, add a consumer for a fuel's provider or remove it, and add a
    provider for a required carrier need.
    """

    CODE = "EF-7L"
    NAME = "one-provider-per-carrier"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = ImportRule.SELECTION_RULES


class OrderValid(ImportRule):
    """What it checks: an evaluation ``order:`` names each number once among the site entries and imports, and a
    re-run record's components stand in the sequence its import record states.

    Why: the order decides the sequence the simulator adds the components in, and with it the result columns; two
    blocks with one number would be ordered by accident, and a record whose components moved would re-run in another
    order than the run it records.

    Example of a fault: ``EF-7P at house.energy_system.yaml:20: import 'heating' and component 'Building' both carry
    order: 2.``.

    How to fix: give every ordered block its own number; re-record a record instead of editing its components.
    """

    CODE = "EF-7P"
    NAME = "order-valid"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class ObserverSelection(SelectionRule):
    """What it checks: every observer -- a meter or a controller whose class declares dynamic default connections --
    selects at least one feed, and every selector of its ``observes:`` matches an output its class declares a feed
    from.

    Why: an observer that observes nothing measures or controls nothing, and a selector that matches nothing is a
    typo or names a device the file lacks; either would run without the observation the file asks for.

    Example of a fault: ``EF-7S at components.Meter: the selector [kind: PV] of 'Meter' matches no output its class
    declares a feed from; candidates: Battery.AcPower.``.

    How to fix: correct the selector, add the device it names, or write ``observes: declared`` to take every output
    the class declares.
    """

    CODE = "EF-7S"
    NAME = "observer-selection"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class ActuationSelection(SelectionRule):
    """What it checks: every controllable output is ranked by exactly the controller it binds: a ``target_input``
    output by one controller (none only when ``optional: true``), a ``via`` output by the partner its need is bound
    to.

    Why: a controllable output is actuated by one controller; two would fight over it, none would leave it at its
    own default, and a controller other than the bound one would actuate a device that reads another's signal.

    Example of a fault: ``EF-7U at components.battery-Battery: the controllable output battery-Battery.AcPower
    (battery.charge) is ranked by no controller; a controllable output is actuated by exactly the one controller it
    binds (D21).``.

    How to fix: let exactly one controller observe the output (bind the need that names it, or adjust the
    controllers' selections), or mark a ``target_input`` output ``optional: true``.
    """

    CODE = "EF-7U"
    NAME = "actuation-selection"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # A refused observer ranks nothing, so the selection checks the controllable outputs only once every observer selected.
    REQUIRES: ClassVar[FrozenSet[str]] = frozenset({"EF-33", "EF-7S", "EF-7V"})


class WeightFree(SelectionRule):
    """What it checks: a weight an observer derives for a further participant of one component type (``default + k``)
    reaches neither another type's base weight nor the monitored-only weight.

    Why: the weights rank the participants of a controller; a derived weight equal to another type's base weight
    would tie the two, and one at the monitored-only weight would rank a participant as only measured.

    Example of a fault: ``EF-7V at components.Ems: 'Ems' would rank battery2-Battery.AcPower, participant 2 of
    BATTERY, at 6 + 1 = 7, which is the base weight of HEAT_PUMP (HeatPump): the two would tie. Pin the weight on the
    feed: ...``.

    How to fix: write the feed with its weight among the observer's inputs and leave it out of the selection.
    """

    CODE = "EF-7V"
    NAME = "weight-free"
    STAGE = RuleStage.BUILD
    ENFORCED = True
