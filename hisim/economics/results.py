"""Typed result objects of the lifecycle cost engine (cost_spec.md §3.7, §3.8, §7).

`LifecycleCostResult` is what `EconomicEvaluator.evaluate` returns and what every exporter, view, report and CLI
command reads; CSV and JSON are export formats, never an internal API. The module also holds the comparison arithmetic
between two results (`compare`, `cumulative_discounted_savings`), because presentation never computes. Every money
field is a LOW/BEST_ESTIMATE/HIGH band (`UncertainValue`, §3.9) whose bounds are envelopes, not quantiles. Signs: cost
positive, revenue and support negative; display figures that show support as positive say so in their names.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.economics.parameters import EconomicParameters
from hisim.economics.provenance import (
    ProvenanceLedger,
    ProvenanceReport,
    ProvenanceReportEntry,
    ResolvedSource,
)
from hisim.economics.subsidies import SubsidyDecision
from hisim.economics.tariffs import FeedInKind, TariffContract
from hisim.economics.timeline import (
    Actor,
    CashFlowEntry,
    CashFlowTimeline,
    CostCategory,
    SubjectKind,
    discount_factor,
)
from hisim.economics.uncertainty import Slot, UncertainValue
from hisim.loadtypes import ComponentType
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass


@dataclass
class ComponentCostBreakdown:
    """Costs of one timeline subject, pivoted from the canonical timeline (§3.7, §7.4).

    A subject is what a cash-flow entry is booked under: a component, an energy carrier, or a synthetic subject such as
    `financing`, `replacement reserve` or `co2 damage`. Because it is a pivot, the breakdowns sum exactly to the
    perspective totals. `npv_by_category` and the two NPV totals are discounted and signed (support and residual values
    negative); the three fields after them are undiscounted display figures where support is positive and separate from
    the gross investment.
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
    #: Support received for this subject, **nominal** euros summed across years, positive band.
    #: The same unit the §6.4 levy basis deducts.
    subsidies_nominal_in_euro: UncertainValue
    #: The same support **discounted** to present value, positive band — the exact mirror of
    #: `npv_by_category[SUBSIDY]`, which is negative-signed.
    subsidies_npv_in_euro: UncertainValue
    annual_cost_series_nominal_in_euro: List[UncertainValue]
    lifecycle_co2_in_kg: float

    def to_json(self) -> dict:
        """Serialize for component_costs.json (§7.4); every band keeps its min/best_estimate/max triplet."""
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


@dataclass(frozen=True)
class EmbodiedCo2Basis:
    """The multiplication behind one subject's embodied CO2: factor x size, once per installation.

    Lets the CO2 section print `factor x size = kg per installation` and multiply by the number of installations.
    `size` is the installed size (`facts.size x facts.count`, so three identical devices are one record of three
    units). `factor_in_kg_per_unit` is always `per_installation_in_kg / size`, also when the data states an absolute
    mass. `installations` counts the year-0 installation plus every replacement within the horizon. `__post_init__`
    enforces the identity, since the section prints it as a multiplication a reader can check.
    """

    #: Tolerance of the `factor x size = per installation` check, in kg. Absolute rather than
    #: relative because the product is a float division multiplied straight back out, so the only
    #: error it can carry is the rounding of one division — orders of magnitude below a gram.
    IDENTITY_TOLERANCE_IN_KG: ClassVar[float] = 1e-6

    factor_in_kg_per_unit: float
    size: float
    size_unit: str
    per_installation_in_kg: float
    installations: int = 1

    def __post_init__(self) -> None:
        """Refuse a record whose factor, size and mass do not multiply out.

        Raises:
            ValueError: If `abs(factor_in_kg_per_unit * size - per_installation_in_kg)` exceeds
                `IDENTITY_TOLERANCE_IN_KG`.
        """
        product = self.factor_in_kg_per_unit * self.size
        if abs(product - self.per_installation_in_kg) > self.IDENTITY_TOLERANCE_IN_KG:
            raise ValueError(
                f"EmbodiedCo2Basis states {self.factor_in_kg_per_unit:g} kg per {self.size_unit} "
                f"x {self.size:g} {self.size_unit} = {product:g} kg, but carries "
                f"{self.per_installation_in_kg:g} kg per installation. The record is printed as "
                "that multiplication, so the three numbers have to be one statement."
            )

    def to_json(self) -> dict:
        """Serialize for lifecycle_costs.json."""
        return {
            "factor_in_kg_per_unit": self.factor_in_kg_per_unit,
            "size": self.size,
            "size_unit": self.size_unit,
            "per_installation_in_kg": self.per_installation_in_kg,
            "installations": self.installations,
        }

    @staticmethod
    def from_json(raw: dict) -> "EmbodiedCo2Basis":
        """Inverse of `to_json`."""
        return EmbodiedCo2Basis(
            factor_in_kg_per_unit=float(raw["factor_in_kg_per_unit"]),
            size=float(raw["size"]),
            size_unit=str(raw.get("size_unit", "")),
            per_installation_in_kg=float(raw["per_installation_in_kg"]),
            installations=int(raw.get("installations", 1)),
        )


