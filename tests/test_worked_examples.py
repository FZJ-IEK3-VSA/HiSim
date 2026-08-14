"""Collector for the worked-example library (cost-spec-v2 §3.5).

Every `tests/worked_examples/<group>/<name>.yaml` is generated from the workbook next to it by
`tools/convert_worked_examples.py`. This module reads the YAML only — never the xlsx (§3.8) —
maps the example's group to the engine entry point it exercises, runs it with the declared
inputs and compares every expected value within its declared tolerance.

The Excel formulas in the workbooks are an independent second implementation: if these tests
pass, two different toolchains agree on the same arithmetic.

Group to entry point:

===============  ==========================================================================
group            entry point
===============  ==========================================================================
`financing`      :func:`hisim.economics.financing.loan_flows`
`discounting`    :class:`hisim.economics.parameters.EconomicParameters` + `CashFlowTimeline`
`tariffs`        :func:`hisim.economics.tariffs.apply_tariff`
`subsidies`      :func:`hisim.economics.subsidies.solve_cumulation` on in-memory catalogs
`end_to_end`     :meth:`hisim.economics.evaluator.EconomicEvaluator.evaluate` (a package under
                 the landlord/tenant split when the example declares a heating investment)
===============  ==========================================================================

The runners aggregate engine outputs (sums, ratios, discounting) but never re-implement
pricing logic: whenever a worked example asserts a capped basis, a scaled rate or a band
price, the number comes out of the engine, so a bug there cannot cancel itself out here.

**Error class.** A failure names a *formula*, and — because the examples are grouped by
calculator and assert intermediates as well as finals (§3.2) — usually the step inside it: the
failure message prints the label, the Excel value, the engine value and the derivation the
workbook recorded. What a failure here can never be is a *price* problem: the end-to-end runner
prices against a synthetic empty country (`XX`, D17) with zero energy prices and overrides every
device figure, so no shipped price can reach an expected value. The one deliberate exception is
the modernization-levy example, which must run under German tenancy law and therefore reads the
statutory percentages of `allocation_DE_2024.json` — it declares them as workbook inputs so that
a change to that file fails the example instead of re-baselining it. Nor can a failure be an extraction
problem — nothing here runs a simulation. Two failure modes of the *library* itself are checked
separately: `test_library_covers_every_group` guards against a group quietly emptying out, and
`test_every_workbook_has_a_generated_yaml` against an xlsx whose YAML was never generated (the
complementary direction, YAML drifting from its workbook, is CI's converter re-run, §3.6).

**Uncertainty policy.** Every worked example uses degenerate bands (§3.2, D16); slot mechanics
are verified by the property tests instead. That is why every runner below reads `.average` and
builds inputs with `UncertainValue.exact` — not a simplification, a stated scope boundary.
"""

# clean

import hashlib
import json
import os
import re
import warnings
from typing import Any, Callable, Dict, List, Optional

import pytest
import yaml

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

pytestmark = [pytest.mark.base, pytest.mark.worked_examples]

#: Root of the library; one sub-directory per calculator group (§3.1).
WORKED_EXAMPLES_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worked_examples")

#: Country code of the synthetic cost database the end-to-end examples price against. It holds
#: no device entries and no escalation defaults, so no shipped price can leak into an example.
SYNTHETIC_COUNTRY = "XX"

#: The price basis year every example is evaluated at.
EXAMPLE_YEAR = 2024

#: Everything before this marker is hashed for the content fingerprint (§3.8); it must stay in
#: sync with `tools/convert_worked_examples.py`.
REVIEW_BLOCK_MARKER = "\nreview:\n"


class UnreviewedWorkedExampleWarning(UserWarning):
    """A worked example carries no valid human review attestation (§3.8).

    Its own warning class so a CI job or a reviewer can filter for exactly this signal, and so
    that switching `enforcement.yaml` from `warn` to `error` later changes only the severity, not
    the meaning. It covers both "never attested" and "attested, but the content changed since" —
    the message distinguishes them, since a stale attestation names the reviewer whose sign-off
    no longer applies.
    """


# --------------------------------------------------------------------------- loading


