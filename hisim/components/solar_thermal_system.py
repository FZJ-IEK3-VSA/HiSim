"""Solar thermal system for DHW."""

from copy import deepcopy
import datetime
import math
from typing import ClassVar, List, Optional, Tuple
import dataclasses
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
import numpy as np
import pandas as pd
import pvlib
from hisim.component import (
    CapexCostDataClass,
    L1ControllerComponent,
    PhysicsComponent,
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
from hisim.part_load import PartLoadCommand, PartLoadControl
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


class SolarThermalSystem(PhysicsComponent):
    """Solar thermal system.

    This class represents a solar thermal system that can be used
    for warm water and space heating.

    The pump runs the part-load ratio its controller commands (``PartLoadRatio``): 1, the whole step, at and below the
    part-load threshold, and above it the fraction of the step that ends the tank at the controller's target. Below 1
    the collector publishes the averaged flow at its full-load outlet temperature and books the heat that flow carries;
    the pump draws the ratio of its electricity. The collector reports the ratio its pump ran (``PartLoadRatioRun``):
    the commanded ratio while the pump runs, 0 otherwise.
    """

    cost_relevance = CostRelevance.PRICED

    # Inputs
    TemperatureOutsideDegC: ClassVar[str] = "TemperatureOutsideDegC"
    DiffuseHorizontalIrradianceWM2: ClassVar[str] = "DiffuseHorizontalIrradianceWM2"
    GlobalHorizontalIrradianceWM2: ClassVar[str] = "GlobalHorizontalIrradianceWM2"
    Azimuth: ClassVar[str] = "Azimuth"
    ApparentZenith: ClassVar[str] = "ApparentZenith"
    TemperatureCollectorInletDegC: ClassVar[str] = "TemperatureCollectorInletDegC"
    #: The collector inlet's temperature at the start of the step: the storage's start-of-step temperature, which no
    #: pass of the step moves.
    TemperatureCollectorInletAtStartOfStepInCelsius: ClassVar[str] = "TemperatureCollectorInletAtStartOfStepInCelsius"
    ControlSignal: ClassVar[str] = "ControlSignal"
    #: The fraction of the step the pump runs, commanded by the controller.
    PartLoadRatio: ClassVar[str] = "PartLoadRatio"

    # Outputs
    #: The fraction of the step the pump ran, reported back to the controller.
    PartLoadRatioRun: ClassVar[str] = "PartLoadRatioRun"
    ThermalPowerOutput: ClassVar[str] = "ThermalPowerOutput"
    ThermalEnergyOutput: ClassVar[str] = "ThermalEnergyOutput"
    #: The flow the collector heat at the inlet's start-of-step temperature needs (kg/s), on which the controller
    #: switches the pump.
    RequiredWaterMassFlowAtStartOfStepInKgPerSecond: ClassVar[str] = "RequiredWaterMassFlowAtStartOfStepInKgPerSecond"
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
        self.inlet_temperature_at_start_of_step_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureCollectorInletAtStartOfStepInCelsius,
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
        self.part_load_command = PartLoadCommand(
            self,
            input_name=SolarThermalSystem.PartLoadRatio,
            ratio_run_output_name=SolarThermalSystem.PartLoadRatioRun,
            device_description="the solar pump",
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
        self.required_water_mass_flow_at_start_of_step_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.RequiredWaterMassFlowAtStartOfStepInKgPerSecond,
            load_type=loadtypes.LoadTypes.WARM_WATER,
            unit=loadtypes.Units.KG_PER_SEC,
            output_description=(
                "The mass flow [kg/s] the collector heat at the inlet's start-of-step temperature needs, whether or "
                "not the pump runs. The pump's own heat in the step does not move it, so the controller switches the "
                "pump on it."
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
        """Return the connections from the hot-water storage: the collector inlet's temperatures.

        The collector's inlet is the storage's return leg. Its step mean is the temperature the collector runs at;
        its start-of-step temperature is the one the collector evaluates the flow the controller switches on.

        Returns:
            The connection list.
        """

        connections: List[ComponentConnection] = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystem.TemperatureCollectorInletDegC,
                storage_classname,
                SimpleDHWStorage.StepMeanWaterTemperatureToHeatGeneratorInCelsius,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystem.TemperatureCollectorInletAtStartOfStepInCelsius,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureAtStartOfStepInCelsius,
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
        connections.append(
            ComponentConnection(
                SolarThermalSystem.PartLoadRatio,
                l1_controller_classname,
                component_class.PartLoadRatio,
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
            collector_heat_in_watt: The collector heat, in W: at the storage's step mean for the circuit, at its
                start-of-step temperature for the flow the controller switches on.
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
            collector_heat_in_watt: The collector heat at the inlet temperature, in W.
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

    def collector_heat_in_watt(
        self, *, inlet_temperature_in_celsius: float, ambient_temperature_in_celsius: float
    ) -> float:
        """Return the heat the whole collector field gives at an inlet temperature in this step's irradiance, in W.

        The heat per m² follows the collector's efficiency curve at the irradiance on the collector plane of the step
        last calculated (:attr:`plane_of_array_irradiance_w_m2`), times the collector area. It is zero when the
        collector loses as much as it gains or more, since the efficiency is clipped at zero; the circuit then moves no
        water.

        Args:
            inlet_temperature_in_celsius: The collector's inlet temperature, in °C.
            ambient_temperature_in_celsius: The outside air temperature, in °C.

        Returns:
            The collector heat, in W.
        """
        return (
            self.collector_curve.heat_in_watt_per_m2(
                inlet_temperature_in_celsius=inlet_temperature_in_celsius,
                ambient_temperature_in_celsius=ambient_temperature_in_celsius,
                irradiance_in_watt_per_m2=float(self.plane_of_array_irradiance_w_m2),
            )
            * self.area_m2
        )

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Run the collector at the storage's step mean and publish its circuit, its heat and the pump's electricity.

        The irradiance on the collector plane is computed once per step from the weather and the sun's position;
        the collector heat at the step mean, which moves while the step iterates, on every call. The collector also
        publishes the flow its heat at the inlet's start-of-step temperature needs, the quantity its controller
        switches the pump on: the pump's own heat in the step does not move it.
        """
        # get inputs
        control_signal = stsv.get_input_value(self.control_signal_channel)
        global_horizontal_irradiance_w_m2 = stsv.get_input_value(self.ghi_channel)
        diffuse_horizontal_irradiance_w_m2 = stsv.get_input_value(self.dhi_channel)
        ambient_air_temperature_deg_c = stsv.get_input_value(self.t_out_channel)
        temperature_collector_inlet_deg_c = stsv.get_input_value(self.water_temperature_input_channel)
        inlet_temperature_at_start_of_step_in_celsius = stsv.get_input_value(
            self.inlet_temperature_at_start_of_step_input_channel
        )
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
        collector_heat_at_return_w = self.collector_heat_in_watt(
            inlet_temperature_in_celsius=temperature_collector_inlet_deg_c,
            ambient_temperature_in_celsius=ambient_air_temperature_deg_c,
        )
        collector_heat_at_start_of_step_in_watt = self.collector_heat_in_watt(
            inlet_temperature_in_celsius=inlet_temperature_at_start_of_step_in_celsius,
            ambient_temperature_in_celsius=ambient_air_temperature_deg_c,
        )
        circuit = self.collector_circuit(
            collector_heat_in_watt=collector_heat_at_return_w,
            inlet_temperature_in_celsius=temperature_collector_inlet_deg_c,
            inlet_to_mean_temperature_difference_in_kelvin=self.config.delta_temperature_n_k,
            pump_runs=control_signal != 0,
        )
        required_mass_flow_at_start_of_step_in_kg_per_second = self.required_mass_flow_in_kg_per_second(
            collector_heat_in_watt=collector_heat_at_start_of_step_in_watt,
            inlet_to_mean_temperature_difference_in_kelvin=self.config.delta_temperature_n_k,
        )
        pump_ratio_run = 0.0
        if control_signal != 0:
            pump_ratio_run = self.part_load_command.ratio(stsv)
            circuit = circuit.at_part_load(part_load_ratio=pump_ratio_run, t_return_c=temperature_collector_inlet_deg_c)
        self.part_load_command.publish_ratio_run(stsv, pump_ratio_run)
        thermal_power_output_w = circuit.power_w
        thermal_energy_output_wh: float = (
            thermal_power_output_w
            * self.my_simulation_parameters.seconds_per_timestep
            / hydronics.UnitConversion.JOULES_PER_WATT_HOUR
        )
        # the solar pump draws its electricity for the part of the step it runs
        electric_power_demand_solar_pump_w = pump_ratio_run * self.pump_power_in_watt(
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
            self.required_water_mass_flow_at_start_of_step_output_channel,
            required_mass_flow_at_start_of_step_in_kg_per_second,
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

    The controller switches the pump on when the collector has heat enough for its switch-on flow, keeps it running
    down to a lower switch-off flow, stops it once the vessel has passed its 80 °C maximum storage temperature and
    restarts it only 3 K below that maximum. These rules are the controller's own, so the configuration holds only the
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


class SolarThermalSystemController(L1ControllerComponent):
    """Solar Controller.

    It takes data from other components and sends signal to the
    solar pump (implicitly integrated in the SolarThermalSystem)
    for activation or deactivation.

    Parameters
    ----------
    Components to connect to:
    (1) SolarThermalSystem (control_signal)

    The controller also owns how much of a step the pump runs (:class:`hisim.part_load.PartLoadControl`): it switches
    the pump on and off as before, and commands the part-load ratio ``PartLoadRatio`` with it. At and below the
    part-load threshold the ratio is 1 whenever the pump runs. Above it the ratio follows
    :class:`hisim.part_load.PartLoadRule` from the ratio the collector reports its pump ran (``PartLoadRatioRun``) and
    the storage's start and end temperatures, so that the storage ends the step at its maximum storage temperature, the
    temperature above which it stops the pump (``TargetTemperatureInCelsius``, 80 °C). Nothing ranks it against a
    backup charging the same storage: each controller aims at its own target, and the backups' 60 °C warm-water aim
    lies below the solar maximum, so the sun may heat the storage above the temperature the backups stop at.

    The on/off decision is taken on what the controller's sensors read at the start of the step, as a real controller
    decides on the temperatures it measures at that moment: the flow the collector heat at the storage's start-of-step
    temperature needs (the collector's ``RequiredWaterMassFlowAtStartOfStepInKgPerSecond``) and the storage's
    start-of-step temperature. No pass of the step moves either, so the pump's own heat cannot switch it off again in
    the step it switches on. One exception: the simulator starts each step from the values the previous step
    converged to and evaluates the controller before the collector and the storage, so the first pass of a step reads
    the previous step's sensors and may decide differently; from the second pass on the decision cannot change, and
    the step keeps the decision of its last pass. Two hysteresis bands keep the pump from switching every few steps:

    * the flow: a pump that stood in the previous step switches on at :attr:`SWITCH_ON_MASS_FLOW_IN_KG_PER_S`, a pump
      that ran keeps running down to the lower :attr:`SWITCH_OFF_MASS_FLOW_IN_KG_PER_S`, as a differential solar
      controller switches on at a collector-to-tank difference of about 7 K and off at about 3 K;
    * the full storage: the pump stops once the storage starts a step above its maximum storage temperature,
      :attr:`MAXIMUM_STORAGE_TEMPERATURE_IN_CELSIUS` (80 °C), and stays stopped until the storage starts a step at
      least :attr:`FULL_STORAGE_RESTART_BELOW_MAXIMUM_IN_KELVIN` (3 K) below the maximum.

    Whether the pump ran and whether the full-storage stop holds are the controller's saved state
    (:class:`SolarThermalSystemControllerState`): set from the step's last pass in :meth:`i_save_state`, restored
    before every pass, so the decision of a pass depends only on that pass's inputs and the saved state.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    #: The storage's start-of-step temperature, on which the pump stops once the storage is full.
    StorageTemperatureAtStartOfStepInCelsius: ClassVar[str] = "StorageTemperatureAtStartOfStepInCelsius"
    #: The storage's temperature at the end of the step, which the part-load ratio is set against.
    StorageTemperatureAtEndOfStepInCelsius: ClassVar[str] = "StorageTemperatureAtEndOfStepInCelsius"
    #: The fraction of the step the pump ran, as the collector reports it.
    PartLoadRatioRun: ClassVar[str] = "PartLoadRatioRun"
    #: The flow the collector heat at the storage's start-of-step temperature needs, on which the pump switches on and
    #: off.
    RequiredWaterMassFlowAtStartOfStepInKgPerSecond: ClassVar[str] = "RequiredWaterMassFlowAtStartOfStepInKgPerSecond"

    # Outputs
    ControlSignalToSolarThermalSystem: ClassVar[str] = "ControlSignalToSolarThermalSystem"
    #: The fraction of the step the pump runs.
    PartLoadRatio: ClassVar[str] = "PartLoadRatio"
    #: The storage temperature above which the controller stops the pump, its maximum storage temperature.
    TargetTemperatureInCelsius: ClassVar[str] = "TargetTemperatureInCelsius"

    #: The pump flow at the storage's start-of-step temperature from which a pump that stood in the previous step
    #: switches on, in kg/s. The flow is sized for twice the collector's inlet-to-mean difference (20 K), so
    #: 0.005 kg/s is the collector heat of about 420 W below which the pump has always stood (0.01 kg/s when the flow
    #: was sized for 10 K).
    SWITCH_ON_MASS_FLOW_IN_KG_PER_S: ClassVar[float] = 0.005

    #: The pump flow at the storage's start-of-step temperature below which a pump that ran in the previous step
    #: switches off, in kg/s: half the switch-on flow, about 210 W of collector heat. Differential solar controllers
    #: switch the pump on at a collector-to-tank difference of about 7 K and off at about 3 K, where the heat gained,
    #: the pipe losses and the pump's electricity are about even, a ratio of 3/7. Near the switching point the collector
    #: heat, and with it this flow, grows about in proportion to that difference, so the switch-off flow lies between
    #: 3/7 and 1/2 of the switch-on flow; half lies inside that band. Without the band a pump whose heat warms the
    #: storage from one step to the next would switch off and on again every few steps.
    SWITCH_OFF_MASS_FLOW_IN_KG_PER_S: ClassVar[float] = 0.0025

    #: The maximum storage temperature: the storage temperature above which the pump stops, in °C. It is the solar
    #: controller's own limit, above the 60 °C warm-water aim the backup generators (boiler, heat pump, district
    #: heating, electric heater) charge to, and above the 69.5 °C the heat pump charges to while the energy manager
    #: raises its set temperature on PV surplus. Solar controllers let the sun heat the tank past the backup's set point
    #: up to a maximum storage temperature that installers set between about 60 and 80 °C; a thermostatic mixing
    #: valve at the tap keeps the water drawn at the tap temperature, so a hotter tank does not scald and delivers
    #: the same heat to the tap. With a maximum equal to the backup's aim the tank waits just below 60 °C on sunny
    #: days and the backup supplies what the sun could have. The owner chose 80 °C, the upper end, on 2026-10-11: the
    #: larger standby loss of the hotter tank is accepted.
    MAXIMUM_STORAGE_TEMPERATURE_IN_CELSIUS: ClassVar[float] = 80.0

    #: How far below the maximum storage temperature the storage must start a step before a pump the full storage
    #: stopped runs again, in K. Solar controllers restart the pump a few kelvin below their maximum storage
    #: temperature, typically 3 to 5 K; 3 K is the lower end. Without it the part-load rule, which ends a charging step
    #: just above the maximum, and the standby loss of the next step switch the pump every second step.
    FULL_STORAGE_RESTART_BELOW_MAXIMUM_IN_KELVIN: ClassVar[float] = 3.0

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
            self.RequiredWaterMassFlowAtStartOfStepInKgPerSecond,
            loadtypes.LoadTypes.WARM_WATER,
            loadtypes.Units.KG_PER_SEC,
            True,
        )

        self.part_load = PartLoadControl(
            self,
            end_temperature_input_name=self.StorageTemperatureAtEndOfStepInCelsius,
            ratio_run_input_name=self.PartLoadRatioRun,
            ratio_output_name=self.PartLoadRatio,
            target_output_name=self.TargetTemperatureInCelsius,
            device_description="the solar pump",
        )

        # Configure Output Channels
        self.control_signal_to_solar_thermal_system_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ControlSignalToSolarThermalSystem,
            loadtypes.LoadTypes.ANY,
            loadtypes.Units.BINARY,
            output_description="Control signal to solar pump in SolarThermalSystem",
        )

        #: The pump state of the step being iterated, as the latest pass decided it.
        self.state = SolarThermalSystemControllerState(pump_runs=False, full_storage_stop_holds=False)
        #: The saved state: the pump state the previous step ended with.
        self.previous_state = SolarThermalSystemControllerState(pump_runs=False, full_storage_stop_holds=False)

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
        connections.append(
            ComponentConnection(
                SolarThermalSystemController.StorageTemperatureAtEndOfStepInCelsius,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureAtEndOfStepInCelsius,
            )
        )
        return connections

    def get_default_connections_from_solar_thermal_system(
        self,
    ) -> List[ComponentConnection]:
        """Return the connections from the collector: its flow at the start-of-step inlet and its pump's ratio run.

        Returns:
            The connection list.
        """

        connections: List[ComponentConnection] = []
        storage_classname = SolarThermalSystem.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystemController.RequiredWaterMassFlowAtStartOfStepInKgPerSecond,
                storage_classname,
                SolarThermalSystem.RequiredWaterMassFlowAtStartOfStepInKgPerSecond,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystemController.PartLoadRatioRun,
                storage_classname,
                SolarThermalSystem.PartLoadRatioRun,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Save the pump state the step that just ended was left with, from its last pass, as the state to start from.

        The simulator calls this at the start of every step, so the saved state is whether the pump ran and whether
        the full-storage stop held in the previous step: the two hysteresis bands of :meth:`pump_runs` and
        :meth:`full_storage_stop_holds` read it.
        """
        self.previous_state = dataclasses.replace(self.state)

    def i_restore_state(self) -> None:
        """Return to the saved state before a pass, so a pass never sees what an earlier pass of the step decided."""
        self.state = dataclasses.replace(self.previous_state)

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Decide whether the pump runs and, above the part-load threshold, the fraction of the step it runs.

        The full-storage stop follows :meth:`full_storage_stop_holds` from the storage's start-of-step temperature and
        whether the stop held in the previous step. In an unforced pass the pump's on/off decision follows
        :meth:`pump_runs` from the collector's flow at the start-of-step temperature, the full-storage stop and whether
        the pump ran in the previous step. Under ``force_convergence`` the controller keeps the pump as the collector
        reports it ran (:meth:`pump_ran_as_reported`) and its part-load ratio becomes the ratio run, so the device
        keeps running what it ran and the iteration stops moving. All of it reads only inputs and the saved state.
        The inputs are start-of-step quantities, so from the second pass on an unforced pass decides as the pass
        before it; the first pass, which reads the previous step's sensors, may decide differently.
        """
        storage_temperature_at_start_in_celsius = stsv.get_input_value(
            self.storage_temperature_at_start_deg_c_input_channel
        )
        full_storage_stop_holds = self.full_storage_stop_holds(
            storage_temperature_at_start_in_celsius=storage_temperature_at_start_in_celsius,
            stop_held_in_previous_step=self.previous_state.full_storage_stop_holds,
        )
        if force_convergence:
            pump_runs = self.pump_ran_as_reported(ratio_run=self.part_load.ratio_run(stsv))
        else:
            pump_runs = self.pump_runs(
                required_mass_flow_at_start_of_step_in_kg_per_second=stsv.get_input_value(
                    self.required_mass_flow_input_channel
                ),
                full_storage_stop_holds=full_storage_stop_holds,
                pump_ran_in_previous_step=self.previous_state.pump_runs,
            )
        self.state = SolarThermalSystemControllerState(
            pump_runs=pump_runs, full_storage_stop_holds=full_storage_stop_holds
        )

        stsv.set_output_value(self.control_signal_to_solar_thermal_system_channel, 1 if pump_runs else 0)
        if force_convergence:
            self.part_load.publish_held(stsv)
        else:
            self.part_load.publish(
                stsv,
                device_runs=pump_runs,
                start_temperature_in_celsius=storage_temperature_at_start_in_celsius,
                target_temperature_in_celsius=self.MAXIMUM_STORAGE_TEMPERATURE_IN_CELSIUS,
            )

    @staticmethod
    def full_storage_stop_holds(
        *, storage_temperature_at_start_in_celsius: float, stop_held_in_previous_step: bool
    ) -> bool:
        """Return whether the full-storage stop holds the solar pump in this step, from the storage and the last step.

        The full-storage stop enforces the controller's maximum storage temperature: it sets once the storage starts a
        step above :attr:`MAXIMUM_STORAGE_TEMPERATURE_IN_CELSIUS` (80 °C), and releases only once the storage starts a
        step at least :attr:`FULL_STORAGE_RESTART_BELOW_MAXIMUM_IN_KELVIN` (3 K) below the maximum. Between the two
        edges it keeps the state of the previous step. It reads the start-of-step temperature, which no pass of the
        step moves. For example, a storage that started at 78.5 °C is held if the stop held in the previous step and
        free otherwise; one that started at 77 °C is free; one that started at 80.02 °C is held.

        Args:
            storage_temperature_at_start_in_celsius: The storage's start-of-step temperature, in °C.
            stop_held_in_previous_step: Whether the stop held in the previous step, the controller's saved state.

        Returns:
            True while the full-storage stop holds the pump.
        """
        maximum_in_celsius = SolarThermalSystemController.MAXIMUM_STORAGE_TEMPERATURE_IN_CELSIUS
        if storage_temperature_at_start_in_celsius > maximum_in_celsius:
            return True
        restart_temperature_in_celsius = (
            maximum_in_celsius - SolarThermalSystemController.FULL_STORAGE_RESTART_BELOW_MAXIMUM_IN_KELVIN
        )
        return stop_held_in_previous_step and storage_temperature_at_start_in_celsius > restart_temperature_in_celsius

    @staticmethod
    def pump_runs(
        *,
        required_mass_flow_at_start_of_step_in_kg_per_second: float,
        full_storage_stop_holds: bool,
        pump_ran_in_previous_step: bool,
    ) -> bool:
        """Return whether the solar pump runs in a step, from the collector's flow, the full-storage stop and its state.

        The flow is the one the collector heat at the storage's start-of-step temperature needs, which the pump's own
        heat in the step does not move. A pump that stood in the previous step switches on when that flow is at least
        :attr:`SWITCH_ON_MASS_FLOW_IN_KG_PER_S` (0.005 kg/s); a pump that ran keeps running while it is at least
        :attr:`SWITCH_OFF_MASS_FLOW_IN_KG_PER_S` (0.0025 kg/s). Either way the pump stands while the full-storage stop
        (:meth:`full_storage_stop_holds`) holds. For example, a collector that needs 0.004 kg/s keeps a running pump
        on, does not switch on a pump that stood, and runs neither while the full-storage stop holds.

        Args:
            required_mass_flow_at_start_of_step_in_kg_per_second: The flow the collector heat at the storage's
                start-of-step temperature needs, in kg/s.
            full_storage_stop_holds: Whether the full-storage stop holds the pump in this step.
            pump_ran_in_previous_step: Whether the pump ran in the previous step, the controller's saved state.

        Returns:
            True while the pump runs.
        """
        if full_storage_stop_holds:
            return False
        if pump_ran_in_previous_step:
            least_mass_flow_in_kg_per_second = SolarThermalSystemController.SWITCH_OFF_MASS_FLOW_IN_KG_PER_S
        else:
            least_mass_flow_in_kg_per_second = SolarThermalSystemController.SWITCH_ON_MASS_FLOW_IN_KG_PER_S
        return required_mass_flow_at_start_of_step_in_kg_per_second >= least_mass_flow_in_kg_per_second

    @staticmethod
    def pump_ran_as_reported(*, ratio_run: float) -> bool:
        """Return whether the pump ran in this pass, from the part-load ratio the collector reports its pump ran with.

        The collector reports the fraction of the step its pump ran: 0 while it stood, more than 0 while it ran. A
        forced pass keeps the pump as it ran, so the on/off signal stops changing without the controller remembering
        an earlier pass. For example, a reported ratio of 0.42 means the pump ran.

        Args:
            ratio_run: The part-load ratio the collector reports, from 0 to 1.

        Returns:
            True if the pump ran.
        """
        return ratio_run > 0.0

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


@dataclass(frozen=True)
class SolarThermalSystemControllerState:
    """The solar pump controller's state: whether the pump runs and whether the full-storage stop holds it.

    The controller keeps two of them: the state of the step being iterated, as the latest pass decided it, and the
    saved state, the one the previous step ended with. The saved state chooses the edges of both hysteresis bands: the
    switch-on or switch-off flow, and whether the storage must fall 3 K below the maximum storage temperature before
    the pump runs again.
    """

    #: Whether the pump runs.
    pump_runs: bool

    #: Whether the full-storage stop holds the pump: set once the storage started a step above the maximum storage
    #: temperature, released once it started a step at least 3 K below it.
    full_storage_stop_holds: bool
