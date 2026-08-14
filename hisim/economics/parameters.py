"""Economic parameters of the lifecycle cost evaluation (cost_spec.md §3.2).

This module owns the *assumptions* half of the engine's inputs: horizon, interest, escalation
rates, the CO2 scenario, the country and the data-directory paths. It deliberately owns no numbers
that describe the world — every price, lifetime, emission factor and subsidy rule lives in the
versioned data files under `hisim/cost_database/` and `hisim/subsidy_catalog/` — and no evaluation
logic beyond the two textbook factors below.

Its place in the pipeline: one `EconomicParameters` instance is attached to a run (via
`SimulationParameters.set_economic_parameters`, a RenoVisor request or a scenario axis), reaches the
evaluator inside `EvaluationInputs`, is stored on every `LifecycleCostResult`, and is written to
`economic_inputs.json` so a stored result can be re-priced later without re-simulating (§4.6).
Because scenario axes vary exactly these fields, keeping the type a plain serializable dataclass is
what makes a full factorial sweep a matter of milliseconds per cell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from dataclass_wizard import JSONWizard

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.timeline import discount_factor
from hisim.loadtypes import ComponentType


@dataclass
class EconomicParameters(JSONWizard):
    """Parameters of the lifecycle cost evaluation (annuity method, VDI 2067 / DIN EN 15459).

    All rates are nominal; results are in nominal euros discounted to year 0. Real-term
    calculation is possible by supplying real rates consistently.

    Every field is a decision a reviewer may want to challenge, which is why they are gathered in
    one serializable record rather than spread over call signatures: the record travels with the
    results, is written to `economic_inputs.json`, and is the object a scenario axis overwrites
    field by field. The defaults are the engine's documented baseline (20 a horizon, 3 % interest,
    2 % general escalation, "central" CO2 path, 250 €/t damage cost); `EconomicParameters()` with no
    arguments is therefore a complete, runnable assumption set.

    Note the two fallback chains encoded in the field pairs: an unset per-carrier or per-asset-class
    escalation rate falls back to the country's `escalation_defaults_<COUNTRY>.json` file and only
    then to the corresponding general rate (§3.2/§3.5). `country`, `cost_database_path` and
    `subsidy_catalog_path` are deliberately excluded from scenario overlays, so a sweep can never
    silently change which dataset it is reading.
    """

    observation_period_in_years: int = 20
    # Nominal calculation interest rate (discount rate).
    interest_rate: float = 0.03
    # General price change for maintenance/operation-related costs.
    general_price_escalation_rate: float = 0.02
    # Per-carrier nominal energy price escalation rates. Unset carriers fall back to the country's
    # escalation defaults file (§3.5), then to `general_price_escalation_rate`.
    energy_price_escalation_rates: Dict[EnergyCarrier, float] = field(default_factory=dict)
    # Escalation applied to feed-in remuneration (EEG-style tariffs are nominally fixed -> 0.0).
    feed_in_escalation_rate: float = 0.0
    # Investment price change rate for replacements.
    investment_price_escalation_rate: float = 0.02
    # Per-asset-class overrides for diverging technology trajectories. Unset classes fall back to
    # the country defaults file (§3.5), then to `investment_price_escalation_rate`.
    investment_price_escalation_rates: Dict[ComponentType, float] = field(default_factory=dict)
    # Named CO2-price trajectory (§3.5); "none" disables explicit carbon pricing.
    co2_price_scenario: str = "central"
    # CO2 damage cost for the macroeconomic perspective (UBA recommendation ~250 EUR/t).
    co2_damage_cost_in_euro_per_ton: float = 250.0
    # Price basis year for database lookups; defaults to the simulation year.
    price_basis_year: Optional[int] = None
    country: str = "DE"
    apply_subsidies: bool = True  # default for perspectives that don't override it
    cost_database_path: Optional[str] = None
    subsidy_catalog_path: Optional[str] = None
    # Escalation of spot-price spreads / flexibility value (§8.5); None = carrier escalation rate.
    spread_escalation_rate: Optional[float] = None
    # Escalation of grid fees / capacity charges (§8.5); None = general escalation rate.
    grid_fee_escalation_rate: Optional[float] = None
    # Anyway-cost (Sowieso-Kosten) threshold in remaining-life years (§4.1).
    anyway_threshold_years: float = 2.0
    # Opt-in for rebilling a load profile under a tariff it was not simulated with (§4.6).
    allow_counterfactual_billing: bool = False

    def __post_init__(self) -> None:
        """Basic sanity validation.

        Only the two conditions that would make the discounting and annuity formulas meaningless are
        checked here (a horizon below one year, an interest rate at or below -100 %). Everything else
        — a negative escalation rate, an unknown CO2 scenario name, a country without data files —
        is legitimate input or is caught later by the data loaders with a far more informative
        message.

        Raises:
            ValueError: If the observation period is below 1 year or the interest rate is <= -1.0.
        """
        if self.observation_period_in_years < 1:
            raise ValueError("observation_period_in_years must be >= 1.")
        if self.interest_rate <= -1.0:
            raise ValueError("interest_rate must be > -100 %.")

    def discount_factor(self, year: int) -> float:
        """1 / (1 + i)^year at these parameters' interest rate.

        Convenience wrapper around the canonical `timeline.discount_factor` (W4.3); the formula
        itself exists exactly once, there.

        Offered on the parameters because most callers already hold the parameter set and would
        otherwise repeat `discount_factor(parameters.interest_rate, year)`. `year` counts from the
        year-0 investment date under the end-of-year convention, so year 0 yields exactly 1.0.
        """
        return discount_factor(self.interest_rate, year)

    def annuity_factor(self) -> float:
        """Annuity factor over the observation period; 1/T for a zero interest rate.

        Converts a net present cost into the equivalent annual cost — the headline KPI of the whole
        engine (§1.1, §7.3) and the figure that makes systems with different investment/running-cost
        profiles comparable at all. It is the capital recovery factor of VDI 2067-1 / DIN EN 15459-1,
        `i(1+i)^T / ((1+i)^T - 1)`, evaluated at this parameter set's interest rate and horizon; the
        zero-rate branch exists because that expression is 0/0 at i = 0, where the correct limit is
        simply spreading the NPV evenly over the T years.
        """
        interest = self.interest_rate
        years = self.observation_period_in_years
        if interest == 0.0:
            return 1.0 / years
        return interest * (1.0 + interest) ** years / ((1.0 + interest) ** years - 1.0)
