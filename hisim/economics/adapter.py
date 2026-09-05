"""Compatibility adapter: legacy components -> ComponentCostFacts (cost_spec.md §10.0 rule 4).

The new engine never calls the legacy `get_cost_capex`/`get_cost_opex` methods (they mutate
configs as a side effect). Facts come from `get_cost_facts()` where a component has adopted
the new API, and otherwise from this adapter, which maps known component classes and their
configs to facts directly. The adapter shrinks as adoption grows (§10.1 Phase 6).

**Why this file exists at all.** §10.0 rule 4 is a hard constraint of the parallel-implementation
phase: calling `get_cost_capex` a second time from the new path would corrupt the very legacy
calculation the new engine must leave bit-identical, because that method writes back into the
component's config. So the new engine reads *configs*, never legacy cost methods — and this
module is the one place that knows how to read them. Nothing here imports a component module
either; the tables are keyed by class *name* (§10.0 rule 1), which keeps `hisim.economics` free
of any dependency on `hisim.components`.

**Its place in the pipeline.** It sits on the extraction side of the cost-spec-v2 seam-1 cut,
used only by `bridge.py` while it walks a finished simulation's wrapped components: for each one
it asks `effective_cost_relevance` whether the component is priced, free of cost or a meter,
`extract_cost_facts` for its `ComponentCostFacts`, `get_energy_flow_facts` for a declared carrier
flow, and `get_meter_spec` for how to read that flow out of the results frame when the component
has not adopted the hook. Everything it produces lands in `EvaluationInputs` and hence in
`economic_inputs.json`; nothing downstream of that file knows this module exists.

**What it does and does not fail on.** The adapter is also used exploratorily, so a component it
cannot describe comes back as a `FactsExtraction` carrying a *reason* rather than as an exception
— the bridge owns the decision to fail (decision D7, issue #2). The one exception is a
configuration that names something the engine has no mapping for, above all a fuel meter whose
`fuel_loadtype` is unset or unknown: that raises `CostDataError` instead of billing the fuel at
oil prices (issue #3), and the bridge catches it per component so it too lands on the D7 path.

**It is temporary and should shrink.** Every entry below is a component that has not yet
implemented `get_cost_facts()` in its own module, where the declaration belongs next to the
config it reads (§9.1). When a component adopts the hook, its entry here becomes dead and should
be deleted; when all of them have, the file goes (§10.1 Phase 6). A reviewer comparing an entry
against the component is doing exactly the right thing — the asset class, the config field and
the unit conversion are the whole reviewable surface.

**Known unit quirks, documented rather than papered over.** Sizes are converted with hand-written
factors here (`* 1e-3` for W→kW) instead of the typed `units.Quantity` helpers, mirroring what
the components do today, and `Battery` deliberately declares the physically correct capacity where
the legacy path has a latent unit bug (issue #20a). Energy quantities are *not* on that list any
more: a meter's kWh stay kWh all the way into `BillingDeterminants.energy_bought_in_kwh`, for
every carrier. Fuels quoted per ton or per liter in the literature are handled on the price side
instead — `database.get_energy_price` divides the quote by the carrier's lower heating value and
bills in EUR/kWh (decision D26, cost-spec-v2 §8) — so the field name and the number in it finally
agree (this is what closed issue #11 / §2.1 issue #21).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from hisim import log
from hisim import loadtypes as lt
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.facts import ComponentCostFacts, CostRelevance, EnergyFlowFacts
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass


@dataclass
class MeterSpec:
    """How to read a meter's carrier flows from the postprocessing results.

    A declarative description of one meter — which carrier it measures and which of its output
    columns hold the bought/sold energy and the instantaneous power — so `bridge.py` can extract
    billing determinants generically instead of special-casing each meter class. It is the
    §3.4/§8.4 "what the meter must measure" contract seen from the extraction side; the pricing
    side never sees a `MeterSpec`, only the resulting `BillingDeterminants`.

    Units: the named output columns are per-timestep energy in Wh (summed and scaled to kWh by
    `bridge._sum_output_column`) and instantaneous power in W, from which the 15-minute billing
    peaks are derived. There is no unit conversion beyond that and deliberately so — every carrier
    is measured, carried and billed in kWh, and a fuel quoted per ton or per liter is converted on
    the *price* side at resolution time (D26).
    """

    carrier: EnergyCarrier
    bought_field: str  # output field name holding bought energy per timestep (Wh)
    sold_field: Optional[str] = None
    power_field: Optional[str] = None  # instantaneous power series (W) for peak computation


def _quantity_value(value: Any) -> float:
    """Unwraps hisim.units Quantity objects.

    Config fields are typed `Quantity` in some components and bare floats in others; this accepts
    both so an extractor need not know which. It does *not* convert units — the caller still
    applies the factor the field's own unit requires, which is why every call site is followed by
    an explicit `* 1e-3`.
    """
    return float(getattr(value, "value", value))


def _heat_pump_facts(config: Any) -> ComponentCostFacts:
    """Facts for `HeatPumpHplib` from its configured thermal output power.

    Named rather than inlined into the table below because the config field arrives as a typed
    `Quantity` and needs unwrapping. Sized in kW thermal, which is what the `HEAT_PUMP` device
    entries are priced per (§3.5); the KPI tag marks it as the space-heating heat pump.
    """
    return ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=_quantity_value(config.set_thermal_output_power_in_watt) * 1e-3,
        size_unit=Units.KILOWATT,
        kpi_tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
    )


def _boiler_facts(config: Any) -> Optional[ComponentCostFacts]:
    """Facts for `GenericBoiler`, whose asset class depends on the fuel it burns.

    One component class covers five priced asset classes — gas, oil, hydrogen, pellet and wood
    chip boilers have different prices, service lives and subsidy treatment — so the fuel carrier
    in the config, not the class name, decides both the `ComponentType` and the KPI tag. Sized in
    kW of maximal thermal power.

    Returns:
        None for a carrier that has no boiler asset class, rather than guessing one. Since the
        class *is* in the extractor table, that None is not an "undeclared component" but an
        unresolved subject: `extract_cost_facts` turns it into a reason and `bridge.py` fails the
        evaluation through the D7 path rather than dropping the boiler silently (issue #2).
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
        size=config.maximal_thermal_power_in_watt * 1e-3,
        size_unit=Units.KILOWATT,
        kpi_tag=kpi_tag,
    )


