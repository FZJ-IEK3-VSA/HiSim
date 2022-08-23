""" Module for generating reports. """
import copy
import time
import os
from typing import Any
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Table

from hisim import utils


class ReportGenerator:

    """ Class for generating reports. """

    def __init__(self, dirpath: str=None) -> None:
        """ Initializes the pdf report. """
        self.story: Any
        self.filepath = os.path.join(dirpath, "report.pdf")
        self.open()
        self.write_preamble()
        self.close()

    def open(self):
        """ Opens a file. """
        self.doc = SimpleDocTemplate(self.filepath, pagesize=letter,
                                     rightMargin=72, leftMargin=72,
                                     topMargin=72, bottomMargin=18)
        self.styles = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(name='Justify', alignment=TA_JUSTIFY))

    def write_preamble(self):
        """ Writes the preamble. """
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

        # Inserts authors
        authors = ["Developers: Dr. Noah Pflugradt",
                   "Vitor Hugo Bellotto Zago"]
        for part in authors:
            ptext = f'<font size="12">{part.strip()}</font>'
            story.append(Paragraph(ptext, self.styles["Normal"]))
        story.append(Spacer(1, 12))

        # Inserts address
        address_parts = ["Forschungszentrum Jülich",
                         "Institute of Energy and Climate Research",
                         "Techno - Economic Systems Analysis (IEK - 3)",
                         "Wilhelm - Johnen - Straße",
                         "52428 Jülich",
                         "Germany"]
        for part in address_parts:
            ptext = f'<font size="12">{part.strip()}</font>'
            story.append(Paragraph(ptext, self.styles["Normal"]))
        story.append(Spacer(1, 12))

        # Inserts configuration setup
        # config = ["Setup function: {}".format(self.setup.function),
        #          "Mode: {}".format(self.setup.mode)]
        # for part in config:
        #    ptext = '<font size="12">%s</font>' % part.strip()
        #    story.append(Paragraph(ptext, self.styles["Normal"]))
        # story.append(Spacer(1, 12))

        # Inserts
        # ptext = '<font size="12">Mode description: {}</font>'.format(self.setup.get_description())
        # self.setup.description
        # story.append(Paragraph(ptext, self.styles["Justify"]))
        # story.append(Spacer(1, 12))

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
        """ Writes a paragraph. """
        if len(text) != 0:
            bar_string = "=============================================================="
            self.story.append(Paragraph(bar_string, self.styles["Normal"]))
            for part in text:
                ptext = f'<font size="12">{part}</font>'
                self.story.append(Paragraph(ptext, self.styles["Normal"]))
            self.story.append(Spacer(1, 12))

    def get_story(self):
        """ Gets the story. """
        self.story = copy.deepcopy(self.story)

    def close(self):
        """ Closes the report. """
        story = copy.deepcopy(self.story)
        self.doc.build(story)
