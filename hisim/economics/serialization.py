"""Serialization of evaluator inputs for post-hoc re-pricing (cost_spec.md §4.6).

All evaluator inputs are serialized into the result directory (`economic_inputs.json`,
accompanied by `cost_provenance.json`) so new economic assumptions never require re-running
the building simulation.

**This module implements the seam-1 contract of cost-spec-v2 §2.1.** The engine is required to be
a pure function of a JSON file: everything the evaluator needs about one simulated variant must
survive being written to disk and read back, so that re-pricing an archived run under new interest
rates, an updated subsidy catalog or a reviewer's "what if" never re-runs the building simulation.
That is also what makes the two error classes separable — "the wrong physical quantities went in"
is a question about this file, "they were priced wrong" is a question about everything downstream
of it — and what lets the downstream tests be small hand-written JSON files with exact expected
results instead of simulation runs.

**Round-trip fidelity is therefore the property under test**, not an implementation detail:
`tests/test_economics_data_and_integration.py` writes inputs, reads them back and asserts that the
reloaded record evaluates to the same NPV per slot, that a `SubsidyBuildingContext.existing_heating`
survives with every attribute (it decides the BEG speed bonus — dropping it used to silently degrade
that scheme, cost-spec-v2 W1.3), and that a `TariffContract` which exists in no catalog file
round-trips byte-equal and prices identically (W1.4). A field that does not round-trip is a bug of
exactly the class this seam exists to prevent: it makes an archived run re-price differently from
the run that produced it, silently and without any error to catch. The one *deliberate* exception is
the default tariff contract synthesized from the §3.5 price entries — it is written for the record
but not restored, because it is derived data the evaluator regenerates at the price basis year, and
restoring it would freeze the shipped prices and defeat scenario price overlays.

The module owns nothing economic: no defaults, no fallbacks, no validation beyond what the target
types enforce in their own constructors. It also owns the *inverse* direction (below the "stored
results" divider, W4.5): reading `lifecycle_costs.json`, `cash_flow_timeline.csv` and
`cost_provenance.json` back into a full `EvaluationMatrix`, so `python -m hisim.economics report`
renders a stored evaluation instead of quietly performing a new one.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, Optional

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.evaluator import EvaluationInputs, SubjectCostFacts, UnresolvedSubject
from hisim.economics.exports import ExportFileNames
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    ExistingAsset,
    ExistingAssetRegister,
)
from hisim.economics.parameters import EconomicParameters
from hisim.economics.provenance import ProvenanceLedger
from hisim.economics.results import (
    AnnualEnergyQuantities,
    ComponentCostBreakdown,
    EvaluationMatrix,
    LifecycleCo2Result,
    LifecycleCostResult,
    ReferenceAreas,
)
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    HeritageStatus,
    PayoutKind,
    SubsidyAward,
    SubsidyBuildingContext,
    SubsidyContext,
    SubsidyDecision,
)
from hisim.economics.tariffs import contract_to_json
from hisim.economics.timeline import Actor, CashFlowEntry, CashFlowTimeline, CostCategory, SubjectKind
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass


class SerializationFileNames:
    """Names of the files the input/provenance serialization writes.

    Kept as named constants rather than literals because both directions of the seam and several
    unrelated readers (the CLI, the parity pass, the report layer, archived-result tooling) address
    the same two files; the export-side names live in `exports.ExportFileNames`.
    """

    ECONOMIC_INPUTS_FILE_NAME = "economic_inputs.json"
    PROVENANCE_FILE_NAME = "cost_provenance.json"


def _band_or_none(value: Optional[UncertainValue]) -> Any:
    """Serializes an optional uncertainty band, preserving the None/zero distinction.

    Every monetary override is optional, and "no override" is a different statement from "an
    override of zero euro" — so None must survive as JSON null rather than collapsing to a band.
    """
    return value.to_json() if value is not None else None


def _band_from(value: Any) -> Optional[UncertainValue]:
    """The inverse of `_band_or_none`: JSON null stays None, anything else becomes a triplet."""
    return UncertainValue.from_json(value) if value is not None else None


def facts_to_json(facts: ComponentCostFacts) -> dict:
    """Serializes ComponentCostFacts.

    Writes every declared field including the per-field overrides and `override_source`, so a
    re-priced run applies exactly the same overrides — and stays as traceable — as the original.
    Enums are stored by `name` (asset class, size unit) and the KPI tag by its value; the free-form
    `technical_attributes` dict passes through unchanged, which is why `ComponentCostFacts` requires
    it to be JSON-serializable in the first place.
    """
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
    """Deserializes ComponentCostFacts.

    The exact inverse of `facts_to_json`. Optional keys are read with `.get` and their dataclass
    defaults, so a file written by an older version still loads; note that this reconstruction runs
    through `ComponentCostFacts.__post_init__`, i.e. the same fail-fast validation a component
    declaration goes through — a hand-edited inputs file with an implausible size or an unpriceable
    size unit is rejected here rather than producing nonsense downstream.
    """
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
    """Serializes BillingDeterminants.

    One record per carrier that crossed the system boundary (§3.4), carrying everything a tariff
    needs to bill it: annual volumes bought and sold, the time-of-use band split, the billing-period
    and annual peaks for capacity charges (§8.4), and — for dynamic tariffs — the in-simulation
    integrated cost/revenue and mean spot price. Every quantity is in kilowatt-hours, for every
    carrier: the `_in_kwh` field names are literally true since prices, not quantities, carry the
    conversion out of EUR/t and EUR/l (D26).
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
    """Deserializes BillingDeterminants.

    Only carrier and bought volume are required; every other determinant defaults to its
    "not measured" value, so a hand-written test input can state a bare annual consumption and be
    billed against a flat tariff without inventing peaks or band splits.
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
    """Serializes a single existing asset.

    An `ExistingAsset` is a piece of the building as it was *before* the measure, and the fields
    below are exactly what the brownfield arithmetic of §4.1 needs: installation year (drives
    remaining life, first replacement and the anyway-cost credit), functionality, carrier, an
    optional like-for-like replacement price, and `replaced_by_asset_classes` — the declaration that
    turns "kept" into "replaced by this measure". Used both for register entries and for the
    subsidy context's `existing_heating`, which the BEG speed bonus conditions on.
    """
    return {
        "asset_class": asset.asset_class.name,
        "size": asset.size,
        "size_unit": asset.size_unit.name,
        "installation_year": asset.installation_year,
        "replacement_cost_override_in_euro": _band_or_none(asset.replacement_cost_override_in_euro),
        "is_functional": asset.is_functional,
        "energy_carrier": asset.energy_carrier.value if asset.energy_carrier else None,
        "replaced_by_asset_classes": [asset_class.name for asset_class in asset.replaced_by_asset_classes],
    }


def asset_from_json(item: dict) -> ExistingAsset:
    """Deserializes a single existing asset.

    Inverse of `asset_to_json`; `is_functional` defaults to True and the replacement declarations to
    empty, which is the "an old but working device is simply there" reading.
    """
    return ExistingAsset(
        asset_class=ComponentType[item["asset_class"]],
        size=item["size"],
        size_unit=Units[item["size_unit"]],
        installation_year=item["installation_year"],
        replacement_cost_override_in_euro=_band_from(item.get("replacement_cost_override_in_euro")),
        is_functional=item.get("is_functional", True),
        energy_carrier=EnergyCarrier(item["energy_carrier"]) if item.get("energy_carrier") else None,
        replaced_by_asset_classes=[ComponentType[name] for name in item.get("replaced_by_asset_classes", [])],
    )


def register_to_json(register: Optional[ExistingAssetRegister]) -> Optional[list]:
    """Serializes the existing-asset register.

    The None/list distinction carries real meaning here and must survive the file: no register at
    all means greenfield, and an empty list means "a brownfield situation with nothing worth
    registering" — the former evaluates the greenfield perspectives, the latter the brownfield ones
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
    """Serializes the subsidy context.

    The applicant/building answers the §5.3 eligibility conditions resolve against — everything the
    §5.7 questionnaire asks. Every field is written even when None, because in the condition language
    an unanswered field is *undetermined* (reported as such, with an upper bound on what it could
    have been worth) rather than false, and that tri-state has to survive re-pricing.

    `building.existing_heating` in particular is load-bearing: the shipped DE catalog's BEG speed
    bonus conditions on it, and it was silently dropped here until cost-spec-v2 W1.3 — re-priced
    runs used to lose that bonus without a word.
    """
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
            "existing_heating": asset_to_json(building.existing_heating) if building.existing_heating else None,
            "has_renovation_roadmap": building.has_renovation_roadmap,
        },
    }


