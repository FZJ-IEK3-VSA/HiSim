"""The library check: every rule an assembly file must obey, every problem listed at once.

``assemblies_spec.md`` §2.6, §3.3 (the contract check), §9.3 and §9.4. The reader refuses a file
whose shape is wrong at its first problem; this check takes a well-formed file and lists **every**
problem it has, so that an author fixing an assembly sees the whole list rather than one item per
run. It fails hard as a whole: a file with any problem is refused (``EF-75``), the message carrying
the full list, each problem with its file and line.

Two strengths of the check exist. The **expansion** runs the contract — parameters, presets,
constraints, variants, members, ports, order — on every assembly it imports, before it expands it;
the units of the parameters it substitutes it checks itself, with the named errors ``EF-78`` and
``EF-79``. The **library** check adds what the library test requires of an assembly before it may
be shipped (D24): a description on every parameter, a ``range`` on every numeric one, a ``name``
that is its library path, and the test contract — a ``tests:`` block with at least one
``tests.monotone``, every unit an ``lt.Units`` member, and every name a declaration uses resolving
to a member, a parameter or a preset.

**A member written in several variant options** (``Heater`` as an immersion heater in one option and
a boiler in another) is checked once per option, against that option's class: a declaration that
holds for one option's class and not for another's is a problem naming the option (hisim-lt0b.13).

**What needs the components is not checked here.** The check reads the assembly files and imports
the member classes only to know that they exist and which configuration they take. A component's
inputs, outputs, default connections and KPIs come into being in its constructor, and no class
declares them a second time, so everything that needs them is checked where the components are
constructed: whether a member declares default connections from a port's partner class, whether a
wire's input and output and a provided output exist, whether a circuit's members own and read its
three outputs, whether a carrier need's consuming outputs exist, carry its carrier and are exactly
what the provider's meter feeds — the wiring stage of every build, which checks every connection on
the constructed components; whether every energy-carrying or
temperature output carries a ``tests.bounds`` entry, whether a bounds entry's unit is its output's
and whether a named KPI is one its member reports — the assembly test harness's isolation run, on
the constructed members, before any declaration is evaluated.
"""

from __future__ import annotations

import enum
import string
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from hisim import loadtypes as lt
from hisim.config.contributions import declared_facts_of
from hisim.config.sizing import declared_field_unit
from hisim.energy_system.assemblies.model import (
    AssemblyFile,
    MemberTemplate,
    ParameterType,
)
from hisim.energy_system.assemblies.parameters import ParameterChecks, ParameterSubstitution
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.bindings import facts_read_by
from hisim.energy_system.classes import ClassBinder
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemError, EnergySystemErrorId
from hisim.energy_system.imports_model import (
    Carriers,
    ObservesPlaceholder,
    ParameterReference,
    Port,
    PortKind,
    PortPlaceholder,
    SwitchValue,
)
from hisim.energy_system.imports_reader import ImportsReader


class CheckStrength(enum.Enum):
    """How much the check requires."""

    #: The contract the expansion requires of every assembly it imports.
    EXPANSION = "expansion"
    #: The contract plus documentation, ranges and the test contract (D24), for the library test.
    LIBRARY = "library"


