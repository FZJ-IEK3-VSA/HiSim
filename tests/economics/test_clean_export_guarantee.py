"""Ireland's PV feed-in is the Clean Export Guarantee: a sourced, market-linked rate with its own provenance.

Owner decision (hisim-j27e, renovisorissues #47): Irish suppliers pay for exported electricity under the Clean Export
Guarantee (CEG), at a rate each supplier sets and the CRU requires to reflect the market value of the export (decision
CRU202454). No rate is guaranteed for any duration, so the Irish feed-in row is MARKET_LINKED: it has no nominally
fixed period and escalates with the ELECTRICITY rate from year 1. Germany keeps its EEG fixed tariff, nominal for 20
years. Every feed-in revenue cites its own provenance records: the feed-in row's rate and kind with the row's sources,
and the annualized energy sold, no longer the electricity purchase price and the energy bought.

The engine cases bill 2,000 kWh of export and nothing else at 0 % interest, so every revenue figure is the rate times
the energy, times the escalation factor under test.
"""

import json
import os
import shutil
from dataclasses import replace
from typing import Any, Dict, List

import pytest

from hisim.economics.calculators.energy import priced_contract
from hisim.economics.carriers import EnergyCarrier, revenue_subject
from hisim.economics.catalog_entries import FeedInKind
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, YearZeroPriceLevel
from hisim.economics.facts import BillingDeterminants
from hisim.economics.parameters import EconomicParameters, StatedEnergyPrice
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.provenance import ParameterOrigin, ParameterProvenance
from hisim.economics.results import LifecycleCostResult, RateOrigin
from hisim.economics.timeline import CashFlowEntry, CostCategory
from hisim.economics.uncertainty import UncertainValue

pytestmark = pytest.mark.base


