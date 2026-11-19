"""Finishing and writing the Sankey figure: connectors, residual marks, labels, title and caption; PNG, PDF, SVG, JSON.

The search leaves the library's drawing of its last round; the renderer sets the canvas as the axes limits (the
library's ``finish()`` fits the axes to its own extents, which must not win), sizes the figure at the fixed pixels
per unit, draws the connector bands, marks residual branches, writes every label line by line at its box, and saves.
``overall.layout.json`` records the library's parameters, the routes and the labels.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Sequence

import matplotlib
import numpy as np
from matplotlib.patches import PathPatch, Polygon
from matplotlib.path import Path as MplPath

from hisim.postprocessing.energy_balance.figure.connectors import Bands
from hisim.postprocessing.energy_balance.figure.graph import FlowKind
from hisim.postprocessing.energy_balance.figure.parameters import LayoutConstants
from hisim.postprocessing.energy_balance.figure.search import Layout
from hisim.postprocessing.energy_balance.figure.texts import FigureTexts


class SankeyFigure:
    """Finishes a layout's drawing and writes PNG, PDF, SVG and the layout file."""

    #: The outline colour of every band.
    OUTLINE: ClassVar[str] = "#404040"
    #: The colour of a residual that is no error.
    RESIDUAL_GREY: ClassVar[str] = "#9e9e9e"
    #: Inches above the canvas for the title and below it for the caption, and the height of one caption line.
    TITLE_IN: ClassVar[float] = 0.55
    CAPTION_IN: ClassVar[float] = 0.7
    CAPTION_LINE_IN: ClassVar[float] = 0.19
    #: The mean width of a caption character, in px (9 pt DejaVu Sans).
    CAPTION_PX_PER_CHARACTER: ClassVar[float] = 7.6
    #: The file names written into the output directory.
    STEM: ClassVar[str] = "overall"

    @staticmethod
    def finish(layout: Layout, constants: LayoutConstants) -> Any:
        """Draw the connectors, residual marks, labels, title and caption onto the last round's figure and size it."""
        drawing = layout.drawing
        assert drawing is not None  # a layout with something to draw has a drawing
        figure, axes = drawing.figure, drawing.axes
        x0, x1, y0, y1 = layout.canvas
        width_in = (x1 - x0) * constants.px_per_unit / constants.dpi
        axes_in = (y1 - y0) * constants.px_per_unit / constants.dpi
        caption = SankeyFigure.wrap_caption(layout.caption, width_in * constants.dpi)
        caption_in = SankeyFigure.CAPTION_IN + max(0, len(caption) - 2) * SankeyFigure.CAPTION_LINE_IN
        height_in = SankeyFigure.TITLE_IN + axes_in + caption_in
        figure.set_size_inches(width_in, height_in)
        axes.set_position((0.0, caption_in / height_in, 1.0, axes_in / height_in))
        axes.set_xlim(x0, x1)
        axes.set_ylim(y0, y1)
        for link_id, route in sorted(drawing.routes.items()):
            if route.shape == "failed":
                continue
            source = next(c for c in layout.parameters.connectors if c.link_id == link_id).source_flow.split("|", 1)[0]
            line, tangents = Bands.rounded_centre_line(route.points, route.radii)
            outline, left, right = Bands.outline(line, tangents, route.width)
            axes.add_patch(Polygon(outline, closed=True, facecolor=layout.parameters.diagram(source).color, edgecolor="none", zorder=2))
            for side in (left, right):
                axes.plot(side[:, 0], side[:, 1], color=SankeyFigure.OUTLINE, linewidth=0.6, zorder=2.1, solid_capstyle="butt")
            connector = next(c for c in layout.parameters.connectors if c.link_id == link_id)
            if connector.parallel:
                head = SankeyFigure.arrowhead(
                    route.points[-1], route.points[-1][0] - route.points[-2][0], route.points[-1][1] - route.points[-2][1], route.width, constants
                )
                axes.add_patch(Polygon(head, closed=True, facecolor=layout.parameters.diagram(source).color, edgecolor=SankeyFigure.OUTLINE, linewidth=0.6, zorder=2.1))
        SankeyFigure.mark_residuals(layout, axes, constants)
        units_per_pt = constants.dpi / 72.0 / constants.px_per_unit
        for label in drawing.labels:
            line_height = label.block.font_pt * 1.25 * units_per_pt
            x = {"left": label.box.x0, "right": label.box.x1}.get(label.align, label.box.centre[0])
            for k, text in enumerate(label.block.lines):
                weight = "bold" if k < label.block.bold_lines else "normal"
                axes.text(
                    x,
                    label.box.y1 - (k + 0.5) * line_height,
                    text,
                    ha=label.align,
                    va="center",
                    fontsize=label.block.font_pt,
                    color=label.block.color,
                    fontweight=weight,
                    zorder=6,
                )
        figure.text(0.5, 1 - 0.28 / height_in, layout.title, ha="center", va="center", fontsize=14)
        figure.text(0.005, 0.08 / height_in, "\n".join(caption), fontsize=9, color="#555555", ha="left", va="bottom", linespacing=1.5)
        return figure

    @staticmethod
    def arrowhead(base: Sequence[float], dx: float, dy: float, width: float, constants: LayoutConstants) -> List[Any]:
        """Return the triangle that ends a band of ``width`` at ``base`` with a tip pointing along (dx, dy).

        The head is as long as the target's notch is deep (half the width times the pitch), so its tip lands on the
        notch's inner point. Example: an eastward band 1 u wide ending at (5, 0) gets the tip (5.35, 0).
        """
        length = float(np.hypot(dx, dy)) or 1.0
        d = np.array([dx, dy]) / length
        n = np.array([-d[1], d[0]])
        b = np.array(base, dtype=float)
        tip = b + d * width / 2 * constants.pitch
        return [tuple(b + n * width / 2), tuple(tip), tuple(b - n * width / 2)]

    @staticmethod
    def mark_residuals(layout: Layout, axes: Any, constants: LayoutConstants) -> None:
        """Draw a residual branch in the unaccounted style: a hatched box with a dashed outline, red for an error."""
        drawing = layout.drawing
        assert drawing is not None  # only called with a drawing
        errors = {label.key for label in drawing.labels if label.block.color == FigureTexts.ERROR_RED}
        for diagram in layout.parameters.diagrams:
            kinds = {f.flow_id: f.kind for f in layout.graph.node(diagram.component).flows}
            for params in diagram.flows:
                if kinds.get(params.flow_id) is not FlowKind.RESIDUAL:
                    continue
                picture = drawing.drawn[diagram.component]
                outer, _, width, away = FigureTexts.stub_geometry(picture, params, constants, layout.parameters.kwh_per_unit)
                corners = SankeyFigure.residual_box(outer, away.vector, width, params.pathlength)
                color = FigureTexts.ERROR_RED if f"flow:{params.flow_id}" in errors else SankeyFigure.RESIDUAL_GREY
                codes = [MplPath.MOVETO] + [MplPath.LINETO] * 3 + [MplPath.CLOSEPOLY]
                axes.add_patch(
                    PathPatch(MplPath(corners + corners[:1], codes), facecolor="#e0e0e0", edgecolor=color, hatch="///", linewidth=1.2, linestyle="--", zorder=4)
                )

    @staticmethod
    def residual_box(outer: Sequence[float], away: Sequence[float], width: float, length: float) -> List[Any]:
        """Return the four corners of the box over a branch: from its outer end back towards the trunk by its length.

        Example: an eastward branch of width 1 ending at (5, 0), 2 u long, gives (5, ±0.58) and (3, ±0.58).
        """
        d = np.array(away, dtype=float)
        n = np.array([-d[1], d[0]])
        half = width / 2 + 0.08
        start = np.array(outer, dtype=float)
        end = start - d * length
        return [tuple(start + n * half), tuple(end + n * half), tuple(end - n * half), tuple(start - n * half)]

    @staticmethod
    def wrap_caption(lines: Sequence[str], width_px: float) -> List[str]:
        """Return the caption lines wrapped to the figure's width (9 pt DejaVu Sans averages about 7.6 px a character).

        Example: a 500-character line on a 1,500 px wide figure becomes three lines of at most 197 characters.
        """
        characters = max(40, int(width_px / SankeyFigure.CAPTION_PX_PER_CHARACTER))
        wrapped: List[str] = []
        for line in lines:
            wrapped.extend(textwrap.wrap(line, characters) or [""])
        return wrapped

    @staticmethod
    def write(layout: Layout, directory: Path, constants: LayoutConstants) -> List[Path]:
        """Write the figure as PNG, PDF and SVG and the layout file into a directory; return the paths written."""
        directory.mkdir(parents=True, exist_ok=True)
        written = []
        with matplotlib.rc_context({"font.family": "DejaVu Sans", "svg.fonttype": "none", "svg.hashsalt": "hisim"}):
            figure = SankeyFigure.finish(layout, constants)
            for extension in ("png", "pdf", "svg"):
                path = directory / f"{SankeyFigure.STEM}.{extension}"
                metadata: Dict[str, Any] = {"CreationDate": None} if extension == "pdf" else ({"Date": None} if extension == "svg" else {})
                figure.savefig(path, dpi=constants.dpi, facecolor="white", metadata=metadata)
                written.append(path)
        layout_path = directory / f"{SankeyFigure.STEM}.layout.json"
        layout_path.write_text(LayoutFile.dumps(layout), encoding="utf-8")
        written.append(layout_path)
        error_path = directory / f"{SankeyFigure.STEM}.layout_error.json"
        if layout.faults:
            written.append(SankeyFigure.write_faults(directory, SankeyFigure.fault_sentences(layout), SankeyFigure.overlap_sentences(layout)))
        elif error_path.exists():
            error_path.unlink()
        return written

    @staticmethod
    def fault_sentences(layout: Layout) -> List[str]:
        """Return a layout's faults as sentences, each starting with its kind (``"overlap: ..."``)."""
        return [f"{fault.kind}: {fault.message}" for fault in layout.faults]

    @staticmethod
    def overlap_sentences(layout: Layout) -> List[str]:
        """Return a layout's overlaps as lines ``"<kind> <first> <> <second>"``."""
        return [f"{overlap.kind} {overlap.first} <> {overlap.second}" for overlap in layout.overlaps]

    @staticmethod
    def write_faults(directory: Path, faults: List[str], overlaps: List[str]) -> Path:
        """Write ``overall.layout_error.json`` (``{"faults": [...], "overlaps": [...]}``) into a directory; return its path.

        The golden check reads this file (status ``layout_fault``); a run whose layout has no fault has none.

        Args:
            directory: Where the file goes; created if missing.
            faults: Every fault as a sentence.
            overlaps: Every overlap left, as a line.

        Returns:
            The path written.
        """
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{SankeyFigure.STEM}.layout_error.json"
        path.write_text(json.dumps({"faults": faults, "overlaps": overlaps}, indent=1), encoding="utf-8")
        return path


