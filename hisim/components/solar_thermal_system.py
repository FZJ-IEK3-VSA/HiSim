"""Solar thermal system for DHW."""

from copy import deepcopy
import datetime
import math
from typing import ClassVar, List, Optional, Tuple
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
import numpy as np
import pandas as pd
import pvlib
from hisim.component import (
    CapexCostDataClass,
    Component,
    ComponentConnection,
    ComponentInput,
    ComponentOutput,
    Coordinates,
    OpexCostDataClass,
    SingleTimeStepValues,
)
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    Sizable,
    Size,
    concrete,
    preset,
    sized_field,
)
from hisim import hydronics, loadtypes, log, utils
from hisim.caching import atomic_cache_write
from hisim.energy_port import EnergyPort
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.components.simple_water_storage import SimpleDHWStorage
from hisim.components.weather import Weather
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.economics.facts import CostRelevance


def plane_of_array_irradiance_w_m2(
    surface_tilt: float,
    surface_azimuth: float,
    apparent_zenith: float,
    solar_azimuth: float,
    global_horizontal_irradiance_w_m2: float,
    diffuse_horizontal_irradiance_w_m2: float,
) -> np.float64:
    """Return the irradiance on the collector plane of one instant, as pvlib works it out.

    The same two pvlib calls the collector has always made -- ``pvlib.irradiance.dni`` and
    ``pvlib.irradiance.get_total_irradiance`` with the isotropic sky -- fed one-element numpy arrays
    instead of one-element pandas Series. pvlib applies the same numpy ufuncs to both and pandas
    hands its arithmetic on so small a Series straight to numpy, so the result is bit for bit the
    value the Series path gives, at a fraction of its cost (the Series path spent most of its
    ~7 ms per call building indexes and DataFrames). ``tests/test_solar_thermal_system.py`` pins
    the equality against the Series path.
    """
    zenith = np.array([apparent_zenith], dtype=np.float64)
    ghi = np.array([global_horizontal_irradiance_w_m2], dtype=np.float64)
    dhi = np.array([diffuse_horizontal_irradiance_w_m2], dtype=np.float64)
    # pandas silences numpy's floating point warnings for Series arithmetic; so does this.
    with np.errstate(all="ignore"):
        direct_normal_irradiance = pvlib.irradiance.dni(ghi=ghi, dhi=dhi, zenith=zenith)
        total_irradiation = pvlib.irradiance.get_total_irradiance(
            surface_tilt=surface_tilt,
            surface_azimuth=surface_azimuth,
            solar_zenith=zenith,
            solar_azimuth=np.array([solar_azimuth], dtype=np.float64),
            # What Series.fillna(0) did: a NaN DNI (dark or below the horizon) counts as none.
            dni=np.where(np.isnan(direct_normal_irradiance), 0.0, direct_normal_irradiance),
            ghi=ghi,
            dhi=dhi,
        )
    return np.float64(total_irradiation["poa_global"][0])


def flat_plate_collector_heat_w_m2(
    eta_0: float,
    a_1: float,
    a_2: float,
    temperature_collector_inlet_deg_c: float,
    delta_temperature_n_k: float,
    ambient_air_temperature_deg_c: float,
    collector_irradiance_w_m2: np.float64,
) -> np.float64:
    """Return the heat one square metre of flat plate collector delivers, in W/m².

    ``oemof.thermal.solar_thermal_collector.calc_eta_c_flate_plate`` times the irradiance, for one
    instant, written out in scalars. The operations and their order are oemof's, down to squaring
    the temperature difference as a numpy float64 (which is what oemof's Series element is), so the
    result is bit for bit what the Series call gave; ``tests/test_solar_thermal_system.py`` pins
    that. An efficiency is clipped at zero, and with no irradiance it is zero.
    """
    delta_t = np.float64(temperature_collector_inlet_deg_c + delta_temperature_n_k - ambient_air_temperature_deg_c)
    efficiency: float = 0
    if collector_irradiance_w_m2 > 0:
        eta = eta_0 - a_1 * delta_t / collector_irradiance_w_m2 - a_2 * delta_t**2 / collector_irradiance_w_m2
        if eta > 0:
            efficiency = eta
    return np.float64(efficiency * collector_irradiance_w_m2)


@dataclass(frozen=True)
class FlatPlateCollectorCurve:

    """A flat-plate collector's efficiency curve, the law that turns irradiance into heat at an inlet temperature.

    The efficiency is ``eta_0 - a_1 dT / G - a_2 dT^2 / G`` with ``dT`` the collector's mean temperature (the inlet
    plus the inlet-to-mean difference) above the air and ``G`` the irradiance on its plane; a value object built
    once from the configuration, so the law is a pure function of its arguments.
    """

    #: The optical efficiency, dimensionless.
    eta_0: float
    #: The linear heat loss coefficient, in W/(m² K).
    a_1_in_watt_per_m2_per_kelvin: float
    #: The quadratic heat loss coefficient, in W/(m² K²).
    a_2_in_watt_per_m2_per_kelvin_squared: float
    #: The difference between the collector's inlet and its mean temperature, in K.
    inlet_to_mean_temperature_difference_in_kelvin: float

    def heat_in_watt_per_m2(
        self,
        *,
        inlet_temperature_in_celsius: float,
        ambient_temperature_in_celsius: float,
        irradiance_in_watt_per_m2: float,
    ) -> float:
        """Return the heat one square metre of collector delivers at an inlet temperature, in W/m².

        It is :func:`flat_plate_collector_heat_w_m2`, zero when the efficiency is not positive. For example, at
        500 W/m², a 40 °C inlet and 5 °C outside a typical collector delivers about 215 W/m².

        Args:
            inlet_temperature_in_celsius: The collector's inlet temperature, in °C.
            ambient_temperature_in_celsius: The outside air temperature, in °C.
            irradiance_in_watt_per_m2: The irradiance on the collector plane, in W/m².

        Returns:
            The heat per square metre of collector, in W/m².
        """
        return float(
            flat_plate_collector_heat_w_m2(
                eta_0=self.eta_0,
                a_1=self.a_1_in_watt_per_m2_per_kelvin,
                a_2=self.a_2_in_watt_per_m2_per_kelvin_squared,
                temperature_collector_inlet_deg_c=inlet_temperature_in_celsius,
                delta_temperature_n_k=self.inlet_to_mean_temperature_difference_in_kelvin,
                ambient_air_temperature_deg_c=ambient_temperature_in_celsius,
                collector_irradiance_w_m2=np.float64(irradiance_in_watt_per_m2),
            )
        )


