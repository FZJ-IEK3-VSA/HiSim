"""Generic Boiler Module.

This heating system can use different energy carriers (natural, gas, oil, hydrogen, ...) for combustion.
The Generic boiler chooses between two modes: a) a conventional boiler (Heizwertheizung) representing older boilers and
b) a condensing boiler (Brennwertheizung) which is more modern and uses heat not only from the combustion process but also from waste gases
which is why it has higher efficiencies than the conventional boiler.
https://www.vaillant.co.uk/advice/understanding-heating-technology/boilers/what-is-a-Generic-boiler/.

The Generic boiler controller can be set as modulating controller (which is often used)
and as non-modulating on_off controller (which is used especially for pellet and wood chip heating).
"""

# Owned
from dataclasses import dataclass, field
import math
from typing import ClassVar, Dict, List, Optional, Tuple
from enum import Enum, unique
import pandas as pd
from dataclasses_json import dataclass_json

from hisim import hydronics
from hisim import log
from hisim import loadtypes as lt
from hisim.part_load import PartLoadCommand, PartLoadControl, StartOfStepPartLoadControl
from hisim.components.configuration import (
    PhysicsConfig,
)
from hisim.component import (
    L1ControllerComponent,
    PhysicsComponent,
    ComponentConnection,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
    OpexCostDataClass,
    CapexCostDataClass,
)
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    FactContribution,
    Self,
    Sizable,
    Size,
    SizingContext,
    SizingLaw,
    concrete,
    law,
    preset,
    sized_field,
)
from hisim.energy_port import EnergyPort
from hisim.subcomponents.base import Subcomponent
from hisim.subcomponents.pumps import ControllerLiftPump, PumpOperatingPoint
from hisim.components.dual_circuit_system import (
    DiverterValve,
    HotWaterPriorityLimit,
    HotWaterPriorityTimer,
    HeatingMode,
    SetTemperatureConfig,
)
from hisim.components.simple_water_storage import (
    SimpleHotWaterStorage,
    SimpleDHWStorage,
)
from hisim.components.heating_curve import HeatingCurveController
from hisim.components.configuration import (
    EmissionFactorsAndCostsForFuelsConfig,
)
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import (
    KpiEntry,
    KpiTagEnumClass,
)
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.economics.facts import CostRelevance


@unique
class BoilerType(str, Enum):
    """Set Boiler Types.

    CONVENTIONAL uses only the heat of combustion, i.e. the lower heating value
    of the fuel; CONDENSING additionally recovers the heat of the water vapour
    in the waste gases, i.e. the higher heating value. Every member carries its
    own name as its value so that a serialized boiler configuration names the
    type explicitly instead of encoding it as an integer.
    """

    CONVENTIONAL = "CONVENTIONAL"
    CONDENSING = "CONDENSING"


@dataclass_json
@dataclass
class GenericBoilerConfig(ConfigBase):
    """Configuration of the GenericBoiler class.

    Named default variants are the ``preset_*`` classmethods below, one per fuel plus the
    nominal catalogue devices. The power fields are sizable: a fuel preset leaves them ``AUTO``
    and ``.resolve(ctx)`` scales them to the building, while a ``*_12kw`` preset pins both to
    the rated device.
    """

    MAIN_CLASS = "hisim.components.generic_boiler.GenericBoiler"

    @staticmethod
    def scale_thermal_power(
        heating_load_of_building_in_watt: float,
        number_of_apartments_in_building: Optional[float],
    ) -> float:
        """Scales the boiler's maximal thermal power to the building.

        The boiler must cover the larger of the space-heating load and the domestic hot
        water demand (approximated as 2.5 kW per apartment), plus ten percent when it
        serves both purposes at once.
        """
        maximal_thermal_power_in_watt_sh = heating_load_of_building_in_watt
        maximal_thermal_power_in_watt_dhw = (
            2500 * number_of_apartments_in_building if number_of_apartments_in_building is not None else 0
        )

        maximal_thermal_power_in_watt = max(maximal_thermal_power_in_watt_sh, maximal_thermal_power_in_watt_dhw)
        if maximal_thermal_power_in_watt_dhw > 0 and maximal_thermal_power_in_watt_sh > 0:
            maximal_thermal_power_in_watt *= 1.1  # add 10% when used for both SH and DHW
        return maximal_thermal_power_in_watt

    @staticmethod
    def heating_value_in_joule_per_m3(energy_carrier: lt.LoadTypes, boiler_type: "BoilerType") -> float:
        """Returns the heating value of the fuel the way this boiler type exploits it.

        A condensing boiler recovers the latent heat of the flue gas and is therefore
        rated on the higher heating value; a conventional one loses it and is rated on the
        lower. Which of the two applies is a property of the configured device, so the
        choice lives here rather than in the component that later divides by it.

        Args:
            energy_carrier: The fuel burnt.
            boiler_type: Condensing or conventional.

        Returns:
            The heating value in joule per cubic metre.

        Raises:
            ValueError: If the carrier has no entry in ``PhysicsConfig`` (district heating
                burns nothing), or if the boiler type is neither of the two.
        """
        properties = PhysicsConfig.get_properties_for_energy_carrier(energy_carrier=energy_carrier)
        if boiler_type == BoilerType.CONDENSING:
            return properties.higher_heating_value_in_joule_per_m3
        if boiler_type == BoilerType.CONVENTIONAL:
            return properties.lower_heating_value_in_joule_per_m3
        raise ValueError(f"Boiler type {boiler_type} is not implemented.")

    @classmethod
    def fuel_constants(
        cls, energy_carrier: lt.LoadTypes, boiler_type: "BoilerType"
    ) -> Tuple[Optional[float], Optional[float]]:
        """Returns the fuel's heating value in kWh per litre and its density in kg per m3.

        The pair the fuel meter needs to turn kilowatt hours back into litres and
        kilograms. It is derived at *build* time rather than inside the component, so a
        meter can read the same two numbers off the generator's configuration through the
        sizing facts instead of repeating them — a gas boiler beside an oil meter is what
        the repetition used to produce.

        District heating is the one carrier that burns nothing: it has no heating value
        and no fuel density, and both are ``None`` rather than a stand-in number that
        would be accounted as litres of something.

        Args:
            energy_carrier: The fuel burnt.
            boiler_type: Condensing or conventional, which picks the heating value.

        Returns:
            ``(heating value in kWh/l, density in kg/m3)``, both ``None`` for district heating.
        """
        if energy_carrier == lt.LoadTypes.DISTRICTHEATING:
            return None, None
        # J = kWh/(3.6 * 1e6) and m3 = 1e3 l
        heating_value_in_kwh_per_liter = cls.heating_value_in_joule_per_m3(energy_carrier, boiler_type) / (3.6 * 1e9)
        density_in_kg_per_m3 = PhysicsConfig.get_properties_for_energy_carrier(
            energy_carrier=energy_carrier
        ).density_in_kg_per_m3
        return heating_value_in_kwh_per_liter, density_in_kg_per_m3

    #: Sizing law of the maximal thermal power: cover space heating or DHW, whichever is
    #: larger (see :meth:`scale_thermal_power`). Named as a ClassVar so that the field
    #: declaration reads as one line and the law can be described in one place.
    MAXIMAL_POWER_LAW: ClassVar[SizingLaw] = law(
        lambda ctx: GenericBoilerConfig.scale_thermal_power(ctx.heating_load_in_watt, ctx.number_of_apartments),
        reads=("heating_load_in_watt", "number_of_apartments"),
        description=(
            "max(Size.HEATING_LOAD_IN_WATT, 2500 * Size.NUMBER_OF_APARTMENTS)," " times 1.1 when both are above zero"
        ),
    )

    component_id: ComponentID
    energy_carrier: lt.LoadTypes
    boiler_type: BoilerType
    minimal_thermal_power_in_watt: Sizable[float] = sized_field(rule=0.0)
    maximal_thermal_power_in_watt: Sizable[float] = sized_field(
        rule=MAXIMAL_POWER_LAW,
        unit=lt.Units.WATT,
        note="the larger of the building's heating load and 2.5 kW per apartment, plus 10 % when it serves both",
    )
    #: The Ecodesign seasonal space heating efficiency ``eta_s`` of the boiler's energy label, dimensionless, on the
    #: gross calorific value and after the corrections the regulations subtract (Regulation (EU) 813/2013 for gas and
    #: oil, 2015/1189 for solid fuel), or ``None``. When stated, the boiler type's efficiency curve is scaled so that
    #: its ``0.85 eta_30 + 0.15 eta_100`` minus the 3 % temperature-control correction reproduces it
    #: (:meth:`BoilerEfficiencySources.curve`); when ``None``, the curve applies unscaled. For example, 0.90 for a
    #: label stating 90 %. The config refuses a value above 0.97; the build refuses one above the boiler's own
    #: ceiling (:meth:`BoilerEfficiencySources.highest_seasonal_efficiency`), which keeps the scaled curve at or below
    #: 1 on the basis its fuel is booked on, for example 0.8682 for a current conventional gas boiler (NCV).
    seasonal_efficiency: Optional[float] = None
    #: The year the boiler was installed, or ``None`` for a current boiler. A year before 2000 picks the IWU 2002 row
    #: of its decade for a gas or oil boiler; ``None`` or a later year takes a current product's data
    #: (:class:`BoilerVintage`). Pellet and wood-chip boilers do not depend on it.
    installation_year: Optional[int] = None
    temperature_delta_in_celsius: float = 20.0
    #: The highest supply temperature the boiler delivers, °C. A charge whose return plus lift would exceed it is
    #: throttled to it. 80 °C is the usual upper flow-temperature setting of domestic
    #: gas, oil and solid-fuel boilers, whose safety high-limit cut-outs act above it (around 90-110 °C, EN 15502-1,
    #: EN 303-5); it lies above the controller's 70 °C hot-water flow aim, so every charge reaches its set point.
    maximal_flow_temperature_in_celsius: float = field(default=80.0, metadata={"unit": lt.Units.CELSIUS})
    #: The smallest rise from return to supply the boiler's pump allows at maximal burner power and the 60 °C
    #: full-load test return, K. The pump's largest flow carries the maximal heat there, the maximal power times the
    #: curve's full-load efficiency ``eta_100``, over this rise: ``m_max = P_max eta_100 / (c rise)``; a condensing
    #: boiler on a colder return makes more heat and so rises slightly more. A lift the controller asks for that is
    #: smaller than the rise the commanded heat gives at that flow is raised to it, so the supply ends above the one
    #: asked for. For example, a 12 kW current condensing gas boiler (``eta_100`` = 0.877) pumps at most
    #: 12000 * 0.877 / (4180 * 5) = 0.50 kg/s (1.8 m3/h) and lifts its water by at least 5 K at full power and by
    #: about 2.5 K at half power on a 60 °C return (6000 W at 0.885). Why 5 K: it bounds the flow of an on/off
    #: burner, or of one held at its minimal power, on a lift close to 0 K. A modulating controller (the
    #: ``modulating`` preset) commands full power only on lifts of 5 K and more and a power proportional to the lift
    #: below, so it reaches the bound only where it holds the burner at its minimal power: a 12 kW boiler with a 1 kW
    #: minimum (the ``*_12kw`` presets) turns that into 890 to 980 W (the gas curve's 30 % value on a 60 to a 30 °C
    #: return), which the largest flow carries over about 0.45 K, so a lift below that is raised. A boiler without a
    #: minimal power never reaches it. A minimal lift above the controller's
    #: ``set_temperature_difference_for_full_power`` would raise lifts on which that controller commands full power,
    #: so the supply would end above the one it asked for: keep it at or below that threshold.
    minimal_lift_at_maximal_power_in_kelvin: float = field(default=5.0, metadata={"unit": lt.Units.KELVIN})
    device_co2_footprint_in_kg: Optional[float] = None
    investment_costs_in_euro: Optional[float] = None
    lifetime_in_years: Optional[float] = None
    maintenance_costs_in_euro_per_year: Optional[float] = None
    subsidy_as_percentage_of_investment_costs: Optional[float] = None
    consumption_in_kilowatt_hour: float = 0.0

    def __post_init__(self) -> None:
        """Refuse an impossible seasonal efficiency or installation year, and an unusable maximum flow or lift.

        The burner's fuel is its heat over its combustion efficiency, which a stated seasonal efficiency scales. A
        seasonal efficiency of 0 or less would divide by zero; one above 0.97 is not an energy label's ``eta_s``,
        since the curve's active-mode efficiency, ``eta_s`` plus the 3 % temperature-control correction, cannot exceed
        1 on the gross calorific value. An installation year must be a whole year. A maximum flow temperature that is
        not a finite positive number of degrees lies below every return, so the boiler would silently never fire. A
        minimal lift of 0 K or less gives the pump no largest flow, so a burner at full power on a lift close to 0 K
        would pump without bound. For example, the defaults (no seasonal efficiency, no year, 80 °C, 5 K) and
        ``seasonal_efficiency=0.9`` are accepted, ``seasonal_efficiency=1.04``, ``maximal_flow_temperature_in_celsius=0``
        and ``minimal_lift_at_maximal_power_in_kelvin=0`` are refused.

        Raises:
            ValueError: If ``seasonal_efficiency`` is not finite or not in (0, 0.97], if ``installation_year`` is not
                a whole number, if ``maximal_flow_temperature_in_celsius`` is not finite or not above 0 °C, or if
                ``minimal_lift_at_maximal_power_in_kelvin`` is not finite or not above 0 K.
        """
        if self.seasonal_efficiency is not None:
            highest_seasonal_efficiency = 1.0 - BoilerEfficiencySources.TEMPERATURE_CONTROL_CORRECTION
            if (
                not math.isfinite(self.seasonal_efficiency)
                or self.seasonal_efficiency <= 0.0
                or self.seasonal_efficiency > highest_seasonal_efficiency
            ):
                raise ValueError(
                    f"The boiler's seasonal_efficiency must be the Ecodesign eta_s on the gross calorific value, a "
                    f"finite number above 0 and at most {highest_seasonal_efficiency:g}, got "
                    f"{self.seasonal_efficiency!r}: eta_s plus the 3 % temperature-control correction cannot exceed 1."
                )
        if self.installation_year is not None and (
            isinstance(self.installation_year, bool) or int(self.installation_year) != self.installation_year
        ):
            raise ValueError(f"The boiler's installation_year must be a whole year, got {self.installation_year!r}.")
        if (
            not math.isfinite(self.maximal_flow_temperature_in_celsius)
            or self.maximal_flow_temperature_in_celsius <= 0.0
        ):
            raise ValueError(
                f"The boiler's maximal_flow_temperature_in_celsius must be a finite temperature above 0 °C, got "
                f"{self.maximal_flow_temperature_in_celsius!r}: below the return the boiler would never fire."
            )
        minimal_lift_in_kelvin = self.minimal_lift_at_maximal_power_in_kelvin
        if not math.isfinite(minimal_lift_in_kelvin) or minimal_lift_in_kelvin <= 0.0:
            raise ValueError(
                f"The boiler's minimal_lift_at_maximal_power_in_kelvin must be a finite rise above 0 K, got "
                f"{minimal_lift_in_kelvin!r}: it sets the pump's largest flow, which 0 K would leave unbounded."
            )

    @staticmethod
    def sizing_facts(config: "GenericBoilerConfig", ctx: SizingContext) -> dict:
        """Contributes the boiler's resolved power band and fuel for the components around it.

        Runs after the boiler itself resolved, so the values are the final concrete numbers
        whether they came from a law, a preset constant or an override.

        The fuel half — the carrier and the two constants :meth:`fuel_constants` derives from it
        and from the boiler type — is what the gas and fuel meters copy instead of repeating, so
        a meter accounting this boiler's consumption cannot state a different fuel from the one
        it burns. Both constants are ``None`` for district heating, which burns nothing.

        Args:
            config: this boiler configuration, fully resolved.
            ctx: the sizing context; unused, every value is this config's own.

        Returns:
            dict: the five facts named in :attr:`SIZING_CONTRIBUTIONS`.
        """
        del ctx
        heating_value_in_kwh_per_liter, density_in_kg_per_m3 = GenericBoilerConfig.fuel_constants(
            config.energy_carrier, config.boiler_type
        )
        return {
            "maximal_thermal_power_in_watt": concrete(config.maximal_thermal_power_in_watt),
            "minimal_thermal_power_in_watt": concrete(config.minimal_thermal_power_in_watt),
            "energy_carrier": config.energy_carrier,
            "heating_value_of_fuel_in_kwh_per_liter": heating_value_in_kwh_per_liter,
            "fuel_density_in_kg_per_m3": density_in_kg_per_m3,
        }

    #: Sizing facts this config contributes: its resolved power band, for consumers that size
    #: from this boiler (its controller), and its fuel — the carrier plus the two constants
    #: derived from it — for the meter that accounts what it burns. With two boilers in one
    #: scenario each is addressable as "<its name>.maximal_thermal_power_in_watt" and a consumer
    #: must say which one it means.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(
            facts=(
                "maximal_thermal_power_in_watt",
                "minimal_thermal_power_in_watt",
                "energy_carrier",
                "heating_value_of_fuel_in_kwh_per_liter",
                "fuel_density_in_kg_per_m3",
            ),
            compute=sizing_facts,
        ),
    )

    @preset
    @classmethod
    def preset_condensing_gas(cls, name: str) -> "GenericBoilerConfig":
        """Condensing gas boiler, scaled to the building it heats."""
        return cls(
            component_id=ComponentID(name=name),
            energy_carrier=lt.LoadTypes.GAS,
            boiler_type=BoilerType.CONDENSING,
        )

    @preset(note="nominal catalogue device")
    @classmethod
    def preset_condensing_gas_12kw(cls, name: str) -> "GenericBoilerConfig":
        """Condensing gas boiler pinned to the 12 kW nominal device."""
        return cls(
            component_id=ComponentID(name=name),
            energy_carrier=lt.LoadTypes.GAS,
            boiler_type=BoilerType.CONDENSING,
            minimal_thermal_power_in_watt=1000.0,
            maximal_thermal_power_in_watt=12000.0,
        )

    @preset
    @classmethod
    def preset_oil(cls, name: str) -> "GenericBoilerConfig":
        """Conventional oil boiler, scaled to the building it heats."""
        return cls(
            component_id=ComponentID(name=name),
            energy_carrier=lt.LoadTypes.OIL,
            boiler_type=BoilerType.CONVENTIONAL,
        )

    @preset(note="nominal catalogue device")
    @classmethod
    def preset_oil_12kw(cls, name: str) -> "GenericBoilerConfig":
        """Conventional oil boiler pinned to the 12 kW nominal device."""
        return cls(
            component_id=ComponentID(name=name),
            energy_carrier=lt.LoadTypes.OIL,
            boiler_type=BoilerType.CONVENTIONAL,
            minimal_thermal_power_in_watt=1000.0,
            maximal_thermal_power_in_watt=12000.0,
        )

    @preset
    @classmethod
    def preset_pellets(cls, name: str) -> "GenericBoilerConfig":
        """Pellet boiler, scaled to the building, modulating down to a twelfth of its maximum."""
        return cls(
            component_id=ComponentID(name=name),
            energy_carrier=lt.LoadTypes.PELLETS,
            boiler_type=BoilerType.CONVENTIONAL,
            # per-preset law: a pellet boiler cannot modulate below a twelfth of its
            # maximal power, unlike the gas/oil default of zero. Reading the sibling
            # field keeps the two consistent even when an author pins the maximum.
            minimal_thermal_power_in_watt=Self("maximal_thermal_power_in_watt") * (1 / 12),
        )

    @preset
    @classmethod
    def preset_wood_chips(cls, name: str) -> "GenericBoilerConfig":
        """Wood chip boiler, scaled to the building, with the pellet modulation limit."""
        return cls(
            component_id=ComponentID(name=name),
            energy_carrier=lt.LoadTypes.WOOD_CHIPS,
            boiler_type=BoilerType.CONVENTIONAL,
            minimal_thermal_power_in_watt=Self("maximal_thermal_power_in_watt") * (1 / 12),
        )

    @preset
    @classmethod
    def preset_hydrogen(cls, name: str) -> "GenericBoilerConfig":
        """Condensing hydrogen boiler, scaled to the building it heats."""
        return cls(
            component_id=ComponentID(name=name),
            energy_carrier=lt.LoadTypes.GREEN_HYDROGEN,
            boiler_type=BoilerType.CONDENSING,
        )


