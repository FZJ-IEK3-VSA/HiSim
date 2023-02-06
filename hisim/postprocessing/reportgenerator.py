""" Module for generating reports. """
# clean
import copy
import time
import os
from typing import Any
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Table

from hisim import utils


class ReportGenerator:

    """Class for generating reports."""

    def __init__(self, dirpath: str) -> None:
        """Initialize the pdf report."""
        if dirpath is None:
            raise ValueError("Result path for the report was none.")
        self.story: Any
        self.filepath = os.path.join(dirpath, "report.pdf")
        self.open()
        self.write_preamble()
        self.close()

    def open(self):
        """Open a file."""
        self.doc = SimpleDocTemplate(
            self.filepath,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=18,
        )
        self.styles = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(name="Justify", alignment=TA_JUSTIFY))

    def write_preamble(self):
        """Write the preamble."""
        # Configuration taken mostly from following tutorial
        # https://www.blog.pythonlibrary.org/2010/03/08/a-simple-step-by-step-reportlab-tutorial/
        story = []

        # Inserts HiSim logo
        logo = os.path.join(utils.hisim_postprocessing_img, "hisim_logo.png")
        im1 = Image(logo, 2 * inch, inch)
        im1.hAlign = "LEFT"

        # Inserts FZJ logo
        logo = os.path.join(utils.hisim_postprocessing_img, "fzj_logo.jpg")
        im2 = Image(logo, 2 * inch, inch)
        im2.hAlign = "RIGHT"

        data = [[im1, im2]]
        report_table = Table(data)
        story.append(report_table)
        story.append(Spacer(1, 24))

        # Insert Title
        titel = "HiSim Simulation Report"
        ptext = f'<font size="18">{titel}</font>'
        story.append(Paragraph(ptext, self.styles["Title"]))
        story.append(Spacer(1, 200))

        # Inserts authors
        authors = ["Developers: Dr. Noah Pflugradt", "Vitor Hugo Bellotto Zago"]
        for part in authors:
            ptext = f'<font size="12">{part.strip()}</font>'
            story.append(Paragraph(ptext, self.styles["Normal"]))
        story.append(Spacer(1, 12))

        # Inserts address
        address_parts = [
            "Forschungszentrum Jülich",
            "Institute of Energy and Climate Research",
            "Techno - Economic Systems Analysis (IEK - 3)",
            "Wilhelm - Johnen - Straße",
            "52428 Jülich",
            "Germany",
        ]
        for part in address_parts:
            ptext = f'<font size="12">{part.strip()}</font>'
            story.append(Paragraph(ptext, self.styles["Normal"]))
        story.append(Spacer(1, 12))

        # Inserts time
        formatted_time = time.ctime()
        ptext = f'<font size="12">{formatted_time}</font>'
        story.append(Paragraph(ptext, self.styles["Normal"]))
        story.append(Spacer(1, 12))

        if hasattr(self, "executation_time"):
            # formatted_time
            ptext = f'<font size="12">{formatted_time}</font>'
            story.append(Paragraph(ptext, self.styles["Normal"]))
            story.append(Spacer(1, 12))
        self.story = story

    def write(self, text):
        """Write a paragraph."""
        if len(text) != 0:
            for part in text:
                ptext = f'<font size="12">{part}</font>'
                self.story.append(Paragraph(ptext, self.styles["Normal"]))
            self.story.append(Spacer(1, 12))

    def get_story(self):
        """Get the story."""
        self.story = copy.deepcopy(self.story)

    def close(self):
        """Close the report."""
        self.story.append(PageBreak())
        story = copy.deepcopy(self.story)
        self.doc.build(story)

    def write_figures_to_report(self, file_path: str) -> None:
        """Add figure to the report."""

        if os.path.isfile(file_path):
            image = Image(file_path, 4 * inch, 3 * inch)
            image.hAlign = "CENTER"
            self.story.append(image)
            self.story.append(Spacer(0, 20))
        else:
            raise ValueError("no files found")

    def write_all_figures_of_one_output_type_to_report(
        self, component_output_folder_path: str, component_name: str, output_type: str
    ) -> None:
        """Add all figures of one component and one output type to the report."""

        bar_string = "=============================================================="
        self.story.append(Paragraph(bar_string, self.styles["Normal"]))

        text = f'<font size="12">{component_name}</font>'
        self.story.append(Paragraph(text, self.styles["Heading1"]))
        text1 = f'<font size="12">{output_type}</font>'
        self.story.append(Paragraph(text1, self.styles["Normal"]))
        self.story.append(Spacer(1, 12))

        for file in os.listdir(component_output_folder_path):
            file_path = os.path.join(component_output_folder_path, file)
            if os.path.isfile(file_path):
                image = Image(file_path, 4 * inch, 3 * inch)
                image.hAlign = "CENTER"
                self.story.append(image)
                self.story.append(Spacer(0, 20))
            else:
                raise ValueError("no files found")

        self.story.append(PageBreak())

    def write_heading(self, text):
        """Write text as heading."""
        if len(text) != 0:
            bar_string = (
                "=============================================================="
            )
            self.story.append(Paragraph(bar_string, self.styles["Normal"]))
            for part in text:
                ptext = f'<font size="12">{part}</font>'
                self.story.append(Paragraph(ptext, self.styles["Heading1"]))
            self.story.append(Spacer(1, 12))

    def page_break(self):
        """Make a page break."""
        self.story.append(PageBreak())
