"""Classes to provide the structure for the KPI generation."""
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Mapping, Optional, Union, List, Tuple
from enum import Enum
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json, LetterCase, config as dataclasses_json_config
import pandas as pd
import numpy as np

if TYPE_CHECKING:
    from hisim.config import ComponentID, DisplayConfig


class KpiTagEnumClass(Enum):
    """Determine KPI tags as enums."""

    GENERAL = "General"
    COSTS = "Costs"
    EMISSIONS = "Emissions"
    BUILDING = "Building"
    AIR_CONDITIONER = "Air Conditioner"
    BATTERY = "Battery"
    CHP = "CHP"
    HEAT_DISTRIBUTION_SYSTEM = "Heat Distribution System"
    HEATPUMP_SPACE_HEATING = "Heat Pump For Space Heating"
    HEATPUMP_DOMESTIC_HOT_WATER = "Heat Pump For Domestic Hot Water"
    HEATPUMP_SPACE_HEATING_AND_DOMESTIC_HOT_WATER = "Heat Pump For SH and DHW"
    RESIDENTS = "Residents"
    GAS_BOILER = "Gas Boiler"
    OIL_BOILER = "Oil Boiler"
    PELLET_BOILER = "Pellet Boiler"
    WOOD_CHIP_BOILER = "Wood Chip Boiler"
    HYDROGEN_BOILER = "Hydrogen Boiler"
    DISTRICT_HEATING = "District Heating"
    GAS_METER = "Gas Meter"
    HEATING_METER = "Heating Meter"
    FUEL_METER = "Fuel Meter"
    ELECTRICITY_METER = "Electricity Meter"
    CAR = "Car"
    CAR_BATTERY = "Car Battery"
    ROOFTOP_PV = "Rooftop PV"
    SOLAR_THERMAL = "Solar Thermal"
    STORAGE_DOMESTIC_HOT_WATER = "Storage For Domestic Hot Water"
    STORAGE_HOT_WATER_SPACE_HEATING = "Storage For Space Heating Hot Water"
    WINDTURBINE = "Wind Turbine"
    SMART_DEVICE = "Smart Device"
    ELECTROLYZER = "Electrolyzer"
    TRANSFORMER = "Transformer"
    # EMS = "Energy Management System"
    ELECTRICITY_GRID = "Electricity Grid"
    THERMAL_GRID = "Thermal Grid"
    COSTS_DISTRICT_GRID = "Costs Of District Grid"
    EMISSIONS_DISTRICT_GRID = "Emissions Of District Grid"
    CONTRACTING = "Contracting"
    GENERIC_HEAT_SOURCE = "Generic Heat Source"  # used in simple_heat_source.py
    GROUND_PROBE = "Ground Probe"
    ELECTRIC_HEATING = "Electric Heating"
    ENERGY_MANAGEMENT_SYSTEM = "Energy Management System"
    DISTRICT_ENERGY_MANAGEMENT_SYSTEM = " District Energy Management System"


