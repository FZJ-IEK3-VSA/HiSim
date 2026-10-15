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

from pathlib import Path
import copy
import time
from html import escape
from typing import Any
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.platypus import Paragraph, Spacer, Image, PageBreak, Table, Flowable
from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate
from reportlab.platypus.frames import Frame
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle, StyleSheet1
from reportlab.lib.units import inch, cm, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus.tableofcontents import TableOfContents
from hisim import utils


class MyDocTemplate(BaseDocTemplate):
    """MyDocTemplate class."""

    #: The margin on every side of the page, in points.
    MARGIN: float = 2.5 * cm

    def __init__(self, filename: str | Path, pagesize: tuple[float, float] = letter, **kw: Any) -> None:
        """Initialize the doc template with one frame, :attr:`MARGIN` inside every edge of the page.

        Args:
            filename: Where the PDF is written.
            pagesize: Width and height of the page, in points; US letter by default.
            **kw: Passed on to ``BaseDocTemplate``.
        """
        self.allow_splitting = 0
        super().__init__(filename, pagesize=pagesize, **kw)
        # 2.5 cm margins on every side of the page: the frame used to be 15 x 25 cm from the lower left
        # corner, which left 4 mm above the first line of each page and 4.1 cm on the right.
        page_width, page_height = pagesize
        self.template: PageTemplate = PageTemplate(
            "normal",
            [Frame(self.MARGIN, self.MARGIN, page_width - 2 * self.MARGIN, page_height - 2 * self.MARGIN, id="F1")],
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

    #: The largest figure a page holds beside a heading and a caption: the frame's width less its
    #: padding, and most of its height, in points.
    LARGEST_FIGURE_WIDTH: float = 16 * cm
    LARGEST_FIGURE_HEIGHT: float = 18 * cm

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
        story.append(Spacer(1, 30))

        # Insert Title
        titel = "HiSim Simulation Report"
        paragraph_text = f'<font size="30">{titel}</font>'
        story.append(Paragraph(paragraph_text, self.styles["Title"]))
        story.append(Spacer(1, 40))

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

        # 12 pt lines without spacers between them: at 16 pt with 10 pt spacers the developers and the
        # address did not fit on the title page and the address and date ran onto page 2.
        title_page_text = ParagraphStyle(name="TitlePageText", parent=self.styles["Normal"], fontSize=12, leading=16)
        for author in authors:
            story.append(Paragraph(author.strip(), title_page_text))
        story.append(Spacer(1, 20))

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
            story.append(Paragraph(address_line.strip(), title_page_text))
        story.append(Spacer(1, 20))

        # Inserts time
        formatted_time = time.ctime()
        story.append(Paragraph(formatted_time, title_page_text))
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
        """Add a plot to the report at its own size, computed from the pixel size and the image's DPI.

        Args:
            file_path: Path of the plot's image file.

        Raises:
            ValueError: If no file exists at ``file_path``.
        """
        self._append_figure(file_path, width_in_points=None, height_in_points=None, use_dpi=True)

    def write_figures_to_report_with_size_four_six(self, file_path: str) -> None:
        """Add a plot to the report, drawn 4 inches wide and 6 inches high.

        Args:
            file_path: Path of the plot's image file.

        Raises:
            ValueError: If no file exists at ``file_path``.
        """
        self._append_figure(file_path, width_in_points=4 * inch, height_in_points=6 * inch, use_dpi=False)

    def write_figures_to_report_with_size_seven_four(self, file_path: str) -> None:
        """Add a plot to the report, drawn 7 inches wide and 4 inches high.

        Args:
            file_path: Path of the plot's image file.

        Raises:
            ValueError: If no file exists at ``file_path``.
        """
        self._append_figure(file_path, width_in_points=7 * inch, height_in_points=4 * inch, use_dpi=False)

    def _append_figure(
        self, file_path: str, width_in_points: float | None, height_in_points: float | None, use_dpi: bool
    ) -> None:
        """Append a plot to the story as a centred ReportLab ``Image`` that drops its pixels after drawing.

        Every figure method of this class goes through here, so the memory rule below holds for
        each plot. A build keeps every flowable of the story alive until it ends. ReportLab's default
        image flowable (``lazy=1``) keeps its decoded bitmap until then, so a report of a few hundred
        3600 x 2400 pixel PNG plots held all of them at once, about 75 MB each. With ``lazy=2``
        ReportLab opens the file when it needs it (at layout for the size, again to draw) and drops
        the decoded bitmap after drawing. The price is time: each build pass decodes each plot
        again (``multiBuild`` runs at least two passes, for the table of contents). ReportLab copies
        a JPEG plot's compressed data into the PDF without decoding it and ignores ``lazy`` for JPEG
        files, so the rule matters for PNG plots only. The two logos of the preamble keep the
        default; they are small.

        Args:
            file_path: Path of the plot's image file.
            width_in_points: Drawn width (1 point = 1/72 inch), or None for the width from the image.
            height_in_points: Drawn height (1 point = 1/72 inch), or None for the height from the image.
            use_dpi: Whether the size taken from the image is scaled by its DPI.

        Raises:
            ValueError: If no file exists at ``file_path``.
        """
        path = Path(file_path)
        if not path.is_file():
            raise ValueError("no files found")
        image = Image(str(path), width=width_in_points, height=height_in_points, useDPI=use_dpi, lazy=2)
        image.hAlign = "CENTER"
        self.story.append(image)

    def write_figure_fitted_to_page(
        self, file_path: str, max_width: float = LARGEST_FIGURE_WIDTH, max_height: float = LARGEST_FIGURE_HEIGHT
    ) -> None:
        """Add a figure scaled to fit a box, keeping its aspect ratio; read lazily, as in `write_figures_to_report`.

        A figure whose shape is not known in advance, such as a Graphviz network chart, is shrunk or
        enlarged until it touches the box on one side. Example: a 2000 x 1000 pixel chart in a box
        of 15 x 20 cm is drawn at 15 x 7.5 cm. A fixed size such as 4 x 6 inches would stretch it.

        Args:
            file_path: The image file.
            max_width: The width of the box, in points; the page's largest figure by default.
            max_height: The height of the box, in points; the page's largest figure by default.

        Raises:
            ValueError: When the file does not exist.
        """
        path = Path(file_path)
        if not path.is_file():
            raise ValueError("no files found")
        width_in_pixels, height_in_pixels = ImageReader(str(path)).getSize()
        scale = min(max_width / width_in_pixels, max_height / height_in_pixels)
        image = Image(str(path), width=width_in_pixels * scale, height=height_in_pixels * scale, lazy=2)
        image.hAlign = "CENTER"
        self.story.append(image)

    #: Font size of table cells, in points; the header row is set in bold at the same size.
    TABLE_FONT_SIZE: float = 8
    #: Font size of the cells of a table with more than :attr:`TABLE_COLUMNS_AT_FULL_SIZE` columns, in points.
    TABLE_FONT_SIZE_WIDE: float = 7
    TABLE_COLUMNS_AT_FULL_SIZE: int = 6
    #: A bold header letter is about this much wider than a body letter.
    TABLE_HEADER_WIDTH_FACTOR: float = 1.2
    #: A column is given width in proportion to its longest cell, counted up to this many characters,
    #: so one long text column cannot squeeze the number columns beside it to nothing.
    TABLE_COLUMN_WEIGHT_CAP: int = 45

    @classmethod
    def table_column_widths(cls, table_as_list_of_lists: list[list[Any]], total_width: float) -> list[float]:
        """Share a table's width among its columns by the length of what they must hold on one line.

        A column must hold its longest body cell, which is usually a number or a name that should
        not break, and the longest single word of its header; the header itself wraps between
        words. Its weight is the longer of the two in characters (a bold header letter counting
        :attr:`TABLE_HEADER_WIDTH_FACTOR`), capped at
        :attr:`TABLE_COLUMN_WEIGHT_CAP`, plus 2; the widths are the total width in proportion to the
        weights. Example: a header ``["Component", "Investment [EUR]"]`` over the row
        ``["PVSystem", "17693.32"]`` weighs 12.8 (``Component``, 9 x 1.2 + 2) and 14 (``Investment``,
        10 x 1.2 + 2).

        Args:
            table_as_list_of_lists: The rows, the first one the header; a row may be shorter than the
                longest one.
            total_width: The width to share, in points.

        Returns:
            One width per column, in points, adding up to ``total_width``.
        """
        column_count = max((len(row) for row in table_as_list_of_lists), default=0)
        header = table_as_list_of_lists[0] if table_as_list_of_lists else []
        weights = []
        for column in range(column_count):
            body = [row[column] for row in table_as_list_of_lists[1:] if column < len(row)]
            longest_body_cell = max((len(str(cell).strip()) for cell in body if cell is not None), default=0)
            header_words = str(header[column]).split() if column < len(header) and header[column] is not None else []
            longest_header_word = max((len(word) for word in header_words), default=0) * cls.TABLE_HEADER_WIDTH_FACTOR
            weights.append(min(max(longest_body_cell, longest_header_word), cls.TABLE_COLUMN_WEIGHT_CAP) + 2)
        total_weight = sum(weights)
        return [total_width * weight / total_weight for weight in weights]

    def write_tables_to_report(self, table_as_list_of_lists: list[list[Any]]) -> None:
        """Add a table that fits the page width, its first row as the header.

        Every cell is set as a paragraph (8 pt, 7 pt above six columns), so a long text wraps between words inside its
        column
        instead of running past the page edge; the columns share the page's width by the length of
        their contents (:meth:`table_column_widths`), and the header row repeats on every page the
        table continues on. A ``None`` cell is left empty.

        Args:
            table_as_list_of_lists: The rows, the first one the header.
        """
        column_count = max((len(row) for row in table_as_list_of_lists), default=0)
        wide = column_count > self.TABLE_COLUMNS_AT_FULL_SIZE
        font_size = self.TABLE_FONT_SIZE_WIDE if wide else self.TABLE_FONT_SIZE
        cell_style = ParagraphStyle(
            name="TableCell",
            parent=self.styles["Normal"],
            fontSize=font_size,
            leading=font_size * 1.2,
        )
        header_style = ParagraphStyle(name="TableHeader", parent=cell_style, fontName="Helvetica-Bold")
        rows = []
        for row_index, row in enumerate(table_as_list_of_lists):
            style = header_style if row_index == 0 else cell_style
            texts = ["" if cell is None else escape(str(cell).strip(), quote=False) for cell in row]
            rows.append([Paragraph(text, style) for text in texts])
        table = Table(
            rows,
            colWidths=self.table_column_widths(table_as_list_of_lists, self.LARGEST_FIGURE_WIDTH),
            repeatRows=1,
            style=[
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
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