class Case:
    """The figures every case below is stated in."""

    #: The annual export billed, in kWh.
    ENERGY_SOLD_IN_KWH: float = 2000.0

    #: The ELECTRICITY escalation rate the market-linked cases configure, deliberately unlike any default.
    ELECTRICITY_RATE: float = 0.05

    #: The horizon of the engine cases, in years.
    HORIZON_IN_YEARS: int = 5

    #: The Clean Export Guarantee rate band of the shipped Irish row, in EUR/kWh: the lowest standard rate
    #: (PrepayPower, 15.89c including VAT), the midpoint of the four largest suppliers (18.5c to 19.5c) and the
    #: highest standard rate (Community Power, 20c).
    IE_RATE_IN_EURO_PER_KWH: UncertainValue = UncertainValue(best_estimate=0.19, minimum=0.1589, maximum=0.20)

    #: The CRU decision the Irish row cites for the market-linked structure.
    CRU_SOURCE_ID: str = "src_cru_202454_clean_export_guarantee"

    #: The perspective of the engine cases: no subsidies, no existing assets.
    PERSPECTIVE: Perspective = Perspective(
        id="test_greenfield_gross",
        installation_context=InstallationContext.GREENFIELD,
        subsidy_mode=SubsidyMode.none(),
    )


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database, loaded once for the module."""
    return CostDatabase()


def _parameters(country: str, price_basis_year: int, **overrides: Any) -> EconomicParameters:
    """Return zero-interest parameters for one country, the ELECTRICITY rate set to `Case.ELECTRICITY_RATE`.

    Carbon pricing is off and the feed-in escalation rate is zero, so a feed-in revenue that grows can only have grown
    with the ELECTRICITY rate.

    Args:
        country: The country priced.
        price_basis_year: The year the price rows are read at.
        overrides: Further `EconomicParameters` fields.

    Returns:
        The parameters.
    """
    parameters = EconomicParameters(
        observation_period_in_years=Case.HORIZON_IN_YEARS,
        interest_rate=0.0,
        general_price_escalation_rate=0.0,
        investment_price_escalation_rate=0.0,
        co2_price_scenario="none",
        energy_price_escalation_rates={EnergyCarrier.ELECTRICITY: Case.ELECTRICITY_RATE},
        feed_in_escalation_rate=0.0,
        country=country,
        price_basis_year=price_basis_year,
    )
    return replace(parameters, **overrides)


def _evaluate(database: CostDatabase, parameters: EconomicParameters) -> LifecycleCostResult:
    """Evaluate a household that only exports `Case.ENERGY_SOLD_IN_KWH` per year.

    Args:
        database: The cost database.
        parameters: The assumptions.

    Returns:
        The lifecycle cost result.
    """
    inputs = EvaluationInputs(
        simulation_year=parameters.price_basis_year or 2026,
        simulated_period_fraction=1.0,
        billing=[
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=0.0,
                energy_sold_in_kwh=Case.ENERGY_SOLD_IN_KWH,
            )
        ],
    )
    return EconomicEvaluator(database, parameters).evaluate(inputs, Case.PERSPECTIVE)


def _feed_in_entries(result: LifecycleCostResult) -> List[CashFlowEntry]:
    """Return the feed-in revenue entries of a result, in year order."""
    entries = [entry for entry in result.timeline.entries if entry.category is CostCategory.FEED_IN_REVENUE]
    return sorted(entries, key=lambda entry: entry.year)


def _cited_records(result: LifecycleCostResult) -> Dict[str, ParameterProvenance]:
    """Return the provenance records the feed-in revenue cites, keyed by parameter path.

    Every year's entry must cite the same records, so the first year's stand for all.
    """
    entries = _feed_in_entries(result)
    assert entries, "no feed-in revenue was booked"
    assert {entry.provenance_ids for entry in entries} == {entries[0].provenance_ids}
    assert result.ledger is not None
    records = [result.ledger.get(record_id) for record_id in entries[0].provenance_ids]
    return {record.parameter: record for record in records}


class TestTheIrishRow:
    """The shipped Irish feed-in row is the sourced Clean Export Guarantee rate."""

    def test_it_is_market_linked_with_the_sourced_band(self, database) -> None:
        """MARKET_LINKED, 15.89c / 19c / 20c, citing the CRU decision and no AI estimate."""
        entry = database.get_energy_price(EnergyCarrier.ELECTRICITY_FEED_IN, 2026, "IE")
        assert entry.feed_in_kind is FeedInKind.MARKET_LINKED
        assert entry.working_price_in_euro_per_kwh == Case.IE_RATE_IN_EURO_PER_KWH
        assert Case.CRU_SOURCE_ID in entry.source_ids
        assert "src_ai_estimates" not in entry.source_ids

    def test_no_unsourced_later_row_replaces_it(self, database) -> None:
        """A run priced in 2035 still starts from the 2026 rate; the market-linked path projects it."""
        assert database.get_energy_price(EnergyCarrier.ELECTRICITY_FEED_IN, 2035, "IE").year == 2026

    def test_germany_keeps_its_fixed_tariff(self, database) -> None:
        """The German EEG row declares nothing and is a FIXED_TARIFF."""
        entry = database.get_energy_price(EnergyCarrier.ELECTRICITY_FEED_IN, 2026, "DE")
        assert entry.feed_in_kind is FeedInKind.FIXED_TARIFF


class TestTheProjection:
    """How the feed-in revenue evolves over the horizon, per country."""

    def test_the_irish_revenue_escalates_with_electricity_from_year_one(self, database) -> None:
        """Year t earns rate x energy x 1.05**(t-1): no year is held at the year-1 figure."""
        result = _evaluate(database, _parameters("IE", 2026))
        amounts = [entry.amount_in_euro.best_estimate for entry in _feed_in_entries(result)]
        year_one = -Case.ENERGY_SOLD_IN_KWH * Case.IE_RATE_IN_EURO_PER_KWH.best_estimate
        expected = [year_one * (1.0 + Case.ELECTRICITY_RATE) ** (year - 1) for year in range(1, 6)]
        assert amounts == pytest.approx(expected)

    def test_the_irish_revenue_follows_the_country_default_electricity_rate(self, database) -> None:
        """Without a configured rate, the ELECTRICITY rate of escalation_defaults_IE.json applies."""
        result = _evaluate(database, _parameters("IE", 2026, energy_price_escalation_rates={}))
        default_rate = database.get_escalation_defaults("IE").carrier_rates[EnergyCarrier.ELECTRICITY]
        amounts = [entry.amount_in_euro.best_estimate for entry in _feed_in_entries(result)]
        assert amounts[4] / amounts[0] == pytest.approx((1.0 + default_rate) ** 4)

    def test_the_german_revenue_stays_nominal(self, database) -> None:
        """The EEG tariff ignores the electricity rate: five equal years at the 2026 rate."""
        result = _evaluate(database, _parameters("DE", 2026))
        rate = database.get_energy_price(EnergyCarrier.ELECTRICITY_FEED_IN, 2026, "DE").working_price_in_euro_per_kwh
        amounts = [entry.amount_in_euro.best_estimate for entry in _feed_in_entries(result)]
        assert amounts == pytest.approx([-Case.ENERGY_SOLD_IN_KWH * rate.best_estimate] * Case.HORIZON_IN_YEARS)

    def test_the_assumptions_state_the_rate_the_irish_feed_in_used(self, database) -> None:
        """The table's feed-in rate is the ELECTRICITY rate that applied, not the configured one it ignores."""
        irish = _evaluate(database, _parameters("IE", 2026)).assumptions
        german = _evaluate(database, _parameters("DE", 2026)).assumptions
        assert irish is not None and german is not None
        assert irish.escalation_rates["feed-in"].rate == Case.ELECTRICITY_RATE
        assert irish.tariffs[EnergyCarrier.ELECTRICITY.value].feed_in_kind is FeedInKind.MARKET_LINKED
        assert german.escalation_rates["feed-in"].rate == 0.0
        assert german.escalation_rates["feed-in"].origin is RateOrigin.CONFIGURATION

    def test_a_stated_irish_rate_stays_market_linked(self, database) -> None:
        """A household's own CEG rate replaces the database's figure, not the way it is paid."""
        stated = StatedEnergyPrice(working_price_in_euro_per_kwh=UncertainValue.exact(0.185))
        parameters = _parameters("IE", 2026, energy_prices={EnergyCarrier.ELECTRICITY_FEED_IN: stated})
        contract = priced_contract(EnergyCarrier.ELECTRICITY, 2026, database, parameters)
        assert contract.feed_in.kind is FeedInKind.MARKET_LINKED
        assert contract.feed_in.rate_in_euro_per_kwh == UncertainValue.exact(0.185)

    def test_the_year_zero_shift_moves_the_irish_revenue_with_electricity(self, database) -> None:
        """Two years from price basis to plan start scale an Irish year-1 revenue by 1.05**2, a German one not."""
        for country, expected_factor in (("IE", (1.0 + Case.ELECTRICITY_RATE) ** 2), ("DE", 1.0)):
            parameters = _parameters(country, 2026)
            level = YearZeroPriceLevel(years=2, parameters=parameters, database=database, price_basis_year=2026)
            level.add_energy([priced_contract(EnergyCarrier.ELECTRICITY, 2026, database, parameters)], {})
            entry = CashFlowEntry(
                year=1,
                amount_in_euro=UncertainValue.exact(-100.0),
                category=CostCategory.FEED_IN_REVENUE,
                subject=revenue_subject(EnergyCarrier.ELECTRICITY),
            )
            assert level.entry(entry).amount_in_euro.best_estimate == pytest.approx(-100.0 * expected_factor)


