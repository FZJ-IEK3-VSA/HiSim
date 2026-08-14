"""Typed result objects of the lifecycle cost engine (cost_spec.md §3.7, §3.8, §7).

CSV/JSON are export formats, never an internal API.

This module owns the seam-4 contract of cost-spec-v2 §2.4: everything the engine publishes about
one evaluation, in typed form, together with the *comparison arithmetic* that turns two of those
into a differential statement. `LifecycleCostResult` is what `EconomicEvaluator.evaluate`
returns and what every downstream consumer reads — the exports, the KPI layer, the audit, the
CLI's `explain`, the derived views and the HTML/markdown reports. The enforceable rule on
the other side of the seam is *presentation never computes*: if a report needs a number, it is
either a field here or a function here (`compare`, `cumulative_discounted_savings`), never a loop
in a template.

What the module deliberately does not own: building the timeline (`evaluator.py`), discounting
and pivoting it into these fields (`calculators/aggregation.py`), and writing any file
(`exports.py`, `reporting.py`). Two things a reviewer should carry into the classes below —
every monetary field is a LOW/BEST_ESTIMATE/HIGH band (`UncertainValue`, §3.9) whose bounds are
*envelopes* rather than quantiles, and signs are uniform across the package: cost positive,
revenue and support negative, with the display figures that mirror support back to positive
saying so in their names (W3.4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from hisim.economics.parameters import EconomicParameters
from hisim.economics.provenance import (
    ProvenanceLedger,
    ProvenanceReport,
    ProvenanceReportEntry,
    ResolvedSource,
)
from hisim.economics.subsidies import SubsidyDecision
from hisim.economics.timeline import (
    Actor,
    CashFlowEntry,
    CashFlowTimeline,
    CostCategory,
    SubjectKind,
    discount_factor,
)
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass


@dataclass
class ComponentCostBreakdown:
    """Per-subject cost breakdown: a pure pivot of the canonical timeline (§3.7, §7.4).

    One record per timeline subject — a component, an energy carrier, or one of the synthetic
    subjects the engine books cross-cutting flows under (`financing`, `replacement reserve`,
    `co2 damage`). Because every cash-flow entry carries its subject, this is a pivot and not a
    second calculation: the breakdowns sum exactly to the perspective totals, by construction
    rather than by agreement between two code paths, which is what makes the stacked-bar frontends
    and the §9.5 audit table trustworthy.

    It carries both views of the same money on purpose. `npv_by_category` and the two NPV totals
    are *discounted and signed* (support and residual values negative); the three fields below
    them are *undiscounted display figures* answering "what does buying this thing cost", where
    support is reported positive and separately from the gross investment. The two subsidy fields
    exist because that figure has two legitimate units and mixing them was a real defect (W3.4).
    """

    subject: str  # component name, or carrier for energy subjects
    subject_kind: SubjectKind
    asset_class: Optional[ComponentType]
    kpi_tag: Optional[KpiTagEnumClass]
    npv_by_category: Dict[CostCategory, UncertainValue]
    total_npv_in_euro: UncertainValue
    equivalent_annual_cost_in_euro: UncertainValue
    # Undiscounted display figures for "what does X cost to buy" views:
    investment_gross_in_euro: UncertainValue
    #: Support received for this subject, **nominal** euros summed across years, positive band
    #: (W3.4). The same unit the §6.4 levy basis deducts.
    subsidies_nominal_in_euro: UncertainValue
    #: The same support **discounted** to present value, positive band — the exact mirror of
    #: `npv_by_category[SUBSIDY]`, which is negative-signed (W3.4).
    subsidies_npv_in_euro: UncertainValue
    annual_cost_series_nominal_in_euro: List[UncertainValue]
    lifecycle_co2_in_kg: float

    def to_json(self) -> dict:
        """Serialization for component_costs.json (§7.4).

        Every band is written through `UncertainValue.to_json`, so a consumer of the file sees the
        same min/best_estimate/max triplet the engine computed with, never a collapsed average.
        """
        return {
            "subject": self.subject,
            "subject_kind": self.subject_kind.value,
            "asset_class": self.asset_class.value if self.asset_class else None,
            "kpi_tag": self.kpi_tag.value if self.kpi_tag else None,
            "npv_by_category": {category.value: value.to_json() for category, value in self.npv_by_category.items()},
            "total_npv_in_euro": self.total_npv_in_euro.to_json(),
            "equivalent_annual_cost_in_euro": self.equivalent_annual_cost_in_euro.to_json(),
            "investment_gross_in_euro": self.investment_gross_in_euro.to_json(),
            "subsidies_nominal_in_euro": self.subsidies_nominal_in_euro.to_json(),
            "subsidies_npv_in_euro": self.subsidies_npv_in_euro.to_json(),
            "annual_cost_series_nominal_in_euro": [
                value.to_json() for value in self.annual_cost_series_nominal_in_euro
            ],
            "lifecycle_co2_in_kg": self.lifecycle_co2_in_kg,
        }


@dataclass
class LifecycleCo2Result:
    """Parallel, undiscounted CO2 accounting (§3.8).

    The CO2 *damage cost* (macroeconomic) and the CO2 *price* (a real cash flow) are distinct
    and must never be added together.

    Everything in this object is a **mass in kilograms**, never a euro, and nothing in it is
    discounted — discounting a physical quantity would be meaningless, and a lifecycle emission
    figure is reported as the sum over the horizon. It runs alongside the money in one build:
    `calculators/investment.py` contributes the embodied mass at installation and at every
    replacement, `calculators/energy.py` the operational mass per carrier, and
    `calculators/co2.py` folds both in and closes the total.

    Held on `LifecycleCostResult` and consumed by the lifecycle-CO2 KPIs, the report's §3.8
    section and the per-subject breakdowns. Note that v1 holds emission factors constant over the
    horizon — grid decarbonization is not modelled — which a reviewer reading a 20-year
    electricity figure should be aware of.
    """

    embodied_co2_in_kg: float = 0.0  # install + replacements, no discounting
    operational_co2_by_year_in_kg: List[float] = field(default_factory=list)  # index = year 1..T
    operational_co2_by_carrier_in_kg: Dict[str, float] = field(default_factory=dict)
    total_co2_in_kg: float = 0.0
    embodied_by_subject_in_kg: Dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialization.

        Note the two operational fields carry different units of aggregation: the by-year array is
        kg *per year* indexed 0..T (year 0 stays zero), the by-carrier map is kg over the *whole*
        horizon.
        """
        return {
            "embodied_co2_in_kg": self.embodied_co2_in_kg,
            "operational_co2_by_year_in_kg": self.operational_co2_by_year_in_kg,
            "operational_co2_by_carrier_in_kg": self.operational_co2_by_carrier_in_kg,
            "total_co2_in_kg": self.total_co2_in_kg,
            "embodied_by_subject_in_kg": self.embodied_by_subject_in_kg,
        }


