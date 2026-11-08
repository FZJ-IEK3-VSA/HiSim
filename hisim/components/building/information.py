"""Derived building parameters from the EPISCOPE/TABULA typology data.

Holds ``BuildingInformation``, which reads a ``BuildingConfig`` and the TABULA row its code
names and derives every quantity the rest of a scenario is sized against. The declaration of
those facts is on the configuration, in ``config.py``; this module is the physics behind them.
"""

import enum
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd


from hisim import loadtypes as lt
from hisim import log, utils
from hisim.config import concrete
from hisim.components.building.config import BuildingConfig
from hisim.components.building.solar_gains import WindowGeometry


@dataclass(frozen=True)
class ValueOrigin:
    """Where one quantity the Building reads came from: the configuration, the TABULA row, or a rule.

    Every optional field of :class:`BuildingConfig` means "take the TABULA row's value" when it is
    unset, so a building described in the configuration can still carry values of the row it
    names without anyone noticing. :meth:`BuildingInformation.value_origins` hands out one of
    these per quantity, and a caller that wants nothing of the row checks that no origin is
    :attr:`TABULA_ROW`.
    """

    #: The value was set in the configuration.
    CONFIGURED: ClassVar[str] = "configured"
    #: The value is the TABULA row's, or computed from the row's columns.
    TABULA_ROW: ClassVar[str] = "TABULA row"
    #: The value follows from a rule of this class: a fixed table, a fallback, or other derived
    #: quantities, whose own origins say where they came from.
    DERIVED: ClassVar[str] = "derived"

    #: One of the three class constants.
    source: str
    #: The columns, the rule or the configuration field, for the report.
    detail: str


class ZeroReferenceWindowAreaError(ValueError):

    """Raised for a TABULA row whose reference window areas are both zero, when windows are asked of it.

    The window area, its U-value and its transmission losses come from the row's reference
    window areas (``A_Window_1`` and ``A_Window_2``); the per-direction window areas that carry
    the solar gains are the row's ``A_Window_<direction>`` columns rescaled so their sum matches
    that window area. A row whose reference window areas are both zero has no distribution to
    rescale, which makes two cases inconsistent, and both are refused by name (hisim-component-physics-4g9.1):

    * A window area configured for such a row would count in the transmission losses while it
      vanished from every orientation.
    * A row that states its windows only per direction -- zero reference areas but a non-zero
      per-direction total, as ``ES.ME.MFH.05.Gen.ReEx.001.001/.002/.003`` do -- would silently
      lose every window it has, whether or not a window area is configured.

    A row with no window data anywhere keeps a window area and scaling factor of 0. A caller
    who wants windows has to name a row whose reference window areas carry them.
    """


@enum.unique
class CellarCondition(str, enum.Enum):
    """The condition of a TABULA row's cellar, as its column ``Code_CellarCond`` spells it.

    The codes and their meanings come from TABULA's EU.01 geometry method (MOBASY 2021, Annex B.1: the codes ``-``,
    ``N``, ``P``, ``C`` and ``NI``) and from the rows themselves: ``f_CellarCond``, the heated fraction of the
    cellar; ``n_Storey_effective`` against ``n_Storey_effective_envelope``; and the floor's sub-elements with their
    adjustment factors ``b_Transmission_Floor_*``. The meanings of ``NI`` and ``0`` are inferred from the rows and
    are not yet checked against Annex B.1. In the German national rows ``N`` is the code of nearly every class up to
    ``.10`` (single-family houses to 2001), ``-`` and ``NI`` are the codes of the classes ``.11`` and ``.12`` (from
    2002 on).
    """

    #: An unheated cellar outside the thermal envelope: the floor to the cellar has b = 0.5, and the cellar adds no
    #: storey to the envelope.
    UNHEATED = "N"
    #: A heated (conditioned) cellar: it adds one storey to the heated and to the envelope storey count.
    HEATED = "C"
    #: A partly heated cellar: it adds half a storey (``DE.N.MFH.03``, ``DE.N.TH.08``, ``DE.N.TH.10``).
    PARTLY_HEATED = "P"
    #: No cellar: the row states no floor to a cellar (``A_Floor_1`` = 0), its floor is ``Floor_2``, "Soil", with
    #: b = 0.5 (``DE.N.SFH.11``, ``.12``, ``DE.N.MFH.11``, ``.12``).
    NONE = "-"
    #: An unheated cellar inside the insulated envelope (inferred): the cellar adds a storey to the envelope count but
    #: not to the heated one, the floor to the cellar has b = 0 and the cellar's floor to the soil b = 0.5
    #: (``DE.N.TH.11``, ``.12``).
    UNHEATED_INSIDE_THE_ENVELOPE = "NI"
    #: The code of the synthetic-average rows only (``DE.N.SFH-TH.01-06.Gen.SyAv.*``, ``MFH-AB.*``,
    #: ``DE.TestRegion.*``), which state no geometry; read as "no cellar" by the owner on 2026-11-06.
    SYNTHETIC_AVERAGE = "0"


