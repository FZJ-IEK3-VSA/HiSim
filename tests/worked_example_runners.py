"""Entry points that drive each worked-example group through the engine (cost-spec-v2 §3.5).

One `_<group>_values` function per workbook group — financing, discounting, tariffs, subsidies,
modernization levy, end-to-end — each mapping an example's inputs onto the engine surface under
test and returning the computed values by label. Moved out of `test_worked_examples.py` (PR-3
review, 500-line rule): these are the library the parametrized driver dispatches into, not
tests themselves. The rule for writing one is unchanged — a runner exercises the *engine's own*
entry points and never re-implements the arithmetic the workbook cross-checks.
"""

import re
from typing import Any, Dict
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.financing import FinancingPlan, LoanType, loan_flows
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.subsidies import (
    Condition,
    EligibleCostSpec,
    MeasureForSubsidy,
    PayoutKind,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
    SubsidyScheme,
    parse_benefit,
    solve_cumulation,
)
from hisim.economics.tariffs import (
    CapacityCharge,
    CapacityChargeKind,
    ControllabilityDiscount,
    FeedIn,
    FeedInKind,
    SupplyKind,
    TariffContract,
    TariffSupply,
    TimeOfUseBand,
    apply_tariff,
)
from hisim.economics.timeline import CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

#: no device entries and no escalation defaults, so no shipped price can leak into an example.
SYNTHETIC_COUNTRY = "XX"
#: The price basis year every example is evaluated at.
EXAMPLE_YEAR = 2024


def _financing_values(inputs: Dict[str, Any]) -> Dict[str, float]:
    """Runs `loan_flows` and exposes the loan schedule under the worked-example labels (§4.4).

    Flattens the year-by-year schedule into the `*_year_<n>_in_euro` labels the workbooks use, so
    an example can assert intermediates — year-1 interest, remaining debt at year 5 — and not only
    the annuity. Two conveniences worth knowing: a workbook may state either the principal
    directly or a net investment plus a financed share (the evaluator finances a share of the
    year-0 net investment; the loan itself only ever sees the resulting principal), and
    `annuity_via_pmt_in_euro` is the same engine number under a second label, so a workbook cell
    computed with Excel's `PMT` is compared against the engine rather than against itself.
    """
    plan = FinancingPlan(
        financed_share=float(inputs.get("financed_share", 1.0)),
        nominal_interest_rate=float(inputs["interest_rate"]),
        term_in_years=int(inputs["duration_in_years"]),
        type=LoanType(str(inputs.get("loan_type", "ANNUITY"))),
    )
    if "principal_in_euro" in inputs:
        principal = float(inputs["principal_in_euro"])
    else:
        # The evaluator finances a share of the year-0 net investment (§4.4); the loan itself
        # only ever sees the resulting principal.
        principal = float(inputs["net_investment_in_euro"]) * plan.financed_share
    disbursement, schedule = loan_flows(plan, UncertainValue.exact(principal))

    values: Dict[str, float] = {
        "principal_in_euro": principal,
        "disbursement_in_euro": disbursement.best_estimate,
    }
    remaining = principal
    total_interest = 0.0
    total_repayment = 0.0
    for year, interest, repayment in schedule:
        values[f"interest_year_{year}_in_euro"] = interest.best_estimate
        values[f"principal_repayment_year_{year}_in_euro"] = repayment.best_estimate
        remaining -= repayment.best_estimate
        values[f"remaining_debt_year_{year}_in_euro"] = remaining
        total_interest += interest.best_estimate
        total_repayment += repayment.best_estimate
    annuity = schedule[0][1].best_estimate + schedule[0][2].best_estimate
    values["annuity_in_euro"] = annuity
    values["annuity_via_pmt_in_euro"] = annuity
    values["total_interest_in_euro"] = total_interest
    values["total_repayment_in_euro"] = total_repayment
    values["total_payments_in_euro"] = total_interest + total_repayment
    return values


