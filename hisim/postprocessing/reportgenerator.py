"""Generate the structured PDF simulation report from postprocessing results.

This module assembles the multi-page PDF report that HiSim writes next to a
finished simulation run. The
:class:`~hisim.postprocessing.reportgenerator.ReportGenerator` class builds a
linear ``story`` of ReportLab platypus flowables (paragraphs, spacers, images,
and tables) and renders it to ``report.pdf`` in the requested results
directory.

Report generation process
--------------------------

Constructing a :class:`~hisim.postprocessing.reportgenerator.ReportGenerator`
opens the document, writes a preamble (the HiSim and FZJ logos, the report
title, the author list, the institute address, and the current timestamp),
inserts a table of contents, and immediately calls
:meth:`~hisim.postprocessing.reportgenerator.ReportGenerator.close`. That call
runs ``BaseDocTemplate.multiBuild`` to finalise the PDF. Additional content
(headings, figures, KPI tables, and page breaks) is appended afterwards
through the ``write_*`` methods. Because ``multiBuild`` already ran during
construction, :meth:`~hisim.postprocessing.reportgenerator.ReportGenerator.close`
must be called again as a mandatory flush step after every batch of new content
so that it appears in the generated file.

Template usage
--------------

:class:`~hisim.postprocessing.reportgenerator.MyDocTemplate` is a
``BaseDocTemplate`` subclass that configures a single page template with one
frame and disables automatic flowable splitting (``allow_splitting = 0``). Its
``afterFlowable`` hook inspects every rendered ``Paragraph`` and, for the
``Heading1`` and ``Heading2`` styles, notifies the ``TableOfContents`` so that
the table of contents is populated automatically. Page numbers are drawn by
:meth:`~hisim.postprocessing.reportgenerator.ReportGenerator.add_page_number`
through the template's ``onPage`` and ``onPageEnd`` callbacks.

Output format
-------------

The sole output format is a multi-page PDF produced with ReportLab. Figure
images are embedded at a few fixed sizes, tabular KPI data is rendered with
``Table`` flowables, and the document is paginated with an automatically
generated table of contents and footer page numbers.
"""

# clean
from pathlib import Path
import copy
import time
from typing import Any
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.platypus import Paragraph, Spacer, Image, PageBreak, Table, Flowable
from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate
from reportlab.platypus.frames import Frame
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle, StyleSheet1
from reportlab.lib.units import inch, cm, mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus.tableofcontents import TableOfContents
from hisim import utils


