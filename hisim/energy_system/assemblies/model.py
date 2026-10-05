"""The in-memory form of an assembly file (``*.assembly.yaml``).

An assembly (``assemblies_spec.md`` §2.1) is a fragment of an energy system in a file of its own:
ordinary component entries whose names are local to it, inner imports of further assemblies,
parameters with units, descriptions and ranges, constraints across them, named presets,
parameter-selected internal variants, an interface of ports, and its test contract (§9.4). The
models here hold exactly what the file says; like the energy-system models they know nothing about
component classes and keep a value that a parameter supplies as the raw ``{$param: <name>}``
mapping the file wrote.
"""

from __future__ import annotations

import enum
import re
from typing import Any, ClassVar, Dict, Mapping, Optional, Pattern, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hisim.energy_system.imports_model import ImportEntry, Port, PortKind
from hisim.energy_system.model import ComponentEntry


class ParameterType(enum.Enum):
    """The type of an assembly parameter (§2.6)."""

    FLOAT = "float"
    INT = "int"
    BOOL = "bool"
    ENUM = "enum"
    STRING = "string"
    LIST = "list"

    @property
    def is_numeric(self) -> bool:
        """Whether the parameter is a number, which is what carries a unit and a range."""
        return self in (ParameterType.FLOAT, ParameterType.INT)


class NoDefault:
    """Marks a parameter declared without a default, which every import must then give."""

    def __repr__(self) -> str:
        """How messages and ``describe`` show the absence of a default."""
        return "<no default>"


#: The one instance of :class:`NoDefault`.
NO_DEFAULT = NoDefault()


class ParameterDeclaration(BaseModel):
    """One parameter of an assembly (§2.6, D24).

    Attributes:
        name: The parameter's key.
        type: Its type.
        unit: A member name of ``lt.Units`` (``WATT``, ``LITER``), or ``None``.
        description: What it means; required by the library check.
        default: Its default, ``AUTO`` for a value a member's sizing law owns, ``None`` for
            "no value" (written ``none``), or :data:`NO_DEFAULT`.
        values: The allowed values of an ``enum`` (or a restricted other type).
        range: ``(min, max)`` of a numeric parameter, the box the assembly is tested over; the
            library check requires it on every numeric parameter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    #: The keys a parameter declaration may carry, in canonical order.
    KEYS: ClassVar[Tuple[str, ...]] = ("type", "unit", "default", "values", "range", "description")

    name: str
    type: ParameterType
    unit: Optional[str] = None
    description: Optional[str] = None
    default: Any = NO_DEFAULT
    values: Optional[Tuple[Any, ...]] = None
    range: Optional[Tuple[float, float]] = None

    @property
    def has_default(self) -> bool:
        """Whether the parameter declares a default."""
        return not isinstance(self.default, NoDefault)

    @property
    def allowed_values(self) -> Optional[Tuple[Any, ...]]:
        """The closed set of values, if the parameter has one: its ``values``, or both booleans."""
        if self.values is not None:
            return self.values
        if self.type == ParameterType.BOOL:
            return (True, False)
        return None


class ConstraintKind(enum.Enum):
    """The three structured constraints across parameters (§2.6)."""

    EXACTLY_ONE_OF = "exactly_one_of"
    AT_MOST_ONE_OF = "at_most_one_of"
    REQUIRES = "requires"


class Constraint(BaseModel):
    """One constraint: ``exactly_one_of``/``at_most_one_of`` a list, or ``requires`` a mapping.

    Attributes:
        kind: Which constraint.
        parameters: The parameters of ``exactly_one_of``/``at_most_one_of``.
        requires: For ``requires``: parameter to the parameters it needs given as well.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ConstraintKind
    parameters: Tuple[str, ...] = ()
    requires: Mapping[str, Tuple[str, ...]] = Field(default_factory=dict)

    def named_parameters(self) -> Tuple[str, ...]:
        """Every parameter the constraint names."""
        if self.kind == ConstraintKind.REQUIRES:
            names = []
            for parameter, needed in self.requires.items():
                names.append(parameter)
                names.extend(needed)
            return tuple(dict.fromkeys(names))
        return self.parameters

    def text(self) -> str:
        """The constraint as a reader writes it."""
        if self.kind == ConstraintKind.REQUIRES:
            parts = [f"{parameter} requires {', '.join(needed)}" for parameter, needed in self.requires.items()]
            return f"requires: {'; '.join(parts)}"
        return f"{self.kind.value}: [{', '.join(self.parameters)}]"


class MemberTemplate(BaseModel):
    """One member of an assembly: a component entry with local names, before expansion.

    Attributes:
        entry: The entry as written; its ``inputs`` and ``sizing_sources`` name other members,
            its ``config`` and constructor arguments may hold ``{$param: ...}`` values, its
            ``order`` is its relative position, and its ``placeholders`` mark where the ports
            that lower into it land.
        preset_parameter: When the preset is written ``{$param: <name>}``, the parameter naming it.
        display: The English display template over the parameters (§2.4), or ``None``.
        variant: For a member of an internal variant, ``(variant, option)``.
        source_path: The key path of the member's block in its file, for its line.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: ComponentEntry
    preset_parameter: Optional[str] = None
    display: Optional[str] = None
    variant: Optional[Tuple[str, str]] = None
    source_path: Tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """The member's name, local to its assembly."""
        return self.entry.name