@dataclass_json
@dataclass
class SolarThermalSystemConfig(ConfigBase):
    """Configuration of the solar thermal system: a collector field preheating the hot water storage.

    A collector is described by three things: the efficiency curve it converts irradiance with
    (``eta_0`` and the two thermal loss parameters), the plane it sits in (``tilt``, ``azimuth``)
    together with the place that plane stands in (``coordinates``, which fix the sun's path over
    it), and how much of it there is (``area_m2``). Only the last is sized against the building:
    the curve and the plane describe the product an installer picks, the area describes how much
    of that product the dwellings need.

    The named default is :meth:`preset_flat_plate`::

        SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem").resolve(context)

    The preset leaves ``area_m2`` at ``AUTO``, so the configuration it returns has to be resolved
    against a context carrying ``number_of_apartments`` before a component can be built from it.
    """

    MAIN_CLASS = "hisim.components.solar_thermal_system.SolarThermalSystem"

    #: How much collector a solar thermal system gets per apartment it serves. The whole of the
    #: collector sizing law: a building with three flats gets three times the collector of a
    #: single-family house, the law assuming that hot water demand scales with the number of
    #: dwellings.
    COLLECTOR_AREA_IN_M2_PER_APARTMENT: ClassVar[float] = 4.0

    component_id: ComponentID

    #: Where the collector stands, which together with the instant is the whole of the sun's
    #: position over it. Aachen.
    coordinates: Coordinates = field(
        default_factory=lambda: Coordinates(latitude_in_degrees=50.78, longitude_in_degrees=6.08)
    )

    #: Compass direction the collector plane faces, in degrees clockwise from north; 180 is south.
    azimuth: float = 180.0
    #: Inclination of the collector plane against the horizontal, in degrees.
    tilt: float = 30.0

    # The three numbers of the efficiency curve are taken from the Excel sheet that can be
    # downloaded from
    # http://www.estif.org/solarkeymarknew/the-solar-keymark-scheme-rules/21-certification-bodies/certified-products/58-collector-performance-parameters
    # Values were determined by changing eta_0, a_1 and a_2 so that the curve fits with the
    # typical flat plate curve.
    #: Optical efficiency: the share of the irradiance on the plane that reaches the fluid while
    #: collector and ambient air are equally warm.
    eta_0: float = 0.78
    #: First-order thermal loss coefficient, in W/(m2*K).
    a_1_w_m2_k: float = 3.2
    #: Second-order thermal loss coefficient, in W/(m2*K2).
    a_2_w_m2_k: float = 0.015

    #: Whether an old solar pump is installed rather than a new one. An old one draws 35 W while
    #: it runs, a new one 10 W.
    old_solar_pump: bool = False

    #: CO2 footprint of investment in kg. Left None, so that the capex computation derives it
    #: from the collector area.
    device_co2_footprint_in_kg: Optional[float] = None
    #: Cost for investment in Euro. Left None, as the footprint above.
    investment_costs_in_euro: Optional[float] = None
    #: Lifetime in years. Left None, as the footprint above.
    lifetime_in_years: Optional[float] = None
    #: Maintenance cost in Euro per year. Left None, as the footprint above.
    maintenance_costs_in_euro_per_year: Optional[float] = None
    #: Subsidies as a percentage of the investment costs. Left None, as the footprint above.
    subsidy_as_percentage_of_investment_costs: Optional[float] = None

    #: Weight of the component, which defines its place in the control hierarchy.
    source_weight: int = 1

    #: Collector area in m2, sized to the building it serves.
    area_m2: Sizable[float] = sized_field(
        rule=Size.NUMBER_OF_APARTMENTS * COLLECTOR_AREA_IN_M2_PER_APARTMENT,
        value_type=float,
        unit=loadtypes.Units.SQUARE_METER,
        note=f"{COLLECTOR_AREA_IN_M2_PER_APARTMENT} m2 of collector per apartment",
    )

    #: Temperature difference between the collector inlet and the collector's mean temperature, in K.
    delta_temperature_n_k: float = 10

    def __post_init__(self) -> None:
        """Refuse an inlet-to-mean temperature difference that is not a finite positive number of kelvin.

        The collector lifts its water by twice this difference, and its flow is the collector heat over that lift.
        A difference of 0 K divides by zero, and a negative one gives a negative flow and a supply below the inlet.
        For example, ``delta_temperature_n_k=10`` is accepted, ``0`` and ``-5`` are refused.

        Raises:
            ValueError: If ``delta_temperature_n_k`` is not finite or not above 0 K.
        """
        if not math.isfinite(self.delta_temperature_n_k) or self.delta_temperature_n_k <= 0:
            raise ValueError(
                f"The collector's inlet-to-mean temperature difference delta_temperature_n_k must be a finite number "
                f"above 0 K, got {self.delta_temperature_n_k!r}: the collector lifts its water by twice this difference "
                "and its flow is the collector heat over that lift."
            )

    @preset
    @classmethod
    def preset_flat_plate(cls, name: str) -> "SolarThermalSystemConfig":
        """A Solar Keymark flat-plate collector over Aachen, facing south at 30 degrees.

        The field defaults are the whole installation: the efficiency curve fitted to the typical
        flat-plate curve, a south-facing plane inclined 30 degrees, the Aachen coordinates and a
        new solar pump. The collector area is the one thing the preset does not state, because it
        follows from the building: it stays ``AUTO`` and ``.resolve(context)`` turns it into four
        square metres per dwelling.

        Example::

            config = SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem").resolve(
                SizingContext(number_of_apartments=3)
            )

        The preset is named after the collector technology rather than ``standard`` because a flat
        plate is one collector kind among several: an evacuated-tube collector is the same class
        with a different curve, and it would want a name of its own.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            SolarThermalSystemConfig: The preset configuration, its area still to be resolved.
        """
        return cls(component_id=ComponentID(name=name))


