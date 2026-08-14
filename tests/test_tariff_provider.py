"""Tests for the tariff provider component (cost_spec.md §8.3).

The point of the component is that control and billing read *one* contract. These tests pin
that property where it can actually break: the price a controller sees per timestep must
integrate to the bill `apply_tariff` charges for the same load, for every supply kind (§8.4).
Both sides go through `tariffs.marginal_purchase_price_in_euro_per_kwh`, so a future change to
price selection has to break these tests before it can make the two drift apart.

**How it is covered.** No golden files and no database: every test builds an in-memory contract
with round prices, runs the component over one simulated day, and compares the *integral* of the
published price series against the bill for exactly that energy. The expected value is therefore
never a stored constant — it is recomputed from the same load and the same contract on the
billing side, which is what makes the test an agreement check rather than a second copy of the
formula. The three supply kinds are covered separately because each selects its price
differently (constant / weekday-hour band mask / spot series x `spot_factor`), and a fourth test
pins the MPC forecast against the published output so the forecast cannot become a third
implementation.

**Error class.** A failure here is the §8.1 consistency problem returning: the simulation
optimized against one price and the lifecycle cost engine billed another. That is a *coupling*
defect between `hisim/components/tariff_provider.py` and `hisim/economics/tariffs.py`, not a
pricing-data problem and not an evaluator problem — the same contract is used on both sides here,
so the data cannot be at fault. The duplication that made this possible was removed with the
tariff straggler commit (cost-spec-v2 §2.2); these tests are what keeps it removed.
"""

# clean

import datetime

import pytest

