"""An import's parameters: applying the preset, checking every value, and substituting them.

``assemblies_spec.md`` §2.6 and §2.7. An import (or one of its instances) states a preset and some
parameter values; the resolved parameter set is the assembly's defaults, overridden by the preset's
values, overridden by the values the import writes. Every value is checked against its declaration
— the type, the allowed values, the range of a numeric parameter — and the constraints across
parameters are checked on the resolved set. Nothing is converted and nothing is defaulted beyond
the declarations: a value that does not fit is a load error naming the import, the parameter and
what would fit.

**Constraint semantics.** A parameter is *stated* when its resolved value is neither ``none``, nor
``AUTO``, nor ``false``. ``exactly_one_of`` requires exactly one of its parameters stated,
``at_most_one_of`` at most one, and ``requires: {a: [b, c]}`` that ``b`` and ``c`` are stated
whenever ``a`` is. Deciding on resolved values rather than on the written ones is what lets a
boolean switch (``with_buffer``, default true) satisfy a ``requires`` without being written, and
the library check proves that every assembly's defaults and every preset satisfy its constraints.

:class:`ParameterSubstitution` replaces every ``{$param: <name>}`` of a member by the resolved
value, wherever a value goes: a config value at any depth, a constructor argument, the preset, an
inner import's parameter, a port's carrier or fact; and every ``{$switch: <selector>, <case>:
<value>, …}`` by the value of the case its selector — a parameter's resolved value, or an internal
variant's selected option — chooses (:class:`~hisim.energy_system.imports_model.SwitchValue`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from hisim.config.sizing import _AutoSize
from hisim.energy_system.assemblies.model import (
    AssemblyFile,
    Constraint,
    ConstraintKind,
    ParameterDeclaration,
    ParameterType,
)
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.imports_model import ParameterReference, SwitchValue


@dataclass(frozen=True)
class ResolvedParameters:
    """The parameters of one import or instance, as given and as resolved.

    Attributes:
        preset: The preset applied, or ``None``.
        given: The values the import wrote, as written.
        resolved: Every declared parameter's value after defaults, preset and given values.
    """

    preset: Optional[str]
    given: Mapping[str, Any]
    resolved: Mapping[str, Any]


class ParameterChecks:
    """The value checks one parameter declaration imposes."""

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
        if value is None:
            return None
        if value == cls.AUTO_SPELLING and declaration.type.is_numeric:
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
        elif kind == ParameterType.LIST and not isinstance(value, list):
            return f"{value!r} is not a list"
        allowed = declaration.values
        if allowed is not None and value not in allowed:
            return f"{value!r} is not one of the allowed values {', '.join(repr(item) for item in allowed)}"
        return None

    @classmethod
    def is_stated(cls, value: Any) -> bool:
        """Whether a resolved value counts as stated for a constraint (see the module docstring)."""
        return value is not None and value is not False and value != cls.AUTO_SPELLING

    @classmethod
    def violation(cls, constraint: Constraint, values: Mapping[str, Any]) -> Optional[str]:
        """Names how a resolved parameter set violates one constraint, or ``None``."""
        if constraint.kind == ConstraintKind.REQUIRES:
            for parameter, needed in constraint.requires.items():
                if not cls.is_stated(values.get(parameter)):
                    continue
                missing = [name for name in needed if not cls.is_stated(values.get(name))]
                if missing:
                    return f"'{parameter}' is stated, so {', '.join(missing)} must be stated as well"
            return None
        stated = [name for name in constraint.parameters if cls.is_stated(values.get(name))]
        if constraint.kind == ConstraintKind.EXACTLY_ONE_OF and len(stated) != 1:
            return (
                f"exactly one of {', '.join(constraint.parameters)} is stated, but {len(stated)} are "
                f"({', '.join(stated) or 'none'})"
            )
        if constraint.kind == ConstraintKind.AT_MOST_ONE_OF and len(stated) > 1:
            return f"at most one of {', '.join(constraint.parameters)} is stated, but {', '.join(stated)} are"
        return None


class ParameterResolver:
    """Resolves and checks the parameters of one import or instance of one assembly."""

    def __init__(self, assembly: AssemblyFile, assembly_path: str, where: str, describe: Callable[[], str]) -> None:
        """Prepares the resolver.

        Args:
            assembly: The assembly imported.
            assembly_path: Its library path, for messages.
            where: The import (and instance) as messages name it, ``import 'pv' (instance 'east')``.
            describe: Renders the import's source map for a message.
        """
        self.assembly = assembly
        self.assembly_path = assembly_path
        self.where = where
        self.describe = describe

    def error(self, error_id: EnergySystemErrorId, problem: str, **kwargs: Any) -> EnergySystemAssemblyError:
        """A refusal naming the import and carrying its source map."""
        return EnergySystemAssemblyError(error_id, self.where, f"{problem} {self.describe()}", **kwargs)

    def resolve(self, preset: Optional[str], given: Mapping[str, Any]) -> ResolvedParameters:
        """Applies the preset and the given values over the defaults and checks the result.

        Raises:
            EnergySystemAssemblyError: ``EF-76`` for an unknown preset or parameter, a value that
                does not fit, or a parameter without a default that nobody gives; ``EF-77`` for a
                violated constraint.
        """
        declarations = self.assembly.parameters
        values: Dict[str, Any] = {
            name: declaration.default for name, declaration in declarations.items() if declaration.has_default
        }
        layers: List[Tuple[str, Mapping[str, Any]]] = []
        if preset is not None:
            if preset not in self.assembly.presets:
                raise self.error(
                    EnergySystemErrorId.PARAMETER_INVALID,
                    f"'{self.assembly_path}' has no preset '{preset}'.",
                    alternatives=tuple(self.assembly.presets),
                    alternatives_label="presets",
                    offending_value=str(preset),
                )
            layers.append((f"preset '{preset}'", self.assembly.presets[preset]))
        layers.append(("the import", given))
        for origin, layer in layers:
            for name, value in layer.items():
                declaration = declarations.get(name)
                if declaration is None:
                    raise self.error(
                        EnergySystemErrorId.PARAMETER_INVALID,
                        f"{origin} sets '{name}', which is not a parameter of '{self.assembly_path}'.",
                        alternatives=tuple(declarations),
                        alternatives_label="parameters",
                        offending_value=str(name),
                    )
                written = None if value == "none" and declaration.type != ParameterType.STRING else value
                problem = ParameterChecks.problem(declaration, written)
                if problem is not None:
                    raise self.error(
                        EnergySystemErrorId.PARAMETER_INVALID,
                        f"{origin} sets the {declaration.type.value} parameter '{name}' of "
                        f"'{self.assembly_path}' to {problem}.",
                    )
                values[name] = written
        missing = [name for name in declarations if name not in values]
        if missing:
            raise self.error(
                EnergySystemErrorId.PARAMETER_INVALID,
                f"'{self.assembly_path}' declares {', '.join(missing)} without a default, and the import gives "
                f"{'it' if len(missing) == 1 else 'them'} no value.",
            )
        for name, declaration in declarations.items():
            problem = ParameterChecks.problem(declaration, values[name])
            if problem is not None:
                raise self.error(
                    EnergySystemErrorId.PARAMETER_INVALID,
                    f"the default of the parameter '{name}' of '{self.assembly_path}' is {problem}.",
                )
        for constraint in self.assembly.constraints:
            violation = ParameterChecks.violation(constraint, values)
            if violation is not None:
                raise self.error(
                    EnergySystemErrorId.CONSTRAINT_VIOLATED,
                    f"the parameters violate the constraint '{constraint.text()}' of '{self.assembly_path}': "
                    f"{violation}.",
                )
        ordered = {name: values[name] for name in declarations}
        return ResolvedParameters(preset=preset, given=dict(given), resolved=ordered)


class ParameterSubstitution:
    """Replaces every ``{$param: <name>}`` in a value tree by the resolved parameter value."""

    def __init__(self, values: Mapping[str, Any], selections: Optional[Mapping[str, Any]] = None):
        """Prepares the substitution.

        Args:
            values: The resolved parameters.
            selections: Selector to the case key a ``{$switch: …}`` takes: every parameter with
                its resolved value and every internal variant with its selected option. ``None``
                takes the parameters alone.
        """
        self.values = values
        self.selections: Mapping[str, Any] = selections if selections is not None else values

    def apply(self, value: Any) -> Any:
        """Returns the tree with every reference and switch substituted; the input is not modified.

        Raises:
            KeyError: For a reference to a parameter the values do not hold, or a switch whose
                selector or selected case is missing; the library check refuses both before any
                expansion, so reaching it is a bug.
        """
        name = ParameterReference.name_of(value)
        if name is not None:
            return self.values[name]
        if SwitchValue.is_switch(value):
            selected = self.selections[SwitchValue.selector_of(value)]
            return self.apply(SwitchValue.cases_of(value)[selected])
        if isinstance(value, Mapping):
            return {key: self.apply(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            # The container keeps its type: a tuple node stays a tuple, a list a list.
            return type(value)(self.apply(item) for item in value)
        return value

    @classmethod
    def references_in(cls, value: Any) -> List[Tuple[str, Tuple[str, ...]]]:
        """Every parameter reference of a tree, with its key path."""
        return cls._collect(value, ParameterReference.name_of, ())

    @classmethod
    def switches_in(cls, value: Any, path: Tuple[str, ...] = ()) -> List[Tuple[Mapping[str, Any], Tuple[str, ...]]]:
        """Every ``{$switch: …}`` of a tree, with its key path, nested ones included."""
        found: List[Tuple[Mapping[str, Any], Tuple[str, ...]]] = []
        if SwitchValue.is_switch(value):
            found.append((value, path))
            for case, item in SwitchValue.cases_of(value).items():
                found.extend(cls.switches_in(item, path + (str(case),)))
        elif isinstance(value, Mapping):
            for key, item in value.items():
                found.extend(cls.switches_in(item, path + (str(key),)))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                found.extend(cls.switches_in(item, path + (str(index),)))
        return found

    @classmethod
    def unlowered_in(cls, value: Any) -> List[Tuple[str, Tuple[str, ...]]]:
        """Every ``$`` spelling of a tree this step does not lower, with its key path."""
        return cls._collect(value, ParameterReference.unlowered_key_of, ())

    @classmethod
    def _collect(
        cls, value: Any, detect: Callable[[Any], Optional[str]], path: Tuple[str, ...]
    ) -> List[Tuple[str, Tuple[str, ...]]]:
        """Walks a value tree and returns what ``detect`` finds at each leaf it recognizes, with its key path.

        Args:
            value: The tree.
            detect: Returns the key a node stands for (a parameter's name, an unlowered spelling),
                or ``None`` for a node it does not recognize, which is then descended into.
            path: The key path of ``value``.

        Returns:
            ``(key, path)`` for every recognized node, in document order.
        """
        key = detect(value)
        if key is not None:
            return [(key, path)]
        found: List[Tuple[str, Tuple[str, ...]]] = []
        if SwitchValue.is_switch(value):
            # A case's value lands where the switch stands, so what it holds keeps the switch's path.
            for item in SwitchValue.cases_of(value).values():
                found.extend(cls._collect(item, detect, path))
        elif isinstance(value, Mapping):
            for child_key, item in value.items():
                found.extend(cls._collect(item, detect, path + (str(child_key),)))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                found.extend(cls._collect(item, detect, path + (str(index),)))
        return found