@unique
class CalorificBasis(str, Enum):
    """The calorific value an efficiency is stated on: the gross (GCV) or the net (NCV) calorific value of the fuel.

    The gross calorific value (GCV, Brennwert, Hs) counts the heat the water vapour of the flue gas gives off when it
    condenses; the net calorific value (NCV, Heizwert, Hi) does not. The same boiler therefore has a higher efficiency
    on the NCV than on the GCV, by the fuel's ratio GCV/NCV (natural gas 1.109). Ecodesign product fiches state the
    GCV, IWU 2002 and most German test reports the NCV.
    """

    GROSS = "GROSS"
    NET = "NET"

    @classmethod
    def of_booking(cls, energy_carrier: lt.LoadTypes, boiler_type: BoilerType) -> "CalorificBasis":
        """Return the basis a boiler's fuel is booked on, the one its efficiency curve must be stated on.

        HiSim books a boiler's fuel in kWh of burner power and turns them into litres or cubic metres with
        :meth:`GenericBoilerConfig.heating_value_in_joule_per_m3`: the higher heating value for a condensing, the
        lower for a conventional boiler. A fuel whose two heating values are equal in ``PhysicsConfig`` (pellets and
        wood chips, which have only an NCV there) is booked on the NCV whatever the boiler type. For example, a
        condensing gas boiler is booked on the GCV, a conventional oil and every pellet boiler on the NCV.

        Args:
            energy_carrier: The fuel the boiler burns.
            boiler_type: Condensing or conventional.

        Returns:
            The calorific basis of the boiler's fuel.
        """
        properties = PhysicsConfig.get_properties_for_energy_carrier(energy_carrier=energy_carrier)
        has_gross_value = (
            properties.higher_heating_value_in_joule_per_m3 > properties.lower_heating_value_in_joule_per_m3
        )
        if boiler_type == BoilerType.CONDENSING and has_gross_value:
            return cls.GROSS
        return cls.NET


@unique
class BoilerVintage(str, Enum):
    """Which generation of boilers an efficiency row describes, chosen by the boiler's installation year.

    IWU 2002 tabulates existing boilers by decade of installation, the 1970s, 1980s and 1990s (Tab. 2 and Tab. 3).
    A boiler with no stated installation year, or one installed from 2000 on, is a current boiler and takes the
    efficiency of a current product's Ecodesign fiche (owner decision of 2026-10-20). For example, 1975 is
    ``SEVENTIES``, 1990 and 1999 are ``NINETIES``, 2000 and ``None`` are ``CURRENT``.
    """

    SEVENTIES = "SEVENTIES"
    EIGHTIES = "EIGHTIES"
    NINETIES = "NINETIES"
    CURRENT = "CURRENT"

    @classmethod
    def of_installation_year(cls, installation_year: Optional[int]) -> "BoilerVintage":
        """Return the vintage of a boiler installed in a year, or of a current boiler when the year is ``None``.

        Args:
            installation_year: The year the boiler was installed, or ``None`` when it is not stated.

        Returns:
            The vintage whose efficiency row the boiler takes.
        """
        if installation_year is None or installation_year >= InstallationYearBands.FIRST_YEAR_OF_CURRENT_BOILERS:
            return cls.CURRENT
        if installation_year >= InstallationYearBands.FIRST_YEAR_OF_THE_NINETIES:
            return cls.NINETIES
        if installation_year >= InstallationYearBands.FIRST_YEAR_OF_THE_EIGHTIES:
            return cls.EIGHTIES
        return cls.SEVENTIES


class InstallationYearBands:
    """The first installation year of each :class:`BoilerVintage`, a namespace of class constants.

    IWU 2002 groups existing boilers by decade ("70er / 80er / 90er Jahre", Tab. 2 and Tab. 3); its newest row is
    the 1990s, so a boiler installed from 2000 on is a current boiler (owner decision of 2026-10-20). A year before
    1980 takes the 1970s row.
    """

    #: First year of IWU 2002's "80er Jahre" rows.
    FIRST_YEAR_OF_THE_EIGHTIES: ClassVar[int] = 1980
    #: First year of IWU 2002's "90er Jahre" rows.
    FIRST_YEAR_OF_THE_NINETIES: ClassVar[int] = 1990
    #: First year a boiler takes a current product's Ecodesign fiche: IWU 2002's newest rows end with the 1990s.
    FIRST_YEAR_OF_CURRENT_BOILERS: ClassVar[int] = 2000


@dataclass(frozen=True)
class EfficiencyTestPoints:
    """One source's two test points of a boiler type: its efficiency at full load and at 30 % load.

    The full-load point is taken at a 60 °C return (80/60 °C, the "high-temperature regime" of Regulation (EU)
    813/2013, Annex II, Table 1 footnote, which is the 70 °C mean boiler water temperature of Directive 92/42/EEC and
    IWU 2002 Tab. 2). The 30 % point is taken at a 30 °C return for a condensing boiler (813/2013, same footnote; IWU
    2002 Tab. 3 states a 30 °C mean boiler temperature, read here as the return). A non-condensing boiler has no
    return-temperature term, so its test temperatures do not enter its curve. For example, the Vitodens 200-W fiche
    gives ``EfficiencyTestPoints(0.877, 0.978, CalorificBasis.GROSS, 0.0030)``.
    """

    #: The efficiency at full load (100 % of the rated output), dimensionless, on :attr:`calorific_basis`.
    efficiency_at_full_load: float
    #: The efficiency at 30 % load, dimensionless, on :attr:`calorific_basis`.
    efficiency_at_part_load: float
    #: The calorific value both efficiencies are stated on.
    calorific_basis: CalorificBasis
    #: The efficiency a condensing boiler gains per kelvin its return is below the full-load test return, on the
    #: GCV, per K; 0 for a non-condensing boiler.
    condensing_gain_on_gross_basis_per_kelvin: float = 0.0
    #: Whether the points are IWU 2002's 20 kW values that IWU's eq. 2-1 moves to the boiler's size.
    follows_size_law: bool = False


@dataclass(frozen=True)
class CombustionEfficiencyCurve(Subcomponent):
    """A boiler's combustion efficiency over its load and its return temperature, on its booking basis.

    The combustion efficiency is the heat into the water over the fuel power, on the calorific basis the boiler's
    fuel is booked on (:meth:`CalorificBasis.of_booking`). The load ratio ``beta`` is the burner (fuel) power over the
    maximal burner power. The curve passes through the two test points: ``eta_100`` at full load and the 60 °C test
    return, ``eta_30`` at 30 % load and the 30 °C test return. Between them it is linear in load, and a condensing
    boiler gains ``g`` per kelvin its return is below 60 °C (BRE, SAP 10 Technical Paper S10TP-12 issue 1.2, 2023:
    0.30 percentage points per kelvin on the GCV for gas, §4.5, and 0.20 for oil, Fig. 2):

    ``eta(beta, T) = eta_100 + (eta_30 - eta_100 - g (60 - 30)) (1 - beta) / 0.7 + g (60 - T)``

    with ``beta`` limited to ``[0.3, 1]`` and ``T`` to ``[30, 60]`` °C. Below 30 % load the burner keeps its 30 %
    value: the standards measure no lower point, and cycling losses are not modelled. Outside the two test returns
    the condensing gain stays constant: above 60 °C natural gas condenses almost nothing (dew point about 55 °C, oil
    about 47 °C), and below 30 °C both standards are silent. For example, the Vitodens curve (0.877, 0.978,
    g = 0.0030) gives ``0.877 + (0.978 - 0.877 - 0.09) * 0.5 / 0.7 + 0.0030 * 20 = 0.945`` at half load and a 40 °C
    return.
    """

    #: The return temperature of the full-load test point, in °C: 813/2013, Annex II, Table 1, footnote (80/60 °C).
    RETURN_TEMPERATURE_AT_FULL_LOAD_TEST_IN_CELSIUS: ClassVar[float] = 60.0
    #: The return temperature of a condensing boiler's 30 % test point, in °C: 813/2013, Annex II, Table 1, footnote.
    RETURN_TEMPERATURE_AT_PART_LOAD_TEST_IN_CELSIUS: ClassVar[float] = 30.0
    #: The load ratio of the part-load test point, dimensionless: 30 % of the rated output (92/42/EEC Art. 5,
    #: 813/2013 Annex II, 2015/1189 Annex I).
    PART_LOAD_TEST_RATIO: ClassVar[float] = 0.3
    #: The weight of the 30 % point in the active-mode seasonal efficiency, dimensionless: ``eta_son = 0,85 x eta_1
    #: + 0,15 x eta_4``, Commission communication 2014/C 207/02, point 4.3(a) (for 813/2013), and Regulation (EU)
    #: 2015/1189, Annex IV, point 4(b) (``eta_son = 0,85 x eta_p + 0,15 x eta_n``).
    SEASONAL_WEIGHT_OF_PART_LOAD: ClassVar[float] = 0.85
    #: The weight of the full-load point in the same sum, dimensionless.
    SEASONAL_WEIGHT_OF_FULL_LOAD: ClassVar[float] = 0.15

    #: ``eta_100``: the efficiency at full load and the full-load test return, dimensionless.
    efficiency_at_full_load: float
    #: ``eta_30``: the efficiency at 30 % load and the part-load test return, dimensionless.
    efficiency_at_part_load: float
    #: ``g``: the efficiency gained per kelvin of return below the full-load test return, per K; 0 when the boiler
    #: does not condense.
    condensing_gain_per_kelvin: float = 0.0

    def efficiency_at(self, load_ratio: float, return_temperature_in_celsius: float) -> float:
        """Return the combustion efficiency at a load ratio and a return temperature, dimensionless.

        Args:
            load_ratio: The burner power over the maximal burner power, dimensionless; limited to [0.3, 1].
            return_temperature_in_celsius: The return temperature of the circuit the burner fires, in °C; limited to
                the two test returns, [30, 60] °C.

        Returns:
            The share of the fuel power that becomes heat.
        """
        full_load_return_in_celsius = self.RETURN_TEMPERATURE_AT_FULL_LOAD_TEST_IN_CELSIUS
        part_load_return_in_celsius = self.RETURN_TEMPERATURE_AT_PART_LOAD_TEST_IN_CELSIUS
        test_return_difference_in_kelvin = full_load_return_in_celsius - part_load_return_in_celsius
        limited_load_ratio = min(max(load_ratio, self.PART_LOAD_TEST_RATIO), 1.0)
        limited_return_in_celsius = min(
            max(return_temperature_in_celsius, part_load_return_in_celsius), full_load_return_in_celsius
        )
        load_term = (
            (
                self.efficiency_at_part_load
                - self.efficiency_at_full_load
                - self.condensing_gain_per_kelvin * test_return_difference_in_kelvin
            )
            * (1.0 - limited_load_ratio)
            / (1.0 - self.PART_LOAD_TEST_RATIO)
        )
        return_term = self.condensing_gain_per_kelvin * (full_load_return_in_celsius - limited_return_in_celsius)
        return float(self.efficiency_at_full_load + load_term + return_term)

    def active_mode_seasonal_efficiency(self) -> float:
        """Return the Ecodesign active-mode seasonal efficiency of the curve, ``0.85 eta_30 + 0.15 eta_100``.

        It is the weighted average both regulations define for a fuel boiler (813/2013 through the communication
        2014/C 207/02, point 4.3(a); 2015/1189, Annex IV, point 4(b)), on the curve's own calorific basis. For
        example, the Vitodens curve gives ``0.85 * 0.978 + 0.15 * 0.877 = 0.963``.

        Returns:
            The active-mode seasonal efficiency, dimensionless.
        """
        return (
            self.SEASONAL_WEIGHT_OF_PART_LOAD * self.efficiency_at_part_load
            + self.SEASONAL_WEIGHT_OF_FULL_LOAD * self.efficiency_at_full_load
        )

    def highest_efficiency(self) -> float:
        """Return the highest efficiency anywhere on the curve, dimensionless.

        The curve is linear in both limited arguments, so its highest value lies on a corner of the domain: full or
        30 % load, at the 30 °C or the 60 °C return.

        Returns:
            The highest efficiency.
        """
        part_load_ratio = self.PART_LOAD_TEST_RATIO
        corners = [
            (load_ratio, return_in_celsius)
            for load_ratio in (part_load_ratio, 1.0)
            for return_in_celsius in (
                self.RETURN_TEMPERATURE_AT_PART_LOAD_TEST_IN_CELSIUS,
                self.RETURN_TEMPERATURE_AT_FULL_LOAD_TEST_IN_CELSIUS,
            )
        ]
        return max(self.efficiency_at(load_ratio, return_in_celsius) for load_ratio, return_in_celsius in corners)

    def scaled(self, factor: float) -> "CombustionEfficiencyCurve":
        """Return the curve multiplied by a factor everywhere: both test points and the condensing gain.

        Args:
            factor: The dimensionless factor, above 0.

        Returns:
            The scaled curve, whose efficiency at every load and return is ``factor`` times this one's.
        """
        return CombustionEfficiencyCurve(
            efficiency_at_full_load=self.efficiency_at_full_load * factor,
            efficiency_at_part_load=self.efficiency_at_part_load * factor,
            condensing_gain_per_kelvin=self.condensing_gain_per_kelvin * factor,
        )