from hisim.components.tariff_provider import TariffProvider, TariffProviderConfig
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import BillingDeterminants
from hisim.economics.tariffs import (
    CapacityCharge,
    CapacityChargeKind,
    SupplyKind,
    TariffContract,
    TariffSupply,
    TimeOfUseBand,
    apply_tariff,
    synthetic_reference_spot_series,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base

#: One kWh bought in every timestep, so the bill is the plain sum of the published prices.
ENERGY_PER_TIMESTEP_IN_KWH = 1.0


def make_provider(contract: TariffContract, seconds_per_timestep: int = 3600) -> TariffProvider:
    """A provider on an in-memory contract, prepared for one simulated day.

    The config names a contract id (`SYNTHETIC_TEST`) that no catalog file holds; the contract is
    assigned directly afterwards, so the tests never touch the shipped tariff directory and a
    price-data PR cannot move their expected values. `i_prepare_simulation()` is what resamples
    the spot series onto the timestep grid and derives the capacity billing interval, so it must
    run before any price is read.
    """
    parameters = SimulationParameters.one_day_only(year=2024, seconds_per_timestep=seconds_per_timestep)
    provider = TariffProvider(
        my_simulation_parameters=parameters,
        config=TariffProviderConfig(
            building_name="BUI1",
            name="TariffProvider",
            tariff_contract_id="SYNTHETIC_TEST",
            forecast_horizon_in_hours=24,
        ),
    )
    provider.contract = contract
    provider.i_prepare_simulation()
    return provider


def additive_components() -> TariffSupply:
    """The non-energy per-kWh components every contract below shares.

    Markup, grid fee and taxes/levies are added to whatever the energy price of the moment is,
    independently of the supply kind — that is exactly why they belong in a shared builder: each
    test then varies only the *energy* half and the agreement it checks still has to hold with a
    realistic 0.162 EUR/kWh of additive components on top. They are stored separately from the
    energy price because the macroeconomic view strips the tax and levy share (§3.3).
    """
    return TariffSupply(
        kind=SupplyKind.FLAT,
        markup_in_euro_per_kwh=UncertainValue.exact(0.017),
        grid_fee_in_euro_per_kwh=UncertainValue.exact(0.094),
        taxes_and_levies_in_euro_per_kwh=UncertainValue.exact(0.051),
    )


def make_contract(supply: TariffSupply) -> TariffContract:
    """A contract carrying the given supply, no standing charge (it is not a per-kWh price).

    The standing charge is deliberately zero: it is an annual amount that lands in its own
    ENERGY_STANDING category and would appear in the bill without ever appearing in a
    per-timestep marginal price, so leaving it out keeps the comparison below an exact identity
    instead of one needing a correction term. Everything else a contract can carry (capacity
    charge, feed-in, §14a discount) is left at its NONE default for the same reason.
    """
    return TariffContract(
        id="TEST_AGREEMENT",
        carrier=EnergyCarrier.ELECTRICITY,
        country="DE",
        region=None,
        valid_from_year=2024,
        supply=supply,
        standing_charge_in_euro_per_year=UncertainValue.exact(0.0),
        source_ids=("inline:price agreement test",),
    )


def simulated_prices(provider: TariffProvider) -> list:
    """The price the component publishes in every timestep of the simulated period.

    This is the controller's view of the tariff: exactly what the component writes to its
    purchase-price output each timestep, collected without running the simulator. Every test
    below integrates this series against a constant 1 kWh per timestep and compares the result
    with the bill, so this helper is the "simulation side" of every agreement assertion.
    """
    return [
        provider._purchase_price(step)  # noqa: SLF001 — the published output, under test
        for step in range(provider.my_simulation_parameters.timesteps)
    ]


class TestSimulationPriceEqualsBillingPrice:
    """§8.4: what the controller pays attention to is what the bill charges."""

    def test_flat(self):
        """FLAT: every timestep sees the same all-in price, and the bill is that price x kWh."""
        supply = additive_components()
        supply.working_price_in_euro_per_kwh = UncertainValue.exact(0.21)
        provider = make_provider(make_contract(supply))
        prices = simulated_prices(provider)
        assert len(set(prices)) == 1

        energy = ENERGY_PER_TIMESTEP_IN_KWH * len(prices)
        bill = apply_tariff(
            BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=energy),
            provider.contract,
        )
        simulated_cost = sum(price * ENERGY_PER_TIMESTEP_IN_KWH for price in prices)
        assert bill.by_category[CostCategory.ENERGY_WORKING].best_estimate == pytest.approx(simulated_cost)

    def test_time_of_use(self):
        """ToU: the bands the provider selects per timestep are the bands the bill charges."""
        supply = additive_components()
        supply.kind = SupplyKind.TIME_OF_USE
        supply.bands = [
            TimeOfUseBand(
                name="night",
                price_in_euro_per_kwh=UncertainValue.exact(0.15),
                hours=list(range(0, 6)) + list(range(22, 24)),
            ),
            TimeOfUseBand(
                name="day",
                price_in_euro_per_kwh=UncertainValue.exact(0.28),
                hours=list(range(6, 22)),
            ),
        ]
        provider = make_provider(make_contract(supply))
        prices = simulated_prices(provider)

        # The meter's job, done here explicitly: attribute each timestep's kWh to its band.
        per_band = {band.name: 0.0 for band in supply.bands}
        start = provider.my_simulation_parameters.start_date
        for step in range(provider.my_simulation_parameters.timesteps):
            moment = start + datetime.timedelta(
                seconds=step * provider.my_simulation_parameters.seconds_per_timestep
            )
            band_name = "night" if moment.hour in supply.bands[0].hours else "day"
            per_band[band_name] += ENERGY_PER_TIMESTEP_IN_KWH
        assert all(energy > 0 for energy in per_band.values())  # both bands are exercised

        bill = apply_tariff(
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=sum(per_band.values()),
                energy_bought_per_band_in_kwh=per_band,
            ),
            provider.contract,
        )
        simulated_cost = sum(price * ENERGY_PER_TIMESTEP_IN_KWH for price in prices)
        assert bill.by_category[CostCategory.ENERGY_WORKING].best_estimate == pytest.approx(simulated_cost)

    def test_dynamic(self):
        """DYNAMIC: the spot value (times spot_factor) plus components, both sides."""
        supply = additive_components()
        supply.kind = SupplyKind.DYNAMIC
        supply.spot_series = "__synthetic__"
        supply.spot_factor = 1.1
        provider = make_provider(make_contract(supply))
        prices = simulated_prices(provider)
        assert len(set(prices)) > 1  # the profile actually varies

        # The meter integrates the *energy-only* spot cost; the engine adds the components.
        hourly = synthetic_reference_spot_series()
        seconds = provider.my_simulation_parameters.seconds_per_timestep
        spot_cost = sum(
            hourly[min(int(step * seconds // 3600), len(hourly) - 1)]
            * supply.spot_factor
            * ENERGY_PER_TIMESTEP_IN_KWH
            for step in range(provider.my_simulation_parameters.timesteps)
        )
        bill = apply_tariff(
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=ENERGY_PER_TIMESTEP_IN_KWH * len(prices),
                cost_integrated_in_euro=spot_cost,
            ),
            provider.contract,
        )
        simulated_cost = sum(price * ENERGY_PER_TIMESTEP_IN_KWH for price in prices)
        assert bill.by_category[CostCategory.ENERGY_WORKING].best_estimate == pytest.approx(simulated_cost)

    def test_forecast_matches_the_published_price(self):
        """The MPC forecast is the same price series the outputs carry (no second formula)."""
        from hisim.sim_repository_singleton import SingletonDictKeyEnum, SingletonSimRepository

        supply = additive_components()
        supply.kind = SupplyKind.DYNAMIC
        supply.spot_series = "__synthetic__"
        provider = make_provider(make_contract(supply))
        stsv = _single_time_step_values(provider)
        provider.i_simulate(0, stsv, False)
        forecast = SingletonSimRepository().get_entry(SingletonDictKeyEnum.PRICEPURCHASEFORECAST24H)
        assert forecast[:5] == pytest.approx([provider._purchase_price(step) for step in range(5)])  # noqa: SLF001


class TestCapacityChargeMarginalIsAPeakSignal:
    """§8.3: `CapacityChargeMarginal` costs money only where a kilowatt more raises the peak.

    The output exists so a rule-based EMS can shave peaks without any tariff bookkeeping: staying
    under the period's running maximum has to read as free, touching it as expensive. A component
    that emitted the price on every timestep would tell a controller that load is always worth
    shifting, which is exactly the decision the capacity charge does *not* pay for.
    """

    def test_below_the_running_peak_costs_nothing(self):
        """Below the period peak the signal is 0; at or above it, the full contract price."""
        provider = capacity_charge_provider(CapacityChargeKind.ANNUAL_PEAK)
        stsv, grid_index = connected_grid_input(provider)

        charges = [
            capacity_charge_at(provider, stsv, grid_index, timestep=0, power_in_watt=5_000.0),
            capacity_charge_at(provider, stsv, grid_index, timestep=1, power_in_watt=3_000.0),
            capacity_charge_at(provider, stsv, grid_index, timestep=2, power_in_watt=6_000.0),
        ]
        assert charges == [CAPACITY_PRICE_IN_EURO_PER_KW, 0.0, CAPACITY_PRICE_IN_EURO_PER_KW]
        assert stsv.values[provider.peak_so_far_output.global_index] == pytest.approx(6.0)

    def test_the_signal_returns_after_the_billing_period_resets(self):
        """A MONTHLY_PEAK period starts with no peak, so its first draw sets one and is priced."""
        provider = capacity_charge_provider(CapacityChargeKind.MONTHLY_PEAK)
        stsv, grid_index = connected_grid_input(provider)
        # One simulated day over twelve periods: the reset falls on every even timestep.
        steps_per_period = max(1, provider.my_simulation_parameters.timesteps // 12)
        assert steps_per_period == 2

        capacity_charge_at(provider, stsv, grid_index, timestep=0, power_in_watt=9_000.0)
        below = capacity_charge_at(provider, stsv, grid_index, timestep=1, power_in_watt=2_000.0)
        after_reset = capacity_charge_at(provider, stsv, grid_index, timestep=2, power_in_watt=2_000.0)
        assert below == 0.0
        assert after_reset == CAPACITY_PRICE_IN_EURO_PER_KW

    def test_a_discarded_convergence_iteration_does_not_change_the_signal(self):
        """The peak a rolled-back iteration saw must not silence the signal in the kept one."""
        provider = capacity_charge_provider(CapacityChargeKind.ANNUAL_PEAK)
        stsv, grid_index = connected_grid_input(provider)
        capacity_charge_at(provider, stsv, grid_index, timestep=1, power_in_watt=5_000.0)

        provider.i_save_state()
        discarded = capacity_charge_at(provider, stsv, grid_index, timestep=3, power_in_watt=9_000.0)
        provider.i_restore_state()
        kept = capacity_charge_at(provider, stsv, grid_index, timestep=3, power_in_watt=7_000.0)

        assert discarded == CAPACITY_PRICE_IN_EURO_PER_KW  # 9 kW was a new peak in that iteration
        assert kept == CAPACITY_PRICE_IN_EURO_PER_KW  # 7 kW is one too, against the restored 5 kW
        assert stsv.values[provider.peak_so_far_output.global_index] == pytest.approx(7.0)


#: Capacity price of the contracts below, in EUR/kW — round, and unlike any per-kWh price here.
CAPACITY_PRICE_IN_EURO_PER_KW = 100.0


def capacity_charge_provider(kind: CapacityChargeKind) -> TariffProvider:
    """A FLAT-supply provider whose contract carries a capacity charge of the given kind.

    The billing interval is one hour so that it is a whole multiple of the hourly timestep the
    tests run on, which is what `validate_billing_interval` demands before a run may start. The
    supply half is deliberately the plainest one available: these tests are about the power-based
    term, and a varying energy price would only add noise to the outputs they read.
    """
    supply = additive_components()
    supply.working_price_in_euro_per_kwh = UncertainValue.exact(0.21)
    contract = make_contract(supply)
    contract.capacity_charge = CapacityCharge(
        kind=kind,
        price_in_euro_per_kw=UncertainValue.exact(CAPACITY_PRICE_IN_EURO_PER_KW),
        billing_interval_in_minutes=60,
    )
    return make_provider(contract)


def connected_grid_input(provider: TariffProvider):
    """Wires the optional grid-draw input to a value slot, as the simulator would.

    The peak tracker only runs when `ElectricityFromGridInWatt` has a source, so a test that never
    connects it would exercise the zero branch instead of the signal. This stands in for the
    connection the simulator makes: one extra slot in the value buffer, and an output object
    pointing at it. Returns the buffer and the index of the grid slot.
    """
    from hisim.component import ComponentOutput, SingleTimeStepValues

    for index, output in enumerate(provider.outputs):
        output.global_index = index
    grid_index = len(provider.outputs)
    source = ComponentOutput(
        object_name="Meter",
        field_name="ElectricityToGrid",
        load_type=provider.electricity_from_grid_input.loadtype,
        unit=provider.electricity_from_grid_input.unit,
    )
    source.global_index = grid_index
    provider.electricity_from_grid_input.source_output = source
    return SingleTimeStepValues(grid_index + 1), grid_index


def capacity_charge_at(provider, stsv, grid_index: int, timestep: int, power_in_watt: float) -> float:
    """Runs one iteration at the given draw and returns the marginal capacity charge published."""
    stsv.values[grid_index] = power_in_watt
    provider.i_simulate(timestep, stsv, False)
    return stsv.values[provider.capacity_charge_output.global_index]


def _single_time_step_values(provider: TariffProvider):
    """A value buffer for the provider's outputs, indexed as the simulator would.

    `i_simulate` writes through `output.global_index`, which the real simulator assigns while
    wiring all components together; this hands out the same indices for one isolated component so
    the forecast test can call `i_simulate` without building a `Simulator`.
    """
    from hisim.component import SingleTimeStepValues

    for index, output in enumerate(provider.outputs):
        output.global_index = index
    return SingleTimeStepValues(len(provider.outputs))
