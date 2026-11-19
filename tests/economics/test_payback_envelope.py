"""The payback band is the envelope of the three worlds' payback years (renovisorissues #73).

Production refused plan sets with ``the band at comparison.discounted_payback_year is 11/9/9``:
the document wrote the LOW world's payback year as ``min`` and the HIGH world's as ``max``, but
which world pays back first depends on which uncertainty dominates the savings. Where the
reference's energy bill dominates, the LOW world (everything cheap) saves least and pays back
last; where the plan's investment dominates, it pays back first. The band is now taken by value
(:class:`~hisim.economics.results.PaybackEnvelope`): ``min`` the earliest world, ``max`` the latest
(``null`` as soon as one world never pays back, ``null`` reading as +infinity), ``best`` the
central world.
"""

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import List

import pytest

from hisim.economics.parameters import EconomicParameters
from hisim.economics.results import (
    PaybackEnvelope,
    cumulative_discounted_savings,
    discounted_payback_year,
)
from hisim.economics.staged import StagedEvaluator
from hisim.economics.staged_document import BandOrderError, StagedDocument
from hisim.economics.uncertainty import UncertainValue

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    baseline_stage,
    brownfield_perspective,
    envelope_stage,
    heat_pump_stage,
    write_database,
)

pytestmark = pytest.mark.base

HORIZON = 20
INTEREST = 0.03


def _series(year_zero: UncertainValue, yearly: UncertainValue) -> SimpleNamespace:
    """A stand-in result: a year-0 amount, then the same yearly amount for the horizon."""
    return SimpleNamespace(
        annual_cost_series_nominal_in_euro=[year_zero] + [yearly] * HORIZON,
        parameters=SimpleNamespace(interest_rate=INTEREST, observation_period_in_years=HORIZON),
    )


def _payback_by_slot(reference: SimpleNamespace, plan: SimpleNamespace) -> dict:
    """Each world's payback year, as :func:`~hisim.economics.results.compare` derives it."""
    curves = cumulative_discounted_savings(reference, plan)  # type: ignore[arg-type]
    return {slot: discounted_payback_year(curve) for slot, curve in curves.items()}


def _band(payback_by_slot: dict) -> dict:
    """The document's band for those years."""
    return PaybackEnvelope.of(payback_by_slot).to_band()


class TestThePaybackYear:
    """A world's payback year is the first year from which its savings curve stays at or above zero (#102).

    The curves are cumulative discounted savings, reference minus plan, so at or above zero means repaid. Before
    renovisorissues #102 the year was the first year after 0 at which the curve reached zero, whatever it did later,
    and a plan that ended in deficit could state "pays back in year N".
    """

    def test_a_curve_that_crosses_zero_and_ends_in_deficit_has_no_payback(self):
        """Repaid in year 2 and 3, in deficit at the horizon: no payback year, so it cannot contradict the NPV."""
        assert discounted_payback_year([-100.0, -20.0, 30.0, 10.0, -5.0]) is None

    def test_a_curve_that_starts_repaid_and_ends_in_deficit_has_no_payback(self):
        """A plan cheaper at first and dearer to run: above zero from year 0, below it at the horizon."""
        assert discounted_payback_year([50.0, 20.0, -10.0, -40.0]) is None

    def test_a_curve_that_dips_rises_and_falls_back_pays_back_at_its_last_return(self):
        """Repaid in year 2, below zero again in year 3 (a replacement), repaid for good from year 4."""
        assert discounted_payback_year([-100.0, -40.0, 10.0, -5.0, 20.0, 30.0]) == 4

    def test_a_curve_repaid_from_year_zero_pays_back_in_year_zero(self):
        """A plan no dearer from the start (grants covering the investment) pays back in year 0."""
        assert discounted_payback_year([0.0, 5.0, 10.0]) == 0
        assert discounted_payback_year([200.0, 150.0, 120.0]) == 0

    def test_a_curve_that_never_reaches_zero_has_no_payback(self):
        """Below zero to the horizon: no payback year."""
        assert discounted_payback_year([-100.0, -80.0, -60.0]) is None

    def test_a_curve_that_reaches_zero_exactly_at_the_horizon_pays_back_there(self):
        """Zero counts as repaid, so a curve ending at exactly zero pays back in its last year."""
        assert discounted_payback_year([-100.0, -50.0, 0.0]) == 2

    def test_an_empty_curve_has_no_payback(self):
        """No years, no payback year."""
        assert discounted_payback_year([]) is None