class BoilerEfficiencySources:
    """The sourced test points of every boiler type and vintage, and the curve a configured boiler gets from them.

    Every number is a class constant with its source. Gas and oil boilers installed before 2000 follow IWU 2002
    (Diefenbach, Loga, Born, Grossklos, Herbert, "Energetische Kenngroessen fuer Heizungsanlagen im Bestand",
    Institut Wohnen und Umwelt, Darmstadt, 1 November 2002, https://www.iwu.de/fileadmin/publikationen/energie/
    werkzeuge/2002_IWU_DiefenbachEtAl_Energetische-Kenngr%C3%B6%C3%9Fen-f%C3%BCr-Heizungsanlagen-im_Bestand.pdf):
    Tab. 2 gives ``eta_k,70`` at a 70 °C mean boiler temperature and Tab. 3 ``eta_30%`` at the type's mean boiler
    temperature, both on the NCV for a 20 kW boiler, and eq. 2-1 moves both to other sizes. A current boiler (no
    stated year, or installed from 2000 on) takes a current product's data. Pellet and wood-chip boilers take their
    product data whatever the year, since IWU's solid-fuel rows describe log boilers. Hydrogen has no published curve
    and takes the gas boiler's (owner decision of 2026-10-20, a stated estimate).

    HiSim's conventional boiler is IWU's low-temperature boiler ("Kessel mit gleitender Temperaturregelung") in the
    1980s and 1990s rows, and its standard boiler ("Spezialkessel") in the 1970s, the only conventional row of that
    decade. A condensing boiler has no 1970s row and takes the 1980s one.
    """

    #: ``g`` of a condensing gas (and hydrogen) boiler on the GCV, per K of return below 60 °C: BRE, SAP 10 Technical
    #: Paper S10TP-12 issue 1.2 (2023), §4.5, theoretical maximum gross efficiency of a gas condensing boiler 97.3 %
    #: at a 30 °C and 88.3 % at a 60 °C return, 9.0 points over 30 K. The paper's Fig. 2 gas curve gives the same,
    #: 96.82 % and 87.54 %, 0.0031 per K.
    CONDENSING_GAS_GAIN_ON_GROSS_BASIS_PER_KELVIN: ClassVar[float] = 0.0030
    #: ``g`` of a condensing oil boiler on the GCV, per K of return below 60 °C: BRE, SAP 10 Technical Paper
    #: S10TP-12 issue 1.2 (2023), §4.1, Fig. 2 "Boiler efficiency - return water temperature relationship by fuel"
    #: (p. 18), oil curve 96.74 % at a 30 °C and 90.83 % at a 60 °C return (GCV, read off the figure's vector data),
    #: 5.9 points over 30 K, 0.00197 per K; linear between the two returns as for gas. Oil gains less than gas because
    #: its flue gas carries less water vapour: the latent heat is about 6.2 % of its GCV against 9.8 % for natural
    #: gas, and 0.0030 * 6.2 / 9.8 = 0.0019. Cross-check: Viessmann, Planungsanleitung Vitorondens 200-T /
    #: Vitoladens 300-C / Vitoladens 300-T, document 5811441 DE (4/2025), technical data: the rated heat output at
    #: 50/30 °C and at 80/60 °C for the same rated heat input gives 0.0022 per K (0.0018 to 0.0023 over 18 ratings).
    #: Owner decision C of 2026-10-21.
    CONDENSING_OIL_GAIN_ON_GROSS_BASIS_PER_KELVIN: ClassVar[float] = 0.0020

    #: Current condensing gas boiler: Viessmann Vitodens 200-W B2HF-11 (10 kW), product fiche to (EU) 811/2013 and
    #: 813/2013, document 5593552-01: eta_4 = 87.7 % (full load, 80/60 °C), eta_1 = 97.8 % (30 % load, 30 °C
    #: return), GCV.
    VITODENS_200W_B2HF_11: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(
        0.877, 0.978, CalorificBasis.GROSS, CONDENSING_GAS_GAIN_ON_GROSS_BASIS_PER_KELVIN
    )
    #: Current condensing oil boiler: Buderus Logano plus GB125-22 (22 kW, article 7736604146), product fiche to
    #: (EU) 811/2013 and 813/2013, document 6721825297 (2020/10): eta_4 = 91.0 %, eta_1 = 97.5 %, GCV.
    LOGANO_PLUS_GB125_22: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(
        0.910, 0.975, CalorificBasis.GROSS, CONDENSING_OIL_GAIN_ON_GROSS_BASIS_PER_KELVIN
    )
    #: Current conventional (low-temperature) boiler: Buderus Logano G125-21 (21 kW oil low-temperature boiler),
    #: Planungsunterlage Logano G125 (Ausgabe 08/2004), section 1.4, "Kennwerte zur Ermittlung der
    #: Anlagen-Aufwandszahl nach DIN 4701-10": eta_100% = 93.7 %, eta_30% = 96.0 %, NCV. No Ecodesign fiche exists:
    #: 813/2013 bars non-condensing boilers up to 400 kW from sale for space heating since 2015 (B1 boilers aside),
    #: so this is the newest household low-temperature boiler data found. Used for gas as well, as IWU 2002 gives gas
    #: and oil low-temperature boilers the same rows.
    LOGANO_G125_21: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(0.937, 0.960, CalorificBasis.NET)
    #: Pellet boiler: Froeling PE1 Pellet 15, "Produktdaten PE1 Pellet" T6390021_de, data to (EU) 2015/1189:
    #: eta_n = 87.8 % (nominal load), eta_p = 84.5 % (30 % load), GCV.
    FROELING_PE1_PELLET_15: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(0.878, 0.845, CalorificBasis.GROSS)
    #: Wood-chip boiler: Hargassner Eco-HK 20 (6-20 kW), brochure "Hackgut" 2020, technical data, "Wirkungsgrad
    #: Volllast / Teillast 93,9 / 91,4 %", NCV (its smallest load, 6 kW, is 30 %).
    HARGASSNER_ECO_HK_20: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(0.939, 0.914, CalorificBasis.NET)

    #: IWU 2002, "Gas-Niedertemperatur-/Brennwertkessel", 80er Jahre: Tab. 2 eta_k,70 = 0.89, Tab. 3
    #: "Gas-Brennwertkessel" eta_30% = 0.97 at 30 °C; NCV, 20 kW.
    IWU_GAS_CONDENSING_1980S: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(
        0.89, 0.97, CalorificBasis.NET, CONDENSING_GAS_GAIN_ON_GROSS_BASIS_PER_KELVIN, follows_size_law=True
    )
    #: IWU 2002, the same rows, 90er Jahre: Tab. 2 eta_k,70 = 0.92, Tab. 3 eta_30% = 1.01; NCV, 20 kW.
    IWU_GAS_CONDENSING_1990S: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(
        0.92, 1.01, CalorificBasis.NET, CONDENSING_GAS_GAIN_ON_GROSS_BASIS_PER_KELVIN, follows_size_law=True
    )
    #: IWU 2002, "Oel-Niedertemperaturkessel/Brennwertkessel", 80er Jahre: Tab. 2 eta_k,70 = 0.89, Tab. 3
    #: "Oel-Brennwertkessel" eta_30% = 0.94 at 30 °C; NCV, 20 kW.
    IWU_OIL_CONDENSING_1980S: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(
        0.89, 0.94, CalorificBasis.NET, CONDENSING_OIL_GAIN_ON_GROSS_BASIS_PER_KELVIN, follows_size_law=True
    )
    #: IWU 2002, the same rows, 90er Jahre: Tab. 2 eta_k,70 = 0.92, Tab. 3 eta_30% = 0.97; NCV, 20 kW.
    IWU_OIL_CONDENSING_1990S: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(
        0.92, 0.97, CalorificBasis.NET, CONDENSING_OIL_GAIN_ON_GROSS_BASIS_PER_KELVIN, follows_size_law=True
    )
    #: IWU 2002, "Gas-/Oel-Niedertemperaturkessel", 80er Jahre: Tab. 2 eta_k,70 = 0.89, Tab. 3 eta_30% = 0.88 at
    #: 40 °C; NCV, 20 kW (the gas and the oil rows are equal).
    IWU_LOW_TEMPERATURE_1980S: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(
        0.89, 0.88, CalorificBasis.NET, follows_size_law=True
    )
    #: IWU 2002, the same rows, 90er Jahre: Tab. 2 eta_k,70 = 0.92, Tab. 3 eta_30% = 0.92 at 40 °C; NCV, 20 kW.
    IWU_LOW_TEMPERATURE_1990S: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(
        0.92, 0.92, CalorificBasis.NET, follows_size_law=True
    )
    #: IWU 2002, "Gas-Spezialkessel", 70er Jahre: Tab. 2 eta_k,70 = 0.85, Tab. 3 eta_30% = 0.80 at 50 °C; NCV, 20 kW.
    IWU_GAS_STANDARD_1970S: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(
        0.85, 0.80, CalorificBasis.NET, follows_size_law=True
    )
    #: IWU 2002, "Oel-Spezialkessel", 70er Jahre: Tab. 2 eta_k,70 = 0.84, Tab. 3 eta_30% = 0.79 at 50 °C; NCV, 20 kW.
    IWU_OIL_STANDARD_1970S: ClassVar[EfficiencyTestPoints] = EfficiencyTestPoints(
        0.84, 0.79, CalorificBasis.NET, follows_size_law=True
    )

    #: IWU 2002 eq. 2-1, ``eta_k,70 = eta_k,70,20kW - 0.0195 + 0.015 lg Q_n`` (Q_n in kW), constant term,
    #: dimensionless. IWU applies it to eta_30% as well ("setze: eta_30% statt eta_K,70").
    SIZE_LAW_OFFSET: ClassVar[float] = -0.0195
    #: IWU 2002 eq. 2-1, the coefficient of ``lg Q_n``, dimensionless per decade of kW.
    SIZE_LAW_SLOPE_PER_DECADE_OF_KILOWATT: ClassVar[float] = 0.015

    #: Wood pellets, GCV over NCV, dimensionless: 18.89 / 17.52 MJ/kg, measured on the A1 test pellets of TUeV SUeD
    #: report 2212100-1 (Froeling P1 Pellet 10 to EN 303-5). ``PhysicsConfig`` has only the NCV of pellets.
    PELLETS_GROSS_OVER_NET: ClassVar[float] = 18.89 / 17.52
    #: Wood chips at 15 % water content (the basis of HiSim's UBA heating value), GCV over NCV, dimensionless, a
    #: stated estimate: EN ISO 18125 Annex E, ``q_net,ar = (q_gr,d - 212.2 H_d - 0.8 (O_d + N_d)) (1 - M/100) -
    #: 24.43 M`` (J/g, contents in % of dry mass), with q_gr,d = 20 000 J/g, H_d = 6.0 %, O_d + N_d = 43.2 % (stated
    #: estimates for wood, not taken from a cited table) and M = 15: q_net = 15 522 J/g against q_gr = 17 000 J/g,
    #: 1.095. The same inputs give UBA's 15.6 MJ/kg NCV within 0.5 %.
    WOOD_CHIPS_GROSS_OVER_NET: ClassVar[float] = 17000.0 / 15522.0

    #: ``F(1)``, the correction for temperature controls that both regulations subtract from the active-mode
    #: seasonal efficiency of a boiler, dimensionless: 3 %, Commission communication 2014/C 207/02, point 4.4(a)
    #: (for 813/2013), and Regulation (EU) 2015/1189, Annex IV, point 4(a). The other corrections, auxiliary
    #: electricity F(2), standby loss F(3) and ignition burner F(4), need product data HiSim does not model, and are
    #: left out (owner decision of 2026-10-20).
    TEMPERATURE_CONTROL_CORRECTION: ClassVar[float] = 0.03

    @classmethod
    def gross_over_net(cls, energy_carrier: lt.LoadTypes) -> float:
        """Return a fuel's GCV over its NCV, dimensionless: the factor between its two efficiency bases.

        ``eta_NCV = eta_GCV * GCV / NCV``. Gas, oil and hydrogen take ``PhysicsConfig``'s own two heating values
        (natural gas 39.819 / 35.895 = 1.109, heating oil 37.682 / 35.358 = 1.066, hydrogen 12.745 / 10.783 =
        1.182); pellets and wood chips, which have only an NCV there, take :attr:`PELLETS_GROSS_OVER_NET` and
        :attr:`WOOD_CHIPS_GROSS_OVER_NET`.

        Args:
            energy_carrier: The fuel.

        Returns:
            The ratio of the gross to the net calorific value.
        """
        if energy_carrier == lt.LoadTypes.PELLETS:
            return cls.PELLETS_GROSS_OVER_NET
        if energy_carrier == lt.LoadTypes.WOOD_CHIPS:
            return cls.WOOD_CHIPS_GROSS_OVER_NET
        properties = PhysicsConfig.get_properties_for_energy_carrier(energy_carrier=energy_carrier)
        return properties.higher_heating_value_in_joule_per_m3 / properties.lower_heating_value_in_joule_per_m3

    @classmethod
    def test_points(
        cls, energy_carrier: lt.LoadTypes, boiler_type: BoilerType, vintage: BoilerVintage
    ) -> EfficiencyTestPoints:
        """Return the source row of a boiler of this fuel, type and vintage.

        Hydrogen takes the gas row of its type. For example, a condensing gas boiler of the 1990s gets
        :attr:`IWU_GAS_CONDENSING_1990S`, a conventional oil boiler with no stated year :attr:`LOGANO_G125_21`, and
        a pellet boiler of 1985 :attr:`FROELING_PE1_PELLET_15`.

        Args:
            energy_carrier: The fuel the boiler burns.
            boiler_type: Condensing or conventional.
            vintage: The boiler's vintage.

        Returns:
            The two test points of the source.

        Raises:
            ValueError: If no source covers the fuel (every carrier but gas, oil, hydrogen, pellets and wood chips).
        """
        if energy_carrier == lt.LoadTypes.PELLETS:
            return cls.FROELING_PE1_PELLET_15
        if energy_carrier == lt.LoadTypes.WOOD_CHIPS:
            return cls.HARGASSNER_ECO_HK_20
        if energy_carrier not in (lt.LoadTypes.GAS, lt.LoadTypes.OIL, lt.LoadTypes.GREEN_HYDROGEN):
            raise ValueError(
                f"No sourced combustion efficiency for a boiler burning {energy_carrier}: the sources cover gas, oil, "
                "hydrogen (as gas), pellets and wood chips."
            )
        is_oil = energy_carrier == lt.LoadTypes.OIL
        if boiler_type == BoilerType.CONDENSING:
            rows = {
                BoilerVintage.CURRENT: cls.LOGANO_PLUS_GB125_22 if is_oil else cls.VITODENS_200W_B2HF_11,
                BoilerVintage.NINETIES: cls.IWU_OIL_CONDENSING_1990S if is_oil else cls.IWU_GAS_CONDENSING_1990S,
                BoilerVintage.EIGHTIES: cls.IWU_OIL_CONDENSING_1980S if is_oil else cls.IWU_GAS_CONDENSING_1980S,
                BoilerVintage.SEVENTIES: cls.IWU_OIL_CONDENSING_1980S if is_oil else cls.IWU_GAS_CONDENSING_1980S,
            }
        else:
            rows = {
                BoilerVintage.CURRENT: cls.LOGANO_G125_21,
                BoilerVintage.NINETIES: cls.IWU_LOW_TEMPERATURE_1990S,
                BoilerVintage.EIGHTIES: cls.IWU_LOW_TEMPERATURE_1980S,
                BoilerVintage.SEVENTIES: cls.IWU_OIL_STANDARD_1970S if is_oil else cls.IWU_GAS_STANDARD_1970S,
            }
        return rows[vintage]

    @staticmethod
    def require_finite_positive_power(maximal_power_in_watt: float) -> None:
        """Refuse a boiler power the efficiency's size law cannot use: not finite, or not above 0 W.

        The curve is built once per run, while the component is constructed, so this check runs at build time and
        never in a simulated step.

        Args:
            maximal_power_in_watt: The boiler's maximal burner power, in W.

        Raises:
            ValueError: If the power is not finite or not above 0 W.
        """
        if not math.isfinite(maximal_power_in_watt) or maximal_power_in_watt <= 0.0:
            raise ValueError(
                f"The boiler's maximal_thermal_power_in_watt must be a finite power above 0 W, got "
                f"{maximal_power_in_watt!r}: the efficiency's size law and the pump's largest flow need it."
            )

    @classmethod
    def size_correction(cls, maximal_power_in_watt: float) -> float:
        """Return IWU 2002 eq. 2-1's correction of a 20 kW efficiency to a boiler's size, dimensionless.

        ``-0.0195 + 0.015 lg(P_n / 1 kW)``; 0 at 20 kW, -0.0045 at 10 kW. ``P_n`` is the maximal burner power, which
        HiSim's ``maximal_thermal_power_in_watt`` holds (it is the fuel input, hisim-component-physics-4g9.29); the
        rated heat output is about 8 % lower, which moves the correction by 0.0005.

        Args:
            maximal_power_in_watt: The boiler's maximal burner power, in W, above 0.

        Returns:
            The correction to add to both 20 kW efficiencies.

        Raises:
            ValueError: If the power is not finite or not above 0 W.
        """
        cls.require_finite_positive_power(maximal_power_in_watt)
        return cls.SIZE_LAW_OFFSET + cls.SIZE_LAW_SLOPE_PER_DECADE_OF_KILOWATT * math.log10(
            maximal_power_in_watt / 1000.0
        )

    @classmethod
    def unscaled_curve(
        cls,
        *,
        energy_carrier: lt.LoadTypes,
        boiler_type: BoilerType,
        installation_year: Optional[int],
        maximal_power_in_watt: float,
    ) -> CombustionEfficiencyCurve:
        """Return a boiler's curve from its source row, moved to its size and put on its booking basis.

        Hydrogen takes the gas boiler's curve as it is: the same efficiencies on the same basis. For example, a
        20 kW condensing gas boiler of 1995 gets IWU's 0.92 / 1.01 (NCV) on the GCV, 0.829 / 0.910.

        Args:
            energy_carrier: The fuel the boiler burns.
            boiler_type: Condensing or conventional.
            installation_year: The year the boiler was installed, or ``None`` for a current boiler.
            maximal_power_in_watt: The maximal burner power, in W, above 0.

        Returns:
            The curve on the calorific basis the boiler's fuel is booked on.
        """
        size_correction = cls.size_correction(maximal_power_in_watt)
        source_carrier = lt.LoadTypes.GAS if energy_carrier == lt.LoadTypes.GREEN_HYDROGEN else energy_carrier
        points = cls.test_points(source_carrier, boiler_type, BoilerVintage.of_installation_year(installation_year))
        efficiency_at_full_load = points.efficiency_at_full_load
        efficiency_at_part_load = points.efficiency_at_part_load
        if points.follows_size_law:
            efficiency_at_full_load += size_correction
            efficiency_at_part_load += size_correction
        gross_over_net = cls.gross_over_net(source_carrier)
        booking = CalorificBasis.of_booking(source_carrier, boiler_type)
        gain_per_kelvin = points.condensing_gain_on_gross_basis_per_kelvin
        if booking == CalorificBasis.NET:
            gain_per_kelvin *= gross_over_net
        if points.calorific_basis == CalorificBasis.NET and booking == CalorificBasis.GROSS:
            efficiency_at_full_load /= gross_over_net
            efficiency_at_part_load /= gross_over_net
        elif points.calorific_basis == CalorificBasis.GROSS and booking == CalorificBasis.NET:
            efficiency_at_full_load *= gross_over_net
            efficiency_at_part_load *= gross_over_net
        return CombustionEfficiencyCurve(
            efficiency_at_full_load=efficiency_at_full_load,
            efficiency_at_part_load=efficiency_at_part_load,
            condensing_gain_per_kelvin=gain_per_kelvin,
        )

    @classmethod
    def gross_to_booking_factor(cls, energy_carrier: lt.LoadTypes, boiler_type: BoilerType) -> float:
        """Return the factor that puts an efficiency on the GCV onto the boiler's booking basis, dimensionless.

        It is the fuel's GCV/NCV for a boiler booked on the NCV (:meth:`CalorificBasis.of_booking`) and 1 for one
        booked on the GCV. Hydrogen takes the gas boiler's curve as it is, so it takes the gas factor as well. For
        example, a conventional gas boiler gives 1.109, a condensing gas boiler 1, a pellet boiler 1.078.

        Args:
            energy_carrier: The fuel the boiler burns.
            boiler_type: Condensing or conventional.

        Returns:
            ``eta_booking / eta_GCV``.
        """
        source_carrier = lt.LoadTypes.GAS if energy_carrier == lt.LoadTypes.GREEN_HYDROGEN else energy_carrier
        if CalorificBasis.of_booking(source_carrier, boiler_type) == CalorificBasis.NET:
            return cls.gross_over_net(source_carrier)
        return 1.0

    @classmethod
    def highest_seasonal_efficiency(
        cls,
        *,
        energy_carrier: lt.LoadTypes,
        boiler_type: BoilerType,
        installation_year: Optional[int],
        maximal_power_in_watt: float,
    ) -> float:
        """Return the highest stated seasonal efficiency ``eta_s`` (GCV) this boiler can take, dimensionless.

        A stated ``eta_s`` scales the boiler's curve by ``s = (eta_s + 0.03) k / eta_son`` (:meth:`curve`), so the
        curve's highest point becomes ``s H``, with ``eta_son = 0.85 eta_30 + 0.15 eta_100`` and ``H`` the highest
        point of the unscaled curve, both on the booking basis, and ``k`` from :meth:`gross_to_booking_factor`. The
        scaled curve must stay at or below 1 on the basis the boiler's fuel is booked on: above it, the heat exceeds
        the fuel booked for it and the combustion loss turns negative. Solving ``s H <= 1`` gives the ceiling
        ``eta_s <= eta_son / (H k) - 0.03``. For a condensing gas or oil boiler, booked on the GCV, that is 1 on the
        GCV (current condensing gas 0.9545). A non-condensing boiler and every pellet and wood-chip boiler is booked
        on the NCV, where a non-condensing boiler cannot reach 1 either, so its ceiling lies a fuel's GCV/NCV lower:
        current conventional gas 0.8682, conventional oil 0.9050, pellets 0.8678, wood chips 0.8624. :meth:`curve`
        refuses exactly what lies above it, and the RenoVisor request check calls it to refuse such a request
        before anything runs.

        Only the IWU rows whose two test points differ depend on the size, through eq. 2-1 (the 1990s and 1980s
        condensing, the 1980s low-temperature and the 1970s standard rows): it adds the same correction to both
        points, so their ceiling rises with the power, by 0.2 points for the 1970s conventional gas row between
        1 kW (0.8253) and 400 kW (0.8274).

        Args:
            energy_carrier: The fuel the boiler burns.
            boiler_type: Condensing or conventional.
            installation_year: The year the boiler was installed, or ``None`` for a current boiler.
            maximal_power_in_watt: The maximal burner power, in W, above 0.

        Returns:
            The highest ``eta_s`` on the GCV whose scaled curve stays at or below 1 on the booking basis.

        Raises:
            ValueError: If the power is not finite or not above 0 W, or no source covers the fuel.
        """
        unscaled = cls.unscaled_curve(
            energy_carrier=energy_carrier,
            boiler_type=boiler_type,
            installation_year=installation_year,
            maximal_power_in_watt=maximal_power_in_watt,
        )
        gross_to_booking = cls.gross_to_booking_factor(energy_carrier, boiler_type)
        return (
            unscaled.active_mode_seasonal_efficiency() / (unscaled.highest_efficiency() * gross_to_booking)
            - cls.TEMPERATURE_CONTROL_CORRECTION
        )

    @classmethod
    def curve(
        cls,
        *,
        energy_carrier: lt.LoadTypes,
        boiler_type: BoilerType,
        installation_year: Optional[int],
        maximal_power_in_watt: float,
        seasonal_efficiency: Optional[float],
    ) -> CombustionEfficiencyCurve:
        """Return the curve a configured boiler burns by: its type's curve, scaled to a stated seasonal efficiency.

        Without a stated seasonal efficiency the curve is :meth:`unscaled_curve`. With one, the curve is scaled so
        that its active-mode seasonal efficiency minus :attr:`TEMPERATURE_CONTROL_CORRECTION`, on the GCV, is the
        stated value: ``s = (eta_s + 0.03) * k / (0.85 eta_30 + 0.15 eta_100)``, where ``k`` is the fuel's GCV/NCV
        for a boiler booked on the NCV and 1 for one booked on the GCV (:meth:`gross_to_booking_factor`). For
        example, a stated 0.90 on the current condensing gas curve gives ``s = 0.93 / 0.963 = 0.966``. A stated value
        above :meth:`highest_seasonal_efficiency` is refused: it would lift the curve above 1 on the booking basis.

        Args:
            energy_carrier: The fuel the boiler burns.
            boiler_type: Condensing or conventional.
            installation_year: The year the boiler was installed, or ``None`` for a current boiler.
            maximal_power_in_watt: The maximal burner power, in W, above 0.
            seasonal_efficiency: The Ecodesign seasonal space heating efficiency ``eta_s`` on the GCV,
                dimensionless, or ``None``.

        Returns:
            The curve on the boiler's booking basis.

        Raises:
            ValueError: If the stated value is above :meth:`highest_seasonal_efficiency`, so the scaled curve would
                exceed 1 somewhere on the basis the boiler's fuel is booked on: the GCV for a condensing gas, oil or
                hydrogen boiler, the NCV for every other one.
        """
        unscaled = cls.unscaled_curve(
            energy_carrier=energy_carrier,
            boiler_type=boiler_type,
            installation_year=installation_year,
            maximal_power_in_watt=maximal_power_in_watt,
        )
        if seasonal_efficiency is None:
            return unscaled
        gross_to_booking = cls.gross_to_booking_factor(energy_carrier, boiler_type)
        highest_seasonal_efficiency = cls.highest_seasonal_efficiency(
            energy_carrier=energy_carrier,
            boiler_type=boiler_type,
            installation_year=installation_year,
            maximal_power_in_watt=maximal_power_in_watt,
        )
        if seasonal_efficiency > highest_seasonal_efficiency:
            on_net_basis = CalorificBasis.of_booking(energy_carrier, boiler_type) == CalorificBasis.NET
            basis = "net" if on_net_basis else "gross"
            year = f"installed in {installation_year}" if installation_year is not None else "a current one"
            hint = (
                "the boiler's fuel is booked on the net calorific value, on which a boiler that does not condense "
                "cannot exceed 1: its heat would exceed the fuel booked for it"
                if on_net_basis
                else "the stated value must be the Ecodesign seasonal space heating efficiency eta_s of the energy "
                "label, on the gross calorific value"
            )
            raise ValueError(
                f"A seasonal efficiency of {seasonal_efficiency!r} is above {highest_seasonal_efficiency:.4f}, the "
                f"highest a {boiler_type.value} {energy_carrier.value} boiler of {maximal_power_in_watt:g} W "
                f"({year}) can take: it would scale the boiler's curve above 1 on the {basis} calorific value; "
                f"{hint}."
            )
        target_on_booking_basis = (seasonal_efficiency + cls.TEMPERATURE_CONTROL_CORRECTION) * gross_to_booking
        return unscaled.scaled(target_on_booking_basis / unscaled.active_mode_seasonal_efficiency())