@dataclass(frozen=True)
class AnnualEnergyQuantities:
    """One carrier's *annualized* energy volumes, as the engine priced them (W4.1/W4.2).

    Extensive simulation quantities scaled to a full year by the §3.6 rule-5 annualization
    (`calculators/annualization.py`), so per-unit figures derived from them — effective
    EUR/kWh, EUR/m²a — are per-year figures like every other engine output. Carried on the
    result because the derived views and the plausibility checks need them *without* reaching
    back into `EvaluationInputs` (cost-spec-v2 W4.2).
    """

    bought_in_kwh: float
    sold_in_kwh: float = 0.0

    def to_json(self) -> dict:
        """Serialization for lifecycle_costs.json."""
        return {"bought_in_kwh": self.bought_in_kwh, "sold_in_kwh": self.sold_in_kwh}


@dataclass(frozen=True)
class ReferenceAreas:
    """The building areas per-area KPIs divide by (§6.3); both optional, both in m².

    Two areas rather than one because the two questions differ: heated floor area is the physics
    reference the building model works in, living area is the legal reference German rent and
    levy rules use (§6.4). Carried on the result so that a stored `lifecycle_costs.json` is enough
    to render every EUR/m²a figure, with no reach-back into `EvaluationInputs` (W4.2). Both may be
    absent, in which case per-area figures are simply not reported.
    """

    heated_floor_area_in_m2: Optional[float] = None
    living_area_in_m2: Optional[float] = None

    def preferred(self) -> Optional[float]:
        """The area per-area figures use: living area when known, else heated floor area.

        The precedence the reports have always used (`reporting.py:263` before W4.2).
        """
        return self.living_area_in_m2 or self.heated_floor_area_in_m2

    def to_json(self) -> dict:
        """Serialization for lifecycle_costs.json."""
        return {
            "heated_floor_area_in_m2": self.heated_floor_area_in_m2,
            "living_area_in_m2": self.living_area_in_m2,
        }


