"""Tariff provider component (cost_spec.md §8.3).

Evolves `generic_price_signal.py` (which stays untouched during the parallel phase) into a
provider driven by a :class:`hisim.economics.tariffs.TariffContract` — the same contract the
postprocessing billing engine reads, so control and billing can never diverge.

Per timestep it outputs the total marginal purchase/injection price and the capacity-charge
state a peak-shaving strategy needs, and publishes a 24 h price forecast to the
`SingletonSimRepository` for MPC controllers (same mechanism as `generic_price_signal.py`).

**Two consumers, one contract.** `hisim/economics/tariffs.py` owns the contract schema, its
loaders and the pure billing engine; this module is its *simulation-side* consumer. During the
run this component turns the contract into a per-timestep price signal that controllers can
optimize against; after the run the postprocessing billing engine (`tariffs.apply_tariff`, fed
by the meters' `BillingDeterminants`) turns the resulting load profile into a bill from the very
same contract object. Neither side owns price data of its own, and the price *selection* rule
— which of FLAT / ToU band / spot value applies at a given moment — exists exactly once, in
`tariffs.marginal_purchase_price_in_euro_per_kwh`, which both sides call. That is the point:
§8.1 opens with the observation that if control optimizes against one price and the evaluation
bills another, any "savings from smart control" are an artifact of the mismatch.

What this module therefore does NOT own: the tariff schema and its data files, the billing
arithmetic, the horizon projection of a bill (§8.5), and any notion of who pays. It also does
not implement a control strategy — it only publishes the signals a strategy needs.
"""

# clean

import datetime
from dataclasses import dataclass
from typing import Any, List, Optional

from dataclass_wizard import JSONWizard

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.component import ConfigBase, DisplayConfig
from hisim.economics.database import CostDataError
from hisim.economics.facts import CostRelevance
from hisim.economics.tariffs import (
    CapacityChargeKind,
    SupplyKind,
    TariffContract,
    load_spot_series,
    load_tariff_contract,
    marginal_purchase_price_in_euro_per_kwh,
    synthetic_reference_spot_series,
    validate_billing_interval,
)
from hisim.sim_repository_singleton import SingletonDictKeyEnum, SingletonSimRepository
from hisim.simulationparameters import SimulationParameters

__authors__ = "HiSim Team"
__license__ = "MIT"
__status__ = "development"


@dataclass
class TariffProviderConfig(ConfigBase, JSONWizard):
    """Configuration of the tariff provider.

    Deliberately thin: everything price-bearing lives in the tariff contract data file, so the
    configuration only has to name *which* contract applies (`tariff_contract_id`) and how far
    ahead the forecast for MPC controllers should reach. Keeping prices out of the component
    config is what allows a scenario to swap the tariff by id, and what guarantees that the
    simulated control signal and the postprocessing bill come from the same source (§8.1).

    As a `ConfigBase`/`JSONWizard` dataclass it is also the JSON-mode surface of this component:
    a `*.scenario.json` names `TariffProvider.get_full_classname()` and supplies these fields.
    """

    building_name: str
    name: str
    #: Contract id resolved against hisim/cost_database/tariffs/, or "SYNTHETIC_TEST" for the
    #: deterministic synthetic reference profile (spec Q16).
    tariff_contract_id: str
    #: Hours of price forecast published for MPC controllers.
    forecast_horizon_in_hours: int

    @classmethod
    def get_main_classname(cls):
        """Returns the fully qualified class name for JSON mode."""
        return TariffProvider.get_full_classname()

    @classmethod
    def get_default_config(cls, building_name: str = "BUI1") -> "TariffProviderConfig":
        """Default: the deterministic synthetic dynamic tariff (for tests).

        The default deliberately selects `SYNTHETIC_TEST` rather than a shipped contract: real
        day-ahead price series cannot be shipped for licensing reasons, so a default pointing at
        a DYNAMIC catalog entry would fail on a fresh checkout. The synthetic profile is a closed
        formula, hence reproducible bit for bit, which is what lets tests assert exact prices.
        """
        return TariffProviderConfig(
            building_name=building_name,
            name="TariffProvider",
            tariff_contract_id="SYNTHETIC_TEST",
            forecast_horizon_in_hours=24,
        )