def load_examples() -> List[Dict[str, Any]]:
    """Reads every generated YAML fixture, sorted by group and name.

    Collection happens at import time so pytest can parametrize one test per example and report
    them individually — adding a workbook plus its YAML adds a named test case with no code
    change. The raw file text is kept alongside the parsed data because the §3.8 attestation is a
    hash over the file *before* its `review:` block, which the parsed mapping cannot reconstruct.
    `enforcement.yaml` is skipped: it is the policy switch, not an example. The sort makes test
    ids and their order deterministic across filesystems.
    """
    examples = []
    for directory, _, file_names in os.walk(WORKED_EXAMPLES_ROOT):
        for file_name in sorted(file_names):
            if not file_name.endswith(".yaml") or file_name == "enforcement.yaml":
                continue
            path = os.path.join(directory, file_name)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            example = yaml.safe_load(text)
            example["path"] = path
            example["raw_text"] = text
            examples.append(example)
    return sorted(examples, key=lambda item: (item["group"], item["name"]))


def enforcement_mode() -> str:
    """Reads the warn/error switch of `tests/worked_examples/enforcement.yaml` (§3.8).

    Whether a missing review attestation fails the suite or only warns is a project decision, not
    a code decision, so it lives in a data file one line long. D9 keeps it at `warn` indefinitely:
    attestations so far are the owner's own, and failing CI on them would enforce a formality
    rather than the external domain-expert review the mechanism is meant to invite.
    """
    path = os.path.join(WORKED_EXAMPLES_ROOT, "enforcement.yaml")
    with open(path, encoding="utf-8") as handle:
        return str(yaml.safe_load(handle).get("enforcement", "warn"))


EXAMPLES = load_examples()
EXAMPLE_IDS = [f"{example['group']}/{example['name']}" for example in EXAMPLES]


