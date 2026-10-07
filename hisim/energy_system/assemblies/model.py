"""The in-memory form of an assembly file (``*.assembly.yaml``), lean v1.

An assembly (``assemblies_spec.md`` §2.1, §13.1) holds ordinary component entries whose names are
local to it, parameters with units, descriptions, defaults and ranges, ``exactly_one_of``
constraints, parameter-selected internal variants, an interface of ports and its test contract
(§9.4). The models hold exactly what the file says and know nothing about component classes; a value
a parameter supplies is kept as the raw ``{$param: <name>}`` mapping the file wrote.
"""

from __future__ import annotations

import enum
import re
from typing import Any, ClassVar, Dict, Mapping, Optional, Pattern, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hisim.energy_system.imports_model import Port
from hisim.energy_system.model import ComponentEntry


class ParameterType(enum.Enum):
    """The type of an assembly parameter (§2.6)."""

    FLOAT = "float"
    INT = "int"
    BOOL = "bool"
    ENUM = "enum"
    STRING = "string"

    @property
    def is_numeric(self) -> bool:
        """Whether the parameter is a number, which is what carries a unit and a range."""
        return self in (ParameterType.FLOAT, ParameterType.INT)

    def written(self, value: Any) -> Any:
        """The value a written spelling stands for: ``none`` is "no value" (``None``), except for a string."""
        return None if value == "none" and self != ParameterType.STRING else value


class NoDefault:
    """Marks a parameter declared without a default, which the library check refuses."""

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
        unit: A member name of ``lt.Units`` (numeric parameters only).
        description: What it means.
        default: Its default; ``AUTO`` for a value a member's sizing law owns, ``None`` for "no
            value" (written ``none``), or :data:`NO_DEFAULT`.
        values: The allowed values of an ``enum``.
        range: ``(min, max)`` of a numeric parameter, the box the assembly is tested over.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    #: The keys a parameter declaration may carry.
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
        return (True, False) if self.type == ParameterType.BOOL else None

    def admits(self, value: Any) -> bool:
        """Whether a ``when:`` or condition value is one of the closed set; a bool admits only ``true``/``false``."""
        allowed = self.allowed_values
        if allowed is None or value not in allowed:
            return False
        return self.type != ParameterType.BOOL or isinstance(value, bool)


class MemberTemplate(BaseModel):
    """One member of an assembly: a component entry with local names, before expansion.

    Attributes:
        entry: The entry as written; its ``inputs`` and ``sizing_sources`` name other members, its
            ``config`` and constructor arguments may hold ``{$param: …}`` values, and its
            ``placeholders`` mark where the needs that lower into it land.
        preset_parameter: When the preset is written ``{$param: <name>}``, the parameter naming it.
        display: The English display template over the parameters (§2.4), or ``None``.
        source_path: The key path of the member's block in its file, for its line.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: ComponentEntry
    preset_parameter: Optional[str] = None
    display: Optional[str] = None
    source_path: Tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """The member's name, local to its assembly."""
        return self.entry.name


class VariantOption(BaseModel):
    """One option of an internal variant: the selector's values it covers and its own members."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    when: Tuple[Any, ...]
    components: Mapping[str, MemberTemplate] = Field(default_factory=dict)


class InternalVariant(BaseModel):
    """A parameter-selected internal variant (§2.6, §5.3): ``selected_by`` and its options."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    selected_by: str
    options: Mapping[str, VariantOption] = Field(default_factory=dict)

    def option_for(self, value: Any) -> Optional[VariantOption]:
        """The option whose ``when:`` covers a value of the selecting parameter."""
        return next((option for option in self.options.values() if value in option.when), None)


class MonotoneDirection(enum.Enum):
    """The direction a ``tests.monotone`` declaration expects (§9.4)."""

    INCREASING = "increasing"
    DECREASING = "decreasing"
    CONSTANT = "constant"


class BoundsDeclaration(BaseModel):
    """``tests.bounds``: an output (``Member.Output`` with its unit) or a KPI in a band."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output: Optional[str] = None
    kpi: Optional[str] = None
    member: Optional[str] = None
    unit: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None


class MonotoneDeclaration(BaseModel):
    """``tests.monotone``: one parameter rises, a KPI of a member (or a derived KPI) moves one way."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parameter: str
    kpi: str
    member: Optional[str] = None
    direction: MonotoneDirection


class ExpectDeclaration(BaseModel):
    """``tests.expect``: a KPI at the assembly's defaults lies in a band on the test weather."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kpi: str
    member: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None


class TestContract(BaseModel):
    """The test contract an assembly carries in its own file (§9.4, D24)."""

    # The name starts with "Test", which pytest would collect from a test module that imports it.
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
        name: The assembly's library path as the file states it (``pv/array``).
        description: What it is.
        parameters: Its parameters, in written order.
        exactly_one_of: Its constraints, each a tuple of parameter names.
        components: Its members outside every internal variant.
        variants: Its internal variants.
        ports: Its interface, every section's ports in one namespace.
        tests: Its test contract, or ``None`` when the file carries none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The schema version an assembly file is written in.
    SCHEMA_VERSION: ClassVar[int] = 4

    #: The value of ``kind`` that marks an assembly file.
    KIND: ClassVar[str] = "assembly"

    #: The grammar of a library path, ``<family>/<name>``: identifiers joined by ``/``.
    LIBRARY_PATH_PATTERN: ClassVar[Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)+$")

    #: The top-level keys, in canonical order.
    TOP_LEVEL_KEYS: ClassVar[Tuple[str, ...]] = (
        "schema_version",
        "kind",
        "name",
        "description",
        "parameters",
        "constraints",
        "components",
        "variants",
        "interface",
        "tests",
    )

    #: The sections of the interface.
    INTERFACE_SECTIONS: ClassVar[Tuple[str, ...]] = ("needs", "provides", "observes")

    name: str
    description: Optional[str] = None
    parameters: Mapping[str, ParameterDeclaration] = Field(default_factory=dict)
    exactly_one_of: Tuple[Tuple[str, ...], ...] = ()
    components: Mapping[str, MemberTemplate] = Field(default_factory=dict)
    variants: Mapping[str, InternalVariant] = Field(default_factory=dict)
    ports: Mapping[str, Port] = Field(default_factory=dict)
    tests: Optional[TestContract] = None

    def all_members(self) -> Tuple[MemberTemplate, ...]:
        """Every member template the file writes, each option's included, in written order."""
        members = list(self.components.values())
        for variant in self.variants.values():
            for option in variant.options.values():
                members.extend(option.components.values())
        return tuple(members)

    def member_names(self) -> Dict[str, str]:
        """Every member name, to the class path it is written with first."""
        names: Dict[str, str] = {}
        for member in self.all_members():
            names.setdefault(member.name, member.entry.class_path)
        return names