class TariffProvider(cp.Component):
    """Publishes per-timestep tariff signals from a TariffContract (§8.3).

    A pure signal source: it consumes no energy, produces none, and never influences the physics
    of a simulation — it only makes the active tariff visible to whatever wants to react to it.
    Four outputs cover the two things a cost-optimizing controller needs: the total marginal
    purchase/injection price in EUR/kWh (`PricePurchase`, `PriceInjection`), and the
    capacity-charge state (`BillingPeriodPeakSoFar` in kW, `CapacityChargeMarginal` in EUR/kW),
    from which a rule-based EMS can do meaningful peak shaving without any tariff bookkeeping of
    its own — staying below the period's peak is free, setting a new one costs the marginal price.

    In the wider flow it is the simulation-side half of §8: it reads the same `TariffContract`
    that the postprocessing billing engine later bills the resulting load profile with, so control
    decisions and their bill cannot be based on different prices. Controllers consume the outputs
    either by ordinary input wiring or, for MPC, through the 24 h forecast published to the
    `SingletonSimRepository`.
    """

    cost_relevance = CostRelevance.FREE_OF_COST  # the contract prices energy, not hardware

    # Outputs
    PricePurchase = "PricePurchase"
    PriceInjection = "PriceInjection"
    BillingPeriodPeakSoFar = "BillingPeriodPeakSoFar"
    CapacityChargeMarginal = "CapacityChargeMarginal"
    # Optional input for peak tracking
    ElectricityFromGridInWatt = "ElectricityFromGridInWatt"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: TariffProviderConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Initializes the provider and loads the contract.

        Resolves the configured contract id to a `TariffContract` immediately, so a missing or
        malformed tariff data file fails here — while the component graph is still being wired —
        rather than at the first timestep. `validate_billing_interval` is called for the same
        reason: a capacity-charge contract whose billing interval is not an integer multiple of
        `seconds_per_timestep` cannot be metered correctly (§8.4), and that is a pre-check, not a
        runtime error.

        It then declares one optional input and the four outputs. The input
        (`ElectricityFromGridInWatt`, `mandatory=False`) is the grid draw the peak tracker follows;
        leaving it unconnected is legitimate and simply means the two capacity-charge outputs stay
        at zero. The outputs carry `LoadTypes.PRICE` with `Units.EUR_PER_KWH` for the two prices,
        kilowatts for the running peak, and `Units.ANY` for the marginal capacity charge, which is
        in EUR/kW — a unit the enum does not offer.

        Args:
            my_simulation_parameters: Simulation clock; supplies `seconds_per_timestep`,
                `timesteps` and `start_date`, all three of which this component reads.
            config: The tariff configuration, above all the contract id.
            my_display_config: Standard HiSim webtool/report visibility settings.
        """
        super().__init__(
            name=config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.tariff_config = config
        self.contract: TariffContract = self._load_contract(config.tariff_contract_id)
        validate_billing_interval(my_simulation_parameters.seconds_per_timestep, self.contract)
        self._price_series: Optional[List[float]] = None
        self._peak_so_far_in_kw: float = 0.0
        self._saved_peak_in_kw: float = 0.0
        self._timesteps_per_billing_period: Optional[int] = None

        self.electricity_from_grid_input: cp.ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.ElectricityFromGridInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            mandatory=False,
        )
        self.price_purchase_output: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.PricePurchase,
            load_type=lt.LoadTypes.PRICE,
            unit=lt.Units.EUR_PER_KWH,
            output_description="Total marginal purchase price (all tariff components summed).",
        )
        self.price_injection_output: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.PriceInjection,
            load_type=lt.LoadTypes.PRICE,
            unit=lt.Units.EUR_PER_KWH,
            output_description="Marginal injection remuneration.",
        )
        self.peak_so_far_output: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.BillingPeriodPeakSoFar,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.KILOWATT,
            output_description="Highest billing-interval mean power in the current billing period.",
        )
        self.capacity_charge_output: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CapacityChargeMarginal,
            load_type=lt.LoadTypes.PRICE,
            unit=lt.Units.ANY,
            output_description="Marginal cost of setting a new period peak [EUR/kW]; 0 below the peak.",
        )

    @staticmethod
    def _load_contract(contract_id: str) -> TariffContract:
        """Resolves a contract id, with one in-memory fallback for tests (spec Q16).

        Everything except the reserved id `SYNTHETIC_TEST` is loaded from
        `hisim/cost_database/tariffs/` by `tariffs.load_tariff_contract`. The synthetic contract is
        built here instead of shipped as a data file because it references the sentinel spot series
        `"__synthetic__"`, which `i_prepare_simulation` resolves to the closed-form reference
        profile rather than to a CSV — real day-ahead series cannot be shipped for licensing
        reasons. Its `inline:` source id is the documented convention for in-memory contracts that
        have no `sources.json` registry behind them; catalog files must never use it.

        The numbers are a plausible German household composition (spot markup, grid fee, taxes and
        levies, VAT, standing charge) and are exact bands: a fixture must not introduce uncertainty
        of its own.
        """
        if contract_id == "SYNTHETIC_TEST":
            from hisim.economics.uncertainty import UncertainValue
            from hisim.economics.tariffs import TariffSupply
            from hisim.economics.carriers import EnergyCarrier

            return TariffContract(
                id="SYNTHETIC_TEST",
                carrier=EnergyCarrier.ELECTRICITY,
                country="DE",
                region=None,
                valid_from_year=0,
                supply=TariffSupply(
                    kind=SupplyKind.DYNAMIC,
                    spot_series="__synthetic__",
                    markup_in_euro_per_kwh=UncertainValue.exact(0.017),
                    grid_fee_in_euro_per_kwh=UncertainValue.exact(0.094),
                    taxes_and_levies_in_euro_per_kwh=UncertainValue.exact(0.051),
                    vat_rate=0.19,
                ),
                standing_charge_in_euro_per_year=UncertainValue.exact(140.0),
                source_ids=("inline:synthetic reference profile for tests (spec Q16)",),
            )
        return load_tariff_contract(contract_id)

    def i_prepare_simulation(self) -> None:
        """Loads/resamples the price series and publishes the forecast base.

        Called once before the timestep loop, this is where all file access and all resampling
        happens, so that `i_simulate` — which runs once per convergence iteration of every
        timestep — is a pure lookup. For a DYNAMIC contract the hourly spot series (a CSV from the
        cost database, or the synthetic reference profile) is expanded to one raw value per
        timestep by holding the hourly value; the last hour is clamped, so a series shorter than
        the simulated year degrades to a constant tail instead of raising.

        For a contract with a capacity charge it additionally derives how many timesteps make up
        one billing interval, which is well defined precisely because the constructor validated
        that the interval is a multiple of the timestep length.

        Raises:
            CostDataError: If a DYNAMIC contract names no spot series at all (§8.2), or if the
                named series cannot be loaded.
        """
        if self.contract.supply.kind == SupplyKind.DYNAMIC:
            series_id = self.contract.supply.spot_series
            if series_id == "__synthetic__":
                hourly = synthetic_reference_spot_series()
            elif series_id is None:
                raise CostDataError(
                    f"Tariff {self.contract.id}: a DYNAMIC contract needs a spot_series (§8.2)."
                )
            else:
                hourly = load_spot_series(series_id)
            seconds = self.my_simulation_parameters.seconds_per_timestep
            steps = self.my_simulation_parameters.timesteps
            # Raw spot values per timestep; `spot_factor` is applied by the shared price
            # selection (`tariffs.energy_price_in_euro_per_kwh`), not here, so the factor cannot
            # be applied twice or forgotten on one of the two sides.
            self._price_series = [
                hourly[min(int(step * seconds // 3600), len(hourly) - 1)] for step in range(steps)
            ]
        if self.contract.capacity_charge.kind != CapacityChargeKind.NONE:
            interval_seconds = self.contract.capacity_charge.billing_interval_in_minutes * 60
            self._timesteps_per_billing_period = max(
                1, interval_seconds // self.my_simulation_parameters.seconds_per_timestep
            )

    def _purchase_price(self, timestep: int) -> float:
        """The marginal purchase price of this timestep, on the AVERAGE slot (§8.4).

        Price *selection* — which of FLAT / ToU band / spot value applies — is not decided here:
        it is `tariffs.marginal_purchase_price_in_euro_per_kwh`, the same function the billing
        engine prices its aggregates with, so the price a controller reacts to and the price the
        bill charges cannot drift apart. This component only supplies the moment (weekday/hour)
        and the spot value of the timestep.
        """
        supply = self.contract.supply
        weekday: Optional[int] = None
        hour: Optional[int] = None
        spot: Optional[float] = None
        if supply.kind == SupplyKind.TIME_OF_USE:
            seconds = timestep * self.my_simulation_parameters.seconds_per_timestep
            moment = self.my_simulation_parameters.start_date.timestamp() + seconds
            when = datetime.datetime.fromtimestamp(moment)
            weekday, hour = when.weekday(), when.hour
        elif supply.kind == SupplyKind.DYNAMIC:
            assert self._price_series is not None
            spot = self._price_series[min(timestep, len(self._price_series) - 1)]
        return marginal_purchase_price_in_euro_per_kwh(self.contract, weekday, hour, spot).average

    def _injection_price(self) -> float:
        """The feed-in remuneration of one kWh, on the AVERAGE slot (§8.4).

        Constant over the run, unlike the purchase price: the contract's feed-in rate is a single
        figure per contract (a FIXED_TARIFF is nominally constant for its duration by definition),
        so no moment has to be passed in. A controller deciding between self-consumption and
        injection compares this figure against `_purchase_price`.

        Like every other output of this component it reports the AVERAGE slot of the uncertainty
        band — a simulation needs one number per timestep, and the LOW/HIGH worlds are evaluated
        by the postprocessing engine, not by the control signal.
        """
        return self.contract.feed_in.rate_in_euro_per_kwh.average

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Outputs the current prices and capacity-charge state; publishes the forecast.

        Called once per convergence iteration of every timestep. The two prices are recomputed
        (a lookup plus an addition — the series was resampled in `i_prepare_simulation`) and
        written unconditionally, so this component always converges immediately; it reads no
        input that could change within a timestep except the grid draw it merely observes.

        The capacity-charge branch runs only for a contract that has one *and* only when the
        optional grid-draw input is actually connected; otherwise both related outputs stay zero,
        which is the honest answer — an unconnected peak tracker knows nothing. The peak itself is
        a running maximum over the billing period and is the component's only mutable state, hence
        the save/restore pair below. `CapacityChargeMarginal` is the peak-shaving signal its output
        description promises: the full contract price on a timestep whose draw is at or above the
        running peak — where one more kilowatt raises the billed peak and therefore costs money —
        and 0.0 on any timestep below it, where extra load is free of capacity charge.

        The 24 h forecast is published once, at timestep 0, and only for a DYNAMIC contract (only
        then is `_price_series` set): it is a look-ahead over the resampled series, which is known
        in full up front, so there is nothing to update later.

        Args:
            timestep: Index of the current timestep.
            stsv: Value store of this timestep; outputs are written into it.
            force_convergence: Set by the simulator on the final iteration of a timestep. Read
                only by the monthly peak reset, which is skipped on such an iteration.
        """
        stsv.set_output_value(self.price_purchase_output, self._purchase_price(timestep))
        stsv.set_output_value(self.price_injection_output, self._injection_price())

        capacity = self.contract.capacity_charge
        if capacity.kind != CapacityChargeKind.NONE and self.electricity_from_grid_input.source_output is not None:
            power_in_kw = stsv.get_input_value(self.electricity_from_grid_input) * 1e-3
            # Billing-interval mean approximated by the timestep value (exact when the timestep
            # equals the interval; the meter computes the exact peaks for billing, §8.4).
            # Month boundaries are approximated as equal twelfths of the simulated horizon, not as
            # calendar months; the exact monthly peaks for billing are computed by the meter (§8.4).
            if capacity.kind == CapacityChargeKind.MONTHLY_PEAK and self._timesteps_per_billing_period:
                steps_per_month = max(1, self.my_simulation_parameters.timesteps // 12)
                if timestep % steps_per_month == 0 and not force_convergence:
                    self._peak_so_far_in_kw = 0.0
            # Compared against the peak *before* this timestep is folded in: at or above it, one
            # more kilowatt raises the billed peak and costs the capacity price; below it, extra
            # load is free. The restore in `i_restore_state` puts the pre-timestep peak back, so
            # every convergence iteration of a timestep answers the same question.
            sets_new_peak = power_in_kw >= self._peak_so_far_in_kw
            self._peak_so_far_in_kw = max(self._peak_so_far_in_kw, power_in_kw)
            stsv.set_output_value(self.peak_so_far_output, self._peak_so_far_in_kw)
            stsv.set_output_value(
                self.capacity_charge_output,
                capacity.price_in_euro_per_kw.average if sets_new_peak else 0.0,
            )
        else:
            stsv.set_output_value(self.peak_so_far_output, 0.0)
            stsv.set_output_value(self.capacity_charge_output, 0.0)

        # 24 h price forecast for MPC (same mechanism as generic_price_signal.py, §8.3).
        if timestep == 0 and self._price_series is not None:
            steps_per_day = int(24 * 3600 / self.my_simulation_parameters.seconds_per_timestep)
            SingletonSimRepository().set_entry(
                key=SingletonDictKeyEnum.PRICEPURCHASEFORECAST24H,
                entry=[
                    self._purchase_price(step)
                    for step in range(min(steps_per_day, len(self._price_series)))
                ],
            )
            SingletonSimRepository().set_entry(
                key=SingletonDictKeyEnum.PRICEINJECTIONFORECAST24H,
                entry=[self._injection_price()] * steps_per_day,
            )

    def i_save_state(self) -> None:
        """Saves the peak tracker.

        The running billing-period peak is the only state this component carries across timesteps,
        so it is the only thing to checkpoint before the simulator starts iterating a timestep.
        Everything else — the prices, the resampled series, the contract — is either derived from
        the timestep index or immutable for the whole run.
        """
        self._saved_peak_in_kw = self._peak_so_far_in_kw

    def i_restore_state(self) -> None:
        """Restores the peak tracker.

        Undoes the running maximum when the simulator rolls a timestep back to re-iterate it.
        Without this, a grid draw seen in a discarded iteration would raise the period peak
        permanently and make a peak-shaving controller pay for a load that never happened.
        """
        self._peak_so_far_in_kw = self._saved_peak_in_kw

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Nothing to double check.

        The optional post-convergence sanity hook has nothing to verify here: this component
        conserves no energy and closes no balance — its outputs are contract lookups, and the
        one accumulated quantity (the period peak) is a maximum that cannot go out of range.
        """

    def write_to_report(self) -> List[str]:
        """Report entry.

        Contributes one line to the simulation report naming the active contract, its supply
        structure and its carrier. That line is what lets a reader of a finished run tell at a
        glance which tariff the controllers were reacting to — the same id the postprocessing
        bill is computed from.
        """
        return [
            f"Tariff provider: contract {self.contract.id} ({self.contract.supply.kind.value}) "
            f"for carrier {self.contract.carrier.value}"
        ]

    def get_cost_facts(self) -> Optional[Any]:
        """The provider itself is free of cost (§9.2).

        Returning None is the declared behavior for a `FREE_OF_COST` component and matches the
        `cost_relevance` class attribute above: this component is a price signal, not a piece of
        hardware, so it has no investment, no lifetime and no embodied emissions. The energy the
        contract prices is booked exactly once, at the meter that measured it — never here.

        Returns:
            None, always.
        """
        return None