@dataclass(frozen=True)
class BurnerModulation(Subcomponent):
    """A boiler burner's power band and its combustion efficiency across it.

    The burner modulates between a minimal and a maximal power. Its combustion efficiency, the share of the fuel
    power that becomes heat, follows the boiler's :class:`CombustionEfficiencyCurve` over the load ratio, the burner
    power over the maximal power, and the return temperature of the circuit it fires. A subcomponent built once from
    the boiler's configuration, so its methods are pure functions of their arguments. For example, a 2-20 kW
    band on the current condensing gas curve burns at 0.945 at 10 kW on a 40 °C return.
    """

    #: The lowest power the burner modulates down to, in W.
    minimal_power_in_watt: float
    #: The highest power the burner reaches, in W, above 0.
    maximal_power_in_watt: float
    #: The combustion efficiency over load and return temperature, on the boiler's booking basis.
    efficiency_curve: CombustionEfficiencyCurve

    def commanded_power_in_watt(self, control_signal: float) -> float:
        """Return the burner power a control signal commands, in W: its share of the maximum, at least the minimum.

        A burner cannot modulate below its minimal power, so a signal that asks for less burns at the minimum. For
        example, a 2-20 kW burner burns 2 kW at a signal of 0.05 and 10 kW at 0.5.

        Args:
            control_signal: The share of the maximal power, dimensionless, 0 to 1.

        Returns:
            The burner power, in W.
        """
        requested_power_in_watt = control_signal * self.maximal_power_in_watt
        if requested_power_in_watt < self.minimal_power_in_watt:
            return self.minimal_power_in_watt
        return requested_power_in_watt

    def efficiency_at(self, burner_power_in_watt: float, return_temperature_in_celsius: float) -> float:
        """Return the combustion efficiency at a burner power and a return temperature, dimensionless.

        It is :meth:`CombustionEfficiencyCurve.efficiency_at` at the load ratio ``burner power / maximal power``.

        Args:
            burner_power_in_watt: The burner power, in W.
            return_temperature_in_celsius: The return temperature of the circuit the burner fires, in °C.

        Returns:
            The share of the fuel power that becomes heat.
        """
        return self.efficiency_curve.efficiency_at(
            burner_power_in_watt / self.maximal_power_in_watt, return_temperature_in_celsius
        )

    def heat_power_in_watt(self, burner_power_in_watt: float, return_temperature_in_celsius: float) -> float:
        """Return the heat the burner turns a burner power into, in W: the power times :meth:`efficiency_at`.

        The one home of the burner's heat, which both sizes the boiler's flow (:meth:`GenericBoiler.fired_circuit`)
        and sets the rise the pump raises a lift to (:meth:`ControllerLiftPump.operating_point`), so the two cannot
        drift apart. For example, a burner at 11 kW and an efficiency of 0.95 there turns it into 10450 W.

        Args:
            burner_power_in_watt: The burner power, in W.
            return_temperature_in_celsius: The return temperature of the circuit the burner fires, in °C.

        Returns:
            The heat, in W.
        """
        return burner_power_in_watt * self.efficiency_at(burner_power_in_watt, return_temperature_in_celsius)


@dataclass(frozen=True)
class BoilerFiring:
    """What one firing circuit of a boiler burns, carries and pumps in one step.

    A value object returned by :meth:`GenericBoiler.fired_circuit`, so the four quantities cannot be swapped by
    position.
    """

    #: The fuel power the burner burns, in W.
    fuel_power_in_watt: float
    #: The heat the circuit's water carries, in W.
    thermal_power_in_watt: float
    #: The circuit's mass flow, in kg/s.
    mass_flow_in_kg_per_second: float
    #: The circuit's supply temperature, in °C.
    supply_temperature_in_celsius: float


@dataclass(frozen=True)
class BurnerState:
    """Whether a boiler's burner is lit, and for how long it has been lit or out, at the end of a step.

    The boiler commits one of these at the end of every step and decides the next step's firing on it
    (:meth:`BurnerTimes.burner_is_lit`). For example, a burner lit for the last two 900 s steps is
    ``BurnerState(is_lit=True, seconds_in_state=1800.0)``.
    """

    #: Whether the burner burnt fuel in the step.
    is_lit: bool
    #: How long the burner has been in that state, lit or out, at the end of the step, in s.
    seconds_in_state: float

    @classmethod
    def at_start(cls) -> "BurnerState":
        """Return the state a simulation starts in: the burner out for longer than any pause, so it may light at once.

        Returns:
            The burner out since an infinitely long time.
        """
        return cls(is_lit=False, seconds_in_state=math.inf)

    def after_step(self, *, is_lit: bool, seconds_per_timestep: float) -> "BurnerState":
        """Return the state at the end of a step in which the burner was lit or out.

        A burner that stays in its state adds the step to its time; one that changes starts counting again from the
        step. For example, a burner out for 900 s that lights in a 900 s step has been lit for 900 s.

        Args:
            is_lit: Whether the burner burnt fuel in the step.
            seconds_per_timestep: The step length, in s.

        Returns:
            The state after the step.
        """
        if is_lit == self.is_lit:
            return BurnerState(is_lit=is_lit, seconds_in_state=self.seconds_in_state + seconds_per_timestep)
        return BurnerState(is_lit=is_lit, seconds_in_state=seconds_per_timestep)


@dataclass(frozen=True)
class BurnerTimes(Subcomponent):
    """A burner's minimum firing time and minimum pause: the device's protection against short cycling.

    A wood pellet or wood chip burner cannot simply switch off: once lit it runs through ignition, a stabilisation of
    the fire and, when it stops, a burn-out of the fuel left on the grate, each lasting minutes. Lit, it therefore
    keeps burning for at least its minimum firing time even when its controller asks it to stop; out, it does not
    light again before its minimum pause has passed. Both are counted in whole timesteps, rounded down, the way the
    boiler's controller counts its own minimum times: 1800 s are 2 steps of 900 s and 30 steps of 60 s, and 600 s at
    900 s per step are 0 steps, no minimum. A modulating gas, oil or hydrogen burner follows its heat demand within its
    power band and has neither (0 s).

    The times per fuel are class constants. Pellets: a measured pellet boiler cycle has about 10.5 minutes of
    start-up (purge, fuel fill, ignition, stabilisation) and 10 minutes of burn-out plus 3 minutes of post-combustion
    ventilation (McDowell, Yousef, Siegenthaler and Butcher, "A new detailed model of wood pellet boilers", SimBuild
    2020, Table 1), so a start that is stopped at once still occupies the burner for about 24 minutes; the minimum
    firing time is 30 minutes and the minimum pause 15 minutes. Wood chips burn on a larger fuel bed that ignites and
    burns out more slowly: 60 and 30 minutes. Both pairs are the times the repository's pellet and wood-chip setups
    already gave their controllers.
    """

    #: A pellet burner's minimum firing time, in s.
    PELLET_MINIMUM_FIRING_TIME_IN_SECONDS: ClassVar[float] = 30 * 60.0
    #: A pellet burner's minimum pause, in s.
    PELLET_MINIMUM_PAUSE_IN_SECONDS: ClassVar[float] = 15 * 60.0
    #: A wood chip burner's minimum firing time, in s.
    WOOD_CHIP_MINIMUM_FIRING_TIME_IN_SECONDS: ClassVar[float] = 60 * 60.0
    #: A wood chip burner's minimum pause, in s.
    WOOD_CHIP_MINIMUM_PAUSE_IN_SECONDS: ClassVar[float] = 30 * 60.0

    #: The shortest time the burner burns once lit, in s, at least 0.
    minimum_firing_time_in_seconds: float
    #: The shortest time the burner stays out once it went out, in s, at least 0.
    minimum_pause_in_seconds: float

    @classmethod
    def of_fuel(cls, energy_carrier: lt.LoadTypes) -> "BurnerTimes":
        """Return the minimum times of a burner of this fuel: pellets and wood chips have them, every other fuel none.

        For example, ``BurnerTimes.of_fuel(lt.LoadTypes.PELLETS)`` gives 1800 s and 900 s, ``lt.LoadTypes.GAS``
        0 s and 0 s.

        Args:
            energy_carrier: The fuel the boiler burns.

        Returns:
            The burner's minimum firing time and minimum pause.
        """
        if energy_carrier == lt.LoadTypes.PELLETS:
            return cls(
                minimum_firing_time_in_seconds=cls.PELLET_MINIMUM_FIRING_TIME_IN_SECONDS,
                minimum_pause_in_seconds=cls.PELLET_MINIMUM_PAUSE_IN_SECONDS,
            )
        if energy_carrier == lt.LoadTypes.WOOD_CHIPS:
            return cls(
                minimum_firing_time_in_seconds=cls.WOOD_CHIP_MINIMUM_FIRING_TIME_IN_SECONDS,
                minimum_pause_in_seconds=cls.WOOD_CHIP_MINIMUM_PAUSE_IN_SECONDS,
            )
        return cls(minimum_firing_time_in_seconds=0.0, minimum_pause_in_seconds=0.0)

    @staticmethod
    def whole_steps(seconds: float, seconds_per_timestep: float) -> int:
        """Return how many whole timesteps a minimum time holds, rounded down.

        This is the one rounding rule of the module: the burner's minimum times and the controller's
        (:meth:`GenericBoilerController.whole_timesteps`) both count with it. For example, 1500 s at 900 s per step
        is 1 step, 600 s at 900 s per step is 0 steps, and 1800 s at 60 s per step is 30 steps.

        Args:
            seconds: The minimum time, in s, at least 0.
            seconds_per_timestep: The step length, in s.

        Returns:
            The number of whole timesteps.
        """
        return math.floor(seconds / seconds_per_timestep)

    @classmethod
    def whole_steps_in_seconds(cls, seconds: float, seconds_per_timestep: float) -> float:
        """Return a minimum time rounded down to whole timesteps, in s.

        For example, 1500 s at 900 s per step is 900 s, 600 s at 900 s per step is 0 s, and 1800 s at 60 s per step
        stays 1800 s.

        Args:
            seconds: The minimum time, in s.
            seconds_per_timestep: The step length, in s.

        Returns:
            The whole timesteps the time holds, in s.
        """
        return cls.whole_steps(seconds, seconds_per_timestep) * seconds_per_timestep

    def times_shorter_than_a_step(self, seconds_per_timestep: float) -> List[str]:
        """Return the names of the minimum times that are above 0 but shorter than one step, so have no effect.

        The burner counts its minimum times in whole steps, rounded down, so a pellet burner's 30 minutes of minimum
        firing time and 15 minutes of minimum pause hold nothing at hourly steps: the burner then lights and goes out
        as asked. For example, ``BurnerTimes.of_fuel(lt.LoadTypes.PELLETS).times_shorter_than_a_step(3600.0)`` is
        ``["minimum firing time", "minimum pause"]``, and at 900 s it is empty. The boiler logs one warning per name,
        the way the controller warns about its own minimum times (:meth:`GenericBoilerController.whole_timesteps`).

        Args:
            seconds_per_timestep: The step length, in s.

        Returns:
            The names of the times without effect, possibly none.
        """
        return [
            name
            for name, seconds in (
                ("minimum firing time", self.minimum_firing_time_in_seconds),
                ("minimum pause", self.minimum_pause_in_seconds),
            )
            if 0 < seconds < seconds_per_timestep
        ]

    def burner_is_lit(self, *, asked_to_burn: bool, committed: BurnerState, seconds_per_timestep: float) -> bool:
        """Return whether the burner burns in this step: as asked, unless a minimum time holds it lit or out.

        A burner lit for less than its minimum firing time burns on although it is not asked to; a burner out for less
        than its minimum pause stays out although it is asked to burn. For example, with 1800 s and 900 s at 900 s per
        step, a burner lit for 900 s burns on, one lit for 1800 s stops when asked, and one out for 900 s lights when
        asked; with 1800 s of pause it would stay out.

        Args:
            asked_to_burn: Whether the controller's command would burn fuel in this step.
            committed: The burner's state at the end of the previous step.
            seconds_per_timestep: The step length, in s.

        Returns:
            Whether the burner burns in this step.
        """
        if committed.is_lit and committed.seconds_in_state < self.whole_steps_in_seconds(
            self.minimum_firing_time_in_seconds, seconds_per_timestep
        ):
            return True
        if not committed.is_lit and committed.seconds_in_state < self.whole_steps_in_seconds(
            self.minimum_pause_in_seconds, seconds_per_timestep
        ):
            return False
        return asked_to_burn


@dataclass(frozen=True)
class BoilerCircuitChannels:
    """The return input and the outputs of one of the boiler's two water circuits.

    The boiler fires one circuit at a time, space heating or hot water; holding each circuit's channels in one
    object lets the step publish either circuit by the same code, with every channel named.
    """

    #: The circuit's return temperature, in °C, read from the storage.
    return_temperature: ComponentInput
    #: The circuit's supply temperature, in °C.
    supply_temperature: ComponentOutput
    #: The circuit's mass flow, in kg/s.
    mass_flow: ComponentOutput
    #: The heat the circuit's water carries, in W.
    thermal_power: ComponentOutput
    #: The heat of the step, in Wh.
    thermal_energy: ComponentOutput
    #: The fuel energy the circuit burnt in the step, in Wh.
    fuel_energy: ComponentOutput


