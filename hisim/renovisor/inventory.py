"""The home inventory as a path-addressed document, validated against the vendored contract.

A *home inventory* is the contract's ``HomeInventoryInput``: everything known about a dwelling
before any renovation — where it stands, how it is built, who lives in it, what heats it. The
translation layer reads and writes it by dotted path, because a path is the one vocabulary the
measure layer and the binding layer share (requirement M6): a measure writes
``energy_system_config.heating_system.system`` and knows nothing about which HiSim component ends
up carrying that value.

Example::

    inventory = Inventory.from_dict(request["home_inventory"])
    inventory.get("building_config.general.construction_year")      # 1988
    inventory.set("building_config.envelope_details.roof_u_value_in_watt_per_m2_per_kelvin", 0.16)
    inventory.validate()                                            # raises on a schema violation

Validation uses the ``HomeInventoryInput`` schema of the vendored ``openapi.yaml``. Two details
make that less trivial than it sounds: OpenAPI 3.0's ``nullable: true`` is not a JSON Schema
keyword and has to be rewritten into a nullable type before a validator sees it
(:class:`OpenApiSchemaAdapter`), and the schema's ``$ref``s point into the surrounding document,
so the whole document is registered as the resolution base.

:class:`PendingContractPaths` lists the paths this step reads or writes that the vendored contract
does not define yet, each with the decision that adds it. It is a to-do list with a test behind it
(check 4 of ``measures_v2_requirements.md`` §7.5): every path a measure writes must be in the
schema or on this list, and the contract PR of step 6 empties the list.
"""

import copy
from typing import Any, ClassVar, Dict, Iterator, List, Mapping, Optional, Tuple

import jsonschema
from referencing import Registry
from referencing.jsonschema import DRAFT202012

from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.reasons import ReasonCode, ValidationError


class OpenApiSchemaAdapter:
    """Rewrites an OpenAPI document into something a JSON Schema validator accepts.

    OpenAPI 3.0 spells "this field may be null" as ``{"type": "number", "nullable": true}``, which
    JSON Schema does not know: a validator ignores the unknown keyword and then rejects ``null``
    against ``"type": "number"``. The rewrite turns every such object into
    ``{"type": ["number", "null"]}``, which means the same thing to a validator.

    Nothing else is rewritten. ``examples`` and the ``x-`` extension keys are unknown keywords that
    validators ignore harmlessly, and leaving them in keeps the adapted document readable next to
    the original.

    Example::

        adapted = OpenApiSchemaAdapter.adapt(ContractFiles.openapi())
        adapted["components"]["schemas"]["HomeInventoryInput"]  # ready to validate against
    """

    #: The OpenAPI 3.0 keyword this class translates away.
    NULLABLE_KEYWORD: ClassVar[str] = "nullable"

    #: The JSON Schema type name a nullable field gains.
    NULL_TYPE: ClassVar[str] = "null"

    @classmethod
    def adapt(cls, document: Mapping[str, Any]) -> Dict[str, Any]:
        """Return a deep copy of *document* with every ``nullable: true`` folded into its type.

        Args:
            document: The parsed OpenAPI document, or any part of one.

        Returns:
            A new dictionary; the input is not modified.
        """
        adapted: Dict[str, Any] = cls._adapt_node(copy.deepcopy(dict(document)))
        return adapted

    @classmethod
    def _adapt_node(cls, node: Any) -> Any:
        """Rewrite one node of the document in place and return it."""
        if isinstance(node, dict):
            for key, child in node.items():
                node[key] = cls._adapt_node(child)
            if node.pop(cls.NULLABLE_KEYWORD, False) is True and "type" in node:
                declared = node["type"]
                types = list(declared) if isinstance(declared, list) else [declared]
                if cls.NULL_TYPE not in types:
                    types.append(cls.NULL_TYPE)
                node["type"] = types
            return node
        if isinstance(node, list):
            return [cls._adapt_node(item) for item in node]
        return node


