""" Module for generating reports. """
# clean
import copy
import time
import os
from typing import Any, Optional, List, Union
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.platypus import Paragraph, Spacer, Image, PageBreak
from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate
from reportlab.platypus.frames import Frame
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm, mm
from reportlab.platypus import Table
from reportlab.platypus.tableofcontents import TableOfContents
from hisim import utils


class MyDocTemplate(BaseDocTemplate):

    """MyDocTemplate class."""

    def __init__(self, filename, **kw):
        """Initialize the doc template."""
        self.allow_splitting = 0
        super().__init__(filename, **kw)
        self.template = PageTemplate(
            "normal", [Frame(2.5 * cm, 2.5 * cm, 15 * cm, 25 * cm, id="F1")]
        )
        self.addPageTemplates(self.template)

    # Entries to the table of contents can be done either manually by
    # calling the addEntry method on the TableOfContents object or automatically
    # by sending a 'TOCEntry' notification in the afterFlowable method of
    # the DocTemplate you are using. The data to be passed to notify is a list
    # of three or four items countaining a level number, the entry text, the page
    # number and an optional destination key which the entry should point to.
    # This list will usually be created in a document template's method like
    # afterFlowable(), making notification calls using the notify() method
    # with appropriate data.

    def afterFlowable(self, flowable):
        """Registers TOC entries."""
        if flowable.__class__.__name__ == "Paragraph":
            text = flowable.getPlainText()
            style = flowable.style.name
            if style == "Heading1":
                self.notify("TOCEntry", (0, text, self.page))
            if style == "Heading2":
                self.notify("TOCEntry", (1, text, self.page))