def subsidy_context_from_json(raw: dict) -> SubsidyContext:
    """Deserializes the subsidy context.

    Tolerant by construction: an empty dict yields a default owner-occupier context with everything
    unanswered, which is what a run without an `EconomicContext` produces and what the flat shim
    path needs. Only `heritage_status` is defaulted to a concrete value (NONE) rather than left
    None, mirroring `SubsidyBuildingContext`'s own default.
    """
    applicant_raw = raw.get("applicant", {})
    building_raw = raw.get("building", {})
    # Absent in files written before the field was round-tripped; stays None there.
    existing_heating_raw = building_raw.get("existing_heating")
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
            existing_heating=asset_from_json(existing_heating_raw) if existing_heating_raw else None,
            has_renovation_roadmap=building_raw.get("has_renovation_roadmap"),
        ),
    )


def inputs_to_json(inputs: EvaluationInputs) -> dict:
    """Serializes EvaluationInputs to the economic_inputs.json structure.

    The seam-1 payload: every field of `EvaluationInputs` appears here, which is the property that
    makes the evaluator a pure function of this file. Nothing economic is written — no prices, no
    rates, no perspective, not even the price basis year, which is re-derived downstream from
    `simulation_year` so the postprocessing bridge and the `evaluate` CLI cannot drift apart
    (cost-spec-v2 W1.2).

    Adding a field to `EvaluationInputs` without adding it here silently breaks re-pricing, since
    the reader would fall back to that field's default. The round-trip tests in
    `tests/test_economics_data_and_integration.py` are what catch it.
    """
    return {
        "simulation_year": inputs.simulation_year,
        "simulated_period_fraction": inputs.simulated_period_fraction,
        "cost_facts": [
            {"subject": subject_facts.subject, "facts": facts_to_json(subject_facts.facts)}
            for subject_facts in inputs.cost_facts
        ],
        "billing": [billing_to_json(determinants) for determinants in inputs.billing],
        # Extraction failures travel with the extract (issue #2): re-pricing an archived run must
        # hit the same D7 wall as the original one, not quietly price a smaller system.
        "unresolved_subjects": [
            {"subject": unresolved.subject, "reason": unresolved.reason}
            for unresolved in inputs.unresolved_subjects
        ],
        "existing_assets": register_to_json(inputs.existing_assets),
        "subsidy_context": subsidy_context_to_json(inputs.subsidy_context),
        # Contracts are embedded in full (W1.4): the file is self-contained, so re-pricing uses
        # byte-identical contract data instead of whatever the tariffs directory happens to hold.
        "tariff_contracts": {
            carrier.value: contract_to_json(contract) for carrier, contract in inputs.tariff_contracts.items()
        },
        "consumed_tariff_ids": inputs.consumed_tariff_ids,
        "annual_heat_demand_in_kwh": inputs.annual_heat_demand_in_kwh,
        "building_specific_emissions_in_kg_per_m2_a": inputs.building_specific_emissions_in_kg_per_m2_a,
        "heated_floor_area_in_m2": inputs.heated_floor_area_in_m2,
        "living_area_in_m2": inputs.living_area_in_m2,
        "current_cold_rent_in_euro_per_m2_month": inputs.current_cold_rent_in_euro_per_m2_month,
    }


