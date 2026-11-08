"""The pin file: a hand layout (the prototype's ``layouts/<stem>.yaml`` keys) drawn instead of the computed layout.

A *complete* pin file lists every sub-diagram with its prior, connection, ``at``, trunk length, title offset and its
flows in the library's order with their orientations, path lengths and label shifts, and every connector with its
turns and label; the figure then draws exactly that, in one round, and reports what overlaps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, List, Mapping, Optional, Tuple

import yaml

from hisim.postprocessing.energy_balance.figure.geometry import Direction, Side
from hisim.postprocessing.energy_balance.figure.graph import DrawnGraph, GraphBuilder
from hisim.postprocessing.energy_balance.figure.inputs import SankeyInputs
from hisim.postprocessing.energy_balance.figure.parameters import ConnectorParams, DiagramParams, FlowParams, LabelPin, LayoutConstants, Parameters, Scale
from hisim.postprocessing.energy_balance.figure.search import Layout, PlacementSearch
from hisim.postprocessing.energy_balance.figure.style import SankeyStyle


class PinError(ValueError):
    """A pin file names a component, flow or connector the run does not have, or lacks a key."""


class PinFile:
    """Reads a complete pin file into the library's parameters."""

    #: The schema a pin file declares.
    SCHEMA: ClassVar[str] = "hisim.sankey_pins/1"

    @staticmethod
    def draw(inputs: SankeyInputs, path: Path, constants: LayoutConstants) -> Layout:
        """Return the layout of a run drawn from a complete pin file, in one round without repair moves."""
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        graph = GraphBuilder.build(inputs)
        style = SankeyStyle.load()
        parameters = PinFile.parameters(document, graph, style, constants)
        return PlacementSearch.draw(inputs, parameters, constants, style)

    @staticmethod
    def label_pin(entry: Mapping[str, Any]) -> Optional[LabelPin]:
        """Return a flow's label pin from its entry ("label" shift, "ha", "va", "inside", "color", "on_bands"), or None."""
        if not any(key in entry for key in ("label", "ha", "va", "inside")):
            return None
        shift = entry.get("label", [0.0, 0.0])
        return LabelPin(
            shift=(float(shift[0]), float(shift[1])),
            ha=str(entry.get("ha", "")),
            va=str(entry.get("va", "")),
            inside=bool(entry.get("inside", False)),
            color=str(entry.get("color", "white")),
            exempt=tuple(str(name) for name in entry.get("on_bands", [])),
        )

    @staticmethod
    def flow_id(component: str, entry: Mapping[str, Any]) -> str:
        """Return the graph's id of a pinned flow: "<component>|<link>", "<component>|residual" or "<component>|cut"."""
        if entry.get("residual"):
            return f"{component}|residual"
        if entry.get("cut"):
            return f"{component}|cut"
        if "link" not in entry:
            raise PinError(f"{component}: a flow entry needs a link, residual or cut: {dict(entry)}")
        return f"{component}|{entry['link']}"

    @staticmethod
    def parameters(document: Mapping[str, Any], graph: DrawnGraph, style: SankeyStyle, constants: LayoutConstants) -> Parameters:
        """Return the parameters a complete pin file gives, the flows' values from the run.

        Raises:
            PinError: If the file's schema is wrong or it names a component, flow or link the run does not have.
        """
        if document.get("schema") != PinFile.SCHEMA:
            raise PinError(f"a pin file declares schema {PinFile.SCHEMA}, not {document.get('schema')!r}")
        canvas = document.get("canvas") or {}
        widest = max((f.magnitude_kwh for node in graph.nodes for f in node.flows), default=1.0)
        kwh_per_unit = float(canvas.get("kwh_per_unit") or Scale.kwh_per_unit(widest, constants.widest_band_in_units))
        min_band = Scale.min_band_kwh(kwh_per_unit, constants)
        names = {node.name for node in graph.nodes}
        diagrams: List[DiagramParams] = []
        for entry in document.get("diagrams", []):
            component = str(entry["component"])
            if component not in names:
                raise PinError(f"the pin file names {component!r}, which the run does not draw")
            diagrams.append(PinFile.diagram(entry, graph, style, (kwh_per_unit, min_band)))
        connectors = [PinFile.connector(entry, graph) for entry in document.get("connectors", [])]
        limits = None
        if "xlim" in canvas and "ylim" in canvas:
            limits = (float(canvas["xlim"][0]), float(canvas["xlim"][1]), float(canvas["ylim"][0]), float(canvas["ylim"][1]))
        return Parameters(kwh_per_unit, min_band, diagrams, connectors, limits, pinned=True)

    @staticmethod
    def diagram(entry: Mapping[str, Any], graph: DrawnGraph, style: SankeyStyle, scale: Tuple[float, float]) -> DiagramParams:
        """Return one pinned sub-diagram; its cut, when it has one, is its first flow (a flat tail of 0.56 u)."""
        _, min_band = scale
        component = str(entry["component"])
        node = graph.node(component)
        values = {f.flow_id: f.value_kwh for f in node.flows}
        flows: List[FlowParams] = []
        if entry.get("cut"):
            flows.append(FlowParams(f"{component}|cut", values[f"{component}|cut"], Scale.drawn_kwh(values[f"{component}|cut"], min_band), 0, 0.56, Side.TAIL))
        for item in entry.get("flows", []):
            flow_id = PinFile.flow_id(component, item)
            if flow_id not in values:
                raise PinError(f"{component}: the pin file names the flow {flow_id!r}, which the run does not have")
            value = values[flow_id]
            orientation = int(item["orientation"])
            side = {0: Side.TAIL if value > 0 else Side.HEAD, 1: Side.LEFT, -1: Side.RIGHT}[orientation]
            flows.append(
                FlowParams(
                    flow_id, value, Scale.drawn_kwh(value, min_band), orientation, float(item["pathlength"]), side, PinFile.label_pin(item), str(item.get("name", ""))
                )
            )
        node_style = style.node_style(component, node.class_name, graph.roles[component])
        prior = entry.get("prior")
        connect = f"{component}|{entry['connect']}" if prior is not None else None
        prior_flow = f"{prior}|{entry['connect']}" if prior is not None else None
        at = entry.get("at")
        offset = entry.get("title_offset", [0.0, 0.0])
        return DiagramParams(
            component,
            str(prior) if prior is not None else None,
            connect,
            prior_flow,
            Direction.E,
            0,
            (float(at[0]), float(at[1])) if at is not None else None,
            float(entry["trunklength"]),
            (float(offset[0]), float(offset[1])),
            node_style.color,
            str(entry.get("title", node_style.title)),
            flows,
        )

    @staticmethod
    def connector(entry: Mapping[str, Any], graph: DrawnGraph) -> ConnectorParams:
        """Return one pinned connector: its link found by label, its turns, its label placement and ``parallel``.

        ``parallel: true`` draws a second link into a child of its source as an arrow of its own (D24); without it the
        connector joins the tree connection with one seam, as the prototype's hand layouts stack it.
        """
        link = next((candidate for candidate in graph.links if candidate.label == entry["link"]), None)
        if link is None:
            raise PinError(f"the pin file names the connector {entry['link']!r}, which the run does not have")
        source = next(f.flow_id for f in graph.node(link.source).flows if f.link_id == link.link_id)
        target = next(f.flow_id for f in graph.node(link.target).flows if f.link_id == link.link_id)
        label = entry.get("label") or {}
        shift = label.get("shift", [0.0, 0.0])
        pin = LabelPin(shift=(float(shift[0]), float(shift[1])), segment=int(label.get("segment", 0)), side=str(label.get("side", "")), at=float(label.get("at", 0.5)))
        return ConnectorParams(link.link_id, link.label, source, target, list(entry.get("turns", [])), pin, bool(entry.get("parallel", False)))
