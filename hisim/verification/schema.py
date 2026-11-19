"""The description of one test case, as the case files state it.

A case is a single zone: its geometry, the opaque surfaces with their constructions (layers and the
printed air-to-air U-value), the windows with their glazing (U-value and the solar heat gain
coefficient by incidence angle), the infiltration, the internal gains, the thermostat and the site.
Every number cites the clause or table of the standard it comes from; the classes here only hold
and check them. A derived case file names its parent and restates what differs (``case_loader``).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple


class CaseFileError(ValueError):
    """A case file states something the schema cannot take; the message names the case and the field."""


def _number(mapping: Mapping[str, Any], key: str, where: str) -> float:
    """Return a numeric field as a float, naming the field when it is missing or not a number."""
    if key not in mapping:
        raise CaseFileError(f"{where}: the field '{key}' is missing")
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaseFileError(f"{where}: the field '{key}' must be a number, not {value!r}")
    return float(value)


def _text(mapping: Mapping[str, Any], key: str, where: str) -> str:
    """Return a text field, naming the field when it is missing or not text."""
    if key not in mapping or not isinstance(mapping[key], str):
        raise CaseFileError(f"{where}: the field '{key}' is missing or not text")
    return str(mapping[key])


@dataclass(frozen=True)
class Layer:
    """One layer of a construction, inside to outside: thickness, conductivity, density, specific heat.

    ``insulation`` marks the layer the effective-thickness rule of spec §3.2.3 stops at (condition
    b); it is stated in the file, not guessed from the conductivity.
    """

    name: str
    thickness_in_m: float
    conductivity_in_watt_per_m_per_kelvin: float
    density_in_kg_per_m3: float
    specific_heat_in_joule_per_kg_per_kelvin: float
    insulation: bool

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], where: str) -> "Layer":
        """Build a layer from its file entry."""
        name = _text(mapping, "name", where)
        where = f"{where}, layer '{name}'"
        layer = cls(
            name=name,
            thickness_in_m=_number(mapping, "thickness_in_m", where),
            conductivity_in_watt_per_m_per_kelvin=_number(mapping, "conductivity_in_watt_per_m_per_kelvin", where),
            density_in_kg_per_m3=_number(mapping, "density_in_kg_per_m3", where),
            specific_heat_in_joule_per_kg_per_kelvin=_number(mapping, "specific_heat_in_joule_per_kg_per_kelvin", where),
            insulation=bool(mapping.get("insulation", False)),
        )
        if layer.thickness_in_m <= 0:
            raise CaseFileError(f"{where}: the thickness must be positive")
        if layer.density_in_kg_per_m3 < 0 or layer.specific_heat_in_joule_per_kg_per_kelvin < 0:
            raise CaseFileError(f"{where}: density and specific heat cannot be negative")
        return layer


@dataclass(frozen=True)
class Construction:
    """A construction: its layers inside to outside and the printed air-to-air U-value (spec §3.2.1)."""

    name: str
    u_value_air_to_air_in_watt_per_m2_per_kelvin: float
    layers: Tuple[Layer, ...]
    source: str

    @classmethod
    def from_mapping(cls, name: str, mapping: Mapping[str, Any], where: str) -> "Construction":
        """Build a construction from its file entry."""
        where = f"{where}, construction '{name}'"
        layers = mapping.get("layers")
        if not isinstance(layers, list) or not layers:
            raise CaseFileError(f"{where}: 'layers' must be a non-empty list")
        return cls(
            name=name,
            u_value_air_to_air_in_watt_per_m2_per_kelvin=_number(mapping, "u_value_air_to_air_in_watt_per_m2_per_kelvin", where),
            layers=tuple(Layer.from_mapping(layer, where) for layer in layers),
            source=_text(mapping, "source", where),
        )

    @property
    def total_thickness_in_m(self) -> float:
        """The sum of the layer thicknesses."""
        return sum(layer.thickness_in_m for layer in self.layers)


@dataclass(frozen=True)
class Surface:
    """One opaque surface facing the zone: its kind, area and construction."""

    KINDS = ("wall", "roof", "floor")

    name: str
    kind: str
    area_in_m2: float
    construction: str

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], where: str) -> "Surface":
        """Build a surface from its file entry."""
        name = _text(mapping, "name", where)
        where = f"{where}, surface '{name}'"
        surface = cls(
            name=name,
            kind=_text(mapping, "kind", where),
            area_in_m2=_number(mapping, "area_in_m2", where),
            construction=_text(mapping, "construction", where),
        )
        if surface.kind not in cls.KINDS:
            raise CaseFileError(f"{where}: the kind must be one of {cls.KINDS}, not '{surface.kind}'")
        if surface.area_in_m2 <= 0:
            raise CaseFileError(f"{where}: the area must be positive")
        return surface


@dataclass(frozen=True)
class Glazing:
    """A glazing system: its printed air-to-air U-value and the solar heat gain coefficient by angle.

    ``shgc_by_incidence_angle`` maps the angle in degrees (0 to 90) to the coefficient;
    ``shgc_hemispherical`` is the value for diffuse radiation. Spec §3.2.8 turns them into one
    F_w per window.
    """

    name: str
    u_value_air_to_air_in_watt_per_m2_per_kelvin: float
    shgc_normal: float
    shgc_hemispherical: float
    shgc_by_incidence_angle: Dict[int, float]
    source: str

    @classmethod
    def from_mapping(cls, name: str, mapping: Mapping[str, Any], where: str) -> "Glazing":
        """Build a glazing from its file entry."""
        where = f"{where}, glazing '{name}'"
        table = mapping.get("shgc_by_incidence_angle")
        if not isinstance(table, dict) or not table:
            raise CaseFileError(f"{where}: 'shgc_by_incidence_angle' must map angles to coefficients")
        by_angle: Dict[int, float] = {}
        for angle, coefficient in table.items():
            if isinstance(angle, bool) or not isinstance(angle, int) or not 0 <= angle <= 90:
                raise CaseFileError(f"{where}: the incidence angle {angle!r} is not an integer of 0 to 90")
            if isinstance(coefficient, bool) or not isinstance(coefficient, (int, float)) or not 0 <= coefficient <= 1:
                raise CaseFileError(f"{where}: the coefficient at {angle} degrees must lie in 0 to 1")
            by_angle[angle] = float(coefficient)
        if 0 not in by_angle or 90 not in by_angle:
            raise CaseFileError(f"{where}: the table must state the coefficients at 0 and at 90 degrees")
        glazing = cls(
            name=name,
            u_value_air_to_air_in_watt_per_m2_per_kelvin=_number(mapping, "u_value_air_to_air_in_watt_per_m2_per_kelvin", where),
            shgc_normal=_number(mapping, "shgc_normal", where),
            shgc_hemispherical=_number(mapping, "shgc_hemispherical", where),
            shgc_by_incidence_angle=dict(sorted(by_angle.items())),
            source=_text(mapping, "source", where),
        )
        if glazing.shgc_by_incidence_angle[0] != glazing.shgc_normal:
            raise CaseFileError(f"{where}: the coefficient at 0 degrees must equal shgc_normal")
        return glazing


@dataclass(frozen=True)
class Window:
    """One window: area, azimuth (180 is south; ``None`` for a horizontal one), tilt and glazing."""

    name: str
    area_in_m2: float
    azimuth_in_degrees: Optional[float]
    tilt_in_degrees: float
    glazing: str

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], where: str) -> "Window":
        """Build a window from its file entry."""
        name = _text(mapping, "name", where)
        where = f"{where}, window '{name}'"
        azimuth = mapping.get("azimuth_in_degrees")
        window = cls(
            name=name,
            area_in_m2=_number(mapping, "area_in_m2", where),
            azimuth_in_degrees=None if azimuth is None else _number(mapping, "azimuth_in_degrees", where),
            tilt_in_degrees=_number(mapping, "tilt_in_degrees", where),
            glazing=_text(mapping, "glazing", where),
        )
        if window.area_in_m2 <= 0:
            raise CaseFileError(f"{where}: the area must be positive")
        if window.azimuth_in_degrees is None and window.tilt_in_degrees != 0:
            raise CaseFileError(f"{where}: a window without an azimuth must be horizontal")
        return window


@dataclass(frozen=True)
class Setback:
    """A night setback of the heating set point: the lower set point and its hours.

    The set point drops to ``setpoint_in_celsius`` at ``from_hour`` and returns at ``to_hour``; the
    standard ramps it back linearly until ``ramp_to_hour`` (7.2.2.1.4 c), which the suite records
    and does not model (decision D18: the step at ``to_hour``).
    """

    setpoint_in_celsius: float
    from_hour: int
    to_hour: int
    ramp_to_hour: int
    clause: str

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], where: str) -> "Setback":
        """Build a setback from its file entry."""
        where = f"{where}, setback"
        setback = cls(
            setpoint_in_celsius=_number(mapping, "setpoint_in_celsius", where),
            from_hour=int(_number(mapping, "from_hour", where)),
            to_hour=int(_number(mapping, "to_hour", where)),
            ramp_to_hour=int(_number(mapping, "ramp_to_hour", where)),
            clause=_text(mapping, "clause", where),
        )
        for hour in (setback.from_hour, setback.to_hour, setback.ramp_to_hour):
            if not 0 <= hour <= 24:
                raise CaseFileError(f"{where}: the hour {hour} is not within 0 to 24")
        return setback


@dataclass(frozen=True)
class Thermostat:
    """The heating and cooling set points of the zone air, with an optional night setback."""

    heating_setpoint_in_celsius: float
    cooling_setpoint_in_celsius: float
    setback: Optional[Setback]
    clause: str

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], where: str) -> "Thermostat":
        """Build a thermostat from its file entry."""
        where = f"{where}, thermostat"
        setback = mapping.get("setback")
        return cls(
            heating_setpoint_in_celsius=_number(mapping, "heating_setpoint_in_celsius", where),
            cooling_setpoint_in_celsius=_number(mapping, "cooling_setpoint_in_celsius", where),
            setback=None if setback is None else Setback.from_mapping(setback, where),
            clause=_text(mapping, "clause", where),
        )


@dataclass(frozen=True)
class Site:
    """The site: the catalogue weather station, its coordinates, altitude and standard time."""

    weather_location: str
    latitude_in_degrees: float
    longitude_in_degrees: float
    altitude_in_m: float
    utc_offset_in_hours: float
    ground_reflectance: float
    source: str

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], where: str) -> "Site":
        """Build the site from its file entry."""
        where = f"{where}, site"
        return cls(
            weather_location=_text(mapping, "weather_location", where),
            latitude_in_degrees=_number(mapping, "latitude_in_degrees", where),
            longitude_in_degrees=_number(mapping, "longitude_in_degrees", where),
            altitude_in_m=_number(mapping, "altitude_in_m", where),
            utc_offset_in_hours=_number(mapping, "utc_offset_in_hours", where),
            ground_reflectance=_number(mapping, "ground_reflectance", where),
            source=_text(mapping, "source", where),
        )


@dataclass(frozen=True)
class Case:
    """One resolved test case: everything the system builder needs, with the standard's references."""

    case_id: str
    title: str
    standard: str
    clause: str
    floor_area_in_m2: float
    volume_in_m3: float
    constructions: Dict[str, Construction]
    surfaces: Tuple[Surface, ...]
    glazings: Dict[str, Glazing]
    windows: Tuple[Window, ...]
    infiltration_air_changes_per_hour: float
    infiltration_clause: str
    internal_gains_in_watt: float
    internal_gains_radiative_fraction: float
    internal_gains_clause: str
    thermostat: Optional[Thermostat]
    site: Site
    notes: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "Case":
        """Build a case from its resolved file content, checking every field by name.

        Raises:
            CaseFileError: for a missing or ill-typed field, a surface or window naming an unknown
                construction or glazing, or a free-float case that still has a thermostat.
        """
        case_id = str(mapping.get("case", ""))
        if not case_id:
            raise CaseFileError("a case file names no 'case'")
        where = f"case {case_id}"
        geometry = mapping.get("geometry")
        if not isinstance(geometry, dict):
            raise CaseFileError(f"{where}: 'geometry' is missing")
        constructions_entry = mapping.get("constructions")
        glazings_entry = mapping.get("glazings")
        if not isinstance(constructions_entry, dict) or not isinstance(glazings_entry, dict):
            raise CaseFileError(f"{where}: 'constructions' and 'glazings' must be mappings by name")
        constructions = {name: Construction.from_mapping(name, entry, where) for name, entry in constructions_entry.items()}
        glazings = {name: Glazing.from_mapping(name, entry, where) for name, entry in glazings_entry.items()}
        surfaces = tuple(Surface.from_mapping(entry, where) for entry in mapping.get("surfaces", []))
        windows = tuple(Window.from_mapping(entry, where) for entry in mapping.get("windows", []))
        for surface in surfaces:
            if surface.construction not in constructions:
                raise CaseFileError(f"{where}: surface '{surface.name}' names the unknown construction '{surface.construction}'")
        for window in windows:
            if window.glazing not in glazings:
                raise CaseFileError(f"{where}: window '{window.name}' names the unknown glazing '{window.glazing}'")
        if not surfaces:
            raise CaseFileError(f"{where}: no surfaces")
        infiltration = mapping.get("infiltration")
        gains = mapping.get("internal_gains")
        if not isinstance(infiltration, dict) or not isinstance(gains, dict):
            raise CaseFileError(f"{where}: 'infiltration' and 'internal_gains' are missing")
        thermostat_entry = mapping.get("thermostat")
        free_float = bool(mapping.get("free_float", False))
        if free_float and thermostat_entry is not None:
            raise CaseFileError(f"{where}: a free-float case has no thermostat")
        if not free_float and thermostat_entry is None:
            raise CaseFileError(f"{where}: a case that is not free float needs a thermostat")
        site = mapping.get("site")
        if not isinstance(site, dict):
            raise CaseFileError(f"{where}: 'site' is missing")
        notes = mapping.get("notes", [])
        return cls(
            case_id=case_id,
            title=_text(mapping, "title", where),
            standard=_text(mapping, "standard", where),
            clause=_text(mapping, "clause", where),
            floor_area_in_m2=_number(geometry, "floor_area_in_m2", f"{where}, geometry"),
            volume_in_m3=_number(geometry, "volume_in_m3", f"{where}, geometry"),
            constructions=constructions,
            surfaces=surfaces,
            glazings=glazings,
            windows=windows,
            infiltration_air_changes_per_hour=_number(infiltration, "air_changes_per_hour", f"{where}, infiltration"),
            infiltration_clause=_text(infiltration, "clause", f"{where}, infiltration"),
            internal_gains_in_watt=_number(gains, "sensible_in_watt", f"{where}, internal_gains"),
            internal_gains_radiative_fraction=_number(gains, "radiative_fraction", f"{where}, internal_gains"),
            internal_gains_clause=_text(gains, "clause", f"{where}, internal_gains"),
            thermostat=None if thermostat_entry is None else Thermostat.from_mapping(thermostat_entry, where),
            site=Site.from_mapping(site, where),
            notes=tuple(str(note) for note in notes),
        )

    @property
    def free_float(self) -> bool:
        """Whether the case runs without heating and cooling."""
        return self.thermostat is None

    def construction_of(self, surface: Surface) -> Construction:
        """The construction a surface is built of."""
        return self.constructions[surface.construction]

    def glazing_of(self, window: Window) -> Glazing:
        """The glazing a window is made of."""
        return self.glazings[window.glazing]