def contracts_from_json(raw: dict, tariffs_base_path: Optional[str] = None) -> Dict[EnergyCarrier, Any]:
    """Reads the tariff contracts of an inputs file.

    Preferred form (W1.4): ``tariff_contracts`` holds the full contract objects, so no catalog
    lookup happens and contracts that exist in no file survive the round trip. Files written
    before that (``tariff_contract_ids``) are still read by resolving the ids against the
    tariffs directory. In both forms, contracts generated from the §3.5 price entries are
    skipped: they are derived data that the evaluator regenerates at the price basis year, which
    keeps scenario price overlays effective.
    """
    from hisim.economics.tariffs import TariffContract, load_tariff_contract

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
        if contract_id.split("_DEFAULT_")[0] in ("DE", "AT") and "_DEFAULT_" in contract_id:
            continue  # default contracts are regenerated from the price entries
        contracts[EnergyCarrier(carrier_name)] = load_tariff_contract(contract_id, tariffs_base_path)
    return contracts


def inputs_from_json(raw: dict, tariffs_base_path: Optional[str] = None) -> EvaluationInputs:
    """Deserializes EvaluationInputs (`tariffs_base_path` only matters for old id-only files).

    The entry point of every downstream consumer that did not run the simulation itself: the
    `evaluate` / `explain` / `report` CLI commands, the parity pass, and the downstream half of the
    seam-1 test strategy (hand-written input files with exact expected results). Only
    `simulation_year` and `simulated_period_fraction` are mandatory; everything else degrades to the
    dataclass default, so a minimal file stays a legitimate input.

    Args:
        raw: The parsed `economic_inputs.json` payload.
        tariffs_base_path: Directory to resolve contract *ids* against — needed only for files
            written before contracts were embedded in full (W1.4).

    Returns:
        The reconstructed record; evaluating it must reproduce the original result exactly.
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
        unresolved_subjects=[
            UnresolvedSubject(subject=item["subject"], reason=item["reason"])
            for item in raw.get("unresolved_subjects", [])
        ],
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
    """Writes economic_inputs.json into the result directory.

    Called by `bridge.py` as the very first thing after extraction — before the cost database is
    consulted, before the resolution check, before any evaluation — so the file is a faithful
    extract of the simulation and never depends on cost-database state (cost-spec-v2 W1.1). It is
    written even for a run where nothing can be priced, which is precisely when someone wants to
    look at it. Indented JSON on purpose: the file is meant to be read and diffed by humans.

    Returns:
        The path written, for logging and for tests.
    """
    path = os.path.join(result_directory, SerializationFileNames.ECONOMIC_INPUTS_FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(inputs_to_json(inputs), file, indent=2)
    return path


def read_inputs(result_directory: str, tariffs_base_path: Optional[str] = None) -> EvaluationInputs:
    """Reads economic_inputs.json from a (possibly archived) result directory.

    ``tariffs_base_path`` is only consulted for legacy files that reference contracts by id.

    "Possibly archived" is the point: a study's result directory stays re-priceable years after the
    simulation ran, against a newer cost database or an updated subsidy catalog, without the
    original system setup, HiSim version or weather data being available (§4.6).

    Raises:
        OSError: If the directory holds no `economic_inputs.json` — which the callers treat as
            "this directory was not produced by a lifecycle-cost run" rather than as a failure.
    """
    path = os.path.join(result_directory, SerializationFileNames.ECONOMIC_INPUTS_FILE_NAME)
    with open(path, encoding="utf-8") as file:
        return inputs_from_json(json.load(file), tariffs_base_path)


# ---------------------------------------------------------------------- stored results (W4.5)
#
# The inverse of the export set, so `python -m hisim.economics report` can render a stored
# evaluation without reconstructing database, catalog and evaluator — which is what it used to
# do, in contradiction of reporting's own docstring (cost-spec-v2 W4.5).
#
# Three files carry a full `EvaluationMatrix`: `lifecycle_costs.json` (everything aggregated),
# `cash_flow_timeline.csv` (the entries, in long format) and, when present,
# `cost_provenance.json` (the ledger). Nothing else is needed: since W4.2/W4.6 the result also
# carries the physical quantities, reference areas, scope and simulation year the reports and
# the plausibility checks used to fetch from `EvaluationInputs`. The one thing a reload cannot
# reproduce is `source_resolver`, a cost-database/catalog artifact — the report reads its source
# list off the stored input audit instead (`input_audit.read_input_audit`).


def _breakdown_from_json(raw: dict) -> ComponentCostBreakdown:
    """Rebuilds one subject's `ComponentCostBreakdown` from `lifecycle_costs.json` (W4.5).

    The per-subject pivot the stacked-bar frontends and the report's section 7 render (§7.4). Every
    field is required here rather than defaulted: a breakdown with a missing category would still
    render, but would no longer satisfy the invariant that the subjects sum to the perspective total
    — so a truncated file must fail loudly instead of producing a chart that does not reconcile.
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
    """Rebuilds one measure's `SubsidyDecision` — the §5 audit trail — from stored results (W4.5).

    Restores not only what was granted (`applied`, with payout kind, schedule, loan terms and which
    caps bound in which slot) but also what was *not*: rejected schemes with their reason and
    undetermined ones with the upper bound of what an unanswered question could still be worth. That
    negative half is the part a reviewer checks, so it has to survive into an archived directory
    rather than being recomputed from a catalog that may have changed since.
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
            )
            for item in raw.get("applied", [])
        ],
        rejected=list(raw.get("rejected", [])),
        undetermined=list(raw.get("undetermined", [])),
        undetermined_upper_bound_in_euro=raw.get("undetermined_upper_bound_in_euro", 0.0),
        other_slot_optimal_combination=dict(raw.get("other_slot_optimal_combination", {})),
    )


def read_cash_flow_timelines(result_directory: str) -> Dict[str, CashFlowTimeline]:
    """Reads `cash_flow_timeline.csv` back into one timeline per perspective.

    The stored timeline is always the **full** allocated one (all payers); the perspective's own
    scope is restored from `LifecycleCostResult.scope_payer`, exactly as the evaluator set it.
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
    """Rebuilds one `LifecycleCostResult` from its `lifecycle_costs.json` entry (W4.5).

    Restores one perspective's complete result — headline KPIs, the category/component/payer pivots,
    the nominal annual series, the CO2 result, the subsidy decisions and the parameters it was
    evaluated under. The timeline and the ledger come from separate files and are passed in, because
    they are stored per run rather than per perspective (`cash_flow_timeline.csv`,
    `cost_provenance.json`); a result without them still renders every aggregate figure, it just
    cannot explain one.

    Args:
        raw: One perspective's entry of `lifecycle_costs.json`.
        timeline: That perspective's reloaded cash-flow timeline, if the CSV was present.
        ledger: That perspective's provenance ledger, if `cost_provenance.json` was present.

    Returns:
        The reconstructed result, equivalent to what the evaluator produced for the same run.
    """
    co2 = raw.get("lifecycle_co2", {})
    return LifecycleCostResult(
        perspective_id=raw["perspective"],
        parameters=EconomicParameters.from_dict(raw["parameters"]),
        total_npv_in_euro=UncertainValue.from_json(raw["total_npv_in_euro"]),
        equivalent_annual_cost_in_euro=UncertainValue.from_json(raw["equivalent_annual_cost_in_euro"]),
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
        monthly_cost_year1_in_euro=_band_from(raw.get("monthly_cost_year1_in_euro")),
        levelized_cost_of_heat_in_euro_per_kwh=_band_from(raw.get("levelized_cost_of_heat_in_euro_per_kwh")),
        timeline=timeline if timeline is not None else CashFlowTimeline(),
        lifecycle_co2_result=LifecycleCo2Result(
            embodied_co2_in_kg=co2.get("embodied_co2_in_kg", 0.0),
            operational_co2_by_year_in_kg=list(co2.get("operational_co2_by_year_in_kg", [])),
            operational_co2_by_carrier_in_kg=dict(co2.get("operational_co2_by_carrier_in_kg", {})),
            total_co2_in_kg=co2.get("total_co2_in_kg", 0.0),
            embodied_by_subject_in_kg=dict(co2.get("embodied_by_subject_in_kg", {})),
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
        raw_flexibility_value_by_carrier=dict(raw.get("raw_flexibility_value_by_carrier", {})),
    )


def matrix_from_json(
    raw: dict,
    timelines: Optional[Dict[str, CashFlowTimeline]] = None,
    ledgers: Optional[Dict[str, ProvenanceLedger]] = None,
) -> EvaluationMatrix:
    """Rebuilds a full `EvaluationMatrix` from `lifecycle_costs.json` (W4.5).

    The matrix is just "one result per perspective id", so this loops `result_from_json` and hands
    each result its own timeline and ledger where they were found. Perspectives with no stored
    timeline or ledger are kept rather than skipped: an incomplete result directory should still
    report its numbers.

    Args:
        raw: The parsed `lifecycle_costs.json` — perspective id -> result payload.
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


def read_results(result_directory: str) -> Optional[EvaluationMatrix]:
    """Reads a stored evaluation back, or None when the directory holds none (W4.5).

    The one call `python -m hisim.economics report` makes before deciding whether it may render or
    must re-price: a directory written by the bridge, by `evaluate` or by an earlier `report` has
    everything the reports need, so reporting stays rendering rather than quietly becoming a second
    evaluation. Returning None (rather than raising) for a directory holding only
    `economic_inputs.json` is what makes that fallback a clean branch in the CLI.

    Reads three files, of which only the first is required: `lifecycle_costs.json` (the aggregates),
    `cash_flow_timeline.csv` (the entries) and `cost_provenance.json` (the ledger).
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