class BuildingInformation:
    """Class for collecting important building parameters to pass to other components.

    The class reads the building config and collects all the important parameters of the buidling.

    """

    #: Process-wide cache of the TABULA housing CSV (class-scoped per repo convention).
    #: The file is static for the lifetime of a process, so it is read at most once and
    #: shared by every instantiation instead of being re-parsed per instance.
    _housing_reference_dataframe: ClassVar[Optional[pd.DataFrame]] = None

    # Fixed model constants, named without changing any value (cleanup phase 3, hazard 7).
    # Units and sources are stated as far as the code documents them.

    #: Heat transfer coefficient between the thermal-mass node "m" and the internal-surface
    #: node "s" [W/(m2 K)]; fixed value h_ms from ISO 13790 (12.2.2, eq. 64, p. 79).
    HEAT_TRANSFER_COEFF_THERMAL_MASS_AND_INTERNAL_SURFACE_IN_WATT_PER_M2_PER_KELVIN: float = 9.1
    #: Dimensionless ratio between the total internal surface area and the conditioned
    #: floor area; A_at from ISO 13790 (7.2.2.2, eq. 9, p. 36), before labeled lambda_at.
    RATIO_BETWEEN_INTERNAL_SURFACE_AREA_AND_FLOOR_AREA: float = 4.5
    #: Heat transfer coefficient between the indoor-air node and the internal-surface node
    #: "s" [W/(m2 K)]; fixed value h_is from ISO 13790 (7.2.2.2, eq. 9, p. 35).
    HEAT_TRANSFER_COEFF_INDOOR_AIR_AND_INTERNAL_SURFACE_IN_WATT_PER_M2_PER_KELVIN: float = 3.45
    #: Volumetric heat capacity of air [Wh/(m3 K)], used for the ventilation conductance.
    HEAT_CAPACITY_OF_AIR_PER_VOLUME_IN_WATT_HOUR_PER_M3_PER_KELVIN: float = 0.34
    #: Average living area per apartment in Germany in 2021 [m2], from the table at
    #: https://www.umweltbundesamt.de/daten/private-haushalte-konsum/wohnen/wohnflaeche#zahl-der-wohnungen-gestiegen
    AVERAGE_LIVING_AREA_PER_APARTMENT_IN_2021_IN_M2: float = 92.1
    #: Conversion factor between joule and watt-hour: 1 Wh = 3600 J.
    JOULE_PER_WATT_HOUR: float = 3.6e3
    #: Fallback conditioned floor area [m2] used when TABULA's A_C_Ref is 0 and the config
    #: provides no floor area either (findings log entry 3; a magic default, logged only).
    FALLBACK_CONDITIONED_FLOOR_AREA_IN_M2: float = 500.0
    #: Thermal-bridging surcharge delta_U [W/(m2 K)] substituted when the TABULA row says 0
    #: (findings log entry 6; a physics question for the design review, value unchanged).
    THERMAL_BRIDGING_DELTA_U_WHEN_TABULA_IS_ZERO_IN_WATT_PER_M2_PER_KELVIN: float = 0.1
    #: Transmission adjustment factor b applied to a floor whose U-value comes from the
    #: config instead of TABULA; the code does not document where the 0.5 comes from.
    FLOOR_ADJUSTMENT_FACTOR_FOR_CONFIGURED_U_VALUE: float = 0.5
    #: Effective-mass-area factor f_a per building heat capacity class, from ISO 13790,
    #: table 12, p. 69/70; multiplied with the conditioned floor area it gives the
    #: effective mass area A_m. Copied per instance into ``building_heat_capacity_class_f_a``.
    BUILDING_HEAT_CAPACITY_CLASS_F_A: ClassVar[Dict[str, float]] = {
        "very light": 2.5,
        "light": 2.5,
        "medium": 2.5,
        "heavy": 3.0,
        "very heavy": 3.5,
    }
    #: Internal heat capacity f_c [J/(m2 K)] per building heat capacity class, from ISO
    #: 13790, table 12, p. 69/70; multiplied with the conditioned floor area it gives the
    #: thermal capacity of the building mass. Copied per instance into
    #: ``building_heat_capacity_class_f_c_in_joule_per_m2_per_kelvin``.
    BUILDING_HEAT_CAPACITY_CLASS_F_C_IN_JOULE_PER_M2_PER_KELVIN: ClassVar[Dict[str, float]] = {
        "very light": 8e4,
        "light": 1.1e5,
        "medium": 1.65e5,
        "heavy": 2.6e5,
        "very heavy": 3.7e5,
    }

    @dataclass(frozen=True)
    class EnvelopeElement:
        """One row of the class-scoped envelope-element table.

        The five envelope elements (floor, wall, roof, window, door) used to be handled by
        ten near-identical ``set_*`` methods; the per-element knowledge those methods
        duplicated lives in one frozen descriptor per element instead (cleanup phase 4).
        A descriptor names the TABULA columns of the element's reference sub-areas, actual
        U-values and transmission adjustment factors, the two ``BuildingConfig`` fields
        that override area and U-value, and the two rules that vary between elements: the
        adjustment factor used when TABULA's b_Transmission columns are not consulted, and
        the zero-reference-area guard against dividing the U-value average by zero. The
        descriptors are consumed by
        :py:meth:`BuildingInformation._scaled_element_area_in_m2` and
        :py:meth:`BuildingInformation._element_u_value_and_adjustment_factor`.
        """

        #: Human-readable element name; not used in any computation, but it makes the
        #: frozen dataclass repr identify the element during debugging and in test output.
        element_name: str
        #: TABULA columns holding the element's reference sub-areas in m2 (``A_*``). Their
        #: sum, scaled with the conditioned-living-area scaling factor, is the element
        #: area, and it is also the weight and divisor of the U-value average.
        area_columns: Tuple[str, ...]
        #: TABULA columns holding the element's actual U-values in W/(m2 K)
        #: (``U_Actual_*``), parallel to :py:attr:`area_columns` entry by entry so the
        #: area-weighted U-value average pairs each U-value with its own sub-area.
        u_value_columns: Tuple[str, ...]
        #: TABULA columns holding the element's transmission adjustment factors
        #: (``b_Transmission_*``); the largest one is used when the U-value comes from
        #: TABULA. Empty for window and door, whose factor is never taken from TABULA.
        transmission_adjustment_columns: Tuple[str, ...]
        #: Name of the ``BuildingConfig`` field whose non-``None`` value replaces the
        #: scaled TABULA area for this element (the ``*_area_in_m2`` fields).
        configured_area_field: str
        #: Name of the ``BuildingConfig`` field whose non-``None`` value replaces the
        #: TABULA U-value average (the ``*_u_value_in_watt_per_m2_per_kelvin`` fields).
        configured_u_value_field: str
        #: Transmission adjustment factor used whenever the b_Transmission columns are not
        #: consulted: always for elements that declare none (window and door use 1), and
        #: when the U-value is configured for those that do (floor uses 0.5, wall and
        #: roof use 1).
        fixed_adjustment_factor: float
        #: Whether a zero total TABULA reference area keeps the first raw U-value instead
        #: of dividing by zero. The door and the window set this. The door's single-column
        #: weighted average is the pinned ``(u * area) / area`` float round trip (findings
        #: log entries 7 and 17), which the guard skips when ``A_Door_1`` is 0; the window's
        #: two-column average divides by zero for a row whose reference window areas are
        #: both zero (hisim-component-physics-4g9.1). A zero-area element has zero conductance either way,
        #: so the kept raw U-value is the material's property, unused by the physics.
        keep_u_value_when_reference_area_is_zero: bool = False
        #: Name of the ``BuildingConfig`` field whose non-``None`` value is the element's
        #: transmission adjustment factor, whatever the U-value's source; empty for window and
        #: door, whose factor is always 1.
        configured_adjustment_factor_field: str = ""

    @dataclass(frozen=True)
    class ElementValues:
        """What the building uses for one envelope element, and where each value came from.

        One per element, recorded by :py:meth:`get_building_heat_transfer_parameters` from the
        very values its conductances multiply, and handed out by :py:meth:`element_values`.
        """

        #: The U-value [W/(m2 K)] the conductance uses.
        u_value_in_watt_per_m2_per_kelvin: float
        #: The transmission adjustment factor the conductance uses.
        adjustment_factor: float
        #: The descriptor's fixed factor, the one used whenever the U-value is configured.
        fixed_adjustment_factor: float
        #: Whether :py:attr:`adjustment_factor` is the row's largest ``b_Transmission`` column
        #: rather than the fixed factor: true for floor, wall and roof with a U-value from TABULA.
        adjustment_factor_from_tabula_row: bool
        #: Where the U-value came from: ``configured``, the TABULA row's columns, or for a door
        #: the row does not state the estimate that replaced it.
        u_value_origin: str

    # The envelope-element table: one descriptor per element, consumed by the explicit
    # area and heat-transfer pipelines in get_building_area_parameters and
    # get_building_heat_transfer_parameters. Adding an element means adding a descriptor
    # here plus its named attribute assignments in those two pipelines.

    #: Floor: two TABULA sub-areas, and the only element whose configured-U-value branch
    #: uses an adjustment factor other than 1 (the undocumented 0.5).
    FLOOR_ELEMENT: ClassVar[EnvelopeElement] = EnvelopeElement(
        element_name="floor",
        area_columns=("A_Floor_1", "A_Floor_2"),
        u_value_columns=("U_Actual_Floor_1", "U_Actual_Floor_2"),
        transmission_adjustment_columns=("b_Transmission_Floor_1", "b_Transmission_Floor_2"),
        configured_area_field="floor_area_in_m2",
        configured_u_value_field="floor_u_value_in_watt_per_m2_per_kelvin",
        fixed_adjustment_factor=FLOOR_ADJUSTMENT_FACTOR_FOR_CONFIGURED_U_VALUE,
        configured_adjustment_factor_field="floor_transmission_adjustment_factor",
    )
    #: Wall: the only element with three TABULA sub-areas. Its derived attributes and
    #: config fields are named "facade" while the TABULA columns say "Wall".
    WALL_ELEMENT: ClassVar[EnvelopeElement] = EnvelopeElement(
        element_name="wall",
        area_columns=("A_Wall_1", "A_Wall_2", "A_Wall_3"),
        u_value_columns=("U_Actual_Wall_1", "U_Actual_Wall_2", "U_Actual_Wall_3"),
        transmission_adjustment_columns=("b_Transmission_Wall_1", "b_Transmission_Wall_2", "b_Transmission_Wall_3"),
        configured_area_field="facade_area_in_m2",
        configured_u_value_field="facade_u_value_in_watt_per_m2_per_kelvin",
        fixed_adjustment_factor=1,
        configured_adjustment_factor_field="facade_transmission_adjustment_factor",
    )
    #: Roof: two TABULA sub-areas, adjustment factor 1 when the U-value is configured.
    ROOF_ELEMENT: ClassVar[EnvelopeElement] = EnvelopeElement(
        element_name="roof",
        area_columns=("A_Roof_1", "A_Roof_2"),
        u_value_columns=("U_Actual_Roof_1", "U_Actual_Roof_2"),
        transmission_adjustment_columns=("b_Transmission_Roof_1", "b_Transmission_Roof_2"),
        configured_area_field="roof_area_in_m2",
        configured_u_value_field="roof_u_value_in_watt_per_m2_per_kelvin",
        fixed_adjustment_factor=1,
        configured_adjustment_factor_field="roof_transmission_adjustment_factor",
    )
    #: Window: no b_Transmission columns (factor is always 1), and its own zero-area guard,
    #: the same rule the door's descriptor declares: a row whose reference window areas
    #: are both zero keeps the raw U_Actual_Window_1 instead of dividing the weighted
    #: average by zero (hisim-component-physics-4g9.1). The per-direction area scaling remains the one
    #: legitimate special case the table does not cover; it lives as an explicit
    #: window-specific step in get_building_area_parameters, which scales by 0 for a row
    #: with no window data anywhere and refuses a zero-reference row that states its windows
    #: per direction only, or that is given a configured window area
    #: (``ZeroReferenceWindowAreaError``). A row that reaches the U-value guard therefore
    #: has a window area of 0.
    WINDOW_ELEMENT: ClassVar[EnvelopeElement] = EnvelopeElement(
        element_name="window",
        area_columns=("A_Window_1", "A_Window_2"),
        u_value_columns=("U_Actual_Window_1", "U_Actual_Window_2"),
        transmission_adjustment_columns=(),
        configured_area_field="window_area_in_m2",
        configured_u_value_field="window_u_value_in_watt_per_m2_per_kelvin",
        fixed_adjustment_factor=1,
        keep_u_value_when_reference_area_is_zero=True,
    )
    #: Door: a single TABULA sub-area, no b_Transmission columns, and the zero-area guard
    #: that keeps the raw U-value when A_Door_1 is 0 instead of dividing by it. A row
    #: whose A_Door_1 is 0 is not left doorless when TABULA estimates a door for it: the
    #: door-specific steps :py:meth:`_door_area_in_m2_and_origin` and
    #: :py:meth:`_door_u_value_adjustment_factor_and_origin` substitute the row's
    #: A_Estim_Door and a door U-value for the missing reference door.
    DOOR_ELEMENT: ClassVar[EnvelopeElement] = EnvelopeElement(
        element_name="door",
        area_columns=("A_Door_1",),
        u_value_columns=("U_Actual_Door_1",),
        transmission_adjustment_columns=(),
        configured_area_field="door_area_in_m2",
        configured_u_value_field="door_u_value_in_watt_per_m2_per_kelvin",
        fixed_adjustment_factor=1,
        keep_u_value_when_reference_area_is_zero=True,
    )

    #: Directions for which TABULA lists per-direction window areas (the
    #: ``A_Window_<direction>`` columns), in the order the per-direction scaling reads
    #: them and in which ``scaled_window_areas_in_m2`` is laid out.
    WINDOWS_DIRECTIONS: ClassVar[Tuple[str, ...]] = ("South", "East", "North", "West", "Horizontal")

    #: The azimuth in degrees of each TABULA window direction, 180 being south; ``None`` for the
    #: horizontal direction, which has no azimuth. The window of each direction stands at a tilt
    #: of 90°, the horizontal one at 0°.
    WINDOW_DIRECTION_AZIMUTHS_IN_DEGREES: ClassVar[Mapping[str, Optional[float]]] = {
        "South": 180.0,
        "East": 90.0,
        "North": 0.0,
        "West": 270.0,
        "Horizontal": None,
    }
    #: The name of the horizontal direction in :attr:`WINDOWS_DIRECTIONS`.
    HORIZONTAL_WINDOW_DIRECTION: ClassVar[str] = "Horizontal"
    #: The tilt against the horizontal, in degrees, of a window in a facade and of a roof light.
    FACADE_WINDOW_TILT_IN_DEGREES: ClassVar[float] = 90.0
    HORIZONTAL_WINDOW_TILT_IN_DEGREES: ClassVar[float] = 0.0

    #: TABULA column holding the typology's own estimate of the door area [m2]. It is
    #: filled in on every row whose ``A_Door_1`` is 0, and it stands in for the reference
    #: door area of such a row (see :py:meth:`_door_area_in_m2_and_origin`).
    ESTIMATED_DOOR_AREA_COLUMN: ClassVar[str] = "A_Estim_Door"
    #: Door U-value [W/(m2 K)] of an estimated door when neither its own row nor any
    #: national row of its country states a door U-value (Denmark lists no door at all).
    #: The value is the U-value of TABULA's existing Irish door construction
    #: ``IE.Door.ReEx.01.01``, the construction chosen for patching the doorless Irish
    #: band-05 rows. TO BE REVIEWED: it is a plausible old-door value, not a
    #: country-specific one.
    DEFAULT_ESTIMATED_DOOR_U_VALUE_IN_WATT_PER_M2_PER_KELVIN: float = 3.0
    #: Process-wide cache of the national mean door U-values, keyed by (country, variant)
    #: and by (country, ``None``) for the mean over all variants; built on first use from
    #: the cached TABULA frame (see :py:meth:`national_mean_door_u_values`).
    _national_mean_door_u_values: ClassVar[Optional[Dict[Tuple[str, Optional[str]], float]]] = None
    #: A TABULA code's country prefix; a national row's code continues with ``.N.``.
    COUNTRY_CODE_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(r"^(?P<country>[A-Z]{2})\.")
    NATIONAL_CODE_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(r"^(?P<country>[A-Z]{2})\.N\.")
    #: A TABULA code's refurbishment variant, its final three digits: ``001`` is the existing
    #: state, ``002``/``003`` and e.g. Ireland's ``011``/``013`` are refurbished states.
    VARIANT_CODE_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(r"\.(?P<variant>\d{3})$")

    def __init__(
        self,
        config: BuildingConfig,
    ):
        """Derive every building parameter as one explicit ordered pipeline.

        Each step below is a method called directly from here that assigns its results to
        named attributes, and the order is the data-flow order: the TABULA row lookup
        first, then the envelope areas (which need the row and produce the scaling
        factor), then the heat-transfer parameters (which need the areas), then the
        thermal-mass constants and the maximum thermal demand (which need both), and the
        TABULA reference indicators last. The former deeper chain of mutating helpers —
        temporal coupling by convention — was flattened into this pipeline in cleanup
        phase 4.
        """

        self.buildingconfig = config

        # Where each quantity the Building reads came from, filled by the pipeline steps below
        # as they decide between a configured value and the row's; see value_origins().
        self._origins: Dict[str, ValueOrigin] = {}

        # get set temperatures for building
        self.set_heating_temperature_for_building_in_celsius = self.buildingconfig.set_heating_temperature_in_celsius
        self.set_cooling_temperature_for_building_in_celsius = self.buildingconfig.set_cooling_temperature_in_celsius
        self.heating_reference_temperature_in_celsius = concrete(
            self.buildingconfig.heating_reference_temperature_in_celsius
        )

        self.building_heat_capacity_class = self.buildingconfig.building_heat_capacity_class

        self.get_building_from_tabula()

        self.get_building_area_parameters()

        self.get_building_heat_transfer_parameters()

        self.get_constants()

        self.get_max_thermal_building_demand()

        self.set_reference_data_from_tabula(
            buildingdata=self.buildingdata_ref,
            scaled_conditioned_floor_area_in_m2=self.scaled_conditioned_floor_area_in_m2,
        )

    @property
    def u_value_wall(self) -> float:
        """Actual U-value of the wall element from the TABULA reference data [W/(m2 K)]."""
        return float(self.buildingdata_ref["U_Actual_Wall_1"].values[0])

    @property
    def u_value_window(self) -> float:
        """Actual U-value of the window element from the TABULA reference data [W/(m2 K)]."""
        return float(self.buildingdata_ref["U_Actual_Window_1"].values[0])

    @property
    def u_value_door(self) -> float:
        """Actual U-value of the door element from the TABULA reference data [W/(m2 K)]."""
        return float(self.buildingdata_ref["U_Actual_Door_1"].values[0])

    @property
    def u_value_roof(self) -> float:
        """Actual U-value of the roof element from the TABULA reference data [W/(m2 K)]."""
        return float(self.buildingdata_ref["U_Actual_Roof_1"].values[0])

    def element_values(self) -> Dict[str, "BuildingInformation.ElementValues"]:
        """Return what the building uses for each envelope element, by the descriptor's ``element_name``.

        The values are the ones :py:meth:`get_building_heat_transfer_parameters` multiplied into
        the conductances, not a recomputation, so a caller naming them -- the RenoVisor
        translator reports the U-value of every element a request leaves out -- names exactly
        what the simulation uses.

        Returns:
            ``floor``, ``wall``, ``roof``, ``window`` and ``door`` -> :py:class:`ElementValues`.
        """
        return dict(self._element_values)

    def value_origins(self) -> Dict[str, "ValueOrigin"]:
        """Return where every quantity the Building reads came from, by the name the Building reads it under.

        The five element areas, U-values and adjustment factors, the conditioned floor area, the
        windows, the two air change rates and the air volume of the ventilation conductance, the
        three ISO 13790 mass and surface quantities, the apartment count and the maximum thermal
        demand: each is :attr:`ValueOrigin.CONFIGURED`, :attr:`ValueOrigin.TABULA_ROW` or
        :attr:`ValueOrigin.DERIVED`, with the detail naming the field, the columns or the rule.
        A caller that describes a building itself and wants nothing of the row it had to name
        checks that no origin is the row's.

        Returns:
            Dict[str, ValueOrigin]: quantity name -> origin.
        """
        return dict(getattr(self, "_origins", {}))

    def window_geometries(self) -> Tuple[WindowGeometry, ...]:
        """Return the windows as the solar-gains producer takes them, one geometry per window.

        From the TABULA row: one window per direction of :attr:`WINDOWS_DIRECTIONS`, in that
        order, with the row's per-direction area rescaled to the window area, at the direction's
        azimuth and tilt, and the row's single optics factors; a direction without area is still
        a window of 0 m². From a configured window list: one geometry per item, in the list's
        order, with its own angles and its own factors where it states them.

        Returns:
            Tuple[WindowGeometry, ...]: the windows.
        """
        return self._window_geometries

    def _record_origin(self, quantity: str, source: str, detail: str) -> None:
        """Record the origin of one quantity for :meth:`value_origins`.

        The record is created here when it is missing, because the test harnesses of this class
        run single pipeline steps on an instance built without ``__init__``.
        """
        if not hasattr(self, "_origins"):
            self._origins = {}
        self._origins[quantity] = ValueOrigin(source=source, detail=detail)

    @property
    def use_air_change_rate_per_hour(self) -> float:
        """Use-related air exchange rate [1/h]: the configured one, else the TABULA row's ``n_air_use``."""
        if self.buildingconfig.use_air_change_rate_per_hour is not None:
            return self.buildingconfig.use_air_change_rate_per_hour
        return float(self.buildingdata_ref["n_air_use"].values[0])

    @property
    def air_infiltration_rate_per_hour(self) -> float:
        """Air infiltration rate [1/h]: the configured one, else the TABULA row's ``n_air_infiltration``.

        The ventilation conductance adds it to the use-related air exchange rate.
        """
        if self.buildingconfig.infiltration_air_change_rate_per_hour is not None:
            return self.buildingconfig.infiltration_air_change_rate_per_hour
        return float(self.buildingdata_ref["n_air_infiltration"].values[0])

    @property
    def thermal_bridging_surcharge_in_watt_per_m2_per_kelvin(self) -> float:
        """Thermal-bridging surcharge delta_U [W/(m2 K)] the building uses.

        The TABULA row's ``delta_U_ThermalBridging``, or
        :py:attr:`THERMAL_BRIDGING_DELTA_U_WHEN_TABULA_IS_ZERO_IN_WATT_PER_M2_PER_KELVIN` for a
        row that reports 0 (findings log entry 6). The thermal-bridging conductance is this
        surcharge times the total envelope area.
        """
        delta_u_thermalbridging_from_tabula = self.buildingdata_ref["delta_U_ThermalBridging"].values[0]
        if delta_u_thermalbridging_from_tabula == 0:
            return self.THERMAL_BRIDGING_DELTA_U_WHEN_TABULA_IS_ZERO_IN_WATT_PER_M2_PER_KELVIN
        return float(delta_u_thermalbridging_from_tabula)

    @property
    def door_report_lines(self) -> List[str]:
        """The door's area and U-value, each with where it came from, for the Building's report.

        A row without a reference door area gets TABULA's estimated door and a U-value from
        the row, its country's national rows or a default (see
        :py:meth:`_door_area_in_m2_and_origin` and
        :py:meth:`_door_u_value_adjustment_factor_and_origin`); the report says which.
        """
        return [
            f"Door Area [m2]: {self.door_area_in_m2:.2f} ({self._door_area_origin})",
            f"Door U-Value [W/m2K]: {self.door_u_value_in_watt_per_m2_per_kelvin:.2f} ({self._door_u_value_origin})",
        ]

    @classmethod
    def read_housing_reference_dataframe(cls) -> pd.DataFrame:
        """Return the TABULA housing CSV as a DataFrame, reading the file at most once per process.

        ``BuildingInformation`` used to re-parse the 3281-row CSV on every instantiation,
        which dominated the construction cost of the class (cleanup phase 3, hazard 5).
        The parsed frame is cached in a class attribute because the file is static for the
        lifetime of a process. The cached frame is shared, so callers must never mutate
        it; :py:meth:`get_building_from_tabula` copies the selected row before exposing it.
        """
        housing_reference_dataframe = cls._housing_reference_dataframe
        if housing_reference_dataframe is None:
            housing_reference_dataframe = pd.read_csv(
                utils.HISIMPATH["housing"],
                decimal=",",
                sep=";",
                encoding="cp1252",
                low_memory=False,
            )
            cls._housing_reference_dataframe = housing_reference_dataframe
        return housing_reference_dataframe

    #: The column of the TABULA rows that states the condition of the cellar (:class:`CellarCondition`).
    CELLAR_CONDITION_COLUMN: ClassVar[str] = "Code_CellarCond"

    #: Where a house's technical room is, by the condition of its cellar. An unheated cellar is an unheated space; a
    #: heated cellar and a house without a cellar keep the technical room in the heated zone. Owner decisions of
    #: 2026-11-04: a partly heated cellar has its technical room in the heated part. Owner decision of 2026-11-06: an
    #: unheated cellar inside the insulated envelope (``NI``, whose floor above has b = 0, so the cellar stays close
    #: to the indoor temperature) and the synthetic averages (``0``) give the heated zone; both meanings are
    #: unverified against MOBASY 2021 Annex B.1.
    TECHNICAL_ROOM_LOCATION_BY_CELLAR_CONDITION: ClassVar[Mapping[CellarCondition, lt.InstallationLocation]] = (
        MappingProxyType(
            {
                CellarCondition.UNHEATED: lt.InstallationLocation.UNHEATED_SPACE,
                CellarCondition.HEATED: lt.InstallationLocation.HEATED_ZONE,
                CellarCondition.PARTLY_HEATED: lt.InstallationLocation.HEATED_ZONE,
                CellarCondition.NONE: lt.InstallationLocation.HEATED_ZONE,
                CellarCondition.UNHEATED_INSIDE_THE_ENVELOPE: lt.InstallationLocation.HEATED_ZONE,
                CellarCondition.SYNTHETIC_AVERAGE: lt.InstallationLocation.HEATED_ZONE,
            }
        )
    )

    @staticmethod
    def technical_room_location_of_cellar_code(cellar_code: str) -> lt.InstallationLocation:
        """Return where a house's technical room is, from the cellar code of its TABULA row.

        The code is looked up in :attr:`TECHNICAL_ROOM_LOCATION_BY_CELLAR_CONDITION`. For example, ``"N"`` (an
        unheated cellar) gives ``UNHEATED_SPACE``, and ``"-"`` (no cellar) gives ``HEATED_ZONE``. A code the table
        does not know is refused, so a new TABULA export cannot slip a meaning past the table.

        Args:
            cellar_code: The row's ``Code_CellarCond``, for example ``"N"``.

        Returns:
            The heated zone or an unheated space; never outdoors.

        Raises:
            ValueError: If the code is not one of :class:`CellarCondition`.
        """
        try:
            cellar_condition = CellarCondition(cellar_code)
        except ValueError:
            known = ", ".join(repr(condition.value) for condition in CellarCondition)
            raise ValueError(
                f"The TABULA cellar code {cellar_code!r} is unknown, so the technical room's location cannot be "
                f"derived from it. The known codes are {known}; pin the building's technical_room_location instead."
            ) from None
        return BuildingInformation.TECHNICAL_ROOM_LOCATION_BY_CELLAR_CONDITION[cellar_condition]

    @classmethod
    def cellar_code_of(cls, building_code: str) -> str:
        """Return the cellar code (``Code_CellarCond``) of the TABULA row a building code names.

        For example, ``"DE.N.SFH.09.Gen.ReEx.001.002"`` (a single-family house of 1995 to 2001) gives ``"N"``.

        Args:
            building_code: The TABULA/EPISCOPE code of the row.

        Returns:
            The row's cellar code as the file spells it.

        Raises:
            ValueError: If no row has that code.
        """
        frame = cls.read_housing_reference_dataframe()
        cellar_codes = frame.loc[frame["Code_BuildingVariant"] == building_code, cls.CELLAR_CONDITION_COLUMN]
        if cellar_codes.empty:
            raise ValueError(f"No TABULA row has the building code {building_code!r}, so it states no cellar code.")
        return str(cellar_codes.iloc[0])

    @classmethod
    def national_mean_door_u_values(cls) -> Dict[Tuple[str, Optional[str]], float]:
        """Return the national mean TABULA door U-values [W/(m2 K)], computed at most once per process.

        A mean runs over a country's national rows (codes ``<CC>.N.…``) that state a door,
        i.e. whose ``A_Door_1`` and ``U_Actual_Door_1`` are both positive. The key
        ``(country, variant)`` holds the mean over the rows of one refurbishment variant
        (the code's final three digits), so an unrefurbished doorless house is not given a
        partly refurbished door; the key ``(country, None)`` holds the mean over every
        variant. The U-values are summed left to right in the file's row order, so the value
        does not depend on a library's summation algorithm. A key without such a row is
        absent from the mapping. The means are the door U-value of an estimated door whose
        own row states none (see :py:meth:`_door_u_value_adjustment_factor_and_origin`).
        """
        national_mean_door_u_values = cls._national_mean_door_u_values
        if national_mean_door_u_values is None:
            frame = cls.read_housing_reference_dataframe()
            # The door descriptor names exactly one TABULA sub-area and one U-value column.
            door_u_values_by_key: Dict[Tuple[str, Optional[str]], List[float]] = {}
            for code, door_area_in_m2, door_u_value in zip(
                frame["Code_BuildingVariant"],
                frame[cls.DOOR_ELEMENT.area_columns[0]],
                frame[cls.DOOR_ELEMENT.u_value_columns[0]],
            ):
                match = cls.NATIONAL_CODE_PATTERN.match(code) if isinstance(code, str) else None
                if match is None or not (door_area_in_m2 > 0 and door_u_value > 0):
                    continue
                country = match.group("country")
                door_u_values_by_key.setdefault((country, None), []).append(float(door_u_value))
                variant_match = cls.VARIANT_CODE_PATTERN.search(code)
                if variant_match is not None:
                    door_u_values_by_key.setdefault((country, variant_match.group("variant")), []).append(
                        float(door_u_value)
                    )
            national_mean_door_u_values = {
                key: cls._left_associated_float_sum(door_u_values) / len(door_u_values)
                for key, door_u_values in door_u_values_by_key.items()
            }
            cls._national_mean_door_u_values = national_mean_door_u_values
        return national_mean_door_u_values

    def get_building_from_tabula(
        self,
    ):
        """Get the building code from a TABULA building."""
        d_f = self.read_housing_reference_dataframe()

        # Gets parameters from chosen building (copied, so the shared cached frame stays untouched)
        self.buildingdata_ref = d_f.loc[d_f["Code_BuildingVariant"] == self.buildingconfig.building_code].copy()
        self.buildingcode = self.buildingconfig.building_code

    def get_constants(
        self,
    ):
        """Derive the thermal-mass, surface-area and radiation constants of the building.

        The fixed ISO 13790 coefficients are exposed as instance attributes (other
        components read them off this class), the two heat-capacity-class lookup tables
        are copied per instance from their class-scoped definitions, and the derived
        capacities, surface areas, radiation reduction factors and the apartment count
        are computed from the scaled conditioned floor area. Requires
        :py:meth:`get_building_area_parameters` to have run.
        """
        # Heat transfer coefficient between nodes "m" and "s" (12.2.2 E64 P79); labeled as h_ms in paper [2] (*** Check header)
        self.heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin = (
            self.HEAT_TRANSFER_COEFF_THERMAL_MASS_AND_INTERNAL_SURFACE_IN_WATT_PER_M2_PER_KELVIN
        )
        # Dimensionless ratio between surfaces and the useful surfaces (7.2.2.2 E9 P36); labeled as A_at in paper [2] (*** Check header); before lambda_at
        self.ratio_between_internal_surface_area_and_floor_area = (
            self.RATIO_BETWEEN_INTERNAL_SURFACE_AREA_AND_FLOOR_AREA
        )
        # Heat transfer coefficient between nodes "air" and "s" (7.2.2.2 E9 P35); labeled as h_is in paper [2] (*** Check header)
        self.heat_transfer_coeff_indoor_air_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin = (
            self.HEAT_TRANSFER_COEFF_INDOOR_AIR_AND_INTERNAL_SURFACE_IN_WATT_PER_M2_PER_KELVIN
        )

        # Copied per instance so a caller mutating its lookup table cannot affect other
        # instances (the former set_constants built fresh dicts per instance as well).
        self.building_heat_capacity_class_f_a: Dict[str, float] = dict(self.BUILDING_HEAT_CAPACITY_CLASS_F_A)
        self.building_heat_capacity_class_f_c_in_joule_per_m2_per_kelvin: Dict[str, float] = dict(
            self.BUILDING_HEAT_CAPACITY_CLASS_F_C_IN_JOULE_PER_M2_PER_KELVIN
        )

        # Room Capacitance [J/K] (TABULA: Internal heat capacity) Ref: ISO standard 12.3.1.2
        # labeled as C_m in the paper [1] (** Check header), before c_m
        if self.buildingconfig.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin is not None:
            self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin = self.buildingconfig.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
            self._record_origin(
                "thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin",
                ValueOrigin.CONFIGURED,
                "thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin",
            )
        else:
            self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin = (
                self.building_heat_capacity_class_f_c_in_joule_per_m2_per_kelvin[self.building_heat_capacity_class] * self.scaled_conditioned_floor_area_in_m2
            )
            self._record_origin(
                "thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin",
                ValueOrigin.DERIVED,
                f"f_c of the heat-capacity class '{self.building_heat_capacity_class}' (ISO 13790 class table) " "times the scaled conditioned floor area",
            )
        # Room Capacitance [Wh/m2K] (TABULA: Internal heat capacity) Ref: ISO standard 12.3.1.2
        self.thermal_capacity_of_building_thermal_mass_in_watthour_per_m2_per_kelvin = (
            self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
            / (self.JOULE_PER_WATT_HOUR * self.scaled_conditioned_floor_area_in_m2)
        )
        # before labeled as a_m
        if self.buildingconfig.effective_mass_area_in_m2 is not None:
            self.effective_mass_area_in_m2 = self.buildingconfig.effective_mass_area_in_m2
            self._record_origin("effective_mass_area_in_m2", ValueOrigin.CONFIGURED, "effective_mass_area_in_m2")
        else:
            self.effective_mass_area_in_m2 = self.scaled_conditioned_floor_area_in_m2 * self.building_heat_capacity_class_f_a[self.building_heat_capacity_class]
            self._record_origin(
                "effective_mass_area_in_m2",
                ValueOrigin.DERIVED,
                f"the scaled conditioned floor area times f_a of the heat-capacity class " f"'{self.building_heat_capacity_class}' (ISO 13790 class table)",
            )
        # before labeled as a_t
        if self.buildingconfig.total_internal_surface_area_in_m2 is not None:
            self.total_internal_surface_area_in_m2 = self.buildingconfig.total_internal_surface_area_in_m2
            self._record_origin("total_internal_surface_area_in_m2", ValueOrigin.CONFIGURED, "total_internal_surface_area_in_m2")
        else:
            self.total_internal_surface_area_in_m2 = self.scaled_conditioned_floor_area_in_m2 * self.ratio_between_internal_surface_area_and_floor_area
            self._record_origin(
                "total_internal_surface_area_in_m2",
                ValueOrigin.DERIVED,
                f"the scaled conditioned floor area times {self.ratio_between_internal_surface_area_and_floor_area} " "(ISO 13790 7.2.2.2)",
            )

        self.reduction_factor_for_non_perpedicular_radiation = self.buildingdata_ref["F_w"].values[0]
        self.reduction_factor_for_frame_area_fraction_of_window = self.buildingdata_ref["F_f"].values[0]
        self.reduction_factor_for_external_vertical_shading = self.buildingdata_ref["F_sh_vert"].values[0]
        self.total_solar_energy_transmittance_for_perpedicular_radiation = self.buildingdata_ref["g_gl_n"].values[0]

        self._window_geometries: Tuple[WindowGeometry, ...] = self._build_window_geometries()

        # Get number of apartments
        self.number_of_apartments = int(
            self.get_number_of_apartments(
                conditioned_floor_area_in_m2=self.scaled_conditioned_floor_area_in_m2,
                scaling_factor=self.scaling_factor_according_to_conditioned_living_area,
                buildingdata=self.buildingdata_ref,
            )
        )

    def _build_window_geometries(self) -> Tuple[WindowGeometry, ...]:
        """Build the windows of :meth:`window_geometries` from the configured list or from the row.

        From the row, the geometry of each direction carries the values ``Building.get_windows``
        used to assemble itself: the direction's azimuth and tilt, the rescaled per-direction area
        and the row's four optics factors, every one passed through ``float``. A configured
        window takes its own angles and area and, for each factor it leaves unset, the row's.

        Returns:
            Tuple[WindowGeometry, ...]: the windows, in the order of the directions or of the list.
        """
        row_frame_fraction = float(self.reduction_factor_for_frame_area_fraction_of_window)
        row_glass_transmittance = float(self.total_solar_energy_transmittance_for_perpedicular_radiation)
        row_nonperpendicular = float(self.reduction_factor_for_non_perpedicular_radiation)
        row_external_shading = float(self.reduction_factor_for_external_vertical_shading)

        if self.buildingconfig.windows is not None:
            self._record_origin(
                "windows",
                ValueOrigin.CONFIGURED,
                f"{len(self.buildingconfig.windows)} configured windows; an unset optics factor is the row's",
            )
            return tuple(
                WindowGeometry(
                    tilt_angle_in_degrees=float(window.tilt_angle_in_degrees),
                    azimuth_angle_in_degrees=(None if window.azimuth_angle_in_degrees is None else float(window.azimuth_angle_in_degrees)),
                    area_in_m2=float(window.area_in_m2),
                    frame_area_fraction_reduction_factor=(
                        row_frame_fraction if window.frame_area_fraction_reduction_factor is None else float(window.frame_area_fraction_reduction_factor)
                    ),
                    glass_solar_transmittance=(row_glass_transmittance if window.glass_solar_transmittance is None else float(window.glass_solar_transmittance)),
                    nonperpendicular_reduction_factor=(
                        row_nonperpendicular if window.nonperpendicular_reduction_factor is None else float(window.nonperpendicular_reduction_factor)
                    ),
                    external_shading_vertical_reduction_factor=(
                        row_external_shading if window.external_shading_vertical_reduction_factor is None else float(window.external_shading_vertical_reduction_factor)
                    ),
                )
                for window in self.buildingconfig.windows
            )

        self._record_origin(
            "windows",
            ValueOrigin.TABULA_ROW,
            "A_Window_<direction> rescaled to the window area; F_f, g_gl_n, F_w, F_sh_vert for every window",
        )
        geometries = []
        for index, windows_direction in enumerate(self.windows_directions):
            if windows_direction == self.HORIZONTAL_WINDOW_DIRECTION:
                window_tilt_angle = self.HORIZONTAL_WINDOW_TILT_IN_DEGREES
            else:
                window_tilt_angle = self.FACADE_WINDOW_TILT_IN_DEGREES
            azimuth_angle = self.WINDOW_DIRECTION_AZIMUTHS_IN_DEGREES[windows_direction]
            geometries.append(
                WindowGeometry(
                    tilt_angle_in_degrees=window_tilt_angle,
                    azimuth_angle_in_degrees=None if azimuth_angle is None else float(azimuth_angle),
                    area_in_m2=float(self.scaled_window_areas_in_m2[index]),
                    frame_area_fraction_reduction_factor=row_frame_fraction,
                    glass_solar_transmittance=row_glass_transmittance,
                    nonperpendicular_reduction_factor=row_nonperpendicular,
                    external_shading_vertical_reduction_factor=row_external_shading,
                )
            )
        return tuple(geometries)

    def _record_floor_area_origin(self) -> None:
        """Record where the scaled conditioned floor area came from, after the scaling step chose it."""
        if self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None:
            self._record_origin(
                "scaled_conditioned_floor_area_in_m2",
                ValueOrigin.CONFIGURED,
                "absolute_conditioned_floor_area_in_m2, passed through the row's A_C_Ref as ref * (target / ref)",
            )
        elif self.buildingconfig.total_base_area_in_m2 is not None:
            self._record_origin(
                "scaled_conditioned_floor_area_in_m2",
                ValueOrigin.CONFIGURED,
                "total_base_area_in_m2, passed through the row's A_C_Ref as ref * (target / ref)",
            )
        elif self.conditioned_floor_area_in_m2_tabula_ref == 0:
            self._record_origin(
                "scaled_conditioned_floor_area_in_m2",
                ValueOrigin.DERIVED,
                f"the fallback of {self.FALLBACK_CONDITIONED_FLOOR_AREA_IN_M2} m2 for a row whose A_C_Ref is 0",
            )
        else:
            self._record_origin("scaled_conditioned_floor_area_in_m2", ValueOrigin.TABULA_ROW, "A_C_Ref")

    def _record_element_area_origin(self, element: EnvelopeElement) -> None:
        """Record where one element's area came from: its configured field or the row's scaled sub-areas."""
        if getattr(self.buildingconfig, element.configured_area_field) is not None:
            self._record_origin(element.configured_area_field, ValueOrigin.CONFIGURED, element.configured_area_field)
        else:
            self._record_origin(
                element.configured_area_field,
                ValueOrigin.TABULA_ROW,
                f"{' + '.join(element.area_columns)} times the conditioned-floor-area scaling factor",
            )

    def get_building_area_parameters(self):
        """Derive every envelope area of the building as an explicit ordered pipeline.

        The conditioned floor area and its scaling factor come first because every
        TABULA-sourced area is scaled with that factor; then each envelope element's area
        is assigned by name from the shared :py:meth:`_scaled_element_area_in_m2` helper
        and the element's table descriptor, in the order the former per-element methods
        ran. The window's per-direction scaling follows its area as the one element
        special case the table does not cover: the per-direction TABULA window areas are
        rescaled so their total matches the resulting window area, which divides by the
        TABULA reference window area — zero for some codes, for which the scaling factor
        is 0 instead of a division by zero when the row has no window data anywhere, and a
        ``ZeroReferenceWindowAreaError`` when the row states its windows per direction only
        or a window area is configured for it (findings log entries 1 and 2, hisim-component-physics-4g9.1).
        The door follows as the second special case: a row without a reference door area
        gets TABULA's estimated door area. The total envelope area closes the pipeline.
        """
        # Reference area [m^2] (TABULA: Reference floor area A_C_Ref )Ref: ISO standard 7.2.2.2
        self.conditioned_floor_area_in_m2_tabula_ref = float((self.buildingdata_ref["A_C_Ref"].values[0]))

        (self.scaling_factor_according_to_conditioned_living_area, self.scaled_conditioned_floor_area_in_m2) = (
            self.get_scaling_factor_according_to_conditioned_living_area(
                conditioned_floor_area_in_m2_tabula_ref=self.conditioned_floor_area_in_m2_tabula_ref
            )
        )
        self._record_floor_area_origin()

        self.floor_area_in_m2 = self._scaled_element_area_in_m2(self.FLOOR_ELEMENT)
        self.facade_area_in_m2 = self._scaled_element_area_in_m2(self.WALL_ELEMENT)
        self.roof_area_in_m2 = self._scaled_element_area_in_m2(self.ROOF_ELEMENT)
        for element in (self.FLOOR_ELEMENT, self.WALL_ELEMENT, self.ROOF_ELEMENT):
            self._record_element_area_origin(element)

        if self.buildingconfig.windows is not None:
            # Configured windows: the window area is their sum, and the row's per-direction
            # areas are not consulted, so there is nothing to rescale and the factor scales
            # nothing. A window area configured beside the list would be a second statement
            # of the same quantity, so it is refused.
            if self.buildingconfig.window_area_in_m2 is not None:
                raise ValueError(
                    f"building code {self.buildingconfig.building_code}: windows are configured as a list of "
                    f"{len(self.buildingconfig.windows)} and a window area of "
                    f"{self.buildingconfig.window_area_in_m2} m2 is configured beside it; the list's sum is the "
                    "window area, so leave window_area_in_m2 unset"
                )
            self.window_area_in_m2 = self._left_associated_float_sum(float(window.area_in_m2) for window in self.buildingconfig.windows)
            self.window_scaling_factor = 1.0
            self.windows_directions: List[str] = []
            self.scaled_window_areas_in_m2: List[float] = []
            self._record_origin("window_area_in_m2", ValueOrigin.CONFIGURED, "the sum of the configured windows")
        else:
            self.window_area_in_m2 = self._scaled_element_area_in_m2(self.WINDOW_ELEMENT)
            self._record_element_area_origin(self.WINDOW_ELEMENT)

            # Window special case: rescale the per-direction TABULA window areas so their
            # total matches the window area derived above. The divisor is the *reference*
            # window area (findings log entries 1 and 2, hisim-component-physics-4g9.1). A code whose reference
            # areas are both zero has no distribution to rescale. When the row has no window
            # data anywhere the factor is 0, consistent with the window area of 0 derived from
            # the same reference. When it states its windows per direction only, those windows
            # would vanish silently, and a configured window area would count in the
            # transmission losses but vanish from every orientation: both are refused by name.
            tabula_reference_window_area_in_m2 = self._tabula_reference_area_in_m2(self.WINDOW_ELEMENT)
            if tabula_reference_window_area_in_m2 == 0:
                self._refuse_windows_without_a_reference_window_area()
                self.window_scaling_factor = 0.0
            else:
                self.window_scaling_factor = self.window_area_in_m2 / tabula_reference_window_area_in_m2
            self.windows_directions = list(self.WINDOWS_DIRECTIONS)
            self.scaled_window_areas_in_m2 = [
                float(self.buildingdata_ref["A_Window_" + windows_direction].iloc[0]) * self.window_scaling_factor for windows_direction in self.windows_directions
            ]

        # Door special case: a row without a reference door area gets TABULA's estimated
        # door instead of none (see _door_area_in_m2_and_origin).
        self.door_area_in_m2, self._door_area_origin = self._door_area_in_m2_and_origin()
        self._record_origin(
            "door_area_in_m2",
            ValueOrigin.CONFIGURED if self.buildingconfig.door_area_in_m2 is not None else ValueOrigin.TABULA_ROW,
            self._door_area_origin,
        )

        self.building_total_area_in_m2 = (
            self.window_area_in_m2
            + self.floor_area_in_m2
            + self.door_area_in_m2
            + self.facade_area_in_m2
            + self.roof_area_in_m2
        )

    def get_building_heat_transfer_parameters(self):
        """Derive every heat-transfer parameter of the building as an explicit ordered pipeline.

        For each envelope element the U-value and transmission adjustment factor come by
        name from the shared :py:meth:`_element_u_value_and_adjustment_factor` helper and
        the element's table descriptor, and the conductance [W/K] is U-value times element
        area times adjustment factor — in that operand order, which is pinned float
        behavior. Thermal bridging and ventilation follow, then the two totals; the method
        requires :py:meth:`get_building_area_parameters` to have run, because the
        conductances multiply with the element areas derived there.
        """
        self._element_values: Dict[str, BuildingInformation.ElementValues] = {}

        u_value, b_factor = self._recorded_element_values(self.FLOOR_ELEMENT)
        self.floor_u_value_in_watt_per_m2_per_kelvin = u_value
        self.floor_adjustment_factor_from_tabula = b_factor
        self.heat_conductance_floor_in_watt_per_kelvin = u_value * self.floor_area_in_m2 * b_factor

        u_value, b_factor = self._recorded_element_values(self.WALL_ELEMENT)
        self.facade_u_value_in_watt_per_m2_per_kelvin = u_value
        self.facade_adjustment_factor_from_tabula = b_factor
        self.heat_conductance_facade_in_watt_per_kelvin = u_value * self.facade_area_in_m2 * b_factor

        u_value, b_factor = self._recorded_element_values(self.ROOF_ELEMENT)
        self.roof_u_value_in_watt_per_m2_per_kelvin = u_value
        self.roof_adjustment_factor_from_tabula = b_factor
        self.heat_conductance_roof_in_watt_per_kelvin = u_value * self.roof_area_in_m2 * b_factor

        u_value, b_factor = self._recorded_element_values(self.WINDOW_ELEMENT)
        self.window_u_value_in_watt_per_m2_per_kelvin = u_value
        self.window_adjustment_factor_from_tabula = b_factor
        self.heat_conductance_window_in_watt_per_kelvin = u_value * self.window_area_in_m2 * b_factor

        u_value, b_factor, self._door_u_value_origin = self._door_u_value_adjustment_factor_and_origin()
        self._record_element_values(self.DOOR_ELEMENT, u_value, b_factor, self._door_u_value_origin)
        self.door_u_value_in_watt_per_m2_per_kelvin = u_value
        self.door_adjustment_factor_from_tabula = b_factor
        self.heat_conductance_door_in_watt_per_kelvin = u_value * self.door_area_in_m2 * b_factor

        self.heat_conductance_thermal_bridging_in_watt_per_kelvin = (
            self._thermal_bridging_conductance_in_watt_per_kelvin()
        )

        self.heat_conductance_ventilation_in_watt_per_kelvin = self._ventilation_conductance_in_watt_per_kelvin()

        self.total_heat_conductance_transmission = (
            self.heat_conductance_door_in_watt_per_kelvin
            + self.heat_conductance_window_in_watt_per_kelvin
            + self.heat_conductance_facade_in_watt_per_kelvin
            + self.heat_conductance_floor_in_watt_per_kelvin
            + self.heat_conductance_roof_in_watt_per_kelvin
            + self.heat_conductance_thermal_bridging_in_watt_per_kelvin
        )

        self.total_heat_conductance_ventilation = self.heat_conductance_ventilation_in_watt_per_kelvin

    def get_scaling_factor_according_to_conditioned_living_area(
        self, conditioned_floor_area_in_m2_tabula_ref: float
    ) -> Tuple[float, float]:
        """Calculate scaling factors for the building.

        Either the absolute conditioned floor area or the total base area should be given.
        The conditioned floor area, the envelope surface areas or window areas are scaled with a scaling factor.
        """

        if (
            self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None
            and self.buildingconfig.total_base_area_in_m2 is not None
        ):
            raise ValueError("Only one variable can be used, the other one must be None.")

        # The TABULA row is read-only after lookup: when A_C_Ref is 0, the substituted
        # floor area used to be written back into self.buildingdata_ref["A_C_Ref"]
        # (findings log entry 3). The write-back was never re-read - A_C_Ref is read
        # exactly once, before this method runs - so it is dropped here and the row keeps
        # what the TABULA file says.

        # The absolute-conditioned-floor-area and total-base-area branches used to be two
        # verbatim copies of the same arithmetic; they are one parameterized path now,
        # with the configured target area as the only difference.
        if self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None:
            target_floor_area_in_m2_from_config: Optional[float] = (
                self.buildingconfig.absolute_conditioned_floor_area_in_m2
            )
        else:
            target_floor_area_in_m2_from_config = self.buildingconfig.total_base_area_in_m2

        # scaling conditioned floor area
        if target_floor_area_in_m2_from_config is not None:
            # this is for preventing that the conditioned_floor_area is 0 (some buildings in TABULA have conditioned_floor_area (A_C_Ref) = 0)
            if conditioned_floor_area_in_m2_tabula_ref == 0:
                scaled_conditioned_floor_area_in_m2 = target_floor_area_in_m2_from_config
                factor_of_config_floor_area_to_tabula_floor_area = 1.0
            # scaling conditioned floor area
            else:
                factor_of_config_floor_area_to_tabula_floor_area = (
                    target_floor_area_in_m2_from_config / conditioned_floor_area_in_m2_tabula_ref
                )
                # Deliberately ref * (target / ref) instead of plain target: the round
                # trip through the ratio produces e.g. 249.99999999999997 from a
                # configured 250.0, and that float artifact is pinned behavior
                # (findings log entry 4) - do not simplify algebraically.
                scaled_conditioned_floor_area_in_m2 = (
                    conditioned_floor_area_in_m2_tabula_ref * factor_of_config_floor_area_to_tabula_floor_area
                )
            scaling_factor_according_to_conditioned_living_area = factor_of_config_floor_area_to_tabula_floor_area

            # if no value for building size is provided in config, use reference value from Tabula or 500 m^2.
        else:
            if conditioned_floor_area_in_m2_tabula_ref == 0:
                scaled_conditioned_floor_area_in_m2 = self.FALLBACK_CONDITIONED_FLOOR_AREA_IN_M2
                log.warning(
                    "There is no reference given for absolute conditioned floor area in m^2, so a default of 500 m^2 is used."
                )
            else:
                scaled_conditioned_floor_area_in_m2 = conditioned_floor_area_in_m2_tabula_ref

            scaling_factor_according_to_conditioned_living_area = 1.0

        return scaling_factor_according_to_conditioned_living_area, scaled_conditioned_floor_area_in_m2

    @staticmethod
    def _left_associated_float_sum(values: Iterable[float]) -> float:
        """Add floats strictly left to right, like a chain of ``+`` operators does.

        The built-in ``sum`` is NOT behavior-identical to chained ``+`` on floats: since
        Python 3.12 it uses Neumaier compensated summation (CPython gh-100425), which can
        differ from plain left-associated addition in the last mantissa bit — e.g. TABULA's
        wall areas 255.95 + 38.48 + 52.92 give 347.35 chained but 347.34999999999997 via
        ``sum``. The former per-element methods wrote the additions out as ``+`` chains and
        the golden pins those exact bit patterns, so the shared helpers must sum this way.
        """
        total = 0.0
        for value in values:
            total = total + value
        return total

    def _scaled_element_area_in_m2(self, element: EnvelopeElement) -> float:
        """Return the area [m2] of one envelope element, from the config or from TABULA.

        A non-``None`` value in the element's configured-area field of the building config
        wins unchanged; otherwise the element's TABULA reference sub-areas (the descriptor's
        ``area_columns``) are summed and scaled with the conditioned-living-area scaling
        factor, exactly as each of the five former ``set_*_area_parameter`` methods did.
        The helper only returns the value — the pipeline in
        :py:meth:`get_building_area_parameters` assigns it to the element's named attribute.
        """
        configured_area_in_m2: Optional[float] = getattr(self.buildingconfig, element.configured_area_field)
        if configured_area_in_m2 is not None:
            return configured_area_in_m2
        return self._tabula_reference_area_in_m2(element) * self.scaling_factor_according_to_conditioned_living_area

    def _tabula_reference_area_in_m2(self, element: EnvelopeElement) -> float:
        """Return the unscaled TABULA reference area [m2] of one element: its sub-areas summed left to right."""
        return self._left_associated_float_sum(
            float(self.buildingdata_ref[area_column].values[0]) for area_column in element.area_columns
        )

    def _refuse_windows_without_a_reference_window_area(self) -> None:
        """Refuse a zero-reference-window row that states windows per direction or is given a window area.

        Called only for a row whose reference window areas are both zero, where the
        per-direction window scaling has no distribution to rescale (hisim-component-physics-4g9.1). A row
        that lists windows per direction all the same would lose every one of them, and a
        configured window area would count in the transmission losses while it vanished
        from every orientation; either way the building would be inconsistent, so it raises
        ``ZeroReferenceWindowAreaError``. A row with no window data anywhere passes.

        Raises:
            ZeroReferenceWindowAreaError: For a positive per-direction window total, or a
                non-zero configured window area.
        """
        building_code = self.buildingconfig.building_code
        per_direction_window_area_in_m2 = self._left_associated_float_sum(
            float(self.buildingdata_ref["A_Window_" + windows_direction].iloc[0])
            for windows_direction in self.WINDOWS_DIRECTIONS
        )
        reference_columns = " and ".join(self.WINDOW_ELEMENT.area_columns)
        if per_direction_window_area_in_m2 > 0:
            raise ZeroReferenceWindowAreaError(
                f"building code {building_code}: the TABULA row's reference window areas "
                f"{reference_columns} are both zero, but its per-direction window areas add up to "
                f"{per_direction_window_area_in_m2} m2. TABULA states this row's windows only per "
                "direction, so the row cannot be simulated consistently: the window area, U-value and "
                "transmission losses come from the reference areas, which have no windows to give"
            )
        if self.window_area_in_m2 != 0:
            raise ZeroReferenceWindowAreaError(
                f"building code {building_code}: a window area of {self.window_area_in_m2} m2 is "
                f"configured, but the TABULA row's reference window areas {reference_columns} are both "
                "zero, so there is no orientation to distribute it over; name a row with window areas "
                "or leave the window area unset"
            )

    def _door_area_in_m2_and_origin(self) -> Tuple[float, str]:
        """Return the door area [m2] and, for the report, where it came from.

        A configured door area and a row's own reference door area (``A_Door_1``) are used
        as for every other element. A row whose reference door area is 0 does not make the
        building doorless when TABULA estimates a door for it: its ``A_Estim_Door`` stands
        in for the reference door area, scaled with the conditioned-living-area scaling
        factor exactly like every other element area. Only a row without an estimate either
        keeps the door area of 0.
        """
        if self.buildingconfig.door_area_in_m2 is not None:
            return self._scaled_element_area_in_m2(self.DOOR_ELEMENT), "configured"
        reference_door_columns = " + ".join(self.DOOR_ELEMENT.area_columns)
        if self._tabula_reference_area_in_m2(self.DOOR_ELEMENT) == 0:
            estimated_door_area_in_m2 = float(self.buildingdata_ref[self.ESTIMATED_DOOR_AREA_COLUMN].values[0])
            if estimated_door_area_in_m2 > 0:
                return estimated_door_area_in_m2 * self.scaling_factor_according_to_conditioned_living_area, (
                    f"TABULA's estimated door area {self.ESTIMATED_DOOR_AREA_COLUMN}, because the row's "
                    f"reference door area {reference_door_columns} is zero"
                )
            return self._scaled_element_area_in_m2(self.DOOR_ELEMENT), (
                f"the TABULA row states neither a door area {reference_door_columns} nor an estimated one "
                f"{self.ESTIMATED_DOOR_AREA_COLUMN}"
            )
        return (
            self._scaled_element_area_in_m2(self.DOOR_ELEMENT),
            f"TABULA reference door area {reference_door_columns}",
        )

    def _door_u_value_adjustment_factor_and_origin(self) -> Tuple[float, float, str]:
        """Return the door's U-value [W/(m2 K)], its adjustment factor and, for the report, the U-value's origin.

        A configured door U-value and a row with a reference door area go through
        :py:meth:`_element_u_value_and_adjustment_factor` unchanged, so the pinned
        ``(u * area) / area`` round trip of every row with a door stays bit-identical. A row
        whose reference door area is 0 but whose door has an area -- TABULA's estimated
        door, or a configured area -- needs a U-value the weighted average cannot give: the
        row's own ``U_Actual_Door_1`` when it is positive, else the mean door U-value of
        its country's national rows of the same refurbishment variant, else that of all
        their variants (:py:meth:`national_mean_door_u_values`), else the
        reviewed-later default
        :py:attr:`DEFAULT_ESTIMATED_DOOR_U_VALUE_IN_WATT_PER_M2_PER_KELVIN`. A door
        without an area keeps the zero-area guard's raw value, which carries no heat loss.
        """
        u_value, adjustment_factor = self._element_u_value_and_adjustment_factor(self.DOOR_ELEMENT)
        u_value_column = " / ".join(self.DOOR_ELEMENT.u_value_columns)
        if self.buildingconfig.door_u_value_in_watt_per_m2_per_kelvin is not None:
            return u_value, adjustment_factor, "configured"
        if self._tabula_reference_area_in_m2(self.DOOR_ELEMENT) != 0:
            return u_value, adjustment_factor, f"TABULA row, {u_value_column}"
        if self.door_area_in_m2 == 0:
            return u_value, adjustment_factor, f"TABULA row, {u_value_column}; unused, the door has no area"
        if u_value > 0:
            return u_value, adjustment_factor, f"TABULA row, {u_value_column}, although the row states no door area"
        fallback_u_value, fallback_origin = self._door_u_value_and_origin_the_row_does_not_state(u_value_column)
        return fallback_u_value, adjustment_factor, fallback_origin

    def _door_u_value_and_origin_the_row_does_not_state(self, u_value_column: str) -> Tuple[float, str]:
        """Return a door U-value [W/(m2 K)] for a door whose row states none, and its origin.

        The mean of the country's national rows of the row's own refurbishment variant comes
        first, then the mean of all their variants, then
        :py:attr:`DEFAULT_ESTIMATED_DOOR_U_VALUE_IN_WATT_PER_M2_PER_KELVIN`.
        """
        building_code = self.buildingconfig.building_code
        country_match = self.COUNTRY_CODE_PATTERN.match(building_code)
        country = country_match.group("country") if country_match is not None else None
        variant_match = self.VARIANT_CODE_PATTERN.search(building_code)
        variant = variant_match.group("variant") if variant_match is not None else None
        national_means = self.national_mean_door_u_values()
        if country is not None and variant is not None and (country, variant) in national_means:
            return national_means[(country, variant)], (
                f"mean {u_value_column} of the {country} national TABULA rows of variant {variant} that "
                "state a door, because the row states none"
            )
        if country is not None and (country, None) in national_means:
            return national_means[(country, None)], (
                f"mean {u_value_column} of the {country} national TABULA rows of all variants that state a "
                f"door, because the row states none and none of variant {variant} states one"
            )
        return self.DEFAULT_ESTIMATED_DOOR_U_VALUE_IN_WATT_PER_M2_PER_KELVIN, (
            f"default, to be reviewed: neither the row nor any {country} national TABULA row states a door "
            "U-value, so the U-value of TABULA's Irish door construction IE.Door.ReEx.01.01 is used"
        )

    def _recorded_element_values(self, element: EnvelopeElement) -> Tuple[float, float]:
        """Return one element's U-value and adjustment factor, recording them for :py:meth:`element_values`.

        The door has its own origin rules and records itself through
        :py:meth:`_record_element_values`.
        """
        u_value, adjustment_factor = self._element_u_value_and_adjustment_factor(element)
        if getattr(self.buildingconfig, element.configured_u_value_field) is not None:
            origin = "configured"
        else:
            origin = f"TABULA row, area-weighted {' / '.join(element.u_value_columns)}"
        self._record_element_values(element, u_value, adjustment_factor, origin)
        return u_value, adjustment_factor

    def _configured_adjustment_factor(self, element: EnvelopeElement) -> Optional[float]:
        """Return the element's configured transmission adjustment factor, or ``None`` when it has no field or it is unset."""
        if not element.configured_adjustment_factor_field:
            return None
        configured_factor: Optional[float] = getattr(self.buildingconfig, element.configured_adjustment_factor_field)
        return configured_factor

    def _record_element_values(
        self, element: EnvelopeElement, u_value: float, adjustment_factor: float, origin: str
    ) -> None:
        """Record what the building uses for one element, as :py:meth:`element_values` hands it out.

        The origins of :py:meth:`value_origins` are recorded beside it, under the element's two
        configured-field names and ``<element>_adjustment_factor``.
        """
        configured = getattr(self.buildingconfig, element.configured_u_value_field) is not None
        factor_configured = self._configured_adjustment_factor(element) is not None
        from_row = bool(element.transmission_adjustment_columns) and not configured and not factor_configured
        self._element_values[element.element_name] = self.ElementValues(
            u_value_in_watt_per_m2_per_kelvin=u_value,
            adjustment_factor=adjustment_factor,
            fixed_adjustment_factor=element.fixed_adjustment_factor,
            adjustment_factor_from_tabula_row=from_row,
            u_value_origin=origin,
        )
        self._record_origin(
            element.configured_u_value_field,
            ValueOrigin.CONFIGURED if configured else ValueOrigin.TABULA_ROW,
            element.configured_u_value_field if configured else origin,
        )
        if factor_configured:
            factor_source, factor_detail = ValueOrigin.CONFIGURED, element.configured_adjustment_factor_field
        elif from_row:
            factor_source = ValueOrigin.TABULA_ROW
            factor_detail = f"the largest of {' / '.join(element.transmission_adjustment_columns)}"
        else:
            factor_source = ValueOrigin.DERIVED
            factor_detail = f"the fixed factor {element.fixed_adjustment_factor} of the {element.element_name}"
        self._record_origin(f"{element.element_name}_adjustment_factor", factor_source, factor_detail)

    def _element_u_value_and_adjustment_factor(self, element: EnvelopeElement) -> Tuple[float, float]:
        """Return the U-value [W/(m2 K)] and transmission adjustment factor of one element.

        A non-``None`` value in the element's configured-U-value field of the building
        config is returned unchanged, paired with the descriptor's fixed adjustment
        factor. Otherwise the U-value is the TABULA sub-areas' area-weighted average of
        the ``u_value_columns`` — with the summands in column order and the reference
        areas as weights and divisor, so the float results of the former per-element
        methods are reproduced bit for bit. For the door (one column, zero-area guard
        set) that average IS the pinned ``(u * area) / area`` round trip, which is NOT a
        float no-op — it shifts the last mantissa bit for 164 TABULA codes (findings log
        entries 7 and 17) — so it must not be simplified algebraically. The guard skips
        the division only when the reference area is zero (door and window, hisim-component-physics-4g9.1
        for the window's two columns) and keeps the raw first U-value. The
        adjustment factor is the largest of the ``b_Transmission`` columns when the
        descriptor names any, and the fixed factor otherwise (window and door). A configured
        adjustment factor (:attr:`EnvelopeElement.configured_adjustment_factor_field`) replaces
        either, as given.
        """
        configured_factor = self._configured_adjustment_factor(element)
        configured_u_value: Optional[float] = getattr(self.buildingconfig, element.configured_u_value_field)
        if configured_u_value is not None:
            if configured_factor is not None:
                return configured_u_value, configured_factor
            return configured_u_value, element.fixed_adjustment_factor

        u_values = [float(self.buildingdata_ref[u_value_column].values[0]) for u_value_column in element.u_value_columns]
        reference_areas_in_m2 = [
            float(self.buildingdata_ref[area_column].values[0]) for area_column in element.area_columns
        ]
        total_reference_area_in_m2 = self._left_associated_float_sum(reference_areas_in_m2)

        if element.keep_u_value_when_reference_area_is_zero and total_reference_area_in_m2 == 0:
            u_value = u_values[0]
        else:
            u_value = (
                self._left_associated_float_sum(
                    sub_u_value * sub_area_in_m2
                    for sub_u_value, sub_area_in_m2 in zip(u_values, reference_areas_in_m2)
                )
                / total_reference_area_in_m2
            )

        if configured_factor is not None:
            adjustment_factor = configured_factor
        elif element.transmission_adjustment_columns:
            adjustment_factor = max(
                float(self.buildingdata_ref[adjustment_column].values[0])
                for adjustment_column in element.transmission_adjustment_columns
            )
        else:
            adjustment_factor = element.fixed_adjustment_factor

        return u_value, adjustment_factor

    def _thermal_bridging_conductance_in_watt_per_kelvin(self) -> float:
        """Return the thermal-bridging heat-transfer conductance of the building [W/K].

        The conductance is the TABULA delta_U_ThermalBridging surcharge times the total
        envelope area, with rows that report 0 substituted by 0.1 W/(m2 K). The TABULA row
        is read-only after lookup: rows with delta_U_ThermalBridging == 0 used to be
        patched to 0.1 in place before being read back (findings log entry 6), so the
        object's view of the row silently differed from the file. The correction is an
        explicit local value instead; whether the 0.1 W/(m2 K) surcharge is good physics
        is a design-review question, not changed here.
        """
        return self.thermal_bridging_surcharge_in_watt_per_m2_per_kelvin * self.building_total_area_in_m2

    def _ventilation_conductance_in_watt_per_kelvin(self) -> float:
        """Return the ventilation heat-transfer conductance of the building [W/K].

        The conductance is the volumetric heat capacity of air times the total air
        exchange rate (use plus infiltration, each configured or the TABULA row's) times the air
        volume: the configured one, or the row's room height times the scaled conditioned floor
        area, multiplied in that order, which is pinned float behaviour. Requires the area
        pipeline to have run for the scaled conditioned floor area.
        """
        for quantity, field_name, column in (
            ("use_air_change_rate_per_hour", "use_air_change_rate_per_hour", "n_air_use"),
            ("infiltration_air_change_rate_per_hour", "infiltration_air_change_rate_per_hour", "n_air_infiltration"),
        ):
            if getattr(self.buildingconfig, field_name) is not None:
                self._record_origin(quantity, ValueOrigin.CONFIGURED, field_name)
            else:
                self._record_origin(quantity, ValueOrigin.TABULA_ROW, column)
        total_air_change_rate_per_hour = self.use_air_change_rate_per_hour + self.air_infiltration_rate_per_hour

        if self.buildingconfig.conditioned_air_volume_in_m3 is not None:
            self._record_origin("conditioned_air_volume_in_m3", ValueOrigin.CONFIGURED, "conditioned_air_volume_in_m3")
            return self.HEAT_CAPACITY_OF_AIR_PER_VOLUME_IN_WATT_HOUR_PER_M3_PER_KELVIN * total_air_change_rate_per_hour * self.buildingconfig.conditioned_air_volume_in_m3
        self._record_origin("conditioned_air_volume_in_m3", ValueOrigin.TABULA_ROW, "h_room times the scaled conditioned floor area")
        return (
            self.HEAT_CAPACITY_OF_AIR_PER_VOLUME_IN_WATT_HOUR_PER_M3_PER_KELVIN
            * total_air_change_rate_per_hour
            * float(self.buildingdata_ref["h_room"].values[0])
            * self.scaled_conditioned_floor_area_in_m2
        )

    def get_max_thermal_building_demand(self) -> None:
        """Calculate max thermal demand."""
        if self.buildingconfig.max_thermal_building_demand_in_watt is None:

            self.max_thermal_building_demand_in_watt = (
                self.total_heat_conductance_transmission + self.total_heat_conductance_ventilation
            ) * (
                self.buildingconfig.initial_internal_temperature_in_celsius
                - self.heating_reference_temperature_in_celsius
            )
            self._record_origin(
                "max_thermal_building_demand_in_watt",
                ValueOrigin.DERIVED,
                "(transmission + ventilation conductance) times (initial indoor temperature - heating reference "
                "temperature); the thermal-bridging conductance is in the transmission total",
            )
        else:
            self.max_thermal_building_demand_in_watt = self.buildingconfig.max_thermal_building_demand_in_watt
            self._record_origin("max_thermal_building_demand_in_watt", ValueOrigin.CONFIGURED, "max_thermal_building_demand_in_watt")

    def get_number_of_apartments(
        self,
        conditioned_floor_area_in_m2: float,
        scaling_factor: float,
        buildingdata: pd.DataFrame,
    ) -> float:
        """Get number of apartments.

        Either from config or from tabula or through approximation with data from
        https://www.umweltbundesamt.de/daten/private-haushalte-konsum/wohnen/wohnflaeche#zahl-der-wohnungen-gestiegen.
        The config and TABULA sources shared verbatim fallback logic; it is one
        parameterized path now. Only the TABULA source falls back on a rescaled building
        (``scaling_factor != 1``), because a rescaled reference apartment count no longer
        matches the scaled floor area - a configured count is trusted as given.
        """

        if self.buildingconfig.number_of_apartments is not None:
            number_of_apartments_origin = self.buildingconfig.number_of_apartments
            use_average_apartment_size_fallback = number_of_apartments_origin == 0
            origin_source, origin_detail = ValueOrigin.CONFIGURED, "number_of_apartments"
        else:
            number_of_apartments_origin = float(buildingdata["n_Apartment"].values[0])
            # if no value given or if the area given in the config is bigger than the tabula ref area
            use_average_apartment_size_fallback = number_of_apartments_origin == 0 or scaling_factor != 1
            origin_source, origin_detail = ValueOrigin.TABULA_ROW, "n_Apartment"

        if use_average_apartment_size_fallback:
            # check table from the link for the year 2021
            number_of_apartments = conditioned_floor_area_in_m2 / self.AVERAGE_LIVING_AREA_PER_APARTMENT_IN_2021_IN_M2
            origin_source = ValueOrigin.DERIVED
            origin_detail = (
                f"the conditioned floor area over {self.AVERAGE_LIVING_AREA_PER_APARTMENT_IN_2021_IN_M2} m2 per "
                f"apartment, because {origin_detail} is 0 or the building is rescaled"
            )
        elif number_of_apartments_origin > 0:
            number_of_apartments = number_of_apartments_origin
        else:
            raise ValueError("Number of apartments can not be negative.")

        self._record_origin("number_of_apartments", origin_source, origin_detail)
        return number_of_apartments

    def set_reference_data_from_tabula(
        self, buildingdata: pd.DataFrame, scaled_conditioned_floor_area_in_m2: float
    ) -> None:
        """Read the TABULA reference indicators off the building row into named attributes.

        This replaces the former eleven-float positional tuple return that ``__init__``
        unpacked into attributes (cleanup phase 3, hazard 1): two attribute names in that
        tuple differed only by a ``ref``/``reference`` spelling and their unit suffix, so
        a positional swap would have been invisible at the call site. Each attribute is
        now assigned directly next to the TABULA column, comment and unit it belongs to,
        which makes the pairing reviewable in one place. Values, column reads and read
        order are unchanged.
        """
        # Floor area related heat load during heating season
        # reference taken from TABULA (* Check header) Q_sol [kWh/m2.a], before q_sol_ref (or solar heat sources?)
        self.tabula_ref_solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year = float(
            (buildingdata["q_sol"].values[0])
        )
        # Floor area related internal heat sources during heating season
        # reference taken from TABULA (* Check header) as Q_int [kWh/m2.a], before q_int_ref
        self.tabula_ref_internal_heat_sources_reference_in_kilowatthour_per_m2_per_year = float(
            buildingdata["q_int"].values[0]
        )
        # Floor area related annual losses
        # reference taken from TABULA (* Check header) as Q_ht [kWh/m2.a], before q_ht_ref
        self.tabula_ref_total_heat_transfer_reference_in_kilowatthour_per_m2_per_year = float(
            buildingdata["q_ht"].values[0]
        )
        # transmission heat losses
        self.tabula_ref_transmission_heat_losses_ref_in_kilowatthour_per_m2_per_year = float(
            buildingdata["q_ht_tr"].values[0]
        )
        # ventilation heat losses
        self.tabula_ref_ventilation_heat_losses_ref_in_kilowatthour_per_m2_per_year = float(
            buildingdata["q_ht_ve"].values[0]
        )
        # Energy need for heating
        # reference taken from TABULA (* Check header) as Q_H_nd [kWh/m2.a], before q_h_nd_ref
        self.tabula_ref_energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year = float(
            buildingdata["q_h_nd"].values[0]
        )
        # Internal heat capacity per m2 reference area [Wh/(m^2.K)] (TABULA: Internal heat capacity)
        self.tabula_ref_thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin = float(
            buildingdata["c_m"].values[0]
        )
        # gain utilisation factor eta_h_gn
        self.tabula_ref_gain_utilisation_factor_reference = float(buildingdata["eta_h_gn"].values[0])

        # Heat transfer coefficient by ventilation in watt per m2 per kelvin (TABULA column h_Ventilation)
        self.tabula_ref_heat_transfer_coeff_by_ventilation_ref_in_watt_per_m2_per_kelvin = float(
            buildingdata["h_Ventilation"].values[0]
        )
        # Heat transfer coefficient by ventilation in watt per kelvin
        # (the same TABULA column, made absolute with the scaled conditioned floor area)
        self.tabula_ref_heat_transfer_coeff_by_ventilation_reference_in_watt_per_kelvin = (
            float(buildingdata["h_Ventilation"].values[0]) * scaled_conditioned_floor_area_in_m2
        )

        # Heat transfer coefficient by transmission in watt per m2 per kelvin (TABULA column h_Transmission)
        self.tabula_ref_heat_transfer_coeff_by_transmission_ref_in_watt_per_m2_per_kelvin = float(
            buildingdata["h_Transmission"].values[0]
        )