class TestTheEnvelope:
    """The band is ordered whichever uncertainty dominates the savings."""

    def test_an_energy_dominated_band_is_no_longer_reversed(self):
        """The production shape: an exact investment, uncertain bills -- the LOW world pays back last."""
        reference = _series(UncertainValue.exact(0.0), UncertainValue(2000.0, 1600.0, 2600.0))
        plan = _series(UncertainValue.exact(10000.0), UncertainValue(900.0, 720.0, 1170.0))
        payback = _payback_by_slot(reference, plan)
        assert payback["low"] > payback["best_estimate"] >= payback["high"], payback

        band = _band(payback)

        assert band == {"min": payback["high"], "best": payback["best_estimate"], "max": payback["low"]}
        StagedDocument.assert_bands_ordered({"comparison": {"discounted_payback_year": band}})

    def test_an_investment_dominated_band_keeps_its_order(self):
        """An uncertain investment, exact bills: the LOW world (cheap investment) pays back first."""
        reference = _series(UncertainValue.exact(0.0), UncertainValue.exact(2000.0))
        plan = _series(UncertainValue(12000.0, 8000.0, 16000.0), UncertainValue.exact(900.0))
        payback = _payback_by_slot(reference, plan)
        assert payback["low"] < payback["best_estimate"] < payback["high"], payback

        band = _band(payback)

        assert band == {"min": payback["low"], "best": payback["best_estimate"], "max": payback["high"]}
        StagedDocument.assert_bands_ordered({"comparison": {"discounted_payback_year": band}})

    def test_a_world_that_never_pays_back_opens_the_late_end(self):
        """``null`` is "never within the horizon", the latest answer there is, so it is ``max``."""
        assert _band({"low": None, "best_estimate": 9, "high": 7}) == {"min": 7, "best": 9, "max": None}
        assert _band({"low": 7, "best_estimate": 9, "high": None}) == {"min": 7, "best": 9, "max": None}

    def test_only_one_world_pays_back(self):
        """The earliest end is that world's year; the central world states its own never."""
        assert _band({"low": None, "best_estimate": None, "high": 6}) == {"min": 6, "best": None, "max": None}

    def test_no_world_pays_back(self):
        """Three nevers are a band of three nulls."""
        assert _band({"low": None, "best_estimate": None, "high": None}) == {
            "min": None,
            "best": None,
            "max": None,
        }

    def test_a_missing_world_is_refused_rather_than_read_as_never(self):
        """None means "never within the horizon", so a world nobody computed must not become one.

        Reading a missing slot as None would state an open range (``latest`` None) that no curve
        supports; the envelope names the missing world instead.
        """
        with pytest.raises(ValueError, match="high"):
            PaybackEnvelope.of({"low": 7, "best_estimate": 9})

    def test_the_envelope_is_ordered_for_every_combination(self):
        """With null as +infinity, earliest <= central <= latest for every triple of years."""
        values: List = [None, 3, 7, 12]
        for low in values:
            for best in values:
                for high in values:
                    envelope = PaybackEnvelope.of({"low": low, "best_estimate": best, "high": high})
                    StagedDocument.assert_bands_ordered({"discounted_payback_year": envelope.to_band()})


class TestTheOrderCheck:
    """The document check reads a null payback end as +infinity instead of skipping the band."""

    def test_a_reversed_band_with_a_null_end_is_refused(self):
        """Before #73 a band with a null end was skipped, so this reversed one passed."""
        with pytest.raises(BandOrderError):
            StagedDocument.assert_bands_ordered(
                {"comparison": {"discounted_payback_year": {"min": None, "best": 9, "max": 11}}}
            )

    def test_the_production_band_is_refused(self):
        """``11/9/9``, as production wrote it."""
        with pytest.raises(BandOrderError):
            StagedDocument.assert_bands_ordered(
                {"comparison": {"discounted_payback_year": {"min": 11, "best": 9, "max": 9}}}
            )

    def test_an_open_late_end_is_accepted(self):
        """``7/9/null``: two worlds pay back, one never does."""
        StagedDocument.assert_bands_ordered(
            {"comparison": {"discounted_payback_year": {"min": 7, "best": 9, "max": None}}}
        )

    def test_a_null_end_elsewhere_is_left_to_the_schema(self):
        """No other band states a meaning for null, so the order check does not guess one."""
        StagedDocument.assert_bands_ordered({"plan": {"totals": {"npv_in_euro": {"min": None, "best": 2, "max": 1}}}})


class TestTheWrittenDocument:
    """A whole staged document with the production slot years writes an ordered band."""

    def test_the_issues_slot_years_write_as_an_ordered_band(self, tmp_path):
        """Slot years 11/9/9 (LOW/central/HIGH) are written 9/9/11, and the document is written."""
        database = write_database(str(tmp_path / "database"))
        parameters = EconomicParameters(
            observation_period_in_years=SyntheticPlan.HORIZON,
            interest_rate=SyntheticPlan.INTEREST_RATE,
            country=SyntheticPlan.COUNTRY,
            price_basis_year=SyntheticPlan.YEAR,
            co2_price_scenario="none",
            apply_subsidies=False,
        )
        perspective = brownfield_perspective()
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0), heat_pump_stage(4)], parameters, perspective
        )
        comparison = replace(
            result.comparison, discounted_payback_years={"low": 11, "best_estimate": 9, "high": 9}
        )
        result = replace(result, comparison=comparison)
        path = tmp_path / StagedDocument.FILE_NAME

        StagedDocument(
            result,
            parameters,
            perspective,
            measure_ids={
                SyntheticPlan.ENVELOPE_SUBJECT: "external_insulation",
                SyntheticPlan.HEAT_PUMP_SUBJECT: "heating_system",
            },
            unpriced_subjects={SyntheticPlan.ENVELOPE_SUBJECT},
        ).write(path)

        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["comparison"]["discounted_payback_year"] == {"min": 9, "best": 9, "max": 11}
