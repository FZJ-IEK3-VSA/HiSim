"""Serialization of evaluator inputs for post-hoc re-pricing (cost_spec.md §4.6).

All evaluator inputs are serialized into the result directory (`economic_inputs.json`,
accompanied by `cost_provenance.json`) so new economic assumptions never require re-running
the building simulation.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.evaluator import EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    ExistingAsset,
    ExistingAssetRegister,
)
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    HeritageStatus,
    SubsidyBuildingContext,
    SubsidyContext,
)
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

ECONOMIC_INPUTS_FILE_NAME = "economic_inputs.json"
PROVENANCE_FILE_NAME = "cost_provenance.json"


def _band_or_none(value: Optional[UncertainValue]) -> Any:
    return value.to_json() if value is not None else None


def _band_from(value: Any) -> Optional[UncertainValue]:
    return UncertainValue.from_json(value) if value is not None else None


def facts_to_json(facts: ComponentCostFacts) -> dict:
    """Serializes ComponentCostFacts."""
    return {
        "asset_class": facts.asset_class.name,
        "size": facts.size,
        "size_unit": facts.size_unit.name,
        "kpi_tag": facts.kpi_tag.value if facts.kpi_tag else None,
        "count": facts.count,
        "investment_cost_override_in_euro": _band_or_none(facts.investment_cost_override_in_euro),
        "installation_cost_override_in_euro": _band_or_none(facts.installation_cost_override_in_euro),
        "lifetime_override_in_years": facts.lifetime_override_in_years,
        "maintenance_rate_override": _band_or_none(facts.maintenance_rate_override),
        "fixed_operation_cost_override_in_euro_per_year": _band_or_none(
            facts.fixed_operation_cost_override_in_euro_per_year
        ),
        "embodied_co2_override_in_kg": facts.embodied_co2_override_in_kg,
        "override_source": facts.override_source,
        "technical_attributes": facts.technical_attributes,
    }


def facts_from_json(raw: dict) -> ComponentCostFacts:
    """Deserializes ComponentCostFacts."""
    from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass

    kpi_tag = None
    if raw.get("kpi_tag"):
        kpi_tag = KpiTagEnumClass(raw["kpi_tag"])
    return ComponentCostFacts(
        asset_class=ComponentType[raw["asset_class"]],
        size=raw["size"],
        size_unit=Units[raw["size_unit"]],
        kpi_tag=kpi_tag,
        count=raw.get("count", 1),
        investment_cost_override_in_euro=_band_from(raw.get("investment_cost_override_in_euro")),
        installation_cost_override_in_euro=_band_from(raw.get("installation_cost_override_in_euro")),
        lifetime_override_in_years=raw.get("lifetime_override_in_years"),
        maintenance_rate_override=_band_from(raw.get("maintenance_rate_override")),
        fixed_operation_cost_override_in_euro_per_year=_band_from(
            raw.get("fixed_operation_cost_override_in_euro_per_year")
        ),
        embodied_co2_override_in_kg=raw.get("embodied_co2_override_in_kg"),
        override_source=raw.get("override_source"),
        technical_attributes=raw.get("technical_attributes", {}),
    )


def billing_to_json(determinants: BillingDeterminants) -> dict:
    """Serializes BillingDeterminants."""
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
    """Deserializes BillingDeterminants."""
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


def register_to_json(register: Optional[ExistingAssetRegister]) -> Optional[list]:
    """Serializes the existing-asset register."""
    if register is None:
        return None
    return [
        {
            "asset_class": asset.asset_class.name,
            "size": asset.size,
            "size_unit": asset.size_unit.name,
            "installation_year": asset.installation_year,
            "replacement_cost_override_in_euro": _band_or_none(asset.replacement_cost_override_in_euro),
            "is_functional": asset.is_functional,
            "energy_carrier": asset.energy_carrier.value if asset.energy_carrier else None,
            "replaced_by_asset_classes": [asset_class.name for asset_class in asset.replaced_by_asset_classes],
        }
        for asset in register.assets
    ]


def register_from_json(raw: Optional[list]) -> Optional[ExistingAssetRegister]:
    """Deserializes the existing-asset register."""
    if raw is None:
        return None
    return ExistingAssetRegister(
        assets=[
            ExistingAsset(
                asset_class=ComponentType[item["asset_class"]],
                size=item["size"],
                size_unit=Units[item["size_unit"]],
                installation_year=item["installation_year"],
                replacement_cost_override_in_euro=_band_from(item.get("replacement_cost_override_in_euro")),
                is_functional=item.get("is_functional", True),
                energy_carrier=EnergyCarrier(item["energy_carrier"]) if item.get("energy_carrier") else None,
                replaced_by_asset_classes=[
                    ComponentType[name] for name in item.get("replaced_by_asset_classes", [])
                ],
            )
            for item in raw
        ]
    )


def subsidy_context_to_json(context: SubsidyContext) -> dict:
    """Serializes the subsidy context."""
    building = context.building
    return {
        "applicant": {
            "actor": context.applicant.actor.value,
            "taxable_household_income_in_euro": context.applicant.taxable_household_income_in_euro,
            "household_size": context.applicant.household_size,
            "main_residence": context.applicant.main_residence,
            "region": context.applicant.region,
        },
        "building": {
            "construction_year": building.construction_year,
            "dwelling_units": building.dwelling_units,
            "heated_floor_area_in_m2": building.heated_floor_area_in_m2,
            "residential_floor_area_in_m2": building.residential_floor_area_in_m2,
            "commercial_floor_area_in_m2": building.commercial_floor_area_in_m2,
            "heritage_status": building.heritage_status.value if building.heritage_status else None,
            "energy_performance_class": building.energy_performance_class,
            "has_isfp": building.has_isfp,
        },
    }


def subsidy_context_from_json(raw: dict) -> SubsidyContext:
    """Deserializes the subsidy context."""
    applicant_raw = raw.get("applicant", {})
    building_raw = raw.get("building", {})
    return SubsidyContext(
        applicant=ApplicantProfile(
            actor=ApplicantActor(applicant_raw.get("actor", "OWNER_OCCUPIER")),
            taxable_household_income_in_euro=applicant_raw.get("taxable_household_income_in_euro"),
            household_size=applicant_raw.get("household_size"),
            main_residence=applicant_raw.get("main_residence"),
            region=applicant_raw.get("region"),
        ),
        building=SubsidyBuildingContext(
            construction_year=building_raw.get("construction_year"),
            dwelling_units=building_raw.get("dwelling_units", 1),
            heated_floor_area_in_m2=building_raw.get("heated_floor_area_in_m2"),
            residential_floor_area_in_m2=building_raw.get("residential_floor_area_in_m2"),
            commercial_floor_area_in_m2=building_raw.get("commercial_floor_area_in_m2", 0.0),
            heritage_status=HeritageStatus(building_raw["heritage_status"])
            if building_raw.get("heritage_status")
            else HeritageStatus.NONE,
            energy_performance_class=building_raw.get("energy_performance_class"),
            has_isfp=building_raw.get("has_isfp"),
        ),
    )


def inputs_to_json(inputs: EvaluationInputs) -> dict:
    """Serializes EvaluationInputs to the economic_inputs.json structure."""
    return {
        "simulation_year": inputs.simulation_year,
        "simulated_period_fraction": inputs.simulated_period_fraction,
        "cost_facts": [
            {"subject": subject_facts.subject, "facts": facts_to_json(subject_facts.facts)}
            for subject_facts in inputs.cost_facts
        ],
        "billing": [billing_to_json(determinants) for determinants in inputs.billing],
        "existing_assets": register_to_json(inputs.existing_assets),
        "subsidy_context": subsidy_context_to_json(inputs.subsidy_context),
        "tariff_contract_ids": {
            carrier.value: contract.id for carrier, contract in inputs.tariff_contracts.items()
        },
        "consumed_tariff_ids": inputs.consumed_tariff_ids,
        "annual_heat_demand_in_kwh": inputs.annual_heat_demand_in_kwh,
        "building_specific_emissions_in_kg_per_m2_a": inputs.building_specific_emissions_in_kg_per_m2_a,
        "heated_floor_area_in_m2": inputs.heated_floor_area_in_m2,
        "living_area_in_m2": inputs.living_area_in_m2,
        "current_cold_rent_in_euro_per_m2_month": inputs.current_cold_rent_in_euro_per_m2_month,
    }


def inputs_from_json(raw: dict, tariffs_base_path: Optional[str] = None) -> EvaluationInputs:
    """Deserializes EvaluationInputs; tariff contracts are re-resolved by id."""
    from hisim.economics.tariffs import load_tariff_contract

    contracts: Dict[EnergyCarrier, Any] = {}
    for carrier_name, contract_id in (raw.get("tariff_contract_ids") or {}).items():
        if contract_id.split("_DEFAULT_")[0] in ("DE", "AT") and "_DEFAULT_" in contract_id:
            continue  # default contracts are regenerated from the price entries
        contracts[EnergyCarrier(carrier_name)] = load_tariff_contract(contract_id, tariffs_base_path)
    return EvaluationInputs(
        simulation_year=raw["simulation_year"],
        simulated_period_fraction=raw["simulated_period_fraction"],
        cost_facts=[
            SubjectCostFacts(subject=item["subject"], facts=facts_from_json(item["facts"]))
            for item in raw.get("cost_facts", [])
        ],
        billing=[billing_from_json(item) for item in raw.get("billing", [])],
        existing_assets=register_from_json(raw.get("existing_assets")),
        subsidy_context=subsidy_context_from_json(raw.get("subsidy_context", {})),
        tariff_contracts=contracts,
        consumed_tariff_ids=raw.get("consumed_tariff_ids", []),
        annual_heat_demand_in_kwh=raw.get("annual_heat_demand_in_kwh"),
        building_specific_emissions_in_kg_per_m2_a=raw.get("building_specific_emissions_in_kg_per_m2_a"),
        heated_floor_area_in_m2=raw.get("heated_floor_area_in_m2"),
        living_area_in_m2=raw.get("living_area_in_m2"),
        current_cold_rent_in_euro_per_m2_month=raw.get("current_cold_rent_in_euro_per_m2_month"),
    )


def write_inputs(inputs: EvaluationInputs, result_directory: str) -> str:
    """Writes economic_inputs.json into the result directory."""
    path = os.path.join(result_directory, ECONOMIC_INPUTS_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(inputs_to_json(inputs), file, indent=2)
    return path


def read_inputs(result_directory: str) -> EvaluationInputs:
    """Reads economic_inputs.json from a (possibly archived) result directory."""
    path = os.path.join(result_directory, ECONOMIC_INPUTS_FILE_NAME)
    with open(path, encoding="utf-8") as file:
        return inputs_from_json(json.load(file))