@dataclass
class LifecycleCo2Result:
    """Lifecycle CO2 masses of one evaluation, undiscounted and in kilograms (§3.8).

    Runs alongside the money in one build: `calculators/investment.py` adds the embodied mass at installation and every
    replacement, `calculators/energy.py` the operational mass per carrier, and `calculators/co2.py` adds them up.
    Nothing is discounted, because a physical quantity is summed over the horizon. The CO2 damage cost (a macroeconomic
    euro figure) and the CO2 price (a real cash flow) are on the timeline and are never added together. Emission
    factors are constant over the horizon; grid decarbonization is not modelled.
    """

    embodied_co2_in_kg: float = 0.0  # install + replacements, no discounting
    operational_co2_by_year_in_kg: List[float] = field(default_factory=list)  # index = year 1..T
    operational_co2_by_carrier_in_kg: Dict[str, float] = field(default_factory=dict)
    total_co2_in_kg: float = 0.0
    embodied_by_subject_in_kg: Dict[str, float] = field(default_factory=dict)
    #: The conversion factors behind the two mass maps above, so the report can state every mass
    #: as one visible multiplication instead of asserting it. Keyed like the maps they explain —
    #: carrier value for the operational factors, timeline subject for the embodied ones. Empty
    #: for a result serialized before the fields existed, in which case the factors table is
    #: skipped rather than reconstructed by division.
    emission_factor_by_carrier_in_kg_per_kwh: Dict[str, float] = field(default_factory=dict)
    embodied_basis_by_subject: Dict[str, "EmbodiedCo2Basis"] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialize for lifecycle_costs.json.

        The operational by-year array is kg per year indexed 0..T (year 0 is zero); the by-carrier map is kg over the
        whole horizon.
        """
        return {
            "embodied_co2_in_kg": self.embodied_co2_in_kg,
            "operational_co2_by_year_in_kg": self.operational_co2_by_year_in_kg,
            "operational_co2_by_carrier_in_kg": self.operational_co2_by_carrier_in_kg,
            "total_co2_in_kg": self.total_co2_in_kg,
            "embodied_by_subject_in_kg": self.embodied_by_subject_in_kg,
            "emission_factor_by_carrier_in_kg_per_kwh": dict(self.emission_factor_by_carrier_in_kg_per_kwh),
            "embodied_basis_by_subject": {
                subject: basis.to_json() for subject, basis in self.embodied_basis_by_subject.items()
            },
        }


@dataclass(frozen=True)
class AnnualEnergyQuantities:
    """One carrier's annualized energy volumes, as the engine priced them.

    Simulated quantities scaled to a full year (§3.6 rule 5, `calculators/annualization.py`), so per-unit figures
    derived from them (EUR/kWh, EUR/m²a) are per year. Carried on the result so views and plausibility checks need not
    read `EvaluationInputs`.
    """

    bought_in_kwh: float
    sold_in_kwh: float = 0.0

    def to_json(self) -> dict:
        """Serialize for lifecycle_costs.json."""
        return {"bought_in_kwh": self.bought_in_kwh, "sold_in_kwh": self.sold_in_kwh}


def _slot_from_json(name: str, context: str) -> Slot:
    """Parse one `Slot` value from a stored map key.

    Args:
        name: The key as the file spells it (`low`, `best_estimate` or `high`).
        context: Dotted path of the field being parsed, used in the error message.

    Returns:
        The slot the key names.

    Raises:
        ValueError: If the key is not a known slot.
    """
    try:
        return Slot(name)
    except ValueError:
        raise ValueError(
            f"{context} is keyed by evaluation slot, and {name!r} is not one of "
            f"{', '.join(slot.value for slot in Slot)}. A value filed under an unknown slot is a "
            "value nothing will ever read."
        ) from None


class LevyBindingMechanism:
    """The wording of the per-slot verdicts on what decided a German modernization levy.

    The modernization levy is the rent increase a landlord may charge after a modernization (§559/§559e BGB). Below a
    statutory ceiling it is a percentage of the cost; at a ceiling the figure is fixed by law, and spending more would
    not raise the rent. `actors.DE2024Ruleset` writes one verdict per band slot and `ModernizationLevySummary` reads it
    back, so both use these constants. Two caps can cut the same slot (the §559e ceiling on the heating part and the
    §559 Abs. 3a ceiling on both together); each cap that bound is named, joined by `BOTH_CAPS_JOINER`, so `startswith`
    on either cap constant tests whether that cap bound. `SLOT_NAMES` maps band field names to `Slot` names.
    """

    GENERAL_CAP = "§559 general cap"
    HEATING_CAP = "§559e heating cap"
    RATE_BELOW_CAP = "of eligible cost, below cap"
    #: What joins the two cap clauses in a world where both ceilings removed euros.
    BOTH_CAPS_JOINER = " and "
    #: Euro per year below which a cap counts as not having removed anything.
    TOLERANCE_IN_EURO = 1e-6
    SLOT_NAMES = {
        "minimum": Slot.LOW,
        "best_estimate": Slot.BEST_ESTIMATE,
        "maximum": Slot.HIGH,
    }

    @staticmethod
    def names_a_cap(verdict: str) -> bool:
        """Whether this verdict says a statutory ceiling decided the levy.

        Args:
            verdict: One value of `ModernizationLevySummary.binding_mechanism_by_slot`.

        Returns:
            True for a verdict naming either cap or both; False for the rate verdict.
        """
        return verdict.startswith(
            (LevyBindingMechanism.HEATING_CAP, LevyBindingMechanism.GENERAL_CAP)
        )


@dataclass(frozen=True)
class ModernizationLevySummary:
    """The §559/§559e modernization levy facts the report states beyond its euro amount.

    The levy's amount is on the timeline as a transfer between tenant and landlord, but whether a statutory ceiling
    decided it, and which, is not visible in a cash flow. At the cap, the same renovation costing 20 % more yields the
    same rent increase. Carried on the result so a stored `lifecycle_costs.json` is enough to render the report. `None`
    for every perspective without a levy (an owner-occupier run, a country whose ruleset has none).
    """

    annual_amount_in_euro: UncertainValue
    general_leg_in_euro: UncertainValue
    heating_leg_in_euro: UncertainValue
    #: The general §559 Abs. 3a ceiling in EUR per m² and month, when one was evaluated.
    cap_in_euro_per_m2_per_month: Optional[float] = None
    #: Which mechanism actually set the levy **in each world**, keyed by `Slot`. The caps are
    #: applied per slot, so the ceiling can decide the expensive world while the cheap one is
    #: still set by the percentage of the modernization cost — two economically different answers
    #: that a single best-estimate flag would hide. Empty for a result serialized before the field
    #: existed and for a run with no living area, in which case no cap could be evaluated at all
    #: and the report states the levy without a verdict.
    binding_mechanism_by_slot: Dict[Slot, str] = field(default_factory=dict)

    @property
    def cap_binding_in_best_estimate(self) -> bool:
        """Whether a statutory ceiling decided the best-estimate levy.

        Returns:
            True when the best-estimate verdict names a cap; False when it names the rate or when there is no verdict
                (no living area, no cap evaluated).
        """
        verdict = self.binding_mechanism_by_slot.get(Slot.BEST_ESTIMATE)
        return verdict is not None and LevyBindingMechanism.names_a_cap(verdict)

    def to_json(self) -> dict:
        """Serialize for lifecycle_costs.json."""
        return {
            "annual_amount_in_euro": self.annual_amount_in_euro.to_json(),
            "general_leg_in_euro": self.general_leg_in_euro.to_json(),
            "heating_leg_in_euro": self.heating_leg_in_euro.to_json(),
            "cap_in_euro_per_m2_per_month": self.cap_in_euro_per_m2_per_month,
            "binding_mechanism_by_slot": {
                slot.value: verdict for slot, verdict in self.binding_mechanism_by_slot.items()
            },
        }

    @staticmethod
    def from_json(raw: Optional[dict]) -> Optional["ModernizationLevySummary"]:
        """Inverse of `to_json`; `None` stays `None` (a run without a levy).

        Raises:
            ValueError: If a verdict is filed under an unknown slot name, which no reader would ever see.
        """
        if not raw:
            return None
        return ModernizationLevySummary(
            annual_amount_in_euro=UncertainValue.from_json(raw["annual_amount_in_euro"]),
            general_leg_in_euro=UncertainValue.from_json(raw["general_leg_in_euro"]),
            heating_leg_in_euro=UncertainValue.from_json(raw["heating_leg_in_euro"]),
            cap_in_euro_per_m2_per_month=raw.get("cap_in_euro_per_m2_per_month"),
            binding_mechanism_by_slot={
                _slot_from_json(name, "ModernizationLevySummary.binding_mechanism_by_slot"): verdict
                for name, verdict in raw.get("binding_mechanism_by_slot", {}).items()
            },
        )


class RateOrigin(str, enum.Enum):
    """The step of the §3.2 escalation fallback chain that produced a rate.

    The chain is: an explicit `EconomicParameters` entry, then the country's defaults table, then the general rate. The
    values are the serialized words, and a stored file with any other word is refused at load time.
    """

    CONFIGURATION = "configuration"
    COUNTRY_DEFAULTS = "country defaults"
    GENERAL_FALLBACK = "general fallback"


@dataclass(frozen=True)
class ResolvedRate:
    """One escalation rate as the run resolved it, with the step of the fallback chain that won (§3.2).

    The assumptions table must cite a source for every value, so the step travels with the number. `source_ids` are the
    §3.10 registry ids of the defaults file when that step won, and empty for a configured or general rate, which the
    report shows as "configuration".
    """

    rate: float
    origin: RateOrigin
    source_ids: List[str] = field(default_factory=list)

    def to_json(self) -> dict:
        """Serialize for lifecycle_costs.json."""
        return {"rate": self.rate, "origin": self.origin.value, "source_ids": list(self.source_ids)}

    @staticmethod
    def from_json(raw: dict) -> "ResolvedRate":
        """Inverse of `to_json`.

        Raises:
            ValueError: If `origin` is not a step of the §3.2 chain.
        """
        origin = raw.get("origin", RateOrigin.CONFIGURATION.value)
        try:
            resolved_origin = RateOrigin(origin)
        except ValueError:
            raise ValueError(
                f"ResolvedRate.origin {origin!r} is not a step of the escalation fallback chain "
                f"({', '.join(step.value for step in RateOrigin)}); the assumptions table cites "
                "the step, so an unknown one would be published as a source it is not."
            ) from None
        return ResolvedRate(
            rate=float(raw["rate"]),
            origin=resolved_origin,
            source_ids=list(raw.get("source_ids", [])),
        )


@dataclass(frozen=True)
class TariffAssumption:
    """The tariff terms one carrier was billed under, as the assumptions table states them.

    Holds the working price per kWh, the annual standing charge and the feed-in rate per exported kWh, plus the
    contract they came from, so a reader can reproduce an energy bill. Carried on the result because the contracts live
    in `EvaluationInputs`, which presentation may not read. For a carrier billed under the flat contract generated by
    `calculators/energy.py`, `contract_id` is its synthetic id and `is_default_contract` is True, shown as "database
    price entry". The feed-in rate is present exactly when `feed_in_kind` is not `NONE`; `__post_init__` enforces this.
    """

    carrier: str
    contract_id: str
    working_price_in_euro_per_kwh: UncertainValue
    standing_charge_in_euro_per_year: UncertainValue
    feed_in_kind: FeedInKind = FeedInKind.NONE
    feed_in_rate_in_euro_per_kwh: Optional[UncertainValue] = None
    is_default_contract: bool = False
    source_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Refuse a feed-in kind and a feed-in rate that do not agree.

        Raises:
            ValueError: If a remunerated kind carries no rate, or a rate is carried under `FeedInKind.NONE`.
        """
        has_rate = self.feed_in_rate_in_euro_per_kwh is not None
        remunerated = self.feed_in_kind != FeedInKind.NONE
        if has_rate != remunerated:
            raise ValueError(
                f"TariffAssumption for {self.carrier!r} states feed_in_kind "
                f"{self.feed_in_kind.value} with "
                f"{'a' if has_rate else 'no'} feed-in rate. The rate is present exactly when the "
                "kind is not NONE: the assumptions table prints the two as one row, so either "
                "half alone is a row that cannot be read."
            )

    @staticmethod
    def from_contract(contract: TariffContract) -> "TariffAssumption":
        """Build the assumption record for one billed contract; the only place it is built.

        Args:
            contract: The contract `calculators/energy.py` billed the carrier under, authored or generated from the
                price entries.

        Returns:
            The record for `EconomicAssumptions.tariffs`, keyed by the carrier's value.
        """
        feed_in = contract.feed_in
        remunerated = feed_in.kind != FeedInKind.NONE
        return TariffAssumption(
            carrier=contract.carrier.value,
            contract_id=contract.id,
            working_price_in_euro_per_kwh=contract.supply.working_price_in_euro_per_kwh,
            standing_charge_in_euro_per_year=contract.standing_charge_in_euro_per_year,
            feed_in_kind=feed_in.kind,
            feed_in_rate_in_euro_per_kwh=feed_in.rate_in_euro_per_kwh if remunerated else None,
            is_default_contract=contract.is_default_contract,
            source_ids=list(contract.source_ids),
        )

    def to_json(self) -> dict:
        """Serialize for lifecycle_costs.json."""
        return {
            "carrier": self.carrier,
            "contract_id": self.contract_id,
            "working_price_in_euro_per_kwh": self.working_price_in_euro_per_kwh.to_json(),
            "standing_charge_in_euro_per_year": self.standing_charge_in_euro_per_year.to_json(),
            "feed_in_kind": self.feed_in_kind.value,
            "feed_in_rate_in_euro_per_kwh": (
                self.feed_in_rate_in_euro_per_kwh.to_json()
                if self.feed_in_rate_in_euro_per_kwh is not None
                else None
            ),
            "is_default_contract": self.is_default_contract,
            "source_ids": list(self.source_ids),
        }

    @staticmethod
    def from_json(raw: dict) -> "TariffAssumption":
        """Inverse of `to_json`.

        Raises:
            ValueError: If `feed_in_kind` is not a `FeedInKind`, or the kind and the rate disagree.
        """
        feed_in = raw.get("feed_in_rate_in_euro_per_kwh")
        kind = raw.get("feed_in_kind", FeedInKind.NONE.value)
        try:
            feed_in_kind = FeedInKind(kind)
        except ValueError:
            raise ValueError(
                f"TariffAssumption.feed_in_kind {kind!r} is not a feed-in remuneration structure "
                f"({', '.join(item.value for item in FeedInKind)}); the kind selects how the "
                "export revenue was computed, so an unknown one cannot be republished as a fact."
            ) from None
        return TariffAssumption(
            carrier=raw["carrier"],
            contract_id=raw.get("contract_id", ""),
            working_price_in_euro_per_kwh=UncertainValue.from_json(raw["working_price_in_euro_per_kwh"]),
            standing_charge_in_euro_per_year=UncertainValue.from_json(raw["standing_charge_in_euro_per_year"]),
            feed_in_kind=feed_in_kind,
            feed_in_rate_in_euro_per_kwh=UncertainValue.from_json(feed_in) if feed_in is not None else None,
            is_default_contract=bool(raw.get("is_default_contract", False)),
            source_ids=list(raw.get("source_ids", [])),
        )