class PendingContractPaths:
    """Inventory paths this translation layer uses that the vendored contract does not define.

    Each entry names the decision that adds the field to ``HomeInventoryInput``. The list exists so
    that "the schema does not have this field" is a known gap rather than a silent one: check 4 of
    ``measures_v2_requirements.md`` §7.5 accepts a written path only when the schema has it or this
    list names it, and the contract PR of step 6 empties the list — after which a non-empty list
    against a contract that has the fields is itself a failure.
    """

    #: Path -> the decision id that adds it to the contract.
    BY_PATH: ClassVar[Dict[str, str]] = {
        "building_config.general.floor_construction": "Q3",
        "building_config.general.wall_construction": "Q3",
        "building_config.general.roof_form": "Q16",
        "building_config.general.roof_orientation_in_degree": "Q16",
        "energy_system_config.photovoltaics.share_of_maximum_pv_potential": "Q18",
        "energy_system_config.ventilation_system.system": "F6",
        "energy_system_config.heating_system.dhw_supply": "Q14",
    }

    @classmethod
    def contains(cls, path: str) -> bool:
        """Return whether *path* is a known gap in the contract."""
        return path in cls.BY_PATH

    @classmethod
    def paths(cls) -> Tuple[str, ...]:
        """Return every pending path, sorted."""
        return tuple(sorted(cls.BY_PATH))


class InventorySchema:
    """The contract's ``HomeInventoryInput`` schema, ready to validate against.

    Building it means adapting the whole OpenAPI document (so that ``$ref``s still resolve after
    the nullable rewrite) and registering it as the resolution base for the relative references
    the inventory schema uses.
    """

    #: Where the inventory schema sits inside the OpenAPI document.
    SCHEMA_NAME: ClassVar[str] = "HomeInventoryInput"
    COMPONENTS_KEY: ClassVar[str] = "components"
    SCHEMAS_KEY: ClassVar[str] = "schemas"

    #: The base URI the document is registered under; the schema's ``$ref``s are fragments of it.
    BASE_URI: ClassVar[str] = ""

    @classmethod
    def validator(cls) -> jsonschema.protocols.Validator:
        """Return a validator for ``HomeInventoryInput``.

        Returns:
            A draft 2020-12 validator whose registry holds the whole adapted OpenAPI document, so
            that ``#/components/schemas/GrantScheme`` resolves.

        Raises:
            KeyError: When the vendored document has no ``HomeInventoryInput`` schema.
        """
        document = OpenApiSchemaAdapter.adapt(ContractFiles.openapi())
        schema = document[cls.COMPONENTS_KEY][cls.SCHEMAS_KEY][cls.SCHEMA_NAME]
        registry = Registry().with_resource(
            uri=cls.BASE_URI, resource=DRAFT202012.create_resource(document)
        )
        return jsonschema.Draft202012Validator(schema, registry=registry)

    @classmethod
    def declares(cls, path: str) -> bool:
        """Return whether the schema defines the given dotted inventory path.

        Args:
            path: A dotted path such as ``building_config.envelope_details.roof_area_in_m2``.

        Returns:
            ``True`` when every segment of the path is a declared property, walking into nested
            objects. Array segments are not supported, because no measure writes into one.
        """
        document = ContractFiles.openapi()
        node = document[cls.COMPONENTS_KEY][cls.SCHEMAS_KEY][cls.SCHEMA_NAME]
        for segment in path.split("."):
            properties = node.get("properties") if isinstance(node, dict) else None
            if not isinstance(properties, dict) or segment not in properties:
                return False
            node = properties[segment]
        return True


