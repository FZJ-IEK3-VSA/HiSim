"""Economic parameters of the lifecycle cost evaluation (cost_spec.md §3.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from dataclass_wizard import JSONWizard

from hisim.economics.carriers import EnergyCarrier
from hisim.loadtypes import ComponentType


@dataclass
class EconomicParameters(JSONWizard):
    """Parameters of the lifecycle cost evaluation (annuity method, VDI 2067 / DIN EN 15459).

    All rates are nominal; results are in nominal euros discounted to year 0. Real-term
    calculation is possible by supplying real rates consistently.
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
        """Basic sanity validation."""
        if self.observation_period_in_years < 1:
            raise ValueError("observation_period_in_years must be >= 1.")
        if self.interest_rate <= -1.0:
            raise ValueError("interest_rate must be > -100 %.")

    def discount_factor(self, year: int) -> float:
        """1 / (1 + i)^year."""
        return 1.0 / ((1.0 + self.interest_rate) ** year)

    def annuity_factor(self) -> float:
        """Annuity factor over the observation period; 1/T for a zero interest rate."""
        interest = self.interest_rate
        years = self.observation_period_in_years
        if interest == 0.0:
            return 1.0 / years
        return interest * (1.0 + interest) ** years / ((1.0 + interest) ** years - 1.0)