class TestTheProvenance:
    """The feed-in revenue cites its own rate, kind and energy, never the purchase price."""

    def test_the_irish_revenue_cites_the_clean_export_guarantee_row(self, database) -> None:
        """Rate and kind from energy_prices_IE.ELECTRICITY_FEED_IN@2026 with its sources, and the energy sold."""
        records = _cited_records(_evaluate(database, _parameters("IE", 2026)))
        row = "energy_prices_IE.ELECTRICITY_FEED_IN@2026"
        assert set(records) == {
            f"{row}.working_price_in_euro_per_kwh",
            f"{row}.feed_in_kind",
            "simulation.ELECTRICITY.energy_sold",
        }
        rate = records[f"{row}.working_price_in_euro_per_kwh"]
        assert rate.origin is ParameterOrigin.DATABASE_ENTRY
        assert rate.value == Case.IE_RATE_IN_EURO_PER_KWH
        assert Case.CRU_SOURCE_ID in rate.source_ids
        assert records[f"{row}.feed_in_kind"].value == FeedInKind.MARKET_LINKED.value
        assert records["simulation.ELECTRICITY.energy_sold"].value == Case.ENERGY_SOLD_IN_KWH

    def test_the_german_revenue_cites_the_eeg_row(self, database) -> None:
        """The same records for Germany, with the kind FIXED_TARIFF."""
        records = _cited_records(_evaluate(database, _parameters("DE", 2026)))
        row = "energy_prices_DE.ELECTRICITY_FEED_IN@2026"
        assert records[f"{row}.feed_in_kind"].value == FeedInKind.FIXED_TARIFF.value
        assert f"{row}.working_price_in_euro_per_kwh" in records
        assert not any(".ELECTRICITY@" in parameter for parameter in records)

    def test_a_stated_rate_cites_the_plan(self, database) -> None:
        """A stated feed-in rate is a REQUEST record, beside the energy sold."""
        stated = StatedEnergyPrice(working_price_in_euro_per_kwh=UncertainValue.exact(0.185))
        parameters = _parameters("IE", 2026, energy_prices={EnergyCarrier.ELECTRICITY_FEED_IN: stated})
        records = _cited_records(_evaluate(database, parameters))
        assert {record.origin for record in records.values()} == {
            ParameterOrigin.REQUEST,
            ParameterOrigin.SIMULATION_OUTPUT,
        }

    def test_the_contract_cites_the_feed_in_sources(self, database) -> None:
        """The electricity contract carries the feed-in rate, so the assumptions table cites the row's sources too."""
        contract = priced_contract(EnergyCarrier.ELECTRICITY, 2026, database, _parameters("IE", 2026))
        assert Case.CRU_SOURCE_ID in contract.source_ids


