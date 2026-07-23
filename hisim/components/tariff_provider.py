"""Tariff provider component (cost_spec.md §8.3).

Evolves `generic_price_signal.py` (which stays untouched during the parallel phase) into a
provider driven by a :class:`hisim.economics.tariffs.TariffContract` — the same contract the
postprocessing billing engine reads, so control and billing can never diverge.

Per timestep it outputs the total marginal purchase/injection price and the capacity-charge
state a peak-shaving strategy needs, and publishes a 24 h price forecast to the
`SingletonSimRepository` for MPC controllers (same mechanism as `generic_price_signal.py`).
"""

# clean

from dataclasses import dataclass
from typing import Any, List, Optional

from dataclass_wizard import JSONWizard

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.component import ConfigBase, DisplayConfig
from hisim.economics.facts import CostRelevance
from hisim.economics.tariffs import (
    CapacityChargeKind,
    SupplyKind,
    TariffContract,
    load_spot_series,
    load_tariff_contract,
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
    """Configuration of the tariff provider."""

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
        """Default: the deterministic synthetic dynamic tariff (for tests)."""
        return TariffProviderConfig(
            building_name=building_name,
            name="TariffProvider",
            tariff_contract_id="SYNTHETIC_TEST",
            forecast_horizon_in_hours=24,
        )


class TariffProvider(cp.Component):
    """Publishes per-timestep tariff signals from a TariffContract (§8.3)."""

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
        """Initializes the provider and loads the contract."""
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
        """Loads/resamples the price series and publishes the forecast base."""
        if self.contract.supply.kind == SupplyKind.DYNAMIC:
            if self.contract.supply.spot_series == "__synthetic__":
                hourly = synthetic_reference_spot_series()
            else:
                hourly = load_spot_series(self.contract.supply.spot_series)
            seconds = self.my_simulation_parameters.seconds_per_timestep
            steps = self.my_simulation_parameters.timesteps
            self._price_series = [
                hourly[min(int(step * seconds // 3600), len(hourly) - 1)] * self.contract.supply.spot_factor
                for step in range(steps)
            ]
        if self.contract.capacity_charge.kind != CapacityChargeKind.NONE:
            interval_seconds = self.contract.capacity_charge.billing_interval_in_minutes * 60
            self._timesteps_per_billing_period = max(
                1, interval_seconds // self.my_simulation_parameters.seconds_per_timestep
            )

    def _purchase_price(self, timestep: int) -> float:
        supply = self.contract.supply
        additive = self.contract.marginal_purchase_price_components().average
        if supply.kind == SupplyKind.FLAT:
            return supply.working_price_in_euro_per_kwh.average + additive
        if supply.kind == SupplyKind.TIME_OF_USE:
            seconds = timestep * self.my_simulation_parameters.seconds_per_timestep
            moment = self.my_simulation_parameters.start_date.timestamp() + seconds
            import datetime

            when = datetime.datetime.fromtimestamp(moment)
            for band in supply.bands:
                if when.weekday() in band.weekdays and when.hour in band.hours:
                    return band.price_in_euro_per_kwh.average + additive
            return (supply.bands[0].price_in_euro_per_kwh.average + additive) if supply.bands else additive
        assert self._price_series is not None
        return self._price_series[min(timestep, len(self._price_series) - 1)] + additive

    def _injection_price(self) -> float:
        return self.contract.feed_in.rate_in_euro_per_kwh.average

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Outputs the current prices and capacity-charge state; publishes the forecast."""
        stsv.set_output_value(self.price_purchase_output, self._purchase_price(timestep))
        stsv.set_output_value(self.price_injection_output, self._injection_price())

        capacity = self.contract.capacity_charge
        if capacity.kind != CapacityChargeKind.NONE and self.electricity_from_grid_input.source_output is not None:
            power_in_kw = stsv.get_input_value(self.electricity_from_grid_input) * 1e-3
            # Billing-interval mean approximated by the timestep value (exact when the timestep
            # equals the interval; the meter computes the exact peaks for billing, §8.4).
            if capacity.kind == CapacityChargeKind.MONTHLY_PEAK and self._timesteps_per_billing_period:
                steps_per_month = max(1, self.my_simulation_parameters.timesteps // 12)
                if timestep % steps_per_month == 0 and not force_convergence:
                    self._peak_so_far_in_kw = 0.0
            self._peak_so_far_in_kw = max(self._peak_so_far_in_kw, power_in_kw)
            stsv.set_output_value(self.peak_so_far_output, self._peak_so_far_in_kw)
            stsv.set_output_value(self.capacity_charge_output, capacity.price_in_euro_per_kw.average)
        else:
            stsv.set_output_value(self.peak_so_far_output, 0.0)
            stsv.set_output_value(self.capacity_charge_output, 0.0)

        # 24 h price forecast for MPC (same mechanism as generic_price_signal.py, §8.3).
        if timestep == 0 and self._price_series is not None:
            steps_per_day = int(24 * 3600 / self.my_simulation_parameters.seconds_per_timestep)
            additive = self.contract.marginal_purchase_price_components().average
            SingletonSimRepository().set_entry(
                key=SingletonDictKeyEnum.PRICEPURCHASEFORECAST24H,
                entry=[price + additive for price in self._price_series[:steps_per_day]],
            )
            SingletonSimRepository().set_entry(
                key=SingletonDictKeyEnum.PRICEINJECTIONFORECAST24H,
                entry=[self._injection_price()] * steps_per_day,
            )

    def i_save_state(self) -> None:
        """Saves the peak tracker."""
        self._saved_peak_in_kw = self._peak_so_far_in_kw

    def i_restore_state(self) -> None:
        """Restores the peak tracker."""
        self._peak_so_far_in_kw = self._saved_peak_in_kw

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Nothing to double check."""

    def write_to_report(self) -> List[str]:
        """Report entry."""
        return [
            f"Tariff provider: contract {self.contract.id} ({self.contract.supply.kind.value}) "
            f"for carrier {self.contract.carrier.value}"
        ]

    def get_cost_facts(self) -> Optional[Any]:
        """The provider itself is free of cost (§9.2)."""
        return None
