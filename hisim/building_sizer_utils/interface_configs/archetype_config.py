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

from dataclasses import dataclass, field
from typing import Optional

from dataclasses_json import dataclass_json


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
        ...     lpg_households=["CHR01_Couple_both_at_Work", "CHR03_Family"],
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
