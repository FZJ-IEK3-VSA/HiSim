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
it asks `effective_cost_relevance` for the class's *declared* cost role, `extract_cost_facts` for
its `ComponentCostFacts`, `get_energy_flow_facts` for a declared carrier flow, and
`get_meter_spec` for how to read that flow out of the results frame when the component has not
adopted the hook. Everything it produces lands in `EvaluationInputs` and hence in
`economic_inputs.json`; nothing downstream of that file knows this module exists.

**What it does and does not fail on.** Nothing here guesses and nothing here shrugs. Relevance is
read off the class declaration only — there is no inference from these tables, so a component that
forgot to declare stays `UNDECLARED` and the bridge aborts the evaluation on it (decision D7,
§9.2). Extraction likewise never returns "no facts" without saying why: an extractor that returns
None, one that trips over a config field that has moved, and a class declared `PRICED` with
neither hook nor table entry all come back as a `FactsExtraction` carrying an `unresolved_reason`.
The adapter is also used exploratorily, so those are records rather than exceptions and the bridge
owns the decision to fail. The one thing that does raise is a configuration naming something the
engine has no mapping for, above all a fuel meter whose `fuel_loadtype` is unset or unknown: that
raises `CostDataError` instead of billing the fuel at oil prices (issue #3), and the bridge catches
it per component so it too lands on the D7 path.

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
    """Facts for `HeatDistribution`, whose asset class depends on the emitter type.

    Floor heating and radiators are separately priced classes, sized in m² of conditioned floor
    area. The emitter type is matched against the *name* of the `HeatDistributionSystemType`
    member, which is also what a serialized config spells it as, so the extractor works whether
    the config holds an enum member or a plain string and reads the config as it is without
    importing the component module.

    Returns:
        None for low-temperature radiators (and any emitter type added later), which have no cost
        database entry yet; see `_boiler_facts` for what an unpriced-but-registered component
        means — it surfaces as an unresolved subject, not as a silent omission.
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
        size=config.absolute_conditioned_floor_area_in_m2,
        size_unit=Units.SQUARE_METER,
        kpi_tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
    )


class FactsExtractors:
    """Class-name keyed extraction table (avoids importing component modules; §10.0).

    The whole compatibility layer in one place: component class name -> a function that turns that
    component's config into `ComponentCostFacts`. Keying by name rather than by type is what keeps
    `hisim.economics` importable without pulling in `hisim.components` (§10.0 rule 1), at the
    price of no static checking — a renamed component class or a renamed config field would drop
    out of the cost model unnoticed. `tests/test_economics_adapter_contract.py` is what makes that
    impossible: it resolves every key below against the classes actually defined in
    `hisim.components` and runs every extractor against the real default configs, so a rename
    fails a test instead of quietly shrinking a cost result.

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
    cannot derive is a configuration error, and configuration errors fail. This one raises rather
    than returning a reason because it is reached from `get_meter_spec` too, which has no
    `FactsExtraction` to put a reason in.

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


@dataclass(frozen=True)
class MeterOutputContract:
    """Which of a meter class's own constants name the columns the billing engine reads.

    A `MeterSpec` needs three column names, and a meter class already publishes them as class
    constants (`ElectricityMeter.ElectricityFromGrid` and friends) because its own `add_output`
    calls use them. This record therefore stores the *constant names*, not the column strings: the
    strings are read off the class at resolution time, so the class stays the single source of the
    column it writes. While they were duplicated here as literals, renaming a meter output moved
    the column and left the adapter reading a name nothing wrote any more — an empty series, a
    carrier billed at zero, and no complaint anywhere.

    `carrier_of` takes the whole component rather than its config because the two fuel-ish meters
    derive their pricing carrier from configured load types and want their instance name for the
    error message, while the other two are fixed.
    """

    carrier_of: Callable[[Any], EnergyCarrier]
    bought_constant: str
    sold_constant: Optional[str] = None
    power_constant: Optional[str] = None


class MeterOutputContracts:
    """Class-name keyed meter table, the energy twin of `FactsExtractors` (§3.4/§8.4).

    The four meter classes the engine can bill from, each with the carrier it meters and the
    constants naming its billable outputs. Keyed by class name for the same reason as the facts
    table — `hisim.economics` imports no component module (§10.0 rule 1) — and pinned by the same
    test file, which resolves every key against the real classes and compares every resolved
    column name against the class constant it claims to read.
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
        # District-heating style heat delivery (cost_module_issues.md #18).
        "HeatingMeter": MeterOutputContract(
            carrier_of=lambda _component: EnergyCarrier.DISTRICT_HEATING,
            bought_constant="HeatConsumption",
        ),
    }


def _meter_output_name(component: Any, constant_name: str) -> str:
    """The output column a meter class publishes under the given constant.

    Reads the constant off `type(component)` instead of repeating its value here, so the meter
    class owns the name of the column it writes and the adapter can only ever ask for a column
    that class actually declares.

    Args:
        component: The meter instance; only its class is read.
        constant_name: Name of the class constant holding the output field name.

    Returns:
        The output field name as the class states it.

    Raises:
        CostDataError: If the class has no such constant. That is a renamed or deleted meter
            output, and the alternative is billing a carrier from a column nothing writes;
            `bridge.py` catches it per component and reports it as an unresolved subject, so the
            run aborts through the D7 path instead of publishing a zero bill.
    """
    meter_class = type(component)
    field_name = getattr(meter_class, constant_name, None)
    if not isinstance(field_name, str):
        raise CostDataError(
            f"Meter class {meter_class.__name__} declares no output-name constant "
            f"{constant_name!r}, so the cost engine cannot tell which results column carries its "
            "metered energy. The constant was renamed or removed; update "
            "adapter.MeterOutputContracts to match."
        )
    return field_name


def get_meter_spec(component: Any) -> Optional[MeterSpec]:
    """Meter descriptor for known meter classes; None for non-meters.

    The energy half of the adapter, and the reason the engine can claim no double counting by
    construction (§3.1): energy is billed only where a *meter* recorded a flow across the system
    boundary, so a component's internal consumption can never turn into a second bill. `bridge.py`
    calls this for every component; a non-None result makes it read the named output columns out
    of the results frame into `BillingDeterminants`.

    The column names come from the meter class itself (`_meter_output_name`), which is what makes a
    renamed meter output a loud failure rather than a carrier quietly billed from an empty series.

    Raises:
        CostDataError: For a fuel meter whose `fuel_loadtype` maps to no pricing carrier — the
            meter exists but cannot say what it meters (issue #3) — and for a meter class that no
            longer declares one of the output constants the table names. `bridge.py` catches both
            per component and turns them into unresolved subjects.
    """
    contract = MeterOutputContracts.BY_CLASS_NAME.get(type(component).__name__)
    if contract is None:
        return None
    return MeterSpec(
        carrier=contract.carrier_of(component),
        bought_field=_meter_output_name(component, contract.bought_constant),
        sold_field=(
            _meter_output_name(component, contract.sold_constant) if contract.sold_constant else None
        ),
        power_field=(
            _meter_output_name(component, contract.power_constant) if contract.power_constant else None
        ),
    )


@dataclass
class FactsExtraction:
    """The outcome of asking one component for its cost facts, with a reason when there are none.

    The seam issue #2 needed: the adapter is also used *exploratorily* — by
    `effective_cost_relevance`, by tests, by anyone inspecting a component — so it must not raise
    when a component it knows yields nothing. It returns this record instead, which lets the one
    caller that owns the policy (`bridge.py`) distinguish the two kinds of "no facts": a class the
    adapter has never heard of *and* which declares no cost role (`unresolved_reason` None — that
    is the §9.2 undeclared-components path, and the bridge rejects it on the declaration before it
    ever gets here) from a class that should have produced facts and did not
    (`unresolved_reason` set — an unresolved subject under decision D7).

    Exactly one of the two fields is meaningful at a time: facts present means resolved,
    `unresolved_reason` present means the component should have had facts and does not.
    """

    facts: Optional[ComponentCostFacts] = None
    #: Why a component that should have had facts produced none; None when there is nothing to
    #: report (an unknown, undeclared class, or facts extracted successfully).
    unresolved_reason: Optional[str] = None


# The eight returns are the precedence rule this function exists to state -- hook, declared
# free of cost, declared priced but undescribable, unknown class, unpriceable configuration,
# extractor accident, no facts, facts -- and folding them into fewer branches would hide the
# order rather than simplify it.
def extract_cost_facts(component: Any) -> FactsExtraction:  # pylint: disable=too-many-return-statements
    """Facts for one component: the adopted `get_cost_facts()` API first, adapter table second.

    The full-information entry point `bridge.py` uses, and the place the migration order is
    enforced: a component that has adopted the §9.1 declaration wins, and the compatibility table
    is consulted only when it has not. That precedence is what lets adoption happen component by
    component without any coordinating change, and what makes an adapter entry become dead the
    moment the component implements the hook.

    The rule for the "no facts" cases is that only a genuinely unknown class may come back
    without a reason. A `get_cost_facts()` that returns None on a class declared `FREE_OF_COST`
    (§9.2) means "genuinely no cost", so the table is deliberately *not* consulted afterwards; any
    other None falls through to the table. A registered class whose extractor returns None — an
    unmapped boiler fuel, an unpriced emitter type — gets an `unresolved_reason` naming the class,
    which the bridge turns into a D7 failure (issue #2). So does a class declared `PRICED` that
    has neither the hook nor a table entry, and so does an extractor that trips over a config
    field that has moved: both used to pass silently (the latter as nothing but a log warning), and
    both meant a component vanished from every cost result because of a rename. A parallel-phase
    accident is still an accident that unprices a device, so it fails like one. A
    `CostDataError` — the adapter's own way of saying "this configuration cannot be priced", see
    `_fuel_meter_carrier` — is reported the same way.

    The only silent outcome left is a class the adapter has never heard of and that declared
    nothing, which the bridge rejects on its `UNDECLARED` relevance before it ever asks for facts.

    Args:
        component: The finished simulation's component object; only its class name, its
            `cost_relevance`, its `config` and the optional hook are read.

    Returns:
        A `FactsExtraction` — facts when the component could be described, a reason when a
        recognized or declared-priced component could not be, and neither when the adapter simply
        does not know the class and the class claims nothing.
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
    """The component class's declared cost role, and nothing else (§9.2).

    §9.2 exists because the naive default — "no `get_cost_facts()` means no costs" — has a failure
    mode locality never had: a forgotten implementation silently drops a component from every cost
    result. Declaring `cost_relevance` on the class is therefore mandatory, and this function only
    reports the declaration: a class that declares nothing is `UNDECLARED`, which `bridge.py`
    treats as fatal (D7) rather than as something to be guessed at.

    Earlier revisions inferred `METER` from `get_meter_spec` and `PRICED` from
    `FactsExtractors.BY_CLASS_NAME` for undeclared classes, as a migration aid. That inference is
    gone: it turned the two things that must be visible — a component nobody has classified, and a
    component whose adapter entry no longer matches its class name — into a plausible-looking
    answer. A meter still needs `get_meter_spec` to say *how* to read its flows, and `bridge.py`
    calls that separately; a meter that is also a priced device (an electricity meter measures
    flows and costs money) declares `METER` and is still asked for cost facts.

    Args:
        component: The component to classify; only its class's `cost_relevance` attribute is read,
            so this never touches a config and never raises.

    Returns:
        The declared `CostRelevance`, or `CostRelevance.UNDECLARED` when the class declares none.
    """
    declared: CostRelevance = getattr(type(component), "cost_relevance", CostRelevance.UNDECLARED)
    return declared
