"""The economic evaluator: facts -> cash flows -> results (cost_spec.md §3, §4).

A pure function of ``(facts, flows, cost_db, subsidy_catalog, econ_params, perspective)``.
No config mutation, no file I/O inside the calculation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from hisim import log
from hisim.economics.actors import AllocationContext, get_ruleset
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase, CostDataError, DeviceEntry
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    ExistingAsset,
    ExistingAssetRegister,
)
from hisim.economics.financing import FinancingPlan, loan_flows
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import (
    Accounting,
    ActorScope,
    InstallationContext,
    Perspective,
    SubsidyModeKind,
)
from hisim.economics.provenance import ParameterOrigin, ParameterProvenance, ProvenanceLedger
from hisim.economics.results import (
    ComponentCostBreakdown,
    EvaluationMatrix,
    LifecycleCo2Result,
    LifecycleCostResult,
)
from hisim.economics.subsidies import (
    MeasureForSubsidy,
    PayoutKind,
    SubsidyCatalog,
    SubsidyContext,
    SubsidyDecision,
    solve_cumulation,
)
from hisim.economics.tariffs import TariffContract, apply_tariff
from hisim.economics.timeline import (
    CashFlowEntry,
    CashFlowTimeline,
    CostCategory,
    SubjectKind,
)
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType


@dataclass
class SubjectCostFacts:
    """A component's cost facts together with its timeline subject name."""

    subject: str
    facts: ComponentCostFacts


@dataclass
class EvaluationInputs:
    """Everything the pure evaluator needs about one simulated variant.

    Serialized to `economic_inputs.json` for post-hoc re-pricing (§4.6).
    """

    simulation_year: int
    simulated_period_fraction: float  # simulated seconds / seconds of a full year
    cost_facts: List[SubjectCostFacts] = field(default_factory=list)
    billing: List[BillingDeterminants] = field(default_factory=list)
    existing_assets: Optional[ExistingAssetRegister] = None
    subsidy_context: SubsidyContext = field(default_factory=SubsidyContext)
    tariff_contracts: Dict[EnergyCarrier, TariffContract] = field(default_factory=dict)
    # Tariff ids whose price signal a controller consumed during the run (§4.6 boundary):
    consumed_tariff_ids: List[str] = field(default_factory=list)
    annual_heat_demand_in_kwh: Optional[float] = None  # for the levelized cost of heat
    # Building context for the actor model (§6.3, §6.4):
    building_specific_emissions_in_kg_per_m2_a: Optional[float] = None
    heated_floor_area_in_m2: Optional[float] = None
    living_area_in_m2: Optional[float] = None
    current_cold_rent_in_euro_per_m2_month: Optional[float] = None


@dataclass
class _DeviceCosting:
    """Resolved year-0 cost building blocks for one component (per slot)."""

    subject: str
    facts: ComponentCostFacts
    entry: Optional[DeviceEntry]
    device_cost: UncertainValue
    installation_cost: UncertainValue
    planning_cost: UncertainValue
    removal_cost_of_replaced: UncertainValue
    maintenance_rate: UncertainValue
    fixed_operation_cost: UncertainValue
    service_life_years: float
    embodied_co2_kg: float
    vat_rate: float
    legacy_flat_subsidy_share: float
    provenance_ids: Tuple[int, ...]
    is_new_investment: bool  # charged at year 0 in this installation context
    first_replacement_year: int  # relative year of the first replacement
    # Coupled-cost share and anyway threshold, resolved from the device entry (Q7):
    energy_related_cost_share: UncertainValue
    anyway_threshold_years: float
    replaced_asset: Optional[ExistingAsset] = None

    @property
    def gross_investment(self) -> UncertainValue:
        """I_gross = device + installation + planning (§3.6)."""
        return self.device_cost + self.installation_cost + self.planning_cost