@dataclass(frozen=True)
class EconomicAssumptions:
    """The economic assumptions the evaluation resolved inside the engine, for the report's assumptions section.

    Holds the escalation rates as resolved through their fallback chains, the tariff terms per carrier, and the annual
    heat demand the heat-cost figure divides by. These are resolved against the cost database and `EvaluationInputs`,
    which presentation may not read. Values already on `EconomicParameters` (interest rate, horizon, price basis year)
    are not copied.

    Escalation rates are keyed `general`, `investment`, `feed-in`, `energy:<carrier value>` or `investment:<asset class
    name>`; tariffs are keyed by carrier value. `annual_heat_demand_in_kwh` is, for one evaluation, the declared demand
    or the measured useful heat, annualized; for a staged plan it is the equivalent annual heat (the annuity factor
    times the discounted sum of each year's heat from the stage active then,
    `StagedEvaluator._equivalent_annual_heat`).
    """

    escalation_rates: Dict[str, ResolvedRate] = field(default_factory=dict)
    tariffs: Dict[str, TariffAssumption] = field(default_factory=dict)
    annual_heat_demand_in_kwh: Optional[float] = None

    def to_json(self) -> dict:
        """Serialize for lifecycle_costs.json."""
        return {
            "escalation_rates": {label: rate.to_json() for label, rate in self.escalation_rates.items()},
            "tariffs": {carrier: tariff.to_json() for carrier, tariff in self.tariffs.items()},
            "annual_heat_demand_in_kwh": self.annual_heat_demand_in_kwh,
        }

    @staticmethod
    def from_json(raw: Optional[dict]) -> Optional["EconomicAssumptions"]:
        """Inverse of `to_json`; `None` stays `None` (a result without the field)."""
        if not raw:
            return None
        return EconomicAssumptions(
            escalation_rates={
                label: ResolvedRate.from_json(value) for label, value in raw.get("escalation_rates", {}).items()
            },
            tariffs={
                carrier: TariffAssumption.from_json(value) for carrier, value in raw.get("tariffs", {}).items()
            },
            annual_heat_demand_in_kwh=raw.get("annual_heat_demand_in_kwh"),
        )


