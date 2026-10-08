"""Serialization of evaluator inputs and stored results, for re-pricing without re-simulating (cost_spec.md §4.6).

Everything the evaluator needs about one simulated variant is written to ``economic_inputs.json`` and read back, so an
archived run can be re-priced under new rates or catalogs and evaluate to the same result. A field that does not
round-trip would make an archived run re-price differently from the original. The one deliberate exception is the
default tariff contract built from the §3.5 price entries: it is written but not restored, because the evaluator
regenerates it at the price basis year. The second half of the module reads ``lifecycle_costs.json``,
``cash_flow_timeline.csv`` and ``cost_provenance.json`` back into an `EvaluationMatrix`, so ``python -m hisim.economics
report`` renders a stored evaluation instead of re-evaluating.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, Optional

from hisim.economics.calculators.aggregation import TimelineAggregation
from hisim.economics.carriers import EnergyCarrier, UsefulHeatKind, validate_energy_attribution
from hisim.economics.evaluator import EvaluationInputs, SubjectCostFacts, UnresolvedSubject
from hisim.economics.exports import ExportFileNames
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    ExistingAsset,
    ExistingAssetRegister,
    InstallationYearOrigin,
)
from hisim.economics.parameters import EconomicParameters
from hisim.economics.provenance import ProvenanceLedger
from hisim.economics.results import (
    AnnualEnergyQuantities,
    ComponentCostBreakdown,
    EconomicAssumptions,
    EmbodiedCo2Basis,
    EvaluationMatrix,
    LifecycleCo2Result,
    LifecycleCostResult,
    ModernizationLevySummary,
    ReferenceAreas,
)
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    DwellingType,
    HeritageStatus,
    PayoutKind,
    SubsidyAward,
    SubsidyBuildingContext,
    SubsidyContext,
    SubsidyDecision,
)
from hisim.economics.tariffs import TariffContract, contract_to_json, load_tariff_contract
from hisim.economics.timeline import Actor, CashFlowEntry, CashFlowTimeline, CostCategory, SubjectKind
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource, KpiTagEnumClass


class SerializationFileNames:
    """Names of the files the input and provenance serialization writes.

    Export file names are in `exports.ExportFileNames`.
    """

    ECONOMIC_INPUTS_FILE_NAME = "economic_inputs.json"
    PROVENANCE_FILE_NAME = "cost_provenance.json"

    #: Top-level key of `economic_inputs.json` holding the country the run was priced for. A re-pricing consumer
    #: cannot derive or default it.
    COUNTRY_KEY = "country"

    #: Top-level key holding the resolved price basis year the run priced at: `EconomicParameters.price_basis_year`
    #: when set, else what `evaluator.effective_price_basis_year` picked. A consumer holding only this file would
    #: otherwise re-derive a possibly different year.
    PRICE_BASIS_YEAR_KEY = "price_basis_year"


def facts_to_json(facts: ComponentCostFacts) -> dict:
    """Serialize `ComponentCostFacts`, including per-field overrides and `override_source`.

    Enums are stored by name (asset class, size unit) and the KPI tag by its value; `technical_attributes` passes
    through unchanged, so it must be JSON-serializable.
    """
    return {
        "asset_class": facts.asset_class.name,
        "size": facts.size,
        "size_unit": facts.size_unit.name,
        "kpi_tag": facts.kpi_tag.value if facts.kpi_tag else None,
        "count": facts.count,
        "investment_cost_override_in_euro": UncertainValue.optional_to_json(facts.investment_cost_override_in_euro),
        "installation_cost_override_in_euro": UncertainValue.optional_to_json(facts.installation_cost_override_in_euro),
        "lifetime_override_in_years": facts.lifetime_override_in_years,
        "maintenance_rate_override": UncertainValue.optional_to_json(facts.maintenance_rate_override),
        "fixed_operation_cost_override_in_euro_per_year": UncertainValue.optional_to_json(
            facts.fixed_operation_cost_override_in_euro_per_year
        ),
        "embodied_co2_override_in_kg": facts.embodied_co2_override_in_kg,
        "purchase_cost_override_in_euro": UncertainValue.optional_to_json(facts.purchase_cost_override_in_euro),
        "override_source": facts.override_source,
        "lifetime_is_engine_fallback": facts.lifetime_is_engine_fallback,
        "price_is_unknown": facts.price_is_unknown,
        "lifetime_of_asset_class": facts.lifetime_of_asset_class.name if facts.lifetime_of_asset_class else None,
        "own_register_entry": facts.own_register_entry,
        "share_of_energy_sold": facts.share_of_energy_sold,
        "technical_attributes": facts.technical_attributes,
    }


def facts_from_json(raw: dict) -> ComponentCostFacts:
    """Deserialize `ComponentCostFacts`, the inverse of `facts_to_json`.

    Optional keys fall back to their dataclass defaults. Construction runs `ComponentCostFacts.__post_init__`, so a
    hand-edited file with an implausible size or unknown size unit is rejected here.
    """
    kpi_tag = None
    if raw.get("kpi_tag"):
        kpi_tag = KpiTagEnumClass(raw["kpi_tag"])
    return ComponentCostFacts(
        asset_class=ComponentType[raw["asset_class"]],
        size=raw["size"],
        size_unit=Units[raw["size_unit"]],
        kpi_tag=kpi_tag,
        count=raw.get("count", 1),
        investment_cost_override_in_euro=UncertainValue.optional_from_json(raw.get("investment_cost_override_in_euro")),
        installation_cost_override_in_euro=UncertainValue.optional_from_json(
            raw.get("installation_cost_override_in_euro")
        ),
        lifetime_override_in_years=raw.get("lifetime_override_in_years"),
        maintenance_rate_override=UncertainValue.optional_from_json(raw.get("maintenance_rate_override")),
        fixed_operation_cost_override_in_euro_per_year=UncertainValue.optional_from_json(
            raw.get("fixed_operation_cost_override_in_euro_per_year")
        ),
        embodied_co2_override_in_kg=raw.get("embodied_co2_override_in_kg"),
        purchase_cost_override_in_euro=UncertainValue.optional_from_json(raw.get("purchase_cost_override_in_euro")),
        override_source=raw.get("override_source"),
        lifetime_is_engine_fallback=bool(raw.get("lifetime_is_engine_fallback", False)),
        price_is_unknown=bool(raw.get("price_is_unknown", False)),
        lifetime_of_asset_class=(
            ComponentType[raw["lifetime_of_asset_class"]] if raw.get("lifetime_of_asset_class") else None
        ),
        # Both keys are optional; older files lack them.
        own_register_entry=bool(raw.get("own_register_entry", False)),
        share_of_energy_sold=float(raw.get("share_of_energy_sold", 1.0)),
        technical_attributes=raw.get("technical_attributes", {}),
    )


def billing_to_json(determinants: BillingDeterminants) -> dict:
    """Serialize `BillingDeterminants`: one record per carrier that crossed the system boundary (§3.4).

    Holds annual volumes bought and sold, the time-of-use band split, the peaks for capacity charges (§8.4), and for
    dynamic tariffs the integrated cost, revenue and mean spot price. Every quantity is in kWh for every carrier;
    conversion from EUR/t or EUR/l happens on the price side.
    """
    return {
        "carrier": determinants.carrier.value,
        "energy_bought_in_kwh": determinants.energy_bought_in_kwh,
        "energy_sold_in_kwh": determinants.energy_sold_in_kwh,
        "energy_bought_per_band_in_kwh": determinants.energy_bought_per_band_in_kwh,
        "cost_integrated_in_euro": determinants.cost_integrated_in_euro,
        "revenue_integrated_in_euro": determinants.revenue_integrated_in_euro,
        "peak_per_billing_period_in_kw": determinants.peak_per_billing_period_in_kw,
        "annual_peak_in_kw": determinants.annual_peak_in_kw,
        "mean_spot_price_in_euro_per_kwh": determinants.mean_spot_price_in_euro_per_kwh,
    }


def billing_from_json(raw: dict) -> BillingDeterminants:
    """Deserialize `BillingDeterminants`; only carrier and bought volume are required.

    Every other determinant defaults to "not measured", so a test input can state a bare annual consumption.
    """
    return BillingDeterminants(
        carrier=EnergyCarrier(raw["carrier"]),
        energy_bought_in_kwh=raw["energy_bought_in_kwh"],
        energy_sold_in_kwh=raw.get("energy_sold_in_kwh", 0.0),
        energy_bought_per_band_in_kwh=raw.get("energy_bought_per_band_in_kwh", {}),
        cost_integrated_in_euro=raw.get("cost_integrated_in_euro"),
        revenue_integrated_in_euro=raw.get("revenue_integrated_in_euro"),
        peak_per_billing_period_in_kw=raw.get("peak_per_billing_period_in_kw", []),
        annual_peak_in_kw=raw.get("annual_peak_in_kw", 0.0),
        mean_spot_price_in_euro_per_kwh=raw.get("mean_spot_price_in_euro_per_kwh"),
    )


def asset_to_json(asset: ExistingAsset) -> dict:
    """Serialize one existing asset, i.e. a piece of the building as it was before the measure.

    The fields are what the brownfield arithmetic of §4.1 needs: installation year (remaining life, first replacement,
    anyway credit), functionality, carrier, an optional like-for-like replacement price, `anyway_share` (the share of
    the avoided like-for-like replacement credited, "Sowieso" share) and `replaced_by_asset_classes`, which marks it as
    replaced by the measure. Used for register entries and for the subsidy context's `existing_heating`.
    """
    return {
        "asset_class": asset.asset_class.name,
        "size": asset.size,
        "size_unit": asset.size_unit.name,
        "installation_year": asset.installation_year,
        "replacement_cost_override_in_euro": UncertainValue.optional_to_json(asset.replacement_cost_override_in_euro),
        "is_functional": asset.is_functional,
        "energy_carrier": asset.energy_carrier.value if asset.energy_carrier else None,
        "replaced_by_asset_classes": [asset_class.name for asset_class in asset.replaced_by_asset_classes],
        "anyway_share": asset.anyway_share,
        "installation_year_origin": (
            asset.installation_year_origin.value if asset.installation_year_origin is not None else None
        ),
        "subject": asset.subject,
    }


def asset_from_json(item: dict) -> ExistingAsset:
    """Deserialize one existing asset, the inverse of `asset_to_json`.

    `is_functional` defaults to True and the replacement declarations to empty: an old but working device that stays.
    """
    return ExistingAsset(
        asset_class=ComponentType[item["asset_class"]],
        size=item["size"],
        size_unit=Units[item["size_unit"]],
        installation_year=item["installation_year"],
        replacement_cost_override_in_euro=UncertainValue.optional_from_json(
            item.get("replacement_cost_override_in_euro")
        ),
        is_functional=item.get("is_functional", True),
        energy_carrier=EnergyCarrier(item["energy_carrier"]) if item.get("energy_carrier") else None,
        replaced_by_asset_classes=[ComponentType[name] for name in item.get("replaced_by_asset_classes", [])],
        # Optional key; older files lack it, and 1.0 is the full like-for-like credit.
        anyway_share=item.get("anyway_share", 1.0),
        # Optional key; older files lack it.
        installation_year_origin=(
            InstallationYearOrigin(item["installation_year_origin"])
            if item.get("installation_year_origin")
            else None
        ),
        # Optional key; older files lack it.
        subject=item.get("subject"),
    )


def register_to_json(register: Optional[ExistingAssetRegister]) -> Optional[list]:
    """Serialize the existing-asset register, keeping None distinct from an empty list.

    None means greenfield; an empty list means brownfield with nothing registered. Each selects different perspectives
    (`perspectives.select_applicable`).
    """
    if register is None:
        return None
    return [asset_to_json(asset) for asset in register.assets]


def register_from_json(raw: Optional[list]) -> Optional[ExistingAssetRegister]:
    """Deserializes the existing-asset register, keeping null distinct from an empty list."""
    if raw is None:
        return None
    return ExistingAssetRegister(assets=[asset_from_json(item) for item in raw])


def subsidy_context_to_json(context: SubsidyContext) -> dict:
    """Serialize the subsidy context: the applicant and building answers the eligibility conditions read (§5.3, §5.7).

    Every field is written even when None, because an unanswered field is undetermined in the condition language, not
    false. `building.existing_heating` matters for the DE catalog's BEG speed bonus.
    """
    building = context.building
    return {
        "applicant": {
            "actor": context.applicant.actor.value,
            "taxable_household_income_in_euro": context.applicant.taxable_household_income_in_euro,
            "household_size": context.applicant.household_size,
            "main_residence": context.applicant.main_residence,
            "region": context.applicant.region,
            "receives_means_tested_benefit": context.applicant.receives_means_tested_benefit,
            "first_time_buyer": context.applicant.first_time_buyer,
            "managed_full_retrofit": context.applicant.managed_full_retrofit,
        },
        "building": {
            "construction_year": building.construction_year,
            "dwelling_units": building.dwelling_units,
            "dwelling_type": building.dwelling_type.value if building.dwelling_type else None,
            "heated_floor_area_in_m2": building.heated_floor_area_in_m2,
            "residential_floor_area_in_m2": building.residential_floor_area_in_m2,
            "commercial_floor_area_in_m2": building.commercial_floor_area_in_m2,
            "heritage_status": building.heritage_status.value if building.heritage_status else None,
            "energy_performance_class": building.energy_performance_class,
            "existing_heating": asset_to_json(building.existing_heating) if building.existing_heating else None,
            "has_renovation_roadmap": building.has_renovation_roadmap,
        },
    }


def subsidy_context_from_json(raw: dict) -> SubsidyContext:
    """Deserialize the subsidy context; an empty dict yields an owner-occupier context with nothing answered.

    Only `heritage_status` gets a concrete default (NONE), as in `SubsidyBuildingContext`.
    """
    applicant_raw = raw.get("applicant", {})
    building_raw = raw.get("building", {})
    # Optional key; older files lack it and the value stays None.
    existing_heating_raw = building_raw.get("existing_heating")
    return SubsidyContext(
        applicant=ApplicantProfile(
            actor=ApplicantActor(applicant_raw.get("actor", "OWNER_OCCUPIER")),
            taxable_household_income_in_euro=applicant_raw.get("taxable_household_income_in_euro"),
            household_size=applicant_raw.get("household_size"),
            main_residence=applicant_raw.get("main_residence"),
            region=applicant_raw.get("region"),
            receives_means_tested_benefit=applicant_raw.get("receives_means_tested_benefit"),
            first_time_buyer=applicant_raw.get("first_time_buyer"),
            managed_full_retrofit=applicant_raw.get("managed_full_retrofit"),
        ),
        building=SubsidyBuildingContext(
            construction_year=building_raw.get("construction_year"),
            dwelling_units=building_raw.get("dwelling_units", 1),
            dwelling_type=DwellingType(building_raw["dwelling_type"])
            if building_raw.get("dwelling_type")
            else None,
            heated_floor_area_in_m2=building_raw.get("heated_floor_area_in_m2"),
            residential_floor_area_in_m2=building_raw.get("residential_floor_area_in_m2"),
            commercial_floor_area_in_m2=building_raw.get("commercial_floor_area_in_m2", 0.0),
            heritage_status=HeritageStatus(building_raw["heritage_status"])
            if building_raw.get("heritage_status")
            else HeritageStatus.NONE,
            energy_performance_class=building_raw.get("energy_performance_class"),
            existing_heating=asset_from_json(existing_heating_raw) if existing_heating_raw else None,
            has_renovation_roadmap=building_raw.get("has_renovation_roadmap"),
        ),
    )


def _attribution_from_json(raw: dict, key: str, context: str) -> Dict[str, Dict[str, float]]:
    """Read one per-subject energy-attribution map back, refusing a negative quantity.

    Used for the extract's simulated-period map and the result's annualized map. An absent key yields an empty map, and
    the energy balance chart then skips itself.

    Args:
        raw: The decoded JSON object holding the map.
        key: Its key in that object.
        context: The dotted field path, for the error message.

    Returns:
        Subject to role to kWh, empty when the key is absent.

    Raises:
        ValueError: If any quantity is negative.
    """
    attribution = {
        subject: {role: float(value) for role, value in by_role.items()}
        for subject, by_role in raw.get(key, {}).items()
    }
    validate_energy_attribution(attribution, context)
    return attribution


def inputs_to_json(inputs: EvaluationInputs) -> dict:
    """Serialize `EvaluationInputs` to the ``economic_inputs.json`` structure.

    Every field of `EvaluationInputs` appears and no economic assumption does (no prices, rates or perspectives); the
    price basis year is derived downstream from `simulation_year`. `write_inputs` adds the country and the resolved
    price basis year beside this payload. A field added to `EvaluationInputs` but not here is lost on re-pricing; the
    round-trip tests in ``tests/test_economics_data_and_integration.py`` catch that.
    """
    return {
        "simulation_year": inputs.simulation_year,
        "simulated_period_fraction": inputs.simulated_period_fraction,
        "cost_facts": [
            {"subject": subject_facts.subject, "facts": facts_to_json(subject_facts.facts)}
            for subject_facts in inputs.cost_facts
        ],
        "billing": [billing_to_json(determinants) for determinants in inputs.billing],
        # Per-subject energy-balance flows (simulated-period kWh per role). Nothing prices them, but they must
        # survive the round trip or a re-priced archive would lose the household energy balance.
        "energy_attribution_by_subject_in_kwh": {
            subject: dict(by_role)
            for subject, by_role in inputs.energy_attribution_by_subject_in_kwh.items()
        },
        # Extraction failures travel with the extract, so re-pricing an archived run fails the same way as the
        # original instead of pricing a smaller system.
        "unresolved_subjects": [
            {"subject": unresolved.subject, "reason": unresolved.reason}
            for unresolved in inputs.unresolved_subjects
        ],
        "existing_assets": register_to_json(inputs.existing_assets),
        "subsidy_context": subsidy_context_to_json(inputs.subsidy_context),
        # Contracts are embedded in full, so re-pricing uses the same contract data rather than whatever the
        # tariffs directory holds.
        "tariff_contracts": {
            carrier.value: contract_to_json(contract) for carrier, contract in inputs.tariff_contracts.items()
        },
        "consumed_tariff_ids": inputs.consumed_tariff_ids,
        "annual_heat_demand_in_kwh": inputs.annual_heat_demand_in_kwh,
        "useful_heat_of_simulated_period_in_kwh": inputs.useful_heat_of_simulated_period_in_kwh,
        "useful_heat_of_simulated_period_by_kind_in_kwh": dict(
            inputs.useful_heat_of_simulated_period_by_kind_in_kwh
        ),
        "building_specific_emissions_in_kg_per_m2_a": inputs.building_specific_emissions_in_kg_per_m2_a,
        "heated_floor_area_in_m2": inputs.heated_floor_area_in_m2,
        "living_area_in_m2": inputs.living_area_in_m2,
        "current_cold_rent_in_euro_per_m2_month": inputs.current_cold_rent_in_euro_per_m2_month,
        # Which subjects are HiSim components, and their KPI source (kpi_address_spec.md); the staged document's
        # rows carry it. None only for a record read from an older file.
        "component_sources": (
            None
            if inputs.component_sources is None
            else {subject: source.to_dict() for subject, source in inputs.component_sources.items()}
        ),
    }


def contracts_from_json(raw: dict, tariffs_base_path: Optional[str] = None) -> Dict[EnergyCarrier, Any]:
    """Read the tariff contracts of an inputs file.

    ``tariff_contracts`` holds full contract objects; older files hold ``tariff_contract_ids``, resolved against the
    tariffs directory. Contracts generated from the §3.5 price entries are skipped in both forms, because the evaluator
    regenerates them at the price basis year (which keeps scenario price overlays effective). An embedded contract is
    recognized by its ``is_default_contract`` flag, an id by `TariffContract.is_default_contract_id`; a loaded id-only
    contract whose flag is set is skipped too.
    """
    contracts: Dict[EnergyCarrier, Any] = {}
    embedded = raw.get("tariff_contracts")
    if embedded:
        for carrier_name, contract_raw in embedded.items():
            contract = TariffContract.from_json(contract_raw)
            if contract.is_default_contract:
                continue
            contracts[EnergyCarrier(carrier_name)] = contract
        return contracts
    for carrier_name, contract_id in (raw.get("tariff_contract_ids") or {}).items():
        if TariffContract.is_default_contract_id(contract_id):
            continue  # never had a file; regenerated from the price entries at the basis year
        contract = load_tariff_contract(contract_id, tariffs_base_path)
        if contract.is_default_contract:
            continue
        contracts[EnergyCarrier(carrier_name)] = contract
    return contracts


def inputs_from_json(raw: dict, tariffs_base_path: Optional[str] = None) -> EvaluationInputs:
    """Deserialize `EvaluationInputs`; only `simulation_year` and `simulated_period_fraction` are required.

    Used by every consumer that did not run the simulation: the `evaluate`, `explain` and `report` CLI commands, the
    parity pass, and tests with hand-written input files. Other fields fall back to their dataclass defaults.

    Args:
        raw: The parsed ``economic_inputs.json`` payload.
        tariffs_base_path: Directory to resolve contract ids against; needed only for older files that store ids.

    Returns:
        The reconstructed record; evaluating it must reproduce the original result.
    """
    contracts = contracts_from_json(raw, tariffs_base_path)
    return EvaluationInputs(
        simulation_year=raw["simulation_year"],
        simulated_period_fraction=raw["simulated_period_fraction"],
        cost_facts=[
            SubjectCostFacts(subject=item["subject"], facts=facts_from_json(item["facts"]))
            for item in raw.get("cost_facts", [])
        ],
        billing=[billing_from_json(item) for item in raw.get("billing", [])],
        energy_attribution_by_subject_in_kwh=_attribution_from_json(
            raw,
            "energy_attribution_by_subject_in_kwh",
            "EvaluationInputs.energy_attribution_by_subject_in_kwh",
        ),
        unresolved_subjects=[
            UnresolvedSubject(subject=item["subject"], reason=item["reason"])
            for item in raw.get("unresolved_subjects", [])
        ],
        existing_assets=register_from_json(raw.get("existing_assets")),
        subsidy_context=subsidy_context_from_json(raw.get("subsidy_context", {})),
        tariff_contracts=contracts,
        consumed_tariff_ids=raw.get("consumed_tariff_ids", []),
        annual_heat_demand_in_kwh=raw.get("annual_heat_demand_in_kwh"),
        useful_heat_of_simulated_period_in_kwh=raw.get("useful_heat_of_simulated_period_in_kwh"),
        # Optional key; older files lack it. `UsefulHeatKind(...)` refuses an unknown kind rather than carrying it
        # into `heat_cost_omits_hot_water`.
        useful_heat_of_simulated_period_by_kind_in_kwh={
            UsefulHeatKind(kind).value: float(kwh)
            for kind, kwh in (raw.get("useful_heat_of_simulated_period_by_kind_in_kwh") or {}).items()
        },
        building_specific_emissions_in_kg_per_m2_a=raw.get("building_specific_emissions_in_kg_per_m2_a"),
        heated_floor_area_in_m2=raw.get("heated_floor_area_in_m2"),
        living_area_in_m2=raw.get("living_area_in_m2"),
        current_cold_rent_in_euro_per_m2_month=raw.get("current_cold_rent_in_euro_per_m2_month"),
        component_sources=_component_sources_from_json(raw),
    )


def _component_sources_from_json(raw: dict) -> Optional[Dict[str, KpiSource]]:
    """Read the subject-to-KPI-source map of an inputs file.

    A file without the field yields None (unknown), never an empty map (no components), so a reader that needs the
    sources, such as the staged document's ``by_subject[].source``, can refuse it.

    Raises:
        ValueError: If the field is not a map of subject to source object, a source lacks ``name`` or has an unknown
            key (:meth:`KpiSource.from_json_object`), or a source's name differs from its subject.
    """
    if "component_sources" not in raw or raw["component_sources"] is None:
        return None
    entries = raw["component_sources"]
    if not isinstance(entries, dict):
        raise ValueError(f"economic_inputs.json: component_sources is not an object: {entries!r}")
    sources: Dict[str, KpiSource] = {}
    for subject, source_raw in entries.items():
        source = KpiSource.from_json_object(source_raw, f"economic_inputs.json: component_sources['{subject}']")
        if source.name != subject:
            raise ValueError(
                f"economic_inputs.json: component_sources files the source named '{source.name}' under "
                f"the subject '{subject}'; a component's subject is its runtime name."
            )
        sources[subject] = source
    return sources


def write_inputs(
    inputs: EvaluationInputs,
    result_directory: str,
    country: Optional[str] = None,
    price_basis_year: Optional[int] = None,
) -> str:
    """Write ``economic_inputs.json`` into the result directory and return its path.

    `bridge.py` calls it right after extraction, before the cost database is consulted, so the file is a faithful
    extract of the simulation even when nothing can be priced. The JSON is indented for humans to read and diff. Beside
    the extract it writes the country and the resolved price basis year (`SerializationFileNames.COUNTRY_KEY`,
    `SerializationFileNames.PRICE_BASIS_YEAR_KEY`), since a consumer holding only this file, such as a staged plan
    built from finished jobs, cannot derive them. Both values come from the caller.

    Args:
        inputs: The extract to write.
        result_directory: Where ``economic_inputs.json`` goes.
        country: The ISO-3166 alpha-2 code the run was priced for, or None if unknown (e.g. a test extract). The key is
            written either way.
        price_basis_year: The resolved basis year the run priced at, or None if unknown. The key is written either way.

    Returns:
        The path written.
    """
    path = os.path.join(result_directory, SerializationFileNames.ECONOMIC_INPUTS_FILE_NAME)
    payload = inputs_to_json(inputs)
    payload[SerializationFileNames.COUNTRY_KEY] = country
    payload[SerializationFileNames.PRICE_BASIS_YEAR_KEY] = price_basis_year
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
    return path


def read_stored_country(result_directory: str) -> Optional[str]:
    """Return the country a stored extract was priced for, or None when the file does not say.

    A staged plan reads its stage directories' country from here, since they hold only ``economic_inputs.json`` and the
    mapping report. None is returned for no file, no key, an explicit null or a non-string value; there is deliberately
    no default country.

    Example::

        read_stored_country("jobs/baseline/results")  # -> "IE"

    Args:
        result_directory: A directory holding ``economic_inputs.json``.

    Returns:
        The ISO-3166 alpha-2 code, or None when the file states none.
    """
    path = os.path.join(result_directory, SerializationFileNames.ECONOMIC_INPUTS_FILE_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as file:
        raw = json.load(file)
    country = raw.get(SerializationFileNames.COUNTRY_KEY)
    return country if isinstance(country, str) else None


def read_stored_price_basis_year(result_directory: str) -> Optional[int]:
    """Return the price basis year a stored extract was priced at, or None when the file does not say.

    The companion of `read_stored_country` for staged plans; re-deriving the year from `simulation_year` could price at
    a different level than the runs behind the plan. None is returned for no file, no key, an explicit null or a
    non-integer value (``True`` counts as non-integer).

    Example::

        read_stored_price_basis_year("jobs/baseline/results")  # -> 2026

    Args:
        result_directory: A directory holding ``economic_inputs.json``.

    Returns:
        The resolved basis year, or None when the file states none.
    """
    path = os.path.join(result_directory, SerializationFileNames.ECONOMIC_INPUTS_FILE_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as file:
        raw = json.load(file)
    year = raw.get(SerializationFileNames.PRICE_BASIS_YEAR_KEY)
    return year if isinstance(year, int) and not isinstance(year, bool) else None


def read_inputs(result_directory: str, tariffs_base_path: Optional[str] = None) -> EvaluationInputs:
    """Read ``economic_inputs.json`` from a result directory, possibly archived years ago.

    The directory can be re-priced against a newer database or catalog without the original setup, HiSim version or
    weather data (§4.6). ``tariffs_base_path`` is only used for older files that reference contracts by id.

    Raises:
        OSError: If the directory holds no ``economic_inputs.json``; callers read this as "not a lifecycle-cost run".
    """
    path = os.path.join(result_directory, SerializationFileNames.ECONOMIC_INPUTS_FILE_NAME)
    with open(path, encoding="utf-8") as file:
        return inputs_from_json(json.load(file), tariffs_base_path)


# ---------------------------------------------------------------------- stored results
#
# The inverse of the export set, so `python -m hisim.economics report` can render a stored evaluation without
# rebuilding database, catalog and evaluator. Three files carry a full `EvaluationMatrix`: `lifecycle_costs.json`
# (aggregates), `cash_flow_timeline.csv` (entries, long format) and, when present, `cost_provenance.json`
# (the ledger). The result also carries the physical quantities, reference areas, scope and simulation year the
# reports need. A reload cannot reproduce `source_resolver`; the report reads its source list from the stored
# input audit instead (`input_audit.read_input_audit`).


def _breakdown_from_json(raw: dict) -> ComponentCostBreakdown:
    """Rebuild one subject's `ComponentCostBreakdown` from ``lifecycle_costs.json``.

    Every field is required, since a breakdown missing a category would not sum to the perspective total.
    """
    return ComponentCostBreakdown(
        subject=raw["subject"],
        subject_kind=SubjectKind(raw["subject_kind"]),
        asset_class=ComponentType(raw["asset_class"]) if raw.get("asset_class") else None,
        kpi_tag=KpiTagEnumClass(raw["kpi_tag"]) if raw.get("kpi_tag") else None,
        npv_by_category={
            CostCategory(key): UncertainValue.from_json(value)
            for key, value in raw["npv_by_category"].items()
        },
        total_npv_in_euro=UncertainValue.from_json(raw["total_npv_in_euro"]),
        equivalent_annual_cost_in_euro=UncertainValue.from_json(raw["equivalent_annual_cost_in_euro"]),
        investment_gross_in_euro=UncertainValue.from_json(raw["investment_gross_in_euro"]),
        subsidies_nominal_in_euro=UncertainValue.from_json(raw["subsidies_nominal_in_euro"]),
        subsidies_npv_in_euro=UncertainValue.from_json(raw["subsidies_npv_in_euro"]),
        annual_cost_series_nominal_in_euro=[
            UncertainValue.from_json(value) for value in raw["annual_cost_series_nominal_in_euro"]
        ],
        lifecycle_co2_in_kg=raw["lifecycle_co2_in_kg"],
    )


def _decision_from_json(raw: dict) -> SubsidyDecision:
    """Rebuild one measure's `SubsidyDecision`, the §5 audit trail, from stored results.

    Restores the applied schemes (payout kind, schedule, loan terms, which caps bound in which slot), the rejected ones
    with their reason, and the undetermined ones with the most an unanswered question could still be worth.
    """
    return SubsidyDecision(
        measure_subject=raw["measure_subject"],
        applied=[
            SubsidyAward(
                scheme_id=item["scheme_id"],
                payout_kind=PayoutKind(item["payout_kind"]),
                upfront_amount=UncertainValue.from_json(item["upfront_amount"]),
                schedule_amounts=[UncertainValue.from_json(a) for a in item.get("schedule_amounts", [])],
                operational_rate_per_kwh=item.get("operational_rate_per_kwh", 0.0),
                operational_carrier=(
                    EnergyCarrier(item["operational_carrier"]) if item.get("operational_carrier") else None
                ),
                operational_duration_years=item.get("operational_duration_years", 0),
                loan_interest_rate=item.get("loan_interest_rate"),
                loan_term_in_years=item.get("loan_term_in_years"),
                # No default: an absent grant share means "not stated by the award" (inherit the
                # plan's), which is a different instruction than a stated 0.0.
                loan_repayment_grant_share=item.get("loan_repayment_grant_share"),
                reduced_vat_rate=item.get("reduced_vat_rate"),
                caps_binding_per_slot=dict(item.get("caps_binding_per_slot", {})),
                # Optional key; older results lack it, and the report then states the amount without the
                # multiplication behind it.
                benefit_rate=item.get("benefit_rate"),
                benefit_rate_before_group_cap=item.get("benefit_rate_before_group_cap"),
                benefit_rate_before_overall_cap=item.get("benefit_rate_before_overall_cap"),
                eligible_basis_in_euro=(
                    UncertainValue.from_json(item["eligible_basis_in_euro"])
                    if item.get("eligible_basis_in_euro") is not None
                    else None
                ),
                eligible_basis_cap_in_euro=item.get("eligible_basis_cap_in_euro"),
                # Optional key; older results lack it, and the award's `label` then falls back to the scheme id.
                display_name=item.get("display_name") or "",
            )
            for item in raw.get("applied", [])
        ],
        rejected=list(raw.get("rejected", [])),
        undetermined=list(raw.get("undetermined", [])),
        undetermined_upper_bound_in_euro=raw.get("undetermined_upper_bound_in_euro", 0.0),
        other_slot_optimal_combination=dict(raw.get("other_slot_optimal_combination", {})),
    )


def read_cash_flow_timelines(result_directory: str) -> Dict[str, CashFlowTimeline]:
    """Read ``cash_flow_timeline.csv`` back into one timeline per perspective.

    The stored timeline is the full allocated one (all payers); the perspective's scope comes from
    `LifecycleCostResult.scope_payer`.
    """
    path = os.path.join(result_directory, ExportFileNames.CASH_FLOW_TIMELINE_FILE_NAME)
    timelines: Dict[str, CashFlowTimeline] = {}
    if not os.path.isfile(path):
        return timelines
    with open(path, newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file, delimiter=";"):
            entries = timelines.setdefault(row["perspective"], CashFlowTimeline()).entries
            provenance = tuple(int(part) for part in (row.get("provenance_ids") or "").split() if part)
            entries.append(
                CashFlowEntry(
                    year=int(row["year"]),
                    amount_in_euro=UncertainValue(
                        best_estimate=float(row["nominal_best_estimate"]),
                        minimum=float(row["nominal_min"]),
                        maximum=float(row["nominal_max"]),
                    ),
                    category=CostCategory(row["category"]),
                    subject=row["subject"],
                    subject_kind=SubjectKind(row.get("subject_kind") or SubjectKind.COMPONENT.value),
                    payer=Actor(row["payer"]),
                    subsidy_scheme_id=row.get("subsidy_scheme_id") or None,
                    provenance_ids=provenance,
                )
            )
    return timelines


def result_from_json(
    raw: dict,
    timeline: Optional[CashFlowTimeline] = None,
    ledger: Optional[ProvenanceLedger] = None,
) -> LifecycleCostResult:
    """Rebuild one perspective's `LifecycleCostResult` from its ``lifecycle_costs.json`` entry.

    Restores the headline KPIs, the category, component and payer pivots, the nominal annual series, the CO2 result,
    the subsidy decisions and the parameters. Timeline and ledger come from separate files; without them every
    aggregate still renders but no figure can be explained.

    Args:
        raw: One perspective's entry of ``lifecycle_costs.json``.
        timeline: That perspective's reloaded cash-flow timeline, if the CSV was present.
        ledger: That perspective's provenance ledger, if ``cost_provenance.json`` was present.

    Returns:
        The reconstructed result, equivalent to what the evaluator produced.
    """
    co2 = raw.get("lifecycle_co2", {})
    equivalent_annual_cost = UncertainValue.from_json(raw["equivalent_annual_cost_in_euro"])
    # Older files have no monthly equivalent; it is the annuity over the months the aggregation divides by, so
    # it is derived rather than missing.
    monthly_equivalent_cost = UncertainValue.optional_from_json(raw.get("monthly_equivalent_cost_in_euro"))
    if monthly_equivalent_cost is None:
        monthly_equivalent_cost = equivalent_annual_cost.scale(1.0 / TimelineAggregation.MONTHS_PER_YEAR)
    return LifecycleCostResult(
        perspective_id=raw["perspective"],
        parameters=EconomicParameters.from_dict(raw["parameters"]),
        total_npv_in_euro=UncertainValue.from_json(raw["total_npv_in_euro"]),
        equivalent_annual_cost_in_euro=equivalent_annual_cost,
        monthly_equivalent_cost_in_euro=monthly_equivalent_cost,
        npv_by_category={
            CostCategory(key): UncertainValue.from_json(value)
            for key, value in raw["npv_by_category"].items()
        },
        npv_by_component={
            subject: UncertainValue.from_json(value) for subject, value in raw["npv_by_component"].items()
        },
        npv_by_payer={
            Actor(key): UncertainValue.from_json(value) for key, value in raw["npv_by_payer"].items()
        },
        component_breakdowns={
            subject: _breakdown_from_json(item) for subject, item in raw["component_breakdowns"].items()
        },
        annual_cost_series_nominal_in_euro=[
            UncertainValue.from_json(value) for value in raw["annual_cost_series_nominal_in_euro"]
        ],
        monthly_cost_year1_in_euro=UncertainValue.optional_from_json(raw.get("monthly_cost_year1_in_euro")),
        levelized_cost_of_heat_in_euro_per_kwh=UncertainValue.optional_from_json(
            raw.get("levelized_cost_of_heat_in_euro_per_kwh")
        ),
        timeline=timeline if timeline is not None else CashFlowTimeline(),
        lifecycle_co2_result=LifecycleCo2Result(
            embodied_co2_in_kg=co2.get("embodied_co2_in_kg", 0.0),
            operational_co2_by_year_in_kg=list(co2.get("operational_co2_by_year_in_kg", [])),
            operational_co2_by_carrier_in_kg=dict(co2.get("operational_co2_by_carrier_in_kg", {})),
            total_co2_in_kg=co2.get("total_co2_in_kg", 0.0),
            embodied_by_subject_in_kg=dict(co2.get("embodied_by_subject_in_kg", {})),
            # Optional key; older files lack it, and the CO2 section then states the masses without their
            # multiplication.
            emission_factor_by_carrier_in_kg_per_kwh=dict(
                co2.get("emission_factor_by_carrier_in_kg_per_kwh", {})
            ),
            embodied_basis_by_subject={
                subject: EmbodiedCo2Basis.from_json(value)
                for subject, value in co2.get("embodied_basis_by_subject", {}).items()
            },
        ),
        subsidy_decisions=[_decision_from_json(item) for item in raw.get("subsidy_decisions", [])],
        sunk_cost_written_off_in_euro=UncertainValue.from_json(raw["sunk_cost_written_off_in_euro"]),
        ledger=ledger,
        scope_payer=Actor(raw.get("scope_payer", Actor.SYSTEM.value)),
        annual_energy_quantities_by_carrier={
            carrier: AnnualEnergyQuantities(
                bought_in_kwh=item.get("bought_in_kwh", 0.0), sold_in_kwh=item.get("sold_in_kwh", 0.0)
            )
            for carrier, item in raw.get("annual_energy_quantities_by_carrier", {}).items()
        },
        reference_areas=ReferenceAreas(
            heated_floor_area_in_m2=raw.get("reference_areas", {}).get("heated_floor_area_in_m2"),
            living_area_in_m2=raw.get("reference_areas", {}).get("living_area_in_m2"),
        ),
        simulated_period_fraction=raw.get("simulated_period_fraction", 1.0),
        simulation_year=raw.get("simulation_year"),
        # Optional key; older files lack it, and the household energy balance then skips itself.
        annual_energy_attribution_by_subject_in_kwh=_attribution_from_json(
            raw,
            "annual_energy_attribution_by_subject_in_kwh",
            "LifecycleCostResult.annual_energy_attribution_by_subject_in_kwh",
        ),
        raw_flexibility_value_by_carrier=dict(raw.get("raw_flexibility_value_by_carrier", {})),
        # Optional key; older results lack it, and every credit is then a full one.
        anyway_share_by_subject={
            subject: float(share) for subject, share in raw.get("anyway_share_by_subject", {}).items()
        },
        # Optional key; without it the anyway caption states the share alone.
        anyway_basis_by_subject={
            subject: float(basis) for subject, basis in raw.get("anyway_basis_by_subject", {}).items()
        },
        # Optional key; without it the caption calls the basis "basis" rather than naming which of the two §4.1
        # branches produced it.
        anyway_basis_kind_by_subject={
            subject: str(kind)
            for subject, kind in raw.get("anyway_basis_kind_by_subject", {}).items()
        },
        # Optional key; None for every perspective without a levy, which is most of them.
        modernization_levy=ModernizationLevySummary.from_json(raw.get("modernization_levy")),
        # Optional key; None for older files.
        assumptions=EconomicAssumptions.from_json(raw.get("assumptions")),
    )


def matrix_from_json(
    raw: dict,
    timelines: Optional[Dict[str, CashFlowTimeline]] = None,
    ledgers: Optional[Dict[str, ProvenanceLedger]] = None,
) -> EvaluationMatrix:
    """Rebuild a full `EvaluationMatrix` (one result per perspective id) from ``lifecycle_costs.json``.

    Perspectives without a stored timeline or ledger are kept, so an incomplete directory still reports its numbers.

    Args:
        raw: The parsed ``lifecycle_costs.json``: perspective id to result payload.
        timelines: Reloaded timelines by perspective id (from `read_cash_flow_timelines`).
        ledgers: Reloaded provenance ledgers by perspective id.
    """
    timelines = timelines or {}
    ledgers = ledgers or {}
    matrix = EvaluationMatrix()
    for perspective, item in raw.items():
        matrix.results[perspective] = result_from_json(
            item, timelines.get(perspective), ledgers.get(perspective)
        )
    return matrix


def read_stored_parameters(result_directory: str) -> Optional[EconomicParameters]:
    """Return the economic parameters a stored run was priced under, or None if the directory has none.

    Lets `explain`, `report` and `evaluate` on an archived directory reproduce the run, including the resolved price
    basis year, instead of using engine defaults. The first perspective's parameters are returned; all perspectives of
    one matrix share them.

    Args:
        result_directory: A directory holding ``lifecycle_costs.json``.

    Returns:
        The stored parameters, or None when there is no stored evaluation or it has no perspective.
    """
    path = os.path.join(result_directory, ExportFileNames.LIFECYCLE_COSTS_FILE_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as file:
        raw = json.load(file)
    for item in raw.values():
        if "parameters" in item:
            parameters: EconomicParameters = EconomicParameters.from_dict(item["parameters"])
            return parameters
    return None


def read_results(result_directory: str) -> Optional[EvaluationMatrix]:
    """Read a stored evaluation back, or return None when the directory holds none.

    ``python -m hisim.economics report`` calls it to decide between rendering and re-pricing. Reads
    ``lifecycle_costs.json`` (required), ``cash_flow_timeline.csv`` and ``cost_provenance.json``.
    """
    path = os.path.join(result_directory, ExportFileNames.LIFECYCLE_COSTS_FILE_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as file:
        raw = json.load(file)
    ledgers: Dict[str, ProvenanceLedger] = {}
    ledger_path = os.path.join(result_directory, SerializationFileNames.PROVENANCE_FILE_NAME)
    if os.path.isfile(ledger_path):
        with open(ledger_path, encoding="utf-8") as file:
            ledgers = {
                perspective: ProvenanceLedger.from_json(item)
                for perspective, item in json.load(file).items()
            }
    return matrix_from_json(raw, read_cash_flow_timelines(result_directory), ledgers)
