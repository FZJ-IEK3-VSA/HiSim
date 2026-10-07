"""Economic parameters of the lifecycle cost evaluation: the assumptions, not the data (cost_spec.md §3.2).

`EconomicParameters` holds horizon, interest, escalation rates, the CO2 scenario, the country and the data paths;
prices, lifetimes and subsidy rules live in the data files. One instance travels with a run into `EvaluationInputs`,
onto every `LifecycleCostResult` and into `economic_inputs.json`, so a stored result can be re-priced without
re-simulating (§4.6). Scenario axes vary these fields.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, ClassVar, Dict, Mapping, Optional

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.timeline import discount_factor
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType


@dataclass(frozen=True)
class StatedEnergyPrice:
    """The year-1 price terms a plan states for one carrier, in place of the database's.

    A household often knows its own gas or electricity price better than a national average. Each field is optional and
    replaces only its half of the flat contract built from the price entry (`calculators/energy.py`); None keeps the
    database value.

    The working price is the all-in year-1 price per kWh, carbon included. For a carrier with `co2_price_exposure > 0`
    the engine books carbon separately from the CO2 price path, so it subtracts that component's year-1 value (exposure
    x emission factor x CO2 price) and bills the rest as working price: year 1 costs exactly the stated price. For
    `ELECTRICITY_FEED_IN` the working price is the remuneration per kWh sold, and there is no standing charge. The
    standing charge replaces the fixed annual charge and escalates with the general rate.

    Args:
        working_price_in_euro_per_kwh: The all-in year-1 working price, or None to keep the database's.
        standing_charge_in_euro_per_year: The fixed annual charge, or None to keep the database's.
    """

    #: The JSON key of the working price, in `EconomicParameters.to_dict` and in a plan's block.
    WORKING_PRICE_KEY: ClassVar[str] = "working_price_in_euro_per_kwh"

    #: The JSON key of the standing charge.
    STANDING_CHARGE_KEY: ClassVar[str] = "standing_charge_in_euro_per_year"

    working_price_in_euro_per_kwh: Optional[UncertainValue] = None
    standing_charge_in_euro_per_year: Optional[UncertainValue] = None

    def to_json(self) -> Dict[str, Any]:
        """Return the stated terms as JSON, one key per stated field.

        Returns:
            ``{"working_price_in_euro_per_kwh"?: …, "standing_charge_in_euro_per_year"?: …}``, each value written by
                `UncertainValue.to_json` (a bare number for an exact figure).
        """
        raw: Dict[str, Any] = {}
        if self.working_price_in_euro_per_kwh is not None:
            raw[self.WORKING_PRICE_KEY] = self.working_price_in_euro_per_kwh.to_json()
        if self.standing_charge_in_euro_per_year is not None:
            raw[self.STANDING_CHARGE_KEY] = self.standing_charge_in_euro_per_year.to_json()
        return raw

    @classmethod
    def from_json(cls, raw: Any, context: str) -> "StatedEnergyPrice":
        """The inverse of :meth:`to_json`.

        Args:
            raw: The mapping :meth:`to_json` wrote.
            context: Where it stood, for the error message.

        Returns:
            The stated terms.

        Raises:
            ValueError: If `raw` is not a mapping, carries a key other than the two fields, or a
                value is not a number or a band.
        """
        if not isinstance(raw, Mapping):
            raise ValueError(f"{context} must be a mapping of stated price terms, got {raw!r}.")
        unknown = sorted(set(raw) - {cls.WORKING_PRICE_KEY, cls.STANDING_CHARGE_KEY})
        if unknown:
            raise ValueError(
                f"{context} states {', '.join(unknown)}; a stated price has only "
                f"{cls.WORKING_PRICE_KEY} and {cls.STANDING_CHARGE_KEY}."
            )
        return cls(
            working_price_in_euro_per_kwh=UncertainValue.optional_from_json(raw.get(cls.WORKING_PRICE_KEY)),
            standing_charge_in_euro_per_year=UncertainValue.optional_from_json(raw.get(cls.STANDING_CHARGE_KEY)),
        )


@dataclass
class EconomicParameters:
    """Assumptions of the lifecycle cost evaluation (annuity method, VDI 2067 / DIN EN 15459).

    All rates are nominal and results are nominal euros discounted to year 0; supply real rates consistently for a real
    calculation. `EconomicParameters()` is a complete baseline: 20-year horizon, 3 % interest, 2 % general escalation,
    "central" CO2 path, 250 EUR/t damage cost. An unset per-carrier or per-asset-class escalation rate falls back to
    the country's `escalation_defaults_<COUNTRY>.json`, then to the general rate (§3.2, §3.5). `country` and the data
    paths cannot be changed by scenario overlays, so a sweep never switches datasets.
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
    # Year-1 price terms a plan states per carrier in place of the database's; a carrier absent
    # here is priced from `energy_prices_<COUNTRY>.json`. See `StatedEnergyPrice` for what the
    # working price means (all-in, carbon included).
    energy_prices: Dict[EnergyCarrier, StatedEnergyPrice] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Check the conditions that would make discounting or the annuity meaningless, and the stated-price rule.

        Other odd input (a negative escalation rate, an unknown CO2 scenario, a country without data) is legitimate or
        caught later by the data loaders.

        Raises:
            ValueError: If the observation period is below 1 year, the interest rate is <= -1.0, or a standing charge
                is stated for `ELECTRICITY_FEED_IN`.
        """
        if self.observation_period_in_years < 1:
            raise ValueError("observation_period_in_years must be >= 1.")
        if self.interest_rate <= -1.0:
            raise ValueError("interest_rate must be > -100 %.")
        feed_in = self.energy_prices.get(EnergyCarrier.ELECTRICITY_FEED_IN)
        if feed_in is not None and feed_in.standing_charge_in_euro_per_year is not None:
            raise ValueError(
                "energy_prices states a standing charge for ELECTRICITY_FEED_IN; the feed-in "
                "carrier is a remuneration per kWh sold and has none."
            )

    def discount_factor(self, year: int) -> float:
        """Return ``1 / (1 + i)**year`` at this interest rate, via `timeline.discount_factor`.

        Year 0 is the investment date (end-of-year convention), so year 0 yields exactly 1.0.
        """
        return discount_factor(self.interest_rate, year)

    def annuity_factor(self) -> float:
        """Return the annuity factor over the horizon, ``i(1+i)^T / ((1+i)^T - 1)``; 1/T for a zero interest rate.

        It turns a net present cost into the equivalent annual cost, the headline KPI (§7.3); it is the capital
        recovery factor of VDI 2067-1. At i = 0 the formula is 0/0 and the limit spreads the NPV evenly.
        """
        interest = self.interest_rate
        years = self.observation_period_in_years
        if interest == 0.0:
            return 1.0 / years
        return interest * (1.0 + interest) ** years / ((1.0 + interest) ** years - 1.0)

    def to_dict(self) -> Dict[str, Any]:
        """Return the parameters as a JSON-serializable dict, one key per field.

        The rate dicts keep their `EnergyCarrier` and `ComponentType` member keys; both enums derive from `str`, so
        `json.dump` writes each key as its value ("HeatPump"), which :meth:`from_dict` reads back. Stated energy prices
        are written through `StatedEnergyPrice.to_json`.
        """
        raw = asdict(self)
        raw["energy_prices"] = {carrier: stated.to_json() for carrier, stated in self.energy_prices.items()}
        return raw

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EconomicParameters":
        """Rebuild a parameter record from a mapping such as :meth:`to_dict` wrote or a hand-written parameters file.

        Rate-dict keys are converted back to enum members by value or name (see `_enum_key`). An omitted field keeps
        its default, so a subset is valid input. An unknown key is refused, because it is most likely a typo in an
        assumption file that would otherwise silently keep a default.

        Args:
            raw: The mapping to read.

        Returns:
            The parameter record, validated by `__post_init__`.

        Raises:
            ValueError: If a key is not a field, a rate dict is present but not a mapping, or a rate-dict key names no
                enum member.
        """
        known = {field_info.name for field_info in fields(cls)}
        values = dict(raw)
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(
                f"unknown economic parameter(s) {', '.join(unknown)}; the record accepts "
                f"{', '.join(sorted(known))}."
            )
        for key, enum_class in (
            ("energy_price_escalation_rates", EnergyCarrier),
            ("investment_price_escalation_rates", ComponentType),
        ):
            if key not in values:
                continue  # absent means "no per-carrier / per-asset-class override", the default
            rates = values[key]
            if not isinstance(rates, dict):
                raise ValueError(
                    f"economic parameter {key!r} must be a mapping of {enum_class.__name__} to "
                    f"rate, got {rates!r}. Omit the key to keep the default (an empty mapping, so "
                    "every carrier or asset class falls back to the general escalation rate); an "
                    "explicit null is not that statement."
                )
            values[key] = {
                cls._enum_key(enum_class, member, key): float(rate) for member, rate in rates.items()
            }
        if "energy_prices" in values:
            stated = values["energy_prices"]
            if not isinstance(stated, dict):
                raise ValueError(
                    f"economic parameter 'energy_prices' must be a mapping of EnergyCarrier to stated "
                    f"price terms, got {stated!r}. Omit the key to price every carrier from the "
                    "database."
                )
            values["energy_prices"] = {
                cls._enum_key(EnergyCarrier, carrier, "energy_prices"): StatedEnergyPrice.from_json(
                    terms, f"energy_prices.{carrier}"
                )
                for carrier, terms in stated.items()
            }
        return cls(**values)

    @staticmethod
    def _enum_key(enum_class: Any, spelling: Any, parameter_name: str) -> Any:
        """Resolve one rate-dict key to an enum member, by value or by member name.

        A stored `economic_inputs.json` carries the value ("HeatPump"); a hand-written file usually uses the name
        ("HEAT_PUMP"). Both are accepted.

        Args:
            enum_class: `EnergyCarrier` or `ComponentType`.
            spelling: The key as written in the JSON file.
            parameter_name: The field the dict belongs to, named in the error.

        Returns:
            The matching enum member.

        Raises:
            ValueError: If the spelling matches neither a member value nor a member name.
        """
        try:
            return enum_class(spelling)
        except ValueError:
            pass
        try:
            return enum_class[spelling]
        except KeyError:
            accepted = ", ".join(f"{member.name}/{member.value!r}" for member in enum_class)
            raise ValueError(
                f"{spelling!r} in economic parameter {parameter_name!r} is no {enum_class.__name__}; "
                f"accepted spellings are the member name or its value: {accepted}."
            ) from None
