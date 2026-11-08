"""The presentation table of the Sankey figure: titles and colours per component class, display names per port.

Read from ``sankey_style.yaml`` beside this module. Class-keyed titles and colours are presentation only; the
layout engine reads roles, never this table's keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping, Optional, Tuple

import yaml

from hisim.postprocessing.energy_balance.figure.graph import Flow, FlowKind, Role


@dataclass(frozen=True)
class NodeStyle:
    """The title and fill colour of one component class."""

    title: str
    color: str


@dataclass(frozen=True)
class SankeyStyle:
    """Titles, colours and display names of the figure.

    Attributes:
        kinds: The style of each component class (meters by component name).
        port_names: The display name of each port label or port name: a plain name, or (in, out) by direction.
        role_colors: The fill colour of each role, for a class without an entry.
        role_titles: The title of a node of a role whose class and name have no entry (meters of a composed file).
    """

    kinds: Mapping[str, NodeStyle]
    port_names: Mapping[str, Tuple[str, str]]
    role_colors: Mapping[str, str]
    role_titles: Mapping[str, str] = field(default_factory=dict)

    #: The style file beside this module.
    STYLE_FILE: ClassVar[str] = "sankey_style.yaml"
    #: The colour of a role missing from the style file.
    FALLBACK_COLOR: ClassVar[str] = "#b0b0b0"

    @staticmethod
    def load(path: Optional[Path] = None) -> "SankeyStyle":
        """Read the style table (the one beside this module by default)."""
        document = yaml.safe_load((path or Path(__file__).with_name(SankeyStyle.STYLE_FILE)).read_text(encoding="utf-8"))
        return SankeyStyle.from_document(document)

    @staticmethod
    def from_document(document: Mapping[str, Any]) -> "SankeyStyle":
        """Build the style from a parsed style file; a plain port name stands for both directions.

        Raises:
            ValueError: If a kind lacks its title or colour.
        """
        kinds = {}
        for name, entry in (document.get("kinds") or {}).items():
            if "title" not in entry or "color" not in entry:
                raise ValueError(f"style kind {name!r} needs a title and a color, it has {sorted(entry)}")
            kinds[str(name)] = NodeStyle(str(entry["title"]), str(entry["color"]))
        names = {}
        for key, entry in (document.get("port_names") or {}).items():
            names[str(key)] = (str(entry["in"]), str(entry["out"])) if isinstance(entry, dict) else (str(entry), str(entry))
        colors = {str(k): str(v) for k, v in (document.get("role_colors") or {}).items()}
        titles = {str(k): str(v) for k, v in (document.get("role_titles") or {}).items()}
        return SankeyStyle(kinds, names, colors, titles)

    def node_style(self, name: str, class_name: str, role: Role) -> NodeStyle:
        """Return a node's title and colour: by class, then by node name (meters), else by its role.

        Without an entry the colour is the role's and the title the role's title where the style has one (a meter of
        a composed file, whose name is prefixed), else the node's name.
        """
        entry = self.kinds.get(class_name) or self.kinds.get(name)
        if entry is not None:
            return entry
        return NodeStyle(self.role_titles.get(role.value, name), self.role_colors.get(role.value, SankeyStyle.FALLBACK_COLOR))

    def flow_name(self, flow: Flow, owner_class: str = "") -> str:
        """Return a flow's display name.

        Looked up by "<class>.<port>" (the class of the component that owns the port, so a composed file's prefixed
        instance names find it too), then by the port name alone (the label's last dotted part), choosing the
        direction's entry; else a boundary flow is named after its boundary node ("Grid import"), a residual
        "unaccounted loss" or "unaccounted gain", anything else after its port.

        Args:
            flow: The flow.
            owner_class: The class of the component named by the flow label's first part, "" if unknown.
        """
        if flow.kind is FlowKind.RESIDUAL:
            return "unaccounted gain" if flow.is_input else "unaccounted loss"
        port = flow.label.rsplit(".", 1)[-1]
        entry = self.port_names.get(f"{owner_class}.{port}") or self.port_names.get(port)
        if entry is not None:
            return entry[0] if flow.is_input else entry[1]
        if flow.kind is FlowKind.BOUNDARY and flow.other_end:
            return flow.other_end[0].upper() + flow.other_end[1:]
        return port
