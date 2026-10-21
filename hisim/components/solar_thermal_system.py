"""Solar thermal system for DHW."""

import math
from typing import ClassVar, List, Optional, Tuple
import dataclasses
from dataclasses import dataclass
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
from hisim import hydronics, loadtypes
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

    """A flat-plate collector's efficiency curve, the law that turns irradiance into heat at a mean temperature.

    The efficiency is ``eta_0 - a_1 dT / G - a_2 dT^2 / G`` with ``dT`` the collector's mean temperature (the inlet
    plus the inlet-to-mean difference, half the lift from inlet to outlet) above the air and ``G`` the irradiance on
    its plane; a value object built once from the configuration, so the law is a pure function of its arguments.
    """

    #: The optical efficiency, dimensionless.
    eta_0: float
    #: The linear heat loss coefficient, in W/(m² K).
    a_1_in_watt_per_m2_per_kelvin: float
    #: The quadratic heat loss coefficient, in W/(m² K²).
    a_2_in_watt_per_m2_per_kelvin_squared: float

    def heat_in_watt_per_m2(
        self,
        *,
        inlet_temperature_in_celsius: float,
        inlet_to_mean_temperature_difference_in_kelvin: float,
        ambient_temperature_in_celsius: float,
        irradiance_in_watt_per_m2: float,
    ) -> float:
        """Return the heat one square metre of collector delivers at an inlet and a mean temperature, in W/m².

        It is :func:`flat_plate_collector_heat_w_m2`, zero when the efficiency is not positive. For example, at
        500 W/m², a 40 °C inlet, a 10 K inlet-to-mean difference and 5 °C outside a typical collector delivers about
        215 W/m².

        Args:
            inlet_temperature_in_celsius: The collector's inlet temperature, in °C.
            inlet_to_mean_temperature_difference_in_kelvin: How far the collector's mean temperature lies above its
                inlet, in K: half the lift from inlet to outlet.
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
                delta_temperature_n_k=inlet_to_mean_temperature_difference_in_kelvin,
                ambient_air_temperature_deg_c=ambient_temperature_in_celsius,
                collector_irradiance_w_m2=np.float64(irradiance_in_watt_per_m2),
            )
        )

    def lift_at_mass_flow_in_kelvin(
        self,
        *,
        inlet_temperature_in_celsius: float,
        ambient_temperature_in_celsius: float,
        irradiance_in_watt_per_m2: float,
        mass_flow_in_kg_per_second_per_m2: float,
    ) -> float:
        """Return the lift from inlet to outlet a collector gives at a fixed flow per m², in K; 0 without heat.

        At a fixed flow the lift ``L`` and the heat set each other: the heat is the curve's at the mean temperature,
        the inlet plus ``L / 2``, and the water carries it, ``c m L``. With ``x`` the inlet above the air and ``q_0``
        the heat at a mean temperature equal to the inlet, the balance ``c m L = q_0 - (a_1 / 2 + a_2 x) L -
        a_2 L^2 / 4`` is a quadratic in ``L`` with one positive root while ``q_0`` is positive. For example, the
        flat-plate preset's curve at 200 W/m², a 45 °C inlet and 20 °C air, at 5 kg/(m² h), lifts the water by
        8.5 K.

        Args:
            inlet_temperature_in_celsius: The collector's inlet temperature, in °C.
            ambient_temperature_in_celsius: The outside air temperature, in °C.
            irradiance_in_watt_per_m2: The irradiance on the collector plane, in W/m².
            mass_flow_in_kg_per_second_per_m2: The flow through one m² of collector, in kg/(s m²), above 0.

        Returns:
            The lift, in K.
        """
        if irradiance_in_watt_per_m2 <= 0.0:
            return 0.0
        inlet_above_air_in_kelvin = inlet_temperature_in_celsius - ambient_temperature_in_celsius
        a_1 = self.a_1_in_watt_per_m2_per_kelvin
        a_2 = self.a_2_in_watt_per_m2_per_kelvin_squared
        heat_at_the_inlet_in_watt_per_m2 = (
            self.eta_0 * irradiance_in_watt_per_m2
            - a_1 * inlet_above_air_in_kelvin
            - a_2 * inlet_above_air_in_kelvin * inlet_above_air_in_kelvin
        )
        if heat_at_the_inlet_in_watt_per_m2 <= 0.0:
            return 0.0
        linear_in_watt_per_m2_per_kelvin = (
            hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * mass_flow_in_kg_per_second_per_m2
            + a_1 / 2.0
            + a_2 * inlet_above_air_in_kelvin
        )
        # the root of a_2/4 L^2 + linear L - q_0 = 0 in the form that stays exact for a_2 = 0
        return (
            2.0
            * heat_at_the_inlet_in_watt_per_m2
            / (
                linear_in_watt_per_m2_per_kelvin
                + math.sqrt(
                    linear_in_watt_per_m2_per_kelvin * linear_in_watt_per_m2_per_kelvin
                    + a_2 * heat_at_the_inlet_in_watt_per_m2
                )
            )
        )

    def stagnation_temperature_in_celsius(
        self, *, ambient_temperature_in_celsius: float, irradiance_in_watt_per_m2: float
    ) -> float:
        """Return the temperature a collector reaches while no water flows through it, in °C.

        Without flow the collector warms until its heat loss equals the irradiance it absorbs, so its temperature
        ``T`` solves ``eta_0 G = a_1 (T - T_a) + a_2 (T - T_a)^2``: the efficiency curve at zero heat, with the whole
        collector at one temperature. For example, the flat-plate preset's curve (``eta_0`` 0.78, ``a_1`` 3.2,
        ``a_2`` 0.015) stagnates 40.9 K above the air at 200 W/m², at 60.9 °C in 20 °C air. Without irradiance the
        collector is at the air temperature. The sensor of a collector whose pump stands reads this temperature, which
        is how a differential controller sees that the collector has heat to give.

        Args:
            ambient_temperature_in_celsius: The outside air temperature, in °C.
            irradiance_in_watt_per_m2: The irradiance on the collector plane, in W/m².

        Returns:
            The stagnation temperature, in °C.
        """
        absorbed_in_watt_per_m2 = self.eta_0 * max(irradiance_in_watt_per_m2, 0.0)
        a_1 = self.a_1_in_watt_per_m2_per_kelvin
        a_2 = self.a_2_in_watt_per_m2_per_kelvin_squared
        if a_2 > 0.0:
            excess_in_kelvin = (math.sqrt(a_1 * a_1 + 4.0 * a_2 * absorbed_in_watt_per_m2) - a_1) / (2.0 * a_2)
        else:
            excess_in_kelvin = absorbed_in_watt_per_m2 / a_1
        return ambient_temperature_in_celsius + excess_in_kelvin


@dataclass(frozen=True)
class CollectorOperatingPoint:
    """What a collector field with its pump running does in one step: its heat, the flow and the lift.

    The water carries the heat, ``heat = c m lift``. A collector without heat has no operating point to run at: its
    point is :meth:`idle`, no heat, no flow, no lift.
    """

    #: The heat of the whole collector field, in W.
    heat_in_watt: float
    #: The flow through the whole collector field, in kg/s.
    mass_flow_in_kg_per_second: float
    #: The lift from collector inlet to outlet, in K.
    lift_in_kelvin: float

    @staticmethod
    def idle() -> "CollectorOperatingPoint":
        """Return the point of a collector without heat: no heat, no flow, no lift.

        Returns:
            The idle operating point.
        """
        return CollectorOperatingPoint(heat_in_watt=0.0, mass_flow_in_kg_per_second=0.0, lift_in_kelvin=0.0)


@dataclass(frozen=True)
class SpeedControlledSolarPump:
    """The solar circuit's speed-controlled pump: the flow it runs for a collector, per m² of collector.

    The pump runs the flow that lifts the water by its target lift, ``Q / (c lift)`` with ``Q`` the collector heat at
    that lift, as long as that flow lies between its minimum and its maximum specific flow times the collector area.
    Otherwise it runs the nearer bound, and the lift follows from the collector at that flow: below the target lift at
    the minimum, above it at the maximum. For example, a 4 m² collector at 45 °C in 20 °C air with a 20 K target lift
    and 5 to 40 kg/(m² h) runs 0.0236 kg/s (20 K) at 800 W/m², and the minimum, 0.0056 kg/s, at 200 W/m², where the
    lift is 8.5 K. A constant-flow pump has its minimum equal to its maximum. Every bound is per m², so collector
    fields of any size run alike per m².
    """

    #: The lift from the collector inlet to its outlet the pump aims at, in K.
    target_lift_in_kelvin: float
    #: The smallest flow the pump runs, per m² of collector, in kg/(m² h).
    minimum_mass_flow_in_kg_per_hour_per_m2: float
    #: The largest flow the pump runs, per m² of collector, in kg/(m² h).
    maximum_mass_flow_in_kg_per_hour_per_m2: float

    #: Seconds in an hour, to turn the specific flows per hour into flows per second.
    SECONDS_PER_HOUR: ClassVar[float] = 3600.0

    def operating_point(
        self,
        *,
        curve: FlatPlateCollectorCurve,
        inlet_temperature_in_celsius: float,
        ambient_temperature_in_celsius: float,
        irradiance_in_watt_per_m2: float,
        area_m2: float,
    ) -> CollectorOperatingPoint:
        """Return the heat, the flow and the lift of a collector field this pump runs, at an inlet temperature.

        First the target lift: the heat at a mean temperature half the target lift above the inlet, and the flow that
        carries it over the target lift. If that flow lies within the bounds, that is the point. Otherwise the pump
        runs the nearer bound and the lift follows from the collector at that flow
        (:meth:`FlatPlateCollectorCurve.lift_at_mass_flow_in_kelvin`). Without heat even at the bound the point is
        idle. For example, 4 m² of the flat-plate preset at a 45 °C inlet in 20 °C air with a 20 K target lift and 5
        to 40 kg/(m² h) give 1975 W at 0.0236 kg/s at 800 W/m², and 198 W at the minimum, 0.0056 kg/s, with an 8.5 K
        lift at 200 W/m².

        Args:
            curve: The collector's efficiency curve.
            inlet_temperature_in_celsius: The collector's inlet temperature, in °C.
            ambient_temperature_in_celsius: The outside air temperature, in °C.
            irradiance_in_watt_per_m2: The irradiance on the collector plane, in W/m².
            area_m2: The collector area, in m².

        Returns:
            The operating point.
        """
        specific_heat = hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K
        heat_in_watt = (
            curve.heat_in_watt_per_m2(
                inlet_temperature_in_celsius=inlet_temperature_in_celsius,
                inlet_to_mean_temperature_difference_in_kelvin=self.target_lift_in_kelvin / 2,
                ambient_temperature_in_celsius=ambient_temperature_in_celsius,
                irradiance_in_watt_per_m2=irradiance_in_watt_per_m2,
            )
            * area_m2
        )
        mass_flow_in_kg_per_second = max(heat_in_watt, 0.0) / (specific_heat * self.target_lift_in_kelvin)
        least_in_kg_per_second = self.minimum_mass_flow_in_kg_per_hour_per_m2 * area_m2 / self.SECONDS_PER_HOUR
        most_in_kg_per_second = self.maximum_mass_flow_in_kg_per_hour_per_m2 * area_m2 / self.SECONDS_PER_HOUR
        if heat_in_watt > 0 and least_in_kg_per_second <= mass_flow_in_kg_per_second <= most_in_kg_per_second:
            return CollectorOperatingPoint(
                heat_in_watt=heat_in_watt,
                mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
                lift_in_kelvin=self.target_lift_in_kelvin,
            )
        if mass_flow_in_kg_per_second > most_in_kg_per_second:
            bound_in_kg_per_second = most_in_kg_per_second
        else:
            bound_in_kg_per_second = least_in_kg_per_second
        lift_in_kelvin = curve.lift_at_mass_flow_in_kelvin(
            inlet_temperature_in_celsius=inlet_temperature_in_celsius,
            ambient_temperature_in_celsius=ambient_temperature_in_celsius,
            irradiance_in_watt_per_m2=irradiance_in_watt_per_m2,
            mass_flow_in_kg_per_second_per_m2=bound_in_kg_per_second / area_m2,
        )
        if lift_in_kelvin <= 0.0:
            return CollectorOperatingPoint.idle()
        return CollectorOperatingPoint(
            heat_in_watt=specific_heat * bound_in_kg_per_second * lift_in_kelvin,
            mass_flow_in_kg_per_second=bound_in_kg_per_second,
            lift_in_kelvin=lift_in_kelvin,
        )


@dataclass_json
@dataclass
class SolarThermalSystemConfig(ConfigBase):
    """Configuration of the solar thermal system: a collector field preheating the hot water storage.

    A collector is described by three things: the efficiency curve it converts irradiance with
    (``eta_0`` and the two thermal loss parameters), the plane it sits in (``tilt``, ``azimuth``),
    and how much of it there is (``area_m2``). It names no location of its own: it stands at the
    weather's location, and the weather publishes the sun over it with the irradiance. Only the area
    is sized against the building:
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

    #: The lift from collector inlet to outlet the solar pump aims at, in K. 20 K is the lift the collector circuit
    #: has always run at (twice the 10 K inlet-to-mean difference its efficiency curve was evaluated at). High-flow
    #: circuits lift the water by 10 to 15 K at 800 W/m², low-flow circuits by far more (W. Weiss, AEE INTEC, "Lay out
    #: of systems: high-flow and low-flow systems",
    #: https://www.crses.sun.ac.za/files/services/events/workshops/05_Lay-out_High_and%20low%20flow.pdf).
    PUMP_TARGET_LIFT_IN_KELVIN: ClassVar[float] = 20.0

    #: The smallest flow of the speed-controlled solar pump per m² of collector, in kg/(m² h): the low end of the
    #: 5 to 20 kg/(m² h) an r.p.m.-controlled low-flow pump runs (the AEE INTEC source above; the Austrian Federal
    #: Ministry of Science and Transport's Forschungsforum 2/97, "Solar systems with layer storage in low-flow
    #: operation", gives 8 to 18 kg/(m² h) for low flow,
    #: https://nachhaltigwirtschaften.at/resources/nw_pdf/fofo/fofo2_97_en.pdf). At 5 kg/(m² h) the preset collector
    #: whose stagnation temperature stood 7 K above the tank lifts the water by 3.3 to 4.3 K (tanks of 20 to 60 °C,
    #: air of -5 to 25 °C), more than the controller's 3 K switch-off difference, so a pump the controller switches on
    #: is not switched off in the next step by the cooler sensor its own flow gives. At 10 kg/(m² h) the lift there is
    #: about 2 K, and the pump switches on and off in alternate steps while the sun stands near the switch-on point.
    PUMP_MINIMUM_MASS_FLOW_IN_KG_PER_HOUR_PER_M2: ClassVar[float] = 5.0

    #: The largest flow of the speed-controlled solar pump per m² of collector, in kg/(m² h): 40 kg/(m² h), the low
    #: end of the 40 to 70 kg/(m² h) typical of high-flow circuits (the AEE INTEC source above), the full speed of a
    #: pump sized for a high-flow circuit. The preset collector's 20 K target lift needs about 21 kg/(m² h) at
    #: 800 W/m² and 28 kg/(m² h) at 1000 W/m², so the maximum hardly binds.
    PUMP_MAXIMUM_MASS_FLOW_IN_KG_PER_HOUR_PER_M2: ClassVar[float] = 40.0

    #: The lift from collector inlet to outlet the speed-controlled solar pump aims at, in K. The collector's mean
    #: temperature, at which its efficiency curve is evaluated, lies half the lift above the inlet.
    pump_target_lift_in_kelvin: float = PUMP_TARGET_LIFT_IN_KELVIN
    #: The smallest flow the solar pump runs per m² of collector, in kg/(m² h).
    pump_minimum_mass_flow_in_kg_per_hour_per_m2: float = PUMP_MINIMUM_MASS_FLOW_IN_KG_PER_HOUR_PER_M2
    #: The largest flow the solar pump runs per m² of collector, in kg/(m² h); equal to the minimum for a
    #: constant-flow pump.
    pump_maximum_mass_flow_in_kg_per_hour_per_m2: float = PUMP_MAXIMUM_MASS_FLOW_IN_KG_PER_HOUR_PER_M2

    def __post_init__(self) -> None:
        """Refuse a solar pump whose numbers give no finite positive flow and lift.

        The pump's flow is the collector heat over its target lift, clamped to its two specific flows: a target lift of
        0 K divides by zero, a minimum flow of 0 gives an infinite lift, and a maximum below the minimum is no range.
        For example, the preset's 20 K and 5 to 40 kg/(m² h) are accepted; ``pump_target_lift_in_kelvin=-5`` and a
        maximum flow of 5 below a minimum of 10 kg/(m² h) are refused.

        Raises:
            ValueError: If ``pump_target_lift_in_kelvin`` is not finite or not above 0 K,
                ``pump_minimum_mass_flow_in_kg_per_hour_per_m2`` is not finite or not above 0, or
                ``pump_maximum_mass_flow_in_kg_per_hour_per_m2`` is not finite or below the minimum.
        """
        if not math.isfinite(self.pump_target_lift_in_kelvin) or self.pump_target_lift_in_kelvin <= 0:
            raise ValueError(
                f"The solar pump's target lift pump_target_lift_in_kelvin must be a finite number above 0 K, got "
                f"{self.pump_target_lift_in_kelvin!r}: the pump's flow is the collector heat over this lift."
            )
        minimum = self.pump_minimum_mass_flow_in_kg_per_hour_per_m2
        maximum = self.pump_maximum_mass_flow_in_kg_per_hour_per_m2
        if not math.isfinite(minimum) or minimum <= 0:
            raise ValueError(
                f"The solar pump's minimum flow pump_minimum_mass_flow_in_kg_per_hour_per_m2 must be a finite number "
                f"above 0 kg/(m2 h), got {minimum!r}: the lift at the minimum flow is the collector heat over it."
            )
        if not math.isfinite(maximum) or maximum < minimum:
            raise ValueError(
                f"The solar pump's maximum flow pump_maximum_mass_flow_in_kg_per_hour_per_m2 must be a finite number "
                f"at least its minimum {minimum!r} kg/(m2 h), got {maximum!r}; a constant-flow pump has both equal."
            )

    @preset
    @classmethod
    def preset_flat_plate(cls, name: str) -> "SolarThermalSystemConfig":
        """A Solar Keymark flat-plate collector at the weather's location, facing south at 30 degrees.

        The field defaults are the whole installation: the efficiency curve fitted to the typical
        flat-plate curve, a south-facing plane inclined 30 degrees and a new solar pump. The collector
        area is the one thing the preset does not state, because it
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

    The solar pump is speed-controlled (:class:`SpeedControlledSolarPump`): it runs the flow that lifts the water by
    its target lift, within a minimum and a maximum flow per m² of collector, and the outlet is the inlet plus the lift
    the collector heat gives at that flow.

    The pump runs the part-load ratio its controller commands (``PartLoadRatio``): 1, the whole step, at and below the
    part-load threshold, and above it the fraction of the step that ends the tank at the controller's target. Below 1
    the collector publishes the averaged flow at its full-load outlet temperature and books the heat that flow carries;
    the pump draws the ratio of its electricity. At a commanded ratio of 0 the collector circuit is idle even while the
    pump signal is on: no flow, and the outlet at the inlet temperature. The collector reports the ratio its pump ran
    (``PartLoadRatioRun``): the commanded ratio while the pump runs, 0 otherwise.

    The collector carries the sensor its controller switches the pump on
    (``CollectorSensorTemperatureAtStartOfStepInCelsius``), read at the start of the step: while the pump stood in the
    previous step, the collector's stagnation temperature in this step's sun
    (:meth:`FlatPlateCollectorCurve.stagnation_temperature_in_celsius`); while it ran, its outlet temperature with the
    tank's start-of-step temperature at the inlet. Whether the pump ran in the previous step is the collector's saved
    state (:class:`SolarThermalSystemState`).
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
    #: The temperature the collector's sensor reads at the start of the step (°C), on which the controller switches
    #: the pump: the stagnation temperature while the pump stood in the previous step, the outlet while it ran.
    CollectorSensorTemperatureAtStartOfStepInCelsius: ClassVar[str] = "CollectorSensorTemperatureAtStartOfStepInCelsius"
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
        #: Whether the pump ran, as the latest pass of the step being iterated has it.
        self.state = SolarThermalSystemState(pump_ran=False)
        #: The saved state: whether the pump ran in the previous step, which chooses what the sensor reads.
        self.previous_state = SolarThermalSystemState(pump_ran=False)
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
        )
        self.pump = SpeedControlledSolarPump(
            target_lift_in_kelvin=config.pump_target_lift_in_kelvin,
            minimum_mass_flow_in_kg_per_hour_per_m2=config.pump_minimum_mass_flow_in_kg_per_hour_per_m2,
            maximum_mass_flow_in_kg_per_hour_per_m2=config.pump_maximum_mass_flow_in_kg_per_hour_per_m2,
        )
        # The plane-of-array irradiance of the timestep last calculated, keyed by the timestep and
        # the weather it came from: its irradiance and its sun. It depends on the weather alone, not
        # on the storage, so the convergence iterations of one timestep share one calculation of it.
        self.plane_of_array_key: Optional[Tuple[int, float, float, float, float]] = None
        self.plane_of_array_irradiance_w_m2: np.float64 = np.float64(0)

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
        self.sensor_temperature_at_start_of_step_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CollectorSensorTemperatureAtStartOfStepInCelsius,
            load_type=loadtypes.LoadTypes.TEMPERATURE,
            unit=loadtypes.Units.CELSIUS,
            output_description=(
                "The temperature [°C] the collector's sensor reads at the start of the step: its stagnation "
                "temperature in the step's sun while the pump stood in the previous step, its outlet temperature at "
                "the tank's start-of-step temperature while the pump ran. No pass of the step moves it, so the "
                "controller switches the pump on it."
            ),
        )

        self.water_temperature_deg_c_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.WaterTemperatureOutput,
            load_type=loadtypes.LoadTypes.TEMPERATURE,
            unit=loadtypes.Units.CELSIUS,
            output_description=(
                "Supply temperature [°C] of the collector's circuit: the storage's step mean plus the lift the "
                "collector heat gives at the pump's flow while the pump runs with heat, the return otherwise."
            ),
        )
        self.electricity_consumption_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityConsumptionOutput,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
            output_description="Electricity consumption of the solar pump.",
            postprocessing_flag=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            energy_port=EnergyPort(loadtypes.EnergyRole.IN, loadtypes.EnergyBalanceCarrier.ELECTRICITY),
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
        its start-of-step temperature is the inlet of the outlet the collector's sensor reads while the pump runs.

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
        """Save whether the pump ran in the step that just ended, from its last pass, as the state to start from.

        The simulator calls this at the start of every step, so the saved state is whether the pump ran in the
        previous step, which chooses whether the sensor reads the stagnation or the outlet temperature.
        """
        self.previous_state = dataclasses.replace(self.state)

    def i_restore_state(self) -> None:
        """Return to the saved state before a pass, so a pass never sees what an earlier pass of the step decided."""
        self.state = dataclasses.replace(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(self) -> None:
        """Forget the plane-of-array irradiance of an earlier run, so the first step computes its own.

        Nothing is precomputed. The sun's position over the collector comes from the weather, at every timestep,
        through the ``ApparentZenith`` and ``Azimuth`` inputs: the weather has looked the run's window up in its own
        year (:mod:`hisim.window_offset`), so its sun and its irradiance stand on one clock. The collector used to
        work out a sun of its own from the run's start date read as UTC, which stood an hour off the weather's
        standard-time clock in every run.
        """
        self.plane_of_array_key = None

    #: The electricity of the solar pump while it runs, in W, for a current high-efficiency circulator.
    PUMP_POWER_IN_WATT: ClassVar[float] = 10.0
    #: The electricity of an old, uncontrolled solar pump while it runs, in W.
    OLD_PUMP_POWER_IN_WATT: ClassVar[float] = 35.0

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
        operating_point: CollectorOperatingPoint,
        inlet_temperature_in_celsius: float,
        pump_runs: bool,
    ) -> hydronics.CircuitStep:
        """Return the collector circuit of one step: its flow, its supply and the heat its water carries.

        While the pump runs and the collector has heat, the circuit runs the operating point's flow and supplies the
        inlet plus the operating point's lift, so its water carries exactly the collector heat. Otherwise it moves no
        water. For example, 2090 W at 0.025 kg/s with a 20 K lift at a 40 °C inlet supply 60 °C.

        Args:
            operating_point: The heat, flow and lift the collector runs at with its pump running
                (:meth:`SpeedControlledSolarPump.operating_point`).
            inlet_temperature_in_celsius: The collector's inlet temperature, the storage's step mean, in °C.
            pump_runs: Whether the controller runs the solar pump.

        Returns:
            The circuit's mass flow, supply temperature and heat.
        """
        if not pump_runs or operating_point.heat_in_watt <= 0:
            return hydronics.CircuitStep.idle(t_return_c=inlet_temperature_in_celsius)
        supply_temperature_in_celsius = inlet_temperature_in_celsius + operating_point.lift_in_kelvin
        return hydronics.CircuitStep(
            mass_flow_kg_per_s=operating_point.mass_flow_in_kg_per_second,
            t_supply_c=supply_temperature_in_celsius,
            power_w=hydronics.circuit_power_w(
                mass_flow_kg_per_s=operating_point.mass_flow_in_kg_per_second,
                t_supply_c=supply_temperature_in_celsius,
                t_return_c=inlet_temperature_in_celsius,
            ),
        )

    def operating_point(
        self, *, inlet_temperature_in_celsius: float, ambient_temperature_in_celsius: float
    ) -> CollectorOperatingPoint:
        """Return the heat, flow and lift the collector field runs at with its pump, at an inlet in this step's sun.

        It is :meth:`SpeedControlledSolarPump.operating_point` with the collector's curve and area at the irradiance
        on the collector plane of the step last calculated (:attr:`plane_of_array_irradiance_w_m2`). Without heat the
        point is idle, and the circuit then moves no water.

        Args:
            inlet_temperature_in_celsius: The collector's inlet temperature, in °C.
            ambient_temperature_in_celsius: The outside air temperature, in °C.

        Returns:
            The operating point.
        """
        return self.pump.operating_point(
            curve=self.collector_curve,
            inlet_temperature_in_celsius=inlet_temperature_in_celsius,
            ambient_temperature_in_celsius=ambient_temperature_in_celsius,
            irradiance_in_watt_per_m2=float(self.plane_of_array_irradiance_w_m2),
            area_m2=self.area_m2,
        )

    def sensor_temperature_at_start_of_step_in_celsius(
        self,
        *,
        inlet_temperature_at_start_of_step_in_celsius: float,
        ambient_temperature_in_celsius: float,
        pump_ran_in_previous_step: bool,
    ) -> float:
        """Return the temperature the collector's sensor reads at the start of the step, in °C.

        While the pump stood in the previous step no water flows through the collector, and the sensor reads its
        stagnation temperature in this step's irradiance
        (:meth:`FlatPlateCollectorCurve.stagnation_temperature_in_celsius`). While the pump ran the sensor reads the
        outlet: the inlet at the tank's start-of-step temperature plus the lift of the collector's operating point at
        that inlet (:meth:`operating_point`), which is 0 when the collector has no heat there. For example, at
        200 W/m² in 20 °C air a standing pump's sensor reads 60.9 °C with the preset curve; a running pump's sensor on
        a 45 °C tank reads 53.5 °C with the preset pump. Both are start-of-step quantities: no pass of the step
        moves them, so the pump's own heat in the step does not switch it.

        Args:
            inlet_temperature_at_start_of_step_in_celsius: The tank's start-of-step temperature, the inlet's, in °C.
            ambient_temperature_in_celsius: The outside air temperature, in °C.
            pump_ran_in_previous_step: Whether the pump ran in the previous step, the collector's saved state.

        Returns:
            The sensor temperature, in °C.
        """
        if not pump_ran_in_previous_step:
            return self.collector_curve.stagnation_temperature_in_celsius(
                ambient_temperature_in_celsius=ambient_temperature_in_celsius,
                irradiance_in_watt_per_m2=float(self.plane_of_array_irradiance_w_m2),
            )
        operating_point = self.operating_point(
            inlet_temperature_in_celsius=inlet_temperature_at_start_of_step_in_celsius,
            ambient_temperature_in_celsius=ambient_temperature_in_celsius,
        )
        return inlet_temperature_at_start_of_step_in_celsius + operating_point.lift_in_kelvin

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Run the collector at the storage's step mean and publish its circuit, its heat and the pump's electricity.

        The irradiance on the collector plane is computed once per step from the weather and the sun's position;
        the collector heat at the step mean, which moves while the step iterates, on every call. The collector also
        publishes its sensor temperature at the start of the step
        (:meth:`sensor_temperature_at_start_of_step_in_celsius`), the quantity its controller switches the pump on,
        and keeps whether its pump ran as its state.
        """
        # get inputs
        control_signal = stsv.get_input_value(self.control_signal_channel)
        global_horizontal_irradiance_w_m2 = stsv.get_input_value(self.ghi_channel)
        diffuse_horizontal_irradiance_w_m2 = stsv.get_input_value(self.dhi_channel)
        ambient_air_temperature_deg_c = stsv.get_input_value(self.t_out_channel)
        apparent_zenith_in_degrees = stsv.get_input_value(self.apparent_zenith_channel)
        solar_azimuth_in_degrees = stsv.get_input_value(self.azimuth_channel)
        temperature_collector_inlet_deg_c = stsv.get_input_value(self.water_temperature_input_channel)
        inlet_temperature_at_start_of_step_in_celsius = stsv.get_input_value(
            self.inlet_temperature_at_start_of_step_input_channel
        )
        # The collector is calculated every timestep, from this timestep's weather and this
        # timestep's storage temperature, and with the weather's sun. The two stages below are the body
        # of oemof.thermal's flat_plate_precalc without its first stage, the sun's position, which the
        # weather publishes -- see i_prepare_simulation.
        # Some more info on the equation:
        # http://www.estif.org/solarkeymarknew/the-solar-keymark-scheme-rules/21-certification-bodies/certified-products/58-collector-performance-parameters #noqa
        # The irradiance on the collector plane needs the weather and the sun only, so it is worked
        # out once per timestep and shared by the timestep's convergence iterations.
        plane_of_array_key = (
            timestep,
            global_horizontal_irradiance_w_m2,
            diffuse_horizontal_irradiance_w_m2,
            apparent_zenith_in_degrees,
            solar_azimuth_in_degrees,
        )
        if plane_of_array_key != self.plane_of_array_key:
            self.plane_of_array_irradiance_w_m2 = plane_of_array_irradiance_w_m2(
                surface_tilt=self.config.tilt,
                surface_azimuth=self.config.azimuth,
                apparent_zenith=apparent_zenith_in_degrees,
                solar_azimuth=solar_azimuth_in_degrees,
                global_horizontal_irradiance_w_m2=global_horizontal_irradiance_w_m2,
                diffuse_horizontal_irradiance_w_m2=diffuse_horizontal_irradiance_w_m2,
            )
            self.plane_of_array_key = plane_of_array_key
        # the efficiency depends on the collector's inlet temperature, the storage's step mean, which moves while the
        # step iterates, so it is calculated every call
        operating_point = self.operating_point(
            inlet_temperature_in_celsius=temperature_collector_inlet_deg_c,
            ambient_temperature_in_celsius=ambient_air_temperature_deg_c,
        )
        sensor_temperature_at_start_of_step_in_celsius = self.sensor_temperature_at_start_of_step_in_celsius(
            inlet_temperature_at_start_of_step_in_celsius=inlet_temperature_at_start_of_step_in_celsius,
            ambient_temperature_in_celsius=ambient_air_temperature_deg_c,
            pump_ran_in_previous_step=self.previous_state.pump_ran,
        )
        pump_runs = control_signal != 0
        circuit = self.collector_circuit(
            operating_point=operating_point,
            inlet_temperature_in_celsius=temperature_collector_inlet_deg_c,
            pump_runs=pump_runs,
        )
        pump_ratio_run = 0.0
        if pump_runs:
            pump_ratio_run = self.part_load_command.ratio(stsv)
            circuit = circuit.at_part_load(part_load_ratio=pump_ratio_run, t_return_c=temperature_collector_inlet_deg_c)
        self.part_load_command.publish_ratio_run(stsv, pump_ratio_run)
        self.state = SolarThermalSystemState(pump_ran=pump_ratio_run > 0.0)
        thermal_power_output_w = circuit.power_w
        thermal_energy_output_wh: float = (
            thermal_power_output_w
            * self.my_simulation_parameters.seconds_per_timestep
            / hydronics.UnitConversion.JOULES_PER_WATT_HOUR
        )
        # the solar pump draws its electricity for the part of the step it runs
        electric_power_demand_solar_pump_w = pump_ratio_run * self.pump_power_in_watt(
            pump_runs=pump_runs, is_old_pump=self.config.old_solar_pump
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
            self.sensor_temperature_at_start_of_step_output_channel, sensor_temperature_at_start_of_step_in_celsius
        )
        stsv.set_output_value(
            self.electricity_consumption_output_channel,
            electric_power_demand_solar_pump_w,
        )
        solar_power_on_collector_w = float(self.plane_of_array_irradiance_w_m2) * self.area_m2
        stsv.set_output_value(self.solar_power_on_collector_channel, solar_power_on_collector_w)
        stsv.set_output_value(self.collector_heat_loss_channel, solar_power_on_collector_w - thermal_power_output_w)
        stsv.set_output_value(self.solar_pump_heat_loss_channel, electric_power_demand_solar_pump_w)