@dataclass(frozen=True)
class ReferenceAreas:
    """The building areas per-area KPIs divide by (§6.3); both optional, both in m².

    Heated floor area is the physics reference of the building model; living area is the legal reference of German rent
    and levy rules (§6.4). When both are absent, per-area figures are not reported.
    """

    heated_floor_area_in_m2: Optional[float] = None
    living_area_in_m2: Optional[float] = None

    def preferred(self) -> Optional[float]:
        """Return the area per-area figures use: living area when known, else heated floor area."""
        return self.living_area_in_m2 or self.heated_floor_area_in_m2

    def to_json(self) -> dict:
        """Serialize for lifecycle_costs.json."""
        return {
            "heated_floor_area_in_m2": self.heated_floor_area_in_m2,
            "living_area_in_m2": self.living_area_in_m2,
        }


class AnywayBasisKinds:
    """Names for the basis of an anyway credit, one per way a credit arises.

    An anyway credit is the cost a measure avoids because the building would have paid it anyway (§4.1): `share x basis
    = credit`. For a like-for-like replacement the basis is the escalated cost of replacing the old asset with its own
    kind. For a coupled measure (`energy_related_cost_share < 1`) it is the non-energy share of the new measure's gross
    cost, such as the scaffolding and render a facade job needs even without insulation. The kind travels with the
    basis so captions can word it from a stored file. `UNRECORDED` is what a result without the field says.
    """

    #: The escalated like-for-like replacement cost of the asset being replaced.
    LIKE_FOR_LIKE = "like-for-like cost"
    #: The non-energy share of the new measure's own gross cost (a coupled measure).
    NON_ENERGY_SHARE = "non-energy share of the measure's gross cost"
    #: A stored result that carries a basis but no name for it.
    UNRECORDED = "basis"


