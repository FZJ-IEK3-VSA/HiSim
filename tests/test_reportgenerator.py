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

import pytest
from PIL import Image as PilImage
from reportlab.pdfgen.canvas import Canvas

from hisim.postprocessing.reportgenerator import ReportGenerator


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