@dataclass_json
@dataclass(frozen=True, kw_only=True)
class KpiAddressStep:
    """One step of a component's address path: the import it came through and that import's instance.

    Serialized as ``{"import": <key>, "instance": <key or null>}``, the exact keys
    :meth:`from_json_object` accepts. Named apart from the ``AddressStep`` a
    :class:`~hisim.config.ComponentID` carries once assemblies exist: that one is the identity's
    step, this one its serialized copy in a KPI source, filled from it by
    :meth:`KpiSource.for_component`.

    Attributes:
        import_key: The key of the import (JSON ``import``, a Python keyword); never empty.
        instance: The instance key of that import, or ``None`` if the import has none.
    """

    import_key: str = field(metadata=dataclasses_json_config(field_name="import"))
    instance: Optional[str] = field(default=None, metadata=dataclasses_json_config(field_name="instance"))

    #: The JSON names of a step's fields: the only keys a step object has.
    JSON_NAMES: ClassVar[Tuple[str, ...]] = ("import", "instance")

    def __post_init__(self) -> None:
        """Refuse a step without an import key, or with an instance that is not a string."""
        if not isinstance(self.import_key, str) or not self.import_key:
            raise ValueError(f"An address step needs a non-empty import key, got {self.import_key!r}.")
        if self.instance is not None and not isinstance(self.instance, str):
            raise ValueError(f"An address step's instance is neither a string nor None: {self.instance!r}.")

    @classmethod
    def from_json_object(cls, raw: Any, where: str) -> "KpiAddressStep":
        """Decode one serialized step strictly: an object with exactly ``import`` and ``instance``.

        Args:
            raw: The parsed JSON object.
            where: What holds the step (a source and an index), named in every error.

        Returns:
            The step.

        Raises:
            ValueError: If ``raw`` is not an object with exactly the keys :attr:`JSON_NAMES`,
                ``import`` is not a non-empty string, or ``instance`` is neither a string nor null.
        """
        if not isinstance(raw, Mapping) or set(raw) != set(cls.JSON_NAMES):
            raise ValueError(
                f"{where}: an address step must be an object with exactly the keys "
                f"{', '.join(cls.JSON_NAMES)}, got {raw!r}."
            )
        if not isinstance(raw["import"], str) or not raw["import"]:
            raise ValueError(f"{where}: the address step's import is not a non-empty string: {raw['import']!r}.")
        if raw["instance"] is not None and not isinstance(raw["instance"], str):
            raise ValueError(f"{where}: the address step's instance is neither a string nor null: {raw['instance']!r}.")
        return cls(import_key=raw["import"], instance=raw["instance"])

    if TYPE_CHECKING:

        def to_dict(self) -> Dict[str, Any]:
            """Stub for the dict dump that @dataclass_json injects at runtime."""
            raise NotImplementedError