class HeatCostNaming:
    """The display names of the heat-cost figure.

    The figure divides a perspective's whole NPV (PV and battery included) by the heat delivered, so it is not a
    levelized cost of heat (LCOH), which counts heating-related cost only. Every place a reader sees it says "system
    cost per unit of heat": the KPI in `lifecycle_kpis.json`, the report KPI and perspectives tables, the plausibility
    row and the derivation caption. The field `LifecycleCostResult.levelized_cost_of_heat_in_euro_per_kwh` keeps its
    name because it is the serialization key of stored results.
    """

    #: The published KPI name and the caption's lead-in.
    FULL = "System cost per unit of heat"
    #: Column-header form, for the perspectives table where the full name would not fit.
    COLUMN = "System cost/kWh heat"
    #: Lower-case form for the plausibility panel, whose check names are sentences-in-lower-case.
    CHECK_LABEL = "system cost per unit of heat"


@dataclass
class LifecycleCostResult:
    """The evaluation of one variant under one perspective (§3.7).

    A perspective is one accounting frame, such as the tenant's view with subsidies (§7.1). Every euro figure is a
    LOW/BEST_ESTIMATE/HIGH band (§3.9). Besides the KPIs the result carries what they are made of: the full `timeline`,
    the provenance `ledger` and `source_resolver`, the subsidy `decisions`, the CO2 masses, and the physical context
    (energy quantities, reference areas, simulated period fraction, simulation year), so presentation never reads
    `EvaluationInputs`. Every KPI is a filter, pivot or discounting of `timeline`, which is what `explain()` relies on.
    `sunk_cost_written_off_in_euro` is reported but excluded from NPV, since a sunk cost must not distort a
    forward-looking comparison (§4.1). `timeline` holds the full allocated timeline so the zero-sum check of §6.5 stays
    possible; the perspective's figures use `scoped_timeline()`, filtered to `scope_payer`.
    """

    perspective_id: str
    parameters: EconomicParameters
    total_npv_in_euro: UncertainValue  # net present cost over the horizon
    equivalent_annual_cost_in_euro: UncertainValue  # NPV x annuity factor — the headline KPI
    #: The equivalent annual cost over `TimelineAggregation.MONTHS_PER_YEAR`: an even monthly spread
    #: of that yearly payment, not a monthly annuity, and the headline monthly figure. Not
    #: `monthly_cost_year1_in_euro`, which is year 1's cash and carries whatever that year replaces.
    monthly_equivalent_cost_in_euro: UncertainValue
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
    #: name), so effective prices are derivable from the result alone.
    annual_energy_quantities_by_carrier: Dict[str, AnnualEnergyQuantities] = field(default_factory=dict)
    #: Building reference areas for per-area figures.
    reference_areas: ReferenceAreas = field(default_factory=ReferenceAreas)
    #: Share of a year the simulation covered; the divisor behind the quantities above (§3.6).
    simulated_period_fraction: float = 1.0
    #: The year the simulation this result prices was run for — the report header's "Simulation
    #: year" and the fallback price basis of the degenerate-band note. Carried here so
    #: presentation needs no `EvaluationInputs`.
    simulation_year: Optional[int] = None
    #: Per-subject **annualized** energy attribution: subject -> energy-balance role
    #: (`EnergyFlowRole.value`) -> kWh per year as a positive magnitude. The name says
    #: `annual_` because the field of the same shape on `EvaluationInputs` holds the *simulated
    #: period*, which differs by the §3.6 annualization divisor.
    #: Optional: it is filled from the component output columns
    #: `adapter.DeviceEnergySpecs` names and stays empty everywhere else, including for results
    #: serialized before it existed. Only the household energy balance reads it, and that chart
    #: skips itself rather than drawing a partial picture when the map carries fewer than two
    #: device flows — no KPI, export or invariant depends on it.
    annual_energy_attribution_by_subject_in_kwh: Dict[str, Dict[str, float]] = field(default_factory=dict)
    #: Per-carrier §8.5 flexibility value of the year-1 bill *before* the clamp the projection
    #: applies (key = `EnergyCarrier.value`). Diagnostics, not a published figure: a negative
    #: entry means the load was timed worse than a flat profile and is what the plausibility
    #: panel's flexibility check reads.
    raw_flexibility_value_by_carrier: Dict[str, float] = field(default_factory=dict)
    #: The Sowieso share each anyway credit was computed at (subject -> share). A credit is
    #: `share x like-for-like cost`, and a reader looking at a credit against an insulation
    #: measure has to be told which share produced it — so the report's timeline detail table and
    #: the captions that name the credit read this. Empty for a run without replaced assets and
    #: for results serialized before the field existed, in which case the renderers say nothing
    #: rather than claiming a share they do not know.
    anyway_share_by_subject: Dict[str, float] = field(default_factory=dict)
    #: The like-for-like cost each anyway credit was computed *on*, per subject, in nominal euro
    #: of the credit year. With `anyway_share_by_subject` this makes the credit a visible
    #: multiplication — `share x basis = credit` — instead of a figure the reader has to trust.
    #: Empty for a run without replaced assets and for results serialized before the field
    #: existed, in which case the caption states the share alone as it did before.
    anyway_basis_by_subject: Dict[str, float] = field(default_factory=dict)
    #: What each of those bases *is*, per subject, from `AnywayBasisKinds`: the avoided
    #: like-for-like replacement, or the non-energy share of the measure's own gross cost for a
    #: coupled measure. The captions word the multiplication from this rather than calling
    #: both by the name of one. Empty for a result serialized before the field existed, where the
    #: basis is stated without a name for it.
    anyway_basis_kind_by_subject: Dict[str, str] = field(default_factory=dict)
    #: The §559/§559e rent increase and whether a statutory cap decided it. Present only for
    #: perspectives the rented-case ruleset allocated; None everywhere else.
    modernization_levy: Optional[ModernizationLevySummary] = None
    #: The escalation rates, tariffs and heat demand this evaluation resolved. None for a result
    #: serialized before the field existed; the assumptions section then renders the parameter
    #: half only and says which half is missing rather than inventing it.
    assumptions: Optional[EconomicAssumptions] = None
    #: The dated replacement schedule behind the timeline: ``(subject, year, nominal escalated
    #: amount)`` per scheduled re-purchase, in the order the subjects were priced. It is the same
    #: schedule the REPLACEMENT entries carry — and the only record of it under OPERATING_ONLY,
    #: where those entries are suppressed in favour of the levelized reserve (§4.2). Carried so
    #: that :class:`hisim.economics.staged.StagedEvaluator` can re-date a stage's replacements to
    #: the year that stage starts in and rebuild the plan's reserve from the re-dated flows. Not
    #: serialized: it is derivable from the priced inputs and is read in-process only, so it is
    #: empty for a result read back from ``lifecycle_costs.json``.
    replacement_flows: List[Tuple[str, int, UncertainValue]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Check that the three anyway-credit maps describe the same set of credits.

        `share`, `basis` and `basis_kind` are one fact split over three maps, and captions multiply them. A result
        without kinds at all is valid (such files exist); a basis or kind without a share, or a kind without a basis,
        is a corrupt file.

        Raises:
            ValueError: If a basis or a kind is recorded for a subject with no share, or a kind for a subject with no
                basis.
        """
        orphans = (
            set(self.anyway_basis_by_subject) | set(self.anyway_basis_kind_by_subject)
        ) - set(self.anyway_share_by_subject)
        if orphans:
            raise ValueError(
                f"Anyway credit records disagree for perspective {self.perspective_id!r}: "
                f"{sorted(orphans)} carry a basis or a basis kind but no share. An anyway credit "
                "is share x basis, so neither half stands alone."
            )
        nameless = set(self.anyway_basis_kind_by_subject) - set(self.anyway_basis_by_subject)
        if nameless:
            raise ValueError(
                f"Anyway credit records disagree for perspective {self.perspective_id!r}: "
                f"{sorted(nameless)} name a basis kind but record no basis to apply it to."
            )

    def scoped_timeline(self) -> CashFlowTimeline:
        """Return the flows this perspective reports on, filtered by `scope_payer`.

        The one definition of scoping, shared with `calculators/aggregation.py` and `explain()`, so a KPI and its
        explanation agree. A SYSTEM-scope result returns everything.
        """
        return self.timeline.scoped_to(self.scope_payer)

    # ------------------------------------------------------------------ provenance (§3.10)

    def explain(self, value_path: str) -> ProvenanceReport:
        """Explain where a result value comes from: its timeline entries and their parameters (§3.10).

        Accepted paths: ``total_npv_in_euro``, ``equivalent_annual_cost_in_euro``, ``npv_by_category[CATEGORY]``,
        ``npv_by_component[subject]``, ``npv_by_payer[actor]`` (and ``monthly_cost_year1_in_euro``, which has the NPV's
        entries). The report goes from the value to the contributing entries, to the `ParameterProvenance` records they
        were built from, to the resolved sources with citation, URL and retrieval date. It backs `python -m
        hisim.economics explain` and works on archived results, since ledger and result are stored together. Source ids
        ``inline:...`` are values the engine introduced (a scenario overlay, an override's `override_source`) and
        appear as `INLINE` sources.

        Args:
            value_path: One of the paths above, using the result's field names.

        Returns:
            A `ProvenanceReport`, renderable as text or JSON. Its `value` is None when the path is valid but the result
                has no such key (e.g. a category that never occurred).

        Raises:
            KeyError: On an unknown container or value path.
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
        """Return the entries a result value is made of, scoped as the value itself is.

        Every KPI except `npv_by_payer` comes from the scoped timeline (`calculators/aggregation.aggregate_timeline`),
        so its entries are filtered the same way. `npv_by_payer` is taken over all payers to show the split, so it uses
        the full timeline.
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
        """Return the value a path addresses, or None when the result has no such key or field.

        Never raises: `_entries_for_path` has already validated the path, and an absent key is a valid "no value". Only
        `UncertainValue` attributes are returned.
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
        """Serialize for lifecycle_costs.json, without the ledger and the timeline.

        The ledger goes to `cost_provenance.json` and the timeline to `cash_flow_timeline.csv`, which keeps the KPI
        payload small. Fields have only been added over time, so consumers of older files keep working.
        """
        return {
            "perspective": self.perspective_id,
            "parameters": self.parameters.to_dict(),
            "total_npv_in_euro": self.total_npv_in_euro.to_json(),
            "equivalent_annual_cost_in_euro": self.equivalent_annual_cost_in_euro.to_json(),
            "monthly_equivalent_cost_in_euro": self.monthly_equivalent_cost_in_euro.to_json(),
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
            # The physical quantities the derived views and the plausibility report are computed
            # from:
            "annual_energy_quantities_by_carrier": {
                carrier: quantities.to_json()
                for carrier, quantities in self.annual_energy_quantities_by_carrier.items()
            },
            "reference_areas": self.reference_areas.to_json(),
            "simulated_period_fraction": self.simulated_period_fraction,
            # The per-subject energy attribution the household energy balance needs. Written even
            # when empty so the schema is stable.
            "annual_energy_attribution_by_subject_in_kwh": {
                subject: dict(by_role)
                for subject, by_role in self.annual_energy_attribution_by_subject_in_kwh.items()
            },
            # The Sowieso share behind every anyway credit on the timeline, and the cost that share
            # was applied to.
            "anyway_share_by_subject": dict(self.anyway_share_by_subject),
            "anyway_basis_by_subject": dict(self.anyway_basis_by_subject),
            "anyway_basis_kind_by_subject": dict(self.anyway_basis_kind_by_subject),
            # The levy the landlord statement reports on.
            "modernization_levy": self.modernization_levy.to_json() if self.modernization_levy else None,
            # The resolved assumptions the assumptions section publishes.
            "assumptions": self.assumptions.to_json() if self.assumptions else None,
            # The scope and the simulation year presentation needs to render from a stored
            # result alone.
            "scope_payer": self.scope_payer.value,
            "simulation_year": self.simulation_year,
            # Diagnostics travelling with the result so a stored evaluation can be re-checked
            # without re-pricing it:
            "raw_flexibility_value_by_carrier": dict(self.raw_flexibility_value_by_carrier),
        }


@dataclass
class EvaluationMatrix:
    """All perspective results of one variant: {perspective -> LifecycleCostResult} (§3.1).

    `EconomicEvaluator.evaluate_matrix` fills one entry per applicable perspective; `exports.py` writes it and the
    reports render it. Rows compare accounting frames of the same building; comparing two buildings is `compare()`.
    """

    results: Dict[str, LifecycleCostResult] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialize for lifecycle_costs.json, in bundle order, so report tables and charts are stable across runs."""
        return {perspective: result.to_json() for perspective, result in self.results.items()}


@dataclass
class VariantComparison:
    """The difference between two variants under one perspective (§3.7), such as a building before and after retrofit.

    Produced by `compare()` and read by the report's comparison section (delta waterfall, payback curve, warm-rent
    change). Deltas are variant minus reference, per slot, so a negative NPV delta means the variant is cheaper.
    Payback and warm-rent neutrality are given per slot (`"low"`, `"best_estimate"`, `"high"`), since a retrofit can
    pay back in one world and not another. A payback range is read by value from :attr:`discounted_payback_envelope`,
    not from the slots in order.
    """

    reference_id: str
    variant_id: str
    perspective_id: str
    npv_delta_in_euro: UncertainValue  # variant - reference, slot-wise
    equivalent_annual_cost_delta_in_euro: UncertainValue
    #: The equivalent annual cost delta over `TimelineAggregation.MONTHS_PER_YEAR`.
    monthly_equivalent_cost_delta_in_euro: UncertainValue
    npv_delta_by_subject: Dict[str, UncertainValue]
    # Discounted payback per slot; each independently None-able ("never within horizon"):
    discounted_payback_years: Dict[str, Optional[int]] = field(default_factory=dict)
    warm_rent_change_per_month_in_euro: Optional[UncertainValue] = None
    warm_rent_neutral_per_slot: Dict[str, bool] = field(default_factory=dict)
    #: The payback *curve* per slot: cumulative discounted savings (reference - variant) for
    #: years 0..T, index = year. `discounted_payback_years` is its zero-crossing, so the
    #: curve a report draws and the number it prints cannot disagree.
    cumulative_discounted_savings_in_euro: Dict[str, List[float]] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Serialize the comparison."""
        return {
            "reference": self.reference_id,
            "variant": self.variant_id,
            "perspective": self.perspective_id,
            "npv_delta_in_euro": self.npv_delta_in_euro.to_json(),
            "equivalent_annual_cost_delta_in_euro": self.equivalent_annual_cost_delta_in_euro.to_json(),
            "monthly_equivalent_cost_delta_in_euro": self.monthly_equivalent_cost_delta_in_euro.to_json(),
            "npv_delta_by_subject": {subject: value.to_json() for subject, value in self.npv_delta_by_subject.items()},
            "discounted_payback_years": self.discounted_payback_years,
            "warm_rent_change_per_month_in_euro": self.warm_rent_change_per_month_in_euro.to_json()
            if self.warm_rent_change_per_month_in_euro
            else None,
            "warm_rent_neutral_per_slot": self.warm_rent_neutral_per_slot,
            "cumulative_discounted_savings_in_euro": self.cumulative_discounted_savings_in_euro,
        }

    @property
    def discounted_payback_envelope(self) -> "PaybackEnvelope":
        """Return the payback years of the three worlds as one range ordered by value."""
        return PaybackEnvelope.of(self.discounted_payback_years)


def _subject_alignment_key(result: LifecycleCostResult, subject: str) -> str:
    """Return the key that aligns a subject across two variants: its asset class and its name (§3.7).

    Keying on both keeps a heat pump from being aligned with a boiler that shares a component name. Subjects present in
    only one variant still get a row, compared against zero in `compare`.
    """
    breakdown = result.component_breakdowns.get(subject)
    asset_class = breakdown.asset_class.value if breakdown and breakdown.asset_class else ""
    return f"{asset_class}|{subject}"


class _SlotAccessors:
    """The three evaluation worlds as (slot name, band accessor), in the order comparisons report them.

    The names match `VariantComparison.discounted_payback_years`.
    """

    BY_SLOT = (
        ("low", lambda band: band.minimum),
        ("best_estimate", lambda band: band.best_estimate),
        ("high", lambda band: band.maximum),
    )


def cumulative_discounted_savings(
    reference: LifecycleCostResult, variant: LifecycleCostResult
) -> Dict[str, List[float]]:
    """Return the payback curve per slot: cumulative discounted (reference - variant) per year.

    Index is year 0..T; the last value is the slot's NPV saving. Discounting uses `timeline.discount_factor`. Years
    missing on either side count as zero flow. Reports draw the curve from this, so the curve and the payback year
    agree.
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
    """Return the first year after 0 at which a payback curve reaches zero, or None if never within the horizon.

    Year 0 is excluded because it is the investment year; a variant costing nothing extra would otherwise pay back at
    once. Only the first crossing counts, so a later dip below zero (a large replacement) is not reflected.

    Args:
        cumulative_savings: One slot's curve from :func:`cumulative_discounted_savings`.

    Returns:
        The year, or None when the curve never reaches zero within the horizon.
    """
    for year, value in enumerate(cumulative_savings):
        if year > 0 and value >= 0:
            return year
    return None


@dataclass(frozen=True)
class PaybackEnvelope:
    """The payback years of the three worlds as one range ordered by value.

    Which world pays back first does not follow from its slot. The LOW slot prices both variants cheaply: where the
    energy bill dominates, the LOW world saves least and pays back last; where the investment dominates, it pays back
    first. The range is therefore taken by value:

    - ``earliest``: the first year any world has paid back; None when none does.
    - ``central``: the best-estimate world's year; None when it never pays back.
    - ``latest``: the year every world has paid back; None as soon as one never does.

    Reading None as +infinity, ``earliest <= central <= latest`` always holds. Per-slot years stay on
    :attr:`VariantComparison.discounted_payback_years`; every stated payback range reads this, built by :meth:`of`.
    """

    earliest: Optional[int]
    central: Optional[int]
    latest: Optional[int]

    @classmethod
    def of(cls, payback_by_slot: Mapping[str, Optional[int]]) -> "PaybackEnvelope":
        """Build the envelope from per-slot payback years keyed ``low``, ``best_estimate`` and ``high``.

        Args:
            payback_by_slot: One payback year per world, None meaning never within the horizon; all three must be
                present.

        Returns:
            The range by value.

        Raises:
            ValueError: If a world is missing; a missing world is not read as "never".
        """
        missing = [slot for slot, _getter in _SlotAccessors.BY_SLOT if slot not in payback_by_slot]
        if missing:
            raise ValueError(
                f"The payback range needs the payback year of every world, but {', '.join(missing)} "
                f"is missing from the per-slot years {dict(payback_by_slot)!r}."
            )
        years = [payback_by_slot[slot] for slot, _getter in _SlotAccessors.BY_SLOT]
        reached = [year for year in years if year is not None]
        return cls(
            earliest=min(reached) if reached else None,
            central=payback_by_slot["best_estimate"],
            latest=max(reached) if len(reached) == len(years) else None,
        )

    def to_band(self) -> Dict[str, Optional[int]]:
        """Return the document's band: ``min`` earliest, ``best`` central, ``max`` latest; None means never."""
        return {"min": self.earliest, "best": self.central, "max": self.latest}


def compare(
    reference: LifecycleCostResult,
    variant: LifecycleCostResult,
    reference_id: str = "reference",
    variant_id: str = "variant",
) -> VariantComparison:
    """Compare two variants: NPV and annuity deltas, discounted payback and warm-rent change (§3.7, §6.5).

    All deltas are per slot: both variants are evaluated within the same LOW, BEST_ESTIMATE or HIGH world, so shared
    uncertainty (the same gas price band, the same devices) cancels and the delta band shows only what the variants do
    not share. Differencing averages would discard the band, and differencing intervals would add two widths. The slots
    are three coherent scenarios, not an outer envelope of the difference; a mixed world (gas expensive while the heat
    pump is cheap) belongs to the scenario axes (§4.6). Payback is the zero crossing of each slot's own savings curve,
    with the range taken by value (:meth:`PaybackEnvelope.of`). Warm-rent neutrality is evaluated per slot; neutrality
    in HIGH is the robust statement (§6.5).

    Args:
        reference: The base variant's result.
        variant: The measures variant's result under the same perspective; its `parameters` supply the interest rate,
            horizon and annuity factor.
        reference_id: Label for the reference, carried into the comparison and its exports.
        variant_id: Label for the variant.

    Returns:
        A `VariantComparison`. The warm-rent fields stay None unless both results carry a TENANT payer NPV, i.e. the
            perspective is a rented one (§6.5).
    """
    # Imported here because the aggregation calculator imports this module for its record types.
    from hisim.economics.calculators.aggregation import (  # pylint: disable=import-outside-toplevel
        TimelineAggregation,
    )

    months_per_year = TimelineAggregation.MONTHS_PER_YEAR
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
        monthly_equivalent_cost_delta_in_euro=eac_delta.scale(1.0 / months_per_year),
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
        delta_per_month = (tenant_variant - tenant_reference).scale(annuity_factor / months_per_year)
        comparison.warm_rent_change_per_month_in_euro = delta_per_month
        comparison.warm_rent_neutral_per_slot = {
            "low": delta_per_month.minimum <= 0,
            "best_estimate": delta_per_month.best_estimate <= 0,
            "high": delta_per_month.maximum <= 0,
        }
    return comparison
