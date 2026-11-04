"""The suite file, the comparison with ranges and the group accounting, with synthetic numbers.

No range value of the standard appears here: the bounds are made up (``synthetic``), and the public
suite file and template carry structure only.
"""

import json
import pathlib
from typing import Any, Dict

import pytest

from hisim.verification import compare
from hisim.verification.suite import SUITES_DIRECTORY, Suite, SuiteFileError
from tests.verification.synthetic import ranges, suite_mapping

pytestmark = pytest.mark.base

TEMPLATE_FILE = SUITES_DIRECTORY / "ashrae140" / "reference_template.json"


def summaries(**annual: Any) -> Dict[str, Dict[str, float]]:
    """Summaries of one time step: case id -> (heating, cooling) in MWh."""
    return {case: {"annual_heating_in_megawatt_hour": heating, "annual_sensible_cooling_in_megawatt_hour": cooling} for case, (heating, cooling) in annual.items()}


def known_deviation(**fields: Any) -> Dict[str, Any]:
    """A register entry of the human for case 1's cooling."""
    entry = {
        "id": "case1_cooling",
        "category": "known_deviation",
        "standard_clause": "Annex A3",
        "description": "cooling above the range",
        "hisim_treatment": "accepted for now",
        "approved_by": "someone",
        "case": "1",
        "output": "annual_cooling",
    }
    entry.update(fields)
    return entry


def test_the_ashrae_suite_file_reads() -> None:
    """Two groups of 21 and 19 ranges; difference ranges are keyed as the tables state them."""
    suite = Suite.load("ashrae140")

    assert [(group.name, group.ranges_total, group.minimum_required) for group in suite.groups] == [("low_mass", 21, 18), ("high_mass", 19, 17)]
    keys = {(range_case.case, range_case.metric): range_case.key for range_case in suite.range_cases}
    assert keys[("600", "heating")] == "600"
    assert keys[("620", "cooling")] == "620-600"
    assert keys[("695", "heating")] == "695-685"
    assert keys[("910", "heating")] == "900-910"
    assert sum(range_case.not_modelled for range_case in suite.range_cases) == 12
    assert suite.outputs_for("640").daily_hourly == (("load", ("02-01",)), ("zone_air_temperature", ("02-01",)))
    assert suite.outputs_for("900FF").temperature_bins_in_celsius == (-50, 98)
    assert not suite.outputs_for("620").daily_hourly


def test_the_public_template_is_the_suite_structure_with_every_value_null() -> None:
    """The template lists each range key once, in suite order, and carries no number."""
    with open(TEMPLATE_FILE, encoding="utf-8") as template_file:
        template = json.load(template_file)

    assert template["ranges"] == Suite.load("ashrae140").reference_template()
    assert template["units"] == compare.REFERENCE_UNITS
    for metrics in template["ranges"].values():
        for bounds in metrics.values():
            assert bounds is None or bounds == [None, None]
    assert template["ranges"]["650-600"] == {"heating": None, "cooling": [None, None]}


def test_a_group_whose_count_does_not_match_its_ranges_is_refused() -> None:
    """ranges_total is checked against the list."""
    mapping = suite_mapping()
    mapping["groups"]["light"]["ranges_total"] = 6

    with pytest.raises(SuiteFileError, match="ranges_total is 6, the group lists 5 ranges"):
        Suite.from_mapping(mapping)


def test_only_a_known_deviation_names_a_case() -> None:
    """A model or input-mapping entry names no case; a known deviation must."""
    with pytest.raises(SuiteFileError, match="only a known deviation"):
        Suite.from_mapping(suite_mapping([known_deviation(category="model")]))
    with pytest.raises(SuiteFileError, match="names its case and its output"):
        Suite.from_mapping(suite_mapping([known_deviation(output=None)]))


def test_reference_errors_never_show_a_bound() -> None:
    """A reversed range is named by key and metric; the numbers stay out of the message."""
    with pytest.raises(compare.ReferenceFileError) as raised:
        ranges(**{"1": {"heating": [625.0, 475.0], "cooling": None}})

    assert "range 1, heating" in str(raised.value)
    assert "625" not in str(raised.value) and "475" not in str(raised.value)
    with pytest.raises(compare.ReferenceFileError, match="units"):
        compare.ReferenceRanges.from_mapping({"units": "kWh", "ranges": {}})