@dataclass_json
@dataclass(frozen=True, kw_only=True)
class KpiSource:
    """The structured address of the component a KPI entry is reported for.

    ``roadmap/kpi_address_spec.md`` ("The source of a component KPI"). A component KPI's address
    is its building, its tag, its name and this source; nothing about the other components of the
    building enters it, so the key ``"<name> (<source.name>)"`` (built by
    :attr:`~hisim.postprocessing.kpi_computation.kpi_address.KpiAddress.key`) never changes when a
    neighbour is added. Consumers filter on these fields
    (:class:`~hisim.postprocessing.kpi_computation.kpi_address.KpiFinder`) and never split a key.

    Serialized with the field names of the spec, in snake_case even inside the camelCase
    :class:`KpiEntry`, each one pinned below: ``import``, ``instance``, ``path``, ``member``,
    ``assembly``, ``name``, ``display_name``, ``label``. The identity fields -- what says which
    component a source is -- are :attr:`IDENTITY_FIELDS`; ``display_name`` and ``label`` are
    presentation.

    Attributes:
        import_key: The key of the import the component came from (JSON ``import``, a Python
            keyword); ``None`` for a component written directly into the energy system.
        instance: The instance key of that import (the request's system id); ``None`` if the
            import has none, and for a site component.
        path: Every address step from the energy system down to the component, outermost first
            (:class:`KpiAddressStep`, JSON ``[{"import": ..., "instance": ...}, ...]``); ``()``
            (JSON ``[]``) for a site component. ``import`` and ``instance`` are its first step:
            equal to ``path[0]`` when the path is non-empty, both ``None`` when it is empty.
        member: The component's name inside its assembly, or its plain component name
            (``ComponentID.name``, without building or unit). ``None`` only on a source read back
            from a JSON written before the source existed.
        assembly: Library path of the innermost owning assembly; one of :attr:`IDENTITY_FIELDS`.
        name: The runtime name, the serialized address (``Component.component_name``, which the
            ``Component`` constructor enforces to be ``ComponentID.key``): the string the KPI key
            is qualified with.
        display_name: The English default label: the component's ``DisplayConfig.pretty_name``,
            else its member name. Never an identifier.
        label: The request's own name for the system, passed through verbatim; ``None`` today.
    """

    import_key: Optional[str] = field(default=None, metadata=dataclasses_json_config(field_name="import"))
    instance: Optional[str] = field(default=None, metadata=dataclasses_json_config(field_name="instance"))
    path: Tuple[KpiAddressStep, ...] = field(default=(), metadata=dataclasses_json_config(field_name="path"))
    member: Optional[str] = field(default=None, metadata=dataclasses_json_config(field_name="member"))
    assembly: Optional[str] = field(default=None, metadata=dataclasses_json_config(field_name="assembly"))
    name: str = field(metadata=dataclasses_json_config(field_name="name"))
    display_name: Optional[str] = field(default=None, metadata=dataclasses_json_config(field_name="display_name"))
    label: Optional[str] = field(default=None, metadata=dataclasses_json_config(field_name="label"))

    #: The JSON names of the fields, in their serialized order: the only keys a source object has.
    JSON_NAMES: ClassVar[Tuple[str, ...]] = (
        "import",
        "instance",
        "path",
        "member",
        "assembly",
        "name",
        "display_name",
        "label",
    )

    #: The JSON names of the fields that say which component a source is: two sources naming one
    #: component agree on all of them, whatever their ``display_name`` and ``label``.
    IDENTITY_FIELDS: ClassVar[Tuple[str, ...]] = ("import", "instance", "path", "member", "assembly", "name")

    def __post_init__(self) -> None:
        """Refuse a source without a name, or whose ``import``/``instance`` is not its path's first step."""
        if not isinstance(self.name, str) or not self.name:
            raise ValueError(f"A KPI source needs a non-empty runtime name, got {self.name!r}.")
        # Typed Any: a caller (or dataclasses_json) may hand over what the annotation does not promise.
        path: Any = self.path
        if isinstance(path, list):
            # dataclasses_json's from_dict hands a JSON array over as a list; the field is a tuple.
            path = tuple(path)
            object.__setattr__(self, "path", path)
        if not isinstance(path, tuple) or not all(isinstance(step, KpiAddressStep) for step in path):
            raise ValueError(f"The KPI source '{self.name}': path is not a sequence of address steps: {path!r}.")
        outermost = (path[0].import_key, path[0].instance) if path else (None, None)
        if (self.import_key, self.instance) != outermost:
            raise ValueError(
                f"The KPI source '{self.name}': import {self.import_key!r} and instance {self.instance!r} "
                f"are not the outermost step of its path {[step.to_dict() for step in path]!r}; they must "
                "equal path[0], and both be null for a site component, whose path is empty."
            )

    def identity(self) -> Tuple[Any, ...]:
        """The values of :attr:`IDENTITY_FIELDS`, in that order: what two sources of one component share."""
        return (self.import_key, self.instance, self.path, self.member, self.assembly, self.name)

    @classmethod
    def for_component(cls, component_id: "ComponentID", display_config: "DisplayConfig") -> "KpiSource":
        """The source of a component's KPIs: the one place a source is built from a component.

        The path is the identity's own: a ``ComponentID`` that carries a ``path`` of address
        steps (each with an ``import_key`` and an ``instance``, outermost first) gives each step
        to the source, and ``import`` and ``instance`` are its first step; one without a path, as
        every ``ComponentID`` is until assemblies exist, is a site component with ``path`` ``()``.
        No assembly and no label yet; the member is the component's name and its runtime name is
        its key. The assemblies work (``assemblies_spec.md`` §2.4) extends this method and
        nothing else.

        Args:
            component_id: The component's structured identity.
            display_config: How the component is presented; its ``pretty_name`` becomes the
                ``display_name`` when set.

        Returns:
            The component's source.
        """
        pretty_name = display_config.pretty_name
        path = tuple(
            KpiAddressStep(import_key=step.import_key, instance=step.instance)
            for step in getattr(component_id, "path", ())
        )
        return cls(
            import_key=path[0].import_key if path else None,
            instance=path[0].instance if path else None,
            path=path,
            member=component_id.name,
            assembly=None,
            name=component_id.key,
            display_name=pretty_name if pretty_name else component_id.name,
            label=None,
        )

    @classmethod
    def from_entry_dict(cls, entry: Mapping[str, Any]) -> Optional["KpiSource"]:
        """Read the source of one serialized KPI entry, as ``all_kpis.json`` holds it.

        The entry's ``source`` object is read when the entry has the field. An entry written
        before the field existed has only ``nameOfSourceComponent``; its source is that name with
        every other field ``None`` (``kpi_address_spec.md``, "Finder").

        Args:
            entry: One entry dict (``KpiEntry.to_dict()`` or its JSON form).

        Returns:
            The source, or ``None`` for a derived KPI.

        Raises:
            ValueError: If ``source`` is neither ``None`` nor a source object
                (:meth:`from_json_object`), or if an entry carries both fields and they name
                different components.
        """
        if "source" not in entry:
            legacy_name = entry.get(KpiEntry.NAME_OF_SOURCE_COMPONENT_KEY)
            return None if legacy_name is None else cls(name=str(legacy_name))
        raw = entry["source"]
        if raw is None:
            return None
        where = f"the KPI entry '{entry.get('name')}'"
        source = cls.from_json_object(raw, where)
        KpiEntry.require_same_source_name(
            kpi_name=entry.get("name"),
            source_name=source.name,
            name_of_source_component=entry.get(KpiEntry.NAME_OF_SOURCE_COMPONENT_KEY),
            where=where,
        )
        return source

    @classmethod
    def from_json_object(cls, raw: Any, where: str) -> "KpiSource":
        """Decode one serialized source strictly: the only decoder for a source read from outside.

        Unlike the ``from_dict`` that ``@dataclass_json`` injects, which drops a key it does not
        know and fails with a bare ``KeyError`` on a missing one, this refuses every key that is
        not one of :attr:`JSON_NAMES` and requires ``name``; ``import``, ``instance``,
        ``member``, ``assembly``, ``display_name`` and ``label`` may be absent and then are
        ``None``, and every present one is a string or ``null``. ``path`` is a list of step
        objects (:meth:`KpiAddressStep.from_json_object`); an absent one is ``[]``, a site
        component's, which a source naming an import then contradicts.

        Args:
            raw: The parsed JSON object.
            where: What holds the object (a file and a key), named in every error.

        Returns:
            The source.

        Raises:
            ValueError: If ``raw`` is not an object, lacks ``name``, carries a key that is not
                one of :attr:`JSON_NAMES`, holds a value other than ``path`` that is neither a
                string nor ``null``, holds a ``path`` that is not a list of step objects, or names
                an ``import``/``instance`` that is not its path's first step.
        """
        if not isinstance(raw, Mapping):
            raise ValueError(f"{where}: a KPI source must be a JSON object, got {raw!r}.")
        unknown = [key for key in raw if key not in cls.JSON_NAMES]
        if unknown:
            raise ValueError(
                f"{where}: the KPI source carries the unknown key(s) {', '.join(repr(key) for key in unknown)}; "
                f"a source has exactly the keys {', '.join(cls.JSON_NAMES)}."
            )
        if "name" not in raw:
            raise ValueError(f"{where}: the KPI source has no 'name', the runtime name its KPI key is qualified with.")
        for key, value in raw.items():
            if key != "path" and value is not None and not isinstance(value, str):
                raise ValueError(f"{where}: source.{key} is neither a string nor null: {value!r}.")
        raw_path = raw.get("path", [])
        if not isinstance(raw_path, list):
            raise ValueError(f"{where}: source.path is not a list of address steps: {raw_path!r}.")
        path = tuple(
            KpiAddressStep.from_json_object(step, f"{where}: source.path[{index}]")
            for index, step in enumerate(raw_path)
        )
        try:
            return cls(
                import_key=raw.get("import"),
                instance=raw.get("instance"),
                path=path,
                member=raw.get("member"),
                assembly=raw.get("assembly"),
                name=raw["name"],
                display_name=raw.get("display_name"),
                label=raw.get("label"),
            )
        except ValueError as error:
            raise ValueError(f"{where}: {error}") from error

    if TYPE_CHECKING:

        def to_dict(self) -> Dict[str, Any]:
            """Stub for the dict dump that @dataclass_json injects at runtime."""
            raise NotImplementedError

        @classmethod
        def from_dict(cls, kvs: Any, *args: Any, **kwargs: Any) -> Any:  # pylint: disable=unused-argument
            """Stub for the dict decoder that @dataclass_json injects at runtime."""
            raise NotImplementedError