class MyDocTemplate(BaseDocTemplate):
    """MyDocTemplate class."""

    def __init__(self, filename: str | Path, **kw: Any) -> None:
        """Initialize the doc template."""
        self.allow_splitting = 0
        super().__init__(filename, **kw)
        self.template: PageTemplate = PageTemplate("normal", [Frame(2.5 * cm, 2.5 * cm, 15 * cm, 25 * cm, id="F1")])
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

    def afterFlowable(self, flowable: Flowable) -> None:
        """Registers TOC entries."""
        if isinstance(flowable, Paragraph):
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
        self.story: list[Flowable] = []
        self.toc: TableOfContents = TableOfContents()

        self.filepath: str = str(Path(dirpath) / "report.pdf")
        self.open()
        self.write_preamble()
        self.write_table_of_contents()
        self.close()

    def open(self) -> None:
        """Open a file."""
        self.doc: MyDocTemplate = MyDocTemplate(
            self.filepath,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=18,
        )

        self.styles: StyleSheet1 = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(name="Justify", alignment=TA_JUSTIFY))
        self.styles.add(ParagraphStyle(name="Normal_CENTER", parent=self.styles["Normal"], alignment=TA_CENTER))
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
        self.style_h1: ParagraphStyle = ParagraphStyle(name="Heading1", fontSize=12, leading=16, spaceBefore=20)
        self.style_h2: ParagraphStyle = ParagraphStyle(name="Heading2", fontSize=12, leading=14, spaceBefore=10)

    def write_table_of_contents(self) -> None:
        """Write the table of contents."""

        # Create an instance of TableOfContents. Override the level styles (optional)
        # and add the object to the story

        self.toc = TableOfContents()

        self.toc.levelStyles = [self.style_h1, self.style_h2]

        self.story.append(Paragraph("<b>Table of contents</b>", self.styles["toc_centered"]))
        self.story.append(self.toc)
        self.story.append(PageBreak())

    @utils.deprecated("Use write_table_of_contents instead.")
    def write_table_of_content(self) -> None:
        """Deprecated alias for :meth:`write_table_of_contents`.

        .. deprecated::
            Renamed to ``write_table_of_contents`` to use the grammatically
            correct plural form ("table of contents"). Will be removed in a
            future version.
        """
        self.write_table_of_contents()

    def write_preamble(self) -> None:
        """Write the preamble."""
        # Configuration taken mostly from following tutorial
        # https://www.blog.pythonlibrary.org/2010/03/08/a-simple-step-by-step-reportlab-tutorial/
        story = []

        # Inserts HiSim logo
        logo = Path(utils.hisim_postprocessing_img) / "hisim_logo.png"
        hisim_logo_image = Image(str(logo), 2 * inch, inch)
        hisim_logo_image.hAlign = "LEFT"

        # Inserts FZJ logo
        logo = Path(utils.hisim_postprocessing_img) / "fzj_logo.jpg"
        fzj_logo_image = Image(str(logo), 2 * inch, inch)
        fzj_logo_image.hAlign = "RIGHT"

        data = [[hisim_logo_image, fzj_logo_image]]
        report_table = Table(data)
        story.append(report_table)
        story.append(Spacer(1, 50))

        # Insert Title
        titel = "HiSim Simulation Report"
        paragraph_text = f'<font size="30">{titel}</font>'
        story.append(Paragraph(paragraph_text, self.styles["Title"]))
        story.append(Spacer(1, 150))

        # Inserts authors
        authors = [
            "Developers:",
            "\n",
            "Dr. Noah Pflugradt",
            "Dr. Sebastian Dickler",
            "Kevin Knosala",
            "Katharina Rieck",
            "Johanna Ganglbauer",
            "David Neuroth",
            "Tjarko Tjaden",
            "Vitor Hugo Bellotto Zago",
            "Maximilian Hillen",
            "Frank Burkrad",
            "Marwa Alfouly",
            "Franz Oldopp",
            "Markus Blasberg",
        ]

        for author in authors:
            paragraph_text = f'<font size="16">{author.strip()}</font>'
            story.append(Paragraph(paragraph_text, self.styles["Normal"]))
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
        for address_line in address_parts:
            paragraph_text = f'<font size="16">{address_line.strip()}</font>'
            story.append(Paragraph(paragraph_text, self.styles["Normal"]))
            story.append(Spacer(1, 10))
        story.append(Spacer(1, 30))

        # Inserts time
        formatted_time = time.ctime()
        paragraph_text = f'<font size="16">{formatted_time}</font>'
        story.append(Paragraph(paragraph_text, self.styles["Normal"]))
        story.append(Spacer(1, 30))

        if hasattr(self, "executation_time"):
            # formatted_time
            paragraph_text = f'<font size="16">{formatted_time}</font>'
            story.append(Paragraph(paragraph_text, self.styles["Normal"]))
            story.append(Spacer(1, 30))
        self.story = story
        self.story.append(PageBreak())

    def copy_story(self) -> None:
        """Replace the story with a deep copy of itself."""
        self.story = copy.deepcopy(self.story)

    @utils.deprecated("Use copy_story instead.")
    def get_story(self) -> None:
        """Deprecated alias for :meth:`copy_story`.

        .. deprecated::
            Renamed to ``copy_story`` because the method replaces
            ``self.story`` with a deep copy of itself instead of returning the
            story. Will be removed in a future version.
        """
        self.copy_story()

    def write_with_normal_alignment(self, text: list[str | None]) -> None:
        """Write a paragraph."""
        if len(text) != 0:
            for part in text:
                if part is not None:
                    if not isinstance(part, str):
                        raise ValueError("Got a non-string somehow: " + str(part))
                    paragraph_text = f'<font size="12">{part.strip()}</font>'
                    self.story.append(Paragraph(paragraph_text, self.styles["Normal"]))
                else:
                    raise ValueError("text contains Nones. Text was: " + str(text))
            self.story.append(Spacer(1, 10))
        self.story.append(Spacer(1, 20))

    def write_with_center_alignment(self, text: list[str]) -> None:
        """Write a paragraph."""
        if len(text) != 0:
            for part in text:
                paragraph_text = f'<font size="12">{part.strip()}</font>'
                paragraph = Paragraph(paragraph_text, self.styles["Normal_CENTER"])
                self.story.append(paragraph)
            self.story.append(Spacer(1, 10))

    def write_figures_to_report(self, file_path: str) -> None:
        """Add figure to the report."""

        path = Path(file_path)
        if path.is_file():
            image = Image(str(path), useDPI=True)
            image.hAlign = "CENTER"
            self.story.append(image)
        else:
            raise ValueError("no files found")

    def write_figures_to_report_with_size_four_six(self, file_path: str) -> None:
        """Add figure to the report with certain size."""

        path = Path(file_path)
        if path.is_file():
            image = Image(str(path), width=4 * inch, height=6 * inch)
            image.hAlign = "CENTER"
            self.story.append(image)
        else:
            raise ValueError("no files found")

    def write_figures_to_report_with_size_seven_four(self, file_path: str) -> None:
        """Add figure to the report with certain size."""

        path = Path(file_path)
        if path.is_file():
            image = Image(str(path), width=7 * inch, height=4 * inch)
            image.hAlign = "CENTER"
            self.story.append(image)
        else:
            raise ValueError("no files found")

    def write_tables_to_report(self, table_as_list_of_lists: list[list[Any]]) -> None:
        """Add table to the report."""

        table = Table(
            table_as_list_of_lists,
            style=[
                ("LINEABOVE", (0, 0), (-1, 0), 1, "black"),
                ("LINEABOVE", (0, 1), (-1, 1), 0.5, "black"),
                ("LINEBELOW", (0, -1), (-1, -1), 1, "black"),
            ],
        )
        self.story.append(table)

    def write_heading_with_style_heading_one(self, text: list[str]) -> None:
        """Write text as heading."""
        if len(text) != 0:
            for part in text:
                paragraph_text = f"<b>{part.strip()}</b>"
                self.story.append(Paragraph(paragraph_text, self.style_h1))
            self.story.append(Spacer(1, 10))
        self.story.append(Spacer(1, 30))

    def write_heading_with_style_heading_two(self, text: list[str]) -> None:
        """Write text as heading."""
        if len(text) != 0:
            for part in text:
                paragraph_text = f"<b>{part.strip()}</b>"
                self.story.append(Paragraph(paragraph_text, self.style_h2))
            self.story.append(Spacer(1, 10))

    def page_break(self) -> None:
        """Make a page break."""
        self.story.append(PageBreak())

    def add_spacer(self) -> None:
        """Add spacer."""
        self.story.append(Spacer(1, 30))

    def add_page_number(self, canvas: Canvas, doc: BaseDocTemplate) -> None:
        """Add page number to report."""
        canvas.saveState()
        canvas.setFont(self.styles["Heading2"].fontName, self.styles["Heading2"].fontSize)
        page_number_text = f"{doc.page}"
        canvas.drawRightString(200 * mm, 20 * mm, page_number_text)
        canvas.restoreState()

    def close(self) -> None:
        """Close the report."""
        story = copy.deepcopy(self.story)
        self.doc.template.onPage = self.add_page_number
        self.doc.template.onPageEnd = self.add_page_number
        self.doc.multiBuild(story)