def _discounting_values(inputs: Dict[str, Any]) -> Dict[str, float]:
    """Discounts a hand-written cash-flow series with the engine's own timeline and factors.

    Reads `cash_flow_year_<n>_in_euro` labels out of the workbook's input table, feeds them into a
    real `CashFlowTimeline` and reports the per-year discount factors, the per-year discounted
    amounts, the NPV, the annuity factor and the EAC. Everything is routed through
    `EconomicParameters.discount_factor` and `CashFlowTimeline.npv` — the canonical helpers of
    W4.3 — so the comparison is against Excel's `NPV`/`PMT`, not against a formula retyped here.
    Like `_financing_values` it publishes the annuity factor twice, under a plain and a
    `_via_pmt` label, so a workbook may derive it either way.
    """
    rate = float(inputs["interest_rate"])
    horizon = int(inputs["observation_period_in_years"])
    parameters = EconomicParameters(observation_period_in_years=horizon, interest_rate=rate)
    # The workbook gives net figures per year, of either sign, all booked under one neutral
    # category — so this synthetic timeline opts out of the §3.9 sign convention (W3.7).
    timeline = CashFlowTimeline(validate=False)
    for label, value in inputs.items():
        match = re.match(r"^cash_flow_year_(\d+)_in_euro$", label)
        if match is None:
            continue
        timeline.add(
            CashFlowEntry(
                year=int(match.group(1)),
                amount_in_euro=UncertainValue.exact(float(value)),
                # The series is given as net figures per year, so one neutral category carries
                # them all; discounting is category-blind (§3.6).
                category=CostCategory.INVESTMENT,
                subject="worked example",
            )
        )
    annuity_factor = parameters.annuity_factor()
    net_present_value = timeline.npv(rate).best_estimate
    values = {
        "annuity_factor": annuity_factor,
        "annuity_factor_via_pmt": annuity_factor,
        "npv_in_euro": net_present_value,
        "eac_in_euro": net_present_value * annuity_factor,
    }
    for year in range(0, horizon + 1):
        values[f"discount_factor_year_{year}"] = parameters.discount_factor(year)
        year_only = timeline.filtered(lambda entry, wanted=year: entry.year == wanted)
        values[f"discounted_cash_flow_year_{year}_in_euro"] = year_only.npv(rate).best_estimate
    return values


def _tariff_contract(inputs: Dict[str, Any]) -> TariffContract:
    """Builds the contract an example describes through its flat input labels (§8.2).

    Workbooks are two-column tables, so a nested contract has to be expressed as flat labels:
    `supply_kind`, `working_price_in_euro_per_kwh`, `band_<name>_price_in_euro_per_kwh` for
    time-of-use bands, plus the capacity-charge, feed-in and controllability keys. Every key is
    optional and defaults to the neutral value (0.0 / NONE), which is what lets one example
    describe a bare flat tariff and the next a fully loaded contract without a schema per case.
    """
    bands = []
    for label, value in sorted(inputs.items()):
        match = re.match(r"^band_([a-z0-9]+)_price_in_euro_per_kwh$", label)
        if match is not None:
            bands.append(
                TimeOfUseBand(name=match.group(1), price_in_euro_per_kwh=UncertainValue.exact(float(value)))
            )
    supply = TariffSupply(
        kind=SupplyKind(str(inputs.get("supply_kind", "FLAT"))),
        working_price_in_euro_per_kwh=UncertainValue.exact(float(inputs.get("working_price_in_euro_per_kwh", 0.0))),
        bands=bands,
        spot_series="worked_example" if inputs.get("supply_kind") == "DYNAMIC" else None,
        markup_in_euro_per_kwh=UncertainValue.exact(float(inputs.get("markup_in_euro_per_kwh", 0.0))),
        grid_fee_in_euro_per_kwh=UncertainValue.exact(float(inputs.get("grid_fee_in_euro_per_kwh", 0.0))),
        taxes_and_levies_in_euro_per_kwh=UncertainValue.exact(
            float(inputs.get("taxes_and_levies_in_euro_per_kwh", 0.0))
        ),
    )
    return TariffContract(
        id="WORKED_EXAMPLE",
        carrier=EnergyCarrier.ELECTRICITY,
        country=SYNTHETIC_COUNTRY,
        region=None,
        valid_from_year=EXAMPLE_YEAR,
        supply=supply,
        standing_charge_in_euro_per_year=UncertainValue.exact(
            float(inputs.get("standing_charge_in_euro_per_year", 0.0))
        ),
        capacity_charge=CapacityCharge(
            kind=CapacityChargeKind(str(inputs.get("capacity_charge_kind", "NONE"))),
            price_in_euro_per_kw=UncertainValue.exact(float(inputs.get("capacity_price_in_euro_per_kw", 0.0))),
        ),
        feed_in=FeedIn(
            kind=FeedInKind(str(inputs.get("feed_in_kind", "NONE"))),
            rate_in_euro_per_kwh=UncertainValue.exact(float(inputs.get("feed_in_rate_in_euro_per_kwh", 0.0))),
        ),
        controllability_discount=ControllabilityDiscount(
            kind=str(inputs.get("controllability_kind", "NONE")),
            annual_amount_in_euro=UncertainValue.exact(float(inputs.get("controllability_amount_in_euro", 0.0))),
            grid_fee_reduction_share=float(inputs.get("grid_fee_reduction_share", 0.0)),
        ),
        source_ids=("inline:worked example",),
    )