@dataclass_json(letter_case=LetterCase.CAMEL)
@dataclass
class KpiEntry:
    """Class for storing one KPI entry.

    Serialized with camelCase keys (``nameOfSourceComponent``): the dicts produced by
    ``to_dict`` are the wire format of the webtool KPI JSON, which predates the repo-wide
    dataclasses_json convention, so the historical spelling is pinned here explicitly.

    Webtool JSON contract (``all_kpis.json``, ``roadmap/kpi_address_spec.md``): the document is
    nested ``building -> tag -> key -> entry``. A **component KPI** is always keyed
    ``"<name> (<source.name>)"``, whether or not another component of the building reports the
    same name, and its entry carries ``source`` (:class:`KpiSource`, an object with the fields
    ``import``, ``instance``, ``path``, ``member``, ``assembly``, ``name``, ``display_name``,
    ``label``). A
    **derived KPI** (the General tag, the meter-derived cost totals, district totals) is keyed by
    its bare name and carries ``"source": null``. A key's shape is fixed by its producer, never by
    who else is present, and readers filter on ``source.*`` rather than splitting keys
    (:class:`~hisim.postprocessing.kpi_computation.kpi_address.KpiFinder`). Before and after, for
    the floor area the Building component reports::

        before: "Conditioned floor area": {"name": "Conditioned floor area", ...,
                    "nameOfSourceComponent": "Building"}
        after:  "Conditioned floor area (Building)": {"name": "Conditioned floor area", ...,
                    "nameOfSourceComponent": "Building",
                    "source": {"import": null, "instance": null, "path": [], "member": "Building",
                               "assembly": null, "name": "Building", "display_name": "Building",
                               "label": null}}

    ``nameOfSourceComponent`` is **deprecated**: it is kept for one release as the same string as
    ``source.name``, so its readers get one release offering both, and is removed in the next.

    Attributes:
        name: Human-readable name of the KPI.
        unit: Unit of the KPI value (e.g. "kWh", "EUR", "kg CO2eq").
        value: Numeric KPI value as a float, or a string for descriptive
            or non-numeric KPIs. May be ``None`` if not yet computed.
        description: Optional human-readable description of the KPI.
        tag: Optional category tag from :class:`KpiTagEnumClass` used to
            group KPIs by component or domain.
        name_of_source_component: Deprecated, kept for one release: the runtime name of the
            component the KPI is reported for, always equal to ``source.name``.
        source: The structured address of that component (:class:`KpiSource`); ``None`` for a
            derived KPI. Filled in by ``Component.component_kpi_entries`` for an entry that has
            none.
    """

    #: The JSON name of :attr:`name_of_source_component`, the deprecated field.
    NAME_OF_SOURCE_COMPONENT_KEY: ClassVar[str] = "nameOfSourceComponent"

    name: str
    unit: str
    value: Optional[Union[float, str]]
    description: Optional[str] = None
    # The tag is written as its enum *value* ("Battery", "General", ...) directly in
    # to_dict(), because the resulting dicts are json.dump'ed as-is by the webtool export.
    tag: Optional[KpiTagEnumClass] = field(
        default=None,
        metadata=dataclasses_json_config(
            encoder=lambda tag: tag.value if tag is not None else None,
            decoder=lambda raw: KpiTagEnumClass(raw) if raw is not None else None,
        ),
    )
    name_of_source_component: Optional[str] = None
    # Snake_case field names inside the camelCase entry: KpiSource pins its own spelling.
    source: Optional[KpiSource] = None
    # Optional uncertainty band (cost_spec.md §7.3): `value` is the AVERAGE slot. Additive —
    # legacy KPIs leave these unset during the parallel phase of the lifecycle cost engine.
    value_min: Optional[float] = None
    value_max: Optional[float] = None

    @staticmethod
    def require_same_source_name(
        kpi_name: Any, source_name: str, name_of_source_component: Optional[str], where: str
    ) -> None:
        """Refuse an entry whose deprecated ``nameOfSourceComponent`` names another component than its source.

        The one statement of the invariant ``name_of_source_component == source.name``, for an
        entry object (:meth:`require_consistent_source`) and a serialized one
        (:meth:`KpiSource.from_entry_dict`) alike. An absent deprecated field is consistent.

        Args:
            kpi_name: The entry's name, for the message.
            source_name: ``source.name``.
            name_of_source_component: The deprecated field, or ``None``.
            where: Who checks, named in the message.

        Raises:
            ValueError: If the deprecated field is set and differs from ``source.name``.
        """
        if name_of_source_component is not None and name_of_source_component != source_name:
            raise ValueError(
                f"{where}: the KPI entry '{kpi_name}' names two different sources: source.name "
                f"'{source_name}' and {KpiEntry.NAME_OF_SOURCE_COMPONENT_KEY} '{name_of_source_component}'. "
                "The deprecated field must equal source.name."
            )

    def require_consistent_source(self, where: str) -> None:
        """Fill the deprecated ``name_of_source_component`` from ``source``, refusing a different one.

        Does nothing for an entry without a source (a derived KPI, or one whose source the caller
        still has to stamp).

        Args:
            where: Who checks, named in the message.

        Raises:
            ValueError: If ``name_of_source_component`` is set and differs from ``source.name``.
        """
        if self.source is None:
            return
        self.require_same_source_name(self.name, self.source.name, self.name_of_source_component, where)
        if self.name_of_source_component is None:
            self.name_of_source_component = self.source.name

    if TYPE_CHECKING:
        # The serialization API is injected at runtime by the @dataclass_json decorator,
        # which mypy deliberately does not resolve (see the mypy.ini note on
        # dataclasses_json); these stubs mirror it for the type checker.
        def to_dict(self) -> Dict[str, Any]:
            """Stub for the dict dump that @dataclass_json injects at runtime."""
            raise NotImplementedError

        @classmethod
        def from_dict(cls, kvs: Any, *args: Any, **kwargs: Any) -> Any:  # pylint: disable=unused-argument
            """Stub for the dict decoder that @dataclass_json injects at runtime."""
            raise NotImplementedError