class TestTheLoader:
    """A price row can declare only the feed-in kinds that pay a flat rate, and only on the feed-in row."""

    @staticmethod
    def _database_with_row_change(tmp_path, carrier: str, kind: str) -> None:
        """Load a copy of the shipped database whose Irish `carrier` row of 2026 states `feed_in_kind: kind`.

        Args:
            tmp_path: The test's temporary directory.
            carrier: The carrier whose row is changed.
            kind: The kind written.
        """
        directory = os.path.join(str(tmp_path), "cost_database")
        shutil.copytree(CostDatabase.DEFAULT_PATH, directory)
        path = os.path.join(directory, "energy_prices_IE.json")
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
        for item in raw["entries"]:
            if item["carrier"] == carrier and item["year"] == 2026:
                item["feed_in_kind"] = kind
        with open(path, "w", encoding="utf-8") as file:
            json.dump(raw, file)
        CostDatabase(directory)

    def test_a_kind_on_another_carrier_is_refused(self, tmp_path) -> None:
        """Only exported electricity is paid for."""
        with pytest.raises(CostDataError, match="only an ELECTRICITY_FEED_IN row"):
            self._database_with_row_change(tmp_path, "ELECTRICITY", "MARKET_LINKED")

    def test_a_spot_referenced_row_is_refused(self, tmp_path) -> None:
        """A spot-referenced remuneration needs a tariff contract with its spot series."""
        with pytest.raises(CostDataError, match="SPOT_REFERENCED"):
            self._database_with_row_change(tmp_path, "ELECTRICITY_FEED_IN", "SPOT_REFERENCED")
