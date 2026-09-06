"""Configuration of a building *archetype* for the modular household model.

An *archetype* is a standardized building type with predefined thermal and
electrical characteristics.  An :class:`ArcheTypeConfig` does **not** store the
underlying physics quantities itself; instead it identifies the building and
the downstream :class:`~hisim.components.building.Building` component resolves
the predefined characteristics from the EPISCOPE/TABULA building-typology
database.  Concretely, the envelope U-values (W/m\u00b2K), the surface areas
(m\u00b2), the heat-capacity class, and hence the normative heating load are
looked up from the TABULA table via ``building_code``; the occupancy schedules
and internal electrical loads are likewise not modelled here but supplied by
the load-profile generator (LPG) household identifiers in ``lpg_households``.

Supported archetypes
--------------------

``building_code`` follows the TABULA generic-example scheme
``<CC>.N.<TYPE>.<band>.Gen.ReEx.001.<variant>``:

- ``<CC>`` -- ISO country code of the typology dataset (e.g. ``DE``, ``IE``,
  ``AT``); the processed table ships with several European countries.
- ``<TYPE>`` -- building typology: ``SFH`` (single-family house), ``TH``
  (terraced house), ``MFH`` (multi-family house) or ``AB`` (apartment block).
- ``<band>`` -- two-digit TABULA construction-year age band.
- ``<variant>`` -- refurbishment level: ``.001`` existing / unrenovated,
  ``.002`` usual refurbishment, ``.003`` advanced refurbishment.

The dataclass default ``DE.N.SFH.05.Gen.ReEx.001.002`` is a German
single-family house, age band ``05`` (construction years 1958-1968), in the
usual-refurbishment variant.

Parameter units
---------------

- ``pv_azimuth``, ``pv_tilt`` -- degrees (azimuth clockwise from north,
  valid range 0°-360°; tilt from the horizontal plane, valid range
  0°-90°); see the class docstring.
- ``pv_rooftop_capacity_in_kilowatt`` -- installed PV peak power in kW.
- ``pv_rooftop_generation_in_kilowatthour`` -- PV energy yield in kWh.
- ``conditioned_floor_area_in_m2`` -- conditioned (heated) floor area in m\u00b2.
- ``number_of_dwellings_per_building`` -- dwelling count (dimensionless).
- ``norm_heating_load_in_kilowatt`` -- normative heating load in kW.
- ``building_density_within_buffer_area_of_100m_radius`` -- built-up fraction
  within a 100 m buffer (dimensionless, 0-1).
- ``nearest_neighbor_distance_m`` -- distance to the nearest neighbouring
  building in metres.
- ``construction_year`` -- calendar year of construction (CE).
- ``coordinates_latitude``, ``coordinates_longitude`` -- geographic
  coordinates in decimal degrees.
- ``weather_location`` / ``weather_try_region`` -- identifiers of the
  test-reference-year (TRY) weather dataset (dimensionless region index).

Physical assumptions
--------------------

The thermal behaviour is computed by a dynamic single-node RC model following
EN ISO 13790 (implemented in :class:`~hisim.components.building.Building`),
not by a steady-state energy balance: the building's thermal mass is
integrated over each simulation time step.  Results are therefore dependent
on the selected climate zone (``weather_location`` / ``weather_try_region``)
and on the simulation time resolution.  Envelope and thermal-mass parameters
are those of the TABULA archetype selected by ``building_code``; this config
holds no per-element U-value overrides.

References
----------
The envelope U-values, surface areas and heat-capacity data resolved from
``building_code`` originate from the EPISCOPE/TABULA building-typology
project; the underlying datasets and web tool are available at
https://episcope.eu/building-typology/webtool/. The dynamic single-node RC
thermal model follows EN ISO 13790 (implemented in
:class:`~hisim.components.building.Building`).
"""

import difflib
from dataclasses import dataclass, field
from typing import ClassVar, List, Optional, Union

from dataclasses_json import dataclass_json
from utspclient.helpers.lpgdata import Households
from utspclient.helpers.lpgpythonbindings import JsonReference