class SolarThermalSystem(Component):
    """Solar thermal system.

    This class represents a solar thermal system that can be used
    for warm water and space heating.
    """

    cost_relevance = CostRelevance.PRICED

    # Inputs
    TemperatureOutsideDegC: ClassVar[str] = "TemperatureOutsideDegC"
    DiffuseHorizontalIrradianceWM2: ClassVar[str] = "DiffuseHorizontalIrradianceWM2"
    GlobalHorizontalIrradianceWM2: ClassVar[str] = "GlobalHorizontalIrradianceWM2"
    Azimuth: ClassVar[str] = "Azimuth"
    ApparentZenith: ClassVar[str] = "ApparentZenith"
    TemperatureCollectorInletDegC: ClassVar[str] = "TemperatureCollectorInletDegC"
    ControlSignal: ClassVar[str] = "ControlSignal"

    # Outputs
    ThermalPowerOutput: ClassVar[str] = "ThermalPowerOutput"
    ThermalEnergyOutput: ClassVar[str] = "ThermalEnergyOutput"
    RequiredWaterMassFlowOutput: ClassVar[str] = "RequiredWaterMassFlowOutput"
    WaterMassFlowOutput: ClassVar[str] = "WaterMassFlowOutput"
    WaterTemperatureOutput: ClassVar[str] = "WaterTemperatureOutput"
    ElectricityConsumptionOutput: ClassVar[str] = "ElectricityConsumptionOutput"
    #: The irradiance on the collector plane times the collector area: the solar power the collectors receive (W).
    SolarPowerOnCollector: ClassVar[str] = "SolarPowerOnCollector"
    #: The solar power the collectors receive and do not deliver as heat: optical and thermal loss, and all of it
    #: while the pump stands (W).
    CollectorHeatLoss: ClassVar[str] = "CollectorHeatLoss"
    #: The solar pump's electricity, which the model does not add to the fluid: it leaves to outdoors (W).
    SolarPumpHeatLoss: ClassVar[str] = "SolarPumpHeatLoss"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SolarThermalSystemConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.componentnameconfig: SolarThermalSystemConfig = config
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: SolarThermalSystemConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # If a component requires states, this can be implemented here.
        self.state: SolarThermalSystemState = SolarThermalSystemState()
        self.previous_state: SolarThermalSystemState = deepcopy(self.state)
        # Initialized variables
        self.factor: float = 1.0
        #: The collector area, read once here rather than at every timestep. ``super().__init__``
        #: has just refused any config still carrying AUTO, so this read is what turns the
        #: sizable field into the plain float the physics multiplies by.
        self.area_m2: float = concrete(config.area_m2)
        self.collector_curve = FlatPlateCollectorCurve(
            eta_0=config.eta_0,
            a_1_in_watt_per_m2_per_kelvin=config.a_1_w_m2_k,
            a_2_in_watt_per_m2_per_kelvin_squared=config.a_2_w_m2_k,
            inlet_to_mean_temperature_difference_in_kelvin=config.delta_temperature_n_k,
        )
        # Where the sun will be at every timestep, filled in i_prepare_simulation from the cache or
        # from pvlib. Nothing downstream of the sun is stored: see i_prepare_simulation.
        self.solar_position: pd.DataFrame = pd.DataFrame()
        # The same two columns as plain arrays, for the per-timestep lookup in i_simulate.
        self.apparent_zenith: np.ndarray = np.empty(0)
        self.solar_azimuth: np.ndarray = np.empty(0)
        # The plane-of-array irradiance of the timestep last calculated, keyed by the timestep and
        # the weather it came from. It depends on the weather and the sun alone, not on the storage,
        # so the convergence iterations of one timestep share one pvlib call.
        self.plane_of_array_key: Optional[Tuple[int, float, float]] = None
        self.plane_of_array_irradiance_w_m2: np.float64 = np.float64(0)
        self.cache_filepath: Optional[str] = None

        # Add inputs
        self.t_out_channel: ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutsideDegC,
            loadtypes.LoadTypes.TEMPERATURE,
            loadtypes.Units.CELSIUS,
            True,
        )

        self.dhi_channel: ComponentInput = self.add_input(
            self.component_name,
            self.DiffuseHorizontalIrradianceWM2,
            loadtypes.LoadTypes.IRRADIANCE,
            loadtypes.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.ghi_channel: ComponentInput = self.add_input(
            self.component_name,
            self.GlobalHorizontalIrradianceWM2,
            loadtypes.LoadTypes.IRRADIANCE,
            loadtypes.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.azimuth_channel: ComponentInput = self.add_input(
            self.component_name,
            self.Azimuth,
            loadtypes.LoadTypes.ANY,
            loadtypes.Units.DEGREES,
            True,
        )

        self.apparent_zenith_channel: ComponentInput = self.add_input(
            self.component_name,
            self.ApparentZenith,
            loadtypes.LoadTypes.ANY,
            loadtypes.Units.DEGREES,
            True,
        )

        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureCollectorInletDegC,
            loadtypes.LoadTypes.TEMPERATURE,
            loadtypes.Units.CELSIUS,
            True,
        )

        self.control_signal_channel: ComponentInput = self.add_input(
            self.component_name,
            SolarThermalSystem.ControlSignal,
            loadtypes.LoadTypes.ANY,
            loadtypes.Units.BINARY,
            True,
        )

        # Add outputs
        self.thermal_power_w_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerOutput,
            load_type=loadtypes.LoadTypes.HEATING,
            unit=loadtypes.Units.WATT,
            postprocessing_flag=[loadtypes.InandOutputType.WATER_HEATING],
            output_description="Thermal power output [W]",
            energy_port=EnergyPort(
                loadtypes.EnergyRole.OUT,
                loadtypes.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT,
                peer_output=self.WaterMassFlowOutput,
            ),
        )

        self.thermal_energy_wh_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalEnergyOutput,
            load_type=loadtypes.LoadTypes.HEATING,
            unit=loadtypes.Units.WATT_HOUR,
            output_description="Thermal energy output [Wh]",
            postprocessing_flag=[loadtypes.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.water_mass_flow_kg_s_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.WaterMassFlowOutput,
            load_type=loadtypes.LoadTypes.WARM_WATER,
            unit=loadtypes.Units.KG_PER_SEC,
            output_description="Mass flow of heat transfer liquid [kg/s]",
        )
        self.required_water_mass_flow_kg_s_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.RequiredWaterMassFlowOutput,
            load_type=loadtypes.LoadTypes.WARM_WATER,
            unit=loadtypes.Units.KG_PER_SEC,
            output_description=(
                "The mass flow [kg/s] the collector heat at the storage's step mean needs: that heat over c times twice "
                "the inlet-to-mean difference, whether or not the pump runs. The controller stops the pump below its "
                "minimum flow."
            ),
        )

        self.water_temperature_deg_c_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.WaterTemperatureOutput,
            load_type=loadtypes.LoadTypes.TEMPERATURE,
            unit=loadtypes.Units.CELSIUS,
            output_description=(
                "Supply temperature [°C] of the collector's circuit: the storage's step mean plus twice the "
                "inlet-to-mean difference while the pump runs with heat, the return otherwise."
            ),
        )
        self.electricity_consumption_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityConsumptionOutput,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
            output_description="Electricity consumption of the solar pump.",
            postprocessing_flag=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            energy_port=EnergyPort(
                loadtypes.EnergyRole.IN,
                loadtypes.EnergyBalanceCarrier.ELECTRICITY,
                peer_output=self.ElectricityConsumptionOutput,
            ),
        )
        self.solar_power_on_collector_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.SolarPowerOnCollector,
            load_type=loadtypes.LoadTypes.IRRADIANCE,
            unit=loadtypes.Units.WATT,
            output_description="Irradiance on the collector plane (poa_global) times the collector area [W].",
            energy_port=EnergyPort(loadtypes.EnergyRole.IN, loadtypes.EnergyBalanceCarrier.SOLAR),
        )
        self.collector_heat_loss_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CollectorHeatLoss,
            load_type=loadtypes.LoadTypes.HEATING,
            unit=loadtypes.Units.WATT,
            output_description=(
                "Solar power on the collectors that is not delivered as heat [W]: the optical and thermal loss of "
                "the efficiency curve, and all of it while the pump stands."
            ),
            energy_port=EnergyPort(loadtypes.EnergyRole.LOSS, loadtypes.EnergyBalanceCarrier.SOLAR),
        )
        self.solar_pump_heat_loss_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.SolarPumpHeatLoss,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
            output_description="Electricity of the solar pump, which the model does not add to the fluid [W].",
            energy_port=EnergyPort(loadtypes.EnergyRole.LOSS, loadtypes.EnergyBalanceCarrier.ELECTRICITY),
        )

        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_controller())

    @staticmethod
    def get_cost_capex(
        config: SolarThermalSystemConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        component_type = loadtypes.ComponentType.SOLAR_THERMAL_SYSTEM
        kpi_tag = KpiTagEnumClass.SOLAR_THERMAL
        unit = loadtypes.Units.SQUARE_METER
        size_of_energy_system = concrete(config.area_m2)

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=component_type,
            unit=unit,
            size_of_energy_system=size_of_energy_system,
            config=config,
            kpi_tag=kpi_tag,
        )
        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(config=config, capex_cost_data_class=capex_cost_data_class)

        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List[ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX."""
        electricity_consumption_in_kilowatt_hour: Optional[float] = None
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.field_name == self.ElectricityConsumptionOutput
                and output.unit == loadtypes.Units.WATT
            ):
                electricity_consumption_in_kilowatt_hour = round(
                    postprocessing_results.iloc[:, index].sum()
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
                break

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
        )
        assert electricity_consumption_in_kilowatt_hour is not None

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=electricity_consumption_in_kilowatt_hour
            * emissions_and_cost_factors.electricity_costs_in_euro_per_kwh,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=electricity_consumption_in_kilowatt_hour
            * emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh,
            total_consumption_in_kwh=electricity_consumption_in_kilowatt_hour,
            consumption_for_domestic_hot_water_in_kwh=electricity_consumption_in_kilowatt_hour,
            loadtype=loadtypes.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.SOLAR_THERMAL,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List[ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []
        opex_dataclass = self.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        capex_dataclass = self.get_cost_capex(self.config, self.my_simulation_parameters)
        dhw_thermal_energy_delivered_in_kilowatt_hour = None
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.ThermalEnergyOutput and output.unit == loadtypes.Units.WATT_HOUR:
                    dhw_thermal_energy_delivered_in_kilowatt_hour = round(
                        postprocessing_results.iloc[:, index].sum() * 1e-3, 1
                    )

        assert dhw_thermal_energy_delivered_in_kilowatt_hour is not None
        total_thermal_energy_delivered_in_kilowatt_hour = dhw_thermal_energy_delivered_in_kilowatt_hour
        thermal_energy_delivered_entry = KpiEntry(
            name="Total thermal energy delivered",
            unit="kWh",
            value=total_thermal_energy_delivered_in_kilowatt_hour,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(thermal_energy_delivered_entry)

        dhw_thermal_energy_delivered_entry = KpiEntry(
            name="Thermal energy delivered for domestic hot water",
            unit="kWh",
            value=dhw_thermal_energy_delivered_in_kilowatt_hour,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_thermal_energy_delivered_entry)

        energy_consumption = KpiEntry(
            name="Total consumption (energy)",
            unit="kWh",
            value=opex_dataclass.total_consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(energy_consumption)

        dhw_energy_consumption = KpiEntry(
            name="Energy consumption for doemstic hot water",
            unit="kWh",
            value=opex_dataclass.consumption_for_domestic_hot_water_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_energy_consumption)

        # Economic and environmental KPIs
        capex = KpiEntry(
            name="CAPEX - Investment cost",
            unit="EUR",
            value=capex_dataclass.capex_investment_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(capex)

        co2_footprint_capex = KpiEntry(
            name="CAPEX - CO2 Footprint",
            unit="kg",
            value=capex_dataclass.device_co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint_capex)

        opex = KpiEntry(
            name="OPEX - Fuel costs",
            unit="EUR",
            value=opex_dataclass.opex_energy_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(opex)

        maintenance_costs = KpiEntry(
            name="OPEX - Maintenance costs",
            unit="EUR",
            value=opex_dataclass.opex_maintenance_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(maintenance_costs)

        co2_footprint = KpiEntry(
            name="OPEX - CO2 Footprint",
            unit="kg",
            value=opex_dataclass.co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint)

        total_costs = KpiEntry(
            name="Total Costs (CAPEX for simulated period + OPEX fuel and maintenance)",
            unit="EUR",
            value=capex_dataclass.capex_investment_cost_for_simulated_period_in_euro
            + opex_dataclass.opex_energy_cost_in_euro
            + opex_dataclass.opex_maintenance_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_costs)

        total_co2_footprint = KpiEntry(
            name="Total CO2 Footprint (CAPEX for simulated period + OPEX)",
            unit="kg",
            value=capex_dataclass.device_co2_footprint_for_simulated_period_in_kg + opex_dataclass.co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_co2_footprint)
        return list_of_kpi_entries

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ) -> List[ComponentConnection]:
        """Get simple_water_storage default connections."""

        connections: List[ComponentConnection] = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystem.TemperatureCollectorInletDegC,
                storage_classname,
                SimpleDHWStorage.StepMeanWaterTemperatureToHeatGeneratorInCelsius,
            )
        )
        return connections

    def get_default_connections_from_weather(self) -> List[ComponentConnection]:
        """Get default connections from weather."""

        connections: List[ComponentConnection] = []
        weather_classname = Weather.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystem.TemperatureOutsideDegC,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystem.GlobalHorizontalIrradianceWM2,
                weather_classname,
                Weather.GlobalHorizontalIrradiance,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystem.DiffuseHorizontalIrradianceWM2,
                weather_classname,
                Weather.DiffuseHorizontalIrradiance,
            )
        )
        connections.append(ComponentConnection(SolarThermalSystem.Azimuth, weather_classname, Weather.Azimuth))
        connections.append(
            ComponentConnection(
                SolarThermalSystem.ApparentZenith,
                weather_classname,
                Weather.ApparentZenith,
            )
        )
        return connections

    def get_default_connections_from_controller(
        self,
    ) -> List[ComponentConnection]:
        """Get Controller default connections."""
        component_class = SolarThermalSystemController
        connections: List[ComponentConnection] = []
        l1_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystem.ControlSignal,
                l1_controller_classname,
                component_class.ControlSignalToSolarThermalSystem,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Saves the current state."""
        self.previous_state = deepcopy(self.state)

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def timestamps_of_the_run(self) -> pd.DatetimeIndex:
        """Return the instant of every timestep of this simulation.

        The sun's position is a function of these and of the coordinates, and of nothing else, which
        is what makes it the only part of the collector calculation that can be worked out before
        the run and cached. They come from ``start_date`` and ``seconds_per_timestep``, both of which
        are already in the cache key.

        Returns:
            pd.DatetimeIndex: one instant per timestep, in simulation order.
        """
        return pd.DatetimeIndex(
            [
                self.my_simulation_parameters.start_date
                + datetime.timedelta(0, self.my_simulation_parameters.seconds_per_timestep * timestep)
                for timestep in range(self.my_simulation_parameters.timesteps)
            ]
        )

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation by working out where the sun will be.

        Only the sun's position is precomputed, and only it is cached. The collector calculation has
        three stages and just the first qualifies: the solar position depends on the timestamps and
        the coordinates, both already in the cache key; the plane-of-array irradiance depends on the
        weather, which arrives through wired inputs; and the collector efficiency depends on the
        storage's inlet temperature, which is the simulation's own state feeding back.

        This component used to cache the output of all three. That value could not be keyed by
        anything -- it was a function of a trajectory the run had not taken yet -- so any hit
        replayed one system's storage behaviour into another's. Restricting what is cached is what
        makes the existing key exactly correct, with nothing to widen. See roadmap/pylpg_flakiness.md
        F8.
        """
        file_exists, self.cache_filepath = utils.get_cache_file(
            self.config.component_id.name, self.config, self.my_simulation_parameters
        )
        timestamps = self.timestamps_of_the_run()

        if file_exists:
            log.information("Get solar position from cache.")
            # float_precision="round_trip" is what makes a cached run and an uncached one the same
            # run. pandas' default CSV reader uses a fast, inexact float parser, and it loses the
            # last bit of roughly a sixth of the values here whatever precision they were written
            # with -- measured: 317 of 2000 with the default write format, 358 with "%.17g", 542
            # with "%.20g", and zero with this argument. The loss is in the reader, not in the
            # digits on disk, which is worth knowing because the obvious remedies make it worse.
            cached = pd.read_csv(self.cache_filepath, sep=",", decimal=".", float_precision="round_trip")
            # No length check here on purpose. The old one accepted any file with the right number
            # of rows, which is exactly how a cache entry belonging to a different system passed
            # unnoticed. With a key that genuinely determines the contents, a mismatched length
            # means the file is corrupt, so it is left to raise rather than warned about.
            self.solar_position = cached.set_index(timestamps)
        else:
            self.solar_position = pvlib.solarposition.get_solarposition(
                time=timestamps,
                latitude=self.config.coordinates.latitude_in_degrees,
                longitude=self.config.coordinates.longitude_in_degrees,
            )[["apparent_zenith", "azimuth"]]

            assert self.cache_filepath is not None
            with atomic_cache_write(
                self.cache_filepath, utils.build_cache_key_string(self.config, self.my_simulation_parameters)
            ) as temporary_cache_filepath:
                # The default float format already writes a shortest round-trip representation;
                # forcing more digits does not help, because what loses precision is the read. See
                # the note beside the read above.
                self.solar_position.to_csv(temporary_cache_filepath, sep=",", decimal=".", index=False)
        self.apparent_zenith = self.solar_position["apparent_zenith"].to_numpy(dtype=np.float64)
        self.solar_azimuth = self.solar_position["azimuth"].to_numpy(dtype=np.float64)
        self.plane_of_array_key = None

    #: The electricity of the solar pump while it runs, in W, for a current high-efficiency circulator.
    PUMP_POWER_IN_WATT: ClassVar[float] = 10.0
    #: The electricity of an old, uncontrolled solar pump while it runs, in W.
    OLD_PUMP_POWER_IN_WATT: ClassVar[float] = 35.0

    @staticmethod
    def required_mass_flow_in_kg_per_second(
        *, collector_heat_in_watt: float, inlet_to_mean_temperature_difference_in_kelvin: float
    ) -> float:
        """Return the flow that carries the collector heat over the collector's lift, in kg/s; 0 without heat.

        The collector lifts the water by twice its inlet-to-mean difference, the difference its efficiency curve is
        evaluated at, so the flow is ``Q / (c 2 dT_n)``. For example, 836 W over 20 K need 0.01 kg/s.

        Args:
            collector_heat_in_watt: The collector heat at the storage's step mean, in W.
            inlet_to_mean_temperature_difference_in_kelvin: The collector's inlet-to-mean difference, in K.

        Returns:
            The mass flow, in kg/s.
        """
        lift_in_kelvin = 2 * inlet_to_mean_temperature_difference_in_kelvin
        return max(collector_heat_in_watt, 0.0) / (hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * lift_in_kelvin)

    @staticmethod
    def pump_power_in_watt(*, pump_runs: bool, is_old_pump: bool) -> float:
        """Return the electricity the solar pump draws in a step, from whether it runs and its kind, in W.

        A running pump draws ``PUMP_POWER_IN_WATT`` (10 W) if it is a current high-efficiency circulator and
        ``OLD_PUMP_POWER_IN_WATT`` (35 W) if it is an old, uncontrolled one; a pump that stands draws nothing.

        Args:
            pump_runs: Whether the controller runs the solar pump in this step.
            is_old_pump: Whether the installed pump is an old one rather than a current one.

        Returns:
            The pump's electric power, in W.
        """
        if not pump_runs:
            return 0.0
        if is_old_pump:
            return SolarThermalSystem.OLD_PUMP_POWER_IN_WATT
        return SolarThermalSystem.PUMP_POWER_IN_WATT

    @staticmethod
    def collector_circuit(
        *,
        collector_heat_in_watt: float,
        inlet_temperature_in_celsius: float,
        inlet_to_mean_temperature_difference_in_kelvin: float,
        pump_runs: bool,
    ) -> hydronics.CircuitStep:
        """Return the collector circuit of one step: its flow, its supply and the heat its water carries.

        While the pump runs and the collector has heat, the circuit carries that heat at the flow
        :meth:`required_mass_flow_in_kg_per_second` and supplies the inlet plus twice the inlet-to-mean difference,
        so its water carries exactly the collector heat. Otherwise it moves no water. For example, 836 W at a 40 °C
        inlet with a 10 K inlet-to-mean difference supply 60 °C at 0.01 kg/s.

        Args:
            collector_heat_in_watt: The collector heat at the storage's step mean, in W.
            inlet_temperature_in_celsius: The collector's inlet temperature, the storage's step mean, in °C.
            inlet_to_mean_temperature_difference_in_kelvin: The collector's inlet-to-mean difference, in K.
            pump_runs: Whether the controller runs the solar pump.

        Returns:
            The circuit's mass flow, supply temperature and heat.
        """
        if not pump_runs or collector_heat_in_watt <= 0:
            return hydronics.CircuitStep.idle(t_return_c=inlet_temperature_in_celsius)
        mass_flow_in_kg_per_second = SolarThermalSystem.required_mass_flow_in_kg_per_second(
            collector_heat_in_watt=collector_heat_in_watt,
            inlet_to_mean_temperature_difference_in_kelvin=inlet_to_mean_temperature_difference_in_kelvin,
        )
        supply_temperature_in_celsius = inlet_temperature_in_celsius + 2 * inlet_to_mean_temperature_difference_in_kelvin
        return hydronics.CircuitStep(
            mass_flow_kg_per_s=mass_flow_in_kg_per_second,
            t_supply_c=supply_temperature_in_celsius,
            power_w=hydronics.circuit_power_w(
                mass_flow_kg_per_s=mass_flow_in_kg_per_second,
                t_supply_c=supply_temperature_in_celsius,
                t_return_c=inlet_temperature_in_celsius,
            ),
        )

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Run the collector at the storage's step mean and publish its circuit, its heat and the pump's electricity.

        The irradiance on the collector plane is computed once per step from the weather and the sun's position;
        the collector heat at the step mean, which moves while the step iterates, on every call.
        """
        # get inputs
        control_signal = stsv.get_input_value(self.control_signal_channel)
        global_horizontal_irradiance_w_m2 = stsv.get_input_value(self.ghi_channel)
        diffuse_horizontal_irradiance_w_m2 = stsv.get_input_value(self.dhi_channel)
        ambient_air_temperature_deg_c = stsv.get_input_value(self.t_out_channel)
        temperature_collector_inlet_deg_c = stsv.get_input_value(self.water_temperature_input_channel)
        # The collector is calculated every timestep, from this timestep's weather and this
        # timestep's storage temperature. Only the sun's position comes from the precomputation, and
        # the two stages below are the body of oemof.thermal's flat_plate_precalc with its first
        # stage lifted out -- see i_prepare_simulation for why that stage and no other.
        # Some more info on the equation:
        # http://www.estif.org/solarkeymarknew/the-solar-keymark-scheme-rules/21-certification-bodies/certified-products/58-collector-performance-parameters #noqa
        # The irradiance on the collector plane needs the weather and the sun only, so it is worked
        # out once per timestep and shared by the timestep's convergence iterations.
        plane_of_array_key = (timestep, global_horizontal_irradiance_w_m2, diffuse_horizontal_irradiance_w_m2)
        if plane_of_array_key != self.plane_of_array_key:
            self.plane_of_array_irradiance_w_m2 = plane_of_array_irradiance_w_m2(
                surface_tilt=self.config.tilt,
                surface_azimuth=self.config.azimuth,
                apparent_zenith=self.apparent_zenith[timestep],
                solar_azimuth=self.solar_azimuth[timestep],
                global_horizontal_irradiance_w_m2=global_horizontal_irradiance_w_m2,
                diffuse_horizontal_irradiance_w_m2=diffuse_horizontal_irradiance_w_m2,
            )
            self.plane_of_array_key = plane_of_array_key
        # the efficiency depends on the collector's inlet temperature, the storage's step mean, which moves while the
        # step iterates, so it is calculated every call
        collector_heat_at_return_w = (
            self.collector_curve.heat_in_watt_per_m2(
                inlet_temperature_in_celsius=temperature_collector_inlet_deg_c,
                ambient_temperature_in_celsius=ambient_air_temperature_deg_c,
                irradiance_in_watt_per_m2=float(self.plane_of_array_irradiance_w_m2),
            )
            * self.area_m2
        )
        circuit = self.collector_circuit(
            collector_heat_in_watt=collector_heat_at_return_w,
            inlet_temperature_in_celsius=temperature_collector_inlet_deg_c,
            inlet_to_mean_temperature_difference_in_kelvin=self.config.delta_temperature_n_k,
            pump_runs=control_signal != 0,
        )
        required_mass_flow_output_kg_s = self.required_mass_flow_in_kg_per_second(
            collector_heat_in_watt=collector_heat_at_return_w,
            inlet_to_mean_temperature_difference_in_kelvin=self.config.delta_temperature_n_k,
        )
        thermal_power_output_w = circuit.power_w
        thermal_energy_output_wh: float = (
            thermal_power_output_w
            * self.my_simulation_parameters.seconds_per_timestep
            / hydronics.UnitConversion.JOULES_PER_WATT_HOUR
        )
        electric_power_demand_solar_pump_w = self.pump_power_in_watt(
            pump_runs=control_signal != 0, is_old_pump=self.config.old_solar_pump
        )
        water_temperature_output_deg_c = circuit.t_supply_c
        mass_flow_output_kg_s = circuit.mass_flow_kg_per_s

        stsv.set_output_value(self.thermal_power_w_output_channel, thermal_power_output_w)
        stsv.set_output_value(self.thermal_energy_wh_output_channel, thermal_energy_output_wh)
        stsv.set_output_value(
            self.water_temperature_deg_c_output_channel,
            water_temperature_output_deg_c,
        )
        stsv.set_output_value(self.water_mass_flow_kg_s_output_channel, mass_flow_output_kg_s)
        stsv.set_output_value(
            self.required_water_mass_flow_kg_s_output_channel,
            required_mass_flow_output_kg_s,
        )
        stsv.set_output_value(
            self.electricity_consumption_output_channel,
            electric_power_demand_solar_pump_w,
        )
        solar_power_on_collector_w = float(self.plane_of_array_irradiance_w_m2) * self.area_m2
        stsv.set_output_value(self.solar_power_on_collector_channel, solar_power_on_collector_w)
        stsv.set_output_value(self.collector_heat_loss_channel, solar_power_on_collector_w - thermal_power_output_w)
        stsv.set_output_value(self.solar_pump_heat_loss_channel, electric_power_demand_solar_pump_w)


@dataclass
class SolarThermalSystemState:
    """The data class saves the state of the simulation results.

    Parameters
    ----------
    output_with_state : int
        Stores the state of the output_with_state value from
        :py:class:`~hisim.component.ComponentName`.

    """

    output_with_state: float = 0


@dataclass_json
@dataclass
class SolarThermalSystemControllerConfig(ConfigBase):
    """Configuration of the solar thermal system's controller: when the solar pump runs.

    The controller runs the pump while the collector has heat enough for its minimum flow and stops it once the
    vessel has reached its 60 °C aim. Both rules are the controller's own, so the configuration holds only the
    controller's identity. The named default is :meth:`preset_standard`::

        SolarThermalSystemControllerConfig.preset_standard("SolarThermalSystemController")
    """

    MAIN_CLASS = "hisim.components.solar_thermal_system.SolarThermalSystemController"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "SolarThermalSystemControllerConfig":
        """The one solar pump controller the fleet runs.

        The preset is called ``standard`` because the controller has no parameter that describes a device or a
        standard; there is nothing else to name it after.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, fully concrete — the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


class SolarThermalSystemController(Component):
    """Solar Controller.

    It takes data from other components and sends signal to the
    solar pump (implicitly integrated in the SolarThermalSystem)
    for activation or deactivation.

    Parameters
    ----------
    Components to connect to:
    (1) SolarThermalSystem (control_signal)

    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    #: The storage's start-of-step temperature, on which the pump stops once the storage is full.
    StorageTemperatureAtStartOfStepInCelsius: ClassVar[str] = "StorageTemperatureAtStartOfStepInCelsius"
    #: The flow the collector heat at the storage's step mean needs, on which the pump stops below its minimum.
    MassFlow: ClassVar[str] = "MassFlow"

    # Outputs
    ControlSignalToSolarThermalSystem: ClassVar[str] = "ControlSignalToSolarThermalSystem"

    #: Below this pump flow at the storage's step mean the controller stops the pump. The flow is sized for
    #: twice the collector's inlet-to-mean difference (20 K), so 0.005 kg/s is the collector heat of about 420 W
    #: below which the pump has always stood (0.01 kg/s when the flow was sized for 10 K).
    MINIMUM_MASS_FLOW_IN_KG_PER_S: ClassVar[float] = 0.005

    #: The storage temperature above which the pump stops, in °C: warm water should leave the tank at 60 °C
    #: (https://www.umweltbundesamt.de/umwelttipps-fuer-den-alltag/heizen-bauen/warmwasser).
    WARM_WATER_AIM_IN_CELSIUS: ClassVar[float] = 60.0

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SolarThermalSystemControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.config = config
        self.my_simulation_parameters = my_simulation_parameters
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # Configure Input Channels
        self.storage_temperature_at_start_deg_c_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.StorageTemperatureAtStartOfStepInCelsius,
            loadtypes.LoadTypes.TEMPERATURE,
            loadtypes.Units.CELSIUS,
            True,
        )

        self.required_mass_flow_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.MassFlow,
            loadtypes.LoadTypes.WARM_WATER,
            loadtypes.Units.KG_PER_SEC,
            True,
        )

        # Configure Output Channels
        self.control_signal_to_solar_thermal_system_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ControlSignalToSolarThermalSystem,
            loadtypes.LoadTypes.ANY,
            loadtypes.Units.BINARY,
            output_description="Control signal to solar pump in SolarThermalSystem",
        )

        self.state: SolarThermalSystemControllerState = SolarThermalSystemControllerState(0, 0, 0)
        self.previous_state: SolarThermalSystemControllerState = self.state.clone()
        self.processed_state: SolarThermalSystemControllerState = self.state.clone()

        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_solar_thermal_system())

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ) -> List[ComponentConnection]:
        """Get simple_water_storage default connections."""

        connections: List[ComponentConnection] = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystemController.StorageTemperatureAtStartOfStepInCelsius,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureAtStartOfStepInCelsius,
            )
        )
        return connections

    def get_default_connections_from_solar_thermal_system(
        self,
    ) -> List[ComponentConnection]:
        """Return the connection of the collector's required mass flow to this controller's input.

        Returns:
            The connection list.
        """

        connections: List[ComponentConnection] = []
        storage_classname = SolarThermalSystem.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystemController.MassFlow,
                storage_classname,
                SolarThermalSystem.RequiredWaterMassFlowOutput,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = self.previous_state.clone()

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulate the solar thermal system controller."""
        if force_convergence:
            # states are saved after each timestep, outputs after each iteration
            # outputs have to be in line with states, so if convergence is forced outputs are aligned to last known state.
            self.state = self.processed_state.clone()
        else:
            pump_runs = self.pump_runs(
                required_mass_flow_in_kg_per_second=stsv.get_input_value(self.required_mass_flow_input_channel),
                storage_temperature_at_start_in_celsius=stsv.get_input_value(
                    self.storage_temperature_at_start_deg_c_input_channel
                ),
            )
            if pump_runs:
                self.state.activate(timestep)
            else:
                self.state.deactivate(timestep)
            self.processed_state = self.state.clone()

        stsv.set_output_value(
            self.control_signal_to_solar_thermal_system_channel,
            self.state.on_off,
        )

    @staticmethod
    def pump_runs(*, required_mass_flow_in_kg_per_second: float, storage_temperature_at_start_in_celsius: float) -> bool:
        """Return whether the solar pump runs in this step.

        It runs while the flow the collector heat at the storage's step mean needs is at least
        :attr:`MINIMUM_MASS_FLOW_IN_KG_PER_S`, which also means the collector has heat there, unless the storage
        started the step above the 60 °C aim. The full-tank stop decides on the start-of-step temperature: on the step
        mean it would have no fixed point when the pump's own heat lifts the mean across the aim. For example, a
        collector that needs 0.01 kg/s runs the pump for a tank that started at 45 °C and not for one at 61 °C.

        Args:
            required_mass_flow_in_kg_per_second: The flow the collector heat needs, in kg/s.
            storage_temperature_at_start_in_celsius: The storage's start-of-step temperature, in °C.

        Returns:
            True while the pump runs.
        """
        storage_is_full = storage_temperature_at_start_in_celsius > SolarThermalSystemController.WARM_WATER_AIM_IN_CELSIUS
        has_heat_enough = required_mass_flow_in_kg_per_second >= SolarThermalSystemController.MINIMUM_MASS_FLOW_IN_KG_PER_S
        return has_heat_enough and not storage_is_full

    def get_cost_opex(
        self,
        all_outputs: List[ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: SolarThermalSystemControllerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List[ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []


class SolarThermalSystemControllerState:
    """Data class that saves the state of the controller."""

    def __init__(
        self,
        on_off: int,
        activation_time_step: int,
        deactivation_time_step: int,
    ) -> None:
        """Initializes the solar pump controller state."""
        self.on_off: int = on_off
        self.activation_time_step: int = activation_time_step
        self.deactivation_time_step: int = deactivation_time_step

    def clone(self) -> "SolarThermalSystemControllerState":
        """Copies the current instance."""
        return SolarThermalSystemControllerState(
            on_off=self.on_off,
            activation_time_step=self.activation_time_step,
            deactivation_time_step=self.deactivation_time_step,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def activate(self, timestep: int) -> None:
        """Activates the solar pump and remembers the time step."""
        self.on_off = 1
        self.activation_time_step = timestep

    def deactivate(self, timestep: int) -> None:
        """Deactivates the solar pump and remembers the time step."""
        self.on_off = 0
        self.deactivation_time_step = timestep