def _tariff_values(inputs: Dict[str, Any]) -> Dict[str, float]:
    """Bills one year with `apply_tariff` and exposes the bill under the example labels (§8.4).

    Assembles the `BillingDeterminants` from the example's quantities — total kWh, per-band kWh,
    sold kWh, the integrated cost of a dynamic year, the billing-period peak — and publishes the
    resulting bill per cost category plus the §8.5 decomposition (mean energy price, volume
    effect, flexibility value). Categories absent from a bill are reported as 0.0 rather than
    omitted, so a workbook can assert "no capacity charge" as a value instead of by silence.
    `apply_tariff` is a pure *year-1* billing engine; escalation and the multi-year projection
    belong to the evaluator and are covered by the end-to-end group instead.
    """
    contract = _tariff_contract(inputs)
    per_band = {}
    for label, value in inputs.items():
        match = re.match(r"^band_([a-z0-9]+)_energy_in_kwh$", label)
        if match is not None:
            per_band[match.group(1)] = float(value)
    determinants = BillingDeterminants(
        carrier=EnergyCarrier.ELECTRICITY,
        energy_bought_in_kwh=float(inputs.get("energy_bought_in_kwh", 0.0)),
        energy_sold_in_kwh=float(inputs.get("energy_sold_in_kwh", 0.0)),
        energy_bought_per_band_in_kwh=per_band,
        cost_integrated_in_euro=(
            float(inputs["cost_integrated_in_euro"]) if "cost_integrated_in_euro" in inputs else None
        ),
        annual_peak_in_kw=float(inputs.get("annual_peak_in_kw", 0.0)),
        mean_spot_price_in_euro_per_kwh=(
            float(inputs["mean_spot_price_in_euro_per_kwh"]) if "mean_spot_price_in_euro_per_kwh" in inputs else None
        ),
    )
    bill = apply_tariff(determinants, contract)
    zero = UncertainValue.exact(0.0)
    return {
        "energy_working_in_euro": bill.by_category.get(CostCategory.ENERGY_WORKING, zero).best_estimate,
        "energy_standing_in_euro": bill.by_category.get(CostCategory.ENERGY_STANDING, zero).best_estimate,
        "energy_capacity_charge_in_euro": bill.by_category.get(CostCategory.ENERGY_CAPACITY_CHARGE, zero).best_estimate,
        "feed_in_revenue_in_euro": bill.by_category.get(CostCategory.FEED_IN_REVENUE, zero).best_estimate,
        "total_bill_in_euro": bill.total().best_estimate,
        "mean_energy_price_in_euro_per_kwh": bill.mean_energy_price_in_euro_per_kwh,
        "volume_effect_in_euro": bill.volume_effect_in_euro,
        "flexibility_value_in_euro": bill.flexibility_value_in_euro,
        "marginal_price_components_in_euro_per_kwh": contract.marginal_purchase_price_components().best_estimate,
    }