def _hds_facts(config: Any) -> Optional[ComponentCostFacts]:
    """Facts for the heat distribution system, whose asset class depends on the emitter type.

    Floor heating and radiators are separately priced classes, sized in m² of conditioned floor
    area. The emitter type is matched against *both* its numeric enum value and its name, so the
    extractor works whether the config holds an enum member or a plain string — it is shared by
    two component classes (`HeatDistribution`, `HeatDistributionSystem`) and reads their configs
    as they are, without importing either.

    Returns:
        None for low-temperature radiators, which have no cost database entry yet; see
        `_boiler_facts` for what an unpriced-but-registered component means — it surfaces as an
        unresolved subject, not as a silent omission.
    """
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


class FactsExtractors:
    """Class-name keyed extraction table (avoids importing component modules; §10.0).

    The whole compatibility layer in one place: component class name -> a function that turns that
    component's config into `ComponentCostFacts`. Keying by name rather than by type is what keeps
    `hisim.economics` importable without pulling in `hisim.components` (§10.0 rule 1), at the
    price of no static checking — a renamed component class silently drops out of the cost model,
    which is what the §9.2 completeness check and the auto-discovered contract test exist to
    catch.

    Each entry is a ~4-line declaration of exactly what §9.1 says a reviewer should have to
    verify: the asset class, the config field the size comes from, the unit conversion, and the
    KPI tag. Entries disappear as components implement `get_cost_facts()` themselves; the table is
    expected to end up empty (§10.1 Phase 6).
    """

    BY_CLASS_NAME: Dict[str, Callable[[Any], Optional[ComponentCostFacts]]] = {
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
    """The pricing carrier a `GasMeter` bills against, natural gas unless it meters hydrogen.

    `EnergyCarrier` is the *pricing* vocabulary and is deliberately distinct from `LoadTypes`,
    the simulation's physical vocabulary; this is one of the few places the two are mapped onto
    each other. Natural gas is the default because a gas meter without an explicit load type is a
    natural-gas meter in every shipped setup.
    """
    if getattr(config, "gas_loadtype", None) == lt.LoadTypes.GREEN_HYDROGEN:
        return EnergyCarrier.HYDROGEN
    return EnergyCarrier.NATURAL_GAS


def _fuel_meter_carrier(config: Any, component_name: str) -> EnergyCarrier:
    """The pricing carrier a `FuelMeter` bills against, from its configured fuel load type.

    The `LoadTypes` -> `EnergyCarrier` mapping for the solid and liquid fuels, plus district
    heating, which HiSim also routes through the fuel meter. There is deliberately no fallback:
    an unmapped or missing `fuel_loadtype` used to be billed as heating oil, so a mis-configured
    meter published oil prices for whatever it actually metered (issue #3). A carrier the engine
    cannot derive is a configuration error, and configuration errors fail (§10.0 rule 3 covers
    *extraction* accidents, not a meter that does not say what it meters).

    Args:
        config: The meter's config; only `fuel_loadtype` is read.
        component_name: The meter's instance name, so the raised message names the component a
            user has to go and fix rather than just its class.

    Returns:
        The pricing carrier for one of the four mapped load types.

    Raises:
        CostDataError: If `fuel_loadtype` is missing or is a load type with no carrier mapping.
            `bridge.py` catches it per component and reports it as an unresolved subject, so it
            aborts the evaluation through the same D7 path as an unpriceable device.
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


def get_meter_spec(component: Any) -> Optional[MeterSpec]:
    """Meter descriptor for known meter classes; None for non-meters.

    The energy half of the adapter, and the reason the engine can claim no double counting by
    construction (§3.1): energy is billed only where a *meter* recorded a flow across the system
    boundary, so a component's internal consumption can never turn into a second bill. `bridge.py`
    calls this for every component; a non-None result makes it read the named output columns out
    of the results frame into `BillingDeterminants`.

    Only the electricity meter declares a `power_field`, because it is the only carrier with a
    capacity-charge tariff to compute peaks for (§8.4).

    Raises:
        CostDataError: For a fuel meter whose `fuel_loadtype` maps to no pricing carrier — the
            meter exists but cannot say what it meters (issue #3). Callers that classify
            components exploratorily (`effective_cost_relevance`) propagate it too; `bridge.py`
            turns it into an unresolved subject for the component.
    """
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
            carrier=_fuel_meter_carrier(
                component.config, getattr(component, "component_name", class_name)
            ),
            bought_field="HeatConsumption",
        )
    if class_name == "HeatingMeter":
        # District-heating style heat delivery (cost_module_issues.md #18).
        return MeterSpec(carrier=EnergyCarrier.DISTRICT_HEATING, bought_field="HeatConsumption")
    return None


@dataclass
class FactsExtraction:
    """The outcome of asking one component for its cost facts, with a reason when there are none.

    The seam issue #2 needed: the adapter is also used *exploratorily* — by
    `effective_cost_relevance`, by tests, by anyone inspecting a component — so it must not raise
    when a component it knows yields nothing. It returns this record instead, which lets the one
    caller that owns the policy (`bridge.py`) distinguish the two kinds of "no facts": a class the
    adapter has never heard of (`unresolved_reason` None — that is the §9.2 undeclared-components
    path) from a class it *does* know that produced nothing anyway (`unresolved_reason` set — an
    unresolved subject under decision D7).

    Exactly one of the two fields is meaningful at a time: facts present means resolved,
    `unresolved_reason` present means the component should have had facts and does not.
    """

    facts: Optional[ComponentCostFacts] = None
    #: Why a component the adapter recognizes produced no facts; None when there is nothing to
    #: report (unknown class, or facts extracted successfully).
    unresolved_reason: Optional[str] = None


# The seven returns are the precedence rule this function exists to state -- hook, declared
# free of cost, unknown class, unpriceable configuration, extractor accident, no facts, facts --
# and folding them into fewer branches would hide the order rather than simplify it.
def extract_cost_facts(component: Any) -> FactsExtraction:  # pylint: disable=too-many-return-statements
    """Facts for one component: the adopted `get_cost_facts()` API first, adapter table second.

    The full-information entry point `bridge.py` uses, and the place the migration order is
    enforced: a component that has adopted the §9.1 declaration wins, and the compatibility table
    is consulted only when it has not. That precedence is what lets adoption happen component by
    component without any coordinating change, and what makes an adapter entry become dead the
    moment the component implements the hook.

    Three subtleties. A `get_cost_facts()` that returns None on a class declared `FREE_OF_COST`
    (§9.2) means "genuinely no cost", so the table is deliberately *not* consulted afterwards; any
    other None falls through to the table. A registered class whose extractor returns None — an
    unmapped boiler fuel, an unpriced emitter type — is *not* silently dropped any more (issue
    #2): it comes back with an `unresolved_reason` naming the class, which the bridge turns into a
    D7 failure. And an extractor that trips over a config field that moved is still only warned
    about, per §10.0 rule 3, because that is a parallel-phase accident rather than a statement
    about the modelled system — with one exception: a `CostDataError` is the adapter's own way of
    saying "this configuration cannot be priced" (see `_fuel_meter_carrier`) and is reported as
    unresolved instead of swallowed.

    Args:
        component: The finished simulation's component object; only its class name, its
            `cost_relevance`, its `config` and the optional hook are read.

    Returns:
        A `FactsExtraction` — facts when the component could be described, a reason when a
        recognized component could not, and neither when the adapter simply does not know it.
    """
    getter = getattr(component, "get_cost_facts", None)
    if getter is not None:
        facts: Optional[ComponentCostFacts]
        try:
            facts = getter()
        except NotImplementedError:
            facts = None
        if facts is not None:
            return FactsExtraction(facts=facts)
        relevance = getattr(type(component), "cost_relevance", CostRelevance.UNDECLARED)
        if relevance == CostRelevance.FREE_OF_COST:
            return FactsExtraction()
    class_name = type(component).__name__
    extractor = FactsExtractors.BY_CLASS_NAME.get(class_name)
    if extractor is None:
        return FactsExtraction()
    try:
        extracted: Optional[ComponentCostFacts] = extractor(component.config)
    except CostDataError as err:
        return FactsExtraction(unresolved_reason=str(err))
    except (AttributeError, TypeError, ValueError) as err:
        log.warning(f"Cost adapter could not extract facts from {class_name}: {err}")
        return FactsExtraction()
    if extracted is None:
        return FactsExtraction(
            unresolved_reason=(
                f"the registered cost extractor for class {class_name} returned no facts "
                "(unmapped fuel or unpriced variant?), so the component would be dropped from "
                "the cost model while counting as priced"
            )
        )
    return FactsExtraction(facts=extracted)


def get_cost_facts(component: Any) -> Optional[ComponentCostFacts]:
    """Just the facts of `extract_cost_facts`, for callers that only want to look.

    The exploratory form: it answers "what would this component contribute" without the
    resolution policy attached, which is what parity tooling and interactive inspection want. The
    bridge deliberately does not use it — dropping the reason is exactly the silent omission issue
    #2 was about — so anything that must not lose a subject calls `extract_cost_facts` instead.
    """
    return extract_cost_facts(component).facts


def get_energy_flow_facts(
    component: Any, all_outputs: Any, postprocessing_results: Any
) -> Optional[EnergyFlowFacts]:
    """The component's adopted §3.4 flow declaration, or None (mirrors `get_cost_facts`, issue #18).

    The billing counterpart of the precedence in `extract_cost_facts`, and the reason
    `component.get_energy_flow_facts` is no longer dead code: `bridge.py` asks this first and only
    falls back to the class-name table of `get_meter_spec` when a component has not adopted the
    hook. There is deliberately no fallback *table* here — the adapter's compatibility knowledge
    about flows is the `MeterSpec`, which the bridge holds separately because reading a series out
    of the results frame is the bridge's job, not the adapter's.

    What the hook cannot express is documented at the bridge's call site: an `EnergyFlowFacts`
    carries carrier and integrated kWh, but no capacity peaks, so those keep coming from the
    `MeterSpec` when one exists.

    Args:
        component: The component to ask; one without the hook (or with the base implementation)
            yields None.
        all_outputs: The run's output declarations, passed through to the hook.
        postprocessing_results: The results frame, passed through to the hook.

    Returns:
        The declared flows, or None when the component did not declare any. A hook that raises
        `NotImplementedError` counts as "not adopted"; every other exception propagates, since a
        meter that fails while reporting its own flows must not be billed as if it measured zero.
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
    """Declared relevance, or the adapter's best guess for legacy components (§9.2).

    §9.2 exists because the naive default — "no `get_cost_facts()` means no costs" — has a failure
    mode locality never had: a forgotten implementation silently drops a component from every cost
    result. The class-level `cost_relevance` declaration makes that omission visible, and this
    function is the bridge during migration: an explicit declaration always wins, and only an
    `UNDECLARED` component is classified from what the adapter happens to know about it.

    The inferred order matters — METER before PRICED — because meters can be both (an electricity
    meter measures flows *and* is itself a priced device); `bridge.py` handles that by reading the
    meter flows and then still asking for cost facts. Everything the adapter recognizes in neither
    way stays `UNDECLARED` and is reported as such: not priced, but not silently forgotten either.

    Note that a component which meters a carrier through the `get_energy_flow_facts` hook but has
    no entry in the meter table must declare `cost_relevance = METER` itself — the inference here
    cannot call the hook, which needs the results frame this function does not have.

    Raises:
        CostDataError: From the `get_meter_spec` inference for a meter whose configuration names
            no priceable carrier (issue #3); classification of such a component is impossible
            rather than merely uncertain.
    """
    declared = getattr(type(component), "cost_relevance", CostRelevance.UNDECLARED)
    if declared != CostRelevance.UNDECLARED:
        return declared
    if get_meter_spec(component) is not None:
        return CostRelevance.METER
    if type(component).__name__ in FactsExtractors.BY_CLASS_NAME:
        return CostRelevance.PRICED
    return CostRelevance.UNDECLARED