class LibraryChecker:
    """Lists every problem of one assembly file."""

    def __init__(
        self, assembly: ResolvedAssembly, resolver: Optional[AssemblyResolver], strength: CheckStrength
    ) -> None:
        """Prepares the check.

        Args:
            assembly: The resolved assembly.
            resolver: Resolves its inner imports; ``None`` skips the checks that need them.
            strength: What the check requires.
        """
        self.assembly = assembly
        self.model: AssemblyFile = assembly.model
        self.resolver = resolver
        self.strength = strength
        self.problems: List[str] = []
        self._configs: Dict[str, Optional[type]] = {}
        self._components: Dict[str, Optional[type]] = {}

    # ------------------------------------------------------------------------------------- reporting

    def add(self, path: Sequence[Any], problem: str) -> None:
        """Records one problem at a key path of the file."""
        self.problems.append(f"{self.assembly.lines.location(*path).text}: {problem}")

    @property
    def library(self) -> bool:
        """Whether the library's additional requirements apply."""
        return self.strength == CheckStrength.LIBRARY

    def check(self) -> List[str]:
        """Runs every rule and returns the problems, in file order of the rules."""
        self._check_name()
        self._check_parameters()
        self._check_constraints()
        self._check_presets()
        self._check_variants()
        self._check_members()
        self._check_imports()
        self._check_ports()
        self._check_switches()
        self._check_order()
        if self.library:
            self._check_tests()
        return self.problems

    # --------------------------------------------------------------------------------------- classes

    def _component_class(self, member: MemberTemplate) -> Optional[type]:
        """The member's component class, or ``None`` after recording (once) why it does not import."""
        class_path = member.entry.class_path
        if class_path not in self._components:
            try:
                self._components[class_path] = ClassBinder.component_class_of(class_path, "class", member.name)
            except EnergySystemError as error:
                self.add(member.source_path + ("class",), f"the class of '{member.name}' does not import: {error}")
                self._components[class_path] = None
        return self._components[class_path]

    def config_class(self, member: MemberTemplate) -> Optional[type]:
        """The configuration class of a member's class, or ``None``."""
        class_path = member.entry.class_path
        if class_path not in self._configs:
            component = self._component_class(member)
            config: Optional[type] = None
            if component is not None:
                try:
                    config = ClassBinder.configuration_class_of(component, "class", member.name)
                except EnergySystemError as error:
                    self.add(member.source_path + ("class",), str(error))
            self._configs[class_path] = config
        return self._configs[class_path]

    # ------------------------------------------------------------------------------------------ rules

    def _check_name(self) -> None:
        """The file's ``name`` is its library path."""
        if self.model.name is None:
            if self.library:
                self.add(("kind",), f"the assembly states no 'name'; write 'name: {self.assembly.path}'.")
        elif self.model.name != self.assembly.path:
            self.add(("name",), f"the assembly is named '{self.model.name}' but lives at '{self.assembly.path}'.")

    def _check_parameters(self) -> None:
        """Types, units, values, ranges, defaults, descriptions (§2.6, D24)."""
        for name, declaration in self.model.parameters.items():
            path = ("parameters", name)
            if name in AssemblyFile.RESERVED_PARAMETER_NAMES:
                self.add(path, f"'{name}' is reserved on an import and an instance and cannot name a parameter.")
            if declaration.unit is not None:
                if declaration.unit not in lt.Units.__members__:
                    self.add(path + ("unit",), f"the unit '{declaration.unit}' of '{name}' is no member of lt.Units.")
                if not declaration.type.is_numeric:
                    self.add(
                        path + ("unit",),
                        f"the {declaration.type.value} parameter '{name}' carries a unit; only a number has one.",
                    )
            if declaration.type == ParameterType.ENUM and declaration.values is None:
                self.add(path, f"the enum parameter '{name}' declares no 'values'.")
            if declaration.range is not None and not declaration.type.is_numeric:
                self.add(
                    path + ("range",),
                    f"the {declaration.type.value} parameter '{name}' carries a range; only a number has one.",
                )
            for value in declaration.values or ():
                problem = ParameterChecks.problem(declaration.model_copy(update={"values": None}), value)
                if problem is not None:
                    self.add(path + ("values",), f"the allowed value of '{name}' is {problem}.")
            if declaration.has_default:
                problem = ParameterChecks.problem(declaration, declaration.default)
                if problem is not None:
                    self.add(path + ("default",), f"the default of '{name}' is {problem}.")
            if self.library:
                if not declaration.description:
                    self.add(path, f"the parameter '{name}' has no description.")
                if declaration.type.is_numeric and declaration.range is None:
                    self.add(path, f"the numeric parameter '{name}' has no range (D24: the box it is tested over).")

    def _check_constraints(self) -> None:
        """Every constraint names declared parameters, and the defaults satisfy it."""
        declared = self.model.parameters
        for index, constraint in enumerate(self.model.constraints):
            for name in constraint.named_parameters():
                if name not in declared:
                    self.add(
                        ("constraints", index),
                        f"the constraint '{constraint.text()}' names '{name}', which is no parameter.",
                    )
        if all(declaration.has_default for declaration in declared.values()):
            defaults = {name: declaration.default for name, declaration in declared.items()}
            for index, constraint in enumerate(self.model.constraints):
                violation = ParameterChecks.violation(constraint, defaults)
                if violation is not None:
                    self.add(("constraints", index), f"the defaults violate '{constraint.text()}': {violation}.")

    def _check_presets(self) -> None:
        """Every preset names declared parameters with fitting values and satisfies the constraints (§2.7)."""
        declared = self.model.parameters
        for preset, values in self.model.presets.items():
            path = ("presets", preset)
            resolved = {name: declaration.default for name, declaration in declared.items() if declaration.has_default}
            for name, value in values.items():
                declaration = declared.get(name)
                if declaration is None:
                    self.add(path + (name,), f"the preset '{preset}' sets '{name}', which is no parameter.")
                    continue
                written = None if value == "none" and declaration.type != ParameterType.STRING else value
                problem = ParameterChecks.problem(declaration, written)
                if problem is not None:
                    self.add(path + (name,), f"the preset '{preset}' sets '{name}' to {problem}.")
                resolved[name] = written
            if len(resolved) == len(declared):
                for constraint in self.model.constraints:
                    violation = ParameterChecks.violation(constraint, resolved)
                    if violation is not None:
                        self.add(path, f"the preset '{preset}' violates '{constraint.text()}': {violation}.")

    def _check_variants(self) -> None:
        """Every internal variant partitions its selector's allowed values (§2.6)."""
        for name, variant in self.model.variants.items():
            path = ("variants", name)
            declaration = self.model.parameters.get(variant.selected_by)
            if declaration is None:
                self.add(
                    path + ("selected_by",),
                    f"the variant '{name}' is selected by '{variant.selected_by}', which is no parameter.",
                )
                continue
            allowed = declaration.allowed_values
            if allowed is None:
                self.add(
                    path + ("selected_by",),
                    f"the variant '{name}' is selected by '{variant.selected_by}', which has no closed set of values "
                    "to partition.",
                )
                continue
            covered: Dict[Any, str] = {}
            for option_name, option in variant.options.items():
                for value in option.when:
                    if value not in allowed:
                        self.add(
                            path + ("options", option_name, "when"),
                            f"the option '{option_name}' covers {value!r}, which '{variant.selected_by}' does not "
                            "allow.",
                        )
                    elif value in covered:
                        self.add(
                            path + ("options", option_name, "when"),
                            f"{variant.selected_by}={value!r} is covered by both '{covered[value]}' and "
                            f"'{option_name}'.",
                        )
                    else:
                        covered[value] = option_name
            missing = [value for value in allowed if value not in covered]
            if missing:
                self.add(
                    path,
                    f"the variant '{name}' covers no option for "
                    f"{variant.selected_by}={', '.join(repr(value) for value in missing)}; the when: lists must "
                    "partition its values.",
                )
            for option_name, option in variant.options.items():
                for member in option.components:
                    if member in self.model.components:
                        self.add(
                            path + ("options", option_name, "components", member),
                            f"the option '{option_name}' writes '{member}', which is also a member outside the "
                            "variant.",
                        )

    def _all_members(self) -> List[MemberTemplate]:
        """Every member template the file writes, each option's included."""
        members: List[MemberTemplate] = list(self.model.components.values())
        for variant in self.model.variants.values():
            for option in variant.options.values():
                members.extend(option.components.values())
        return members

    def _templates(self) -> Dict[str, List[MemberTemplate]]:
        """Member name to every template written under it: one, or one per variant option that writes it."""
        templates: Dict[str, List[MemberTemplate]] = {}
        for member in self._all_members():
            templates.setdefault(member.name, []).append(member)
        return templates

    def _applies(self, port: Port, template: MemberTemplate) -> bool:
        """Whether a port is active in the variant option a member template belongs to.

        A port whose ``active_when`` or ``required_when`` restricts the variant's selector to values
        the option does not cover is never lowered in that option, so the option's class owes it
        nothing.
        """
        if template.variant is None:
            return True
        variant = self.model.variants.get(template.variant[0])
        option = variant.options.get(template.variant[1]) if variant is not None else None
        if variant is None or option is None:
            return True
        for conditions in (port.active_when, port.required_when):
            allowed = conditions.get(variant.selected_by)
            if allowed is not None and not set(option.when) & set(allowed):
                return False
        return True

    @staticmethod
    def _option(template: MemberTemplate, templates: Sequence[MemberTemplate]) -> str:
        """`` in option 'o' of the variant 'v'`` for a member written in several options, else nothing."""
        if len(templates) < 2 or template.variant is None:
            return ""
        return f" in option '{template.variant[1]}' of the variant '{template.variant[0]}'"

    def _check_members(self) -> None:
        """References between members, parameter references, classes, units, display templates."""
        names = set(self.model.member_names())
        declared = self.model.parameters
        for member in self._all_members():
            entry = member.entry
            path = member.source_path
            for item in entry.inputs:
                if item.source not in names:
                    self.add(
                        path + ("inputs",),
                        f"'{member.name}' takes an input from '{item.source}', which is no member; everything that "
                        "crosses the boundary crosses through a port.",
                    )
            for fact, reference in entry.sizing_references():
                if reference.component not in names:
                    self.add(
                        path + ("sizing_sources", fact),
                        f"'{member.name}' takes '{fact}' from '{reference.component}', which is no member.",
                    )
            trees = {"config": dict(entry.config)}
            if entry.constructor is not None:
                trees["constructor"] = dict(entry.constructor.arguments)
            for root, tree in trees.items():
                for parameter, value_path in ParameterSubstitution.references_in(tree):
                    if parameter not in declared:
                        self.add(
                            path + (root,) + value_path,
                            f"'{member.name}' refers to the parameter '{parameter}', which is not declared.",
                        )
                    elif not self.library:
                        # The expansion checks units itself, with its named errors (EF-78, EF-79).
                        continue
                    elif root == "config":
                        self._check_unit(member, parameter, value_path)
                    elif declared[parameter].type.is_numeric:
                        self.add(
                            path + (root,) + value_path,
                            f"the numeric parameter '{parameter}' feeds a constructor argument, which declares no "
                            "unit.",
                        )
            if member.preset_parameter is not None:
                declaration = declared.get(member.preset_parameter)
                if declaration is None or declaration.type not in (ParameterType.ENUM, ParameterType.STRING):
                    self.add(
                        path + ("preset",),
                        f"'{member.name}' takes its preset from '{member.preset_parameter}', which is no enum or "
                        "string parameter.",
                    )
            if member.display is not None:
                for _text, field, _spec, _conversion in string.Formatter().parse(member.display):
                    if field and field not in declared:
                        self.add(
                            path, f"the display template of '{member.name}' names '{field}', which is no parameter."
                        )
            self.config_class(member)

    def _check_unit(self, member: MemberTemplate, parameter: str, value_path: Tuple[str, ...]) -> None:
        """A numeric parameter's unit against the field it feeds (D16 b)."""
        declaration = self.model.parameters[parameter]
        if not declaration.type.is_numeric:
            return
        location = member.source_path + ("config",) + value_path
        config_class = self.config_class(member)
        if config_class is None:
            return
        if len(value_path) != 1:
            self.add(location, f"the numeric parameter '{parameter}' feeds a nested value, which declares no unit.")
            return
        field_unit = declared_field_unit(config_class, value_path[0])
        if field_unit is None:
            self.add(
                location,
                f"'{parameter}' feeds {config_class.__name__}.{value_path[0]}, which declares no unit; declare it with "
                f"sized_field(unit=...) or field(metadata={{'unit': ...}}).",
            )
        elif getattr(field_unit, "name", str(field_unit)) != declaration.unit:
            self.add(
                location,
                f"'{parameter}' is in {declaration.unit or 'no unit'}, the field "
                f"{config_class.__name__}.{value_path[0]} it feeds in {getattr(field_unit, 'name', field_unit)}.",
            )

    def _check_imports(self) -> None:
        """Inner imports resolve, name no member, and their parameter references name declared parameters."""
        members = set(self.model.member_names())
        for key, entry in self.model.imports.items():
            path = ("imports", key)
            if key in members:
                self.add(path, f"the inner import '{key}' has the name of a member; a verb could mean either.")
            tree = {"preset": entry.preset, "parameters": dict(entry.parameters)}
            for instance in (entry.instances or {}).values():
                tree[f"instance {instance.name}"] = {"preset": instance.preset, "parameters": dict(instance.parameters)}
            for parameter, _value_path in ParameterSubstitution.references_in(tree):
                if parameter not in self.model.parameters:
                    self.add(
                        path, f"the inner import '{key}' refers to the parameter '{parameter}', which is not declared."
                    )
            if self.resolver is not None:
                try:
                    self.resolver.resolve(entry.assembly, f"{self.assembly.label}: imports.{key}")
                except EnergySystemError as error:
                    self.add(path + ("assembly",), f"the inner import '{key}' does not resolve: {error}")

    def _check_conditions(self, path: Tuple[Any, ...], conditions: Mapping[str, Tuple[Any, ...]], what: str) -> None:
        """``required_when``/``active_when`` name declared parameters and allowed values."""
        for parameter, values in conditions.items():
            declaration = self.model.parameters.get(parameter)
            if declaration is None:
                self.add(path, f"{what} names '{parameter}', which is no parameter.")
                continue
            allowed = declaration.allowed_values
            for value in values:
                if allowed is not None and value not in allowed:
                    self.add(path, f"{what} lists {parameter}={value!r}, which the parameter does not allow.")

    @staticmethod
    def _placeholders_of(template: MemberTemplate) -> Set[str]:
        """The ports a member template carries ``{$port: …}`` placeholders for."""
        return {
            placed.placeholder.port
            for placed in template.entry.placeholders
            if isinstance(placed.placeholder, PortPlaceholder)
        }

    def _check_ports(self) -> None:  # pylint: disable=too-many-branches  # one branch per port kind
        """The contract check of §3.3 from the file: every port names existing members and placeholders.

        Whether the members' constructed components have the default connections, inputs and outputs
        the ports lower to is the wiring stage's to check, on every build.
        """
        members = self._templates()
        for name, port in self.model.ports.items():
            path: Tuple[Any, ...] = ("interface", port.section, name)
            self._check_conditions(path, port.required_when, f"the port '{name}' required_when")
            self._check_conditions(path, port.active_when, f"the port '{name}' active_when")
            if port.kind == PortKind.NEED:
                for into in port.into:
                    if into not in members:
                        self.add(path + ("into",), f"the port '{name}' lowers into '{into}', which is no member.")
                        continue
                    for template in members[into]:
                        if self._applies(port, template):
                            self._check_need_into(path, name, template, members[into])
            elif port.kind == PortKind.PROVIDED:
                if (port.output_member or "") not in members:
                    self.add(
                        path + ("output",),
                        f"the port '{name}' provides '{port.output}', but '{port.output_member}' is no member.",
                    )
                else:
                    self._check_provided(path, name, port)
            elif port.kind == PortKind.CIRCUIT:
                self._check_circuit_port(path, name, port, members)
            elif port.kind == PortKind.CARRIER:
                self._check_carrier_port(path, name, port, members)
            elif port.kind == PortKind.FACT:
                self._check_fact_port(path, name, port, members)
            elif port.kind == PortKind.OBSERVER:
                self._check_observer_port(path, name, port, members)
            elif port.kind == PortKind.ACTUATES:
                self._check_actuates_port(path, name, port)
            elif port.kind == PortKind.REEXPORT:
                inner = (port.reexports or ".").split(".", 1)[0]
                if inner not in self.model.imports:
                    self.add(
                        path + ("from",),
                        f"the port '{name}' re-exports '{port.reexports}', but '{inner}' is no inner import.",
                    )
            elif port.kind == PortKind.INTERNAL:
                for end in port.ends:
                    head = end.split(".", 1)[0]
                    if "." in end and head not in self.model.imports:
                        self.add(
                            path + ("bind",),
                            f"the internal port '{name}' names '{end}', but '{head}' is no inner import.",
                        )
                    elif "." not in end and end not in members:
                        self.add(path + ("bind",), f"the internal port '{name}' names '{end}', which is no member.")
                if all("." not in end for end in port.ends):
                    self.add(
                        path + ("bind",),
                        f"the internal port '{name}' binds two members; members are wired by a bare name in the "
                        "receiver's inputs, and an internal entry binds a member to an inner import's port.",
                    )
                elif len(port.ends) == 2 and "." not in port.ends[1]:
                    for template in members.get(port.ends[1], []):
                        if name not in self._placeholders_of(template):
                            self.add(
                                path + ("bind",),
                                f"the internal port '{name}' lands in '{port.ends[1]}'"
                                f"{self._option(template, members[port.ends[1]])}, which carries no "
                                f"'{{$port: {name}}}' placeholder.",
                            )
        observers = [name for name, port in self.model.ports.items() if port.kind == PortKind.OBSERVER]
        actuating = [name for name, port in self.model.ports.items() if port.kind == PortKind.ACTUATES]
        if actuating and (len(actuating) != 1 or len(observers) != 1):
            self.add(
                ("interface", "actuates"),
                f"the assembly actuates {len(actuating)} priority lists over {len(observers)} observer ports; a "
                "controller ranks the feeds of its one observer port by its one priority list (§4.4).",
            )
        for holder in self._all_members():
            for placed in holder.entry.placeholders:
                placeholder = placed.placeholder
                if isinstance(placeholder, ObservesPlaceholder):
                    observer = self.model.ports.get(placeholder.observer)
                    if observer is None or observer.kind != PortKind.OBSERVER or holder.name not in observer.into:
                        self.add(
                            holder.source_path + ("inputs", placed.position),
                            f"'{holder.name}' carries a placeholder for the observer '{placeholder.observer}', which "
                            "is no observer port lowering into it.",
                        )
                    continue
                target = self.model.ports.get(placeholder.port)
                if target is None:
                    self.add(
                        holder.source_path + ("inputs", placed.position),
                        f"'{holder.name}' carries a placeholder for '{placeholder.port}', which is no port.",
                    )
                elif not self._lands_in(target, holder.name):
                    self.add(
                        holder.source_path + ("inputs", placed.position),
                        f"'{holder.name}' carries a placeholder for '{placeholder.port}', which does not lower into "
                        "it"
                        + (
                            " (a fact port lowers to a sizing_sources line, not to an input)"
                            if target.kind == PortKind.FACT
                            else ""
                        )
                        + (
                            " (a carrier need lands in its provider's meter)"
                            if target.kind == PortKind.CARRIER and not target.is_provision
                            else ""
                        )
                        + ".",
                    )

    def _check_need_into(
        self, path: Tuple[Any, ...], name: str, template: MemberTemplate, templates: List[MemberTemplate]
    ) -> None:
        """A need's member template carries its placeholder.

        Whether its constructed component declares default connections from the partner classes, and
        has the inputs its wires name, the wiring stage checks.
        """
        into = template.name
        option = self._option(template, templates)
        if name not in self._placeholders_of(template):
            self.add(
                path + ("into",),
                f"the port '{name}' lowers into '{into}'{option}, which carries no '{{$port: {name}}}' placeholder.",
            )

    def _check_provided(self, path: Tuple[Any, ...], name: str, port: Port) -> None:
        """A controllable provided output's ``via`` names a need of the assembly (§4.4).

        Whether the output exists on the constructed member, and the input a ``target_input`` names,
        the wiring stage checks.
        """
        if port.controllable_via is not None:
            via = self.model.ports.get(port.controllable_via)
            if via is None or via.kind != PortKind.NEED:
                self.add(
                    path + ("controllable",),
                    f"the provided output '{name}' is controllable via '{port.controllable_via}', which is no need of "
                    "the assembly; 'via' names the need whose binding lowers to the L1 controller's modifier.",
                )

    def _check_observer_port(
        self, path: Tuple[Any, ...], name: str, port: Port, members: Mapping[str, List[MemberTemplate]]
    ) -> None:
        """An observer port lowers into members that carry its placeholder exactly once (§4.1).

        What its member may observe — the dynamic default connections its constructed component
        declares — the selection decides once the components exist.
        """
        for into in port.into:
            if into not in members:
                self.add(path + ("into",), f"the observer port '{name}' lowers into '{into}', which is no member.")
                continue
            for template in members[into]:
                option = self._option(template, members[into])
                placed = [
                    item
                    for item in template.entry.placeholders
                    if isinstance(item.placeholder, ObservesPlaceholder) and item.placeholder.observer == name
                ]
                if len(placed) != 1:
                    self.add(
                        path + ("into",),
                        f"'{into}'{option} carries {len(placed)} '{{$observes: {name}}}' placeholders; the feeds of "
                        f"the observer port '{name}' land at exactly one.",
                    )

    def _check_actuates_port(self, path: Tuple[Any, ...], name: str, port: Port) -> None:
        """A priority list is a list of selectors, or a list parameter whose default is one (§4.4)."""
        parameter = ParameterReference.name_of(port.priorities)
        if parameter is None:
            return
        declaration = self.model.parameters.get(parameter)
        if declaration is None or declaration.type != ParameterType.LIST:
            self.add(
                path,
                f"the priorities of '{name}' are taken from '{parameter}', which is no list parameter of the assembly.",
            )
            return
        if declaration.has_default and declaration.default is not None:
            try:
                ImportsReader.selectors(declaration.default, f"parameters.{parameter}.default")
            except EnergySystemError as error:
                self.add(("parameters", parameter, "default"), f"the default priorities are no selectors: {error}")

    @staticmethod
    def _lands_in(port: Port, member: str) -> bool:
        """Whether a port's lowered items may land in a member's placeholder for it."""
        if port.kind == PortKind.NEED:
            return member in port.into
        if port.kind == PortKind.CIRCUIT:
            return member in port.members
        if port.kind == PortKind.CARRIER:
            return port.is_provision and member == port.meter
        return port.kind != PortKind.FACT

    def _check_circuit_port(
        self, path: Tuple[Any, ...], name: str, port: Port, members: Mapping[str, List[MemberTemplate]]
    ) -> None:
        """A circuit end names existing members (§3.3).

        Which member owns and which reads the circuit's three outputs the constructed members say; the
        wiring stage checks the bare names the circuit lowers to on every build.
        """
        for member_name in port.members:
            if member_name not in members:
                self.add(path + ("member",), f"the circuit port '{name}' names '{member_name}', which is no member.")

    def _carrier_values(self, port: Port) -> List[Any]:
        """The carriers a ``{$param: …}`` carrier port can take: the parameter's values while the port is active."""
        parameter = ParameterReference.name_of(port.carrier)
        declaration = self.model.parameters.get(parameter or "")
        if declaration is None or declaration.allowed_values is None:
            return []
        values = list(declaration.allowed_values)
        for conditions in (port.active_when, port.required_when):
            if parameter in conditions:
                values = [value for value in values if value in conditions[parameter or ""]]
        return values

    def _check_carrier_port(
        self, path: Tuple[Any, ...], name: str, port: Port, members: Mapping[str, List[MemberTemplate]]
    ) -> None:
        """A carrier names a carrier; a need names outputs of existing members, a provision its meter (§5.1).

        Whether a named output exists and carries the need's carrier, the constructed member says; the
        wiring stage checks it on every build.
        """
        literal = port.carrier if isinstance(port.carrier, str) else None
        if literal is not None and not Carriers.is_carrier(literal):
            self.add(
                path + ("carrier",),
                f"the carrier port '{name}' names '{literal}', which is no carrier; a carrier is written as an "
                f"lt.EnergyBalanceCarrier value: {', '.join(Carriers.names())}.",
            )
        cases = (
            list(SwitchValue.cases_of(port.carrier).values())
            if isinstance(port.carrier, Mapping) and SwitchValue.is_switch(port.carrier)
            else []
        )
        for value in self._carrier_values(port) + [case for case in cases if isinstance(case, str)]:
            if not Carriers.is_carrier(value):
                self.add(
                    path + ("carrier",),
                    f"the carrier port '{name}' takes the carrier {value!r} with some parameters, which is no carrier; "
                    f"a carrier is an lt.EnergyBalanceCarrier value: {', '.join(Carriers.names())}.",
                )
        if port.is_provision:
            if port.meter is None:
                if literal is not None and literal != Carriers.ELECTRICITY:
                    self.add(
                        path,
                        f"the provision of '{literal}' names no 'meter'; a fuel's consumers are observed by its "
                        "provider's meter, where their feeds land.",
                    )
                return
            if literal == Carriers.ELECTRICITY:
                self.add(
                    path + ("meter",),
                    "an electricity provision names no 'meter': electricity has no link, and the meter's selection "
                    "is its observer port (interface.observes, §4.3).",
                )
            if port.meter not in members:
                self.add(path + ("meter",), f"the carrier provision '{name}' names the meter '{port.meter}', which is "
                         "no member.")
            else:
                for template in members[port.meter]:
                    if name not in self._placeholders_of(template):
                        self.add(
                            path + ("meter",),
                            f"the meter '{port.meter}'{self._option(template, members[port.meter])} of the carrier "
                            f"provision '{name}' carries no '{{$port: {name}}}' placeholder for its consumers' feeds.",
                        )
            return
        for item in port.outputs:
            if "." not in item:
                provided = self.model.ports.get(item)
                if provided is None or provided.kind != PortKind.PROVIDED:
                    self.add(
                        path + ("outputs",),
                        f"the carrier need '{name}' names '{item}', which is neither 'Member.Output' nor a provided "
                        "output of the assembly.",
                    )
                continue
            member_name = item.split(".", 1)[0]
            if member_name not in members:
                self.add(path + ("outputs",), f"the carrier need '{name}' names '{item}', but '{member_name}' is no "
                         "member.")

    def _check_fact_port(
        self, path: Tuple[Any, ...], name: str, port: Port, members: Mapping[str, List[MemberTemplate]]
    ) -> None:
        """A fact need lowers into members whose classes read the fact; a provided fact is a contribution (§6)."""
        literal = port.fact if isinstance(port.fact, str) else None
        if port.is_provision:
            for member_name in port.members:
                if member_name not in members:
                    self.add(path + ("member",), f"the provided fact '{name}' names '{member_name}', which is no "
                             "member.")
                    continue
                for template in members[member_name]:
                    config_class = self.config_class(template)
                    if config_class is not None and literal is not None and literal not in declared_facts_of(
                        config_class
                    ):
                        self.add(
                            path + ("member",),
                            f"the provided fact '{literal}' is not among the SIZING_CONTRIBUTIONS of "
                            f"{config_class.__name__} (member '{member_name}'"
                            f"{self._option(template, members[member_name])}), which declares "
                            f"{', '.join(declared_facts_of(config_class)) or 'none'}.",
                        )
            return
        for member_name in port.into:
            if member_name not in members:
                self.add(path + ("into",), f"the fact port '{name}' lowers into '{member_name}', which is no member.")
                continue
            for template in members[member_name]:
                config_class = self.config_class(template)
                if config_class is not None and literal is not None and literal not in facts_read_by(config_class):
                    self.add(
                        path + ("into",),
                        f"the fact port '{name}' lowers '{literal}' into '{member_name}'"
                        f"{self._option(template, members[member_name])}, whose class {config_class.__name__} reads "
                        f"no such fact (it reads {', '.join(facts_read_by(config_class)) or 'none'}).",
                    )

    def _check_switches(self) -> None:
        """Every ``{$switch: …}`` names one selector and covers each of its values exactly once."""
        trees: List[Tuple[Tuple[Any, ...], Any]] = []
        for member in self._all_members():
            trees.append((member.source_path + ("config",), dict(member.entry.config)))
            if member.entry.constructor is not None:
                trees.append((member.source_path + ("constructor",), dict(member.entry.constructor.arguments)))
        for key, entry in self.model.imports.items():
            trees.append((("imports", key, "parameters"), dict(entry.parameters)))
            for instance in (entry.instances or {}).values():
                trees.append((("imports", key, "instances", instance.name), dict(instance.parameters)))
        for name, port in self.model.ports.items():
            trees.append((("interface", port.section, name), {"carrier": port.carrier, "fact": port.fact}))
        for root, tree in trees:
            for switch, value_path in ParameterSubstitution.switches_in(tree):
                self._check_switch(root + value_path, switch)

    def _check_switch(self, path: Tuple[Any, ...], switch: Mapping[str, Any]) -> None:
        """One switch: its selector is a parameter or a variant, and its cases partition the selector's values."""
        selector = SwitchValue.selector_of(switch)
        cases = list(SwitchValue.cases_of(switch))
        is_parameter = isinstance(selector, str) and selector in self.model.parameters
        is_variant = isinstance(selector, str) and selector in self.model.variants
        if is_parameter == is_variant:
            self.add(
                path,
                f"the switch on '{selector}' names "
                + ("both a parameter and an internal variant" if is_parameter else "neither a parameter nor an "
                   "internal variant")
                + " of the assembly.",
            )
            return
        if is_parameter:
            allowed = self.model.parameters[selector].allowed_values
            if allowed is None:
                self.add(path, f"the switch on '{selector}' selects by a parameter without a closed set of values.")
                return
            expected = list(allowed)
        else:
            expected = list(self.model.variants[selector].options)
        missing = [value for value in expected if value not in cases]
        unknown = [case for case in cases if case not in expected]
        if missing or unknown:
            self.add(
                path,
                f"the switch on '{selector}' must cover {', '.join(repr(value) for value in expected)} exactly once; "
                + "; ".join(
                    part
                    for part in (
                        f"missing {', '.join(repr(value) for value in missing)}" if missing else "",
                        f"unknown {', '.join(repr(case) for case in unknown)}" if unknown else "",
                    )
                    if part
                )
                + ".",
            )

    def _check_order(self) -> None:
        """Member and inner-import ``order:`` numbers: all or none, no repeats at one level (D23)."""
        entries: List[Tuple[str, Optional[int], Optional[Tuple[str, str]]]] = [
            (f"member {member.name}", member.entry.order, member.variant) for member in self._all_members()
        ] + [(f"import {key}", entry.order, None) for key, entry in self.model.imports.items()]
        numbered = [entry for entry in entries if entry[1] is not None]
        if numbered and len(numbered) != len(entries):
            missing = [label for label, order, _variant in entries if order is None]
            self.add(("components",), f"a level numbers all its entries or none; {', '.join(missing)} carry no order:.")
        for index, (label, order, variant) in enumerate(numbered):
            later = numbered[index + 1:]
            for other_label, other_order, other_variant in later:
                same_variant_other_option = (
                    variant is not None
                    and other_variant is not None
                    and variant[0] == other_variant[0]
                    and variant[1] != other_variant[1]
                )
                if order == other_order and not same_variant_other_option:
                    self.add(("components",), f"{label} and {other_label} both declare order: {order}.")

    def _check_tests(self) -> None:
        """The test contract (§9.4, D24) from the file: a monotone, units of ``lt.Units``, resolvable names.

        Which outputs carry energy or a temperature and so need a bounds entry, the unit of a bounded
        output and the KPIs a member reports, the constructed members say; the assembly test
        harness checks them in its isolation run.
        """
        tests = self.model.tests
        if tests is None:
            self.add(("kind",), "the assembly carries no test contract ('tests:' with bounds and monotone, §9.4).")
            return
        members = self._templates()
        for index, bounds in enumerate(tests.bounds):
            path = ("tests", "bounds", index)
            if bounds.unit is not None and bounds.unit not in lt.Units.__members__:
                self.add(path, f"the unit '{bounds.unit}' is no member of lt.Units.")
            if bounds.output is not None:
                member_name = bounds.output.split(".", 1)[0]
                if member_name not in members:
                    self.add(path, f"the bounds entry names '{bounds.output}', but '{member_name}' is no member.")
            else:
                self._check_kpi_member(path, members, bounds.member or "")
        numeric = [name for name, declaration in self.model.parameters.items() if declaration.type.is_numeric]
        if numeric and not tests.monotone:
            self.add(
                ("tests",),
                f"the test contract has no monotone entry; one is required while the assembly has a numeric parameter "
                f"({', '.join(numeric)}).",
            )
        for index, monotone in enumerate(tests.monotone):
            path = ("tests", "monotone", index)
            declaration = self.model.parameters.get(monotone.parameter)
            if declaration is None:
                self.add(path, f"the monotone entry moves '{monotone.parameter}', which is no parameter.")
            elif not declaration.type.is_numeric:
                self.add(path, f"the monotone entry moves '{monotone.parameter}', which is no number.")
            self._check_kpi_member(path, members, monotone.member)
        for index, expect in enumerate(tests.expect):
            path = ("tests", "expect", index)
            if expect.preset not in self.model.presets:
                self.add(
                    path, f"the expect entry names the preset '{expect.preset}', which the assembly does not offer."
                )
            self._check_kpi_member(path, members, expect.member)

    def _check_kpi_member(
        self, path: Tuple[Any, ...], members: Mapping[str, List[MemberTemplate]], member: str
    ) -> None:
        """A test declaration's KPI names an existing member; the KPI itself the harness checks on its instance."""
        if member not in members:
            self.add(path, f"the entry names the member '{member}', which does not exist.")


def check_assembly(
    assembly: ResolvedAssembly,
    resolver: Optional[AssemblyResolver] = None,
    strength: CheckStrength = CheckStrength.LIBRARY,
) -> List[str]:
    """Lists every problem of one assembly; empty when it has none."""
    return LibraryChecker(assembly, resolver, strength).check()


def require_valid(assembly: ResolvedAssembly, resolver: Optional[AssemblyResolver], strength: CheckStrength) -> None:
    """Refuses an assembly with any problem, listing all of them (``EF-75``)."""
    problems = check_assembly(assembly, resolver, strength)
    if problems:
        raise EnergySystemAssemblyError(
            EnergySystemErrorId.ASSEMBLY_LIBRARY_CHECK,
            assembly.label,
            f"the assembly '{assembly.path}' has {len(problems)} problem{'s' if len(problems) != 1 else ''}: "
            + " | ".join(problems),
        )
