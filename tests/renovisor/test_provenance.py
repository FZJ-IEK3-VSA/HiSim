"""The value wrapper every leaf of ``result.json`` is made of.

Three claims are worth a test here, and they are all about shape rather than about numbers.
A provenance object serializes to the four keys the contract expects and omits the period when it
has none. A :class:`Range` is *one* value of *one* provenance object -- decision A9's whole point,
since three sibling fields with three provenances would be three different claims about one
figure. And a period knows what share of a year it is, because every annual figure of a short run
is divided by exactly that number.
"""

import datetime

import pytest

from hisim.renovisor.provenance import MissingField, Period, ProvenancedValue, Range
from hisim.renovisor.vocabulary import Provenance

pytestmark = pytest.mark.base


def test_a_provenance_object_carries_value_provenance_and_source() -> None:
    """The three keys every leaf has, with the enum written as its wire value."""
    document = ProvenancedValue(
        value=12.5, provenance=Provenance.SIMULATED, source="all_kpis.json: 'x'"
    ).to_json()

    assert document == {"value": 12.5, "provenance": "SIMULATED", "source": "all_kpis.json: 'x'"}
    assert "period" not in document


def test_a_period_bound_value_carries_its_period() -> None:
    """Requirement A14: a figure extrapolated from a day has to say it came from a day."""
    period = Period.from_dates(datetime.datetime(2021, 1, 1), datetime.datetime(2021, 1, 2))

    document = ProvenancedValue(
        value=1.0, provenance=Provenance.PARTIAL, source="scaled", period=period
    ).to_json()

    assert document["period"]["start"] == "2021-01-01T00:00:00"
    assert document["period"]["end"] == "2021-01-02T00:00:00"
    assert document["period"]["fraction_of_year"] == pytest.approx(1 / 365, rel=1e-2)


def test_a_range_is_one_value_and_not_three_fields() -> None:
    """Decision A9/AC9: the band is the value of one provenance object, in HiSim's slot names."""
    document = ProvenancedValue(
        value=Range(low=1.0, best_estimate=2.0, high=4.0),
        provenance=Provenance.PARTIAL,
        source="the engine",
    ).to_json()

    assert document["value"] == {"low": 1.0, "best_estimate": 2.0, "high": 4.0}
    assert set(document) == {"value", "provenance", "source"}


def test_a_mocked_value_may_be_null_or_an_object() -> None:
    """Decision A12: an absent model is a labelled null, never an invented letter."""
    assert ProvenancedValue(None, Provenance.MOCKED, "no procedure").to_json()["value"] is None
    structured = ProvenancedValue({"none": 6}, Provenance.MOCKED, "examples[0]").to_json()
    assert structured["value"] == {"none": 6}


def test_the_engines_uncertain_value_spelling_is_translated_once() -> None:
    """``min``/``max`` is the cost engine's spelling; ``low``/``high`` is the contract's (C3)."""
    band = Range.from_uncertain_value({"min": 1.0, "best_estimate": 2.0, "max": 4.0})
    assert band is not None
    assert band.to_json() == {"low": 1.0, "best_estimate": 2.0, "high": 4.0}


def test_a_certain_engine_figure_is_a_degenerate_band() -> None:
    """The engine writes a band with no width as a bare number; it is still a band here."""
    band = Range.from_uncertain_value(7.0)
    assert band == Range(low=7.0, best_estimate=7.0, high=7.0)


def test_an_absent_engine_figure_is_not_a_zero() -> None:
    """``None`` in, ``None`` out: an absent figure must become an absent field, never a zero."""
    assert Range.from_uncertain_value(None) is None
    assert Range.from_uncertain_value({"min": 1.0}) is None
    assert Range.from_uncertain_value("n/a") is None


def test_bounds_without_a_third_number_take_the_midpoint() -> None:
    """The material database states a minimum and a maximum and no best estimate (Q8/Q9)."""
    assert Range.from_bounds(100.0, 200.0) == Range(low=100.0, best_estimate=150.0, high=200.0)


def test_ranges_add_and_scale_slot_by_slot() -> None:
    """The cost engine's own convention: each of the three worlds stays internally consistent."""
    total = Range(1.0, 2.0, 3.0).plus(Range(10.0, 20.0, 30.0))
    assert total == Range(low=11.0, best_estimate=22.0, high=33.0)
    assert total.scaled(0.5) == Range(low=5.5, best_estimate=11.0, high=16.5)


def test_a_full_year_is_recognised_as_one() -> None:
    """A full-year run needs no extrapolation, which is what makes its figures SIMULATED."""
    year = Period.from_dates(datetime.datetime(2021, 1, 1), datetime.datetime(2022, 1, 1))
    day = Period.from_dates(datetime.datetime(2021, 1, 1), datetime.datetime(2021, 1, 2))

    assert year.is_full_year()
    assert not day.is_full_year()


def test_a_missing_field_names_itself_and_says_why() -> None:
    """Decision R8: an absent field is absent and states its reason, rather than being a zero."""
    entry = MissingField(field="costs.grant_in_euro", reason="no Irish catalogue").to_json()
    assert entry == {"field": "costs.grant_in_euro", "reason": "no Irish catalogue"}
