"""The PDF report releases each figure's decoded pixels once the figure is drawn.

The report embeds every plot as a ReportLab ``Image`` flowable. ReportLab keeps a flowable's
decoded image (an ``ImageReader`` holding the full bitmap) for as long as the flowable lives,
unless the flowable is created with ``lazy=2``, and a report build keeps every flowable of the
story alive until the build ends. A report of a few hundred 3600 x 2400 pixel plots therefore
held all their bitmaps at once, about 75 MB each, and a one-day run with every post-processing
option grew past 12 GB. These tests pin that each figure-writing method creates a flowable that
drops its bitmap after it is drawn, and that a report with figures still builds.
"""

from pathlib import Path
from typing import Any, List

import pytest
from PIL import Image as PilImage
from reportlab.pdfgen.canvas import Canvas

from reportlab.lib.pagesizes import letter
from reportlab.platypus import Paragraph

from hisim.postprocessing.reportgenerator import MyDocTemplate, ReportGenerator


@pytest.fixture(name="figure_path")
def fixture_figure_path(tmp_path: Path) -> str:
    """A small RGBA PNG standing in for a plot, written into the test's own directory."""
    path = tmp_path / "figure.png"
    PilImage.new("RGBA", (300, 200), (200, 30, 30, 255)).save(path, dpi=(300, 300))
    return str(path)


@pytest.mark.base
@pytest.mark.parametrize(
    "method_name",
    [
        "write_figures_to_report",
        "write_figures_to_report_with_size_four_six",
        "write_figures_to_report_with_size_seven_four",
    ],
)
def test_a_drawn_figure_holds_no_decoded_image(tmp_path: Path, figure_path: str, method_name: str) -> None:
    """After drawing, the flowable has no ImageReader left, so its bitmap can be freed."""
    report = ReportGenerator(dirpath=str(tmp_path))
    getattr(report, method_name)(figure_path)
    flowable = report.story[-1]
    canvas = Canvas(str(tmp_path / "drawn.pdf"))
    flowable.wrap(500, 700)
    flowable.drawOn(canvas, 0, 0)
    assert flowable.__dict__.get("_img") is None


@pytest.mark.base
def test_a_report_with_figures_builds(tmp_path: Path, figure_path: str) -> None:
    """Two builds of a story with the same figure twice both write the PDF, with the figure embedded."""
    report = ReportGenerator(dirpath=str(tmp_path))
    report.open()
    report.write_figures_to_report(figure_path)
    report.write_figures_to_report_with_size_seven_four(figure_path)
    report.close()
    report.open()
    report.write_figures_to_report_with_size_four_six(figure_path)
    report.close()
    content = (tmp_path / "report.pdf").read_bytes()
    assert content.count(b"/Subtype /Image") >= 1


@pytest.mark.base
def test_the_title_page_holds_the_developers_the_address_and_the_date(tmp_path: Path) -> None:
    """The preamble fits on page 1 and the table of contents is page 2.

    At 16 pt with 10 pt spacers the address and the date ran onto page 2, ahead of the contents.
    """
    report = ReportGenerator(dirpath=str(tmp_path))
    assert report.doc.page == 2


@pytest.mark.base
def test_the_page_frame_keeps_a_margin_on_every_side(tmp_path: Path) -> None:
    """Text starts 2.5 cm below the top edge of the page, not 4 mm, and the side margins are equal."""
    report = ReportGenerator(dirpath=str(tmp_path))
    frame = report.doc.template.frames[0]
    page_width, page_height = letter
    assert frame.x1 == pytest.approx(MyDocTemplate.MARGIN)
    assert page_width - (frame.x1 + frame.width) == pytest.approx(MyDocTemplate.MARGIN)
    assert page_height - (frame.y1 + frame.height) == pytest.approx(MyDocTemplate.MARGIN)


@pytest.mark.base
def test_a_wide_table_is_wrapped_within_the_page_width(tmp_path: Path) -> None:
    """Long headers wrap inside their columns, and the columns add up to the page's figure width.

    The cost tables were set as plain strings, wider than the page and centred, so their first
    column (the component names) was cut off at the left edge.
    """
    report = ReportGenerator(dirpath=str(tmp_path))
    rows: List[List[Any]] = [
        ["Component", "Total energy consumption [kWh]", "CO2-emissions of energy consumption [kg]", "Costs [EUR]"],
        ["UTSPConnector", 4658.48, 1909.98, None],
    ]
    report.write_tables_to_report(rows)
    table = report.story[-1]
    cells = table._cellvalues  # pylint: disable=protected-access
    assert all(isinstance(cell, Paragraph) for row in cells for cell in row)
    assert cells[1][3].getPlainText() == ""
    width, _height = table.wrap(ReportGenerator.LARGEST_FIGURE_WIDTH, 1000)
    assert width == pytest.approx(ReportGenerator.LARGEST_FIGURE_WIDTH)


@pytest.mark.base
def test_table_columns_share_the_width_by_what_they_hold_on_one_line() -> None:
    """A column weighs its longest body cell or its header's longest word (x 1.2), capped at 45, plus 2.

    The header wraps between words, so a long header does not widen its column; a body cell such as
    ``17693.32`` must not break, which it did when the header lengths decided the widths.
    """
    rows = [
        ["Component", "Investment for simulated period [EUR]", "Note"],
        ["MoreAdvancedHeatPumpHPLib", "17693.32", "x" * 60],
    ]
    widths = ReportGenerator.table_column_widths(rows, 88.0)
    assert widths == pytest.approx([27.0, 14.0, 47.0])


@pytest.mark.base
def test_a_fitted_figure_keeps_its_aspect_ratio(tmp_path: Path, figure_path: str) -> None:
    """A 300 x 200 pixel figure in a 150 x 300 point box is drawn at 150 x 100 points, not stretched."""
    report = ReportGenerator(dirpath=str(tmp_path))
    report.write_figure_fitted_to_page(figure_path, max_width=150, max_height=300)
    image = report.story[-1]
    assert (image.drawWidth, image.drawHeight) == pytest.approx((150, 100))
