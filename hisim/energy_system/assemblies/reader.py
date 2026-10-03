"""Reading an assembly file (``*.assembly.yaml``) into its model, keeping every line number.

The reader decides what the document alone decides: the version and the ``kind``, the shape of
every block, the kind of every port by the keys it carries, and the shape of the test contract.
It raises the first shape problem it meets (``EF-70``, or the format's own ``EF-0x``/``EF-1x``
codes for a component entry), naming the key path. Whether a member's class exists, whether a
port's members and outputs are real, whether every parameter is documented and ranged and whether
the variants partition their selector are the library check's (:mod:`.library`), which lists every
problem of a file at once.

The line numbers come from a :class:`~hisim.energy_system.source_lines.LineIndex` built from the
same text, which the expansion turns into the source map of everything it produces (§9.2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from hisim.energy_system.assemblies.model import (
    NO_DEFAULT,
    AssemblyFile,
    BoundsDeclaration,
    Constraint,
    ConstraintKind,
    ExpectDeclaration,
    InternalVariant,
    InternalVariantOption,
    MemberTemplate,
    MonotoneDeclaration,
    ParameterDeclaration,
    ParameterType,
    TestContract,
    MonotoneDirection,
)
from hisim.energy_system.document import RawDocument
from hisim.energy_system.entries import EntryReader
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.imports_model import ParameterReference, Port
from hisim.energy_system.imports_reader import ImportsReader
from hisim.energy_system.names import NameRules
from hisim.energy_system.source_lines import LineIndex


class AssemblyReader:
    """Builds an :class:`AssemblyFile` from a document, block by block."""

    #: The suffix an assembly file carries.
    SUFFIX = ".assembly.yaml"

    #: The keys a member entry may carry beyond a component entry's: its relative ``order`` and
    #: its ``display`` template.
    MEMBER_EXTENSIONS: Tuple[str, ...] = ("order",)

    @classmethod
    def read(cls, source: Union[str, Path], origin: Optional[str] = None) -> Tuple[AssemblyFile, LineIndex]:
        """Reads a path or YAML text.

        Args:
            source: A path to an assembly file, or its text.
            origin: How the file is named in messages; its name, or ``<text>``, when omitted.

        Returns:
            The model and the line index of the document.

        Raises:
            EnergySystemFormatError: For the first shape problem.
        """
        if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and source.endswith(".yaml")):
            text = Path(source).read_text(encoding="utf-8")
            label = origin or Path(source).name
        else:
            text = str(source)
            label = origin or RawDocument.TEXT_ORIGIN
        document = RawDocument.parse_text(text, label)
        return cls.build(document, label), LineIndex.from_text(text, label)

    @classmethod
    def build(cls, document: Mapping[str, Any], origin: str) -> AssemblyFile:
        """Builds the model from a parsed document."""
        ImportsReader.check_keys(document, origin, AssemblyFile.TOP_LEVEL_KEYS, "an assembly file")
        version = document.get("schema_version")
        if version != AssemblyFile.SCHEMA_VERSION or isinstance(version, bool):
            raise EnergySystemFormatError(
                EnergySystemErrorId.SCHEMA_VERSION,
                f"{origin}.schema_version",
                f"an assembly file declares schema_version {AssemblyFile.SCHEMA_VERSION}, not {version!r}.",
                alternatives=[str(AssemblyFile.SCHEMA_VERSION)],
                alternatives_label="schema versions",
            )
        if document.get("kind") != AssemblyFile.KIND:
            raise ImportsReader.shape_error(
                f"{origin}.kind",
                f"an assembly file says 'kind: {AssemblyFile.KIND}', not {document.get('kind')!r}.",
            )
        return AssemblyFile(
            schema_version=AssemblyFile.SCHEMA_VERSION,
            name=RawDocument.string(document.get("name"), f"{origin}.name", required=False),
            description=RawDocument.string(document.get("description"), f"{origin}.description", required=False),
            parameters=cls.parameters(document.get("parameters"), f"{origin}.parameters"),
            constraints=cls.constraints(document.get("constraints"), f"{origin}.constraints"),
            presets=cls.presets(document.get("presets"), f"{origin}.presets"),
            components=cls.members(document.get("components"), f"{origin}.components", None, ("components",)),
            imports=ImportsReader.imports(document.get("imports"), f"{origin}.imports"),
            variants=cls.variants(document.get("variants"), f"{origin}.variants"),
            ports=cls.interface(document.get("interface"), f"{origin}.interface"),
            tests=cls.tests(document.get("tests"), f"{origin}.tests") if "tests" in document else None,
        )

    @classmethod
    def parameters(cls, raw: Any, location: str) -> Dict[str, ParameterDeclaration]:
        """Builds the parameter declarations."""
        block = RawDocument.mapping(raw, location)
        declarations: Dict[str, ParameterDeclaration] = {}
        for name, value in block.items():
            NameRules.check_identifier(name, location, "parameter")
            body_location = f"{location}.{name}"
            body = RawDocument.mapping(value, body_location)
            ImportsReader.check_keys(body, body_location, ParameterDeclaration.KEYS, "a parameter")
            written_type = body.get("type")
            try:
                parameter_type = ParameterType(written_type)
            except ValueError as error:
                raise ImportsReader.shape_error(
                    f"{body_location}.type",
                    f"the parameter '{name}' has the type {written_type!r}.",
                    allowed=[member.value for member in ParameterType],
                    offending=str(written_type),
                ) from error
            values = body.get("values")
            if values is not None and (not isinstance(values, list) or not values):
                raise RawDocument.malformed(f"{body_location}.values", values, "a non-empty list of values")
            declarations[name] = ParameterDeclaration(
                name=name,
                type=parameter_type,
                unit=RawDocument.string(body.get("unit"), f"{body_location}.unit", required=False),
                description=RawDocument.string(body.get("description"), f"{body_location}.description", required=False),
                default=cls.default(body, parameter_type),
                values=tuple(values) if values is not None else None,
                range=cls.range(body.get("range"), f"{body_location}.range"),
            )
        return declarations

    @classmethod
    def default(cls, body: Mapping[str, Any], parameter_type: ParameterType) -> Any:
        """Reads a default: ``none`` is "no value" for every type but a string."""
        if "default" not in body:
            return NO_DEFAULT
        value = body["default"]
        if value == "none" and parameter_type != ParameterType.STRING:
            return None
        return value

    @classmethod
    def range(cls, raw: Any, location: str) -> Optional[Tuple[float, float]]:
        """Reads ``range: {min, max}``."""
        if raw is None:
            return None
        block = RawDocument.mapping(raw, location)
        ImportsReader.check_keys(block, location, ("min", "max"), "a range")
        bounds = []
        for key in ("min", "max"):
            value = block.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RawDocument.malformed(f"{location}.{key}", value, "a number")
            bounds.append(float(value))
        if bounds[0] > bounds[1]:
            raise ImportsReader.shape_error(location, f"the range's min {bounds[0]} lies above its max {bounds[1]}.")
        return bounds[0], bounds[1]

    @classmethod
    def constraints(cls, raw: Any, location: str) -> Tuple[Constraint, ...]:
        """Reads the constraint list; each item is a one-key mapping."""
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise RawDocument.malformed(location, raw, "a list of constraints")
        constraints: List[Constraint] = []
        for index, item in enumerate(raw):
            item_location = f"{location}[{index}]"
            block = RawDocument.mapping(item, item_location)
            if len(block) != 1:
                raise ImportsReader.shape_error(
                    item_location,
                    "a constraint is a mapping with exactly one key.",
                    allowed=[kind.value for kind in ConstraintKind],
                )
            key, value = next(iter(block.items()))
            try:
                kind = ConstraintKind(key)
            except ValueError as error:
                raise ImportsReader.shape_error(
                    f"{item_location}.{key}",
                    f"'{key}' is not a constraint.",
                    allowed=[kind.value for kind in ConstraintKind],
                    offending=str(key),
                ) from error
            if kind == ConstraintKind.REQUIRES:
                mapping = RawDocument.mapping(value, f"{item_location}.{key}")
                requires = {
                    parameter: ImportsReader.names(needed, f"{item_location}.{key}.{parameter}", "parameter")
                    for parameter, needed in mapping.items()
                }
                constraints.append(Constraint(kind=kind, requires=requires))
            else:
                parameters = ImportsReader.names(value, f"{item_location}.{key}", "parameter")
                constraints.append(Constraint(kind=kind, parameters=parameters))
        return tuple(constraints)

    @classmethod
    def presets(cls, raw: Any, location: str) -> Dict[str, Mapping[str, Any]]:
        """Reads the presets: name to parameter values."""
        block = RawDocument.mapping(raw, location)
        presets: Dict[str, Mapping[str, Any]] = {}
        for name, values in block.items():
            NameRules.check_identifier(name, location, "preset")
            presets[name] = RawDocument.mapping(values, f"{location}.{name}")
        return presets

    @classmethod
    def members(
        cls, raw: Any, location: str, variant: Optional[Tuple[str, str]], source_path: Tuple[str, ...]
    ) -> Dict[str, MemberTemplate]:
        """Reads a components block of an assembly: entries with local names, an order and a display."""
        block = RawDocument.mapping(raw, location)
        members: Dict[str, MemberTemplate] = {}
        for name, value in block.items():
            NameRules.check_identifier(name, location, "member")
            member_location = f"{location}.{name}"
            body = dict(RawDocument.mapping(value, member_location))
            display = RawDocument.string(body.pop("display", None), f"{member_location}.display", required=False)
            preset_parameter = ParameterReference.name_of(body.get("preset"))
            if preset_parameter is not None:
                body.pop("preset")
            entry = EntryReader.entry(name, body, member_location, extensions=cls.MEMBER_EXTENSIONS, placeholders=True)
            members[name] = MemberTemplate(
                entry=entry,
                preset_parameter=preset_parameter,
                display=display,
                variant=variant,
                source_path=source_path + (name,),
            )
        return members

    @classmethod
    def variants(cls, raw: Any, location: str) -> Dict[str, InternalVariant]:
        """Reads the internal variants: ``selected_by`` and options with ``when:`` and members."""
        block = RawDocument.mapping(raw, location)
        variants: Dict[str, InternalVariant] = {}
        for name, value in block.items():
            NameRules.check_identifier(name, location, "variant")
            variant_location = f"{location}.{name}"
            body = RawDocument.mapping(value, variant_location)
            ImportsReader.check_keys(body, variant_location, ("selected_by", "options"), "an internal variant")
            selector = NameRules.check_identifier(
                body.get("selected_by"), f"{variant_location}.selected_by", "parameter"
            )
            options_block = RawDocument.mapping(body.get("options"), f"{variant_location}.options")
            if not options_block:
                raise RawDocument.malformed(f"{variant_location}.options", body.get("options"), "at least one option")
            options: Dict[str, InternalVariantOption] = {}
            for option_name, option_value in options_block.items():
                NameRules.check_identifier(option_name, f"{variant_location}.options", "variant option")
                option_location = f"{variant_location}.options.{option_name}"
                option = RawDocument.mapping(option_value, option_location)
                ImportsReader.check_keys(option, option_location, ("when", "components"), "a variant option")
                when = option.get("when")
                if not isinstance(when, list) or not when:
                    raise RawDocument.malformed(f"{option_location}.when", when, "a non-empty list of values")
                options[option_name] = InternalVariantOption(
                    name=option_name,
                    when=tuple(when),
                    components=cls.members(
                        option.get("components"),
                        f"{option_location}.components",
                        (name, option_name),
                        ("variants", name, "options", option_name, "components"),
                    ),
                )
            variants[name] = InternalVariant(name=name, selected_by=selector, options=options)
        return variants

    @classmethod
    def interface(cls, raw: Any, location: str) -> Dict[str, Port]:
        """Reads the interface: every section's ports in one namespace."""
        block = RawDocument.mapping(raw, location)
        ImportsReader.check_keys(block, location, AssemblyFile.INTERFACE_SECTIONS, "an interface")
        ports: Dict[str, Port] = {}
        for section in AssemblyFile.INTERFACE_SECTIONS:
            for name, port in ImportsReader.ports(block.get(section), f"{location}.{section}", section).items():
                if name in ports:
                    raise ImportsReader.shape_error(
                        f"{location}.{section}.{name}",
                        f"the port '{name}' is declared under '{ports[name].section}' and again under '{section}'.",
                    )
                ports[name] = port
        return ports

    @classmethod
    def tests(cls, raw: Any, location: str) -> TestContract:
        """Reads the test contract (§9.4): ``bounds``, ``monotone``, ``expect``."""
        block = RawDocument.mapping(raw, location)
        ImportsReader.check_keys(block, location, TestContract.KEYS, "a test contract")
        return TestContract(
            bounds=tuple(
                cls._bounds(item, f"{location}.bounds[{index}]")
                for index, item in enumerate(cls._list(block.get("bounds"), f"{location}.bounds"))
            ),
            monotone=tuple(
                cls._monotone(item, f"{location}.monotone[{index}]")
                for index, item in enumerate(cls._list(block.get("monotone"), f"{location}.monotone"))
            ),
            expect=tuple(
                cls._expect(item, f"{location}.expect[{index}]")
                for index, item in enumerate(cls._list(block.get("expect"), f"{location}.expect"))
            ),
        )

    @classmethod
    def _list(cls, raw: Any, location: str) -> List[Any]:
        """A list block, absent meaning empty."""
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise RawDocument.malformed(location, raw, "a list")
        return raw

    @classmethod
    def _number(cls, block: Mapping[str, Any], key: str, location: str) -> Optional[float]:
        """An optional number."""
        value = block.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RawDocument.malformed(f"{location}.{key}", value, "a number")
        return float(value)

    @classmethod
    def _bounds(cls, raw: Any, location: str) -> BoundsDeclaration:
        """One ``bounds`` entry: an output with its unit, or a KPI with its member; min and/or max."""
        block = RawDocument.mapping(raw, location)
        ImportsReader.check_keys(block, location, ("output", "kpi", "member", "unit", "min", "max"), "a bounds entry")
        if ("output" in block) == ("kpi" in block):
            raise ImportsReader.shape_error(location, "a bounds entry names exactly one of 'output' and 'kpi'.")
        if "output" in block:
            NameRules.split_reference(block["output"], f"{location}.output", require_member=True)
            if "unit" not in block:
                raise ImportsReader.shape_error(location, "a bounds entry on an output declares the output's 'unit'.")
            if "member" in block:
                raise ImportsReader.shape_error(
                    location, "an output is written 'Member.Output'; 'member' is for a KPI."
                )
        elif "member" not in block:
            raise ImportsReader.shape_error(location, "a bounds entry on a KPI names the 'member' that reports it.")
        if "min" not in block and "max" not in block:
            raise ImportsReader.shape_error(location, "a bounds entry states 'min', 'max' or both.")
        return BoundsDeclaration(
            output=block.get("output"),
            kpi=RawDocument.string(block.get("kpi"), f"{location}.kpi", required=False),
            member=RawDocument.string(block.get("member"), f"{location}.member", required=False),
            unit=RawDocument.string(block.get("unit"), f"{location}.unit", required=False),
            min=cls._number(block, "min", location),
            max=cls._number(block, "max", location),
        )

    @classmethod
    def _monotone(cls, raw: Any, location: str) -> MonotoneDeclaration:
        """One ``monotone`` entry."""
        block = RawDocument.mapping(raw, location)
        ImportsReader.check_keys(block, location, ("parameter", "kpi", "member", "direction"), "a monotone entry")
        written = block.get("direction")
        try:
            direction = MonotoneDirection(written)
        except ValueError as error:
            raise ImportsReader.shape_error(
                f"{location}.direction",
                f"'{written}' is not a direction.",
                allowed=[member.value for member in MonotoneDirection],
                offending=str(written),
            ) from error
        return MonotoneDeclaration(
            parameter=RawDocument.string(block.get("parameter"), f"{location}.parameter", required=True) or "",
            kpi=RawDocument.string(block.get("kpi"), f"{location}.kpi", required=True) or "",
            member=RawDocument.string(block.get("member"), f"{location}.member", required=True) or "",
            direction=direction,
        )

    @classmethod
    def _expect(cls, raw: Any, location: str) -> ExpectDeclaration:
        """One ``expect`` entry."""
        block = RawDocument.mapping(raw, location)
        ImportsReader.check_keys(block, location, ("preset", "kpi", "member", "min", "max"), "an expect entry")
        if "min" not in block and "max" not in block:
            raise ImportsReader.shape_error(location, "an expect entry states 'min', 'max' or both.")
        return ExpectDeclaration(
            preset=RawDocument.string(block.get("preset"), f"{location}.preset", required=True) or "",
            kpi=RawDocument.string(block.get("kpi"), f"{location}.kpi", required=True) or "",
            member=RawDocument.string(block.get("member"), f"{location}.member", required=True) or "",
            min=cls._number(block, "min", location),
            max=cls._number(block, "max", location),
        )