def _subsidy_scheme(letter: str, inputs: Dict[str, Any]) -> SubsidyScheme:
    """Builds one synthetic scheme from the `scheme_<letter>_*` input labels (§5.2).

    Schemes are addressed by a single letter (`scheme_a_rate`, `scheme_b_payout_kind`, …) so a
    workbook can describe a cumulation problem of two or three schemes in a flat table; the
    eligibility condition is always the empty `all` tree, i.e. satisfied by every context, because
    these examples test the *arithmetic* of stacking and capping, not eligibility. The two
    eligible-cost caps are read positionally as (first dwelling unit, each further unit).
    """
    prefix = f"scheme_{letter}_"
    scheme_id = f"SCHEME_{letter.upper()}"
    # The benefit payload is built through the catalog parser (W2.2), so a worked example
    # exercises exactly the typing the shipped catalogs go through.
    raw_benefit: Dict[str, Any] = {"kind": str(inputs[f"{prefix}benefit_kind"])}
    if f"{prefix}rate" in inputs:
        raw_benefit["rate"] = float(inputs[f"{prefix}rate"])
    if f"{prefix}amount_in_euro" in inputs:
        raw_benefit["amount"] = float(inputs[f"{prefix}amount_in_euro"])
    if f"{prefix}years" in inputs:
        raw_benefit["years"] = int(inputs[f"{prefix}years"])
    benefit_kind, benefit = parse_benefit(raw_benefit, scheme_id)
    caps = []
    if f"{prefix}cap_first_unit_in_euro" in inputs:
        caps.append(float(inputs[f"{prefix}cap_first_unit_in_euro"]))
    if f"{prefix}cap_further_unit_in_euro" in inputs:
        caps.append(float(inputs[f"{prefix}cap_further_unit_in_euro"]))
    return SubsidyScheme(
        id=scheme_id,
        country=SYNTHETIC_COUNTRY,
        region=None,
        valid_from="1900-01-01",
        valid_to=None,
        legal_basis="synthetic worked-example scheme (cost-spec-v2 §3)",
        url="https://example.invalid/worked-example",
        asset_classes=[ComponentType.HEAT_PUMP],
        measure_kinds=["INSTALL", "REPLACE"],
        eligibility=Condition(kind="all"),  # no children: satisfied by every context
        benefit_kind=benefit_kind,
        benefit=benefit,
        eligible_cost=EligibleCostSpec(cap_per_dwelling_unit_in_euro=caps),
        cumulation_group=(str(inputs["cumulation_group"]) if "cumulation_group" in inputs else None),
        combined_rate_cap=(float(inputs["combined_rate_cap"]) if "combined_rate_cap" in inputs else None),
        excludes=([str(inputs[f"{prefix}excludes"])] if f"{prefix}excludes" in inputs else []),
        payout_kind=PayoutKind(str(inputs[f"{prefix}payout_kind"])),
    )


