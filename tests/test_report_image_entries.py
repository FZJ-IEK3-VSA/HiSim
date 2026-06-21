"""Tests for ReportImageEntry init and SystemChartEntry dataclass."""

# clean

import pytest

from hisim.postprocessing.report_image_entries import (
    ReportImageEntry,
    SystemChartEntry,
)


@pytest.mark.base
def test_system_chart_entry_fields() -> None:
    """SystemChartEntry stores path and caption by field order."""
    entry = SystemChartEntry(path="/tmp/chart.png", caption="My Chart")
    assert entry.path == "/tmp/chart.png"
    assert entry.caption == "My Chart"


@pytest.mark.base
def test_report_image_entry_happy_path() -> None:
    """ReportImageEntry stores every argument on the corresponding attribute."""
    entry = ReportImageEntry(
        component_name="HeatPump",
        output_type="power",
        file_path="/tmp/x.png",
        component_output_folder_path="/tmp/",
        category="electricity",
        output_description="Power output",
        unit="W",
    )
    assert entry.component_name == "HeatPump"
    assert entry.output_type == "power"
    assert entry.file_path == "/tmp/x.png"
    assert entry.component_output_folder_path == "/tmp/"
    assert entry.category == "electricity"
    assert entry.output_description == "Power output"
    assert entry.unit == "W"


@pytest.mark.base
def test_report_image_entry_optional_fields_none() -> None:
    """category and unit may be None without raising."""
    entry = ReportImageEntry(
        component_name="HeatPump",
        output_type="power",
        file_path="/tmp/x.png",
        component_output_folder_path="/tmp/",
        category=None,
        output_description="desc",
        unit=None,
    )
    assert entry.category is None
    assert entry.unit is None


@pytest.mark.base
def test_report_image_entry_component_name_none_raises() -> None:
    """A None component_name raises ValueError mentioning the cause."""
    with pytest.raises(ValueError, match="Component name was None"):
        ReportImageEntry(
            component_name=None,
            output_type="power",
            file_path="/tmp/x.png",
            component_output_folder_path="/tmp/",
            category="electricity",
            output_description="Power output",
            unit="W",
        )


@pytest.mark.base
def test_report_image_entry_output_description_none_raises() -> None:
    """A None output_description raises ValueError mentioning the component."""
    with pytest.raises(
        ValueError, match="Component description was none from component: HeatPump"
    ):
        ReportImageEntry(
            component_name="HeatPump",
            output_type="power",
            file_path="/tmp/x.png",
            component_output_folder_path="/tmp/",
            category="electricity",
            output_description=None,
            unit="W",
        )