class ReportGenerator:

    """Class for generating reports."""

    def __init__(self, dirpath: str) -> None:
        """Initialize the pdf report."""
        if dirpath is None:
            raise ValueError("Result path for the report was none.")
        self.story: Any
        self.toc = TableOfContents()

        self.filepath = os.path.join(dirpath, "report.pdf")
        self.open()
        self.write_preamble()
        self.write_table_of_content()
        self.close()

    def open(self):
        """Open a file."""
        self.doc = MyDocTemplate(
            self.filepath,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=18,
        )

        self.styles = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(name="Justify", alignment=TA_JUSTIFY))
        self.styles.add(
            ParagraphStyle(
                name="Normal_CENTER", parent=self.styles["Normal"], alignment=TA_CENTER
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="toc_centered",
                parent=self.styles["Normal"],
                fontSize=20,
                leading=16,
                alignment=TA_CENTER,
                spaceAfter=40,
            )
        )
        self.style_h1 = ParagraphStyle(
            name="Heading1", fontSize=12, leading=16, spaceBefore=20
        )
        self.style_h2 = ParagraphStyle(
            name="Heading2", fontSize=12, leading=14, spaceBefore=10
        )

    def write_table_of_content(self):
        """Write Table of Content."""

        # Create an instance of TableOfContents. Override the level styles (optional)
        # and add the object to the story

        self.toc = TableOfContents()

        self.toc.levelStyles = [self.style_h1, self.style_h2]

        self.story.append(
            Paragraph("<b>Table of contents</b>", self.styles["toc_centered"])
        )
        self.story.append(self.toc)
        self.story.append(PageBreak())

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
        story.append(Spacer(1, 50))

        # Insert Title
        titel = "HiSim Simulation Report"
        ptext = f'<font size="30">{titel}</font>'
        story.append(Paragraph(ptext, self.styles["Title"]))
        story.append(Spacer(1, 150))

        # Inserts authors
        authors = [
            "Developers:",
            "\n",
            "Dr. Noah Pflugradt",
            "Vitor Hugo Bellotto Zago",
            "Katharina Rieck",
        ]
        for part in authors:
            ptext = f'<font size="16">{part.strip()}</font>'
            story.append(Paragraph(ptext, self.styles["Normal"]))
            story.append(Spacer(1, 10))
        story.append(Spacer(1, 30))

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
            ptext = f'<font size="16">{part.strip()}</font>'
            story.append(Paragraph(ptext, self.styles["Normal"]))
            story.append(Spacer(1, 10))
        story.append(Spacer(1, 30))

        # Inserts time
        formatted_time = time.ctime()
        ptext = f'<font size="16">{formatted_time}</font>'
        story.append(Paragraph(ptext, self.styles["Normal"]))
        story.append(Spacer(1, 30))

        if hasattr(self, "executation_time"):
            # formatted_time
            ptext = f'<font size="16">{formatted_time}</font>'
            story.append(Paragraph(ptext, self.styles["Normal"]))
            story.append(Spacer(1, 30))
        self.story = story
        self.story.append(PageBreak())

    def get_story(self):
        """Get the story."""
        self.story = copy.deepcopy(self.story)

    def write_with_normal_alignment(
        self, text: Union[List[str], List[Optional[str]]]
    ) -> None:
        """Write a paragraph."""
        if len(text) != 0:
            for part in text:
                if part is not None:
                    if not isinstance(part, str):
                        raise ValueError("Got a non-string somehow: " + str(part))
                    ptext = f'<font size="12">{part.strip()}</font>'
                    self.story.append(Paragraph(ptext, self.styles["Normal"]))
                else:
                    raise ValueError("text contains Nones. Text was: " + str(text))
            self.story.append(Spacer(1, 10))
        self.story.append(Spacer(1, 20))

    def write_with_center_alignment(self, text: List[str]) -> None:
        """Write a paragraph."""
        if len(text) != 0:
            for part in text:
                ptext = f'<font size="12">{part.strip()}</font>'
                paragraph = Paragraph(ptext, self.styles["Normal_CENTER"])
                self.story.append(paragraph)
            self.story.append(Spacer(1, 10))

    def write_figures_to_report(self, file_path: str) -> None:
        """Add figure to the report."""

        if os.path.isfile(file_path):
            image = Image(file_path, useDPI=True)
            image.hAlign = "CENTER"
            self.story.append(image)
        else:
            raise ValueError("no files found")

    def write_figures_to_report_with_size_four_six(self, file_path: str) -> None:
        """Add figure to the report with certain size."""

        if os.path.isfile(file_path):
            image = Image(file_path, width=4 * inch, height=6 * inch)
            image.hAlign = "CENTER"
            self.story.append(image)
        else:
            raise ValueError("no files found")

    def write_figures_to_report_with_size_seven_four(self, file_path: str) -> None:
        """Add figure to the report with certain size."""

        if os.path.isfile(file_path):
            image = Image(file_path, width=7 * inch, height=4 * inch)
            image.hAlign = "CENTER"
            self.story.append(image)
        else:
            raise ValueError("no files found")

    def write_heading_with_style_heading_one(self, text: List[str]) -> None:
        """Write text as heading."""
        if len(text) != 0:
            for part in text:
                ptext = f"<b>{part.strip()}</b>"
                self.story.append(Paragraph(ptext, self.style_h1))
            self.story.append(Spacer(1, 10))
        self.story.append(Spacer(1, 30))

    def write_heading_with_style_heading_two(self, text: List[str]) -> None:
        """Write text as heading."""
        if len(text) != 0:
            for part in text:
                ptext = f"<b>{part.strip()}</b>"
                self.story.append(Paragraph(ptext, self.style_h2))
            self.story.append(Spacer(1, 10))

    def page_break(self):
        """Make a page break."""
        self.story.append(PageBreak())

    def add_spacer(self):
        """Add spacer."""
        self.story.append(Spacer(1, 30))

    def add_page_number(self, canvas, doc):
        """Add page number to report."""
        canvas.saveState()
        canvas.setFont(
            self.styles["Heading2"].fontName, self.styles["Heading2"].fontSize
        )
        page_number_text = f"{doc.page}"
        canvas.drawRightString(200 * mm, 20 * mm, page_number_text)
        canvas.restoreState()

    def close(self):
        """Close the report."""
        story = copy.deepcopy(self.story)
        self.doc.template.onPage = self.add_page_number
        self.doc.template.onPageEnd = self.add_page_number
        self.doc.multiBuild(story)