class LayoutFile:
    """Serialises a layout to ``overall.layout.json``: the library's parameters, routes and labels, rounded."""

    #: Decimal places of every length in the file.
    DIGITS: ClassVar[int] = 4
    #: The schema of the layout file.
    SCHEMA: ClassVar[str] = "hisim.sankey_layout/2"

    @staticmethod
    def rounded(value: float) -> float:
        """Return a length rounded to the file's digits, with -0.0 written as 0.0."""
        result = round(value, LayoutFile.DIGITS)
        return 0.0 if result == 0 else result

    @staticmethod
    def dumps(layout: Layout) -> str:
        """Return the layout as JSON text (sorted keys, two-space indent)."""
        r = LayoutFile.rounded
        parameters = layout.parameters
        drawing = layout.drawing
        routes = drawing.routes if drawing is not None else {}
        document = {
            "schema": LayoutFile.SCHEMA,
            "title": layout.title,
            "canvas": {"kwh_per_unit": parameters.kwh_per_unit, "xlim": [r(layout.canvas[0]), r(layout.canvas[1])], "ylim": [r(layout.canvas[2]), r(layout.canvas[3])]},
            "chain": list(layout.graph.chain),
            "rounds": layout.rounds,
            "diagrams": [
                {
                    "component": d.component,
                    "prior": d.prior,
                    "connect": d.connect.split("|", 1)[1] if d.connect else None,
                    "at": [r(d.at[0]), r(d.at[1])] if d.at is not None else None,
                    "axis": d.axis.value,
                    "trunklength": r(d.trunklength),
                    "flows": [
                        {
                            "link": f.flow_id.split("|", 1)[1],
                            "orientation": f.orientation,
                            "pathlength": r(f.pathlength),
                            "value_kwh": r(f.value_kwh),
                            "inflated": f.inflated,
                        }
                        for f in d.flows
                    ],
                }
                for d in parameters.diagrams
            ],
            "connectors": [
                {"link": c.label, "shape": routes[c.link_id].shape, "parallel": c.parallel, "points": [[r(x), r(y)] for x, y in routes[c.link_id].points]}
                for c in parameters.connectors
                if c.link_id in routes
            ],
            "labels": [
                {"key": label.key, "box": [r(label.box.x0), r(label.box.y0), r(label.box.x1), r(label.box.y1)], "lines": list(label.block.lines)}
                for label in (drawing.labels if drawing is not None else [])
            ],
            "overlaps": [{"kind": o.kind, "first": o.first, "second": o.second} for o in layout.overlaps],
            "faults": [{"kind": f.kind, "message": f.message} for f in layout.faults],
        }
        return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