@dataclass
class LifecycleCostResult:
    """The evaluation of one variant under one perspective (§3.7).

    The engine's headline object: one simulated variant, seen through one of the nine default
    perspectives (§7.1), with every euro figure as a LOW/BEST_ESTIMATE/HIGH band (§3.9). It is produced
    by `EconomicEvaluator.evaluate` and consumed by everything downstream — `exports.py`,
    `audit.py`, `views.py`, `reporting.py`, the KPI layer and the CLI — which is why it carries
    not just the KPIs but also the raw material behind them: the full `timeline`, the provenance
    `ledger` and its `source_resolver`, the subsidy `decisions`, the CO2 masses, and the physical
    context (energy quantities, reference areas, simulated period fraction, simulation year) that
    presentation would otherwise have to fetch from `EvaluationInputs` (W4.2, W4.6).

    Every KPI on it is a filter, a pivot or a discounting of `timeline`, so the fields reconcile
    with each other by construction (§3.1); `explain()` exploits exactly that to answer "where
    does this number come from" down to the citation. Note what is deliberately *not* netted into
    the decision KPIs: `sunk_cost_written_off_in_euro` is reported because researchers want to see
    it, and excluded from NPV because a sunk cost must not distort a forward-looking comparison
    (§4.1).

    Scope is the one subtlety. `timeline` always holds the *full* allocated timeline so that the
    §6.5 zero-sum invariant stays checkable, while the perspective itself reports on `scope_payer`
    — every scoped figure goes through `scoped_timeline()`, which is also what `explain()`
    filters on (§7 B4).
    """

    perspective_id: str
    parameters: EconomicParameters
    total_npv_in_euro: UncertainValue  # net present cost over the horizon
    equivalent_annual_cost_in_euro: UncertainValue  # NPV x annuity factor — the headline KPI
    npv_by_category: Dict[CostCategory, UncertainValue]
    npv_by_component: Dict[str, UncertainValue]
    npv_by_payer: Dict[Actor, UncertainValue]
    component_breakdowns: Dict[str, ComponentCostBreakdown]
    annual_cost_series_nominal_in_euro: List[UncertainValue]  # liquidity view, year 0..T
    monthly_cost_year1_in_euro: Optional[UncertainValue]
    levelized_cost_of_heat_in_euro_per_kwh: Optional[UncertainValue]
    timeline: CashFlowTimeline
    lifecycle_co2_result: LifecycleCo2Result
    subsidy_decisions: List[SubsidyDecision] = field(default_factory=list)
    # Written-off residual book value of replaced assets, reported but excluded from
    # decision KPIs (§4.1):
    sunk_cost_written_off_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    ledger: Optional[ProvenanceLedger] = None
    source_resolver: Optional[Dict[str, ResolvedSource]] = None
    # The payer this perspective reports on (§6). `timeline` always holds the FULL allocated
    # timeline (all payers, for the zero-sum invariant and payer pivots); consumers that
    # present "this perspective's flows" must filter by this payer — see `scoped_timeline()`.
    scope_payer: Actor = Actor.SYSTEM
    #: Annualized energy volumes per carrier (key = `EnergyCarrier.value`, the timeline subject
    #: name), so effective prices are derivable from the result alone (W4.2).
    annual_energy_quantities_by_carrier: Dict[str, AnnualEnergyQuantities] = field(default_factory=dict)
    #: Building reference areas for per-area figures (W4.2).
    reference_areas: ReferenceAreas = field(default_factory=ReferenceAreas)
    #: Share of a year the simulation covered; the divisor behind the quantities above (§3.6).
    simulated_period_fraction: float = 1.0
    #: The year the simulation this result prices was run for — the report header's "Simulation
    #: year" and the fallback price basis of the degenerate-band note. Carried here (W4.6) so
    #: presentation needs no `EvaluationInputs`.
    simulation_year: Optional[int] = None
    #: Per-carrier §8.5 flexibility value of the year-1 bill *before* the clamp the projection
    #: applies (key = `EnergyCarrier.value`). Diagnostics, not a published figure: a negative
    #: entry means the load was timed worse than a flat profile and is what the plausibility
    #: panel's flexibility check reads (issue #25b).
    raw_flexibility_value_by_carrier: Dict[str, float] = field(default_factory=dict)

    def scoped_timeline(self) -> CashFlowTimeline:
        """The flows this perspective actually reports on (filtered by `scope_payer`).

        The single definition of scoping, shared with `calculators/aggregation.py` and with
        `explain()` — having two would let a KPI and its explanation disagree, which is exactly
        the defect §7 B4 records. A SYSTEM-scope result returns everything, so callers need not
        special-case the unallocated view.
        """
        return self.timeline.scoped_to(self.scope_payer)

    # ------------------------------------------------------------------ provenance (§3.10)

    def explain(self, value_path: str) -> ProvenanceReport:
        """Lineage of any result value: a filter of timeline entries plus their parameters.

        Accepted paths: ``total_npv_in_euro``, ``equivalent_annual_cost_in_euro``,
        ``npv_by_category[CATEGORY]``, ``npv_by_component[subject]``, ``npv_by_payer[actor]``.
        (`_entries_for_path` additionally resolves ``monthly_cost_year1_in_euro``, which is the
        same scoped entry set as the NPV.)

        This is the §3.10 traceability guarantee in code: because every published figure is a
        pivot of the timeline, its lineage is a set union rather than a second mechanism. The
        report walks value -> contributing cash-flow entries -> the `ParameterProvenance` records
        those entries were built from -> the resolved sources with citation, url and retrieval
        date. It is what backs `python -m hisim.economics explain`, and it works offline on an
        archived result directory years later, since ledger and result are stored side by side.

        Source ids of the form ``inline:…`` do not live in any registry — they are values the
        engine itself introduced (a scenario overlay, an override's `override_source`) — and are
        materialized as `INLINE` sources rather than dropped, so no leaf of the report is silently
        empty.

        Args:
            value_path: One of the paths above. Addressing uses the result field names, the same
                names the exports use; there is no separate query language.

        Returns:
            A `ProvenanceReport` renderable as text or JSON. Its `value` is None when the path is
            valid but the result carries no such key (e.g. a category that never occurred).

        Raises:
            KeyError: on an unknown container or an unknown value path.
        """
        entries = self._entries_for_path(value_path)
        value = self._value_for_path(value_path)
        report = ProvenanceReport(value_path=f"{self.perspective_id}/{value_path}", value=value)
        source_ids: List[str] = []
        for entry in entries:
            parameters = [self.ledger.get(record_id) for record_id in entry.provenance_ids] if self.ledger else []
            report.entries.append(
                ProvenanceReportEntry(
                    year=entry.year,
                    category=entry.category.value,
                    subject=entry.subject,
                    amount=entry.amount_in_euro,
                    parameters=parameters,
                )
            )
            for parameter in parameters:
                source_ids.extend(parameter.source_ids)
        if self.source_resolver:
            seen = set()
            for source_id in source_ids:
                if source_id in seen:
                    continue
                seen.add(source_id)
                if source_id.startswith("inline:"):
                    report.sources.append(
                        ResolvedSource(
                            source_id=source_id,
                            citation=source_id[len("inline:"):],
                            url=None,
                            publication_year=None,
                            retrieved=None,
                            kind="INLINE",
                        )
                    )
                elif source_id in self.source_resolver:
                    report.sources.append(self.source_resolver[source_id])
        return report

    def _entries_for_path(self, value_path: str) -> List[CashFlowEntry]:
        """The entries a result value is made of — scoped exactly as the value itself is (B4).

        Every KPI except `npv_by_payer` is derived from the perspective's **scoped** timeline
        (`calculators/aggregation.aggregate_timeline`), so explaining one has to filter the same
        flows: until this was fixed, `explain("total_npv_in_euro")` on an actor-scoped
        perspective listed the whole allocated timeline, including entries the KPI never
        contained. `npv_by_payer` is the one pivot taken over *all* payers — it exists to show
        the split — and it stays on the full timeline, which for a given payer is the same set
        of entries either way.
        """
        scoped = self.scoped_timeline()
        bracket = re.match(r"(\w+)\[(.+)\]$", value_path)
        if bracket:
            container, key = bracket.group(1), bracket.group(2)
            if container == "npv_by_category":
                category = CostCategory(key)
                return [entry for entry in scoped.entries if entry.category == category]
            if container == "npv_by_component":
                return [entry for entry in scoped.entries if entry.subject == key]
            if container == "npv_by_payer":
                actor = Actor(key)
                return [entry for entry in self.timeline.entries if entry.payer == actor]
            raise KeyError(f"Unknown result container {container!r} in {value_path!r}.")
        if value_path in ("total_npv_in_euro", "equivalent_annual_cost_in_euro", "monthly_cost_year1_in_euro"):
            return list(scoped.entries)
        raise KeyError(f"Unknown result value path {value_path!r}.")

    def _value_for_path(self, value_path: str) -> Optional[UncertainValue]:
        """The value a path addresses, or None when the result has no such key or field.

        The lenient half of `explain`: unlike `_entries_for_path` it never raises, because by the
        time it runs the path has already been validated by the entry lookup — a container key
        that is simply absent from this perspective (a category that never occurred) is a legal
        answer of "no value", not an error. Only `UncertainValue` attributes are returned, so a
        path pointing at a non-monetary field yields None rather than an untyped object.
        """
        bracket = re.match(r"(\w+)\[(.+)\]$", value_path)
        if bracket:
            container, key = bracket.group(1), bracket.group(2)
            if container == "npv_by_category":
                return self.npv_by_category.get(CostCategory(key))
            if container == "npv_by_component":
                return self.npv_by_component.get(key)
            if container == "npv_by_payer":
                return self.npv_by_payer.get(Actor(key))
        attribute = getattr(self, value_path, None)
        return attribute if isinstance(attribute, UncertainValue) else None

    def to_json(self) -> dict:
        """Serialization for lifecycle_costs.json (without the ledger — stored separately).

        The ledger goes to its own `cost_provenance.json` because it is large and is addressed by
        (perspective, field) rather than embedded per value (§3.10) — a KPI payload for the webtool
        must stay small, and `explain` resolves against the stored ledger instead. The timeline is
        likewise omitted here: it has its own `cash_flow_timeline.csv`.

        Growth of this payload has been strictly additive (the W4.2 physical quantities, the W4.6
        scope and simulation year), so a consumer written against an older file keeps working.
        """
        return {
            "perspective": self.perspective_id,
            "parameters": self.parameters.to_dict(),
            "total_npv_in_euro": self.total_npv_in_euro.to_json(),
            "equivalent_annual_cost_in_euro": self.equivalent_annual_cost_in_euro.to_json(),
            "npv_by_category": {category.value: value.to_json() for category, value in self.npv_by_category.items()},
            "npv_by_component": {subject: value.to_json() for subject, value in self.npv_by_component.items()},
            "npv_by_payer": {actor.value: value.to_json() for actor, value in self.npv_by_payer.items()},
            "annual_cost_series_nominal_in_euro": [
                value.to_json() for value in self.annual_cost_series_nominal_in_euro
            ],
            "monthly_cost_year1_in_euro": self.monthly_cost_year1_in_euro.to_json()
            if self.monthly_cost_year1_in_euro
            else None,
            "levelized_cost_of_heat_in_euro_per_kwh": self.levelized_cost_of_heat_in_euro_per_kwh.to_json()
            if self.levelized_cost_of_heat_in_euro_per_kwh
            else None,
            "sunk_cost_written_off_in_euro": self.sunk_cost_written_off_in_euro.to_json(),
            "lifecycle_co2": self.lifecycle_co2_result.to_json(),
            "subsidy_decisions": [decision.to_json() for decision in self.subsidy_decisions],
            "component_breakdowns": {
                subject: breakdown.to_json() for subject, breakdown in self.component_breakdowns.items()
            },
            # Additive since W4.2 — the physical quantities the derived views and the
            # plausibility report are computed from:
            "annual_energy_quantities_by_carrier": {
                carrier: quantities.to_json()
                for carrier, quantities in self.annual_energy_quantities_by_carrier.items()
            },
            "reference_areas": self.reference_areas.to_json(),
            "simulated_period_fraction": self.simulated_period_fraction,
            # Additive since W4.6 — the scope and the simulation year presentation needs so it
            # can render from a stored result alone (W4.5).
            "scope_payer": self.scope_payer.value,
            "simulation_year": self.simulation_year,
            # Diagnostics travelling with the result so a stored evaluation can be re-checked
            # without re-pricing it (issue #25b):
            "raw_flexibility_value_by_carrier": dict(self.raw_flexibility_value_by_carrier),
        }


