"""Derived building parameters from the EPISCOPE/TABULA typology data.

Part of the ``hisim.components.building`` package split (see the package ``__init__``
for the layout and the TABULA reference). Holds ``BuildingInformation``, moved
verbatim from the former single-module ``building.py``.
"""

# clean

from typing import ClassVar, Dict, List, Optional, Tuple

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

    def __init__(
        self,
        config: BuildingConfig,
    ):
        """Initialize the class."""

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
        """Get the constants."""
        self.set_constants()

        # Room Capacitance [J/K] (TABULA: Internal heat capacity) Ref: ISO standard 12.3.1.2
        # labeled as C_m in the paper [1] (** Check header), before c_m
        self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin = (
            self.building_heat_capacity_class_f_c_in_joule_per_m2_per_kelvin[self.building_heat_capacity_class]
            * self.scaled_conditioned_floor_area_in_m2
        )
        # Room Capacitance [Wh/m2K] (TABULA: Internal heat capacity) Ref: ISO standard 12.3.1.2
        self.thermal_capacity_of_building_thermal_mass_in_watthour_per_m2_per_kelvin = (
            self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
            / (3.6e3 * self.scaled_conditioned_floor_area_in_m2)
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

    def set_constants(self):
        """Set important constants."""

        # Heat transfer coefficient between nodes "m" and "s" (12.2.2 E64 P79); labeled as h_ms in paper [2] (*** Check header)
        self.heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin = 9.1
        # Dimensionless ratio between surfaces and the useful surfaces (7.2.2.2 E9 P36); labeled as A_at in paper [2] (*** Check header); before lambda_at
        self.ratio_between_internal_surface_area_and_floor_area = 4.5
        # Heat transfer coefficient between nodes "air" and "s" (7.2.2.2 E9 P35); labeled as h_is in paper [2] (*** Check header)
        self.heat_transfer_coeff_indoor_air_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin = 3.45

        # heat capacity class values in ISO 13790, table 12, p.69/70
        self.building_heat_capacity_class_f_a: Dict[str, float] = {
            "very light": 2.5,
            "light": 2.5,
            "medium": 2.5,
            "heavy": 3.0,
            "very heavy": 3.5,
        }

        self.building_heat_capacity_class_f_c_in_joule_per_m2_per_kelvin: Dict[str, float] = {
            "very light": 8e4,
            "light": 1.1e5,
            "medium": 1.65e5,
            "heavy": 2.6e5,
            "very heavy": 3.7e5,
        }

    def get_building_area_parameters(self):
        """Get the building parameter."""
        # Reference area [m^2] (TABULA: Reference floor area A_C_Ref )Ref: ISO standard 7.2.2.2
        self.conditioned_floor_area_in_m2_tabula_ref = float((self.buildingdata_ref["A_C_Ref"].values[0]))

        (self.scaling_factor_according_to_conditioned_living_area, self.scaled_conditioned_floor_area_in_m2) = (
            self.get_scaling_factor_according_to_conditioned_living_area(
                conditioned_floor_area_in_m2_tabula_ref=self.conditioned_floor_area_in_m2_tabula_ref
            )
        )

        self.set_floor_area_parameter()

        # if self.facade_u_value_in_watt_per_m2_per_kelvin is not None or self.facade_area_in_m2 is not None:
        self.set_wall_area_parameter()

        # if self.roof_u_value_in_watt_per_m2_per_kelvin is not None or self.roof_area_in_m2 is not None:
        self.set_roof_area_parameter()

        # if self.window_u_value_in_watt_per_m2_per_kelvin is not None or self.window_area_in_m2 is not None:
        self.set_window_area_parameter()

        # if self.door_u_value_in_watt_per_m2_per_kelvin is not None or self.door_area_in_m2 is not None:
        self.set_door_area_parameter()

        self.building_total_area_in_m2 = (
            self.window_area_in_m2
            + self.floor_area_in_m2
            + self.door_area_in_m2
            + self.facade_area_in_m2
            + self.roof_area_in_m2
        )

    def get_building_heat_transfer_parameters(self):
        """Get the building heat transfer parameter."""

        self.set_floor_heat_transfer_parameter()

        # if self.facade_u_value_in_watt_per_m2_per_kelvin is not None or self.facade_area_in_m2 is not None:
        self.set_wall_heat_transfer_parameter()

        # if self.roof_u_value_in_watt_per_m2_per_kelvin is not None or self.roof_area_in_m2 is not None:
        self.set_roof_heat_transfer_parameter()

        # if self.window_u_value_in_watt_per_m2_per_kelvin is not None or self.window_area_in_m2 is not None:
        self.set_window_heat_transfer_parameter()

        # if self.door_u_value_in_watt_per_m2_per_kelvin is not None or self.door_area_in_m2 is not None:
        self.set_door_heat_transfer_parameter()

        self.set_thermal_bridging_parameter()

        self.set_ventilation_heat_transfer_parameter()

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

        # scaling conditioned floor area
        if self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None:
            # this is for preventing that the conditioned_floor_area is 0 (some buildings in TABULA have conditioned_floor_area (A_C_Ref) = 0)
            if conditioned_floor_area_in_m2_tabula_ref == 0:
                scaled_conditioned_floor_area_in_m2 = self.buildingconfig.absolute_conditioned_floor_area_in_m2
                factor_of_absolute_floor_area_to_tabula_floor_area = 1.0
            # scaling conditioned floor area
            else:
                factor_of_absolute_floor_area_to_tabula_floor_area = (
                    self.buildingconfig.absolute_conditioned_floor_area_in_m2 / conditioned_floor_area_in_m2_tabula_ref
                )
                scaled_conditioned_floor_area_in_m2 = (
                    conditioned_floor_area_in_m2_tabula_ref * factor_of_absolute_floor_area_to_tabula_floor_area
                )
            scaling_factor_according_to_conditioned_living_area = factor_of_absolute_floor_area_to_tabula_floor_area

        elif self.buildingconfig.total_base_area_in_m2 is not None:
            # this is for preventing that the conditioned_floor_area is 0
            if conditioned_floor_area_in_m2_tabula_ref == 0:
                scaled_conditioned_floor_area_in_m2 = self.buildingconfig.total_base_area_in_m2
                factor_of_total_base_area_to_tabula_floor_area = 1.0
            # scaling conditioned floor area
            else:
                factor_of_total_base_area_to_tabula_floor_area = (
                    self.buildingconfig.total_base_area_in_m2 / conditioned_floor_area_in_m2_tabula_ref
                )
                scaled_conditioned_floor_area_in_m2 = (
                    conditioned_floor_area_in_m2_tabula_ref * factor_of_total_base_area_to_tabula_floor_area
                )
            scaling_factor_according_to_conditioned_living_area = factor_of_total_base_area_to_tabula_floor_area

            # if no value for building size is provided in config, use reference value from Tabula or 500 m^2.
        else:
            if conditioned_floor_area_in_m2_tabula_ref == 0:
                scaled_conditioned_floor_area_in_m2 = 500.0
                log.warning(
                    "There is no reference given for absolute conditioned floor area in m^2, so a default of 500 m^2 is used."
                )
            else:
                scaled_conditioned_floor_area_in_m2 = conditioned_floor_area_in_m2_tabula_ref

            scaling_factor_according_to_conditioned_living_area = 1.0

        return scaling_factor_according_to_conditioned_living_area, scaled_conditioned_floor_area_in_m2

    def set_floor_area_parameter(
        self,
    ):
        """Manipulate building data of roof."""

        if self.buildingconfig.floor_area_in_m2 is None:
            area_floor_1 = float(self.buildingdata_ref["A_Floor_1"].values[0])
            area_floor_2 = float(self.buildingdata_ref["A_Floor_2"].values[0])
            self.floor_area_in_m2 = (
                area_floor_1 + area_floor_2
            ) * self.scaling_factor_according_to_conditioned_living_area
        else:
            self.floor_area_in_m2 = self.buildingconfig.floor_area_in_m2

    def set_wall_area_parameter(self):
        """Manipulate building data of walls."""
        if self.buildingconfig.facade_area_in_m2 is None:
            area_wall_1 = float(self.buildingdata_ref["A_Wall_1"].values[0])
            area_wall_2 = float(self.buildingdata_ref["A_Wall_2"].values[0])
            area_wall_3 = float(self.buildingdata_ref["A_Wall_3"].values[0])
            self.facade_area_in_m2 = (
                area_wall_1 + area_wall_2 + area_wall_3
            ) * self.scaling_factor_according_to_conditioned_living_area

        else:
            self.facade_area_in_m2 = self.buildingconfig.facade_area_in_m2

    def set_roof_area_parameter(
        self,
    ):
        """Manipulate building data of roof."""
        if self.buildingconfig.roof_area_in_m2 is None:
            area_roof_1 = float(self.buildingdata_ref["A_Roof_1"].values[0])
            area_roof_2 = float(self.buildingdata_ref["A_Roof_2"].values[0])
            self.roof_area_in_m2 = (
                area_roof_1 + area_roof_2
            ) * self.scaling_factor_according_to_conditioned_living_area
        else:
            self.roof_area_in_m2 = self.buildingconfig.roof_area_in_m2

    def set_window_area_parameter(
        self,
    ):
        """Manipulate building data of windows."""
        area_window_1_ref = float(self.buildingdata_ref["A_Window_1"].values[0])
        area_window_2_ref = float(self.buildingdata_ref["A_Window_2"].values[0])
        if self.buildingconfig.window_area_in_m2 is None:
            self.window_area_in_m2 = (
                area_window_1_ref + area_window_2_ref
            ) * self.scaling_factor_according_to_conditioned_living_area
        else:
            self.window_area_in_m2 = self.buildingconfig.window_area_in_m2

        self.window_scaling_factor = self.window_area_in_m2 / (area_window_1_ref + area_window_2_ref)

        # scaling window areas over wall area
        self.windows_directions: List[str] = [
            "South",
            "East",
            "North",
            "West",
            "Horizontal",
        ]

        self.scaled_window_areas_in_m2: List[float] = []
        for windows_direction in self.windows_directions:
            window_area_of_direction_in_m2 = float(self.buildingdata_ref["A_Window_" + windows_direction].iloc[0])

            self.scaled_window_areas_in_m2.append(window_area_of_direction_in_m2 * self.window_scaling_factor)

    def set_door_area_parameter(
        self,
    ):
        """Manipulate building data of door."""
        if self.buildingconfig.door_area_in_m2 is None:
            area_door_1 = float(self.buildingdata_ref["A_Door_1"].values[0])
            self.door_area_in_m2 = area_door_1 * self.scaling_factor_according_to_conditioned_living_area
        else:
            self.door_area_in_m2 = self.buildingconfig.door_area_in_m2

    def set_floor_heat_transfer_parameter(
        self,
    ):
        """Manipulate building data of floor."""
        if self.buildingconfig.floor_u_value_in_watt_per_m2_per_kelvin is None:

            floor_u_value_in_watt_per_m2_per_kelvin_1 = float(self.buildingdata_ref["U_Actual_Floor_1"].values[0])
            floor_u_value_in_watt_per_m2_per_kelvin_2 = float(self.buildingdata_ref["U_Actual_Floor_2"].values[0])

            area_floor_1 = float(self.buildingdata_ref["A_Floor_1"].values[0])
            area_floor_2 = float(self.buildingdata_ref["A_Floor_2"].values[0])

            self.floor_u_value_in_watt_per_m2_per_kelvin = (
                (floor_u_value_in_watt_per_m2_per_kelvin_1 * area_floor_1)
                + (floor_u_value_in_watt_per_m2_per_kelvin_2 * area_floor_2)
            ) / (area_floor_1 + area_floor_2)

            b_floor_1 = float(self.buildingdata_ref["b_Transmission_Floor_1"].values[0])
            b_floor_2 = float(self.buildingdata_ref["b_Transmission_Floor_2"].values[0])

            self.floor_adjustment_factor_from_tabula = max([b_floor_1, b_floor_2])

        else:
            self.floor_u_value_in_watt_per_m2_per_kelvin = self.buildingconfig.floor_u_value_in_watt_per_m2_per_kelvin

            self.floor_adjustment_factor_from_tabula = 0.5

        self.heat_conductance_floor_in_watt_per_kelvin = (
            self.floor_u_value_in_watt_per_m2_per_kelvin
            * self.floor_area_in_m2
            * self.floor_adjustment_factor_from_tabula
        )

    def set_wall_heat_transfer_parameter(
        self,
    ):
        """Manipulate building data of wall."""
        if self.buildingconfig.facade_u_value_in_watt_per_m2_per_kelvin is None:

            facade_u_value_in_watt_per_m2_per_kelvin_1 = float(self.buildingdata_ref["U_Actual_Wall_1"].values[0])
            facade_u_value_in_watt_per_m2_per_kelvin_2 = float(self.buildingdata_ref["U_Actual_Wall_2"].values[0])
            facade_u_value_in_watt_per_m2_per_kelvin_3 = float(self.buildingdata_ref["U_Actual_Wall_3"].values[0])

            area_wall_1 = float(self.buildingdata_ref["A_Wall_1"].values[0])
            area_wall_2 = float(self.buildingdata_ref["A_Wall_2"].values[0])
            area_wall_3 = float(self.buildingdata_ref["A_Wall_3"].values[0])

            self.facade_u_value_in_watt_per_m2_per_kelvin = (
                (facade_u_value_in_watt_per_m2_per_kelvin_1 * area_wall_1)
                + (facade_u_value_in_watt_per_m2_per_kelvin_2 * area_wall_2)
                + (facade_u_value_in_watt_per_m2_per_kelvin_3 * area_wall_3)
            ) / (area_wall_1 + area_wall_2 + area_wall_3)

            b_wall_1 = float(self.buildingdata_ref["b_Transmission_Wall_1"].values[0])
            b_wall_2 = float(self.buildingdata_ref["b_Transmission_Wall_2"].values[0])
            b_wall_3 = float(self.buildingdata_ref["b_Transmission_Wall_3"].values[0])

            self.facade_adjustment_factor_from_tabula = max([b_wall_1, b_wall_2, b_wall_3])

        else:
            self.facade_u_value_in_watt_per_m2_per_kelvin = self.buildingconfig.facade_u_value_in_watt_per_m2_per_kelvin

            self.facade_adjustment_factor_from_tabula = 1

        self.heat_conductance_facade_in_watt_per_kelvin = (
            self.facade_u_value_in_watt_per_m2_per_kelvin
            * self.facade_area_in_m2
            * self.facade_adjustment_factor_from_tabula
        )

    def set_roof_heat_transfer_parameter(
        self,
    ):
        """Manipulate building data of heat transfer."""
        if self.buildingconfig.roof_u_value_in_watt_per_m2_per_kelvin is None:
            roof_u_value_in_watt_per_m2_per_kelvin_1 = float(self.buildingdata_ref["U_Actual_Roof_1"].values[0])
            roof_u_value_in_watt_per_m2_per_kelvin_2 = float(self.buildingdata_ref["U_Actual_Roof_2"].values[0])

            area_roof_1 = float(self.buildingdata_ref["A_Roof_1"].values[0])
            area_roof_2 = float(self.buildingdata_ref["A_Roof_2"].values[0])

            self.roof_u_value_in_watt_per_m2_per_kelvin = (
                (roof_u_value_in_watt_per_m2_per_kelvin_1 * area_roof_1)
                + (roof_u_value_in_watt_per_m2_per_kelvin_2 * area_roof_2)
            ) / (area_roof_1 + area_roof_2)

            b_roof_1 = float(self.buildingdata_ref["b_Transmission_Roof_1"].values[0])
            b_roof_2 = float(self.buildingdata_ref["b_Transmission_Roof_2"].values[0])

            self.roof_adjustment_factor_from_tabula = max([b_roof_1, b_roof_2])

        else:
            self.roof_u_value_in_watt_per_m2_per_kelvin = self.buildingconfig.roof_u_value_in_watt_per_m2_per_kelvin

            self.roof_adjustment_factor_from_tabula = 1

        self.heat_conductance_roof_in_watt_per_kelvin = (
            self.roof_u_value_in_watt_per_m2_per_kelvin * self.roof_area_in_m2 * self.roof_adjustment_factor_from_tabula
        )

    def set_window_heat_transfer_parameter(
        self,
    ):
        """Manipulate building data of heat transfer."""
        if self.buildingconfig.window_u_value_in_watt_per_m2_per_kelvin is None:
            window_u_value_in_watt_per_m2_per_kelvin_1 = float(self.buildingdata_ref["U_Actual_Window_1"].values[0])
            window_u_value_in_watt_per_m2_per_kelvin_2 = float(self.buildingdata_ref["U_Actual_Window_2"].values[0])

            area_window_1 = float(self.buildingdata_ref["A_Window_1"].values[0])
            area_window_2 = float(self.buildingdata_ref["A_Window_2"].values[0])

            self.window_u_value_in_watt_per_m2_per_kelvin = (
                (window_u_value_in_watt_per_m2_per_kelvin_1 * area_window_1)
                + (window_u_value_in_watt_per_m2_per_kelvin_2 * area_window_2)
            ) / (area_window_1 + area_window_2)
        else:
            self.window_u_value_in_watt_per_m2_per_kelvin = self.buildingconfig.window_u_value_in_watt_per_m2_per_kelvin

        self.window_adjustment_factor_from_tabula = 1

        self.heat_conductance_window_in_watt_per_kelvin = (
            self.window_u_value_in_watt_per_m2_per_kelvin
            * self.window_area_in_m2
            * self.window_adjustment_factor_from_tabula
        )

    def set_door_heat_transfer_parameter(
        self,
    ):
        """Manipulate building data of heat transfer."""
        if self.buildingconfig.door_u_value_in_watt_per_m2_per_kelvin is None:
            area_door_1 = float(self.buildingdata_ref["A_Door_1"].values[0])
            door_u_value_in_watt_per_m2_per_kelvin = float(self.buildingdata_ref["U_Actual_Door_1"].values[0])

            # With a single door the area-weighted U-value is just the door's own
            # U-value; guard against a zero door area (no door) to avoid dividing by
            # zero, while keeping the exact expression for the non-zero case.
            if area_door_1 != 0:
                self.door_u_value_in_watt_per_m2_per_kelvin = (
                    door_u_value_in_watt_per_m2_per_kelvin * area_door_1
                ) / (area_door_1)
            else:
                self.door_u_value_in_watt_per_m2_per_kelvin = door_u_value_in_watt_per_m2_per_kelvin
        else:
            self.door_u_value_in_watt_per_m2_per_kelvin = self.buildingconfig.door_u_value_in_watt_per_m2_per_kelvin

        self.door_adjustment_factor_from_tabula = 1

        self.heat_conductance_door_in_watt_per_kelvin = (
            self.door_u_value_in_watt_per_m2_per_kelvin * self.door_area_in_m2 * self.door_adjustment_factor_from_tabula
        )

    def set_thermal_bridging_parameter(
        self,
    ):
        """Manipulate building data of heat transfer."""
        # The TABULA row is read-only after lookup: rows with delta_U_ThermalBridging == 0
        # used to be patched to 0.1 in place before being read back (findings log entry 6),
        # so the object's view of the row silently differed from the file. The correction
        # is now an explicit local value; whether the 0.1 W/(m2 K) surcharge is good
        # physics is a design-review question, not changed here.
        delta_u_thermalbridging_from_tabula = self.buildingdata_ref["delta_U_ThermalBridging"].values[0]
        if delta_u_thermalbridging_from_tabula == 0:
            delta_u_thermalbridging = 0.1
        else:
            delta_u_thermalbridging = float(delta_u_thermalbridging_from_tabula)

        self.heat_conductance_thermal_bridging_in_watt_per_kelvin = (
            delta_u_thermalbridging * self.building_total_area_in_m2
        )

    def set_ventilation_heat_transfer_parameter(self):
        """Manipulate building data of heat transfer."""
        heat_capacity_of_air_per_volume_in_watt_hour_per_m3_per_kelvin = 0.34

        self.heat_conductance_ventilation_in_watt_per_kelvin = (
            heat_capacity_of_air_per_volume_in_watt_hour_per_m3_per_kelvin
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
        """

        if self.buildingconfig.number_of_apartments is not None:
            number_of_apartments_origin = self.buildingconfig.number_of_apartments

            if number_of_apartments_origin == 0:
                # check table from the link for the year 2021
                average_living_area_per_apartment_in_2021_in_m2 = 92.1
                number_of_apartments = conditioned_floor_area_in_m2 / average_living_area_per_apartment_in_2021_in_m2
            elif number_of_apartments_origin > 0:
                number_of_apartments = number_of_apartments_origin

            else:
                raise ValueError("Number of apartments can not be negative.")

        elif self.buildingconfig.number_of_apartments is None:
            number_of_apartments_origin = float(buildingdata["n_Apartment"].values[0])

            # if no value given or if the area given in the config is bigger than the tabula ref area
            if number_of_apartments_origin == 0 or scaling_factor != 1:
                # check table from the link for the year 2021
                average_living_area_per_apartment_in_2021_in_m2 = 92.1
                number_of_apartments = conditioned_floor_area_in_m2 / average_living_area_per_apartment_in_2021_in_m2
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
        if self.tabula_ref_heat_transfer_coeff_by_ventilation_ref_in_watt_per_m2_per_kelvin is None:
            raise ValueError("h_Ventilation was none.")
        # Heat transfer coefficient by ventilation in watt per kelvin
        # (the same TABULA column, made absolute with the scaled conditioned floor area)
        self.tabula_ref_heat_transfer_coeff_by_ventilation_reference_in_watt_per_kelvin = (
            float(buildingdata["h_Ventilation"].values[0]) * scaled_conditioned_floor_area_in_m2
        )

        # Heat transfer coefficient by transmission in watt per m2 per kelvin (TABULA column h_Transmission)
        self.tabula_ref_heat_transfer_coeff_by_transmission_ref_in_watt_per_m2_per_kelvin = float(
            buildingdata["h_Transmission"].values[0]
        )
        if self.tabula_ref_heat_transfer_coeff_by_transmission_ref_in_watt_per_m2_per_kelvin is None:
            raise ValueError("h_Transmission was none.")