def _subsidy_values(inputs: Dict[str, Any]) -> Dict[str, float]:
    """Solves the cumulation problem of a tiny in-memory catalog (§5.4).

    Discovers how many schemes the example declares from its `scheme_<letter>_` labels, builds a
    catalog out of them and runs the real solver, then publishes the per-scheme award, the upfront
    total, any tax-credit instalments, the support NPV and the effective support share. The
    catalog is in-memory and the country synthetic, so the shipped BEG/§35c definitions cannot
    influence a result; the cap that *is* asserted comes from the example's own numbers.
    Note `support_npv_in_euro` is the one figure assembled here rather than by the engine — the
    upfront awards plus each instalment discounted with the engine's own discount factor.
    """
    letters = sorted({match.group(1) for match in (re.match(r"^scheme_([a-z])_", key) for key in inputs) if match})
    schemes = [_subsidy_scheme(letter, inputs) for letter in letters]
    catalog = SubsidyCatalog(
        schemes=schemes,
        questions={},
        snapshot_date=None,
        overall_cap_share=None,
        base_path="",
        country=SYNTHETIC_COUNTRY,
    )
    measure_cost = float(inputs["measure_cost_in_euro"])
    facts = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=float(inputs.get("measure_size_in_kw", 10.0)),
        size_unit=Units.KILOWATT,
    )
    measure = MeasureForSubsidy(
        subject="measure",
        facts=facts,
        measure_kind="INSTALL",
        cost_by_category={CostCategory.INVESTMENT: UncertainValue.exact(measure_cost)},
    )
    dwelling_units = int(inputs.get("dwelling_units", 1))
    context = SubsidyContext(building=SubsidyBuildingContext(dwelling_units=dwelling_units))
    parameters = EconomicParameters(interest_rate=float(inputs["interest_rate"]))
    decision = solve_cumulation(catalog, measure, context, EXAMPLE_YEAR, parameters.discount_factor)

    awards = {award.scheme_id: award for award in decision.applied}
    values: Dict[str, float] = {}
    for letter in letters:
        award = awards.get(f"SCHEME_{letter.upper()}")
        values[f"scheme_{letter}_award_in_euro"] = award.upfront_amount.best_estimate if award is not None else 0.0
    total_upfront = sum(award.upfront_amount.best_estimate for award in decision.applied)
    schedule_total = 0.0
    support_npv = total_upfront
    for award in decision.applied:
        for offset, amount in enumerate(award.schedule_amounts, start=1):
            values[f"tax_credit_year_{offset}_in_euro"] = amount.best_estimate
            schedule_total += amount.best_estimate
            support_npv += amount.best_estimate * parameters.discount_factor(offset)
    values["total_upfront_award_in_euro"] = total_upfront
    values["tax_credit_total_in_euro"] = schedule_total
    values["support_npv_in_euro"] = support_npv
    values["effective_support_share"] = total_upfront / measure_cost if measure_cost else 0.0
    cap = schemes[0].eligible_cost.cap_for_units(dwelling_units)
    if cap is not None:
        values["eligible_cap_in_euro"] = cap
    return values


def _levy_scheme(scheme_id: str, asset_class: ComponentType, rate: float) -> SubsidyScheme:
    """A synthetic upfront grant of `rate` on one asset class, for the levy package example.

    The §559 Abs. 2 subsidy deduction is half of what the levy example is about, so the package
    needs support that is *attributable to one measure* — which is exactly what a per-asset-class
    scheme produces. Eligibility is the empty `all` tree (satisfied by every context), because the
    example asserts the levy arithmetic, not who qualifies.
    """
    benefit_kind, benefit = parse_benefit({"kind": "SHARE_OF_ELIGIBLE_COST", "rate": rate}, scheme_id)
    return SubsidyScheme(
        id=scheme_id,
        country="DE",
        region=None,
        valid_from="1900-01-01",
        valid_to=None,
        legal_basis="synthetic worked-example scheme (cost-spec-v2 §3)",
        url="https://example.invalid/worked-example",
        asset_classes=[asset_class],
        measure_kinds=["INSTALL", "REPLACE"],
        eligibility=Condition(kind="all"),
        benefit_kind=benefit_kind,
        benefit=benefit,
        eligible_cost=EligibleCostSpec(),
        cumulation_group=None,
        combined_rate_cap=None,
        excludes=[],
        payout_kind=PayoutKind.UPFRONT_GRANT,
    )