@dataclass
class EvaluationMatrix:
    """{perspective -> LifecycleCostResult} for one variant (§3.1).

    The unit of output of one run: `EconomicEvaluator.evaluate_matrix` fills it with one entry per
    applicable perspective, and it is what `exports.py` writes and `reporting.py` renders. The
    rows are independent evaluations of the *same* variant, so comparing two rows compares two
    accounting frames (owner vs. tenant, with vs. without subsidies), never two buildings —
    comparing buildings is `compare()` below, across two matrices.
    """

    results: Dict[str, LifecycleCostResult] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialization for lifecycle_costs.json.

        Insertion order is the bundle order, which is what makes the report's perspective tables
        and whisker charts stable across runs.
        """
        return {perspective: result.to_json() for perspective, result in self.results.items()}


@dataclass
class VariantComparison:
    """Differential analysis of two variants (§3.7): the RenoVisor base-vs-measures case.

    The answer to "is the retrofit worth it", which is a *difference* question and not a totals
    question: what matters is variant minus reference, under one and the same perspective and one
    and the same economic world. Produced by `compare()`, consumed by the report's comparison
    section (delta waterfall, payback curve, warm-rent change) and by the scenario layer's
    robustness statements.

    Sign and slot conventions, which every field below follows: deltas are **variant − reference**
    and slot-wise, so a *negative* NPV delta means the variant is cheaper; payback and
    warm-rent-neutrality are reported per slot (`"low"`, `"best_estimate"`, `"high"`) because a
    retrofit can pay back in the optimistic world and never in the pessimistic one, and saying so
    is the honest statement.
    """

    reference_id: str
    variant_id: str
    perspective_id: str
    npv_delta_in_euro: UncertainValue  # variant - reference, slot-wise
    equivalent_annual_cost_delta_in_euro: UncertainValue
    npv_delta_by_subject: Dict[str, UncertainValue]
    # Discounted payback per slot; each independently None-able ("never within horizon"):
    discounted_payback_years: Dict[str, Optional[int]] = field(default_factory=dict)
    warm_rent_change_per_month_in_euro: Optional[UncertainValue] = None
    warm_rent_neutral_per_slot: Dict[str, bool] = field(default_factory=dict)
    #: The payback *curve* per slot: cumulative discounted savings (reference - variant) for
    #: years 0..T, index = year (W4.4). `discounted_payback_years` is its zero-crossing, so the
    #: curve a report draws and the number it prints cannot disagree.
    cumulative_discounted_savings_in_euro: Dict[str, List[float]] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialization."""
        return {
            "reference": self.reference_id,
            "variant": self.variant_id,
            "perspective": self.perspective_id,
            "npv_delta_in_euro": self.npv_delta_in_euro.to_json(),
            "equivalent_annual_cost_delta_in_euro": self.equivalent_annual_cost_delta_in_euro.to_json(),
            "npv_delta_by_subject": {subject: value.to_json() for subject, value in self.npv_delta_by_subject.items()},
            "discounted_payback_years": self.discounted_payback_years,
            "warm_rent_change_per_month_in_euro": self.warm_rent_change_per_month_in_euro.to_json()
            if self.warm_rent_change_per_month_in_euro
            else None,
            "warm_rent_neutral_per_slot": self.warm_rent_neutral_per_slot,
            "cumulative_discounted_savings_in_euro": self.cumulative_discounted_savings_in_euro,
        }