def test_a_range_takes_the_case_its_difference_or_the_reversed_difference() -> None:
    """Case 2 minus case 1; case 1 minus case 3 for the reversed range."""
    suite = Suite.from_mapping(suite_mapping())
    annual = compare.annual_loads(summaries(**{"1": (500.0, 250.0), "2": (550.0, 200.0), "3": (400.0, 300.0)}))

    values = [compare.range_value(range_case, annual) for range_case in suite.range_cases]

    assert values == [500.0, 250.0, 50.0, 100.0, None]


def test_the_statuses_of_the_ranges() -> None:
    """Inside passes, outside fails, a missing case skips, a case not modelled says so."""
    suite = Suite.from_mapping(suite_mapping())

    results, _ = compare.evaluate(suite, summaries(**{"1": (500.0, 350.0), "2": (550.0, 200.0)}), ranges(), 3600)

    assert [result.status for result in results] == ["pass", "fail", "pass", "skipped", "not_modelled"]
    heating = results[0]
    assert heating.position == pytest.approx(0.5)
    assert heating.distance_to_nearer_bound == pytest.approx(100.0)
    cooling = results[1]
    assert cooling.position == pytest.approx(1.5)
    assert cooling.distance_to_nearer_bound == pytest.approx(-50.0)
    assert results[3].note == "not run: case 3"
    assert results[3].value is None


def test_a_known_deviation_of_the_human_turns_a_failure_into_it() -> None:
    """The entry applies to its time step only when it names one."""
    suite = Suite.from_mapping(suite_mapping([known_deviation(timestep_s=900)]))
    annual = summaries(**{"1": (500.0, 350.0)})

    at_900, _ = compare.evaluate(suite, annual, ranges(), 900)
    at_3600, _ = compare.evaluate(suite, annual, ranges(), 3600)

    assert (at_900[1].status, at_900[1].note) == ("known_deviation", "register entry case1_cooling")
    assert at_3600[1].status == "fail"


def test_without_the_reference_file_every_compared_range_is_skipped() -> None:
    """The simulations still count as run; the note names the missing source."""
    suite = Suite.from_mapping(suite_mapping())

    results, accounts = compare.evaluate(suite, summaries(**{"1": (500.0, 250.0), "2": (550.0, 200.0), "3": (400.0, 300.0)}), None, 3600)

    assert [result.status for result in results] == ["skipped"] * 4 + ["not_modelled"]
    assert results[0].note == "Reference data 'ranges.json' not found (set HISIM_VERIFICATION_DATA)"
    assert results[0].value == 500.0
    assert (accounts[0].attempted, accounts[0].formal_result) == (0, "not_passed")


def test_the_group_account_counts_and_judges() -> None:
    """Three of five met is the minimum; a skipped and a not-modelled range count as not met."""
    suite = Suite.from_mapping(suite_mapping())

    _, passing = compare.evaluate(suite, summaries(**{"1": (500.0, 250.0), "2": (550.0, 200.0)}), ranges(), 3600)
    _, failing = compare.evaluate(suite, summaries(**{"1": (500.0, 350.0), "2": (550.0, 200.0)}), ranges(), 3600)

    account = passing[0]
    assert (account.ranges_total, account.attempted, account.met, account.failed, account.skipped, account.not_modelled) == (5, 3, 3, 0, 1, 1)
    assert account.formal_result == "pass"
    assert account.headline == "attempted ranges met: 3 of 3"
    assert (failing[0].met, failing[0].failed, failing[0].formal_result) == (2, 1, "not_passed")


def test_the_private_reference_file_has_the_template_layout(reference_file: pathlib.Path) -> None:
    """The filled file reads, with the keys and the null pattern of the template; no bound is shown."""
    with open(reference_file, encoding="utf-8") as private_file:
        private = json.load(private_file)
    template = Suite.load("ashrae140").reference_template()

    compare.ReferenceRanges.from_mapping(private)
    assert list(private["ranges"]) == list(template)
    nulls_differ = [(key, metric) for key, metrics in template.items() for metric in metrics if (private["ranges"][key][metric] is None) != (metrics[metric] is None)]
    assert not nulls_differ
