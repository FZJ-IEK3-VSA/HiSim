"""An import's parameters checked and resolved, its internal variants selected, its ports' states decided.

``assemblies_spec.md`` §2.6, §3.1. The resolved parameter set of one import (or instance) is the
assembly's defaults overridden by the values the import writes. Every value is checked against its
declaration — the type, the allowed values, the range of a numeric parameter — and the
``exactly_one_of`` constraints are checked on the resolved set. Nothing is converted and nothing is
defaulted beyond the declarations: a value that does not fit is a load error naming the import, the
parameter and what would fit.

A parameter is *stated* for a constraint when its resolved value is neither ``none``, nor ``AUTO``,
nor ``false``; ``0`` is a stated value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Set, Tuple

from hisim.config.sizing import _AutoSize
from hisim.energy_system.assemblies.model import AssemblyFile, MemberTemplate, ParameterDeclaration, ParameterType
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.imports_model import Port, PortState


class ParameterChecks:
    """The value checks a parameter declaration imposes, and the constraint rule."""

    #: The spelling of a value a member's sizing law computes.
    AUTO_SPELLING: str = _AutoSize.WIRE_SPELLING

    @classmethod
    def problem(  # pylint: disable=too-many-return-statements  # one return per rule a value can break
        cls, declaration: ParameterDeclaration, value: Any
    ) -> Optional[str]:
        """Names what is wrong with a value for a parameter, or ``None`` when it fits.

        ``none`` (Python ``None``) is "no value" and fits every parameter; ``AUTO`` fits a numeric
        one, leaving the field it feeds to its law.
        """
        if value is None or (value == cls.AUTO_SPELLING and declaration.type.is_numeric):
            return None
        kind = declaration.type
        if kind.is_numeric:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"{value!r} is not a number"
            if kind == ParameterType.INT and not isinstance(value, int):
                return f"{value!r} is not an integer"
            if declaration.range is not None and not declaration.range[0] <= value <= declaration.range[1]:
                return f"{value!r} lies outside the range [{declaration.range[0]}, {declaration.range[1]}]"
        elif kind == ParameterType.BOOL and not isinstance(value, bool):
            return f"{value!r} is not true or false"
        elif kind in (ParameterType.ENUM, ParameterType.STRING) and not isinstance(value, str):
            return f"{value!r} is not a string"
        if declaration.values is not None and value not in declaration.values:
            return f"{value!r} is not one of {', '.join(repr(item) for item in declaration.values)}"
        return None

    @classmethod
    def is_stated(cls, value: Any) -> bool:
        """Whether a resolved value counts for a constraint; by identity, so ``0`` is stated and ``false`` is not."""
        return value is not None and value is not False and value != cls.AUTO_SPELLING

    @classmethod
    def violation(cls, names: Tuple[str, ...], values: Mapping[str, Any]) -> Optional[str]:
        """Names how a resolved parameter set violates one ``exactly_one_of``, or ``None``."""
        stated = [name for name in names if cls.is_stated(values.get(name))]
        if len(stated) == 1:
            return None
        return f"exactly one of {', '.join(names)} is stated, but {len(stated)} are ({', '.join(stated) or 'none'})"


@dataclass(frozen=True)
class Selection:
    """The parameters of one import or instance resolved, and what they select.

    Attributes:
        given: The values the import wrote, as written.
        resolved: Every declared parameter's value after the defaults and the given values.
        variants: Internal variant to the option the parameters select.
        members: The members of the selected world, in written order.
        dropped: The names of members only an unselected option writes.
    """

    given: Mapping[str, Any]
    resolved: Mapping[str, Any]
    variants: Mapping[str, str]
    members: Mapping[str, MemberTemplate]
    dropped: Tuple[str, ...]

    def state(self, port: Port) -> PortState:
        """A port's requirement state for these parameters (§3.1); a site entry's port has none to read."""
        if port.active_when and not self.holds(port.active_when):
            return PortState.INACTIVE
        if port.is_provision:
            return PortState.PROVIDED
        if port.required_when:
            return PortState.REQUIRED if self.holds(port.required_when) else PortState.INACTIVE
        return PortState.OPTIONAL if port.optional else PortState.REQUIRED

    def holds(self, conditions: Mapping[str, Tuple[Any, ...]]) -> bool:
        """Whether a conjunctive ``{parameter: [values]}`` condition holds."""
        return all(self.resolved.get(parameter) in allowed for parameter, allowed in conditions.items())


#: The selection of a site entry: no parameters, no variants.
SITE = Selection(given={}, resolved={}, variants={}, members={}, dropped=())


def select(assembly: AssemblyFile, label: str, given: Mapping[str, Any], where: str) -> Selection:
    """Resolves and checks one import's parameters and selects its internal variants.

    Args:
        assembly: The assembly imported, which the library check has accepted.
        label: Its file label, for messages.
        given: The parameter values the import (or instance) writes.
        where: The import (and instance) and its line, for messages.

    Returns:
        The selection.

    Raises:
        EnergySystemAssemblyError: ``EF-76`` for an unknown parameter, a value that does not fit or a
            variant selector left at no value, ``EF-77`` for a violated ``exactly_one_of``.
    """
    declarations = assembly.parameters
    values: Dict[str, Any] = {name: declaration.default for name, declaration in declarations.items()}
    for name, value in given.items():
        declaration = declarations.get(name)
        if declaration is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PARAMETER_INVALID,
                where,
                f"the import sets '{name}', which is not a parameter of '{label}'.",
                alternatives=tuple(declarations),
                alternatives_label="parameters",
                offending_value=str(name),
            )
        written = declaration.type.written(value)
        problem = ParameterChecks.problem(declaration, written)
        if problem is not None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PARAMETER_INVALID,
                where,
                f"the import sets the {declaration.type.value} parameter '{name}' of '{label}' to {problem}.",
            )
        values[name] = written
    for names in assembly.exactly_one_of:
        violation = ParameterChecks.violation(names, values)
        if violation is not None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.CONSTRAINT_VIOLATED,
                where,
                f"the parameters violate the constraint exactly_one_of [{', '.join(names)}] of '{label}': {violation}.",
            )
    members: Dict[str, MemberTemplate] = dict(assembly.components)
    variants: Dict[str, str] = {}
    offered: Set[str] = set()
    for variant in assembly.variants.values():
        option = variant.option_for(values[variant.selected_by])
        if option is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PARAMETER_INVALID,
                where,
                f"the parameter '{variant.selected_by}' of '{label}' selects the variant '{variant.name}', but it "
                f"resolves to {values[variant.selected_by]!r}, which no option covers.",
                alternatives=[repr(value) for option in variant.options.values() for value in option.when],
                alternatives_label=f"values of '{variant.selected_by}'",
            )
        variants[variant.name] = option.name
        members.update(option.components)
        for other in variant.options.values():
            offered.update(other.components)
    ordered = {member.name: member for member in assembly.all_members() if member.name in members}
    return Selection(
        given=dict(given),
        resolved=values,
        variants=variants,
        members={name: members[name] for name in ordered},
        dropped=tuple(sorted(offered - set(members))),
    )
