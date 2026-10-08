"""Compatibility adapter: reads cost facts and meter flows from components by class name (cost_spec.md §10.0 rule 4).

The engine never calls the legacy `get_cost_capex`/`get_cost_opex` methods, because they write back into the
component's config. Facts come from a component's own `get_cost_facts()` where it has one, and otherwise from the
class-name tables here, so `hisim.economics` imports no component module (§10.0 rule 1). `bridge.py` is the only
caller; an entry becomes dead once its component implements the hook (§10.1 Phase 6). Energy stays in kWh for every
carrier; fuels quoted per ton or liter are converted on the price side (`database.get_energy_price`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, Optional, Tuple

from hisim import log
from hisim import loadtypes as lt
from hisim.config import concrete
from hisim.economics.carriers import EnergyCarrier, EnergyFlowRole, UsefulHeatKind
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.facts import ComponentCostFacts, CostRelevance, EnergyFlowFacts
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass


@dataclass
class MeterSpec:
    """How to read a meter's carrier flows from the postprocessing results (§3.4, §8.4).

    Names the carrier the meter measures and its output columns for bought energy, sold energy and power, so
    `bridge.py` extracts billing determinants for every meter class the same way. Energy columns are per-timestep Wh
    (summed and scaled to kWh by `bridge._sum_output_column`); the power column is in W and gives the 15-minute billing
    peaks.
    """

    carrier: EnergyCarrier
    bought_field: str  # output field name holding bought energy per timestep (Wh)
    sold_field: Optional[str] = None
    power_field: Optional[str] = None  # instantaneous power series (W) for peak computation


def _quantity_value(value: Any) -> float:
    """Return the plain float of a `hisim.units` Quantity, or the value itself if it is already a number.

    Config fields are typed `Quantity` in some components and bare floats in others. No unit conversion is done; each
    caller applies its own factor (e.g. `* 1e-3` for W to kW).
    """
    return float(getattr(value, "value", value))


def _boiler_facts(config: Any) -> Optional[ComponentCostFacts]:
    """Return the cost facts of a `GenericBoiler`, whose asset class depends on the fuel it burns.

    Gas, oil, hydrogen, pellet and wood-chip boilers are separately priced asset classes, so the configured fuel
    carrier decides the `ComponentType` and the KPI tag. The size is the maximal thermal power in kW.

    Returns:
        The facts, or None for a carrier with no boiler asset class; `extract_cost_facts` turns that None into an
            unresolved subject.
    """
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
        size=concrete(config.maximal_thermal_power_in_watt) * 1e-3,
        size_unit=Units.KILOWATT,
        kpi_tag=kpi_tag,
    )


def _hds_facts(config: Any) -> Optional[ComponentCostFacts]:
    """Return the cost facts of a `HeatDistribution`, whose asset class depends on the emitter type.

    Floor heating and radiators are separately priced, sized in m² of conditioned floor area. The emitter type is
    matched by the name of its `HeatDistributionSystemType` member, so an enum member and a serialized string both
    work.

    Returns:
        The facts, or None for an emitter type with no cost database entry (low-temperature radiators); that None
            becomes an unresolved subject.
    """
    heating_system = getattr(config, "heating_system", None)
    heating_name = getattr(heating_system, "name", heating_system)
    if heating_name == "FLOORHEATING":
        asset_class = ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
    elif heating_name == "RADIATOR":
        asset_class = ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR
    else:
        return None  # low-temperature radiators have no cost database entry yet
    return ComponentCostFacts(
        asset_class=asset_class,
        size=concrete(config.absolute_conditioned_floor_area_in_m2),
        size_unit=Units.SQUARE_METER,
        kpi_tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
    )


class FactsExtractors:
    """Component class name -> function turning that component's config into `ComponentCostFacts` (§10.0).

    Keyed by class name so `hisim.economics` imports no component module (§10.0 rule 1).
    `tests/test_economics_adapter_contract.py` resolves every key against the real classes and runs every extractor on
    the real default configs, so a renamed class or config field fails a test. Each entry states the asset class, the
    config field the size comes from, the unit conversion and the KPI tag; entries are removed as components implement
    `get_cost_facts()` (§10.1 Phase 6).
    """

    BY_CLASS_NAME: Dict[str, Callable[[Any], Optional[ComponentCostFacts]]] = {
        # HeatPumpHplib has no entry: it is not a class in hisim.components, and the
        # contract test refuses such a key. The hplib heat pump is MoreAdvancedHeatPumpHPLib below.
        "MoreAdvancedHeatPumpHPLib": lambda config: ComponentCostFacts(
            asset_class=ComponentType.HEAT_PUMP,
            size=_quantity_value(config.set_thermal_output_power_in_watt) * 1e-3,
            size_unit=Units.KILOWATT,
            kpi_tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING_AND_DOMESTIC_HOT_WATER,
        ),
        # The legacy battery capex multiplies the kWh capacity by 1e-3, a unit bug; the adapter
        # declares the physically correct size.
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
        "DistrictHeating": lambda config: ComponentCostFacts(
            asset_class=ComponentType.DISTRICT_HEATING,
            size=config.connected_load_in_w * 1e-3,
            size_unit=Units.KILOWATT,
            kpi_tag=KpiTagEnumClass.DISTRICT_HEATING,
        ),
        "ElectricHeating": lambda config: ComponentCostFacts(
            asset_class=ComponentType.ELECTRIC_HEATER,
            size=config.maximum_electric_power_w * 1e-3,
            size_unit=Units.KILOWATT,
            kpi_tag=KpiTagEnumClass.ELECTRIC_HEATING,
        ),
        "HeatDistribution": _hds_facts,
        # The two vessels are two asset classes, priced alike, because the existing-asset register
        # matches by class: a RenoVisor heating_system measure replaces the buffer and keeps the
        # hot-water cylinder, and with one class the register could not say which was kept. The
        # components' own legacy capex path keeps THERMAL_ENERGY_STORAGE.
        "SimpleHotWaterStorage": lambda config: ComponentCostFacts(
            asset_class=ComponentType.SPACE_HEATING_STORAGE,
            size=concrete(config.volume_heating_water_storage_in_liter),
            size_unit=Units.LITER,
            kpi_tag=KpiTagEnumClass.STORAGE_HOT_WATER_SPACE_HEATING,
        ),
        "SimpleDHWStorage": lambda config: ComponentCostFacts(
            asset_class=ComponentType.DOMESTIC_HOT_WATER_STORAGE,
            size=concrete(config.volume_heating_water_storage_in_liter),
            size_unit=Units.LITER,
            kpi_tag=KpiTagEnumClass.STORAGE_DOMESTIC_HOT_WATER,
        ),
        "SolarThermalSystem": lambda config: ComponentCostFacts(
            asset_class=ComponentType.SOLAR_THERMAL_SYSTEM,
            size=concrete(config.area_m2),
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
        # The two devices of the electrolyzer setup. Both are industrial equipment priced from the
        # proposed rows migrated into devices_DE.json, not household appliances; see the `notes` of
        # those entries for what "proposed" means for a figure that rests on them.
        "Electrolyzer": lambda config: ComponentCostFacts(
            asset_class=ComponentType.ELECTROLYZER,
            size=config.nom_load,
            size_unit=Units.KILOWATT,
            kpi_tag=KpiTagEnumClass.ELECTROLYZER,
        ),
        "Transformer": lambda config: ComponentCostFacts(
            asset_class=ComponentType.TRANSFORMER_AND_RECTIFIER,
            size=config.rated_power_in_kilowatt,
            size_unit=Units.KILOWATT,
            kpi_tag=KpiTagEnumClass.TRANSFORMER,
        ),
    }


def _gas_meter_carrier(config: Any) -> EnergyCarrier:
    """Return the pricing carrier a `GasMeter` bills against: natural gas, or hydrogen if it meters hydrogen.

    `EnergyCarrier` is the pricing vocabulary, separate from the simulation's `LoadTypes`; a gas meter without an
    explicit load type is a natural-gas meter.
    """
    if getattr(config, "gas_loadtype", None) == lt.LoadTypes.GREEN_HYDROGEN:
        return EnergyCarrier.HYDROGEN
    return EnergyCarrier.NATURAL_GAS


def _fuel_meter_carrier(config: Any, component_name: str) -> EnergyCarrier:
    """Return the pricing carrier a `FuelMeter` bills against, from its configured fuel load type.

    Maps the solid and liquid fuels and district heating, which HiSim also routes through the fuel meter. There is no
    fallback carrier: a meter whose fuel cannot be mapped is a configuration error. It raises rather than returning a
    reason because `get_meter_spec` reaches it too.

    Args:
        config: The meter's config; only `fuel_loadtype` is read.
        component_name: The meter's instance name, used in the error message.

    Returns:
        The pricing carrier for one of the mapped load types.

    Raises:
        CostDataError: If `fuel_loadtype` is missing or has no carrier mapping; `bridge.py` reports it as an unresolved
            subject (a component the engine cannot price, which aborts the evaluation).
    """
    fuel = getattr(config, "fuel_loadtype", None)
    mapping = {
        lt.LoadTypes.OIL: EnergyCarrier.HEATING_OIL,
        lt.LoadTypes.PELLETS: EnergyCarrier.PELLETS,
        lt.LoadTypes.WOOD_CHIPS: EnergyCarrier.WOOD_CHIPS,
        lt.LoadTypes.DISTRICTHEATING: EnergyCarrier.DISTRICT_HEATING,
    }
    if fuel in mapping:
        return mapping[fuel]
    known = ", ".join(sorted(load_type.value for load_type in mapping))
    raise CostDataError(
        f"Fuel meter {component_name}: fuel_loadtype "
        f"{getattr(fuel, 'value', fuel)!r} has no energy carrier mapping (known: {known}), so the "
        "metered fuel cannot be billed."
    )


@dataclass(frozen=True)
class MeterOutputContract:
    """Which of a meter class's own constants name the columns the billing engine reads.

    Stores the constant names (e.g. `ElectricityFromGrid`), not the column strings; the strings are read off the class
    at resolution time, so the class stays the single source of the column it writes and a renamed output fails loudly.
    `carrier_of` takes the whole component because the gas and fuel meters derive their carrier from configuration and
    name the instance in their error message.
    """

    carrier_of: Callable[[Any], EnergyCarrier]
    bought_constant: str
    sold_constant: Optional[str] = None
    power_constant: Optional[str] = None


class MeterOutputContracts:
    """Meter class name -> `MeterOutputContract`: the four meter classes the engine can bill from (§3.4, §8.4).

    Keyed by class name like `FactsExtractors`, and pinned by the same contract test, which resolves every key and
    column constant against the real classes.
    """

    BY_CLASS_NAME: Dict[str, MeterOutputContract] = {
        "ElectricityMeter": MeterOutputContract(
            carrier_of=lambda _component: EnergyCarrier.ELECTRICITY,
            bought_constant="ElectricityFromGrid",
            sold_constant="ElectricityToGrid",
            # The only carrier with a capacity-charge tariff, so the only one needing peaks (§8.4).
            power_constant="ElectricityFromGridInWatt",
        ),
        "GasMeter": MeterOutputContract(
            carrier_of=lambda component: _gas_meter_carrier(component.config),
            bought_constant="GasFromGrid",
        ),
        "FuelMeter": MeterOutputContract(
            carrier_of=lambda component: _fuel_meter_carrier(
                component.config, getattr(component, "component_name", type(component).__name__)
            ),
            bought_constant="HeatConsumption",
        ),
        # District-heating style heat delivery.
        "HeatingMeter": MeterOutputContract(
            carrier_of=lambda _component: EnergyCarrier.DISTRICT_HEATING,
            bought_constant="HeatConsumption",
        ),
    }


def _declared_output_name(component: Any, constant_name: str, table_name: str) -> str:
    """Return the output column name a component class publishes under the given class constant.

    Every adapter table that names columns (meter contracts, energy-balance specs, useful-heat sources) resolves
    through this function, so a table can only ask for a column the class declares.

    Args:
        component: The component instance; only its class is read.
        constant_name: Name of the class constant holding the output field name.
        table_name: The adapter table that named the constant, for the error message.

    Returns:
        The output field name as the class states it.

    Raises:
        CostDataError: If the class has no such constant (a renamed or deleted output); `bridge.py` reports it as an
            unresolved subject.
    """
    component_class = type(component)
    field_name = getattr(component_class, constant_name, None)
    if not isinstance(field_name, str):
        raise CostDataError(
            f"Component class {component_class.__name__} declares no output-name constant "
            f"{constant_name!r}, so the cost engine cannot tell which results column that row of "
            f"adapter.{table_name} refers to. The constant was renamed or removed; update "
            f"adapter.{table_name} to match."
        )
    return field_name


def get_meter_spec(component: Any) -> Optional[MeterSpec]:
    """Return the meter descriptor of a known meter class, or None for any other component.

    Energy is billed only where a meter recorded a flow across the system boundary, so a component's internal
    consumption can never be billed twice (§3.1). `bridge.py` calls this for every component and reads the named
    columns into `BillingDeterminants`.

    Raises:
        CostDataError: For a fuel meter whose `fuel_loadtype` maps to no pricing carrier, or a meter class missing one
            of the named output constants; `bridge.py` reports both as unresolved subjects.
    """
    contract = MeterOutputContracts.BY_CLASS_NAME.get(type(component).__name__)
    if contract is None:
        return None
    table = "MeterOutputContracts"
    return MeterSpec(
        carrier=contract.carrier_of(component),
        bought_field=_declared_output_name(component, contract.bought_constant, table),
        sold_field=(
            _declared_output_name(component, contract.sold_constant, table)
            if contract.sold_constant
            else None
        ),
        power_field=(
            _declared_output_name(component, contract.power_constant, table)
            if contract.power_constant
            else None
        ),
    )


@dataclass
class FactsExtraction:
    """The outcome of asking one component for its cost facts, with a reason when there are none.

    At most one field is set. `facts` means resolved. `unresolved_reason` means the component should have produced
    facts and did not; `bridge.py` then aborts the evaluation. `not_installed_reason` means the component is configured
    at zero size and is left out of the cost model, as if the setup had not built it. All three None is what a
    `FREE_OF_COST` class or an unknown, undeclared class returns. The adapter returns this record instead of raising
    because it is also used for inspection and in tests; the bridge decides what fails.
    """

    facts: Optional[ComponentCostFacts] = None
    #: Why a component that should have had facts produced none; None when there is nothing to
    #: report (an unknown, undeclared class, or facts extracted successfully).
    unresolved_reason: Optional[str] = None
    #: Why a component that *could* be described contributes nothing anyway: it is configured at
    #: zero size. Reported and logged, never a failure — see the class docstring.
    not_installed_reason: Optional[str] = None

    def __post_init__(self) -> None:
        """Refuse a record that states more than one answer.

        Raises:
            ValueError: If more than one of `facts`, `unresolved_reason` and `not_installed_reason` is set.
        """
        stated = [
            name
            for name, value in (
                ("facts", self.facts),
                ("unresolved_reason", self.unresolved_reason),
                ("not_installed_reason", self.not_installed_reason),
            )
            if value is not None
        ]
        if len(stated) > 1:
            raise ValueError(
                f"FactsExtraction states {' and '.join(stated)} at once; at most one of facts, "
                "unresolved_reason and not_installed_reason may be set, because the caller reads "
                "them by branch order and would silently drop the rest."
            )


@dataclass(frozen=True)
class DeviceEnergySpec:
    """How to read one energy-balance flow of a component out of the results frame.

    The physical counterpart of `MeterOutputContract`: it covers flows nobody is charged for, such as PV generation or
    battery charging, so `bridge.py` can build the household energy balance. `output_constant` is the name of the class
    constant holding the column name (e.g. `MoreAdvancedHeatPumpHPLib.ElectricalInputPowerTotal`), resolved through
    `_declared_output_name`. The summing side reads the output's declared unit (W, Wh or kWh) and converts to kWh.
    `positive_part` serves a signed channel such as a battery's AC power: True takes the positive part, False the
    magnitude of the negative part, None sums the whole column.
    """

    role: EnergyFlowRole
    output_constant: str
    positive_part: Optional[bool] = None


@dataclass(frozen=True)
class ResolvedDeviceEnergyFlow:
    """One energy-balance flow with its column name resolved off the component class; what `bridge.py` reads."""

    role: EnergyFlowRole
    field_name: str
    positive_part: Optional[bool] = None


class DeviceEnergySpecs:
    """Component class name -> the flows it contributes to the household energy balance.

    Every component class that declares an electricity output in a convertible unit has a row: a real one when its flow
    is a terminal of the balance, an explicit empty one when it is not. A class with no row has not been reviewed, and
    `bridge.py` refuses a run that contains it. Empty rows are control channels (instructions, not flows), duplicate or
    aggregate channels counted elsewhere, and real flows with no terminal in `carriers.EnergyFlowRole`, which the
    balance carries in its residual node. `tests/test_economics_extraction.py` binds every key and constant to the real
    classes.
    """

    BY_CLASS_NAME: Dict[str, Tuple[DeviceEnergySpec, ...]] = {
        # ---------------------------------------------------------------- drawn terminals
        "PVSystem": (DeviceEnergySpec(EnergyFlowRole.PV_GENERATION, "ElectricityEnergyOutput"),),
        "Battery": (
            DeviceEnergySpec(EnergyFlowRole.BATTERY_CHARGE, "AcBatteryPowerUsed", positive_part=True),
            DeviceEnergySpec(EnergyFlowRole.BATTERY_DISCHARGE, "AcBatteryPowerUsed", positive_part=False),
        ),
        "MoreAdvancedHeatPumpHPLib": (
            DeviceEnergySpec(EnergyFlowRole.HEAT_PUMP_ELECTRICITY, "ElectricalInputPowerTotal"),
        ),
        # The older single-channel heat pump: one electricity output, flagged
        # ELECTRICITY_CONSUMPTION_UNCONTROLLED, so it is the same terminal as its successor's.
        "GenericHeatPump": (
            DeviceEnergySpec(EnergyFlowRole.HEAT_PUMP_ELECTRICITY, "ElectricityOutput"),
        ),
        "UtspLpgConnector": (
            DeviceEnergySpec(EnergyFlowRole.HOUSEHOLD_ELECTRICITY, "ElectricalEnergyConsumption"),
        ),
        "ElectricityMeter": (
            DeviceEnergySpec(EnergyFlowRole.GRID_IMPORT, "ElectricityFromGrid"),
            DeviceEnergySpec(EnergyFlowRole.GRID_EXPORT, "ElectricityToGrid"),
        ),
        # ------------------------------------------------- control and energy management
        # These publish setpoints, targets, surpluses and curtailment — an instruction to a
        # device, or the arithmetic of one. The energy they refer to is metered at the device
        # that follows the instruction, and counting both would count it twice.
        "L2GenericEnergyManagementSystem": (),
        "FuelCellController": (),
        "L1Controller": (),
        "PTXController": (),
        "XTPController": (),
        # ------------------------------------------------------ examples and templates
        # Shipped as documentation of the component API. They appear in no priced setup, and a
        # row here is what keeps them from failing a run that happens to include one.
        "ExampleComponent": (),
        "ComponentName": (),
        # -------------------------------- real device flows with no terminal in the vocabulary
        # `carriers.EnergyFlowRole` has no terminal for these. Their kilowatt hours stay in the
        # balance's residual node; giving one a terminal means adding a role to the chart.
        #
        # An EV battery is not a pass-through of the house bus (the car drives away with the
        # energy), so it cannot be drawn under the battery's charge/discharge pair.
        "CarBattery": (),
        "Car": (),
        # Electric heat and cooling: resistive space/DHW heat and air conditioning are neither the
        # household base load nor a heat pump's electricity.
        "ElectricHeating": (),
        "AirConditioner": (),
        "SimpleAirConditioner": (),
        # Auxiliary pump electricity of a solar thermal loop.
        "SolarThermalSystem": (),
        # The hydrogen chain (electrolyzers, fuel cells, reversible cells, CHP and their storage):
        # electricity that leaves or enters the bus as hydrogen. The balance has no hydrogen
        # terminal, and half of these channels are the same energy seen twice (a load in W beside
        # a cumulative total in kWh).
        "AdvancedElectrolyzer": (),
        "Electrolyzer": (),
        "HydrogenStorage": (),
        "FuelCell": (),
        "CHP": (),
        "SimpleCHP": (),
    }


def resolve_device_energy_flows(component: Any) -> Optional[Tuple[ResolvedDeviceEnergyFlow, ...]]:
    """Return the energy-balance flows this component class publishes, with their columns resolved.

    `bridge.py` calls this for every component regardless of cost relevance, since the energy balance is a physical
    record.

    Args:
        component: The wrapped component; its class name selects the row and its class carries the output constants.

    Returns:
        The resolved flows (possibly empty, for an explicit empty row), or None for a class the table does not list.

    Raises:
        CostDataError: If a row names an output constant the class does not declare.
    """
    specs = DeviceEnergySpecs.BY_CLASS_NAME.get(type(component).__name__)
    if specs is None:
        return None
    return tuple(
        ResolvedDeviceEnergyFlow(
            role=spec.role,
            field_name=_declared_output_name(component, spec.output_constant, "DeviceEnergySpecs"),
            positive_part=spec.positive_part,
        )
        for spec in specs
    )


@dataclass(frozen=True)
class UsefulHeatSource:
    """Where one component class states useful heat, and the sign convention of that column.

    `output_constant` is the name of the class constant holding the column name, resolved through
    `_declared_output_name`. `sign` is `INTO_THE_HOUSE` for a column that counts delivered heat as positive and
    `LEAVING_THE_SOURCE` for one that counts it as negative (a hot-water tank). The extraction sums `sign * column` and
    refuses a timestep of the other sign (`bridge._useful_heat_by_kind`).
    """

    #: The column counts heat delivered into the house as positive.
    INTO_THE_HOUSE: ClassVar[int] = 1
    #: The column counts heat leaving the component as negative.
    LEAVING_THE_SOURCE: ClassVar[int] = -1

    kind: UsefulHeatKind
    output_constant: str
    sign: int

    def __post_init__(self) -> None:
        """Refuse a sign convention that is neither of the two the table knows.

        Raises:
            ValueError: If `sign` is not `INTO_THE_HOUSE` or `LEAVING_THE_SOURCE`.
        """
        if self.sign not in (self.INTO_THE_HOUSE, self.LEAVING_THE_SOURCE):
            raise ValueError(
                f"UsefulHeatSource for {self.output_constant!r} has sign {self.sign!r}; a sign "
                f"convention is {self.INTO_THE_HOUSE} (heat into the house) or "
                f"{self.LEAVING_THE_SOURCE} (heat leaving the component)."
            )


@dataclass(frozen=True)
class ResolvedUsefulHeatSource:
    """One `UsefulHeatSource` with its column name resolved off the component class."""

    kind: UsefulHeatKind
    field_name: str
    sign: int


class UsefulHeatSources:
    """Component class name -> where it states the useful heat the levelized cost of heat divides by.

    Useful heat is the heat the house uses: the rooms' heating demand plus the heat in the hot water drawn, not what a
    generator produces, so a reference and a plan divide by the same need. Keyed by class name and naming output
    constants like `DeviceEnergySpecs`; `tests/test_economics_bridge.py` binds every key and constant. Hot-water
    components other than `SimpleDHWStorage` are not surveyed yet, so a run with a building and no listed
    hot-water source is flagged (`EvaluationInputs.heat_cost_omits_hot_water`).
    """

    BY_CLASS_NAME: ClassVar[Dict[str, UsefulHeatSource]] = {
        # The ideal heating demand of the rooms, cooling excluded: the building splits its thermal
        # demand by sign and writes the heating half clipped at zero, so it is >= 0 by construction.
        "Building": UsefulHeatSource(
            UsefulHeatKind.ROOM_HEATING, "TheoreticalHeatingEnergyDemand", UsefulHeatSource.INTO_THE_HOUSE
        ),
        # The heat in the hot water drawn off, from the fresh-water to the tap temperature. The
        # tank writes it as c * m * (T_fresh - T_tap), i.e. as heat leaving the tank (<= 0).
        "SimpleDHWStorage": UsefulHeatSource(
            UsefulHeatKind.HOT_WATER, "ThermalEnergyConsumptionDHW", UsefulHeatSource.LEAVING_THE_SOURCE
        ),
    }


def resolve_useful_heat_source(component: Any) -> Optional[ResolvedUsefulHeatSource]:
    """Return the useful-heat column this component class publishes, or None if the class has no row.

    Args:
        component: The wrapped component; its class name selects the row and its class carries the output constant.

    Returns:
        The resolved source, or None for a class `UsefulHeatSources` does not list.

    Raises:
        CostDataError: If the row names an output constant the class does not declare.
    """
    source = UsefulHeatSources.BY_CLASS_NAME.get(type(component).__name__)
    if source is None:
        return None
    return ResolvedUsefulHeatSource(
        kind=source.kind,
        field_name=_declared_output_name(component, source.output_constant, "UsefulHeatSources"),
        sign=source.sign,
    )


# The returns state the precedence order: hook, declared free of cost, declared priced but
# undescribable, unknown class, unpriceable configuration, extractor failure, no facts, facts.
def extract_cost_facts(component: Any) -> FactsExtraction:  # pylint: disable=too-many-return-statements
    """Return the cost facts of one component: its own `get_cost_facts()` first, the adapter table second.

    Precedence: a component's `get_cost_facts()` wins; if it returns None on a class declared `FREE_OF_COST` (§9.2),
    the component has no cost and the table is not consulted; any other None falls through to the table. Without a
    reason only an unknown, undeclared class comes back. An unresolved reason is returned when a registered extractor
    yields None, when a class declared `PRICED` has neither hook nor table entry, when an extractor fails on a missing
    config field, and when a `CostDataError` is raised. Facts describing a zero-size component come back as
    `not_installed_reason` (e.g. a PV system sized at zero share of the roof), so they skip the component instead of
    aborting the evaluation.

    Args:
        component: The simulated component; only its class name, `cost_relevance`, `config` and the optional hook are
            read.

    Returns:
        A `FactsExtraction` with facts, an unresolved reason, a not-installed reason, or nothing for an unknown
            undeclared class.
    """
    getter = getattr(component, "get_cost_facts", None)
    if getter is not None:
        facts: Optional[ComponentCostFacts]
        try:
            facts = getter()
        except NotImplementedError:
            facts = None
        if facts is not None:
            return _resolved_or_not_installed(component, facts)
        relevance = getattr(type(component), "cost_relevance", CostRelevance.UNDECLARED)
        if relevance == CostRelevance.FREE_OF_COST:
            return FactsExtraction()
    class_name = type(component).__name__
    extractor = FactsExtractors.BY_CLASS_NAME.get(class_name)
    if extractor is None:
        if getattr(type(component), "cost_relevance", CostRelevance.UNDECLARED) == CostRelevance.PRICED:
            return FactsExtraction(
                unresolved_reason=(
                    f"class {class_name} declares cost_relevance PRICED, but neither "
                    "get_cost_facts() nor the adapter table FactsExtractors.BY_CLASS_NAME "
                    "yields facts for it, so nothing can describe it to the cost model"
                )
            )
        return FactsExtraction()
    try:
        extracted: Optional[ComponentCostFacts] = extractor(component.config)
    except CostDataError as err:
        return FactsExtraction(unresolved_reason=str(err))
    except (AttributeError, TypeError, ValueError) as err:
        log.warning(f"Cost adapter could not extract facts from {class_name}: {err}")
        return FactsExtraction(
            unresolved_reason=(
                f"the registered cost extractor for class {class_name} failed with "
                f"{type(err).__name__}: {err} — most likely a config field it reads has been "
                "renamed, which would drop the component from the cost model"
            )
        )
    if extracted is None:
        return FactsExtraction(
            unresolved_reason=(
                f"the registered cost extractor for class {class_name} returned no facts "
                "(unmapped fuel or unpriced variant?), so the component would be dropped from "
                "the cost model while counting as priced"
            )
        )
    return _resolved_or_not_installed(component, extracted)


def _resolved_or_not_installed(component: Any, facts: ComponentCostFacts) -> FactsExtraction:
    """Return resolved facts, or a not-installed skip if the facts say the component has zero size.

    Shared by the hook and the table branch of `extract_cost_facts`, so both treat a zero-size component the same way:
    it is left out of pricing. `bridge.py` separately checks that such a component moved no energy
    (`_non_zero_energy_flows`).

    Args:
        component: Only its class name is read, for the reason string.
        facts: The facts the hook or the extractor produced.

    Returns:
        A resolved `FactsExtraction`, or one carrying only `not_installed_reason`.
    """
    if not facts.is_not_installed():
        return FactsExtraction(facts=facts)
    return FactsExtraction(
        not_installed_reason=(
            f"{type(component).__name__} ({facts.asset_class.value}) is configured at zero size — "
            "not installed, so it is excluded from pricing"
        )
    )


def get_cost_facts(component: Any) -> Optional[ComponentCostFacts]:
    """Return just the facts of `extract_cost_facts`, dropping any reason, for callers that only inspect.

    For parity tooling and interactive inspection. `bridge.py` uses `extract_cost_facts`, because dropping the reason
    would let a component vanish from the cost results silently.
    """
    return extract_cost_facts(component).facts


def get_energy_flow_facts(
    component: Any, all_outputs: Any, postprocessing_results: Any
) -> Optional[EnergyFlowFacts]:
    """Return the component's own §3.4 energy flow declaration, or None if it has none.

    `bridge.py` asks this first and falls back to `get_meter_spec` for components without the hook. `EnergyFlowFacts`
    carries carrier and kWh but no capacity peaks, so peaks keep coming from the `MeterSpec` where one exists.

    Args:
        component: The component to ask; one without the hook, or with the base implementation, yields None.
        all_outputs: The run's output declarations, passed through to the hook.
        postprocessing_results: The results frame, passed through to the hook.

    Returns:
        The declared flows, or None. A hook raising `NotImplementedError` counts as not implemented; any other
            exception propagates, so a failing meter is never billed as zero.
    """
    getter = getattr(component, "get_energy_flow_facts", None)
    if getter is None:
        return None
    try:
        flows: Optional[EnergyFlowFacts] = getter(all_outputs, postprocessing_results)
    except NotImplementedError:
        return None
    return flows


def effective_cost_relevance(component: Any) -> CostRelevance:
    """Return the component class's declared cost role (§9.2).

    Only the class's `cost_relevance` declaration is read; nothing is inferred from the adapter tables, so a class that
    declares nothing is `UNDECLARED`, which `bridge.py` treats as fatal. A meter that also costs money declares `METER`
    and is still asked for cost facts.

    Args:
        component: The component to classify; only its class's `cost_relevance` attribute is read, so this never
            raises.

    Returns:
        The declared `CostRelevance`, or `CostRelevance.UNDECLARED`.
    """
    declared: CostRelevance = getattr(type(component), "cost_relevance", CostRelevance.UNDECLARED)
    return declared
