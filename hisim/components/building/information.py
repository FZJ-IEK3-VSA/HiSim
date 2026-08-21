"""Derived building parameters from the EPISCOPE/TABULA typology data.

Part of the ``hisim.components.building`` package split (see the package ``__init__``
for the layout and the TABULA reference). Holds ``BuildingInformation``, moved
verbatim from the former single-module ``building.py``.
"""

# clean

from dataclasses import dataclass
from typing import ClassVar, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from hisim import log, utils
from hisim.components.building.config import BuildingConfig


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
        the door-only guard against a zero reference area. The descriptors are consumed by
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
        #: of dividing by zero. Only the door sets this: its single-column weighted
        #: average is the pinned ``(u * area) / area`` float round trip (findings log
        #: entries 7 and 17), and the guard skips it when ``A_Door_1`` is 0.
        keep_u_value_when_reference_area_is_zero: bool = False

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
    )
    #: Window: no b_Transmission columns (factor is always 1). The per-direction area
    #: scaling is the one legitimate special case the table does not cover; it lives as
    #: an explicit window-specific step in get_building_area_parameters, including the
    #: pinned ZeroDivisionError for codes whose reference window areas are both zero
    #: (findings log entries 1 and 2).
    WINDOW_ELEMENT: ClassVar[EnvelopeElement] = EnvelopeElement(
        element_name="window",
        area_columns=("A_Window_1", "A_Window_2"),
        u_value_columns=("U_Actual_Window_1", "U_Actual_Window_2"),
        transmission_adjustment_columns=(),
        configured_area_field="window_area_in_m2",
        configured_u_value_field="window_u_value_in_watt_per_m2_per_kelvin",
        fixed_adjustment_factor=1,
    )
    #: Door: a single TABULA sub-area, no b_Transmission columns, and the zero-area guard
    #: that keeps the raw U-value when A_Door_1 is 0 instead of dividing by it.
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

        # get set temperatures for building
        self.set_heating_temperature_for_building_in_celsius = self.buildingconfig.set_heating_temperature_in_celsius
        self.set_cooling_temperature_for_building_in_celsius = self.buildingconfig.set_cooling_temperature_in_celsius
        self.heating_reference_temperature_in_celsius = self.buildingconfig.heating_reference_temperature_in_celsius

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
        self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin = (
            self.building_heat_capacity_class_f_c_in_joule_per_m2_per_kelvin[self.building_heat_capacity_class]
            * self.scaled_conditioned_floor_area_in_m2
        )
        # Room Capacitance [Wh/m2K] (TABULA: Internal heat capacity) Ref: ISO standard 12.3.1.2
        self.thermal_capacity_of_building_thermal_mass_in_watthour_per_m2_per_kelvin = (
            self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
            / (self.JOULE_PER_WATT_HOUR * self.scaled_conditioned_floor_area_in_m2)
        )
        # before labeled as a_m
        self.effective_mass_area_in_m2 = (
            self.scaled_conditioned_floor_area_in_m2
            * self.building_heat_capacity_class_f_a[self.building_heat_capacity_class]
        )
        # before labeled as a_t
        self.total_internal_surface_area_in_m2 = (
            self.scaled_conditioned_floor_area_in_m2 * self.ratio_between_internal_surface_area_and_floor_area
        )

        self.reduction_factor_for_non_perpedicular_radiation = self.buildingdata_ref["F_w"].values[0]
        self.reduction_factor_for_frame_area_fraction_of_window = self.buildingdata_ref["F_f"].values[0]
        self.reduction_factor_for_external_vertical_shading = self.buildingdata_ref["F_sh_vert"].values[0]
        self.total_solar_energy_transmittance_for_perpedicular_radiation = self.buildingdata_ref["g_gl_n"].values[0]

        # Get number of apartments
        self.number_of_apartments = int(
            self.get_number_of_apartments(
                conditioned_floor_area_in_m2=self.scaled_conditioned_floor_area_in_m2,
                scaling_factor=self.scaling_factor_according_to_conditioned_living_area,
                buildingdata=self.buildingdata_ref,
            )
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
        TABULA reference window area — zero for some codes, which therefore raise
        (findings log entries 1 and 2, pinned behavior). The total envelope area closes
        the pipeline.
        """
        # Reference area [m^2] (TABULA: Reference floor area A_C_Ref )Ref: ISO standard 7.2.2.2
        self.conditioned_floor_area_in_m2_tabula_ref = float((self.buildingdata_ref["A_C_Ref"].values[0]))

        (self.scaling_factor_according_to_conditioned_living_area, self.scaled_conditioned_floor_area_in_m2) = (
            self.get_scaling_factor_according_to_conditioned_living_area(
                conditioned_floor_area_in_m2_tabula_ref=self.conditioned_floor_area_in_m2_tabula_ref
            )
        )

        self.floor_area_in_m2 = self._scaled_element_area_in_m2(self.FLOOR_ELEMENT)
        self.facade_area_in_m2 = self._scaled_element_area_in_m2(self.WALL_ELEMENT)
        self.roof_area_in_m2 = self._scaled_element_area_in_m2(self.ROOF_ELEMENT)
        self.window_area_in_m2 = self._scaled_element_area_in_m2(self.WINDOW_ELEMENT)

        # Window special case: rescale the per-direction TABULA window areas so their
        # total matches the window area derived above. The divisor is the *reference*
        # window area, so a configured window area cannot rescue a code whose reference
        # areas are both zero — those codes raise ZeroDivisionError here, exactly as
        # pinned in the golden (findings log entries 1 and 2).
        self.window_scaling_factor = self.window_area_in_m2 / (
            float(self.buildingdata_ref["A_Window_1"].values[0])
            + float(self.buildingdata_ref["A_Window_2"].values[0])
        )
        self.windows_directions: List[str] = list(self.WINDOWS_DIRECTIONS)
        self.scaled_window_areas_in_m2: List[float] = [
            float(self.buildingdata_ref["A_Window_" + windows_direction].iloc[0]) * self.window_scaling_factor
            for windows_direction in self.windows_directions
        ]

        self.door_area_in_m2 = self._scaled_element_area_in_m2(self.DOOR_ELEMENT)

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
        u_value, b_factor = self._element_u_value_and_adjustment_factor(self.FLOOR_ELEMENT)
        self.floor_u_value_in_watt_per_m2_per_kelvin = u_value
        self.floor_adjustment_factor_from_tabula = b_factor
        self.heat_conductance_floor_in_watt_per_kelvin = u_value * self.floor_area_in_m2 * b_factor

        u_value, b_factor = self._element_u_value_and_adjustment_factor(self.WALL_ELEMENT)
        self.facade_u_value_in_watt_per_m2_per_kelvin = u_value
        self.facade_adjustment_factor_from_tabula = b_factor
        self.heat_conductance_facade_in_watt_per_kelvin = u_value * self.facade_area_in_m2 * b_factor

        u_value, b_factor = self._element_u_value_and_adjustment_factor(self.ROOF_ELEMENT)
        self.roof_u_value_in_watt_per_m2_per_kelvin = u_value
        self.roof_adjustment_factor_from_tabula = b_factor
        self.heat_conductance_roof_in_watt_per_kelvin = u_value * self.roof_area_in_m2 * b_factor

        u_value, b_factor = self._element_u_value_and_adjustment_factor(self.WINDOW_ELEMENT)
        self.window_u_value_in_watt_per_m2_per_kelvin = u_value
        self.window_adjustment_factor_from_tabula = b_factor
        self.heat_conductance_window_in_watt_per_kelvin = u_value * self.window_area_in_m2 * b_factor

        u_value, b_factor = self._element_u_value_and_adjustment_factor(self.DOOR_ELEMENT)
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
        tabula_reference_area_in_m2 = self._left_associated_float_sum(
            float(self.buildingdata_ref[area_column].values[0]) for area_column in element.area_columns
        )
        return tabula_reference_area_in_m2 * self.scaling_factor_according_to_conditioned_living_area

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
        entries 7 and 17) — so it must not be simplified algebraically; the guard skips
        the division only when the reference area is zero and keeps the raw U-value. The
        adjustment factor is the largest of the ``b_Transmission`` columns when the
        descriptor names any, and the fixed factor otherwise (window and door).
        """
        configured_u_value: Optional[float] = getattr(self.buildingconfig, element.configured_u_value_field)
        if configured_u_value is not None:
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

        if element.transmission_adjustment_columns:
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
        delta_u_thermalbridging_from_tabula = self.buildingdata_ref["delta_U_ThermalBridging"].values[0]
        if delta_u_thermalbridging_from_tabula == 0:
            delta_u_thermalbridging = self.THERMAL_BRIDGING_DELTA_U_WHEN_TABULA_IS_ZERO_IN_WATT_PER_M2_PER_KELVIN
        else:
            delta_u_thermalbridging = float(delta_u_thermalbridging_from_tabula)

        return delta_u_thermalbridging * self.building_total_area_in_m2

    def _ventilation_conductance_in_watt_per_kelvin(self) -> float:
        """Return the ventilation heat-transfer conductance of the building [W/K].

        The conductance is the volumetric heat capacity of air times the total air
        exchange rate (use plus infiltration, from the TABULA row), the room height and
        the scaled conditioned floor area. Requires the area pipeline to have run for the
        scaled conditioned floor area.
        """
        return (
            self.HEAT_CAPACITY_OF_AIR_PER_VOLUME_IN_WATT_HOUR_PER_M3_PER_KELVIN
            * (
                float(self.buildingdata_ref["n_air_use"].values[0])
                + float(self.buildingdata_ref["n_air_infiltration"].values[0])
            )
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
                - self.buildingconfig.heating_reference_temperature_in_celsius
            )
        else:
            self.max_thermal_building_demand_in_watt = self.buildingconfig.max_thermal_building_demand_in_watt

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
        else:
            number_of_apartments_origin = float(buildingdata["n_Apartment"].values[0])
            # if no value given or if the area given in the config is bigger than the tabula ref area
            use_average_apartment_size_fallback = number_of_apartments_origin == 0 or scaling_factor != 1

        if use_average_apartment_size_fallback:
            # check table from the link for the year 2021
            number_of_apartments = conditioned_floor_area_in_m2 / self.AVERAGE_LIVING_AREA_PER_APARTMENT_IN_2021_IN_M2
        elif number_of_apartments_origin > 0:
            number_of_apartments = number_of_apartments_origin
        else:
            raise ValueError("Number of apartments can not be negative.")

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