@dataclass(frozen=True)
class SolarThermalSystemState:
    """The collector's state: whether its pump ran.

    The collector keeps two of them: the state of the step being iterated, as the latest pass has it, and the saved
    state, the one the previous step ended with. The saved state chooses what the collector's sensor reads at the start
    of the step: the stagnation temperature after a step the pump stood, the outlet after a step it ran.
    """

    #: Whether the pump ran: the controller signalled it on and the collector ran a part-load ratio above 0.
    pump_ran: bool


@dataclass_json
@dataclass
class SolarThermalSystemControllerConfig(ConfigBase):
    """Configuration of the solar thermal system's controller: when the solar pump runs.

    The controller switches the pump on when the collector's sensor reads more than 7 K above the tank, keeps it
    running while the sensor reads more than 3 K above the tank, stops it once the vessel has passed its 80 °C maximum
    storage temperature and restarts it only 3 K below that maximum. These rules are the controller's own, so the
    configuration holds only the controller's identity. The named default is :meth:`preset_standard`::

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

    It is a differential controller: the on/off decision compares two temperatures its sensors read at the start of
    the step, as a real solar controller decides on the temperatures it measures at that moment. They are the
    collector's sensor (the collector's ``CollectorSensorTemperatureAtStartOfStepInCelsius``: its stagnation
    temperature after a step the pump stood, its outlet after a step it ran) and the storage's start-of-step
    temperature. No pass of the step moves either, so the pump's own heat cannot switch it off again in the step it
    switches on. One exception: the simulator starts each step from the values the previous step
    converged to and evaluates the controller before the collector and the storage, so the first pass of a step reads
    the previous step's sensors and may decide differently; from the second pass on the decision cannot change, and
    the step keeps the decision of its last pass. Two hysteresis bands keep the pump from switching every few steps:

    * the temperature difference: a pump that stood in the previous step switches on when the collector's sensor
      reads more than :attr:`SWITCH_ON_TEMPERATURE_DIFFERENCE_IN_KELVIN` (7 K) above the storage, a pump that ran
      keeps running while it reads more than :attr:`SWITCH_OFF_TEMPERATURE_DIFFERENCE_IN_KELVIN` (3 K) above it;
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
    #: The temperature the collector's sensor reads at the start of the step, which the pump switches on against the
    #: storage's.
    CollectorSensorTemperatureAtStartOfStepInCelsius: ClassVar[str] = "CollectorSensorTemperatureAtStartOfStepInCelsius"

    # Outputs
    ControlSignalToSolarThermalSystem: ClassVar[str] = "ControlSignalToSolarThermalSystem"
    #: The fraction of the step the pump runs.
    PartLoadRatio: ClassVar[str] = "PartLoadRatio"
    #: The storage temperature above which the controller stops the pump, its maximum storage temperature.
    TargetTemperatureInCelsius: ClassVar[str] = "TargetTemperatureInCelsius"

    #: How far the collector's sensor must read above the storage for a pump that stood in the previous step to
    #: switch on, in K. Differential solar controllers switch on at a collector-to-tank difference of a few kelvin,
    #: adjustable from about 2 to 16 K, with factory settings of about 6 K (RESOL DeltaSol A and DeltaSol ES manuals,
    #: https://www.heizungsdiscount24.de/pdf/Resol-DeltaSol-A-Anleitung.pdf). The owner chose 7 K and 3 K on
    #: 2026-10-14.
    SWITCH_ON_TEMPERATURE_DIFFERENCE_IN_KELVIN: ClassVar[float] = 7.0

    #: How far the collector's sensor must read above the storage for a pump that ran in the previous step to keep
    #: running, in K. Below it the heat gained no longer pays for the pipe losses and the pump's electricity;
    #: differential controllers switch off at about 3 to 4 K (the DeltaSol ES's factory setting is 4 K). Without the
    #: band between the two differences a pump whose start cools the sensor would switch off and on every few steps.
    SWITCH_OFF_TEMPERATURE_DIFFERENCE_IN_KELVIN: ClassVar[float] = 3.0

    #: The maximum storage temperature: the storage temperature above which the pump stops, in °C. It is the solar
    #: controller's own limit, above the 60 °C warm-water aim the backup generators (boiler, heat pump, district
    #: heating, electric heater) charge to, and above the 68 °C the heat pump charges to while the energy manager
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

        self.collector_sensor_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.CollectorSensorTemperatureAtStartOfStepInCelsius,
            loadtypes.LoadTypes.TEMPERATURE,
            loadtypes.Units.CELSIUS,
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
            aggregation=loadtypes.OutputAggregation.MEAN,
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
        """Return the connections from the collector: its sensor temperature at the step start and its pump's ratio run.

        Returns:
            The connection list.
        """

        connections: List[ComponentConnection] = []
        collector_classname = SolarThermalSystem.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystemController.CollectorSensorTemperatureAtStartOfStepInCelsius,
                collector_classname,
                SolarThermalSystem.CollectorSensorTemperatureAtStartOfStepInCelsius,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystemController.PartLoadRatioRun,
                collector_classname,
                SolarThermalSystem.PartLoadRatioRun,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Save the pump state the step that just ended was left with, from its last pass, as the state to start from.

        The simulator calls this at the start of every step, so the saved state is whether the pump ran and whether
        the full-storage stop held in the previous step: the two hysteresis bands of :meth:`pump_runs` and
        :meth:`full_storage_stop_holds` read it. The pump counts as having run only if the collector ran a part-load
        ratio above 0, which is what the collector reports and what a forced pass keeps.
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
        :meth:`pump_runs` from the collector's sensor and the storage's start-of-step temperature, the full-storage
        stop and whether the pump ran in the previous step; the state saves the pump as running only with a ratio
        above 0, as the collector will report it. Under ``force_convergence`` the controller keeps the pump as the
        collector reports it ran (:meth:`pump_ran_as_reported`) and its part-load ratio becomes the ratio run, so the
        device keeps running what it ran and the iteration stops moving. All of it reads only inputs and the saved
        state. The inputs are start-of-step quantities, so from the second pass on an unforced pass decides as the
        pass before it; the first pass, which reads the previous step's sensors, may decide differently.
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
            stsv.set_output_value(self.control_signal_to_solar_thermal_system_channel, 1 if pump_runs else 0)
            self.part_load.publish_held(stsv)
            pump_ran = pump_runs
        else:
            pump_runs = self.pump_runs(
                collector_sensor_temperature_in_celsius=stsv.get_input_value(
                    self.collector_sensor_temperature_input_channel
                ),
                storage_temperature_at_start_in_celsius=storage_temperature_at_start_in_celsius,
                full_storage_stop_holds=full_storage_stop_holds,
                pump_ran_in_previous_step=self.previous_state.pump_runs,
            )
            stsv.set_output_value(self.control_signal_to_solar_thermal_system_channel, 1 if pump_runs else 0)
            ratio = self.part_load.publish(
                stsv,
                device_runs=pump_runs,
                start_temperature_in_celsius=storage_temperature_at_start_in_celsius,
                target_temperature_in_celsius=self.MAXIMUM_STORAGE_TEMPERATURE_IN_CELSIUS,
            )
            pump_ran = pump_runs and ratio > 0.0
        self.state = SolarThermalSystemControllerState(
            pump_runs=pump_ran, full_storage_stop_holds=full_storage_stop_holds
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
        collector_sensor_temperature_in_celsius: float,
        storage_temperature_at_start_in_celsius: float,
        full_storage_stop_holds: bool,
        pump_ran_in_previous_step: bool,
    ) -> bool:
        """Return whether the solar pump runs in a step, from the two sensors, the full-storage stop and its state.

        A pump that stood in the previous step switches on when the collector's sensor reads more than
        :attr:`SWITCH_ON_TEMPERATURE_DIFFERENCE_IN_KELVIN` (7 K) above the storage's start-of-step temperature; a pump
        that ran keeps running while it reads more than :attr:`SWITCH_OFF_TEMPERATURE_DIFFERENCE_IN_KELVIN` (3 K)
        above it. Either way the pump stands while the full-storage stop (:meth:`full_storage_stop_holds`) holds. For
        example, a collector sensor 5 K above the storage keeps a running pump on, does not switch on a pump that
        stood, and runs neither while the full-storage stop holds.

        Args:
            collector_sensor_temperature_in_celsius: The collector's sensor temperature at the start of the step,
                in °C.
            storage_temperature_at_start_in_celsius: The storage's start-of-step temperature, in °C.
            full_storage_stop_holds: Whether the full-storage stop holds the pump in this step.
            pump_ran_in_previous_step: Whether the pump ran in the previous step, the controller's saved state.

        Returns:
            True while the pump runs.
        """
        if full_storage_stop_holds:
            return False
        if pump_ran_in_previous_step:
            least_difference_in_kelvin = SolarThermalSystemController.SWITCH_OFF_TEMPERATURE_DIFFERENCE_IN_KELVIN
        else:
            least_difference_in_kelvin = SolarThermalSystemController.SWITCH_ON_TEMPERATURE_DIFFERENCE_IN_KELVIN
        difference_in_kelvin = collector_sensor_temperature_in_celsius - storage_temperature_at_start_in_celsius
        return difference_in_kelvin > least_difference_in_kelvin

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
    switch-on or switch-off temperature difference, and whether the storage must fall 3 K below the maximum storage
    temperature before the pump runs again.
    """

    #: Whether the pump runs: signalled on with a part-load ratio above 0.
    pump_runs: bool

    #: Whether the full-storage stop holds the pump: set once the storage started a step above the maximum storage
    #: temperature, released once it started a step at least 3 K below it.
    full_storage_stop_holds: bool