def _modernization_levy_values(inputs: Dict[str, Any], database: CostDatabase) -> Dict[str, float]:
    """Evaluates a mixed retrofit package under the landlord/tenant split (§6.4, D27).

    The second end-to-end entry point, and the only one that reaches the allocation layer: two
    subjects (a heat pump and a wall insulation, sized in kW and m²), each with its own upfront
    grant, are evaluated for a rented German building with a declared living area and cold rent.
    Published are the engine's booked levy — the tenant leg of the minted transfer pair — its
    present value and both payers' NPVs, plus the paragraph split, which the timeline deliberately
    does not carry separately (see `actors.DE2024Ruleset.modernization_levy_entries`) and which is
    therefore read from the ruleset's `compute_modernization_levy` on the *engine's own* levy
    basis, obtained from the public `build_timeline`.

    **Why this runner uses `country="DE"`** while every other end-to-end example prices against
    the synthetic `XX` country (D17): §559e is German law, and the ruleset is selected by country.
    No shipped *price* can leak in even so — every device figure is overridden and the package has
    no energy bill at all — but the statutory percentages do come from the shipped
    `allocation_DE_2024.json`. The workbook therefore declares them as inputs and derives its
    expected values from those copies, which makes a change to the shipped legal parameters fail
    this example loudly instead of silently re-baselining it.
    """
    from hisim.economics.actors import AllocationContext, DE2024Ruleset, get_ruleset
    from hisim.economics.perspectives import ActorScope
    from hisim.economics.provenance import ProvenanceLedger
    from hisim.economics.timeline import Actor

    horizon = int(inputs["horizon_in_years"])
    parameters = EconomicParameters(
        observation_period_in_years=horizon,
        interest_rate=float(inputs["interest_rate"]),
        general_price_escalation_rate=0.0,
        investment_price_escalation_rate=0.0,
        energy_price_escalation_rates={carrier: 0.0 for carrier in EnergyCarrier},
        co2_price_scenario="none",
        country="DE",
        price_basis_year=EXAMPLE_YEAR,
    )
    subjects = [
        ("HeatPump", ComponentType.HEAT_PUMP, Units.KILOWATT, float(inputs["heating_investment_in_euro"])),
        (
            "Envelope.WallInsulation",
            ComponentType.WALL_EXTERNAL_INSULATION,
            Units.SQUARE_METER,
            float(inputs["envelope_investment_in_euro"]),
        ),
    ]
    cost_facts = [
        SubjectCostFacts(
            subject,
            ComponentCostFacts(
                asset_class=asset_class,
                size=10.0,
                size_unit=size_unit,
                investment_cost_override_in_euro=UncertainValue.exact(investment),
                installation_cost_override_in_euro=UncertainValue.exact(0.0),
                lifetime_override_in_years=float(horizon),
                maintenance_rate_override=UncertainValue.exact(0.0),
                fixed_operation_cost_override_in_euro_per_year=UncertainValue.exact(0.0),
                embodied_co2_override_in_kg=0.0,
                override_source="worked example (cost-spec-v2 §3)",
            ),
        )
        for subject, asset_class, size_unit, investment in subjects
    ]
    catalog = SubsidyCatalog(
        schemes=[
            _levy_scheme("SCHEME_HEATING", ComponentType.HEAT_PUMP, float(inputs["heating_subsidy_rate"])),
            _levy_scheme(
                "SCHEME_ENVELOPE", ComponentType.WALL_EXTERNAL_INSULATION, float(inputs["envelope_subsidy_rate"])
            ),
        ],
        questions={},
        snapshot_date=None,
        overall_cap_share=None,
        base_path="",
        country="DE",
    )
    evaluation_inputs = EvaluationInputs(
        simulation_year=EXAMPLE_YEAR,
        simulated_period_fraction=1.0,
        cost_facts=cost_facts,
        billing=[],
        tariff_contracts={},
        subsidy_context=SubsidyContext(building=SubsidyBuildingContext(dwelling_units=1)),
        living_area_in_m2=float(inputs["living_area_in_m2"]),
        current_cold_rent_in_euro_per_m2_month=float(inputs["current_cold_rent_in_euro_per_m2_month"]),
    )
    perspective = Perspective(
        id="worked_example_landlord",
        installation_context=InstallationContext.GREENFIELD,
        actor_scope=ActorScope.LANDLORD,
        subsidy_mode=SubsidyMode.full(),
    )
    evaluator = EconomicEvaluator(database, parameters, subsidy_catalog=catalog)
    result = evaluator.evaluate(evaluation_inputs, perspective)

    values: Dict[str, float] = {
        "landlord_npv_in_euro": result.npv_by_payer[Actor.LANDLORD].best_estimate,
        "tenant_npv_in_euro": result.npv_by_payer[Actor.TENANT].best_estimate,
        "present_value_factor_sum": sum(parameters.discount_factor(year) for year in range(1, horizon + 1)),
    }
    for subject, _asset_class, _unit, _investment in subjects:
        values[f"subsidy_{subject.rsplit('.', maxsplit=1)[-1].lower()}_in_euro"] = -sum(
            entry.amount_in_euro.best_estimate
            for entry in result.timeline.entries
            if entry.category == CostCategory.SUBSIDY and entry.subject == subject
        )
    levy_entries = [
        entry
        for entry in result.timeline.entries
        if entry.category == CostCategory.MODERNIZATION_LEVY and entry.payer == Actor.TENANT
    ]
    values["annual_levy_total_in_euro"] = levy_entries[0].amount_in_euro.best_estimate if levy_entries else 0.0
    values["levy_npv_in_euro"] = sum(
        entry.amount_in_euro.best_estimate * parameters.discount_factor(entry.year) for entry in levy_entries
    )
    # The paragraph split, off the engine's own levy basis (the timeline books only the sum).
    build = evaluator.build_timeline(evaluation_inputs, perspective, ProvenanceLedger())
    ruleset = get_ruleset(True, parameters.country)
    # The §559e example is German by construction. `get_ruleset` is typed to the protocol,
    # which promises only `allocate`; the levy pools, bases and caps below are the German
    # ruleset's own, so the concrete type is asserted rather than assumed.
    assert isinstance(ruleset, DE2024Ruleset), "the §559e example needs the German ruleset"
    context = AllocationContext(
        horizon_years=horizon,
        living_area_in_m2=float(inputs["living_area_in_m2"]),
        current_cold_rent_in_euro_per_m2_month=float(inputs["current_cold_rent_in_euro_per_m2_month"]),
        modernization_cost_in_euro=build.basis.modernization_cost,
        subsidies_received_in_euro=build.basis.subsidies,
        avoided_maintenance_in_euro=build.basis.avoided_maintenance,
        levy_subjects=build.basis.by_subject,
    )
    outcome = ruleset.compute_modernization_levy(context)
    values["annual_levy_heating_in_euro"] = outcome.heating_levy_in_euro.best_estimate
    values["annual_levy_general_in_euro"] = outcome.general_levy_in_euro.best_estimate
    heating_basis, general_basis = ruleset.levy_pools(context)
    values["heating_levy_basis_in_euro"] = ruleset.levy_basis(*heating_basis).best_estimate
    values["general_levy_basis_in_euro"] = ruleset.levy_basis(*general_basis).best_estimate
    months_of_area = 12.0 * float(inputs["living_area_in_m2"])
    values["heating_cap_in_euro_per_year"] = (
        ruleset.levy.heating_cap_in_euro_per_m2_per_month * months_of_area
    )
    values["general_cap_in_euro_per_year"] = ruleset.general_cap_rate(context) * months_of_area
    return values