class KpiHelperClass:
    """Class for providing some helper functions for calculating KPIs."""

    @staticmethod
    def compute_total_energy_from_power_timeseries(power_timeseries_in_watt: pd.Series, time_resolution_in_seconds: float) -> float:
        """Compute total energy in kWh from a power time series in watts.

        Args:
            power_timeseries_in_watt: Power values in watts sampled at a fixed
                time resolution. An empty series yields 0.0.
            time_resolution_in_seconds: Constant time step between samples, in
                seconds.

        Returns:
            The total energy in kilowatt-hours as a float.
        """
        if power_timeseries_in_watt.empty:
            return 0.0

        energy_in_kilowatt_hour = float(power_timeseries_in_watt.sum() * time_resolution_in_seconds / 3.6e6)
        return energy_in_kilowatt_hour

    @staticmethod
    def compute_mean_max_min_values(list_or_pandas_series: Union[List[float], pd.Series]) -> Tuple[float, float, float]:
        """Calculate mean, maximum, and minimum values of a numeric sequence.

        Args:
            list_or_pandas_series: A list or pandas Series of numeric values.

        Returns:
            A tuple ``(mean_value, max_value, min_value)`` of floats.
        """

        # Convert the input to an ndarray once and reuse it. Passing a plain
        # ``list`` to ``np.mean``/``np.max``/``np.min`` would internally call
        # ``np.asarray`` three times, building the same array each time.
        arr = np.asarray(list_or_pandas_series)
        mean_value = float(arr.mean())
        max_value = float(arr.max())
        min_value = float(arr.min())

        return mean_value, max_value, min_value
