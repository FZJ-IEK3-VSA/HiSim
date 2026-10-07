"""Reading the blocks assemblies add to the format: imports, binding verbs, ports and placeholders.

Both files that write these blocks read them here — the energy-system file of schema version 4
(its ``imports`` and the ports and verbs on its top-level entries) and the assembly file (its
interface) — so the two spell every block alike and refuse the same mistakes with the same message.
Everything decided here is decidable from the document alone; whether a port's members, partners
and outputs exist is the library check's and the expansion's business.

A shape problem is ``EF-70``. A construct of the design that lean v1 does not have
(``assemblies_spec.md`` §13.1, D26) is refused by name with ``EF-73`` (:class:`CutConstructs`): it
is never read as an unknown key or, worse, ignored.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple

from hisim import loadtypes as lt
from hisim.energy_system.document import RawDocument
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.imports_model import (
    BindingVerbs,
    ImportEntry,
    InstanceEntry,
    ParameterReference,
    PlacedPlaceholder,
    Port,
    PortKind,
    PortPlaceholder,
    Selection,
    Selector,
)
from hisim.energy_system.names import NameRules


class CutConstructs:
    """The constructs of the design that lean v1 does not have, each refused by name (D26)."""

    #: What each cut construct was, and what v1 does instead (``assemblies_spec.md`` §13.1).
    DECISIONS: ClassVar[Mapping[str, str]] = {
        "imports": "nesting is not in v1: assemblies are flat, and a shared sub-structure is duplicated",
        "from": "re-exports belong to nesting, which is not in v1: assemblies are flat",
        "internal": "internal ports belong to nesting, which is not in v1: assemblies are flat",
        "order": "order: is a flat integer on a top-level component or an import only, never on an instance, an "
        "assembly member or a group's or variant's component (revised 2026-10-07)",
        "presets": "presets inside assemblies are not in v1: parameters have defaults and importers set values",
        "preset": "assembly presets are not in v1: an import sets parameter values; the defaults are the preset",
        "$switch": "$switch is not in v1: $param is the only placeholder; per-variant values live in the options",
        "$fact": "$fact is not in v1: $param is the only placeholder; a field following a fact has a law",
        "$derived": "$derived is not in v1: $param is the only placeholder",
        "export": "fact exports are not in v1: a fact read from two providers is ambiguous; write sizing_sources",
        "at_most_one_of": "at_most_one_of is not in v1: express it as an enum parameter selecting a variant",
        "requires": "requires is not in v1: express it as an enum parameter selecting a variant",
        "priorities": "priorities are not in v1: the controller ranks what its class ranks, in the class's order",
        "actuates": "actuates is not in v1: a controllable output (controllable:) states what a controller drives",
        "feed": "feed: overrides are not in v1: a feed has the tags and weight its observer's class declares",
        "required": "required: is not in v1: a selector matching nothing is refused, whatever it says",
    }

    @classmethod
    def error(cls, location: str, construct: str) -> EnergySystemFormatError:
        """The refusal of one cut construct (``EF-73``)."""
        return EnergySystemFormatError(
            EnergySystemErrorId.NOT_IN_V1,
            location,
            f"'{construct}': {cls.DECISIONS[construct]} (assemblies_spec.md §13.1, D26).",
        )

    @classmethod
    def refuse_keys(cls, block: Mapping[str, Any], location: str, constructs: Sequence[str]) -> None:
        """Refuses the first key of ``block`` that is one of the given cut constructs."""
        for key in block:
            if key in constructs:
                raise cls.error(f"{location}.{key}", key)

    @classmethod
    def refuse_values(cls, value: Any, location: str) -> None:
        """Refuses a ``$switch``/``$fact``/``$derived`` anywhere in a value tree, and a malformed ``$param``."""
        for key, path in ParameterReference.walk(value):
            where = ".".join((location,) + path)
            if key in ParameterReference.CUT_KEYS:
                raise cls.error(where, key)
            if key == ParameterReference.KEY:
                raise ImportsReader.shape_error(where, "a parameter reference is written {$param: <name>}, alone.")


class ImportsReader:
    """Builds imports, verbs, ports and placeholders from their raw blocks, one block at a time."""

    #: The keys that switch a port by the parameters of its assembly (§3.1, D8 ii).
    CONDITION_KEYS: ClassVar[Tuple[str, ...]] = ("required_when", "active_when")

    #: The keys a port of each kind may carry besides the state keys; the first marks the kind.
    KIND_KEYS: ClassVar[Mapping[PortKind, Tuple[str, ...]]] = {
        PortKind.NEED: ("partner", "into", "wires"),
        PortKind.PROVIDED: ("output", "controllable"),
        PortKind.CIRCUIT: ("circuit", "member"),
        PortKind.CARRIER: ("carrier", "outputs", "meter"),
        PortKind.FACT: ("fact", "into", "member", "many"),
        PortKind.OBSERVER: ("into", "default"),
    }

    #: The kinds each section may hold; a site entry's ``ports`` hold needs, circuit ends and carriers.
    SECTION_KINDS: ClassVar[Mapping[str, Tuple[PortKind, ...]]] = {
        "needs": (PortKind.NEED, PortKind.CIRCUIT, PortKind.CARRIER, PortKind.FACT),
        "provides": (PortKind.PROVIDED, PortKind.CIRCUIT, PortKind.CARRIER, PortKind.FACT),
        "observes": (PortKind.OBSERVER,),
        "ports": (PortKind.NEED, PortKind.CIRCUIT, PortKind.CARRIER),
    }

    #: The keys of a provided output's ``controllable`` block (§4.4): one of the first two.
    CONTROLLABLE_KEYS: ClassVar[Tuple[str, ...]] = ("target_input", "via", "optional")

    #: The keys a section allows of a kind; the rest a port of that kind there must not carry.
    SECTION_KEYS: ClassVar[Mapping[Tuple[str, PortKind], Tuple[str, ...]]] = {
        ("needs", PortKind.CARRIER): ("carrier", "outputs"),
        ("provides", PortKind.CARRIER): ("carrier", "meter"),
        ("ports", PortKind.CARRIER): ("carrier", "outputs"),
        ("needs", PortKind.FACT): ("fact", "into", "many"),
        ("provides", PortKind.FACT): ("fact", "member"),
        ("ports", PortKind.CIRCUIT): ("circuit",),
    }

    @classmethod
    def shape_error(
        cls, location: str, problem: str, *, allowed: Optional[Sequence[str]] = None, offending: Optional[str] = None
    ) -> EnergySystemFormatError:
        """The one rejection of a block this module reads (``EF-70``)."""
        return EnergySystemFormatError(
            EnergySystemErrorId.ASSEMBLY_SHAPE,
            location,
            problem,
            alternatives=allowed,
            alternatives_label="keys",
            offending_value=offending,
        )

    @classmethod
    def check_keys(cls, block: Mapping[str, Any], location: str, allowed: Sequence[str], role: str) -> None:
        """Refuses a key the block does not know (a cut construct by name, before the generic refusal)."""
        CutConstructs.refuse_keys(block, location, tuple(key for key in CutConstructs.DECISIONS if key not in allowed))
        for key in block:
            if key not in allowed:
                raise cls.shape_error(
                    f"{location}.{key}", f"'{key}' is not a key of {role}.", allowed=allowed, offending=str(key)
                )

    @classmethod
    def names(cls, value: Any, location: str, role: str) -> Tuple[str, ...]:
        """Reads one name or a non-empty list of names."""
        items = value if isinstance(value, list) else [value]
        if not items:
            raise RawDocument.malformed(location, value, f"a {role} name or a non-empty list of them")
        return tuple(NameRules.check_identifier(item, location, role) for item in items)

    @classmethod
    def conditions(cls, value: Any, location: str) -> Dict[str, Tuple[Any, ...]]:
        """Reads ``required_when``/``active_when``: parameter to a non-empty list of values (§3.1, D8 ii)."""
        conditions: Dict[str, Tuple[Any, ...]] = {}
        for parameter, values in RawDocument.mapping(value, location).items():
            NameRules.check_identifier(parameter, location, "parameter")
            if not isinstance(values, list) or not values:
                raise RawDocument.malformed(f"{location}.{parameter}", values, "a non-empty list of values")
            conditions[parameter] = tuple(values)
        return conditions

    @classmethod
    def port(cls, name: str, raw: Any, location: str, section: str, site_entry: Optional[str] = None) -> Port:
        """Builds one port, classifying it by the key that marks its kind.

        Args:
            name: The port's key.
            raw: Its block.
            location: Key path of the block.
            section: ``needs``, ``provides``, ``observes`` or ``ports`` (a site entry's).
            site_entry: For a site entry's port, the entry's name: the port lowers into the entry,
                and its values are plain (a site entry has no parameters).

        Returns:
            The port.

        Raises:
            EnergySystemFormatError: ``EF-70`` for a block of no kind the section allows or a key
                its kind does not carry, ``EF-73`` for a cut construct (``from:``, ``export:``).
        """
        NameRules.check_identifier(name, location, "port")
        block = RawDocument.mapping(raw, location)
        kinds = cls.SECTION_KINDS[section]
        kind = next((kind for kind in kinds if cls.KIND_KEYS[kind][0] in block), None)
        if section == "observes":
            kind = PortKind.OBSERVER
        if kind is None:
            CutConstructs.refuse_keys(block, location, ("from", "export"))
            markers = [cls.KIND_KEYS[kind][0] for kind in kinds]
            raise cls.shape_error(
                location, f"the port '{name}' under '{section}' carries none of {', '.join(markers)}.", allowed=markers
            )
        state_keys = ("optional",) if site_entry is not None else ("optional",) + cls.CONDITION_KEYS
        cls.check_keys(block, location, cls.KIND_KEYS[kind] + state_keys, f"a {kind.value} port")
        if site_entry is not None:
            CutConstructs.refuse_values(block, location)
            if ParameterReference.walk(block):
                raise cls.shape_error(location, f"a site entry has no parameters; the port '{name}' writes {{$param}}.")
        optional = block.get("optional", False)
        if not isinstance(optional, bool):
            raise RawDocument.malformed(f"{location}.optional", optional, "true or false")
        if optional and "required_when" in block:
            raise cls.shape_error(
                location,
                f"the port '{name}' is 'optional: true' and has 'required_when' at once, which contradict each "
                "other; an optional port switched off by parameters writes 'active_when'.",
            )
        fields: Dict[str, Any] = {
            "name": name,
            "section": section,
            "kind": kind,
            "optional": optional,
            "required_when": cls.conditions(block.get("required_when"), f"{location}.required_when"),
            "active_when": cls.conditions(block.get("active_when"), f"{location}.active_when"),
            "raw": block,
        }
        allowed = cls.SECTION_KEYS.get((section, kind))
        for key in block:
            if allowed is not None and key in cls.KIND_KEYS[kind] and key not in allowed:
                raise cls.shape_error(
                    f"{location}.{key}", f"a {kind.value} port under '{section}' carries no '{key}'.", allowed=allowed
                )
        if kind == PortKind.NEED:
            fields["partner"] = cls.names(block.get("partner"), f"{location}.partner", "partner class")
            if "wires" in block:
                wires = RawDocument.mapping(block["wires"], f"{location}.wires")
                for target, source in wires.items():
                    NameRules.check_identifier(target, f"{location}.wires", "input")
                    NameRules.check_identifier(source, f"{location}.wires.{target}", "output")
                fields["wires"] = wires
        elif kind == PortKind.PROVIDED:
            NameRules.split_reference(block.get("output"), f"{location}.output", require_member=True)
            fields["output"] = block["output"]
            if "controllable" in block:
                fields["controllable"] = cls._controllable(block["controllable"], f"{location}.controllable")
        elif kind == PortKind.CIRCUIT:
            fields["circuit"] = NameRules.check_identifier(block.get("circuit"), f"{location}.circuit", "circuit")
        elif kind == PortKind.CARRIER:
            carriers = [carrier.value for carrier in lt.EnergyBalanceCarrier]
            if block.get("carrier") not in carriers:
                raise cls.shape_error(
                    f"{location}.carrier",
                    f"the carrier port '{name}' names {block.get('carrier')!r}, which is no energy carrier.",
                    allowed=carriers,
                    offending=str(block.get("carrier")),
                )
            fields["carrier"] = block["carrier"]
            if "outputs" in block or section == "needs":
                items = block.get("outputs")
                if not isinstance(items, list) or not items:
                    raise RawDocument.malformed(f"{location}.outputs", items, "a non-empty list of consuming outputs")
                for item in items:
                    if site_entry is not None:
                        NameRules.check_identifier(item, f"{location}.outputs", "output")
                    else:
                        NameRules.split_reference(item, f"{location}.outputs", require_member=True)
                fields["outputs"] = tuple(items)
            elif "meter" in block:
                fields["meter"] = NameRules.check_identifier(block["meter"], f"{location}.meter", "member")
        elif kind == PortKind.FACT:
            fields["fact"] = NameRules.check_identifier(block.get("fact"), f"{location}.fact", "fact")
            many = block.get("many", False)
            if not isinstance(many, bool):
                raise RawDocument.malformed(f"{location}.many", many, "true or false")
            fields["many"] = many
        else:
            if block.get("default") is None:
                raise cls.shape_error(location, f"the observer port '{name}' states its 'default' selection.")
            fields["selection"] = cls.selection(block["default"], f"{location}.default")
        cls._members(kind, section, block, location, site_entry, fields)
        return Port(**fields)

    @classmethod
    def _members(
        cls,
        kind: PortKind,
        section: str,
        block: Mapping[str, Any],
        location: str,
        site_entry: Optional[str],
        fields: Dict[str, Any],
    ) -> None:
        """Fills a port's ``into`` and ``members``, and refuses a port without the members its kind needs.

        A site entry's port lowers into the entry, which is its one member; an assembly's port names
        its members: ``into`` (a need, a fact need, an observer), ``member`` (a circuit end, a provided
        fact), ``meter`` (a fuel provision), and the members of a carrier need's consuming outputs.
        """
        if site_entry is not None:
            for key in ("into", "member", "meter"):
                if key in block:
                    raise cls.shape_error(f"{location}.{key}", "a site entry's port lowers into the entry itself.")
            fields["members"] = (site_entry,)
            if kind == PortKind.NEED:
                fields["into"] = (site_entry,)
            return
        named = {
            key: cls.names(block[key], f"{location}.{key}", "member") for key in ("member", "meter") if key in block
        }
        lands = kind in (PortKind.NEED, PortKind.OBSERVER) or (kind == PortKind.FACT and section == "needs")
        if lands:
            fields["into"] = cls.names(block.get("into"), f"{location}.into", "member")
        if kind == PortKind.CIRCUIT and "member" not in named:
            raise RawDocument.malformed(f"{location}.member", None, "the member or members at this end of the circuit")
        if kind == PortKind.FACT and section == "provides" and len(named.get("member", ())) != 1:
            raise RawDocument.malformed(f"{location}.member", block.get("member"), "the one member providing the fact")
        if kind == PortKind.CARRIER and section == "provides" and fields["carrier"] != "electricity" and not named:
            raise cls.shape_error(location, f"the provision of the fuel {fields['carrier']} names its 'meter:'.")
        if kind == PortKind.CARRIER and fields["carrier"] == "electricity" and "meter" in named:
            raise cls.shape_error(
                f"{location}.meter", "electricity has no link: its meter observes (observes:); no feed lands in it."
            )
        consumers = tuple(item.split(".", 1)[0] for item in fields.get("outputs", ()))
        members = fields.get("into", ()) + named.get("member", ()) + named.get("meter", ()) + consumers
        fields["members"] = tuple(dict.fromkeys(members))

    @classmethod
    def _controllable(cls, raw: Any, location: str) -> Dict[str, Any]:
        """Reads ``controllable: {target_input: …}`` or ``{via: <need>}`` (§4.4)."""
        block = RawDocument.mapping(raw, location)
        cls.check_keys(block, location, cls.CONTROLLABLE_KEYS, "a controllable block")
        if ("target_input" in block) == ("via" in block):
            raise cls.shape_error(location, "a controllable output names exactly one of 'target_input' and 'via'.")
        key = "target_input" if "target_input" in block else "via"
        NameRules.check_identifier(block[key], f"{location}.{key}", "input" if key == "target_input" else "need")
        if "optional" in block and (key == "via" or not isinstance(block["optional"], bool)):
            raise cls.shape_error(f"{location}.optional", "'optional: true|false' goes with a 'target_input' only.")
        return dict(block)

    @classmethod
    def selection(cls, raw: Any, location: str) -> Selection:
        """Reads a selection: ``declared``, or a non-empty list of selectors (§4.1)."""
        if raw == Selection.DECLARED:
            return Selection()
        if not isinstance(raw, list) or not raw:
            raise RawDocument.malformed(location, raw, "'declared' or a non-empty list of selectors")
        vocabularies = {
            "component_type": tuple(lt.ComponentType.__members__),
            "flow": tuple(lt.InandOutputType.__members__),
        }
        selectors = []
        for index, item in enumerate(raw):
            where = f"{location}[{index}]"
            block = RawDocument.mapping(item, where)
            cls.check_keys(block, where, Selector.KEYS, "a selector")
            if not block:
                raise cls.shape_error(where, "an empty selector selects everything; write 'declared' for that.")
            values: Dict[str, Tuple[str, ...]] = {}
            for key, value in block.items():
                values[key] = cls.names(value, f"{where}.{key}", key.replace("_", " "))
                vocabulary = vocabularies.get(key)
                unknown = [name for name in values[key] if vocabulary is not None and name not in vocabulary]
                if unknown:
                    raise cls.shape_error(
                        f"{where}.{key}", f"'{unknown[0]}' is no {key} tag.", allowed=vocabulary, offending=unknown[0]
                    )
            selectors.append(Selector(**values))
        return Selection(selectors=tuple(selectors))

    @classmethod
    def ports(cls, raw: Any, location: str, section: str, site_entry: Optional[str] = None) -> Dict[str, Port]:
        """Builds every port of one section."""
        block = RawDocument.mapping(raw, location)
        return {name: cls.port(name, value, f"{location}.{name}", section, site_entry) for name, value in block.items()}

    @classmethod
    def verbs(cls, block: Mapping[str, Any], location: str) -> BindingVerbs:
        """Reads the three binding verbs of an entry or an import (§3.1)."""
        bind = cls._verb_mapping(block.get("bind"), f"{location}.bind")
        optional_bind = cls._verb_mapping(block.get("optional-bind"), f"{location}.optional-bind")
        none_value = block.get("none")
        none: Tuple[str, ...] = ()
        if none_value is not None:
            if not isinstance(none_value, list):
                raise RawDocument.malformed(f"{location}.none", none_value, "a list of port names")
            none = tuple(NameRules.check_identifier(port, f"{location}.none", "port") for port in none_value)
        written: Dict[str, str] = {}
        for verb, ports in (("bind", tuple(bind)), ("optional-bind", tuple(optional_bind)), ("none", none)):
            for port in ports:
                if port in written:
                    raise cls.shape_error(
                        f"{location}.{verb}.{port}",
                        f"the port '{port}' carries two verbs, '{written[port]}' and '{verb}'.",
                    )
                written[port] = verb
        return BindingVerbs(bind=bind, optional_bind=optional_bind, none=none)

    @classmethod
    def _verb_mapping(cls, raw: Any, location: str) -> Dict[str, str]:
        """Reads ``bind:`` or ``optional-bind:``: port to partner reference."""
        block = RawDocument.mapping(raw, location)
        for port, partner in block.items():
            NameRules.check_identifier(port, location, "port")
            if not isinstance(partner, str) or not partner or partner.count(NameRules.REFERENCE_SEPARATOR) > 2:
                raise RawDocument.malformed(
                    f"{location}.{port}", partner, "<component>, <import>[.<instance>][.<port>]"
                )
            for part in partner.split(NameRules.REFERENCE_SEPARATOR):
                NameRules.check_identifier(part, f"{location}.{port}", "partner")
        return dict(block)

    @classmethod
    def inputs_with_placeholders(cls, raw: Any, location: str) -> Tuple[Tuple[Any, ...], Tuple[PlacedPlaceholder, ...]]:
        """Splits an input list into its raw ordinary items and its placeholders with their positions."""
        if raw is None:
            return (), ()
        if not isinstance(raw, list):
            raise RawDocument.malformed(location, raw, "a list of input items")
        items = []
        placed = []
        for index, item in enumerate(raw):
            keys = set(item) if isinstance(item, dict) else set()
            if "$observes" in keys:
                raise cls.shape_error(
                    f"{location}[{index}]",
                    "an observer's selected feeds follow its own inputs; it writes no placeholder.",
                )
            if PortPlaceholder.KEY in keys:
                item_location = f"{location}[{index}]"
                cls.check_keys(item, item_location, (PortPlaceholder.KEY,), "a placeholder")
                name = NameRules.check_identifier(item[PortPlaceholder.KEY], item_location, "port")
                placed.append(PlacedPlaceholder(position=index, placeholder=PortPlaceholder(port=name)))
            else:
                items.append((index, item))
        return tuple(items), tuple(placed)

    @classmethod
    def imports(cls, raw: Any, location: str) -> Dict[str, ImportEntry]:
        """Builds the ``imports`` block of an energy-system file."""
        block = RawDocument.mapping(raw, location)
        return {name: cls.import_entry(name, value, f"{location}.{name}") for name, value in block.items()}

    @classmethod
    def import_entry(cls, name: str, raw: Any, location: str) -> ImportEntry:
        """Builds one import (§2.2)."""
        NameRules.check_identifier(name, location, "import")
        block = RawDocument.mapping(raw, location)
        cls.check_keys(block, location, ImportEntry.IMPORT_KEYS, "an import")
        parameters = RawDocument.mapping(block.get("parameters"), f"{location}.parameters")
        CutConstructs.refuse_values(parameters, f"{location}.parameters")
        if ParameterReference.walk(parameters):
            raise cls.shape_error(f"{location}.parameters", "an energy-system file has no parameters to refer to.")
        instances: Optional[Dict[str, InstanceEntry]] = None
        if "instances" in block:
            if "parameters" in block:
                raise cls.shape_error(
                    location,
                    f"the import '{name}' has instances and import-level parameters; each instance states its own.",
                )
            written = RawDocument.mapping(block["instances"], f"{location}.instances")
            if not written:
                raise RawDocument.malformed(f"{location}.instances", block["instances"], "at least one instance")
            instances = {key: cls.instance(key, value, f"{location}.instances.{key}") for key, value in written.items()}
        return ImportEntry(
            name=name,
            assembly=RawDocument.string(block.get("assembly"), f"{location}.assembly", required=True) or "",
            parameters=parameters,
            instances=instances,
            verbs=cls.verbs(block, location),
            observes=cls.selection(block["observes"], f"{location}.observes") if "observes" in block else None,
            order=cls.order(block, location),
            **cls._reserved(block, location),
        )

    @classmethod
    def instance(cls, name: str, raw: Any, location: str) -> InstanceEntry:
        """Builds one instance: its parameter values, with the reserved fields beside them."""
        NameRules.check_identifier(name, location, "instance")
        block = dict(RawDocument.mapping(raw, location))
        CutConstructs.refuse_keys(block, location, ("preset", "order"))
        reserved = cls._reserved(block, location)
        parameters = {key: value for key, value in block.items() if key not in ImportEntry.RESERVED_KEYS}
        CutConstructs.refuse_values(parameters, location)
        if ParameterReference.walk(parameters):
            raise cls.shape_error(location, "an energy-system file has no parameters to refer to.")
        return InstanceEntry(name=name, parameters=parameters, **reserved)

    @classmethod
    def order(cls, block: Mapping[str, Any], location: str) -> Optional[int]:
        """Reads the flat ``order:`` of a top-level entry or an import, an integer (D26 revised, §2.3)."""
        value = block.get("order")
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise RawDocument.malformed(f"{location}.order", value, "an integer")
        return value

    @classmethod
    def _reserved(cls, block: Mapping[str, Any], location: str) -> Dict[str, Any]:
        """Reads ``installation_year`` (an integer) and ``quote`` (a mapping), recorded only (D18, D22)."""
        reserved: Dict[str, Any] = {}
        year = block.get("installation_year")
        if year is not None:
            if isinstance(year, bool) or not isinstance(year, int):
                raise RawDocument.malformed(f"{location}.installation_year", year, "a year, as an integer")
            reserved["installation_year"] = year
        if block.get("quote") is not None:
            reserved["quote"] = RawDocument.mapping(block["quote"], f"{location}.quote")
        return reserved