@dataclass_json
@dataclass
class ArcheTypeConfig:
    """Configuration of a single building *archetype* for the modular household.

    An *archetype* is a standardized building type. This dataclass does **not**
    store the underlying physics quantities itself; it identifies the building
    and the downstream :class:`~hisim.components.building.Building` component
    resolves the predefined envelope characteristics (U-values in W/m²K, surface
    areas in m², heat-capacity class and normative heating load) from the
    EPISCOPE/TABULA building-typology table via ``building_code``. The occupancy
    schedules and internal electrical loads are likewise supplied by the Load
    Profile Generator (LPG) household profile names in ``lpg_households``, not
    modelled here. See the module docstring for the full physical-assumptions
    and parameter-units reference.

    Instead this config holds the identifying and geometric quantities the
    downstream resolution needs, with their defaults:

    - ``building_code`` (default ``"DE.N.SFH.05.Gen.ReEx.001.002"``): TABULA
      archetype code selecting country, building type, age band and
      refurbishment level -- the source of the envelope U-values.
    - ``conditioned_floor_area_in_m2`` (default ``121.2``): conditioned (heated)
      floor area in m².
    - ``number_of_dwellings_per_building`` (default ``1``): dwelling count.
    - ``norm_heating_load_in_kilowatt`` (default ``None``): optional normative
      heating load in kW; when ``None`` it is derived from the TABULA archetype.
    - ``construction_year`` (default ``1964``): calendar year of construction.
    - ``weather_location`` / ``weather_try_region`` (default ``"AACHEN"`` /
      ``6``): test-reference-year (TRY) weather dataset identifiers.
    - ``coordinates_latitude`` / ``coordinates_longitude`` (default ``50.77664``
      / ``6.0834``): site location in decimal degrees.
    - ``lpg_households`` (default ``["CHR01_Couple_both_at_Work"]``): LPG
      household profile names; listing several stacks distinct households in one
      building for multi-family modelling.

    Photovoltaic orientation is given by two angle fields whose values are
    always expressed in **degrees**, never radians. This matches the convention
    used by the downstream PV component
    (:class:`~hisim.components.generic_pv_system.PVSystemConfig`), which
    interprets azimuth "from north in °" and tilt "from horizontal":

    - ``pv_azimuth``: panel azimuth angle in degrees, measured clockwise from
      north. The default ``180`` corresponds to a south-facing panel.
    - ``pv_tilt``: panel tilt angle in degrees from the horizontal plane. The
      default ``30`` corresponds to a 30° tilt.
    - ``pv_rooftop_capacity_in_kilowatt`` / ``pv_rooftop_generation_in_kilowatthour``
      (default ``None`` / ``None``): optional PV peak power in kW and annual
      energy yield in kWh.

    Callers must pass these values in degrees; do not mix degrees and radians.

    A minimal usage example::

        >>> from hisim.building_sizer_utils.interface_configs.archetype_config import (
        ...     ArcheTypeConfig,
        ... )
        >>> archetype = ArcheTypeConfig()  # default German SFH in Aachen
        >>> archetype = ArcheTypeConfig(  # multi-family with two households
        ...     building_code="DE.N.MFH.05.Gen.ReEx.001.002",
        ...     conditioned_floor_area_in_m2=300.0,
        ...     number_of_dwellings_per_building=2,
        ...     lpg_households=[
        ...         "CHR01_Couple_both_at_Work",
        ...         "CHR03_Family_1_child_both_at_work",
        ...     ],
        ... )

    """

    building_name: str = "BUI1"
    building_id: str = "default_building"
    #: PV panel azimuth in degrees, measured clockwise from north (180° = south).
    pv_azimuth: float = 180
    #: PV panel tilt in degrees from the horizontal plane.
    pv_tilt: float = 30
    pv_rooftop_capacity_in_kilowatt: Optional[float] = None
    pv_rooftop_generation_in_kilowatthour: Optional[float] = None
    building_code: str = "DE.N.SFH.05.Gen.ReEx.001.002"
    conditioned_floor_area_in_m2: float = 121.2
    number_of_dwellings_per_building: int = 1
    norm_heating_load_in_kilowatt: Optional[float] = None
    weather_location: str = "AACHEN"
    weather_try_region: int = 6

    weather_filepath: Optional[str] = None
    weather_datasource: Optional[str] = None

    building_postal_code: str = "52062"
    building_location: str = "Aachen"
    lpg_households: list[str] = field(default_factory=lambda: ["CHR01_Couple_both_at_Work"])
    commodity: str = "electric"
    supply_level: str = "central_heating"
    building_density_within_buffer_area_of_100m_radius: float = 0.09
    nearest_neighbor_distance_m: float = 20.0
    construction_year: int = 1964
    coordinates_latitude: float = 50.77664
    coordinates_longitude: float = 6.0834

    # Optional building-envelope override. If any of these are set, the value is passed through to
    # the Building component and used instead of the TABULA archetype default. If left None (default),
    # the envelope is derived from the TABULA building_code exactly as before (opt-in, backward compatible).
    building_heat_capacity_class: Optional[str] = None
    floor_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    floor_area_in_m2: Optional[float] = None
    facade_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    facade_area_in_m2: Optional[float] = None
    roof_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    roof_area_in_m2: Optional[float] = None
    window_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    window_area_in_m2: Optional[float] = None
    door_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    door_area_in_m2: Optional[float] = None

    #: Module path of the registry that defines every legal ``lpg_households`` entry, quoted in the
    #: refusal below so a caller who mistyped a name is told where the valid ones live.
    LPG_HOUSEHOLD_REGISTRY: ClassVar[str] = "utspclient.helpers.lpgdata.Households"

    def resolve_lpg_households(self) -> Union[JsonReference, List[JsonReference]]:
        """Turn the configured ``lpg_households`` profile names into LPG household references.

        The names in ``lpg_households`` are attribute names of the Load Profile Generator's
        ``Households`` registry; the occupancy component needs the ``JsonReference`` objects behind
        them. A single configured name resolves to one reference, several names resolve to a list of
        references that the occupancy component stacks into one building. The eleven building-sizer
        setups call this instead of resolving the names themselves, so an unknown name is refused
        the same way everywhere: the loop in those setups used to skip a name the registry did not
        know without any diagnostic, which quietly built a household with fewer occupants than the
        configuration asked for.

        A minimal usage example::

            >>> from hisim.building_sizer_utils.interface_configs.archetype_config import (
            ...     ArcheTypeConfig,
            ... )
            >>> ArcheTypeConfig().resolve_lpg_households()  # doctest: +ELLIPSIS
            JsonReference(...)

        Returns:
            Union[JsonReference, List[JsonReference]]: The single reference for a one-name
            configuration, or the list of references, in configured order, for several names.

        Raises:
            ValueError: If ``lpg_households`` is empty, or if it names a household the registry does
                not define. The refusal quotes the unknown name, names the registry that holds the
                legal ones, and lists the closest known names when there are any.
            TypeError: If ``lpg_households`` is not a list, or if any of its entries is not a
                string. The refusal names the offending index and value.
        """
        if not isinstance(self.lpg_households, list):
            raise TypeError(
                f"Type {type(self.lpg_households)} is incompatible. Should be List[str]."
            )
        if not self.lpg_households:
            raise ValueError(
                "Config list with lpg household is empty. Name at least one household from "
                f"{self.LPG_HOUSEHOLD_REGISTRY} in 'lpg_households'."
            )
        for index, entry in enumerate(self.lpg_households):
            if not isinstance(entry, str):
                raise TypeError(
                    f"lpg_households[{index}] is {entry!r}, of type {type(entry).__name__}. Every entry has "
                    "to be a household profile name. Should be List[str]."
                )

        # A bare string reaches this field as a list of its characters: dataclasses_json coerces
        # "CHR01_..." into ["C", "H", "R", ...] rather than refusing it, and every character then
        # looks like an unknown household. Detect the shape here so the refusal can say so.
        looks_like_a_split_string = all(len(entry) == 1 for entry in self.lpg_households)
        resolved = [
            self._resolve_one_lpg_household(name, looks_like_a_split_string) for name in self.lpg_households
        ]
        if len(resolved) == 1:
            return resolved[0]
        return resolved

    def _resolve_one_lpg_household(self, household_name: str, looks_like_a_split_string: bool) -> JsonReference:
        """Look one LPG household profile name up in the registry, or refuse it.

        Split out of :meth:`resolve_lpg_households` so the single-name and the multi-name case
        cannot drift apart: both go through this lookup and therefore produce the same refusal for
        an unknown name.

        Args:
            household_name: The configured profile name, an attribute name of the LPG ``Households``
                registry such as ``"CHR01_Couple_both_at_Work"``.
            looks_like_a_split_string: Whether every configured entry is a single character, which
                is what a household name given as a bare string rather than a list decodes to. The
                refusal then says so instead of complaining about each character on its own.

        Returns:
            JsonReference: The registry entry the name stands for.

        Raises:
            ValueError: If the registry defines no household of that name.
        """
        known_names = [name for name in vars(Households) if not name.startswith("_")]
        if household_name not in known_names:
            suggestions = difflib.get_close_matches(household_name, known_names, n=3, cutoff=0.5)
            hint = f" Did you mean {', '.join(suggestions)}?" if suggestions else ""
            if looks_like_a_split_string:
                hint = (
                    " This looks like one household name given as a bare string rather than a list of names,"
                    " split into one entry per character."
                )
            raise ValueError(
                f"Unknown LPG household '{household_name}' in 'lpg_households'. The legal names are the "
                f"{len(known_names)} household profiles defined in {self.LPG_HOUSEHOLD_REGISTRY}, for example "
                f"'{known_names[0]}'.{hint} An unknown name used to be skipped without a word, which built a "
                "household with fewer occupants than the configuration asked for."
            )
        registry_entry: JsonReference = getattr(Households, household_name)
        return registry_entry
