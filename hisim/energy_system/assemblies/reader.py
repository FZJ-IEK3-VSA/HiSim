"""Reading an assembly file (``*.assembly.yaml``) into its model, keeping every line number.

The reader decides what the document alone decides: the version, ``kind`` and ``name``, the shape
of every block, the kind of every port, and the shape of the test contract. It raises the first
shape problem it meets (``EF-70``, or the format's own codes for a component entry), and refuses a
construct lean v1 does not have by name (``EF-73``): inner ``imports``, ``presets``, ``from:``,
``internal:``, ``order:``, ``$switch``/``$fact``/``$derived``, ``at_most_one_of``/``requires``,
``export``, ``priorities`` and ``actuates``. Whether the members' classes exist, whether ports name
real members and whether the variants partition their selector is the library check's
(:mod:`.library`), which lists every problem of a file at once.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from hisim.energy_system.assemblies.model import (
    NO_DEFAULT,
    AssemblyFile,
    BoundsDeclaration,
    ExpectDeclaration,
    InternalVariant,
    MemberTemplate,
    MonotoneDeclaration,
    MonotoneDirection,
    ParameterDeclaration,
    ParameterType,
    TestContract,
    VariantOption,
)
from hisim.energy_system.document import RawDocument
from hisim.energy_system.entries import EntryReader
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.imports_model import ParameterReference, Port
from hisim.energy_system.imports_reader import CutConstructs, ImportsReader
from hisim.energy_system.names import NameRules
from hisim.energy_system.source_lines import LineIndex


class AssemblyReader:
    """Builds an :class:`AssemblyFile` from a document, block by block."""

    #: The suffix an assembly file carries.
    SUFFIX = ".assembly.yaml"

    @classmethod
    def read_text(cls, text: str, label: str) -> Tuple[AssemblyFile, LineIndex]:
        """Reads the text of one assembly file: the model, and the line index of the same text.

        Args:
            text: The document.
            label: How the file is named in messages.

        Returns:
            The model and the line index.

        Raises:
            EnergySystemFormatError: For the first shape problem, or a construct v1 does not have.
        """
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
                f"{origin}.kind", f"an assembly file says 'kind: {AssemblyFile.KIND}', not {document.get('kind')!r}."
            )
        return AssemblyFile(
            name=RawDocument.string(document.get("name"), f"{origin}.name", required=True) or "",
            description=RawDocument.string(document.get("description"), f"{origin}.description", required=False),
            parameters=cls.parameters(document.get("parameters"), f"{origin}.parameters"),
            exactly_one_of=cls.constraints(document.get("constraints"), f"{origin}.constraints"),
            components=cls.members(document.get("components"), f"{origin}.components", ("components",)),
            variants=cls.variants(document.get("variants"), f"{origin}.variants"),
            ports=cls.interface(document.get("interface"), f"{origin}.interface"),
            tests=cls.tests(document.get("tests"), f"{origin}.tests") if "tests" in document else None,
        )

    @classmethod
    def parameters(cls, raw: Any, location: str) -> Dict[str, ParameterDeclaration]:
        """Builds the parameter declarations; a parameter named ``priorities`` is a cut construct."""
        declarations: Dict[str, ParameterDeclaration] = {}
        for name, value in RawDocument.mapping(raw, location).items():
            NameRules.check_identifier(name, location, "parameter")
            CutConstructs.refuse_keys({name: value}, location, ("priorities",))
            body_location = f"{location}.{name}"
            body = RawDocument.mapping(value, body_location)
            ImportsReader.check_keys(body, body_location, ParameterDeclaration.KEYS, "a parameter")
            try:
                parameter_type = ParameterType(body.get("type"))
            except ValueError as error:
                raise ImportsReader.shape_error(
                    f"{body_location}.type",
                    f"the parameter '{name}' has the type {body.get('type')!r}.",
                    allowed=[member.value for member in ParameterType],
                    offending=str(body.get("type")),
                ) from error
            values = body.get("values")
            if values is not None and (not isinstance(values, list) or not values):
                raise RawDocument.malformed(f"{body_location}.values", values, "a non-empty list of values")
            default: Any = parameter_type.written(body["default"]) if "default" in body else NO_DEFAULT
            declarations[name] = ParameterDeclaration(
                name=name,
                type=parameter_type,
                unit=RawDocument.string(body.get("unit"), f"{body_location}.unit", required=False),
                description=RawDocument.string(body.get("description"), f"{body_location}.description", required=False),
                default=default,
                values=tuple(values) if values is not None else None,
                range=cls.range(body.get("range"), f"{body_location}.range"),
            )
        return declarations

    @classmethod
    def range(cls, raw: Any, location: str) -> Optional[Tuple[float, float]]:
        """Reads ``range: {min, max}``."""
        if raw is None:
            return None
        block = RawDocument.mapping(raw, location)
        ImportsReader.check_keys(block, location, ("min", "max"), "a range")
        bounds = [cls.number(block, key, location, required=True) for key in ("min", "max")]
        low, high = float(bounds[0] or 0.0), float(bounds[1] or 0.0)
        if low > high:
            raise ImportsReader.shape_error(location, f"the range's min {low} lies above its max {high}.")
        return low, high

    @classmethod
    def constraints(cls, raw: Any, location: str) -> Tuple[Tuple[str, ...], ...]:
        """Reads the constraint list: ``exactly_one_of`` only (``at_most_one_of``/``requires`` are cut)."""
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise RawDocument.malformed(location, raw, "a list of constraints")
        constraints: List[Tuple[str, ...]] = []
        for index, item in enumerate(raw):
            item_location = f"{location}[{index}]"
            block = RawDocument.mapping(item, item_location)
            ImportsReader.check_keys(block, item_location, ("exactly_one_of",), "a constraint")
            names = block.get("exactly_one_of")
            if not isinstance(names, list) or len(names) < 2:
                raise RawDocument.malformed(
                    f"{item_location}.exactly_one_of", names, "a list of two or more parameters"
                )
            constraints.append(ImportsReader.names(names, f"{item_location}.exactly_one_of", "parameter"))
        return tuple(constraints)

    @classmethod
    def members(cls, raw: Any, location: str, source_path: Tuple[str, ...]) -> Dict[str, MemberTemplate]:
        """Reads a components block of an assembly: entries with local names and a display template."""
        members: Dict[str, MemberTemplate] = {}
        for name, value in RawDocument.mapping(raw, location).items():
            NameRules.check_identifier(name, location, "member")
            member_location = f"{location}.{name}"
            body = dict(RawDocument.mapping(value, member_location))
            display = RawDocument.string(body.pop("display", None), f"{member_location}.display", required=False)
            preset_parameter = ParameterReference.name_of(body.get("preset"))
            if preset_parameter is not None:
                body.pop("preset")
            for key in ("config", "constructor"):
                CutConstructs.refuse_values(body.get(key), f"{member_location}.{key}")
            entry = EntryReader.entry(name, body, member_location, placeholders=True)
            members[name] = MemberTemplate(
                entry=entry,
                preset_parameter=preset_parameter,
                display=display,
                source_path=source_path + (name,),
            )
        return members

    @classmethod
    def variants(cls, raw: Any, location: str) -> Dict[str, InternalVariant]:
        """Reads the internal variants: ``selected_by`` and options with ``when:`` and their members."""
        variants: Dict[str, InternalVariant] = {}
        for name, value in RawDocument.mapping(raw, location).items():
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
            options: Dict[str, VariantOption] = {}
            for option_name, option_value in options_block.items():
                NameRules.check_identifier(option_name, f"{variant_location}.options", "variant option")
                option_location = f"{variant_location}.options.{option_name}"
                option = RawDocument.mapping(option_value, option_location)
                ImportsReader.check_keys(option, option_location, ("when", "components"), "a variant option")
                when = option.get("when")
                if not isinstance(when, list) or not when:
                    raise RawDocument.malformed(f"{option_location}.when", when, "a non-empty list of values")
                options[option_name] = VariantOption(
                    name=option_name,
                    when=tuple(when),
                    components=cls.members(
                        option.get("components"),
                        f"{option_location}.components",
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
    def number(cls, block: Mapping[str, Any], key: str, location: str, *, required: bool = False) -> Optional[float]:
        """An optional (or required) number of a block."""
        value = block.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RawDocument.malformed(f"{location}.{key}", value, "a number")
        return float(value)

    @classmethod
    def tests(cls, raw: Any, location: str) -> TestContract:
        """Reads the test contract (§9.4): ``bounds``, ``monotone``, ``expect`` (at the defaults)."""
        block = RawDocument.mapping(raw, location)
        ImportsReader.check_keys(block, location, TestContract.KEYS, "a test contract")
        entries: Dict[str, List[Mapping[str, Any]]] = {}
        for key in TestContract.KEYS:
            items = block.get(key) or []
            if not isinstance(items, list):
                raise RawDocument.malformed(f"{location}.{key}", items, "a list")
            entries[key] = [RawDocument.mapping(item, f"{location}.{key}[{index}]") for index, item in enumerate(items)]
        bounds: List[BoundsDeclaration] = []
        for index, item in enumerate(entries["bounds"]):
            where = f"{location}.bounds[{index}]"
            ImportsReader.check_keys(item, where, ("output", "kpi", "member", "unit", "min", "max"), "a bounds entry")
            if ("output" in item) == ("kpi" in item):
                raise ImportsReader.shape_error(where, "a bounds entry names exactly one of 'output' and 'kpi'.")
            if "output" in item:
                NameRules.split_reference(item["output"], f"{where}.output", require_member=True)
                if "unit" not in item or "member" in item:
                    raise ImportsReader.shape_error(where, "a bounds entry on 'Member.Output' states its 'unit' alone.")
            strings = cls._strings(item, where, "output", "kpi", "member", "unit")
            bounds.append(BoundsDeclaration.model_validate({**cls._band(item, where), **strings}))
        monotone: List[MonotoneDeclaration] = []
        for index, item in enumerate(entries["monotone"]):
            where = f"{location}.monotone[{index}]"
            ImportsReader.check_keys(item, where, ("parameter", "kpi", "member", "direction"), "a monotone entry")
            try:
                direction = MonotoneDirection(item.get("direction"))
            except ValueError as error:
                raise ImportsReader.shape_error(
                    f"{where}.direction",
                    f"{item.get('direction')!r} is not a direction.",
                    allowed=[member.value for member in MonotoneDirection],
                ) from error
            strings = cls._strings(item, where, "parameter", "kpi", "member", required=("parameter", "kpi"))
            monotone.append(MonotoneDeclaration.model_validate({"direction": direction, **strings}))
        expect: List[ExpectDeclaration] = []
        for index, item in enumerate(entries["expect"]):
            where = f"{location}.expect[{index}]"
            ImportsReader.check_keys(item, where, ("kpi", "member", "min", "max"), "an expect entry")
            strings = cls._strings(item, where, "kpi", "member", required=("kpi",))
            expect.append(ExpectDeclaration.model_validate({**cls._band(item, where), **strings}))
        return TestContract(bounds=tuple(bounds), monotone=tuple(monotone), expect=tuple(expect))

    @classmethod
    def _band(cls, item: Mapping[str, Any], location: str) -> Dict[str, Optional[float]]:
        """``min`` and ``max`` of a bounds or expect entry, at least one of them."""
        band = {key: cls.number(item, key, location) for key in ("min", "max")}
        if band["min"] is None and band["max"] is None:
            raise ImportsReader.shape_error(
                location, "the entry states 'min', 'max' or both; a band without an end holds anything."
            )
        return band

    @classmethod
    def _strings(
        cls, item: Mapping[str, Any], location: str, *keys: str, required: Tuple[str, ...] = ()
    ) -> Dict[str, Optional[str]]:
        """The string fields of a test entry."""
        return {key: RawDocument.string(item.get(key), f"{location}.{key}", required=key in required) for key in keys}