def _end_to_end_values(inputs: Dict[str, Any], database: CostDatabase) -> Dict[str, float]:
    """Evaluates a full variant through `EconomicEvaluator.evaluate` (§3.6, §3.7).

    The seam-1 entry point: one device and (optionally) one electricity carrier are turned into a
    complete `EvaluationInputs`, evaluated through the public evaluator API, and published as NPV
    per category, nominal amount per category and year, the annual cost series, the EAC and the
    monthly year-1 figure. Every device figure is overridden and the tariff contract is built
    here, so the synthetic `XX` database contributes nothing (D17) — a shipped price cannot leak
    into a hand-checkable number.

    Both the per-category NPVs and the per-year nominal amounts are pre-seeded with 0.0 for every
    category and year before the timeline is folded in. That is deliberate: it lets a workbook
    assert that a category is *absent* (no replacement inside the horizon, no residual value) as
    an explicit zero, instead of the collector reporting "no such label" and the example silently
    covering less than it claims.

    An example that declares `heating_investment_in_euro` is a *package* under the landlord/tenant
    split and goes to :func:`_modernization_levy_values` instead: one device cannot express the
    §559/§559e paragraph split (§6.4, D27).
    """
    if "heating_investment_in_euro" in inputs:
        return _modernization_levy_values(inputs, database)
    horizon = int(inputs["horizon_in_years"])
    parameters = EconomicParameters(
        observation_period_in_years=horizon,
        interest_rate=float(inputs["interest_rate"]),
        general_price_escalation_rate=float(inputs.get("general_price_escalation_rate", 0.0)),
        investment_price_escalation_rate=float(inputs.get("investment_price_escalation_rate", 0.0)),
        energy_price_escalation_rates={
            carrier: float(inputs.get("energy_price_escalation_rate", 0.0)) for carrier in EnergyCarrier
        },
        feed_in_escalation_rate=float(inputs.get("feed_in_escalation_rate", 0.0)),
        co2_price_scenario="none",
        country=SYNTHETIC_COUNTRY,
        price_basis_year=EXAMPLE_YEAR,
    )
    facts = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue.exact(float(inputs["investment_in_euro"])),
        installation_cost_override_in_euro=UncertainValue.exact(0.0),
        lifetime_override_in_years=float(inputs["lifetime_in_years"]),
        maintenance_rate_override=UncertainValue.exact(float(inputs.get("maintenance_rate", 0.0))),
        fixed_operation_cost_override_in_euro_per_year=UncertainValue.exact(0.0),
        embodied_co2_override_in_kg=0.0,
        override_source="worked example (cost-spec-v2 §3)",
    )
    billing = []
    contracts = {}
    if "energy_bought_in_kwh" in inputs or "energy_sold_in_kwh" in inputs:
        billing.append(
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=float(inputs.get("energy_bought_in_kwh", 0.0)),
                energy_sold_in_kwh=float(inputs.get("energy_sold_in_kwh", 0.0)),
            )
        )
        contracts[EnergyCarrier.ELECTRICITY] = TariffContract(
            id="WORKED_EXAMPLE_FLAT",
            carrier=EnergyCarrier.ELECTRICITY,
            country=SYNTHETIC_COUNTRY,
            region=None,
            valid_from_year=EXAMPLE_YEAR,
            supply=TariffSupply(
                kind=SupplyKind.FLAT,
                working_price_in_euro_per_kwh=UncertainValue.exact(
                    float(inputs.get("electricity_price_in_euro_per_kwh", 0.0))
                ),
            ),
            standing_charge_in_euro_per_year=UncertainValue.exact(
                float(inputs.get("standing_charge_in_euro_per_year", 0.0))
            ),
            feed_in=FeedIn(
                kind=FeedInKind.FIXED_TARIFF if inputs.get("feed_in_rate_in_euro_per_kwh") else FeedInKind.NONE,
                rate_in_euro_per_kwh=UncertainValue.exact(float(inputs.get("feed_in_rate_in_euro_per_kwh", 0.0))),
                duration_in_years=20,
            ),
            source_ids=("inline:worked example",),
        )
    evaluation_inputs = EvaluationInputs(
        simulation_year=EXAMPLE_YEAR,
        simulated_period_fraction=1.0,
        cost_facts=[SubjectCostFacts("device", facts)],
        billing=billing,
        tariff_contracts=contracts,
    )
    perspective = Perspective(
        id="worked_example",
        installation_context=InstallationContext.GREENFIELD,
        subsidy_mode=SubsidyMode.none(),
    )
    result = EconomicEvaluator(database, parameters).evaluate(evaluation_inputs, perspective)

    values: Dict[str, float] = {
        "total_npv_in_euro": result.total_npv_in_euro.best_estimate,
        "eac_in_euro": result.equivalent_annual_cost_in_euro.best_estimate,
        "annuity_factor": parameters.annuity_factor(),
        "present_value_factor_sum": sum(parameters.discount_factor(year) for year in range(1, horizon + 1)),
    }
    if result.monthly_cost_year1_in_euro is not None:
        values["monthly_cost_year_1_in_euro"] = result.monthly_cost_year1_in_euro.best_estimate
    for category in CostCategory:
        values[f"npv_{category.value.lower()}_in_euro"] = 0.0
        for year in range(0, horizon + 1):
            values[f"nominal_{category.value.lower()}_year_{year}_in_euro"] = 0.0
    for category, band in result.npv_by_category.items():
        values[f"npv_{category.value.lower()}_in_euro"] = band.best_estimate
    for year, band in enumerate(result.annual_cost_series_nominal_in_euro):
        values[f"annual_cost_year_{year}_in_euro"] = band.best_estimate
    for entry in result.timeline.entries:
        key = f"nominal_{entry.category.value.lower()}_year_{entry.year}_in_euro"
        values[key] = values.get(key, 0.0) + entry.amount_in_euro.best_estimate
    return values