def _subject_alignment_key(result: LifecycleCostResult, subject: str) -> str:
    """Aligns subjects across variants by (asset_class, subject) (§3.7).

    Two variants name their subjects independently, so the per-subject delta needs a rule for
    deciding what is "the same thing" on both sides. Keying on the asset class *and* the subject
    name — rather than on the name alone — is what lets the frontend show "which component drives
    the difference" without name-matching heuristics, and it keeps a heat pump from being aligned
    with a boiler that happens to share a component name. Subjects present in only one variant
    still get a row, compared against an explicit zero in `compare`.
    """
    breakdown = result.component_breakdowns.get(subject)
    asset_class = breakdown.asset_class.value if breakdown and breakdown.asset_class else ""
    return f"{asset_class}|{subject}"


class _SlotAccessors:
    """The three evaluation worlds as (slot name, band accessor), in the order comparisons
    report them. The names match `VariantComparison.discounted_payback_years`."""

    BY_SLOT = (
        ("low", lambda band: band.minimum),
        ("best_estimate", lambda band: band.best_estimate),
        ("high", lambda band: band.maximum),
    )


def cumulative_discounted_savings(
    reference: LifecycleCostResult, variant: LifecycleCostResult
) -> Dict[str, List[float]]:
    """The payback curve per slot: cumulative discounted (reference - variant) per year (W4.4).

    Index = year 0..T; the last value equals the NPV saving of that slot. Discounting goes
    through the canonical `timeline.discount_factor` (W4.3). Missing years — a shorter series
    on either side — count as zero flow, as they did before this moved out of `compare`.

    Replaces the loops in `results.compare` (was `results.py:303-323`), `reporting._payback_svg`
    (`reporting.py:882-895`) and `report_plots.plot_payback_curve` (`report_plots.py:216-229`),
    which each re-derived it.
    """
    interest = variant.parameters.interest_rate
    horizon = variant.parameters.observation_period_in_years
    reference_series = reference.annual_cost_series_nominal_in_euro
    variant_series = variant.annual_cost_series_nominal_in_euro
    curves: Dict[str, List[float]] = {}
    for slot_name, getter in _SlotAccessors.BY_SLOT:
        cumulative = 0.0
        curve: List[float] = []
        for year in range(0, horizon + 1):
            reference_amount = getter(reference_series[year]) if year < len(reference_series) else 0.0
            variant_amount = getter(variant_series[year]) if year < len(variant_series) else 0.0
            cumulative += (reference_amount - variant_amount) * discount_factor(interest, year)
            curve.append(cumulative)
        curves[slot_name] = curve
    return curves


