"""Compatibility adapter: legacy components -> ComponentCostFacts (cost_spec.md §10.0 rule 4).

The new engine never calls the legacy `get_cost_capex`/`get_cost_opex` methods (they mutate
configs as a side effect). Facts come from `get_cost_facts()` where a component has adopted
the new API, and otherwise from this adapter, which maps known component classes and their
configs to facts directly. The adapter shrinks as adoption grows (§10.1 Phase 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from hisim import log
from hisim import loadtypes as lt
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import ComponentCostFacts, CostRelevance
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass


@dataclass
class MeterSpec:
    """How to read a meter's carrier flows from the postprocessing results."""

    carrier: EnergyCarrier
    bought_field: str  # output field name holding bought energy per timestep (Wh)
    sold_field: Optional[str] = None
    power_field: Optional[str] = None  # instantaneous power series (W) for peak computation
    # Converts kWh to the carrier's native billing quantity (liters, tons); identity default.
    quantity_conversion: Callable[[float, Any], float] = lambda kwh, config: kwh


def _quantity_value(value: Any) -> float:
    """Unwraps hisim.units Quantity objects."""
    return float(getattr(value, "value", value))


def _heat_pump_facts(config: Any) -> ComponentCostFacts:
    return ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=_quantity_value(config.set_thermal_output_power_in_watt) * 1e-3,
        size_unit=Units.KILOWATT,
        kpi_tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
    )


def _boiler_facts(config: Any) -> Optional[ComponentCostFacts]:
    carrier_map = {
        lt.LoadTypes.GAS: (ComponentType.GAS_HEATER, KpiTagEnumClass.GAS_BOILER),
        lt.LoadTypes.OIL: (ComponentType.OIL_HEATER, KpiTagEnumClass.OIL_BOILER),
        lt.LoadTypes.GREEN_HYDROGEN: (ComponentType.HYDROGEN_HEATER, KpiTagEnumClass.HYDROGEN_BOILER),
        lt.LoadTypes.PELLETS: (ComponentType.PELLET_HEATER, KpiTagEnumClass.PELLET_BOILER),
        lt.LoadTypes.WOOD_CHIPS: (ComponentType.WOOD_CHIP_HEATER, KpiTagEnumClass.WOOD_CHIP_BOILER),
    }
    mapping = carrier_map.get(config.energy_carrier)
    if mapping is None:
        return None
    asset_class, kpi_tag = mapping
    return ComponentCostFacts(
        asset_class=asset_class,
        size=config.maximal_thermal_power_in_watt * 1e-3,
        size_unit=Units.KILOWATT,
        kpi_tag=kpi_tag,
    )


def _hds_facts(config: Any) -> Optional[ComponentCostFacts]:
    heating_system = getattr(config, "heating_system", None)
    heating_value = getattr(heating_system, "value", heating_system)
    if heating_value in (2, "FloorHeating"):
        asset_class = ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
    elif heating_value in (1, "Radiator"):
        asset_class = ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR
    else:
        return None  # low-temperature radiators have no cost database entry yet
    return ComponentCostFacts(
        asset_class=asset_class,
        size=config.absolute_conditioned_floor_area_in_m2,
        size_unit=Units.SQUARE_METER,
        kpi_tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
    )