class EconomicEvaluator:
    """Builds the canonical timeline and evaluates perspectives against it."""

    def __init__(
        self,
        cost_database: CostDatabase,
        parameters: EconomicParameters,
        subsidy_catalog: Optional[SubsidyCatalog] = None,
    ) -> None:
        """The catalog is optional: without one the legacy flat-percentage shim applies (§10.1)."""
        self.database = cost_database
        self.parameters = parameters
        self.subsidy_catalog = subsidy_catalog

    # ------------------------------------------------------------------ rate resolution

    def carrier_escalation_rate(self, carrier: EnergyCarrier) -> float:
        """Fallback chain: explicit parameter -> country defaults file -> general rate (§3.2)."""
        if carrier in self.parameters.energy_price_escalation_rates:
            return self.parameters.energy_price_escalation_rates[carrier]
        defaults = self.database.get_escalation_defaults(self.parameters.country)
        if carrier in defaults.carrier_rates:
            return defaults.carrier_rates[carrier]
        return self.parameters.general_price_escalation_rate

    def investment_escalation_rate(self, asset_class: ComponentType) -> float:
        """Fallback chain for per-asset-class investment escalation (learning curves, §3.2)."""
        if asset_class in self.parameters.investment_price_escalation_rates:
            return self.parameters.investment_price_escalation_rates[asset_class]
        defaults = self.database.get_escalation_defaults(self.parameters.country)
        if asset_class in defaults.asset_class_rates:
            return defaults.asset_class_rates[asset_class]
        return self.parameters.investment_price_escalation_rate

    def price_basis_year(self, inputs: EvaluationInputs) -> int:
        """Price basis year for database lookups; defaults to the simulation year."""
        return self.parameters.price_basis_year or inputs.simulation_year

    # ------------------------------------------------------------------ pre-run resolution check (§9.3)

    def resolve_check(self, inputs: EvaluationInputs, strict: bool = True) -> List[str]:
        """Dry-resolves every declared fact against the database; returns problem messages.

        Runs before the timestep loop so a missing database entry fails in seconds. The same
        pass populates the provenance ledger during evaluation (§3.10, §9.3).
        """
        problems: List[str] = []
        year = self.price_basis_year(inputs)
        for subject_facts in inputs.cost_facts:
            facts = subject_facts.facts
            if facts.has_overrides() and not facts.override_source:
                message = (
                    f"{subject_facts.subject}: cost overrides set without override_source (§3.10)."
                )
                if strict:
                    problems.append(message)
                else:
                    log.warning(message)
            if facts.investment_cost_override_in_euro is not None and facts.lifetime_override_in_years is not None:
                continue  # fully overridden facts need no database entry
            try:
                entry = self.database.get_device_entry(facts.asset_class, year, self.parameters.country)
            except CostDataError as err:
                problems.append(str(err))
                continue
            if entry.size_unit != facts.size_unit:
                problems.append(
                    f"{subject_facts.subject}: declared size_unit {facts.size_unit.value!r} does not match "
                    f"the database entry's per_unit ({entry.per_unit!r})."
                )
        for determinants in inputs.billing:
            if not self.database.has_energy_price(determinants.carrier, self.parameters.country):
                problems.append(
                    f"No energy price entry for carrier {determinants.carrier.value} in "
                    f"{self.parameters.country}."
                )
        return problems

    # ------------------------------------------------------------------ device costing

    def _resolve_device(
        self,
        subject_facts: SubjectCostFacts,
        inputs: EvaluationInputs,
        ledger: ProvenanceLedger,
        context: InstallationContext,
    ) -> _DeviceCosting:
        facts = subject_facts.facts
        year = self.price_basis_year(inputs)
        entry: Optional[DeviceEntry] = None
        provenance_ids: List[int] = []
        try:
            entry = self.database.get_device_entry(facts.asset_class, year, self.parameters.country)
        except CostDataError:
            if facts.investment_cost_override_in_euro is None or facts.lifetime_override_in_years is None:
                raise

        def override_record(field_name: str, value) -> int:
            return ledger.record(
                ParameterProvenance(
                    parameter=f"{subject_facts.subject}.{field_name}",
                    value=value,
                    origin=ParameterOrigin.CONFIG_OVERRIDE,
                    source_ids=(f"inline:{facts.override_source or 'override without source (migration mode)'}",),
                    detail=facts.override_source,
                )
            )

        if facts.investment_cost_override_in_euro is not None:
            device_cost = facts.investment_cost_override_in_euro.scale(float(facts.count))
            provenance_ids.append(override_record("investment_cost_override_in_euro", device_cost))
        else:
            assert entry is not None
            device_cost = entry.investment_for_size(facts.size).scale(float(facts.count))
            provenance_ids.append(self.database.provenance_for_device(entry, ledger, "specific_investment"))
        if facts.installation_cost_override_in_euro is not None:
            installation_cost = facts.installation_cost_override_in_euro
            provenance_ids.append(override_record("installation_cost_override_in_euro", installation_cost))
        elif entry is not None:
            installation_cost = entry.fixed_installation_cost_in_euro
        else:
            installation_cost = UncertainValue.exact(0.0)
        planning_cost = entry.planning_cost_in_euro if entry is not None else UncertainValue.exact(0.0)
        if facts.maintenance_rate_override is not None:
            maintenance_rate = facts.maintenance_rate_override
            provenance_ids.append(override_record("maintenance_rate_override", maintenance_rate))
        elif entry is not None:
            maintenance_rate = entry.maintenance_rate_per_year
            provenance_ids.append(self.database.provenance_for_device(entry, ledger, "maintenance_rate_per_year"))
        else:
            maintenance_rate = UncertainValue.exact(0.0)
        if facts.fixed_operation_cost_override_in_euro_per_year is not None:
            fixed_operation = facts.fixed_operation_cost_override_in_euro_per_year
            provenance_ids.append(override_record("fixed_operation_cost_override_in_euro_per_year", fixed_operation))
        elif entry is not None:
            fixed_operation = entry.fixed_operation_cost_in_euro_per_year
        else:
            fixed_operation = UncertainValue.exact(0.0)
        if facts.lifetime_override_in_years is not None:
            service_life = facts.lifetime_override_in_years
            provenance_ids.append(override_record("lifetime_override_in_years", service_life))
        else:
            assert entry is not None
            service_life = entry.service_life_in_years
            provenance_ids.append(self.database.provenance_for_device(entry, ledger, "service_life_in_years"))
        if facts.embodied_co2_override_in_kg is not None:
            embodied_co2 = facts.embodied_co2_override_in_kg
        elif entry is not None:
            embodied_co2 = entry.embodied_co2_for_size(facts.size) * facts.count
        else:
            embodied_co2 = 0.0

        # Installation context: matched-kept vs new measure vs replacement (§4.1). The
        # replacement check runs FIRST so like-for-like measures (new windows replacing old
        # windows, same asset class) are charged as investments instead of "kept".
        register = inputs.existing_assets
        is_new_investment = True
        first_replacement_year = int(round(service_life))
        replaced_asset: Optional[ExistingAsset] = None
        if context in (InstallationContext.BROWNFIELD, InstallationContext.STATUS_QUO) and register is not None:
            replaced_asset = next(
                (
                    asset
                    for asset in register.assets
                    if facts.asset_class in asset.replaced_by_asset_classes
                ),
                None,
            )
            if replaced_asset is None:
                matched = register.find(facts.asset_class)
                if matched is not None:
                    # Kept asset: no investment; first replacement at service_life - current_age.
                    # Ages anchor on the price basis year (the economic "today", like scheme
                    # validity and the CO2 path), not the possibly historical weather year.
                    is_new_investment = False
                    age = matched.age_in_years(self.price_basis_year(inputs))
                    first_replacement_year = max(1, int(round(service_life - age)))
        elif context == InstallationContext.STATUS_QUO and register is None:
            is_new_investment = False
            log.warning(
                f"STATUS_QUO without an existing-asset register: treating {subject_facts.subject} "
                "as an existing asset of age 0."
            )

        if context == InstallationContext.STATUS_QUO:
            is_new_investment = False
            if register is not None and register.find(facts.asset_class) is None:
                first_replacement_year = int(round(service_life))

        removal_cost = UncertainValue.exact(0.0)
        if is_new_investment and replaced_asset is not None:
            # Disposal of the replaced device type (§3.5 removal_cost).
            try:
                old_entry = self.database.get_device_entry(replaced_asset.asset_class, year, self.parameters.country)
                removal_cost = old_entry.removal_cost_in_euro
            except CostDataError:
                removal_cost = UncertainValue.exact(0.0)

        return _DeviceCosting(
            subject=subject_facts.subject,
            facts=facts,
            entry=entry,
            device_cost=device_cost,
            installation_cost=installation_cost,
            planning_cost=planning_cost,
            removal_cost_of_replaced=removal_cost,
            maintenance_rate=maintenance_rate,
            fixed_operation_cost=fixed_operation,
            service_life_years=service_life,
            embodied_co2_kg=embodied_co2,
            vat_rate=entry.vat_rate if entry is not None else 0.0,
            legacy_flat_subsidy_share=entry.legacy_flat_subsidy_share if entry is not None else 0.0,
            provenance_ids=tuple(provenance_ids),
            is_new_investment=is_new_investment,
            first_replacement_year=first_replacement_year,
            energy_related_cost_share=(
                entry.energy_related_cost_share if entry is not None else UncertainValue.exact(1.0)
            ),
            anyway_threshold_years=(
                entry.anyway_threshold_years_override
                if entry is not None and entry.anyway_threshold_years_override is not None
                else self.parameters.anyway_threshold_years
            ),
            replaced_asset=replaced_asset,
        )

    # ------------------------------------------------------------------ timeline construction (§3.6)

    def build_timeline(
        self,
        inputs: EvaluationInputs,
        perspective: Perspective,
        ledger: ProvenanceLedger,
    ) -> Tuple[CashFlowTimeline, List[SubsidyDecision], LifecycleCo2Result, UncertainValue, Dict[str, UncertainValue]]:
        """Builds the canonical timeline for one perspective.

        Returns (timeline, subsidy decisions, co2 result, sunk cost, modernization basis parts).
        """
        params = self.parameters
        horizon = params.observation_period_in_years
        timeline = CashFlowTimeline()
        co2_result = LifecycleCo2Result(operational_co2_by_year_in_kg=[0.0] * (horizon + 1))
        decisions: List[SubsidyDecision] = []
        sunk_cost = UncertainValue.exact(0.0)
        modernization_cost = UncertainValue.exact(0.0)
        subsidies_total = UncertainValue.exact(0.0)
        anyway_credit_total = UncertainValue.exact(0.0)
        context = perspective.installation_context
        include_investment = context != InstallationContext.OPERATING_ONLY
        macro = perspective.accounting == Accounting.MACROECONOMIC

        replacement_flows_for_reserve: List[Tuple[int, UncertainValue]] = []

        for subject_facts in inputs.cost_facts:
            costing = self._resolve_device(subject_facts, inputs, ledger, context)
            gross = costing.gross_investment
            subject = costing.subject
            asset_rate = self.investment_escalation_rate(costing.facts.asset_class)

            # --- year-0 investment (§3.6 rule 1)
            if include_investment and costing.is_new_investment:
                timeline.add(
                    CashFlowEntry(
                        year=0,
                        amount_in_euro=costing.device_cost + costing.installation_cost,
                        category=CostCategory.INVESTMENT,
                        subject=subject,
                        provenance_ids=costing.provenance_ids,
                    )
                )
                if costing.planning_cost.maximum > 0:
                    timeline.add(
                        CashFlowEntry(
                            year=0,
                            amount_in_euro=costing.planning_cost,
                            category=CostCategory.PLANNING,
                            subject=subject,
                            provenance_ids=costing.provenance_ids,
                        )
                    )
                if costing.removal_cost_of_replaced.maximum > 0:
                    timeline.add(
                        CashFlowEntry(
                            year=0,
                            amount_in_euro=costing.removal_cost_of_replaced,
                            category=CostCategory.REMOVAL,
                            subject=subject,
                            provenance_ids=costing.provenance_ids,
                        )
                    )
                modernization_cost = modernization_cost + gross + costing.removal_cost_of_replaced
                co2_result.embodied_co2_in_kg += costing.embodied_co2_kg
                co2_result.embodied_by_subject_in_kg[subject] = (
                    co2_result.embodied_by_subject_in_kg.get(subject, 0.0) + costing.embodied_co2_kg
                )

                # Replaced asset: sunk cost and anyway-cost credit (§4.1).
                replaced = costing.replaced_asset
                if replaced is not None:
                    try:
                        old_entry = self.database.get_device_entry(
                            replaced.asset_class, self.price_basis_year(inputs), params.country
                        )
                        like_for_like = (
                            replaced.replacement_cost_override_in_euro
                            or old_entry.investment_for_size(replaced.size)
                        )
                        old_life = old_entry.service_life_in_years
                    except CostDataError:
                        like_for_like = replaced.replacement_cost_override_in_euro or UncertainValue.exact(0.0)
                        old_life = 20.0
                    age = replaced.age_in_years(self.price_basis_year(inputs))
                    remaining = max(0.0, old_life - age)
                    sunk_cost = sunk_cost + like_for_like.scale(remaining / old_life if old_life else 0.0)
                    if remaining <= costing.anyway_threshold_years:
                        credit_year = int(round(remaining))
                        share = costing.energy_related_cost_share
                        if share.average < 1.0:
                            # Coupled-cost credit (Q7): the non-energy share of the measure
                            # (scaffolding, render, standard glazing) would have been spent
                            # anyway when the old element was due — it replaces the
                            # like-for-like credit so the two never double count.
                            non_energy_share = UncertainValue(
                                average=1.0 - share.average,
                                minimum=1.0 - share.maximum,
                                maximum=1.0 - share.minimum,
                            )
                            rate = self.investment_escalation_rate(costing.facts.asset_class)
                            credit = gross.multiply_band(non_energy_share).scale((1.0 + rate) ** credit_year)
                        elif like_for_like.maximum > 0:
                            old_rate = self.investment_escalation_rate(replaced.asset_class)
                            credit = like_for_like.scale((1.0 + old_rate) ** credit_year)
                        else:
                            credit = UncertainValue.exact(0.0)
                        if credit.maximum > 0:
                            timeline.add(
                                CashFlowEntry(
                                    year=credit_year,
                                    amount_in_euro=credit.as_revenue(),
                                    category=CostCategory.ANYWAY_COST_CREDIT,
                                    subject=subject,
                                    provenance_ids=costing.provenance_ids,
                                )
                            )
                            anyway_credit_total = anyway_credit_total + credit

            # --- replacements (§3.6 rule 2)
            replacement_years: List[int] = []
            replacement_year = costing.first_replacement_year if not costing.is_new_investment else int(
                round(costing.service_life_years)
            )
            while replacement_year < horizon:
                if replacement_year >= 1:
                    replacement_years.append(replacement_year)
                replacement_year += max(1, int(round(costing.service_life_years)))
            for repl_year in replacement_years:
                amount = gross.scale((1.0 + asset_rate) ** repl_year)
                replacement_flows_for_reserve.append((repl_year, amount))
                if include_investment:
                    timeline.add(
                        CashFlowEntry(
                            year=repl_year,
                            amount_in_euro=amount,
                            category=CostCategory.REPLACEMENT,
                            subject=subject,
                            provenance_ids=costing.provenance_ids,
                        )
                    )
                co2_result.embodied_co2_in_kg += costing.embodied_co2_kg
                co2_result.embodied_by_subject_in_kg[subject] = (
                    co2_result.embodied_by_subject_in_kg.get(subject, 0.0) + costing.embodied_co2_kg
                )

            # --- residual value at year T (§3.6 rule 3)
            if include_investment:
                last_install_year = replacement_years[-1] if replacement_years else (
                    0 if costing.is_new_investment else costing.first_replacement_year - int(
                        round(costing.service_life_years)
                    )
                )
                life = costing.service_life_years
                remaining_at_horizon = last_install_year + life - horizon
                if remaining_at_horizon > 0 and life > 0:
                    escalated_price = gross.scale((1.0 + asset_rate) ** max(0, last_install_year))
                    residual = escalated_price.scale(remaining_at_horizon / life)
                    timeline.add(
                        CashFlowEntry(
                            year=horizon,
                            amount_in_euro=residual.as_revenue(),
                            category=CostCategory.RESIDUAL_VALUE,
                            subject=subject,
                            provenance_ids=costing.provenance_ids,
                        )
                    )

            # --- maintenance & fixed operation (§3.6 rule 4)
            annual_maintenance = costing.maintenance_rate.multiply_band(gross)
            for year in range(1, horizon + 1):
                escalation = (1.0 + params.general_price_escalation_rate) ** (year - 1)
                amount = (annual_maintenance + costing.fixed_operation_cost).scale(escalation)
                if amount.maximum != 0:
                    timeline.add(
                        CashFlowEntry(
                            year=year,
                            amount_in_euro=amount,
                            category=CostCategory.MAINTENANCE
                            if annual_maintenance.maximum > 0
                            else CostCategory.FIXED_OPERATION,
                            subject=subject,
                            provenance_ids=costing.provenance_ids,
                        )
                    )

            # --- subsidies (§5; flat shim §10.1)
            if (
                include_investment
                and costing.is_new_investment
                and not macro
                and perspective.subsidy_mode.kind != SubsidyModeKind.NONE
            ):
                subsidy_total = self._add_subsidies(
                    timeline, costing, inputs, perspective, ledger, decisions
                )
                subsidies_total = subsidies_total + subsidy_total

        # --- energy costs per carrier (§3.6 rule 5, §8)
        self._add_energy_flows(timeline, inputs, perspective, ledger, co2_result, horizon, macro)

        # --- operating view: replacement reserve instead of investment categories (§4.2)
        if context == InstallationContext.OPERATING_ONLY and replacement_flows_for_reserve:
            discounted_band = UncertainValue.exact(0.0)
            for repl_year, amount in replacement_flows_for_reserve:
                discounted_band = discounted_band + amount.scale(params.discount_factor(repl_year))
            reserve = discounted_band.scale(params.annuity_factor())
            for year in range(1, horizon + 1):
                timeline.add(
                    CashFlowEntry(
                        year=year,
                        amount_in_euro=reserve,
                        category=CostCategory.REPLACEMENT_RESERVE,
                        subject="replacement reserve",
                    )
                )

        # --- macroeconomic CO2 damage (§4.5)
        if macro:
            damage_rate = params.co2_damage_cost_in_euro_per_ton / 1000.0  # EUR per kg
            for year in range(1, horizon + 1):
                emissions = (
                    co2_result.operational_co2_by_year_in_kg[year]
                    if year < len(co2_result.operational_co2_by_year_in_kg)
                    else 0.0
                )
                if emissions:
                    timeline.add(
                        CashFlowEntry(
                            year=year,
                            amount_in_euro=UncertainValue.exact(emissions * damage_rate),
                            category=CostCategory.CO2_DAMAGE,
                            subject="co2 damage",
                        )
                    )

        # --- financing (§4.4)
        if perspective.financing is not None and include_investment:
            self._apply_financing(timeline, perspective.financing, decisions)

        co2_result.total_co2_in_kg = co2_result.embodied_co2_in_kg + sum(co2_result.operational_co2_by_year_in_kg)
        basis_parts = {
            "modernization_cost": modernization_cost,
            "subsidies": subsidies_total,
            "avoided_maintenance": anyway_credit_total,
        }
        return timeline, decisions, co2_result, sunk_cost, basis_parts

    def _add_subsidies(
        self,
        timeline: CashFlowTimeline,
        costing: _DeviceCosting,
        inputs: EvaluationInputs,
        perspective: Perspective,
        ledger: ProvenanceLedger,
        decisions: List[SubsidyDecision],
    ) -> UncertainValue:
        """Adds subsidy flows for one measure; returns the total upfront support (positive)."""
        params = self.parameters
        total = UncertainValue.exact(0.0)
        if self.subsidy_catalog is None:
            # Phase-1 shim: the legacy flat percentage from the database entry (§10.1).
            share = costing.legacy_flat_subsidy_share
            if share > 0:
                amount = (costing.device_cost + costing.installation_cost).scale(share)
                timeline.add(
                    CashFlowEntry(
                        year=0,
                        amount_in_euro=amount.as_revenue(),
                        category=CostCategory.SUBSIDY,
                        subject=costing.subject,
                        subsidy_scheme_id="LEGACY_FLAT",
                        provenance_ids=costing.provenance_ids,
                    )
                )
                total = total + amount
            return total
        measure = MeasureForSubsidy(
            subject=costing.subject,
            facts=costing.facts,
            measure_kind="REPLACE" if costing.replaced_asset is not None else "INSTALL",
            cost_by_category={
                CostCategory.INVESTMENT: costing.device_cost + costing.installation_cost,
                CostCategory.PLANNING: costing.planning_cost,
                CostCategory.REMOVAL: costing.removal_cost_of_replaced,
            },
            vat_rate=costing.vat_rate,
        )
        for determinants in inputs.billing:
            if determinants.energy_sold_in_kwh:
                measure.annual_energy_sold_in_kwh[determinants.carrier] = (
                    determinants.energy_sold_in_kwh / max(inputs.simulated_period_fraction, 1e-9)
                )
        # Scheme validity follows the price basis year — the economic "today" — not the
        # (possibly historical) weather year of the simulation.
        decision = solve_cumulation(
            self.subsidy_catalog,
            measure,
            inputs.subsidy_context,
            self.price_basis_year(inputs),
            params.discount_factor,
        )
        decisions.append(decision)
        for award in decision.applied:
            if not perspective.subsidy_mode.admits(award.scheme_id):
                continue
            provenance = ledger.record(
                ParameterProvenance(
                    parameter=f"subsidy.{award.scheme_id}",
                    value=award.upfront_amount if award.upfront_amount.maximum else str(award.payout_kind.value),
                    origin=ParameterOrigin.DATABASE_ENTRY,
                    data_file=f"subsidy_catalog/{self.subsidy_catalog.country}.json#{award.scheme_id}",
                    source_ids=(f"inline:subsidy scheme {award.scheme_id}",),
                )
            )
            if award.payout_kind == PayoutKind.UPFRONT_GRANT and award.upfront_amount.maximum > 0:
                timeline.add(
                    CashFlowEntry(
                        year=0,
                        amount_in_euro=award.upfront_amount.as_revenue(),
                        category=CostCategory.SUBSIDY,
                        subject=costing.subject,
                        subsidy_scheme_id=award.scheme_id,
                        provenance_ids=(provenance,),
                    )
                )
                total = total + award.upfront_amount
            elif award.payout_kind == PayoutKind.TAX_CREDIT_SCHEDULE:
                for offset, amount in enumerate(award.schedule_amounts, start=1):
                    if offset > params.observation_period_in_years:
                        break
                    timeline.add(
                        CashFlowEntry(
                            year=offset,
                            amount_in_euro=amount.as_revenue(),
                            category=CostCategory.SUBSIDY,
                            subject=costing.subject,
                            subsidy_scheme_id=award.scheme_id,
                            provenance_ids=(provenance,),
                        )
                    )
                    total = total + amount.scale(params.discount_factor(offset))
            elif award.payout_kind == PayoutKind.OPERATIONAL and award.operational_carrier is not None:
                energy = measure.annual_energy_sold_in_kwh.get(award.operational_carrier, 0.0)
                for year in range(1, min(award.operational_duration_years, params.observation_period_in_years) + 1):
                    amount = UncertainValue.exact(award.operational_rate_per_kwh * energy)
                    timeline.add(
                        CashFlowEntry(
                            year=year,
                            amount_in_euro=amount.as_revenue(),
                            category=CostCategory.SUBSIDY,
                            subject=costing.subject,
                            subsidy_scheme_id=award.scheme_id,
                            provenance_ids=(provenance,),
                        )
                    )
        return total

    def _default_contract(self, carrier: EnergyCarrier, year: int) -> TariffContract:
        """Default flat contract from the §3.5 price entries (behaves as before, §8.2)."""
        entry = self.database.get_energy_price(carrier, year, self.parameters.country)
        contract = TariffContract.default_from_price_entry(entry, self.parameters.country)
        if carrier == EnergyCarrier.ELECTRICITY and self.database.has_energy_price(
            EnergyCarrier.ELECTRICITY_FEED_IN, self.parameters.country
        ):
            feed_in_entry = self.database.get_energy_price(EnergyCarrier.ELECTRICITY_FEED_IN, year, self.parameters.country)
            from hisim.economics.tariffs import FeedIn, FeedInKind  # local import to avoid cycle noise

            contract.feed_in = FeedIn(
                kind=FeedInKind.FIXED_TARIFF,
                rate_in_euro_per_kwh=feed_in_entry.working_price_in_euro_per_kwh,
                duration_in_years=20,
            )
        return contract

    def _add_energy_flows(
        self,
        timeline: CashFlowTimeline,
        inputs: EvaluationInputs,
        perspective: Perspective,
        ledger: ProvenanceLedger,
        co2_result: LifecycleCo2Result,
        horizon: int,
        macro: bool,
    ) -> None:
        """Adds per-carrier energy cost flows projected over the horizon (§3.6 rule 5, §8.5)."""
        params = self.parameters
        year = self.price_basis_year(inputs)
        fraction = inputs.simulated_period_fraction
        if fraction <= 0:
            raise ValueError("simulated_period_fraction must be > 0.")
        if fraction < 0.999:
            log.warning(
                f"Simulated period covers {fraction:.2%} of a year; energy flows are annualized "
                "by linear extrapolation (§3.6)."
            )
        for determinants in inputs.billing:
            carrier = determinants.carrier
            annualized = BillingDeterminants(
                carrier=carrier,
                energy_bought_in_kwh=determinants.energy_bought_in_kwh / fraction,
                energy_sold_in_kwh=determinants.energy_sold_in_kwh / fraction,
                energy_bought_per_band_in_kwh={
                    band: energy / fraction for band, energy in determinants.energy_bought_per_band_in_kwh.items()
                },
                cost_integrated_in_euro=(
                    determinants.cost_integrated_in_euro / fraction
                    if determinants.cost_integrated_in_euro is not None
                    else None
                ),
                revenue_integrated_in_euro=(
                    determinants.revenue_integrated_in_euro / fraction
                    if determinants.revenue_integrated_in_euro is not None
                    else None
                ),
                peak_per_billing_period_in_kw=determinants.peak_per_billing_period_in_kw,
                annual_peak_in_kw=determinants.annual_peak_in_kw,
                mean_spot_price_in_euro_per_kwh=determinants.mean_spot_price_in_euro_per_kwh,
            )
            contract = inputs.tariff_contracts.get(carrier) or self._default_contract(carrier, year)
            price_entry = self.database.get_energy_price(carrier, year, self.parameters.country)
            price_provenance = self.database.provenance_for_price(price_entry, ledger, "working_price_in_euro_per_kwh")
            energy_provenance = ledger.record(
                ParameterProvenance(
                    parameter=f"simulation.{carrier.value}.energy_bought",
                    value=annualized.energy_bought_in_kwh,
                    origin=ParameterOrigin.SIMULATION_OUTPUT,
                    detail=f"annualized from simulated fraction {fraction:.4f}",
                )
            )
            provenance_ids = (price_provenance, energy_provenance)
            bill = apply_tariff(annualized, contract)

            if macro:
                # Strip taxes/levies and VAT from the working price (§4.5). Migrated AS_LEGACY
                # entries carry tax_and_levy_share=0, so this is approximate for them.
                strip = 1.0 - price_entry.tax_and_levy_share
                working = bill.by_category.get(CostCategory.ENERGY_WORKING)
                if working is not None:
                    bill.by_category[CostCategory.ENERGY_WORKING] = working.scale(strip)

            carrier_rate = self.carrier_escalation_rate(carrier)
            spread_rate = params.spread_escalation_rate if params.spread_escalation_rate is not None else carrier_rate
            grid_rate = (
                params.grid_fee_escalation_rate
                if params.grid_fee_escalation_rate is not None
                else params.general_price_escalation_rate
            )
            co2_path = self.database.get_co2_price_path(params.country, params.co2_price_scenario)
            emission_factor = price_entry.emission_factor_in_kg_per_kwh
            annual_emissions = annualized.energy_bought_in_kwh * emission_factor

            working_band = bill.by_category.get(CostCategory.ENERGY_WORKING, UncertainValue.exact(0.0))
            flexibility = max(0.0, bill.flexibility_value_in_euro)
            volume_band = working_band + UncertainValue.exact(flexibility)

            for projection_year in range(1, horizon + 1):
                escalation = (1.0 + carrier_rate) ** (projection_year - 1)
                working = volume_band.scale(escalation)
                if flexibility:
                    working = working - UncertainValue.exact(flexibility).scale(
                        (1.0 + spread_rate) ** (projection_year - 1)
                    )
                if working.maximum != 0 or working.minimum != 0:
                    timeline.add(
                        CashFlowEntry(
                            year=projection_year,
                            amount_in_euro=working,
                            category=CostCategory.ENERGY_WORKING,
                            subject=carrier.value,
                            subject_kind=SubjectKind.CARRIER,
                            provenance_ids=provenance_ids,
                        )
                    )
                standing = bill.by_category.get(CostCategory.ENERGY_STANDING)
                if standing is not None and (standing.maximum or standing.minimum):
                    timeline.add(
                        CashFlowEntry(
                            year=projection_year,
                            amount_in_euro=standing.scale(
                                (1.0 + params.general_price_escalation_rate) ** (projection_year - 1)
                            ),
                            category=CostCategory.ENERGY_STANDING,
                            subject=carrier.value,
                            subject_kind=SubjectKind.CARRIER,
                            provenance_ids=provenance_ids,
                        )
                    )
                capacity = bill.by_category.get(CostCategory.ENERGY_CAPACITY_CHARGE)
                if capacity is not None and capacity.maximum:
                    timeline.add(
                        CashFlowEntry(
                            year=projection_year,
                            amount_in_euro=capacity.scale((1.0 + grid_rate) ** (projection_year - 1)),
                            category=CostCategory.ENERGY_CAPACITY_CHARGE,
                            subject=carrier.value,
                            subject_kind=SubjectKind.CARRIER,
                            provenance_ids=provenance_ids,
                        )
                    )
                feed_in = bill.by_category.get(CostCategory.FEED_IN_REVENUE)
                if feed_in is not None and not macro and feed_in.minimum != 0:
                    # EEG-style fixed tariffs stay nominal for their duration (§8.5, spec Q10).
                    within_duration = projection_year <= contract.feed_in.duration_in_years
                    feed_escalation = (
                        1.0 if within_duration else (1.0 + params.feed_in_escalation_rate) ** (projection_year - 1)
                    )
                    timeline.add(
                        CashFlowEntry(
                            year=projection_year,
                            amount_in_euro=feed_in.scale(feed_escalation),
                            category=CostCategory.FEED_IN_REVENUE,
                            subject=EnergyCarrier.ELECTRICITY_FEED_IN.value
                            if carrier == EnergyCarrier.ELECTRICITY
                            else carrier.value,
                            subject_kind=SubjectKind.CARRIER,
                            provenance_ids=provenance_ids,
                        )
                    )
                # Explicit CO2 price component (§3.5): exposure share of emissions.
                if not macro and co2_path is not None and price_entry.co2_price_exposure > 0 and annual_emissions:
                    # The CO2 path is anchored on the price basis year (the economic "today").
                    co2_price = co2_path.price(year + projection_year - 1)
                    amount = annual_emissions * price_entry.co2_price_exposure * co2_price / 1000.0
                    if amount:
                        timeline.add(
                            CashFlowEntry(
                                year=projection_year,
                                amount_in_euro=UncertainValue.exact(amount),
                                category=CostCategory.ENERGY_CO2_PRICE,
                                subject=carrier.value,
                                subject_kind=SubjectKind.CARRIER,
                                provenance_ids=provenance_ids,
                            )
                        )
                co2_result.operational_co2_by_year_in_kg[projection_year] += annual_emissions
            co2_result.operational_co2_by_carrier_in_kg[carrier.value] = annual_emissions * horizon

    def _apply_financing(
        self, timeline: CashFlowTimeline, plan: FinancingPlan, decisions: List[SubsidyDecision]
    ) -> None:
        """Replaces (a share of) the year-0 outflow by loan flows (§4.4)."""
        loan_plan = plan
        for decision in decisions:
            for award in decision.applied:
                if award.payout_kind == PayoutKind.LOAN_TERMS and award.scheme_id == plan.subsidized_by_scheme_id:
                    loan_plan = FinancingPlan(
                        financed_share=plan.financed_share,
                        nominal_interest_rate=award.loan_interest_rate or plan.nominal_interest_rate,
                        term_in_years=award.loan_term_in_years or plan.term_in_years,
                        type=plan.type,
                        subsidized_by_scheme_id=plan.subsidized_by_scheme_id,
                        repayment_grant_share=award.loan_repayment_grant_share,
                    )
        year0_net = UncertainValue.exact(0.0)
        for entry in timeline.entries:
            if entry.year == 0 and entry.category in (
                CostCategory.INVESTMENT,
                CostCategory.PLANNING,
                CostCategory.REMOVAL,
                CostCategory.SUBSIDY,
            ):
                year0_net = year0_net + entry.amount_in_euro
        if year0_net.maximum <= 0:
            return
        principal = year0_net.scale(loan_plan.financed_share)
        disbursement, schedule = loan_flows(loan_plan, principal)
        timeline.add(
            CashFlowEntry(
                year=0,
                amount_in_euro=disbursement.as_revenue(),
                category=CostCategory.LOAN_DISBURSEMENT,
                subject="financing",
            )
        )
        if loan_plan.repayment_grant_share > 0:
            grant = principal.scale(loan_plan.repayment_grant_share)
            timeline.add(
                CashFlowEntry(
                    year=0,
                    amount_in_euro=grant.as_revenue(),
                    category=CostCategory.SUBSIDY,
                    subject="financing",
                    subsidy_scheme_id=loan_plan.subsidized_by_scheme_id,
                )
            )
        for year, interest, repayment in schedule:
            if year > self.parameters.observation_period_in_years:
                break
            if interest.maximum:
                timeline.add(
                    CashFlowEntry(
                        year=year, amount_in_euro=interest, category=CostCategory.LOAN_INTEREST, subject="financing"
                    )
                )
            if repayment.maximum:
                timeline.add(
                    CashFlowEntry(
                        year=year, amount_in_euro=repayment, category=CostCategory.LOAN_PRINCIPAL, subject="financing"
                    )
                )

    # ------------------------------------------------------------------ evaluation (§3.7)

    def evaluate(
        self,
        inputs: EvaluationInputs,
        perspective: Perspective,
        ledger: Optional[ProvenanceLedger] = None,
    ) -> LifecycleCostResult:
        """Evaluates one perspective: timeline -> allocation -> discounting -> result."""
        params = self.parameters
        ledger = ledger or ProvenanceLedger()
        timeline, decisions, co2_result, sunk_cost, basis_parts = self.build_timeline(inputs, perspective, ledger)

        # Actor allocation (§6).
        if perspective.actor_scope != ActorScope.SYSTEM:
            rented = perspective.actor_scope in (ActorScope.LANDLORD, ActorScope.TENANT)
            ruleset = get_ruleset(rented, params.country)
            allocation_context = AllocationContext(
                horizon_years=params.observation_period_in_years,
                building_specific_emissions_in_kg_per_m2_a=inputs.building_specific_emissions_in_kg_per_m2_a,
                heated_floor_area_in_m2=inputs.heated_floor_area_in_m2,
                living_area_in_m2=inputs.living_area_in_m2,
                current_cold_rent_in_euro_per_m2_month=inputs.current_cold_rent_in_euro_per_m2_month,
                modernization_cost_in_euro=basis_parts["modernization_cost"],
                subsidies_received_in_euro=basis_parts["subsidies"],
                avoided_maintenance_in_euro=basis_parts["avoided_maintenance"],
            )
            timeline = ruleset.allocate(timeline, allocation_context)

        interest = params.interest_rate
        horizon = params.observation_period_in_years
        npv_by_payer = {
            payer: value for payer, value in timeline.npv_by(interest, lambda entry: entry.payer).items()
        }

        # The perspective reports the scope actor's flows (SYSTEM = everything).
        scope_actor = perspective.actor_scope.to_actor()
        if perspective.actor_scope == ActorScope.SYSTEM:
            scoped = timeline
        else:
            scoped = timeline.filtered(lambda entry: entry.payer == scope_actor)

        total_npv = scoped.npv(interest)
        annuity = params.annuity_factor()
        npv_by_category = {
            category: value for category, value in scoped.npv_by(interest, lambda entry: entry.category).items()
        }
        npv_by_component = {
            subject: value for subject, value in scoped.npv_by(interest, lambda entry: entry.subject).items()
        }
        annual_series = scoped.nominal_annual_series(horizon)
        monthly_year1 = annual_series[1].scale(1.0 / 12.0) if len(annual_series) > 1 else None

        levelized = None
        if inputs.annual_heat_demand_in_kwh:
            levelized = total_npv.scale(annuity / inputs.annual_heat_demand_in_kwh)

        breakdowns = self._build_breakdowns(scoped, inputs, co2_result, interest, annuity, horizon)

        return LifecycleCostResult(
            perspective_id=perspective.id,
            parameters=params,
            total_npv_in_euro=total_npv,
            equivalent_annual_cost_in_euro=total_npv.scale(annuity),
            npv_by_category=npv_by_category,
            npv_by_component=npv_by_component,
            npv_by_payer=npv_by_payer,
            component_breakdowns=breakdowns,
            annual_cost_series_nominal_in_euro=annual_series,
            monthly_cost_year1_in_euro=monthly_year1,
            levelized_cost_of_heat_in_euro_per_kwh=levelized,
            timeline=timeline,
            lifecycle_co2_result=co2_result,
            subsidy_decisions=decisions,
            sunk_cost_written_off_in_euro=sunk_cost,
            ledger=ledger,
            source_resolver={
                source_id: entry.to_resolved() for source_id, entry in self.database.sources.entries.items()
            },
            scope_payer=scope_actor,
        )

    def _build_breakdowns(
        self,
        scoped: CashFlowTimeline,
        inputs: EvaluationInputs,
        co2_result: LifecycleCo2Result,
        interest: float,
        annuity: float,
        horizon: int,
    ) -> Dict[str, ComponentCostBreakdown]:
        """The per-subject pivot of the canonical timeline (§7.4 rule 1)."""
        facts_by_subject = {subject_facts.subject: subject_facts.facts for subject_facts in inputs.cost_facts}
        breakdowns: Dict[str, ComponentCostBreakdown] = {}
        for subject in scoped.subjects():
            subject_timeline = scoped.filtered(lambda entry, _subject=subject: entry.subject == _subject)
            npv_by_category = {
                category: value
                for category, value in subject_timeline.npv_by(interest, lambda entry: entry.category).items()
            }
            total = subject_timeline.npv(interest)
            facts = facts_by_subject.get(subject)
            kind = SubjectKind.COMPONENT
            for entry in subject_timeline.entries:
                kind = entry.subject_kind
                break
            investment_gross = UncertainValue.sum(
                entry.amount_in_euro
                for entry in subject_timeline.entries
                if entry.year == 0
                and entry.category in (CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL)
            )
            subsidies = UncertainValue.sum(
                entry.amount_in_euro.as_revenue()
                for entry in subject_timeline.entries
                if entry.category == CostCategory.SUBSIDY
            )
            operational_co2 = (
                co2_result.operational_co2_by_carrier_in_kg.get(subject, 0.0) if kind == SubjectKind.CARRIER else 0.0
            )
            breakdowns[subject] = ComponentCostBreakdown(
                subject=subject,
                subject_kind=kind,
                asset_class=facts.asset_class if facts else None,
                kpi_tag=facts.kpi_tag if facts else None,
                npv_by_category=npv_by_category,
                total_npv_in_euro=total,
                equivalent_annual_cost_in_euro=total.scale(annuity),
                investment_gross_in_euro=investment_gross,
                subsidies_in_euro=subsidies,
                annual_cost_series_nominal_in_euro=subject_timeline.nominal_annual_series(horizon),
                lifecycle_co2_in_kg=co2_result.embodied_by_subject_in_kg.get(subject, 0.0) + operational_co2,
            )
        return breakdowns

    def evaluate_matrix(
        self,
        inputs: EvaluationInputs,
        perspectives: List[Perspective],
    ) -> EvaluationMatrix:
        """Evaluates a set of perspectives against the same simulation results (§4)."""
        matrix = EvaluationMatrix()
        for perspective in perspectives:
            matrix.results[perspective.id] = self.evaluate(inputs, perspective)
        return matrix