class Inventory:
    """A ``HomeInventoryInput`` document with dotted-path access and schema validation.

    Example::

        inventory = Inventory.from_dict(data)
        inventory.has("energy_system_config.solar_thermal_system")     # False
        inventory.set("energy_system_config.heating_system.system", "HEAT_PUMP")

    The document is plain dictionaries and lists throughout, exactly as it arrived and exactly as
    it will be written back out: the translation layer does not model the inventory as classes,
    because the contract's field set is still moving and a class per block would have to move with
    it. Paths are the type discipline instead, checked against the schema.

    Args:
        data: The inventory document. :meth:`from_dict` deep-copies it; the constructor takes
            ownership of what it is given.
    """

    #: The separator between path segments.
    SEPARATOR: ClassVar[str] = "."

    def __init__(self, data: Dict[str, Any]) -> None:
        """Take ownership of *data* as the document this inventory addresses."""
        self._data = data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Inventory":
        """Return an inventory over a deep copy of *data*.

        Deep-copying is what makes requirement R5 hold: a package is applied to a copy, so the
        inventory a caller handed in is never changed under it and a second package starts from
        the same state.

        Args:
            data: The inventory document as parsed JSON.

        Returns:
            The new :class:`Inventory`.
        """
        return cls(copy.deepcopy(dict(data)))

    def copy(self) -> "Inventory":
        """Return a deep copy of this inventory."""
        return Inventory(copy.deepcopy(self._data))

    def get(self, path: str, default: Any = None) -> Any:
        """Return the value at *path*, or *default* when any segment is missing.

        Args:
            path: A dotted path, e.g. ``building_config.general.construction_year``.
            default: What to return when the path does not exist or holds ``None``.

        Returns:
            The value, or *default*. A path that exists but holds ``null`` returns *default* too,
            because a null field in this contract means "not stated".
        """
        node: Any = self._data
        for segment in path.split(self.SEPARATOR):
            if not isinstance(node, dict) or segment not in node:
                return default
            node = node[segment]
        return default if node is None else node

    def has(self, path: str) -> bool:
        """Return whether *path* exists and holds a value other than ``null``."""
        return self.get(path, None) is not None

    def set(self, path: str, value: Any) -> None:
        """Write *value* at *path*, creating the intermediate objects it needs.

        Args:
            path: A dotted path.
            value: The value to write; any JSON-compatible value.

        Raises:
            ValidationError: ``SCHEMA_VIOLATION`` when an intermediate segment exists and is not
                an object, so that writing would have to replace a value with a block.
        """
        segments = path.split(self.SEPARATOR)
        node: Dict[str, Any] = self._data
        for segment in segments[:-1]:
            child = node.get(segment)
            if child is None:
                child = {}
                node[segment] = child
            if not isinstance(child, dict):
                raise ValidationError(
                    ReasonCode.SCHEMA_VIOLATION,
                    path,
                    f"'{segment}' holds a {type(child).__name__}, so '{path}' cannot be written",
                )
            node = child
        node[segments[-1]] = value

    def to_dict(self) -> Dict[str, Any]:
        """Return the document as plain dictionaries and lists (the live object, not a copy)."""
        return self._data

    def leaf_paths(self) -> Tuple[str, ...]:
        """Return the dotted path of every leaf of the document, sorted."""
        return tuple(sorted(self._iter_leaf_paths(self._data)))

    def validate(self) -> None:
        """Check the document against the contract's ``HomeInventoryInput`` schema.

        Raises:
            ValidationError: ``SCHEMA_VIOLATION`` naming the path of the first offending value.
                The vendored schema declares no required fields, so this catches wrong types and
                values outside an enum, not absent blocks.
        """
        validator = InventorySchema.validator()
        errors = sorted(validator.iter_errors(self._data), key=lambda error: list(error.absolute_path))
        if not errors:
            return
        first = errors[0]
        path = self.SEPARATOR.join(str(segment) for segment in first.absolute_path) or "<root>"
        raise ValidationError(ReasonCode.SCHEMA_VIOLATION, path, first.message)

    @classmethod
    def _iter_leaf_paths(cls, value: Any, prefix: str = "") -> Iterator[str]:
        """Yield the dotted path of every leaf in a nested JSON-like structure."""
        if isinstance(value, dict):
            for key, child in value.items():
                child_prefix = f"{prefix}{cls.SEPARATOR}{key}" if prefix else str(key)
                yield from cls._iter_leaf_paths(child, child_prefix)
        elif isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value):
            for index, item in enumerate(value):
                yield from cls._iter_leaf_paths(item, f"{prefix}[{index}]")
        elif prefix:
            yield prefix


class InventoryPathCheck:
    """Checks that every inventory path the translation layer writes is one the contract knows.

    This is check 4 of ``measures_v2_requirements.md`` §7.5, as a class rather than a test helper
    so that the map generator of step 4 can report the same thing. A path passes when the schema
    declares it or :class:`PendingContractPaths` names it.
    """

    @classmethod
    def unknown_paths(cls, paths: Mapping[str, Any]) -> Tuple[str, ...]:
        """Return the paths that are neither in the schema nor pending.

        Args:
            paths: Any iterable of dotted paths; a mapping is accepted so the resolver's write
                table can be passed straight in.

        Returns:
            The offending paths, sorted.
        """
        return cls.unknown_in(tuple(paths))

    @classmethod
    def unknown_in(cls, paths: Tuple[str, ...]) -> Tuple[str, ...]:
        """Return the paths that are neither declared by the schema nor pending, sorted."""
        unknown: List[str] = [
            path for path in paths if not InventorySchema.declares(path) and not PendingContractPaths.contains(path)
        ]
        return tuple(sorted(set(unknown)))

    @classmethod
    def describe(cls, path: str) -> Optional[str]:
        """Return why a path is acceptable, or ``None`` when it is not.

        Args:
            path: A dotted inventory path.

        Returns:
            ``"declared by HomeInventoryInput"``, ``"pending, added by <decision>"``, or ``None``.
        """
        if InventorySchema.declares(path):
            return "declared by HomeInventoryInput"
        if PendingContractPaths.contains(path):
            return f"pending, added by {PendingContractPaths.BY_PATH[path]}"
        return None
