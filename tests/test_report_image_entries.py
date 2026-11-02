"""Tests for ReportImageEntry init and SystemChartEntry dataclass."""

import pytest

from hisim.postprocessing.report_image_entries import (
    ReportImageEntry,
    SystemChartEntry,
)


@pytest.mark.base
def test_system_chart_entry_fields() -> None:
    """Ensure SystemChartEntry stores file_path and caption by field order."""
    entry = SystemChartEntry(file_path="/tmp/chart.png", caption="My Chart")
    assert entry.file_path == "/tmp/chart.png"
    assert entry.caption == "My Chart"


@pytest.mark.base
def test_report_image_entry_happy_path() -> None:
    """Ensure ReportImageEntry stores every argument on the corresponding attribute."""
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
    """Category and unit may be None without raising."""
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
            component_name=None,  # type: ignore[arg-type]
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


@pytest.mark.base
def test_report_image_entry_both_none_raises_component_name_first() -> None:
    """The component_name check fires first when both are None.

    component_name is validated before output_description.
    """
    with pytest.raises(ValueError, match="Component name was None"):
        ReportImageEntry(
            component_name=None,  # type: ignore[arg-type]
            output_type="power",
            file_path="/tmp/x.png",
            component_output_folder_path="/tmp/",
            category=None,
            output_description=None,
            unit=None,
        )


@pytest.mark.base
@pytest.mark.parametrize(
    ("file_name", "expected_caption"),
    [
        ("line.png", "Fig. 7: PVSystem ElectricityOutput, line chart over the run"),
        ("carpet.png", "Fig. 7: PVSystem ElectricityOutput, carpet plot by day and time of day"),
        ("days_m0_d0.png", "Fig. 7: PVSystem ElectricityOutput, single day"),
        ("bar.png", "Fig. 7: PVSystem ElectricityOutput, monthly bar chart"),
        ("other.png", "Fig. 7: PVSystem ElectricityOutput"),
    ],
)
def test_the_report_caption_names_the_kind_of_chart(file_name: str, expected_caption: str) -> None:
    """The four charts of one output had the same caption; each now says which chart it is."""
    from hisim.postprocessing.postprocessing_main import PostProcessor  # pylint: disable=import-outside-toplevel

    entry = ReportImageEntry(
        component_name="PVSystem",
        output_type="ElectricityOutput",
        file_path=f"/results/PVSystem/ElectricityOutput/{file_name}",
        component_output_folder_path="/results/PVSystem/ElectricityOutput",
        category=None,
        output_description="PV output",
        unit="W",
    )
    assert PostProcessor.figure_caption(7, entry) == expected_caption