#: Class-name keyed extraction table (avoids importing component modules; §10.0).
_FACTS_EXTRACTORS: Dict[str, Callable[[Any], Optional[ComponentCostFacts]]] = {
    "HeatPumpHplib": _heat_pump_facts,
    "MoreAdvancedHeatPumpHPLib": lambda config: ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=_quantity_value(config.set_thermal_output_power_in_watt) * 1e-3,
        size_unit=Units.KILOWATT,
        kpi_tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING_AND_DOMESTIC_HOT_WATER,
    ),
    "ModularHeatPump": lambda config: ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=config.power_th * 1e-3,
        size_unit=Units.KILOWATT,
        kpi_tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
    ),
    # Note: the legacy battery capex multiplies the kWh capacity by 1e-3 — a latent unit bug
    # surfaced by the parity harness (cost_module_issues.md #20a). The adapter declares the
    # physically correct size.
    "Battery": lambda config: ComponentCostFacts(
        asset_class=ComponentType.BATTERY,
        size=config.custom_battery_capacity_generic_in_kilowatt_hour,
        size_unit=Units.KWH,
        kpi_tag=KpiTagEnumClass.BATTERY,
    ),
    "PVSystem": lambda config: ComponentCostFacts(
        asset_class=ComponentType.PV,
        size=config.power_in_watt * 1e-3,
        size_unit=Units.KILOWATT,
        kpi_tag=KpiTagEnumClass.ROOFTOP_PV,
    ),
    "GenericBoiler": _boiler_facts,
    "GenericDistrictHeating": lambda config: ComponentCostFacts(
        asset_class=ComponentType.DISTRICT_HEATING,
        size=config.connected_load_w * 1e-3,
        size_unit=Units.KILOWATT,
        kpi_tag=KpiTagEnumClass.DISTRICT_HEATING,
    ),
    "GenericElectricHeating": lambda config: ComponentCostFacts(
        asset_class=ComponentType.ELECTRIC_HEATER,
        size=config.maximum_electric_power_w * 1e-3,
        size_unit=Units.KILOWATT,
        kpi_tag=KpiTagEnumClass.ELECTRIC_HEATING,
    ),
    "HeatDistribution": _hds_facts,
    "HeatDistributionSystem": _hds_facts,
    "SimpleHotWaterStorage": lambda config: ComponentCostFacts(
        asset_class=ComponentType.THERMAL_ENERGY_STORAGE,
        size=config.volume_heating_water_storage_in_liter,
        size_unit=Units.LITER,
        kpi_tag=KpiTagEnumClass.STORAGE_HOT_WATER_SPACE_HEATING,
    ),
    "SimpleDHWStorage": lambda config: ComponentCostFacts(
        asset_class=ComponentType.THERMAL_ENERGY_STORAGE,
        size=config.volume_heating_water_storage_in_liter,
        size_unit=Units.LITER,
        kpi_tag=KpiTagEnumClass.STORAGE_DOMESTIC_HOT_WATER,
    ),
    "SolarThermalSystem": lambda config: ComponentCostFacts(
        asset_class=ComponentType.SOLAR_THERMAL_SYSTEM,
        size=config.area_m2,
        size_unit=Units.SQUARE_METER,
        kpi_tag=KpiTagEnumClass.SOLAR_THERMAL,
    ),
    "ElectricityMeter": lambda config: ComponentCostFacts(
        asset_class=ComponentType.ELECTRICITY_METER,
        size=1.0,
        size_unit=Units.ANY,
        kpi_tag=KpiTagEnumClass.ELECTRICITY_METER,
    ),
    "GasMeter": lambda config: ComponentCostFacts(
        asset_class=ComponentType.GAS_METER,
        size=1.0,
        size_unit=Units.ANY,
        kpi_tag=KpiTagEnumClass.GAS_METER,
    ),
    "L2GenericEnergyManagementSystem": lambda config: ComponentCostFacts(
        asset_class=ComponentType.ENERGY_MANAGEMENT_SYSTEM,
        size=1.0,
        size_unit=Units.ANY,
        kpi_tag=KpiTagEnumClass.ENERGY_MANAGEMENT_SYSTEM,
    ),
}


def _gas_meter_carrier(config: Any) -> EnergyCarrier:
    if getattr(config, "gas_loadtype", None) == lt.LoadTypes.GREEN_HYDROGEN:
        return EnergyCarrier.HYDROGEN
    return EnergyCarrier.NATURAL_GAS