# --------------------------------------------------------------------------- group runners


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
        "disbursement_in_euro": disbursement.average,
    }
    remaining = principal
    total_interest = 0.0
    total_repayment = 0.0
    for year, interest, repayment in schedule:
        values[f"interest_year_{year}_in_euro"] = interest.average
        values[f"principal_repayment_year_{year}_in_euro"] = repayment.average
        remaining -= repayment.average
        values[f"remaining_debt_year_{year}_in_euro"] = remaining
        total_interest += interest.average
        total_repayment += repayment.average
    annuity = schedule[0][1].average + schedule[0][2].average
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
    net_present_value = timeline.npv(rate).average
    values = {
        "annuity_factor": annuity_factor,
        "annuity_factor_via_pmt": annuity_factor,
        "npv_in_euro": net_present_value,
        "eac_in_euro": net_present_value * annuity_factor,
    }
    for year in range(0, horizon + 1):
        values[f"discount_factor_year_{year}"] = parameters.discount_factor(year)
        year_only = timeline.filtered(lambda entry, wanted=year: entry.year == wanted)
        values[f"discounted_cash_flow_year_{year}_in_euro"] = year_only.npv(rate).average
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
        "energy_working_in_euro": bill.by_category.get(CostCategory.ENERGY_WORKING, zero).average,
        "energy_standing_in_euro": bill.by_category.get(CostCategory.ENERGY_STANDING, zero).average,
        "energy_capacity_charge_in_euro": bill.by_category.get(CostCategory.ENERGY_CAPACITY_CHARGE, zero).average,
        "feed_in_revenue_in_euro": bill.by_category.get(CostCategory.FEED_IN_REVENUE, zero).average,
        "total_bill_in_euro": bill.total().average,
        "mean_energy_price_in_euro_per_kwh": bill.mean_energy_price_in_euro_per_kwh,
        "volume_effect_in_euro": bill.volume_effect_in_euro,
        "flexibility_value_in_euro": bill.flexibility_value_in_euro,
        "marginal_price_components_in_euro_per_kwh": contract.marginal_purchase_price_components().average,
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
        values[f"scheme_{letter}_award_in_euro"] = award.upfront_amount.average if award is not None else 0.0
    total_upfront = sum(award.upfront_amount.average for award in decision.applied)
    schedule_total = 0.0
    support_npv = total_upfront
    for award in decision.applied:
        for offset, amount in enumerate(award.schedule_amounts, start=1):
            values[f"tax_credit_year_{offset}_in_euro"] = amount.average
            schedule_total += amount.average
            support_npv += amount.average * parameters.discount_factor(offset)
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
    from hisim.economics.actors import AllocationContext, get_ruleset
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
            ComponentType.WALL_INSULATION,
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
                "SCHEME_ENVELOPE", ComponentType.WALL_INSULATION, float(inputs["envelope_subsidy_rate"])
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
        "landlord_npv_in_euro": result.npv_by_payer[Actor.LANDLORD].average,
        "tenant_npv_in_euro": result.npv_by_payer[Actor.TENANT].average,
        "present_value_factor_sum": sum(parameters.discount_factor(year) for year in range(1, horizon + 1)),
    }
    for subject, _asset_class, _unit, _investment in subjects:
        values[f"subsidy_{subject.split('.')[-1].lower()}_in_euro"] = -sum(
            entry.amount_in_euro.average
            for entry in result.timeline.entries
            if entry.category == CostCategory.SUBSIDY and entry.subject == subject
        )
    levy_entries = [
        entry
        for entry in result.timeline.entries
        if entry.category == CostCategory.MODERNIZATION_LEVY and entry.payer == Actor.TENANT
    ]
    values["annual_levy_total_in_euro"] = levy_entries[0].amount_in_euro.average if levy_entries else 0.0
    values["levy_npv_in_euro"] = sum(
        entry.amount_in_euro.average * parameters.discount_factor(entry.year) for entry in levy_entries
    )
    # The paragraph split, off the engine's own levy basis (the timeline books only the sum).
    build = evaluator.build_timeline(evaluation_inputs, perspective, ProvenanceLedger())
    ruleset = get_ruleset(True, parameters.country)
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
    values["annual_levy_heating_in_euro"] = outcome.heating_levy_in_euro.average
    values["annual_levy_general_in_euro"] = outcome.general_levy_in_euro.average
    heating_basis, general_basis = ruleset.levy_pools(context)
    values["heating_levy_basis_in_euro"] = ruleset.levy_basis(*heating_basis).average
    values["general_levy_basis_in_euro"] = ruleset.levy_basis(*general_basis).average
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
        "total_npv_in_euro": result.total_npv_in_euro.average,
        "eac_in_euro": result.equivalent_annual_cost_in_euro.average,
        "annuity_factor": parameters.annuity_factor(),
        "present_value_factor_sum": sum(parameters.discount_factor(year) for year in range(1, horizon + 1)),
    }
    if result.monthly_cost_year1_in_euro is not None:
        values["monthly_cost_year_1_in_euro"] = result.monthly_cost_year1_in_euro.average
    for category in CostCategory:
        values[f"npv_{category.value.lower()}_in_euro"] = 0.0
        for year in range(0, horizon + 1):
            values[f"nominal_{category.value.lower()}_year_{year}_in_euro"] = 0.0
    for category, band in result.npv_by_category.items():
        values[f"npv_{category.value.lower()}_in_euro"] = band.average
    for year, band in enumerate(result.annual_cost_series_nominal_in_euro):
        values[f"annual_cost_year_{year}_in_euro"] = band.average
    for entry in result.timeline.entries:
        key = f"nominal_{entry.category.value.lower()}_year_{entry.year}_in_euro"
        values[key] = values.get(key, 0.0) + entry.amount_in_euro.average
    return values


