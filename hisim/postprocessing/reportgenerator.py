""" Module for generating reports. """
# clean
import copy
import time
import os
from typing import Any, Optional
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Table
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from hisim import utils


class ReportGenerator:

    """Class for generating reports."""

    def __init__(self, dirpath: str) -> None:
        """Initialize the pdf report."""
        if dirpath is None:
            raise ValueError("Result path for the report was none.")
        self.story: Any
        self.toc = TableOfContents()
        self.toc.levelStyles = [
        ParagraphStyle(fontName='Times-Bold', fontSize=20, name='TOCHeading1', leftIndent=20, firstLineIndent=-20, spaceBefore=10, leading=16),
        ParagraphStyle(fontSize=18, name='TOCHeading2', leftIndent=40, firstLineIndent=-20, spaceBefore=5, leading=12),
        ]
        self.filepath = os.path.join(dirpath, "report.pdf")
        self.open()
        self.write_preamble()
        self.write_table_of_content()
        self.close()


    def addPageNumber(canvas: canvas, doc):
        """
        Add the page number
        """
        page_num = canvas.getPageNumber()
        text = "Page #%s" % page_num
        canvas.drawRightString(200*mm, 20*mm, text)
        return page_num

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
        self.styles.add(ParagraphStyle(name="Normal_CENTER", parent=self.styles["Normal"], alignment=TA_CENTER))

    def afterFlowable(self, flowable):
         "Registers TOC entries."
         if flowable.__class__.__name__ == 'Paragraph':
             text = flowable.getPlainText()
             style = flowable.style.name
             if style == 'Heading1':
                 self.notify('TOCEntry', (0, text, self.page))
             if style == 'Heading2':
                 self.notify('TOCEntry', (1, text, self.page))

    def write_table_of_content(self):
        """Write Table of Content."""
        self.story.append(self.toc)

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
        self.story.append(PageBreak())

    def write_with_normal_alignment(self, text):
        """Write a paragraph."""
        if len(text) != 0:
            for part in text:
                ptext = f'<font size="12">{part}</font>'
                self.story.append(Paragraph(ptext, self.styles["Normal"]))
            self.story.append(Spacer(1, 12))

    def write_with_center_alignment(self, text):
        """Write a paragraph."""
        if len(text) != 0:
            for part in text:
                ptext = f'<font size="12">{part}</font>'
                paragraph = Paragraph(ptext, self.styles["Normal_CENTER"])
                self.story.append(paragraph)
            self.story.append(Spacer(1, 12))

    def get_story(self):
        """Get the story."""
        self.story = copy.deepcopy(self.story)


    def write_figures_to_report(self, file_path: str) -> None:
        """Add figure to the report."""

        if os.path.isfile(file_path):
            image = Image(file_path, 5 * inch, 3 * inch)
            image.hAlign = "CENTER"
            self.story.append(image)
            self.story.append(Spacer(1, 24))
        else:
            raise ValueError("no files found")
        self.afterFlowable(Image)

    def write_all_figures_of_one_output_type_to_report(
        self,
        component_output_folder_path: str,
        component_name: str,
        output_type: str,
        output_description: Optional[str],
    ) -> None:
        """Add all figures of one component and one output type to the report."""

        bar_string = "=============================================================="
        self.story.append(Paragraph(bar_string, self.styles["Normal"]))

        text = f'<font size="12">{component_name}</font>'
        self.story.append(Paragraph(text, self.styles["Heading1"]))
        text1 = f'<font size="12">{output_type}</font>'
        self.story.append(Paragraph(text1, self.styles["Normal"]))
        text2 = f'<font size="12">{output_description}</font>'
        self.story.append(Paragraph(text2, self.styles["Normal"]))
        self.story.append(Spacer(1, 12))

        for file in os.listdir(component_output_folder_path):
            file_path = os.path.join(component_output_folder_path, file)
            if os.path.isfile(file_path):
                image = Image(file_path, 5 * inch, 3 * inch)
                image.hAlign = "CENTER"
                self.story.append(image)
                self.story.append(Spacer(1, 24))
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
            self.toc.addEntry(text=ptext, level=1, pageNum=self.addPageNumber(canvas, self.doc))

    def page_break(self):
        """Make a page break."""
        self.story.append(PageBreak())

    def add_spacer(self):
        """Add spacer."""
        self.story.append(Spacer(1,24))

    def close(self):
        """Close the report."""
        self.story.append(PageBreak())
        story = copy.deepcopy(self.story)
        self.doc.multiBuild(story)