def _fuel_meter_carrier(config: Any) -> EnergyCarrier:
    fuel = getattr(config, "fuel_loadtype", None)
    mapping = {
        lt.LoadTypes.OIL: EnergyCarrier.HEATING_OIL,
        lt.LoadTypes.PELLETS: EnergyCarrier.PELLETS,
        lt.LoadTypes.WOOD_CHIPS: EnergyCarrier.WOOD_CHIPS,
        lt.LoadTypes.DISTRICTHEATING: EnergyCarrier.DISTRICT_HEATING,
    }
    if fuel in mapping:
        return mapping[fuel]
    return EnergyCarrier.HEATING_OIL


def _fuel_quantity(kwh: float, config: Any) -> float:
    """Converts kWh of fuel to the carrier's native billing quantity (liters/tons) when possible."""
    fuel = getattr(config, "fuel_loadtype", None)
    heating_value = getattr(config, "heating_value_of_fuel_in_kwh_per_liter", None)
    if fuel == lt.LoadTypes.OIL and heating_value:
        return kwh / float(heating_value)  # liters
    if fuel in (lt.LoadTypes.PELLETS, lt.LoadTypes.WOOD_CHIPS):
        # Native billing quantity is tons; use the PhysicsConfig heating values (issues #20).
        from hisim.components.configuration import PhysicsConfig

        physics = PhysicsConfig.get_properties_for_energy_carrier(fuel)
        kwh_per_kg = physics.lower_heating_value_in_joule_per_kg / 3.6e6
        return kwh / kwh_per_kg / 1000.0  # tons
    return kwh


def get_meter_spec(component: Any) -> Optional[MeterSpec]:
    """Meter descriptor for known meter classes; None for non-meters."""
    class_name = type(component).__name__
    if class_name == "ElectricityMeter":
        return MeterSpec(
            carrier=EnergyCarrier.ELECTRICITY,
            bought_field="ElectricityFromGrid",
            sold_field="ElectricityToGrid",
            power_field="ElectricityFromGridInWatt",
        )
    if class_name == "GasMeter":
        return MeterSpec(carrier=_gas_meter_carrier(component.config), bought_field="GasFromGrid")
    if class_name == "FuelMeter":
        return MeterSpec(
            carrier=_fuel_meter_carrier(component.config),
            bought_field="HeatConsumption",
            quantity_conversion=_fuel_quantity,
        )
    if class_name == "HeatingMeter":
        # District-heating style heat delivery (cost_module_issues.md #18).
        return MeterSpec(carrier=EnergyCarrier.DISTRICT_HEATING, bought_field="HeatConsumption")
    return None


def get_cost_facts(component: Any) -> Optional[ComponentCostFacts]:
    """Facts for one component: the adopted `get_cost_facts()` API first, adapter table second."""
    getter = getattr(component, "get_cost_facts", None)
    if getter is not None:
        facts: Optional[ComponentCostFacts]
        try:
            facts = getter()
        except NotImplementedError:
            facts = None
        if facts is not None:
            return facts
        relevance = getattr(type(component), "cost_relevance", CostRelevance.UNDECLARED)
        if relevance == CostRelevance.FREE_OF_COST:
            return None
    extractor = _FACTS_EXTRACTORS.get(type(component).__name__)
    if extractor is None:
        return None
    try:
        extracted: Optional[ComponentCostFacts] = extractor(component.config)
        return extracted
    except (AttributeError, TypeError, ValueError) as err:
        log.warning(f"Cost adapter could not extract facts from {type(component).__name__}: {err}")
        return None


def effective_cost_relevance(component: Any) -> CostRelevance:
    """Declared relevance, or the adapter's best guess for legacy components (§9.2)."""
    declared = getattr(type(component), "cost_relevance", CostRelevance.UNDECLARED)
    if declared != CostRelevance.UNDECLARED:
        return declared
    if get_meter_spec(component) is not None:
        return CostRelevance.METER
    if type(component).__name__ in _FACTS_EXTRACTORS:
        return CostRelevance.PRICED
    return CostRelevance.UNDECLARED