@pytest.fixture(name="synthetic_database", scope="module")
def fixture_synthetic_database(tmp_path_factory) -> CostDatabase:
    """A cost database with nothing in it but one electricity price entry.

    The end-to-end examples override every device figure and bring their own tariff contract, so
    the database must not contribute any number of its own. It also carries no escalation
    defaults file, which keeps the parameter fallback chain (§3.2) at the explicit rates the
    example declares.
    """
    directory = tmp_path_factory.mktemp("worked_example_cost_database")
    sources = {
        "sources": [
            {
                "id": "src_worked_example",
                "citation": "Synthetic worked-example data (cost-spec-v2 §3)",
                "publication_year": 2024,
                "retrieved": "2026-08-12",
                "kind": "EXPERT_ESTIMATE",
                "notes": "Round numbers invented for the worked-example library; never a research input.",
            }
        ]
    }
    prices = {
        "entries": [
            {
                "carrier": "ELECTRICITY",
                "year": EXAMPLE_YEAR,
                "working_price_in_euro_per_kwh": 0.0,
                "standing_charge_in_euro_per_year": 0.0,
                "emission_factor_in_kg_per_kwh": 0.0,
                "co2_price_exposure": 0.0,
                "tax_and_levy_share": 0.0,
                "quantity_unit": "kWh",
                "source_ids": ["src_worked_example"],
                "notes": "Zero prices: every worked example brings its own tariff contract.",
            }
        ]
    }
    (directory / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
    (directory / f"energy_prices_{SYNTHETIC_COUNTRY}.json").write_text(json.dumps(prices), encoding="utf-8")
    return CostDatabase(str(directory))


# --------------------------------------------------------------------------- the tests


@pytest.mark.parametrize("example", EXAMPLES, ids=EXAMPLE_IDS)
def test_worked_example(example: Dict[str, Any], synthetic_database: CostDatabase) -> None:
    """Runs one worked example and compares every expected value within its tolerance."""
    runners: Dict[str, Callable[[Dict[str, Any]], Dict[str, float]]] = {
        "financing": _financing_values,
        "discounting": _discounting_values,
        "tariffs": _tariff_values,
        "subsidies": _subsidy_values,
        "end_to_end": lambda inputs: _end_to_end_values(inputs, synthetic_database),
    }
    group = example["group"]
    assert group in runners, f"no entry point registered for worked-example group {group!r}"
    computed = runners[group](example["inputs"])
    for label, expectation in example["expected"].items():
        assert label in computed, (
            f"{example['name']}: the {group} entry point produces no value called {label!r}; "
            "either the workbook label or the collector mapping is wrong."
        )
        difference = abs(computed[label] - float(expectation["value"]))
        assert difference <= float(expectation["abs_tol"]), (
            f"{example['name']}.{label}: Excel says {expectation['value']}, the engine says "
            f"{computed[label]!r} (difference {difference!r} > tolerance {expectation['abs_tol']}). "
            f"Derivation: {expectation.get('derivation') or expectation.get('note')}"
        )


@pytest.mark.parametrize("example", EXAMPLES, ids=EXAMPLE_IDS)
def test_worked_example_review_attestation(example: Dict[str, Any]) -> None:
    """Validates the §3.8 attestation from the YAML alone; warns while enforcement is `warn`."""
    text = example["raw_text"]
    index = text.find(REVIEW_BLOCK_MARKER)
    content = text if index == -1 else text[: index + 1]
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12].upper()
    fingerprint = f"{digest[0:4]}-{digest[4:8]}-{digest[8:12]}"
    review: Optional[Dict[str, Any]] = example.get("review")
    if review is not None and review.get("fingerprint") == fingerprint:
        return
    if review is None:
        message = f"{example['name']}: no review attestation — content fingerprint {fingerprint} (§3.8)."
    else:
        message = (
            f"{example['name']}: review by {review.get('reviewed_by')!r} is stale — content "
            f"fingerprint {fingerprint} (§3.8)."
        )
    if enforcement_mode() == "error":
        pytest.fail(message)
    warnings.warn(UnreviewedWorkedExampleWarning(message))


def test_library_covers_every_group() -> None:
    """The library holds at least 20 examples and no group is empty (§3)."""
    assert len(EXAMPLES) >= 20, f"only {len(EXAMPLES)} worked examples found; the library needs at least 20."
    groups = {example["group"] for example in EXAMPLES}
    expected_groups = {"financing", "discounting", "tariffs", "subsidies", "end_to_end"}
    assert expected_groups <= groups, f"missing worked-example groups: {sorted(expected_groups - groups)}"
    for group in expected_groups:
        assert any(example["group"] == group for example in EXAMPLES), f"group {group!r} is empty."


def test_every_workbook_has_a_generated_yaml() -> None:
    """Every committed workbook has its generated fixture next to it (§3.6)."""
    for directory, _, file_names in os.walk(WORKED_EXAMPLES_ROOT):
        for file_name in file_names:
            if not file_name.endswith(".xlsx") or file_name.startswith(("_", "~$")):
                continue
            yaml_path = os.path.join(directory, os.path.splitext(file_name)[0] + ".yaml")
            assert os.path.isfile(yaml_path), (
                f"{os.path.join(directory, file_name)} has no generated YAML; "
                "run python tools/convert_worked_examples.py"
            )