def discounted_payback_year(cumulative_savings: List[float]) -> Optional[int]:
    """First year > 0 at which a payback curve reaches zero; None = never within the horizon.

    The zero-crossing of the curve `cumulative_discounted_savings` produced, which is why the two
    live next to each other: a report that draws the curve and prints the year cannot show a
    crossing the number contradicts (W4.4). Year 0 is excluded because that is the investment year
    itself — a variant that costs nothing extra up front would otherwise "pay back" immediately.

    Only the *first* crossing is reported: a curve that dips back below zero later (a big
    replacement inside the horizon) is not reflected here, which is the usual convention and the
    reason the payback figure is a coarse robustness indicator rather than a decision KPI.

    Returns:
        The year, or None when the curve never reaches zero within the horizon — a legitimate
        result that the band reports per slot, so "pays back in the optimistic world only" is
        expressible.
    """
    for year, value in enumerate(cumulative_savings):
        if year > 0 and value >= 0:
            return year
    return None


def compare(
    reference: LifecycleCostResult,
    variant: LifecycleCostResult,
    reference_id: str = "reference",
    variant_id: str = "variant",
) -> VariantComparison:
    """Differential NPV, differential annuity, discounted payback, warm-rent change (§3.7, §6.5).

    All deltas are slot-wise: reference and variant are compared within the same
    LOW/BEST_ESTIMATE/HIGH world, so shared cost uncertainty cancels.

    **Why slot-wise and not on averages, and not on the bands as intervals.** The two variants
    share most of their uncertain inputs — the same gas price band, the same maintenance rates,
    often the same devices. Differencing the *averages* would throw the band away and report a
    single number as if it were certain; differencing the bands as intervals would add the two
    widths and inflate a delta that is largely common-mode. Comparing within one slot keeps each
    world internally consistent (both variants priced with gas at its high end, say), so what
    remains in the delta band is the uncertainty the two variants genuinely do *not* share. The
    caveat is stated in §3.9 and is worth repeating: slot-wise deltas are three coherent
    scenarios, not an outer envelope of the difference — an extremal delta could occur in a mixed
    world (gas at max *while* the heat pump comes in cheap), and cross-parameter questions of that
    kind belong to the scenario axes and the break-even search (§4.6).

    The same reasoning drives the two derived figures: the discounted payback is the zero-crossing
    of each slot's own savings curve (so payback is a band, each slot independently "never"), and
    warm-rent neutrality is evaluated per slot, where neutrality in the HIGH slot — "neutral even
    if everything comes in expensive" — is the robust policy statement (§6.5).

    Args:
        reference: The "do nothing" / base variant's result.
        variant: The measures variant's result, evaluated under the *same* perspective; its
            `parameters` supply the interest rate, horizon and annuity factor used throughout.
        reference_id: Label for the reference, carried into the comparison and its exports.
        variant_id: Label for the variant.

    Returns:
        A `VariantComparison`. The warm-rent fields stay None unless *both* results carry a TENANT
        payer NPV, i.e. unless the perspective is one of the rented ones (§6.5).
    """
    npv_delta = variant.total_npv_in_euro - reference.total_npv_in_euro
    eac_delta = variant.equivalent_annual_cost_in_euro - reference.equivalent_annual_cost_in_euro

    # Subject alignment with explicit zeros for one-sided subjects (§3.7).
    keys = {}
    for result in (reference, variant):
        for subject in result.npv_by_component:
            keys[_subject_alignment_key(result, subject)] = subject
    npv_delta_by_subject = {}
    zero = UncertainValue.exact(0.0)
    for _key, subject in sorted(keys.items()):
        reference_value = reference.npv_by_component.get(subject, zero)
        variant_value = variant.npv_by_component.get(subject, zero)
        npv_delta_by_subject[subject] = variant_value - reference_value

    savings = cumulative_discounted_savings(reference, variant)
    payback = {slot: discounted_payback_year(series) for slot, series in savings.items()}

    comparison = VariantComparison(
        reference_id=reference_id,
        variant_id=variant_id,
        perspective_id=variant.perspective_id,
        npv_delta_in_euro=npv_delta,
        equivalent_annual_cost_delta_in_euro=eac_delta,
        npv_delta_by_subject=npv_delta_by_subject,
        discounted_payback_years=payback,
        cumulative_discounted_savings_in_euro=savings,
    )

    # Warm-rent neutrality (§6.5): only meaningful for tenant-scope results. No discounting
    # here — the annuity factor spreads an NPV over the horizon; it is the §3.4 counterpart of
    # `discount_factor` and lives on `EconomicParameters` for the same reason.
    tenant_reference = reference.npv_by_payer.get(Actor.TENANT)
    tenant_variant = variant.npv_by_payer.get(Actor.TENANT)
    if tenant_reference is not None and tenant_variant is not None:
        annuity_factor = variant.parameters.annuity_factor()
        delta_per_month = (tenant_variant - tenant_reference).scale(annuity_factor / 12.0)
        comparison.warm_rent_change_per_month_in_euro = delta_per_month
        comparison.warm_rent_neutral_per_slot = {
            "low": delta_per_month.minimum <= 0,
            "best_estimate": delta_per_month.best_estimate <= 0,
            "high": delta_per_month.maximum <= 0,
        }
    return comparison
