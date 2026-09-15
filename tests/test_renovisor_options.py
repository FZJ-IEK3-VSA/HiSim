"""Tests of the option reader: what it accepts, what it refuses, and what it reports.

Every one of these cases is something a caller can get wrong, and each has to come back as a
validation error naming the JSON path of the offending value rather than as a crash or a silent
default.
"""

from typing import Any, Mapping

import pytest

from hisim.renovisor.catalogue import Catalogue, MeasureSpec
from hisim.renovisor.options import Default, Options
from hisim.renovisor.reasons import ReasonCode, ValidationError
from hisim.renovisor.report import MappingReport, ReportStatus

pytestmark = pytest.mark.base

ENTRY_PATH = "package.measures[3]"


@pytest.fixture(name="catalogue", scope="module")
def fixture_catalogue() -> Catalogue:
    """The vendored catalogue, loaded once for the module."""
    return Catalogue.load()


def build(spec: MeasureSpec, supplied: Mapping[str, Any]) -> Options:
    """Build an option reader over a fresh report, at a fixed package path."""
    return Options(spec, supplied, MappingReport(), ENTRY_PATH)


def test_enum_returns_the_hisim_value(catalogue: Catalogue) -> None:
    """A supplied enum value is checked against the spec's list and handed back unchanged."""
    options = build(catalogue.by_id("HEATING_SYSTEM"), {"type_of_system": "GAS_HEATING"})
    assert options.enum("type_of_system") == "GAS_HEATING"


def test_unknown_option_key_is_rejected_on_construction(catalogue: Catalogue) -> None:
    """A key the measure does not have is caught before any value is read."""
    with pytest.raises(ValidationError) as error:
        build(catalogue.by_id("HEATING_SYSTEM"), {"colour": "green"})
    assert error.value.reason is ReasonCode.UNKNOWN_OPTION
    assert error.value.path == f"{ENTRY_PATH}.options.colour"


def test_unknown_enum_value_is_rejected(catalogue: Catalogue) -> None:
    """A value outside the catalogue's list names the path and the allowed values."""
    options = build(catalogue.by_id("HEATING_SYSTEM"), {"type_of_system": "NUCLEAR"})
    with pytest.raises(ValidationError) as error:
        options.enum("type_of_system")
    assert error.value.reason is ReasonCode.UNKNOWN_OPTION_VALUE
    assert error.value.path == f"{ENTRY_PATH}.options.type_of_system"


def test_enum_with_a_non_string_value_is_a_type_mismatch(catalogue: Catalogue) -> None:
    """An enum takes a string; a number is a type error, not an unknown value."""
    options = build(catalogue.by_id("HEATING_SYSTEM"), {"type_of_system": 3})
    with pytest.raises(ValidationError) as error:
        options.enum("type_of_system")
    assert error.value.reason is ReasonCode.OPTION_TYPE_MISMATCH


def test_missing_option_without_a_default_is_rejected(catalogue: Catalogue) -> None:
    """An absent option with no default is a missing field, not a zero."""
    options = build(catalogue.by_id("EXTERNAL_INSULATION"), {})
    with pytest.raises(ValidationError) as error:
        options.integer("thickness_in_mm")
    assert error.value.reason is ReasonCode.MISSING_OPTION


def test_a_default_is_used_and_reported_with_its_source(catalogue: Catalogue) -> None:
    """Requirement M7: an applied default appears in the report with where its number comes from."""
    report = MappingReport()
    options = Options(catalogue.by_id("EXTERNAL_INSULATION"), {}, report, ENTRY_PATH)

    assert options.integer("thickness_in_mm", default=Default(140, "SEAI guidance")) == 140

    line = next(item for item in report.to_list() if item["path"].endswith("thickness_in_mm"))
    assert line["status"] == ReportStatus.DEFAULTED.value
    assert line["rule"] == "SEAI guidance"


def test_a_supplied_value_is_reported_as_used(catalogue: Catalogue) -> None:
    """A value that came from the request is reported as used, so the report covers both cases."""
    report = MappingReport()
    options = Options(catalogue.by_id("EXTERNAL_INSULATION"), {"thickness_in_mm": 120}, report, ENTRY_PATH)

    assert options.integer("thickness_in_mm", default=Default(140, "SEAI guidance")) == 120

    line = next(item for item in report.to_list() if item["path"].endswith("thickness_in_mm"))
    assert line["status"] == ReportStatus.USED.value


def test_integer_options_reject_booleans_and_floats(catalogue: Catalogue) -> None:
    """``true`` is an ``int`` in Python and must not pass as a thickness."""
    for value in (True, 120.5, "120"):
        options = build(catalogue.by_id("EXTERNAL_INSULATION"), {"thickness_in_mm": value})
        with pytest.raises(ValidationError) as error:
            options.integer("thickness_in_mm")
        assert error.value.reason is ReasonCode.OPTION_TYPE_MISMATCH


def test_integer_options_honour_the_catalogue_s_value_list(catalogue: Catalogue) -> None:
    """A glazing-pane count outside the listed set is an unknown value."""
    options = build(catalogue.by_id("WINDOW_REPLACEMENT"), {"glazing_panes": 4})
    with pytest.raises(ValidationError) as error:
        options.integer("glazing_panes")
    assert error.value.reason is ReasonCode.UNKNOWN_OPTION_VALUE


def test_integer_options_honour_a_measure_s_range(catalogue: Catalogue) -> None:
    """A range the measure imposes is checked in the reader, so the path is named the same way."""
    options = build(catalogue.by_id("CHANGE_ROOM_TEMPERATURE"), {"new_room_temperature": 40})
    with pytest.raises(ValidationError) as error:
        options.integer("new_room_temperature", minimum=15, maximum=26)
    assert error.value.reason is ReasonCode.UNKNOWN_OPTION_VALUE
    assert "26" in error.value.detail


def test_booleans_must_be_json_booleans(catalogue: Catalogue) -> None:
    """A string never passes as a boolean: 'no' is a true string in every JSON reader."""
    options = build(catalogue.by_id("SUSPENDED_GROUND_FLOOR_INSULATION"), {"air_barrier": "no"})
    with pytest.raises(ValidationError) as error:
        options.boolean("air_barrier")
    assert error.value.reason is ReasonCode.OPTION_TYPE_MISMATCH


def test_boolean_reads_a_supplied_value(catalogue: Catalogue) -> None:
    """A JSON boolean comes back as itself."""
    options = build(catalogue.by_id("SUSPENDED_GROUND_FLOOR_INSULATION"), {"air_barrier": True})
    assert options.boolean("air_barrier") is True
    assert options.is_supplied("air_barrier")
    assert not options.is_supplied("thickness_in_mm")


def test_reading_an_option_the_measure_lacks_is_an_unknown_option(catalogue: Catalogue) -> None:
    """A registry function asking for the wrong option gets a validation error, not a KeyError."""
    options = build(catalogue.by_id("OUTSIDE_SHADING"), {})
    with pytest.raises(ValidationError) as error:
        options.enum("material")
    assert error.value.reason is ReasonCode.UNKNOWN_OPTION


def test_note_writes_the_measure_level_report_line(catalogue: Catalogue) -> None:
    """A registry function says what it did through the reader, so the path is built once."""
    report = MappingReport()
    options = Options(catalogue.by_id("HEATING_SYSTEM"), {}, report, ENTRY_PATH)

    options.note(ReportStatus.APPROXIMATED, "biomass simulated as pellets", rule="Q14")

    line = next(item for item in report.to_list() if item["path"] == "package.measures[0]")
    assert line["measure_id"] == "HEATING_SYSTEM"
    assert line["rule"] == "Q14"