class InternalVariantOption(BaseModel):
    """One option of an internal variant: the selector's values it covers and its members."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    when: Tuple[Any, ...]
    components: Mapping[str, MemberTemplate] = Field(default_factory=dict)


class InternalVariant(BaseModel):
    """A parameter-selected internal variant (§2.6, §5.3): ``selected_by`` and its options."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    selected_by: str
    options: Mapping[str, InternalVariantOption] = Field(default_factory=dict)

    def option_for(self, value: Any) -> Optional[InternalVariantOption]:
        """The option whose ``when:`` covers a value of the selecting parameter."""
        return next((option for option in self.options.values() if value in option.when), None)


class MonotoneDirection(enum.Enum):
    """The direction a ``tests.monotone`` declaration expects (§9.4)."""

    INCREASING = "increasing"
    DECREASING = "decreasing"
    CONSTANT = "constant"


class BoundsDeclaration(BaseModel):
    """``tests.bounds``: an output (``Member.Output`` with its unit) or a KPI of a member in a band."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output: Optional[str] = None
    kpi: Optional[str] = None
    member: Optional[str] = None
    unit: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None

    @property
    def subject(self) -> str:
        """What the declaration bounds, as a reader writes it."""
        return self.output if self.output is not None else f"{self.kpi} of {self.member}"


class MonotoneDeclaration(BaseModel):
    """``tests.monotone``: one parameter rises, a member's KPI moves in one direction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parameter: str
    kpi: str
    member: str
    direction: MonotoneDirection


class ExpectDeclaration(BaseModel):
    """``tests.expect``: a preset's KPI of a member lies in a band on the test weather."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    preset: str
    kpi: str
    member: str
    min: Optional[float] = None
    max: Optional[float] = None


class TestContract(BaseModel):
    """The test contract an assembly carries in its own file (§9.4, D24)."""

    # The class name starts with "Test", which pytest would collect if it were importable from a
    # test module; ``__test__`` tells it that this is not a test.
    __test__: ClassVar[bool] = False

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The keys of the ``tests:`` block.
    KEYS: ClassVar[Tuple[str, ...]] = ("bounds", "monotone", "expect")

    bounds: Tuple[BoundsDeclaration, ...] = ()
    monotone: Tuple[MonotoneDeclaration, ...] = ()
    expect: Tuple[ExpectDeclaration, ...] = ()


class AssemblyFile(BaseModel):
    """A whole assembly file as read.

    Attributes:
        schema_version: The format version, 4.
        name: The assembly's library path as the file states it (``pv/array``).
        description: What it is.
        parameters: Its parameters, in written order.
        constraints: Its constraints across parameters.
        presets: Named parameter sets.
        components: Its members outside every internal variant.
        imports: Its inner imports (§2.5).
        variants: Its internal variants.
        ports: Its interface, every section's ports in one namespace.
        tests: Its test contract, or ``None`` when the file carries none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The schema version an assembly file is written in.
    SCHEMA_VERSION: ClassVar[int] = 4

    #: The value of ``kind`` that marks an assembly file.
    KIND: ClassVar[str] = "assembly"

    #: The grammar of a library path, ``<family>/<name>``: identifiers joined by ``/``, at least two of
    #: them. The resolver and both schemas read it from here.
    LIBRARY_PATH_PATTERN: ClassVar[Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)+$")

    #: The top-level keys, in canonical order.
    TOP_LEVEL_KEYS: ClassVar[Tuple[str, ...]] = (
        "schema_version",
        "kind",
        "name",
        "description",
        "parameters",
        "constraints",
        "presets",
        "imports",
        "components",
        "variants",
        "interface",
        "tests",
    )

    #: The sections of the interface.
    INTERFACE_SECTIONS: ClassVar[Tuple[str, ...]] = ("needs", "provides", "internal", "observes", "actuates")

    #: Keys reserved on an instance and an import (D18, D22), which no parameter may be named.
    RESERVED_PARAMETER_NAMES: ClassVar[Tuple[str, ...]] = ("preset", "parameters", "installation_year", "quote")

    schema_version: int
    name: Optional[str] = None
    description: Optional[str] = None
    parameters: Mapping[str, ParameterDeclaration] = Field(default_factory=dict)
    constraints: Tuple[Constraint, ...] = ()
    presets: Mapping[str, Mapping[str, Any]] = Field(default_factory=dict)
    components: Mapping[str, MemberTemplate] = Field(default_factory=dict)
    imports: Mapping[str, ImportEntry] = Field(default_factory=dict)
    variants: Mapping[str, InternalVariant] = Field(default_factory=dict)
    ports: Mapping[str, Port] = Field(default_factory=dict)
    tests: Optional[TestContract] = None

    def member_names(self) -> Tuple[str, ...]:
        """The names of every member the file writes, in written order, each once."""
        names = list(self.components)
        for variant in self.variants.values():
            for option in variant.options.values():
                names.extend(name for name in option.components if name not in names)
        return tuple(names)

    def ports_of(self, *kinds: PortKind) -> Dict[str, Port]:
        """The interface's ports of the given kinds."""
        return {name: port for name, port in self.ports.items() if port.kind in kinds}