class GenericBoiler(PhysicsComponent):
    """GenericBoiler class.

    Get Control Signal and calculate on base of it Massflow and Temperature of Massflow.

    A hot-water charge runs the requested part-load ratio its controller commands (``RequestedPartLoadRatioDhw``): 1,
    the whole step, at and below the part-load threshold; above it the fraction of the step that brings the tank to
    the controller's target. Below 1 the boiler publishes the averaged flow, the full-load flow times the ratio, at the
    full-load supply temperature, and books the heat that flow carries and the ratio times the full-load fuel. It
    reports the actual ratio it ran with (``ActualPartLoadRatioDhw``): the requested ratio while it charges the tank, 0
    otherwise. Cycling losses of the burner are not modelled.

    A space-heating charge of the buffer runs its own requested ratio the same way (``RequestedPartLoadRatioSh``,
    reported back as the actual ratio ``ActualPartLoadRatioSh``). Where the burner's minimum times override the
    controller (see below), the actual ratios are what the burner ran: 1 for the space-heating charge of a burner held
    lit, 0 for both charges of one held out. Above the part-load threshold the controller may also give the
    space-heating charge the rest of a hot-water step that ends inside the step: the boiler then fires both circuits in
    the step, the hot-water charge at its requested ratio and the space-heating charge at its requested ratio, with the
    control signal and lift the controller sends for it (``ControlSignalForSpaceHeatingRemainder``,
    ``TemperatureDeltaForSpaceHeatingRemainderInKelvin``), and the fuel of both adds up.

    Without a buffer the space-heating circuit may run on the heat distribution system's flow: the distribution system
    pumps its design flow and the boiler, wired to it through ``WaterInputMassFlowSh``, is a heater on that flow, like
    a district-heating substation without a pump (``roadmap/hydronic_coupling_spec.md``, D25 as amended on 2026-10-22).
    It then holds the commanded power instead of the controller's lift: the supply is the return plus the rise that
    power's heat gives at the flow it reads, throttled at the maximal flow temperature, and it books the heat with that
    flow, so both ends of the circuit book the same water. With no flow it does not fire, like a boiler's flow switch.
    The space-heating circuit then publishes the flow it reads in every step, firing or not
    (:meth:`fired_on_distribution_flow`). Unwired, the boiler pumps its own flow as before.

    A pellet or wood-chip burner keeps its minimum firing time and minimum pause (:class:`BurnerTimes`), the device's
    protection against short cycling: lit, it burns on although its controller asks it to stop, sending the heat to the
    space-heating buffer; out, it stays out although asked to burn. The burner's state, lit or out and since when, is
    committed at the end of every step. A gas, oil or hydrogen burner has no minimum times.
    """

    cost_relevance = CostRelevance.PRICED

    # Input
    ControlSignal = "ControlSignal"  # at which Procentage is the GenericBoiler modulating [0..1]
    OperatingMode = "OperatingMode"
    TemperatureDelta = "TemperatureDelta"
    #: The hot-water supply temperature the controller aims at: the boiler's hot-water supply never exceeds it.
    SupplyTemperatureSetForDHWInCelsius = "SupplyTemperatureSetForDHWInCelsius"
    #: The requested part-load ratio of a hot-water charge: the fraction of the step the controller commands it to run.
    RequestedPartLoadRatioDhw = "RequestedPartLoadRatioDhw"
    #: The requested part-load ratio of a space-heating charge: the fraction of the step the controller commands it to run.
    RequestedPartLoadRatioSh = "RequestedPartLoadRatioSh"
    #: The control signal and the lift of a space-heating charge in the rest of a hot-water step.
    ControlSignalForSpaceHeatingRemainder = "ControlSignalForSpaceHeatingRemainder"
    TemperatureDeltaForSpaceHeatingRemainderInKelvin = "TemperatureDeltaForSpaceHeatingRemainderInKelvin"
    WaterInputTemperatureSh = "WaterInputTemperatureSh"
    #: The space-heating circuit's mass flow, in kg/s, when the component at its other end pumps it: a heat
    #: distribution system that pumps its design flow, with no buffer in between. Optional; unwired, the boiler pumps.
    WaterInputMassFlowSh = "WaterInputMassFlowSh"
    WaterInputTemperatureDhw = "WaterInputTemperatureDhw"

    # Output
    #: The actual part-load ratio of the hot-water charge: the fraction of the step it ran, reported back to the controller.
    ActualPartLoadRatioDhw = "ActualPartLoadRatioDhw"
    #: The actual part-load ratio of the space-heating charge: the fraction of the step it ran, reported back to the
    #: controller.
    ActualPartLoadRatioSh = "ActualPartLoadRatioSh"
    WaterOutputMassFlowSh = "WaterOutputMassFlowSh"
    WaterOutputTemperatureSh = "WaterOutputTemperatureSh"
    EnergyDemandSh = "EnergyDemandSh"
    ThermalPowerGenerationSh = "ThermalOutputPowerSh"
    ThermalOutputEnergySh = "ThermalOutputEnergySh"
    WaterOutputMassFlowDhw = "WaterOutputMassFlowDhw"
    WaterOutputTemperatureDhw = "WaterOutputTemperatureDhw"
    EnergyDemandDhw = "EnergyDemandDhw"
    ThermalOutputPowerDhw = "ThermalOutputPowerDhw"
    ThermalOutputEnergyDhw = "ThermalOutputEnergyDhw"
    TotalFuelConsumption = "TotalFuelConsumption"
    #: The fuel power (W) of this step that did not become booked heat: the flue and burner loss.
    CombustionHeatLoss = "CombustionHeatLoss"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericBoilerConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
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
        self.control_signal_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.ControlSignal,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            True,
        )
        self.operating_mode_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.OperatingMode,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            True,
        )
        self.temperature_delta_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.TemperatureDelta,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.KELVIN,
            True,
        )
        self.supply_temperature_set_for_dhw_in_celsius_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.SupplyTemperatureSetForDHWInCelsius,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.dhw_part_load_command = PartLoadCommand(
            self,
            requested_ratio_input_name=GenericBoiler.RequestedPartLoadRatioDhw,
            actual_ratio_output_name=GenericBoiler.ActualPartLoadRatioDhw,
            device_description="the boiler's hot-water charge",
        )
        self.space_heating_part_load_command = PartLoadCommand(
            self,
            requested_ratio_input_name=GenericBoiler.RequestedPartLoadRatioSh,
            actual_ratio_output_name=GenericBoiler.ActualPartLoadRatioSh,
            device_description="the boiler's space-heating charge",
        )
        self.control_signal_for_space_heating_remainder_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.ControlSignalForSpaceHeatingRemainder,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            False,
        )
        self.temperature_delta_for_space_heating_remainder_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.TemperatureDeltaForSpaceHeatingRemainderInKelvin,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.KELVIN,
            False,
        )

        self.total_fuel_input_power_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.TotalFuelConsumption,
            self.config.energy_carrier,
            lt.Units.WATT,
            output_description=(
                "The fuel power the burner burns in this step, W: the commanded burner power while it fires "
                "unthrottled, the heat its circuit carries over the combustion efficiency while it is throttled, "
                "and zero while it does not fire."
            ),
        )

        fuel_carrier = EnergyPort.carrier_of_fuel(self.config.energy_carrier)
        # Space heating
        self.water_input_temperature_sh_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.WaterInputTemperatureSh,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.water_input_mass_flow_sh_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.WaterInputMassFlowSh,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            False,
        )
        self.water_output_mass_flow_sh_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.WaterOutputMassFlowSh,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            output_description=(
                "The space-heating circuit's mass flow, kg/s: the flow the boiler's pump runs while it fires and 0 "
                "while it idles, or, with WaterInputMassFlowSh wired, the flow it reads there, in every step."
            ),
        )
        self.water_output_temperature_sh_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.WaterOutputTemperatureSh,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterOutputTemperatureSh} will follow.",
        )
        self.energy_demand_sh_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.EnergyDemandSh,
            self.config.energy_carrier,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.EnergyDemandSh} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
            energy_port=EnergyPort(lt.EnergyRole.IN, fuel_carrier),
        )
        self.thermal_output_power_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerGenerationSh,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerGenerationSh} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_output_energy_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputEnergySh,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalOutputEnergySh} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
            energy_port=EnergyPort(
                lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT, peer_output=self.WaterOutputMassFlowSh
            ),
        )

        # DHW
        self.water_input_temperature_dhw_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.WaterInputTemperatureDhw,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            False,
        )
        self.water_output_mass_flow_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.WaterOutputMassFlowDhw,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            output_description="Mass flow rate of warm water.",
        )
        self.water_output_temperature_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.WaterOutputTemperatureDhw,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="Warm water output temperature",
        )
        self.energy_demand_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.EnergyDemandDhw,
            self.config.energy_carrier,
            lt.Units.WATT_HOUR,
            output_description="Energy demand",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
            energy_port=EnergyPort(lt.EnergyRole.IN, fuel_carrier),
        )
        self.thermal_output_power_dhw_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPowerDhw,
            load_type=lt.LoadTypes.WARM_WATER,
            unit=lt.Units.WATT,
            output_description="Thermal power output",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_output_energy_dhw_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputEnergyDhw,
            load_type=lt.LoadTypes.WARM_WATER,
            unit=lt.Units.WATT_HOUR,
            output_description="Thermal energy output",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
            energy_port=EnergyPort(
                lt.EnergyRole.OUT,
                lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT,
                peer_output=self.WaterOutputMassFlowDhw,
            ),
        )
        self.combustion_heat_loss_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CombustionHeatLoss,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            output_description=(
                "Fuel power of this step that did not become heat, the flue and burner loss: the fuel power times "
                "one minus the combustion efficiency while the boiler heats, zero while it is off."
            ),
            energy_port=EnergyPort(lt.EnergyRole.LOSS, fuel_carrier),
        )
        self.space_heating_circuit = BoilerCircuitChannels(
            return_temperature=self.water_input_temperature_sh_channel,
            supply_temperature=self.water_output_temperature_sh_channel,
            mass_flow=self.water_output_mass_flow_sh_channel,
            thermal_power=self.thermal_output_power_sh_channel,
            thermal_energy=self.thermal_output_energy_sh_channel,
            fuel_energy=self.energy_demand_sh_channel,
        )
        self.hot_water_circuit = BoilerCircuitChannels(
            return_temperature=self.water_input_temperature_dhw_channel,
            supply_temperature=self.water_output_temperature_dhw_channel,
            mass_flow=self.water_output_mass_flow_dhw_channel,
            thermal_power=self.thermal_output_power_dhw_channel,
            thermal_energy=self.thermal_output_energy_dhw_channel,
            fuel_energy=self.energy_demand_dhw_channel,
        )
        # Set important parameters
        self.build()
        # the burner's state committed at the end of the previous step, and the one this step's pass leaves
        self.committed_burner_state: BurnerState = BurnerState.at_start()
        self.burner_state: BurnerState = self.committed_burner_state
        self.fuel_consumption_in_liter: float = 0
        self.fuel_consumption_in_kg: float = 0

        self.add_default_connections(self.get_default_connections_from_controller_generic_boiler())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

    def get_default_connections_from_controller_generic_boiler(
        self,
    ):
        """Get Controller Generic Boiler default connections."""
        component_class = GenericBoilerController
        connections = []
        l1_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoiler.ControlSignal,
                l1_controller_classname,
                component_class.ControlSignalToGenericBoiler,
            )
        )
        connections.append(
            ComponentConnection(
                GenericBoiler.OperatingMode,
                l1_controller_classname,
                component_class.OperatingMode,
            )
        )
        connections.append(
            ComponentConnection(
                GenericBoiler.TemperatureDelta,
                l1_controller_classname,
                component_class.TemperatureDelta,
            )
        )
        connections.append(
            ComponentConnection(
                GenericBoiler.SupplyTemperatureSetForDHWInCelsius,
                l1_controller_classname,
                component_class.SupplyTemperatureSetForDHWInCelsius,
            )
        )
        for input_name, output_name in (
            (GenericBoiler.RequestedPartLoadRatioDhw, component_class.RequestedPartLoadRatioDhw),
            (GenericBoiler.RequestedPartLoadRatioSh, component_class.RequestedPartLoadRatioSh),
            (
                GenericBoiler.ControlSignalForSpaceHeatingRemainder,
                component_class.ControlSignalForSpaceHeatingRemainder,
            ),
            (
                GenericBoiler.TemperatureDeltaForSpaceHeatingRemainderInKelvin,
                component_class.TemperatureDeltaForSpaceHeatingRemainderInKelvin,
            ),
        ):
            connections.append(ComponentConnection(input_name, l1_controller_classname, output_name))
        return connections

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get Simple hot water storage default connections."""
        component_class = SimpleHotWaterStorage
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoiler.WaterInputTemperatureSh,
                hws_classname,
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get Simple dhw storage default connections."""
        component_class = SimpleDHWStorage
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoiler.WaterInputTemperatureDhw,
                hws_classname,
                component_class.StepMeanWaterTemperatureToHeatGeneratorInCelsius,
            )
        )
        return connections

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Get values from config
        self.energy_carrier = self.config.energy_carrier
        self.minimal_thermal_power_in_watt = self.config.minimal_thermal_power_in_watt
        self.maximal_thermal_power_in_watt = self.config.maximal_thermal_power_in_watt
        self.maximal_flow_temperature_in_celsius = self.config.maximal_flow_temperature_in_celsius
        self.efficiency_curve = BoilerEfficiencySources.curve(
            energy_carrier=self.energy_carrier,
            boiler_type=self.config.boiler_type,
            installation_year=self.config.installation_year,
            maximal_power_in_watt=self.maximal_thermal_power_in_watt,
            seasonal_efficiency=self.config.seasonal_efficiency,
        )
        self.burner = BurnerModulation(
            minimal_power_in_watt=self.minimal_thermal_power_in_watt,
            maximal_power_in_watt=self.maximal_thermal_power_in_watt,
            efficiency_curve=self.efficiency_curve,
        )
        #: The boiler's circulation pump: the flow that carries the commanded heat over the lift the controller asks for,
        #: ``m = P_th / (c lift)``. On a lift close to 0 K that flow has no bound (12 kW at 0.877 over 0.01 K is
        #: 252 kg/s), so the largest flow carries the maximal heat over ``minimal_lift_at_maximal_power_in_kelvin`` and
        #: a smaller lift is raised to the rise the heat gives at that flow; the supply then ends above the one asked for.
        self.pump: ControllerLiftPump = ControllerLiftPump.sized_for(
            maximal_heat_power_in_watt=self.maximal_thermal_power_in_watt * self.efficiency_curve.efficiency_at_full_load,
            minimal_lift_at_maximal_heat_in_kelvin=self.config.minimal_lift_at_maximal_power_in_kelvin,
        )
        self.burner_times = BurnerTimes.of_fuel(self.energy_carrier)
        if self.minimal_thermal_power_in_watt <= 0 < self.burner_times.minimum_firing_time_in_seconds:
            # a held burner burns at least its minimal power; at 0 W it carries no heat and its pump no flow
            raise ValueError(
                f"{self.component_name}: a {self.energy_carrier.value} burner keeps a minimum firing time and burns "
                "at least its minimal power while it is held lit, so its minimal_thermal_power_in_watt must be "
                f"above 0, not {self.minimal_thermal_power_in_watt}."
            )
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        for name in self.burner_times.times_shorter_than_a_step(seconds_per_timestep):
            log.warning(
                f"{self.component_name}: the burner's {name} is shorter than one timestep of {seconds_per_timestep} s, "
                "so it has no effect at this resolution (the burner counts its minimum times in whole timesteps, "
                "rounded down)."
            )

        # The fuel constants are derived by the config, which is also what contributes them
        # as sizing facts, so the meter accounting this boiler's consumption reads exactly
        # the numbers the boiler itself burns by. Condensing/conventional picks the higher
        # or the lower heating value; an unknown carrier still raises here, as it always did.
        self.heating_value_of_fuel_in_joule_per_m3 = GenericBoilerConfig.heating_value_in_joule_per_m3(
            self.energy_carrier, self.config.boiler_type
        )
        heating_value_in_kwh_per_liter, density_in_kg_per_m3 = GenericBoilerConfig.fuel_constants(
            self.energy_carrier, self.config.boiler_type
        )
        # ``fuel_constants`` returns None only for district heating, and the line above already
        # refused that carrier the way it always did, so both values are numbers here.
        assert heating_value_in_kwh_per_liter is not None and density_in_kg_per_m3 is not None
        self.heating_value_of_fuel_in_kwh_per_liter = heating_value_in_kwh_per_liter
        self.fuel_density_in_kg_per_m3 = density_in_kg_per_m3

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.config.get_string_dict()

    def i_save_state(self) -> None:
        """Commit the burner's state of the step that just converged: whether it was lit, and for how long."""
        self.committed_burner_state = self.burner_state

    def i_restore_state(self) -> None:
        """Start the pass from the burner's committed state, discarding what an earlier pass of this step left."""
        self.burner_state = self.committed_burner_state

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Fire the circuit the controller asks for, within the burner's minimum times, and publish both circuits.

        The controller sends a control signal, an operating mode and a lift. In space-heating or hot-water mode with
        a positive lift the boiler fires that mode's circuit (:meth:`commanded_firing`). In space-heating mode the
        hot-water circuit idles. In hot-water mode the rest of the step may fire the space-heating circuit as well,
        when the controller commands it a space-heating ratio, signal and lift (:meth:`space_heating_remainder_firing`);
        otherwise the space-heating circuit idles. Off, or without a lift, the controller asks for no fire.

        The burner then burns as asked, unless its minimum times (:class:`BurnerTimes`) hold it: a burner lit for less
        than its minimum firing time burns on, at the commanded power and at least its minimal power, and sends that
        heat to the space-heating circuit, the buffer an on/off burner charges, at the pump's largest flow for the whole
        step (:meth:`whole_step_firing` with a lift of 0 K). For example, a pellet burner whose controller switched to
        off burns at its minimal power, a twelfth of its maximal one, and one whose controller still names space heating
        with the buffer above its set temperature burns at full power. A burner out for less than its minimum pause
        stays out. Out, no circuit moves water and no fuel is burnt, except that a space-heating circuit on the
        distribution system's flow (``WaterInputMassFlowSh`` wired) carries that flow at its return. On that flow the
        space-heating circuit fires on the flow it reads, not on the lift, and a burner held lit sends its heat into
        that flow; with no flow it does not fire (:meth:`fired_on_distribution_flow`). The decision reads only this
        step's inputs and the burner's state committed at the end of the previous step, so every pass of a step decides
        the same way on the same inputs.

        The burner counts as lit in a step when it burns fuel (:attr:`BurnerState.is_lit`). A held burner whose
        space-heating return is already at the maximal flow temperature carries no heat, so it burns no fuel: it
        goes out, the way a boiler's high-limit thermostat stops its burner, and its minimum pause starts.

        Raises:
            ValueError: If the control signal is outside [0, 1] or the operating mode is unknown.
        """
        control_signal = stsv.get_input_value(self.control_signal_channel)
        operating_mode = stsv.get_input_value(self.operating_mode_channel)
        lift_in_kelvin = stsv.get_input_value(self.temperature_delta_channel)
        if not 0 <= control_signal <= 1:
            raise ValueError(f"Expected a control signal between 0 and 1, not {control_signal}")

        fired: Optional[BoilerCircuitChannels]
        if operating_mode == HeatingMode.SPACE_HEATING.value:
            fired = self.space_heating_circuit
        elif operating_mode == HeatingMode.DOMESTIC_HOT_WATER.value:
            fired = self.hot_water_circuit
        elif operating_mode == HeatingMode.OFF.value:
            fired = None
        else:
            raise ValueError(f"Unknown operating mode {operating_mode}")

        firings: Dict[str, BoilerFiring] = {}
        # the requested ratio of each circuit the controller has fired, keyed like the firings
        requested_ratios: Dict[str, float] = {}
        # a circuit without a lift moves no water, so it carries no heat and the controller asks for no fire
        if fired is not None and lift_in_kelvin > 0:
            firing, requested_ratio = self.commanded_firing(
                stsv, fired=fired, control_signal=control_signal, lift_in_kelvin=lift_in_kelvin
            )
            circuit_name = "hot_water" if fired is self.hot_water_circuit else "space_heating"
            firings[circuit_name], requested_ratios[circuit_name] = firing, requested_ratio
        if fired is self.hot_water_circuit:
            remainder = self.space_heating_remainder_firing(stsv)
            if remainder is not None:
                firings["space_heating"], requested_ratios["space_heating"] = remainder
        asked_to_burn = any(firing.fuel_power_in_watt > 0 for firing in firings.values())
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        burner_is_lit = self.burner_times.burner_is_lit(
            asked_to_burn=asked_to_burn,
            committed=self.committed_burner_state,
            seconds_per_timestep=seconds_per_timestep,
        )
        # From the requested to the actual ratios: a fired circuit runs its requested ratio, a circuit not fired runs 0,
        # and the burner's minimum times override both.
        actual_ratios = {name: requested_ratios.get(name, 0.0) for name in ("hot_water", "space_heating")}
        if burner_is_lit and not asked_to_burn:
            # held lit by the minimum firing time: the whole step to the space-heating buffer at the pump's largest flow
            firings = {
                "space_heating": self.whole_step_firing(
                    stsv, fired=self.space_heating_circuit, control_signal=control_signal, lift_in_kelvin=0.0
                )
            }
            actual_ratios = {"hot_water": 0.0, "space_heating": 1.0}
        elif asked_to_burn and not burner_is_lit:
            # held out by the minimum pause
            firings = {}
            actual_ratios = {"hot_water": 0.0, "space_heating": 0.0}
        self.burner_state = self.committed_burner_state.after_step(
            is_lit=any(firing.fuel_power_in_watt > 0 for firing in firings.values()),
            seconds_per_timestep=seconds_per_timestep,
        )
        self.dhw_part_load_command.publish_actual_ratio(stsv, actual_ratios["hot_water"])
        self.space_heating_part_load_command.publish_actual_ratio(stsv, actual_ratios["space_heating"])
        self.publish_firings(stsv, space_heating=firings.get("space_heating"), hot_water=firings.get("hot_water"))

    def space_heating_remainder_firing(self, stsv: SingleTimeStepValues) -> Optional[Tuple["BoilerFiring", float]]:
        """Return the space-heating charge in the rest of a hot-water step and its requested ratio, or None without one.

        The controller commands it with a requested space-heating ratio above 0 in a hot-water step and sends its
        control signal and lift. The charge is the space-heating circuit's firing at that signal and lift, run for the
        requested ratio's fraction of the step (:meth:`part_loaded_firing`).

        Args:
            stsv: The step's values.

        Returns:
            The firing and the requested ratio it fired at, or None.
        """
        requested_ratio = self.space_heating_part_load_command.requested_ratio(stsv)
        lift_in_kelvin = stsv.get_input_value(self.temperature_delta_for_space_heating_remainder_channel)
        control_signal = stsv.get_input_value(self.control_signal_for_space_heating_remainder_channel)
        if requested_ratio <= 0.0 or lift_in_kelvin <= 0.0 or control_signal <= 0.0:
            return None
        return self.commanded_firing(
            stsv, fired=self.space_heating_circuit, control_signal=control_signal, lift_in_kelvin=lift_in_kelvin
        )

    def publish_firings(
        self,
        stsv: SingleTimeStepValues,
        *,
        space_heating: Optional["BoilerFiring"],
        hot_water: Optional["BoilerFiring"],
    ) -> None:
        """Publish both circuits, each firing or idle, and the boiler's fuel power and combustion loss over both.

        A space-heating circuit on the distribution system's flow carries that flow while it idles too, at its return
        temperature and with no heat and no fuel, so its published flow is the flow it reads in every step.

        Args:
            stsv: The step's values.
            space_heating: The space-heating circuit's firing, or None while it idles.
            hot_water: The hot-water circuit's firing, or None while it idles.
        """
        if space_heating is None and self.space_heating_runs_on_distribution_flow():
            space_heating = BoilerFiring(
                fuel_power_in_watt=0.0,
                thermal_power_in_watt=0.0,
                mass_flow_in_kg_per_second=stsv.get_input_value(self.water_input_mass_flow_sh_channel),
                supply_temperature_in_celsius=stsv.get_input_value(self.water_input_temperature_sh_channel),
            )
        fuel_power_in_watt, combustion_loss_in_watt = 0.0, 0.0
        for circuit, firing in ((self.space_heating_circuit, space_heating), (self.hot_water_circuit, hot_water)):
            if firing is None:
                self.publish_idle_circuit(stsv, circuit)
                continue
            self.publish_firing(stsv, circuit, firing)
            fuel_power_in_watt += firing.fuel_power_in_watt
            combustion_loss_in_watt += firing.fuel_power_in_watt - firing.thermal_power_in_watt
        stsv.set_output_value(self.total_fuel_input_power_channel, fuel_power_in_watt)
        stsv.set_output_value(self.combustion_heat_loss_channel, combustion_loss_in_watt)

    def commanded_firing(
        self,
        stsv: SingleTimeStepValues,
        *,
        fired: "BoilerCircuitChannels",
        control_signal: float,
        lift_in_kelvin: float,
    ) -> Tuple["BoilerFiring", float]:
        """Return the firing of the circuit the controller commands, and the requested ratio it fires at.

        A lift smaller than the rise the commanded heat gives at the pump's largest flow is raised to that rise
        (:attr:`pump`), so the flow stays one a household circuit carries and the supply ends above the
        one asked for. The hot-water supply stops at the controller's set temperature and both supplies at the maximal
        flow temperature. Each circuit runs the requested ratio the controller commands for it
        (:meth:`part_loaded_firing`) on the circuit's whole-step firing (:meth:`whole_step_firing`); a space-heating
        circuit on the distribution system's flow keeps that flow (:meth:`part_loaded_firing_on_distribution_flow`).

        Args:
            stsv: The step's values, read for the circuit's return, the hot-water set temperature and the requested
                ratio.
            fired: The circuit the controller's mode names.
            control_signal: The share of the maximal burner power the controller commands, 0 to 1.
            lift_in_kelvin: The lift the controller asks for, in K, above 0.

        Returns:
            The circuit's firing, and the requested ratio it fired at. :meth:`i_simulate` turns it into the actual
            ratio, which the burner's minimum times may override.
        """
        firing = self.whole_step_firing(stsv, fired=fired, control_signal=control_signal, lift_in_kelvin=lift_in_kelvin)
        return_temperature_in_celsius = stsv.get_input_value(fired.return_temperature)
        command = (
            self.dhw_part_load_command if fired is self.hot_water_circuit else self.space_heating_part_load_command
        )
        requested_ratio = command.requested_ratio(stsv)
        if fired is self.space_heating_circuit and self.space_heating_runs_on_distribution_flow():
            firing = self.part_loaded_firing_on_distribution_flow(
                firing, part_load_ratio=requested_ratio, return_temperature_in_celsius=return_temperature_in_celsius
            )
            return firing, requested_ratio
        firing = self.part_loaded_firing(
            firing, part_load_ratio=requested_ratio, return_temperature_in_celsius=return_temperature_in_celsius
        )
        return firing, requested_ratio

    def whole_step_firing(
        self,
        stsv: SingleTimeStepValues,
        *,
        fired: "BoilerCircuitChannels",
        control_signal: float,
        lift_in_kelvin: float,
    ) -> "BoilerFiring":
        """Return the firing of a circuit that burns the whole step, before any part-load ratio is applied.

        A lift smaller than the rise the commanded heat gives at the pump's largest flow is raised to that rise
        (:attr:`pump`). The hot-water supply stops at the controller's set temperature and both supplies at
        the maximal flow temperature. A burner its minimum firing time holds lit although no fire is asked for fires
        the space-heating circuit this way with a lift of 0 K, so it runs at the pump's largest flow for the whole
        step: it is not part-loaded, since the controller that asked it to stop may command a space-heating ratio of
        0 above 600 s, and the burner would then go out before its minimum firing time. A space-heating circuit on the
        distribution system's flow (``WaterInputMassFlowSh`` wired) ignores the lift and fires on the flow it reads
        (:meth:`fired_on_distribution_flow`).

        Args:
            stsv: The step's values, read for the circuit's return, the hot-water set temperature and, on the
                distribution system's flow, that flow.
            fired: The circuit that fires.
            control_signal: The share of the maximal burner power the controller commands, 0 to 1.
            lift_in_kelvin: The lift the controller asks for, in K; 0 for a burner held lit.

        Returns:
            The circuit's firing over the whole step.
        """
        commanded_power_in_watt = self.burner.commanded_power_in_watt(control_signal)
        return_temperature_in_celsius = stsv.get_input_value(fired.return_temperature)
        if fired is self.space_heating_circuit and self.space_heating_runs_on_distribution_flow():
            return self.fired_on_distribution_flow(
                burner=self.burner,
                control_signal=control_signal,
                mass_flow_in_kg_per_second=stsv.get_input_value(self.water_input_mass_flow_sh_channel),
                return_temperature_in_celsius=return_temperature_in_celsius,
                maximal_supply_temperature_in_celsius=self.maximal_flow_temperature_in_celsius,
            )
        pumped = self.pump.operating_point(
            heat_power_in_watt=self.burner.heat_power_in_watt(commanded_power_in_watt, return_temperature_in_celsius),
            return_temperature_in_celsius=return_temperature_in_celsius,
            lift_to_supply_set_temperature_in_kelvin=lift_in_kelvin,
        )
        if fired is self.hot_water_circuit:
            supply_temperature_in_celsius = hydronics.capped_hot_water_supply_temperature_c(
                unthrottled_supply_c=pumped.supply_temperature_in_celsius,
                return_c=return_temperature_in_celsius,
                maximal_supply_c=self.maximal_flow_temperature_in_celsius,
                set_supply_c=stsv.get_input_value(self.supply_temperature_set_for_dhw_in_celsius_channel),
            )
        else:
            supply_temperature_in_celsius = hydronics.throttled_supply_temperature_c(
                unthrottled_supply_c=pumped.supply_temperature_in_celsius,
                return_c=return_temperature_in_celsius,
                supply_limit_c=self.maximal_flow_temperature_in_celsius,
            )
        return self.fired_circuit(
            burner=self.burner,
            control_signal=control_signal,
            pumped=pumped,
            return_temperature_in_celsius=return_temperature_in_celsius,
            supply_temperature_in_celsius=supply_temperature_in_celsius,
        )

    def space_heating_runs_on_distribution_flow(self) -> bool:
        """Return whether the space-heating circuit runs on the flow another component pumps: its flow input is wired.

        A boiler without a buffer between it and the heat distribution system is wired to the distribution system's
        flow (``WaterInputMassFlowSh``), since the distribution system pumps that circuit; every other boiler leaves
        the input unwired and pumps its own flow. For example, ``gas_boiler_household.energy_system.yaml`` wires it,
        and every boiler that charges a buffer does not. Read from the wiring in every call, which is one attribute
        lookup, so a test that wires the input after building the boiler sees it.

        Returns:
            True when the flow input is wired.
        """
        return self.water_input_mass_flow_sh_channel.source_output is not None

    @staticmethod
    def fired_on_distribution_flow(
        *,
        burner: "BurnerModulation",
        control_signal: float,
        mass_flow_in_kg_per_second: float,
        return_temperature_in_celsius: float,
        maximal_supply_temperature_in_celsius: float,
    ) -> "BoilerFiring":
        """Return the fuel, heat and supply of a space-heating circuit that heats a flow another component pumps.

        The heat distribution system pumps the circuit, so the boiler holds a power, not a lift: the burner burns at
        the power the control signal commands, and its heat raises the flow it reads by ``P_th / (m c)``. The supply
        is the return plus that rise, throttled at the maximal supply temperature
        (:func:`hydronics.throttled_supply_temperature_c`). The heat is what the flow carries,
        ``m c (T_sup - T_ret)``, booked with the flow read, so the boiler books the heat the distribution system
        receives. Unthrottled, the fuel is the commanded power; throttled, the burner cycles at its commanded power
        and the fuel is the heat over the efficiency at that power. With no flow (0 kg/s or less) the burner does not
        fire, the way a boiler's flow switch keeps it out: no fuel, no heat, and the supply at the return. The flow
        the circuit publishes is the flow read, in every case.

        Example: 0.27 kg/s returning at 27 °C and a burner at 10 kW with an efficiency of 0.95 there rise by
        9500 / (0.27 * 4180) = 8.42 K to 35.42 °C, carry 9500 W and burn 10 kW. On a 76 °C return, where the
        efficiency is 0.88, the 8800 W would rise by 7.80 K to 83.80 °C; the supply stops at 80 °C, the flow carries
        0.27 * 4180 * 4 = 4514.4 W and the burner burns 4514.4 / 0.88 = 5130 W.

        Why the boiler takes the distribution system's flow rather than the other way round: with the boiler pumping,
        its circuit would stop whenever it idles and it would restart on a stale return; with the distribution system
        pumping, the circuit runs whenever the room asks for heat (``roadmap/hydronic_coupling_spec.md``, D25 as
        amended on 2026-10-22).

        Args:
            burner: The burner subcomponent: its power band and efficiency curve.
            control_signal: The share of the maximal burner power the controller commands, dimensionless, 0 to 1.
            mass_flow_in_kg_per_second: The circuit's flow the distribution system pumps, in kg/s.
            return_temperature_in_celsius: The circuit's return temperature, in °C.
            maximal_supply_temperature_in_celsius: The highest supply temperature the boiler delivers, in °C.

        Returns:
            The fuel power, the heat, the mass flow (the one read) and the supply temperature of the circuit.
        """
        if mass_flow_in_kg_per_second <= 0.0:
            return BoilerFiring(
                fuel_power_in_watt=0.0,
                thermal_power_in_watt=0.0,
                mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
                supply_temperature_in_celsius=return_temperature_in_celsius,
            )
        commanded_power_in_watt = burner.commanded_power_in_watt(control_signal)
        rise_in_kelvin = burner.heat_power_in_watt(commanded_power_in_watt, return_temperature_in_celsius) / (
            hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * mass_flow_in_kg_per_second
        )
        unthrottled_supply_in_celsius = return_temperature_in_celsius + rise_in_kelvin
        supply_temperature_in_celsius = hydronics.throttled_supply_temperature_c(
            unthrottled_supply_c=unthrottled_supply_in_celsius,
            return_c=return_temperature_in_celsius,
            supply_limit_c=maximal_supply_temperature_in_celsius,
        )
        thermal_power_in_watt = hydronics.circuit_power_w(
            mass_flow_kg_per_s=mass_flow_in_kg_per_second,
            t_supply_c=supply_temperature_in_celsius,
            t_return_c=return_temperature_in_celsius,
        )
        fuel_power_in_watt = commanded_power_in_watt
        if supply_temperature_in_celsius < unthrottled_supply_in_celsius:
            fuel_power_in_watt = thermal_power_in_watt / burner.efficiency_at(
                commanded_power_in_watt, return_temperature_in_celsius
            )
        return BoilerFiring(
            fuel_power_in_watt=fuel_power_in_watt,
            thermal_power_in_watt=thermal_power_in_watt,
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
            supply_temperature_in_celsius=supply_temperature_in_celsius,
        )

    @staticmethod
    def fired_circuit(
        *,
        burner: "BurnerModulation",
        control_signal: float,
        pumped: PumpOperatingPoint,
        return_temperature_in_celsius: float,
        supply_temperature_in_celsius: float,
    ) -> "BoilerFiring":
        """Return the fuel, heat and flow of a boiler circuit that fires at a control signal on its pump's flow.

        The burner burns at the power the control signal commands and turns it into heat at its combustion
        efficiency (:class:`BurnerModulation`). The pump runs the flow that carries that heat over the lift
        (:attr:`pump`), and the circuit's heat is what its water carries from the return to the
        supply, ``m c (T_sup - T_ret)``. Unthrottled, the supply is the pump's, the heat is ``P_th`` and the fuel is
        the commanded power. Throttled, the supply is below that, the flow stays, the heat is smaller, and the burner
        cycles at its commanded power, so the fuel is the heat over the efficiency at that power. For example, a
        20 kW burner at full signal with a 30 K lift on a 70 °C return throttled to 80 °C carries a third of its heat
        and burns a third of its fuel.

        Args:
            burner: The burner subcomponent: its power band and efficiency curve.
            control_signal: The share of the maximal burner power the controller commands, dimensionless, 0 to 1.
            pumped: The boiler pump's operating point for the burner's heat: its flow, in kg/s, above 0, and the
                unthrottled supply that flow carries the heat to, in °C, the return plus the lift the circuit runs at.
            return_temperature_in_celsius: The circuit's return temperature, in °C.
            supply_temperature_in_celsius: The circuit's supply temperature after any throttling, in °C, at most the
                unthrottled supply.

        Returns:
            The fuel power, the heat, the mass flow and the supply temperature of the circuit.
        """
        commanded_power_in_watt = burner.commanded_power_in_watt(control_signal)
        efficiency = burner.efficiency_at(commanded_power_in_watt, return_temperature_in_celsius)
        thermal_power_in_watt = hydronics.circuit_power_w(
            mass_flow_kg_per_s=pumped.mass_flow_in_kg_per_second,
            t_supply_c=supply_temperature_in_celsius,
            t_return_c=return_temperature_in_celsius,
        )
        is_throttled = supply_temperature_in_celsius < pumped.supply_temperature_in_celsius
        fuel_power_in_watt = thermal_power_in_watt / efficiency if is_throttled else commanded_power_in_watt
        return BoilerFiring(
            fuel_power_in_watt=fuel_power_in_watt,
            thermal_power_in_watt=thermal_power_in_watt,
            mass_flow_in_kg_per_second=pumped.mass_flow_in_kg_per_second,
            supply_temperature_in_celsius=supply_temperature_in_celsius,
        )

    def publish_firing(
        self, stsv: SingleTimeStepValues, circuit: "BoilerCircuitChannels", firing: "BoilerFiring"
    ) -> None:
        """Publish a firing circuit's flow, supply, heat and fuel, and the boiler's fuel power and combustion loss.

        The energies of the step are the powers times the step length; the combustion loss is the fuel power that
        did not become the circuit's heat.
        """
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        joules_per_watt_hour = hydronics.UnitConversion.JOULES_PER_WATT_HOUR
        stsv.set_output_value(self.total_fuel_input_power_channel, firing.fuel_power_in_watt)
        stsv.set_output_value(
            self.combustion_heat_loss_channel, firing.fuel_power_in_watt - firing.thermal_power_in_watt
        )
        stsv.set_output_value(circuit.thermal_power, firing.thermal_power_in_watt)
        stsv.set_output_value(
            circuit.thermal_energy, firing.thermal_power_in_watt * seconds_per_timestep / joules_per_watt_hour
        )
        stsv.set_output_value(
            circuit.fuel_energy, firing.fuel_power_in_watt * seconds_per_timestep / joules_per_watt_hour
        )
        stsv.set_output_value(circuit.supply_temperature, firing.supply_temperature_in_celsius)
        stsv.set_output_value(circuit.mass_flow, firing.mass_flow_in_kg_per_second)

    @staticmethod
    def publish_idle_circuit(stsv: SingleTimeStepValues, circuit: "BoilerCircuitChannels") -> None:
        """Publish a circuit that does not fire: no flow, no heat, no fuel, and its supply equal to its return.

        An idle circuit moves no water, so its supply temperature carries no information; it is set to the return
        so that ``m c (T_sup - T_ret)`` is zero by both factors.
        """
        for channel in (circuit.thermal_power, circuit.thermal_energy, circuit.fuel_energy, circuit.mass_flow):
            stsv.set_output_value(channel, 0.0)
        stsv.set_output_value(circuit.supply_temperature, stsv.get_input_value(circuit.return_temperature))

    @staticmethod
    def part_loaded_firing(
        full_load: "BoilerFiring", *, part_load_ratio: float, return_temperature_in_celsius: float
    ) -> "BoilerFiring":
        """Return a circuit's fuel, heat and flow when the burner runs only a fraction of the step at full load.

        The circuit carries the averaged flow, the full-load flow times the ratio, at the full-load supply
        temperature; its heat is what that flow carries, ``m c (T_sup - T_ret)``, and the fuel is the ratio times the
        full-load fuel. A ratio of 1 returns the full-load firing unchanged. A ratio of 0 returns an idle circuit, as
        :meth:`hydronics.CircuitStep.idle` and :meth:`publish_idle_circuit` define it: no fuel, no heat, no flow, and
        the supply at the return. Example: 0.2 kg/s from 55 to 70 °C with 14 kW of fuel at a ratio of 0.25 gives
        0.05 kg/s at 70 °C, 3135 W of heat and 3500 W of fuel; at a ratio of 0 it gives 0 kg/s at 55 °C.

        Why the supply drops to the return at a ratio of 0: a circuit that moves no water has no supply of its own,
        and a full-load supply published with no flow reads as a firing burner in the outlet temperature column. The
        energies do not depend on it, since a store weights a supply by its flow.

        Args:
            full_load: The circuit's fuel in W, heat in W, flow in kg/s and supply in °C at full load over the whole
                step.
            part_load_ratio: The fraction of the step the burner runs, from 0 to 1.
            return_temperature_in_celsius: The circuit's return temperature, in °C; the supply at a ratio of 0.

        Returns:
            The circuit's firing at that ratio.
        """
        if part_load_ratio >= 1.0:
            return full_load
        if part_load_ratio <= 0.0:
            idle = hydronics.CircuitStep.idle(t_return_c=return_temperature_in_celsius)
            return BoilerFiring(
                fuel_power_in_watt=0.0,
                thermal_power_in_watt=idle.power_w,
                mass_flow_in_kg_per_second=idle.mass_flow_kg_per_s,
                supply_temperature_in_celsius=idle.t_supply_c,
            )
        mass_flow_in_kg_per_second = part_load_ratio * full_load.mass_flow_in_kg_per_second
        return BoilerFiring(
            fuel_power_in_watt=part_load_ratio * full_load.fuel_power_in_watt,
            thermal_power_in_watt=hydronics.circuit_power_w(
                mass_flow_kg_per_s=mass_flow_in_kg_per_second,
                t_supply_c=full_load.supply_temperature_in_celsius,
                t_return_c=return_temperature_in_celsius,
            ),
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
            supply_temperature_in_celsius=full_load.supply_temperature_in_celsius,
        )

    @staticmethod
    def part_loaded_firing_on_distribution_flow(
        full_load: "BoilerFiring", *, part_load_ratio: float, return_temperature_in_celsius: float
    ) -> "BoilerFiring":
        """Return a firing on the distribution system's flow when the burner runs only a fraction of the step.

        The distribution system pumps the circuit for the whole step, so the flow stays the one read; the burner
        fires the ratio's fraction of the step, so the heat and the fuel are the ratio times the full-load ones, and
        the supply averaged over the step lies the ratio's share of the full-load rise above the return. A ratio of 1
        returns the full-load firing unchanged; a ratio of 0 carries the flow at the return, with no heat and no
        fuel. Example: 0.27 kg/s from 27 to 35 °C with 9028.8 W of heat and 9500 W of fuel at a ratio of 0.25 gives
        0.27 kg/s at 29 °C, 2257.2 W of heat and 2375 W of fuel.

        Why it exists beside :meth:`part_loaded_firing`, whose averaged flow is the full-load flow times the ratio:
        here the flow is the distribution system's, not the boiler's, so scaling it would publish a flow the circuit
        does not carry. The controller commands whole steps to a boiler without a buffer, so a ratio below 1 reaches
        this law only from a controller wired to a buffer.

        Args:
            full_load: The circuit's firing over the whole step (:meth:`fired_on_distribution_flow`).
            part_load_ratio: The fraction of the step the burner runs, from 0 to 1.
            return_temperature_in_celsius: The circuit's return temperature, in °C.

        Returns:
            The circuit's firing at that ratio.
        """
        if part_load_ratio >= 1.0:
            return full_load
        return BoilerFiring(
            fuel_power_in_watt=part_load_ratio * full_load.fuel_power_in_watt,
            thermal_power_in_watt=part_load_ratio * full_load.thermal_power_in_watt,
            mass_flow_in_kg_per_second=full_load.mass_flow_in_kg_per_second,
            supply_temperature_in_celsius=return_temperature_in_celsius
            + part_load_ratio * (full_load.supply_temperature_in_celsius - return_temperature_in_celsius),
        )

    @staticmethod
    def get_cost_capex(
        config: GenericBoilerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""

        if config.energy_carrier == lt.LoadTypes.GAS:
            kpi_tag = KpiTagEnumClass.GAS_BOILER
            component_type = lt.ComponentType.GAS_HEATER
        elif config.energy_carrier == lt.LoadTypes.OIL:
            kpi_tag = KpiTagEnumClass.OIL_BOILER
            component_type = lt.ComponentType.OIL_HEATER
        elif config.energy_carrier == lt.LoadTypes.GREEN_HYDROGEN:
            kpi_tag = KpiTagEnumClass.HYDROGEN_BOILER
            component_type = lt.ComponentType.HYDROGEN_HEATER
        elif config.energy_carrier == lt.LoadTypes.PELLETS:
            kpi_tag = KpiTagEnumClass.PELLET_BOILER
            component_type = lt.ComponentType.PELLET_HEATER
        elif config.energy_carrier == lt.LoadTypes.WOOD_CHIPS:
            kpi_tag = KpiTagEnumClass.WOOD_CHIP_BOILER
            component_type = lt.ComponentType.WOOD_CHIP_HEATER
        else:
            raise ValueError(f"Energy carrier {config.energy_carrier} for generic_boiler not implemented yet.")

        unit = lt.Units.KILOWATT
        size_of_energy_system = concrete(config.maximal_thermal_power_in_watt) * 1e-3

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=component_type,
            unit=unit,
            size_of_energy_system=size_of_energy_system,
            config=config,
            kpi_tag=kpi_tag,
        )

        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of energy and maintenance costs."""
        sh_consumption_in_kilowatt_hour = None
        dhw_consumption_in_kwh = None
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.field_name == self.EnergyDemandSh
                and output.unit == lt.Units.WATT_HOUR
            ):
                sh_consumption_in_kilowatt_hour = round(sum(postprocessing_results.iloc[:, index]) * 1e-3, 1)
            if (
                output.component_name == self.component_name
                and output.field_name == self.EnergyDemandDhw
                and output.unit == lt.Units.WATT_HOUR
            ):
                dhw_consumption_in_kwh = round(sum(postprocessing_results.iloc[:, index]) * 1e-3, 1)

        assert sh_consumption_in_kilowatt_hour is not None
        assert dhw_consumption_in_kwh is not None
        self.config.consumption_in_kilowatt_hour = sh_consumption_in_kilowatt_hour + dhw_consumption_in_kwh

        self.fuel_consumption_in_liter = round(
            self.config.consumption_in_kilowatt_hour / self.heating_value_of_fuel_in_kwh_per_liter,
            1,
        )
        self.fuel_consumption_in_kg = round(
            self.fuel_consumption_in_liter * 1e-3 * self.fuel_density_in_kg_per_m3,
            1,
        )
        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
        )
        if self.energy_carrier == lt.LoadTypes.GAS:
            kpi_tag = KpiTagEnumClass.GAS_BOILER
            co2_per_unit = emissions_and_cost_factors.gas_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.gas_costs_in_euro_per_kwh
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.config.consumption_in_kilowatt_hour * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.OIL:
            kpi_tag = KpiTagEnumClass.OIL_BOILER
            co2_per_unit = emissions_and_cost_factors.oil_footprint_in_kg_per_l
            euro_per_unit = emissions_and_cost_factors.oil_costs_in_euro_per_l
            co2_per_simulated_period_in_kg = self.fuel_consumption_in_liter * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.fuel_consumption_in_liter * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.GREEN_HYDROGEN:
            kpi_tag = KpiTagEnumClass.HYDROGEN_BOILER
            co2_per_unit = emissions_and_cost_factors.green_hydrogen_gas_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.green_hydrogen_gas_costs_in_euro_per_kwh
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.config.consumption_in_kilowatt_hour * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.PELLETS:
            kpi_tag = KpiTagEnumClass.PELLET_BOILER
            co2_per_unit = emissions_and_cost_factors.pellet_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.pellet_costs_in_euro_per_t
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.fuel_consumption_in_kg / 1000 * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.WOOD_CHIPS:
            kpi_tag = KpiTagEnumClass.WOOD_CHIP_BOILER
            co2_per_unit = emissions_and_cost_factors.wood_chip_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.wood_chip_costs_in_euro_per_t
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.fuel_consumption_in_kg / 1000 * euro_per_unit

        else:
            raise ValueError(f"Energy carrier {self.energy_carrier} not implemented for Generic boiler.")

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=self.config.consumption_in_kilowatt_hour,
            consumption_for_domestic_hot_water_in_kwh=dhw_consumption_in_kwh,
            consumption_for_space_heating_in_kwh=sh_consumption_in_kilowatt_hour,
            loadtype=self.energy_carrier,
            kpi_tag=kpi_tag,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []
        opex_dataclass = self.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        assert isinstance(self.config, GenericBoilerConfig)
        capex_dataclass = self.get_cost_capex(self.config, self.my_simulation_parameters)

        # Energy related KPIs
        sh_thermal_energy_delivered_in_kilowatt_hour = None
        dhw_thermal_energy_delivered_in_kilowatt_hour = None
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.ThermalOutputEnergySh and output.unit == lt.Units.WATT_HOUR:
                    sh_thermal_energy_delivered_in_kilowatt_hour = round(
                        sum(postprocessing_results.iloc[:, index]) * 1e-3, 1
                    )
                if output.field_name == self.ThermalOutputEnergyDhw and output.unit == lt.Units.WATT_HOUR:
                    dhw_thermal_energy_delivered_in_kilowatt_hour = round(
                        sum(postprocessing_results.iloc[:, index]) * 1e-3, 1
                    )

        assert sh_thermal_energy_delivered_in_kilowatt_hour is not None
        assert dhw_thermal_energy_delivered_in_kilowatt_hour is not None
        total_thermal_energy_delivered_in_kilowatt_hour = (
            sh_thermal_energy_delivered_in_kilowatt_hour + dhw_thermal_energy_delivered_in_kilowatt_hour
        )
        thermal_energy_delivered_entry = KpiEntry(
            name="Total thermal energy delivered",
            unit="kWh",
            value=total_thermal_energy_delivered_in_kilowatt_hour,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(thermal_energy_delivered_entry)

        sh_thermal_energy_delivered_entry = KpiEntry(
            name="Thermal energy delivered for space heating",
            unit="kWh",
            value=sh_thermal_energy_delivered_in_kilowatt_hour,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(sh_thermal_energy_delivered_entry)
        dhw_thermal_energy_delivered_entry = KpiEntry(
            name="Thermal energy delivered for domestic hot water",
            unit="kWh",
            value=dhw_thermal_energy_delivered_in_kilowatt_hour,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_thermal_energy_delivered_entry)

        energy_consumption = KpiEntry(
            name=f"Total {self.energy_carrier.value} consumption (energy)",
            unit="kWh",
            value=opex_dataclass.total_consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(energy_consumption)

        sh_energy_consumption = KpiEntry(
            name=f"Energy {self.energy_carrier.value} consumption for space heating",
            unit="kWh",
            value=opex_dataclass.consumption_for_space_heating_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(sh_energy_consumption)

        dhw_energy_consumption = KpiEntry(
            name=f"Energy {self.energy_carrier.value} consumption for doemstic hot water",
            unit="kWh",
            value=opex_dataclass.consumption_for_domestic_hot_water_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_energy_consumption)

        fuel_consumption_l = KpiEntry(
            name=f"Total {self.energy_carrier.value} consumption (volume)",
            unit="l",
            value=self.fuel_consumption_in_liter,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(fuel_consumption_l)

        fuel_consumption_kg = KpiEntry(
            name=f"Total {self.energy_carrier.value} consumption (mass)",
            unit="kg",
            value=self.fuel_consumption_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(fuel_consumption_kg)

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


@dataclass_json
@dataclass
class GenericBoilerControllerConfig(ConfigBase):
    """Boiler Controller Config Class.

    The two control laws a burner can be run under are :meth:`preset_modulating` and
    :meth:`preset_on_off`; the boiler's power band is not part of either, being copied from
    the boiler this controller regulates.
    """

    MAIN_CLASS = "hisim.components.generic_boiler.GenericBoilerController"

    component_id: ComponentID
    #: Whether the controller modulates the burner continuously or switches it on and off.
    is_modulating: bool = True
    #: Lower end of the boiler's power band; copied from the boiler this controller regulates.
    minimal_thermal_power_in_watt: Sizable[float] = sized_field(rule=Size.MINIMAL_THERMAL_POWER_IN_WATT)
    #: Upper end of the boiler's power band; copied from the boiler this controller regulates.
    maximal_thermal_power_in_watt: Sizable[float] = sized_field(rule=Size.MAXIMAL_THERMAL_POWER_IN_WATT)
    set_temperature_difference_for_full_power: float = 5.0
    #: Shortest burn once an on/off controller switched the burner on. The controller counts it in
    #: whole timesteps, rounded down: 1500 s at 900 s per timestep is one timestep, and a value
    #: shorter than one timestep has no effect (the controller warns at construction). 0 means no
    #: minimum and does not warn; a negative value is refused.
    minimum_runtime_in_seconds: float = field(default=1800, metadata={"unit": lt.Units.SECONDS})
    #: Shortest pause once an on/off controller switched the burner off, in whole timesteps rounded
    #: down as :attr:`minimum_runtime_in_seconds` is. 0 means no minimum and does not warn; a
    #: negative value is refused.
    minimum_resting_time_in_seconds: float = field(default=1800, metadata={"unit": lt.Units.SECONDS})
    with_domestic_hot_water_preparation: bool = False
    #: If used as secondary heat generator for DHW in hybrid mode.
    secondary_mode: Optional[bool] = False
    hysteresis_water_temperature_offset: float = 10.0
    #: How long a hot-water charge has priority over space heating before it is interrupted for
    #: :attr:`space_heating_interval_after_hot_water_priority_in_seconds`, in s; 0 means absolute priority
    #: (:class:`~hisim.components.dual_circuit_system.HotWaterPriorityLimit`, which names the sources). Real
    #: controllers that have the limit ship 45 to 150 min. The default is 15 min, the owner's decision of 2026-10-21
    #: (hisim-room-air-dhw-dip-a5by): the space-heating buffer, sized at 20 l/kW, empties after 20 to 25 min of
    #: priority, and the room air, which has no heat capacity of its own in the building model, falls with it. On
    #: household_gas_building_sizer (full year 2021, 900 s) the quarter-hours with a room-air drop above 0.5 K during
    #: hot water were 246 with absolute priority, 243 with 60 / 30 min, 231 with 45 / 30 min and 16 with 15 / 15 min.
    maximum_hot_water_priority_time_in_seconds: float = field(default=900.0, metadata={"unit": lt.Units.SECONDS})
    #: How long space heating has the boiler after an interrupted hot-water charge before the charge resumes, in s.
    #: The default is 15 min, as long as the maximum priority time (the Siemens Albatros2 rule).
    space_heating_interval_after_hot_water_priority_in_seconds: float = field(
        default=900.0, metadata={"unit": lt.Units.SECONDS}
    )

    def __post_init__(self) -> None:
        """Refuse a minimum runtime or resting time that is negative or not finite, naming the field and the value.

        The controller turns both times into whole timesteps by rounding down, so a negative time would become 0 or
        a negative count, which means no minimum without a word. For example, the ``on_off`` preset's 0 s (no
        minimum) and a pellet boiler's 1800 s are accepted, -900 s is refused.

        The hot-water priority times are checked by
        :meth:`~hisim.components.dual_circuit_system.HotWaterPriorityLimit.require_valid_times`.

        Raises:
            ValueError: If ``minimum_runtime_in_seconds`` or ``minimum_resting_time_in_seconds`` is not a finite
                number of at least 0 s, or the hot-water priority times are invalid.
        """
        for field_name, seconds in (
            ("minimum_runtime_in_seconds", self.minimum_runtime_in_seconds),
            ("minimum_resting_time_in_seconds", self.minimum_resting_time_in_seconds),
        ):
            if not math.isfinite(seconds) or seconds < 0.0:
                raise ValueError(
                    f"The boiler's controller needs {field_name} to be a finite time of at least 0 s, "
                    f"got {seconds!r} s: 0 means no minimum, and a negative time would silently mean the same."
                )
        HotWaterPriorityLimit.require_valid_times(
            maximum_priority_time_in_seconds=self.maximum_hot_water_priority_time_in_seconds,
            space_heating_interval_in_seconds=self.space_heating_interval_after_hot_water_priority_in_seconds,
            controller_description="The boiler's controller",
        )

    @preset
    @classmethod
    def preset_modulating(cls, name: str) -> "GenericBoilerControllerConfig":
        """The continuously modulating control law, for a gas, oil or hydrogen boiler.

        A modulating burner follows the heat demand within its power band instead of cycling,
        which is what every condensing gas and oil boiler in this repository does. The power
        band itself is not part of the preset: it is copied from the boiler this controller
        regulates, so the two can never drift apart.
        """
        return cls(component_id=ComponentID(name=name), is_modulating=True)

    @preset
    @classmethod
    def preset_on_off(cls, name: str) -> "GenericBoilerControllerConfig":
        """The switching control law, for a burner that runs at one power or not at all.

        The minimum runtime and resting time are what make a switching burner tolerable for the
        device, and they differ per fuel: the values here are the ones the repository uses for a
        generic on/off boiler, while a pellet boiler wants 30 and 15 minutes and a wood chip
        boiler 60 and 30. Those are field overrides on this preset rather than presets of their
        own, because the fuel is not a second control law — it only moves two numbers.
        """
        return cls(
            component_id=ComponentID(name=name),
            is_modulating=False,
            minimum_runtime_in_seconds=0,
            minimum_resting_time_in_seconds=0,
        )


class GenericBoilerControllerState:
    """Data class that saves the state of the controller."""

    def __init__(
        self,
        on_off: int,
        activation_time_step: int,
        deactivation_time_step: int,
        percentage: float,
    ) -> None:
        """Initializes the heat pump controller state."""
        self.on_off: int = on_off
        self.activation_time_step: int = activation_time_step
        self.deactivation_time_step: int = deactivation_time_step
        self.percentage: float = percentage

    def clone(self) -> "GenericBoilerControllerState":
        """Copies the current instance."""
        return GenericBoilerControllerState(
            on_off=self.on_off,
            activation_time_step=self.activation_time_step,
            deactivation_time_step=self.deactivation_time_step,
            percentage=self.percentage,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def activate(self, timestep: int) -> None:
        """Activates the heat pump and remembers the time step."""
        self.on_off = 1
        self.activation_time_step = timestep

    def deactivate(self, timestep: int) -> None:
        """Deactivates the heat pump and remembers the time step."""
        self.on_off = 0
        self.deactivation_time_step = timestep


class GenericBoilerController(L1ControllerComponent):
    """Generic Boiler Controller.

    It takes data from other
    components and sends signal to the Generic_boiler for
    activation or deactivation.
    Modulating Power with respect to water temperature from storage if applied.

    Parameters
    ----------
    Components to connect to:
    (1) Generic_boiler (control_signal)

    The controller also owns how much of a step a hot-water charge runs (:class:`hisim.part_load.PartLoadControl`):
    it decides the mode on the tank's start-of-step temperature as before, and commands the requested part-load ratio
    ``RequestedPartLoadRatioDhw`` with it. At and below the part-load threshold the requested ratio is 1 whenever the
    charge runs. Above it the requested ratio follows :class:`hisim.part_load.PartLoadRule` from the actual ratio the
    boiler reports it ran with (``ActualPartLoadRatioDhw``) and the tank's start and end temperatures, so that the tank
    ends the step at the warm-water aim, the temperature at which the controller ends the charge
    (``TargetTemperatureDhwInCelsius``, 60 °C), or at most a few hundredths of a kelvin above it, except on a step the
    simulator forces to converge, which ends wherever its last pass left it.

    The minimum running time is kept here, in the controller, not in the boiler: a step held in hot-water mode only by
    it gets the rule's requested ratio like any other, which is 0 for a tank that starts at or above the aim, so the
    boiler does not fire on it. A fine-step run, which has no part load, keeps firing through the minimum running time.
    These times act on the controller's mode; the burner's own minimum firing time and minimum pause, which act on
    whether it burns, are the boiler's (:class:`BurnerTimes`).
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    WaterTemperatureInputFromWaterStorage = "WaterTemperatureInputFromWaterStorage"
    WaterTemperatureInputFromDHWStorage = "WaterTemperatureInputFromDHWStorage"
    #: The hot-water tank's temperature at the end of the step, which the requested part-load ratio is set against.
    WaterTemperatureAtEndOfStepFromDHWStorageInCelsius = "WaterTemperatureAtEndOfStepFromDHWStorageInCelsius"
    #: The actual part-load ratio of the boiler's hot-water charge: the fraction of the step it ran, as the boiler reports it.
    ActualPartLoadRatioDhw = "ActualPartLoadRatioDhw"
    #: The buffer's end-of-step temperature, and its end temperature without the boiler's circuit, which the
    #: requested space-heating part-load ratio is set from.
    WaterTemperatureAtEndOfStepFromWaterStorageInCelsius = "WaterTemperatureAtEndOfStepFromWaterStorageInCelsius"
    WaterTemperatureAtEndOfStepWithoutBoilerFromWaterStorageInCelsius = (
        "WaterTemperatureAtEndOfStepWithoutBoilerFromWaterStorageInCelsius"
    )
    #: The actual part-load ratio of the boiler's space-heating charge: the fraction of the step it ran, as the boiler
    #: reports it.
    ActualPartLoadRatioSh = "ActualPartLoadRatioSh"

    # The heating curve's signals: the space-heating supply temperature to aim at, and the heating season.
    SpaceHeatingSupplyTemperatureSetInCelsius = "SpaceHeatingSupplyTemperatureSetInCelsius"
    HeatingSeason = "HeatingSeason"

    # Outputs
    ControlSignalToGenericBoiler = "ControlSignalToGenericBoiler"
    OperatingMode = "OperatingMode"
    TemperatureDelta = "TemperatureDelta"
    #: The hot-water supply temperature the controller aims at: its 60 °C aim plus its hysteresis, 70 °C by default.
    SupplyTemperatureSetForDHWInCelsius = "SupplyTemperatureSetForDHWInCelsius"
    #: The requested part-load ratio of the boiler's hot-water charge: the fraction of the step it is to run.
    RequestedPartLoadRatioDhw = "RequestedPartLoadRatioDhw"
    #: The tank temperature at which the controller ends a hot-water charge, its warm-water aim.
    TargetTemperatureDhwInCelsius = "TargetTemperatureDhwInCelsius"
    #: The requested part-load ratio of the boiler's space-heating charge: the fraction of the step it is to run.
    RequestedPartLoadRatioSh = "RequestedPartLoadRatioSh"
    #: The buffer temperature a space-heating charge aims at: the heating curve's supply temperature.
    TargetTemperatureShInCelsius = "TargetTemperatureShInCelsius"
    #: The control signal and the lift of a space-heating charge in the rest of a hot-water step.
    ControlSignalForSpaceHeatingRemainder = "ControlSignalForSpaceHeatingRemainder"
    TemperatureDeltaForSpaceHeatingRemainderInKelvin = "TemperatureDeltaForSpaceHeatingRemainderInKelvin"

    #: The lift a charge asks for when the buffer starts at or above its target: none of its own, so the pump's
    #: largest flow sets the rise (:attr:`GenericBoiler.pump`). A positive value only so that the
    #: boiler fires.
    ASKED_LIFT_OF_A_CHARGE_ABOVE_THE_TARGET_IN_KELVIN: ClassVar[float] = 0.1

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericBoilerControllerConfig,
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

        # warm water should always have at least 55°C, should be 60°C when leaving heat generator, see source below
        # https://www.umweltbundesamt.de/umwelttipps-fuer-den-alltag/heizen-bauen/warmwasser#undefined
        self.warm_water_temperature_aim_in_celsius: float = 60.0
        DiverterValve.require_valid_hysteresis(
            hysteresis_in_kelvin=self.config.hysteresis_water_temperature_offset,
            set_temperature_in_celsius=self.warm_water_temperature_aim_in_celsius,
            field_name="hysteresis_water_temperature_offset",
            controller_description="The boiler's controller",
        )
        # the supply temperature a hot-water charge aims at: the warm-water aim plus the hysteresis
        self.hot_water_supply_temperature_set_in_celsius: float = (
            self.warm_water_temperature_aim_in_celsius + self.config.hysteresis_water_temperature_offset
        )

        self.minimum_runtime_in_timesteps = self.whole_timesteps(
            "minimum_runtime_in_seconds", self.config.minimum_runtime_in_seconds
        )
        self.minimum_resting_time_in_timesteps = self.whole_timesteps(
            "minimum_resting_time_in_seconds", self.config.minimum_resting_time_in_seconds
        )

        self.state: GenericBoilerControllerState = GenericBoilerControllerState(0, 0, 0, 0)
        # the hot-water priority timer at the end of the last unforced pass, and at the start of the step
        self.hot_water_priority_timer = HotWaterPriorityTimer()
        self.hot_water_priority_timer_at_start_of_step = HotWaterPriorityTimer()

        self.build()

        # input channel
        self.water_temperature_space_heating_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromWaterStorage,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        if self.config.with_domestic_hot_water_preparation:
            self.water_temperature_dhw_input_channel: ComponentInput = self.add_input(
                self.component_name,
                self.WaterTemperatureInputFromDHWStorage,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
                True,
            )

        self.dhw_part_load = PartLoadControl(
            self,
            end_temperature_input_name=self.WaterTemperatureAtEndOfStepFromDHWStorageInCelsius,
            actual_ratio_input_name=self.ActualPartLoadRatioDhw,
            requested_ratio_output_name=self.RequestedPartLoadRatioDhw,
            target_output_name=self.TargetTemperatureDhwInCelsius,
            device_description="the boiler's hot-water charge",
            controls_a_store=self.config.with_domestic_hot_water_preparation,
        )

        self.space_heating_part_load = StartOfStepPartLoadControl(
            self,
            end_temperature_input_name=self.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius,
            end_temperature_without_circuit_input_name=(
                self.WaterTemperatureAtEndOfStepWithoutBoilerFromWaterStorageInCelsius
            ),
            actual_ratio_input_name=self.ActualPartLoadRatioSh,
            requested_ratio_output_name=self.RequestedPartLoadRatioSh,
            device_description="the boiler's space-heating charge",
            target_output_name=self.TargetTemperatureShInCelsius,
        )

        self.supply_temperature_set_channel: ComponentInput = self.add_input(
            self.component_name,
            self.SpaceHeatingSupplyTemperatureSetInCelsius,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.heating_season_channel: ComponentInput = self.add_input(
            self.component_name,
            self.HeatingSeason,
            lt.LoadTypes.ANY,
            lt.Units.BINARY,
            True,
        )

        self.control_signal_to_generic_boiler_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ControlSignalToGenericBoiler,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="Control signal for modulation of boiler.",
        )
        self.operating_mode_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.OperatingMode,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Operating mode.",
            aggregation=lt.OutputAggregation.NONE,
        )
        self.temperature_delta_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureDelta,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.KELVIN,
            output_description="Temperature difference between actual and set water temperature.",
        )
        self.control_signal_for_space_heating_remainder_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ControlSignalForSpaceHeatingRemainder,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description=(
                "The control signal of a space-heating charge in the rest of a hot-water step: full power while the "
                "buffer gets such a charge (above the part-load threshold), 0 otherwise."
            ),
        )
        self.temperature_delta_for_space_heating_remainder_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureDeltaForSpaceHeatingRemainderInKelvin,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.KELVIN,
            output_description=(
                "The lift of a space-heating charge in the rest of a hot-water step: the heating curve's supply "
                "temperature less the buffer's start temperature, at least a small positive lift; 0 without it."
            ),
        )
        self.supply_temperature_set_for_dhw_in_celsius_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.SupplyTemperatureSetForDHWInCelsius,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=(
                "The hot-water supply temperature the controller aims at, the warm-water aim plus the hysteresis "
                "(70 °C by default). The boiler's hot-water supply stops at it, so a charge ends at its target inside "
                "the step."
            ),
        )

        self.controller_mode: HeatingMode
        self.previous_controller_mode: HeatingMode

        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_dhw_storage())
        self.add_default_connections(self.get_default_connections_from_heating_curve())
        self.add_default_connections(self.get_default_connections_from_generic_boiler())

    def whole_timesteps(self, field_name: str, seconds: float) -> int:
        """A minimum time of the configuration in whole timesteps, rounded down; warns when it has no effect.

        Example: 1500 s at 900 s per timestep is one timestep; 600 s is zero timesteps, so the
        controller logs one warning that the field has no effect at this resolution. Rounding down
        keeps a pause or burn from being stretched beyond what was configured, which at a coarse
        timestep would cost heat production.

        Args:
            field_name: The configuration field, named in the warning.
            seconds: Its value.

        Returns:
            The whole timesteps the controller holds the burner's state for.
        """
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        if 0 < seconds < seconds_per_timestep:
            log.warning(
                f"{self.component_name}: {field_name} = {seconds} s is shorter than one timestep of "
                f"{seconds_per_timestep} s, so it has no effect at this resolution (the controller counts "
                "minimum times in whole timesteps, rounded down)."
            )
        return BurnerTimes.whole_steps(seconds, seconds_per_timestep)

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple_water_storage default connections."""

        connections = []
        storage_classname = SimpleHotWaterStorage.get_classname()
        # the sensor is the buffer's start-of-step temperature, which no pass of the step moves, so the on/off
        # decision cannot cycle with the temperatures the buffer publishes to its circuits
        connections.append(
            ComponentConnection(
                GenericBoilerController.WaterTemperatureInputFromWaterStorage,
                storage_classname,
                SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius,
            )
        )
        connections.append(
            ComponentConnection(
                GenericBoilerController.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius,
                storage_classname,
                SimpleHotWaterStorage.WaterTemperatureAtEndOfStepInCelsius,
            )
        )
        connections.append(
            ComponentConnection(
                GenericBoilerController.WaterTemperatureAtEndOfStepWithoutBoilerFromWaterStorageInCelsius,
                storage_classname,
                SimpleHotWaterStorage.WaterTemperatureAtEndOfStepWithoutHeatGeneratorInCelsius,
            )
        )
        return connections

    def get_default_connections_from_dhw_storage(
        self,
    ) -> List[ComponentConnection]:
        """Get default connections from DHW storage."""
        connections = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoilerController.WaterTemperatureInputFromDHWStorage,
                storage_classname,
                # the tank's start-of-step temperature T0: the controller decides on a value the step's
                # iteration does not move
                SimpleDHWStorage.WaterTemperatureAtStartOfStepInCelsius,
            )
        )
        connections.append(
            ComponentConnection(
                GenericBoilerController.WaterTemperatureAtEndOfStepFromDHWStorageInCelsius,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureAtEndOfStepInCelsius,
            )
        )
        return connections

    def get_default_connections_from_generic_boiler(self) -> List[ComponentConnection]:
        """Return the default connections from the boiler: the actual part-load ratios its two charges ran with.

        The controller sets the next requested ratios from the actual ratios the boiler reports, so the boiler's reports
        are wired back to it. A boiler without hot water reports 0 for it on every step.

        Returns:
            The connections of ``ActualPartLoadRatioDhw`` and ``ActualPartLoadRatioSh``.
        """
        return [
            ComponentConnection(
                GenericBoilerController.ActualPartLoadRatioDhw,
                GenericBoiler.get_classname(),
                GenericBoiler.ActualPartLoadRatioDhw,
            ),
            ComponentConnection(
                GenericBoilerController.ActualPartLoadRatioSh,
                GenericBoiler.get_classname(),
                GenericBoiler.ActualPartLoadRatioSh,
            ),
        ]

    @staticmethod
    def get_default_connections_from_heating_curve() -> List[ComponentConnection]:
        """Return the connections of the supply temperature set point and the heating season from the heating curve.

        Returns:
            The two connections.
        """
        return [
            ComponentConnection(
                GenericBoilerController.SpaceHeatingSupplyTemperatureSetInCelsius,
                HeatingCurveController.get_classname(),
                HeatingCurveController.SpaceHeatingSupplyTemperatureSetInCelsius,
            ),
            ComponentConnection(
                GenericBoilerController.HeatingSeason,
                HeatingCurveController.get_classname(),
                HeatingCurveController.HeatingSeason,
            ),
        ]

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_mode = HeatingMode.OFF
        self.previous_controller_mode = self.controller_mode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state: the mode, and the hot-water priority timer the step starts from.

        Every unforced pass decides the step from the timer saved here; a forced pass keeps the timer of the last
        unforced pass, as it keeps that pass's outputs, so the timer always matches what the controller published.
        """
        self.previous_controller_mode = self.controller_mode
        self.hot_water_priority_timer_at_start_of_step = self.hot_water_priority_timer

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_mode = self.previous_controller_mode

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(
        self,
    ) -> List[str]:
        """Write important variables to report."""
        return self.config.get_string_dict()

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Decide the boiler's mode and power on the stores' start temperatures, and its requested part-load ratios.

        Under ``force_convergence`` every output keeps the value of the last pass, except the requested part-load
        ratios above the threshold (the space-heating one only with the buffer wired): each port then carries the
        actual ratio the boiler reports it ran with, so the charge stops changing (``publish_held``). At and below the
        threshold the requested ratios keep their last values too.
        """

        if force_convergence:
            self.dhw_part_load.publish_held(stsv)
            self.space_heating_part_load.publish_held(stsv)
        else:
            # Retrieves inputs
            water_temperature_input_from_space_heating_water_storage_in_celsius = stsv.get_input_value(
                self.water_temperature_space_heating_input_channel
            )

            water_temperature_input_from_dhw_water_storage_in_celsius = None
            if self.config.with_domestic_hot_water_preparation:
                water_temperature_input_from_dhw_water_storage_in_celsius = stsv.get_input_value(
                    self.water_temperature_dhw_input_channel
                )

            heating_flow_temperature_from_heat_distribution_system = stsv.get_input_value(
                self.supply_temperature_set_channel
            )

            control_signal, temperature_delta = self.determine_operating_mode(
                stsv.get_input_value(self.heating_season_channel) > 0.5,
                water_temperature_input_from_space_heating_water_storage_in_celsius,
                water_temperature_input_from_dhw_water_storage_in_celsius,
                heating_flow_temperature_from_heat_distribution_system,
                timestep,
            )

            stsv.set_output_value(self.control_signal_to_generic_boiler_channel, control_signal)
            stsv.set_output_value(self.operating_mode_channel, self.controller_mode.value)
            stsv.set_output_value(self.temperature_delta_channel, temperature_delta)
            stsv.set_output_value(
                self.supply_temperature_set_for_dhw_in_celsius_channel, self.hot_water_supply_temperature_set_in_celsius
            )
            requested_hot_water_ratio = self.dhw_part_load.publish(
                stsv,
                device_runs=self.controller_mode == HeatingMode.DOMESTIC_HOT_WATER,
                start_temperature_in_celsius=water_temperature_input_from_dhw_water_storage_in_celsius or 0.0,
                target_temperature_in_celsius=self.warm_water_temperature_aim_in_celsius,
            )
            self.publish_space_heating_part_load(
                stsv,
                is_heating_season=stsv.get_input_value(self.heating_season_channel) > 0.5,
                buffer_start_temperature_in_celsius=water_temperature_input_from_space_heating_water_storage_in_celsius,
                supply_temperature_set_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                requested_hot_water_ratio=requested_hot_water_ratio,
                temperature_delta_in_kelvin=temperature_delta,
            )

    def publish_space_heating_part_load(
        self,
        stsv: SingleTimeStepValues,
        *,
        is_heating_season: bool,
        buffer_start_temperature_in_celsius: float,
        supply_temperature_set_in_celsius: float,
        requested_hot_water_ratio: float,
        temperature_delta_in_kelvin: float,
    ) -> None:
        """Publish the requested space-heating ratio, and the commands of its two special cases.

        At and below the part-load threshold, or without a buffer, a space-heating step runs whole, as always. Above it
        the fraction ends the buffer at the heating curve's supply temperature
        (:class:`hisim.part_load.StartOfStepPartLoadControl`), with two cases a whole-step run never needed:

        * **A buffer at or above its target that its draw would empty.** The buffer is booked on its start temperature
          and does not see its own fall within the step, so waiting for the next step would let the distribution
          system drain it first. The charge runs the fraction that ends it at the target, at full power and with no
          lift of its own (:attr:`ASKED_LIFT_OF_A_CHARGE_ABOVE_THE_TARGET_IN_KELVIN`).
        * **The rest of a hot-water step.** A hot-water charge that ends inside the step (requested ratio below 1)
          leaves the rest of the step; in the heating season the space-heating charge may run in it, at most that rest,
          at full power, with the lift to the supply temperature.

        Args:
            stsv: The step's values.
            is_heating_season: Whether the heating curve names a heating day.
            buffer_start_temperature_in_celsius: The buffer's start-of-step temperature, in °C.
            supply_temperature_set_in_celsius: The heating curve's supply temperature, the charge's target, in °C.
            requested_hot_water_ratio: The requested hot-water ratio published in this pass, from 0 to 1.
            temperature_delta_in_kelvin: The lift :meth:`determine_operating_mode` asked for, in K.
        """
        part = self.space_heating_part_load
        remainder_signal, remainder_lift_in_kelvin = 0.0, 0.0
        if self.controller_mode == HeatingMode.SPACE_HEATING:
            requested_space_heating_ratio = part.publish_raising(
                stsv, device_runs=True, target_temperature_in_celsius=supply_temperature_set_in_celsius
            )
            charges_a_buffer_at_its_target = temperature_delta_in_kelvin <= 0.0
            if part.runs_fractions() and requested_space_heating_ratio > 0.0 and charges_a_buffer_at_its_target:
                stsv.set_output_value(self.control_signal_to_generic_boiler_channel, 1.0)
                stsv.set_output_value(
                    self.temperature_delta_channel, self.ASKED_LIFT_OF_A_CHARGE_ABOVE_THE_TARGET_IN_KELVIN
                )
        else:
            rest_of_a_hot_water_step = (
                self.controller_mode == HeatingMode.DOMESTIC_HOT_WATER
                and part.runs_fractions()
                and is_heating_season
                and requested_hot_water_ratio < 1.0
            )
            requested_space_heating_ratio = part.publish_raising(
                stsv,
                device_runs=rest_of_a_hot_water_step,
                target_temperature_in_celsius=supply_temperature_set_in_celsius,
                budget=1.0 - requested_hot_water_ratio,
            )
            if requested_space_heating_ratio > 0.0:
                remainder_signal = 1.0
                remainder_lift_in_kelvin = max(
                    supply_temperature_set_in_celsius - buffer_start_temperature_in_celsius,
                    self.ASKED_LIFT_OF_A_CHARGE_ABOVE_THE_TARGET_IN_KELVIN,
                )
        stsv.set_output_value(self.control_signal_for_space_heating_remainder_channel, remainder_signal)
        stsv.set_output_value(self.temperature_delta_for_space_heating_remainder_channel, remainder_lift_in_kelvin)

    def determine_operating_mode(
        self,
        is_heating_season: bool,
        water_temperature_input_from_space_heating_water_storage_in_celsius: float,
        water_temperature_input_from_dhw_water_storage_in_celsius: Optional[float],
        heating_flow_temperature_from_heat_distribution_system: float,
        timestep,
    ) -> Tuple[float, float]:
        """Determine which operating mode to use in dual-circuit system."""

        previous_controller_mode = self.controller_mode
        timer = self.hot_water_priority_timer_at_start_of_step
        self.controller_mode = DiverterValve.determine_operating_mode(
            with_domestic_hot_water_preparation=self.config.with_domestic_hot_water_preparation,
            # an interrupted charge stays latched while space heating has the boiler, so it resumes after the interval
            current_controller_mode=(
                HeatingMode.DOMESTIC_HOT_WATER if timer.charge_interrupted else previous_controller_mode
            ),
            is_heating_season=is_heating_season,
            water_temperature_input_sh_in_celsius=water_temperature_input_from_space_heating_water_storage_in_celsius,
            water_temperature_input_dhw_in_celsius=water_temperature_input_from_dhw_water_storage_in_celsius,
            set_temperatures=SetTemperatureConfig(
                set_temperature_space_heating_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                set_temperature_dhw_in_celsius=self.warm_water_temperature_aim_in_celsius,
                hysteresis_water_temperature_offset_in_celsius=self.config.hysteresis_water_temperature_offset,
            ),
            parallel_space_heating_and_dhw_option=False,
        )
        self.limit_hot_water_priority(
            timer,
            space_heating_requested=DiverterValve.space_heating_needed(
                is_heating_season=is_heating_season,
                water_temperature_in_celsius=water_temperature_input_from_space_heating_water_storage_in_celsius,
                set_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                hysteresis_in_kelvin=self.config.hysteresis_water_temperature_offset,
            ),
        )

        # Enforce minimum run and idle times (if necessary overwrites previously set mode)
        self.enforce_minimum_run_and_idle_times(previous_controller_mode, timestep)

        if self.controller_mode == HeatingMode.SPACE_HEATING:
            # get a modulated control signal between 0 and 1
            if self.config.is_modulating is True:
                control_signal = self.modulate_power(
                    water_temperature_input_in_celsius=water_temperature_input_from_space_heating_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                )
            else:
                control_signal = 1
            temperature_delta = (
                heating_flow_temperature_from_heat_distribution_system
                - water_temperature_input_from_space_heating_water_storage_in_celsius
            )
        elif self.controller_mode == HeatingMode.DOMESTIC_HOT_WATER:
            assert self.config.with_domestic_hot_water_preparation is not None
            assert water_temperature_input_from_dhw_water_storage_in_celsius is not None
            if self.config.is_modulating is True:
                control_signal = self.modulate_power(
                    water_temperature_input_in_celsius=water_temperature_input_from_dhw_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=self.hot_water_supply_temperature_set_in_celsius,
                )
            else:
                control_signal = 1
            temperature_delta = max(
                self.hot_water_supply_temperature_set_in_celsius
                - water_temperature_input_from_dhw_water_storage_in_celsius,
                0,
            )
        elif self.controller_mode == HeatingMode.OFF:
            control_signal = 0
            temperature_delta = 0
        else:
            raise ValueError("Controller mode unknown.")

        return control_signal, temperature_delta

    def limit_hot_water_priority(self, timer: HotWaterPriorityTimer, *, space_heating_requested: bool) -> None:
        """Hand a hot-water step to space heating once the charge has had priority for its maximum time.

        The diverter valve's mode is the request; :meth:`HotWaterPriorityLimit.decide` says whether the charge runs
        in this step. A step it yields becomes a space-heating step, and the timer it returns keeps the charge latched
        (``charge_interrupted``) until the charge resumes and ends at the warm-water aim. For example, at 900 s with
        the default 15 / 15 minutes on a winter day, a charge that needs three steps runs on the first, third and fifth
        step, and space heating has the second and the fourth.

        Args:
            timer: The hot-water priority timer at the start of the step.
            space_heating_requested: Whether the space-heating circuit asks for heat in this step
                (:meth:`DiverterValve.space_heating_needed`).
        """
        charges, self.hot_water_priority_timer = HotWaterPriorityLimit.decide(
            timer=timer,
            charge_requested=self.controller_mode == HeatingMode.DOMESTIC_HOT_WATER,
            space_heating_requested=space_heating_requested,
            maximum_priority_time_in_seconds=self.config.maximum_hot_water_priority_time_in_seconds,
            space_heating_interval_in_seconds=self.config.space_heating_interval_after_hot_water_priority_in_seconds,
            seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep,
        )
        if self.controller_mode == HeatingMode.DOMESTIC_HOT_WATER and not charges:
            self.controller_mode = HeatingMode.SPACE_HEATING

    def enforce_minimum_run_and_idle_times(self, previous_controller_mode: HeatingMode, timestep: int) -> None:
        """Enforces minimum run and idle times."""
        if (
            previous_controller_mode != HeatingMode.OFF
            and self.state.activation_time_step + self.minimum_runtime_in_timesteps > timestep
            and self.controller_mode == HeatingMode.OFF
        ):
            # mandatory on, minimum runtime not reached
            self.controller_mode = previous_controller_mode
        if (
            previous_controller_mode == HeatingMode.OFF
            and self.state.deactivation_time_step + self.minimum_resting_time_in_timesteps > timestep
            and self.controller_mode != HeatingMode.OFF
        ):
            self.controller_mode = HeatingMode.OFF
            # mandatory off, minimum resting time not reached

    def modulate_power(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
    ) -> float:
        """Modulate linearly between minimial_thermal_power and max_thermal_power of Generic Boiler.

        Use only when boiler is in operating mode, as this function will not go below the
        percentage required to fullfill the minimum thermal power requirement.
        """

        minimal_percentage = float(
            self.config.minimal_thermal_power_in_watt / self.config.maximal_thermal_power_in_watt
        )
        if (
            water_temperature_input_in_celsius
            < set_heating_flow_temperature_in_celsius - self.config.set_temperature_difference_for_full_power
        ):
            percentage = 1.0
            return percentage
        if water_temperature_input_in_celsius < set_heating_flow_temperature_in_celsius:
            linear_fit = 1.0 - (
                (
                    self.config.set_temperature_difference_for_full_power
                    - (set_heating_flow_temperature_in_celsius - water_temperature_input_in_celsius)
                )
                / self.config.set_temperature_difference_for_full_power
            )
            percentage = float(max(minimal_percentage, linear_fit))
            return percentage  # type: ignore
        if water_temperature_input_in_celsius <= set_heating_flow_temperature_in_celsius:
            percentage = minimal_percentage
            return percentage  # type: ignore

        return minimal_percentage

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: GenericBoilerControllerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
