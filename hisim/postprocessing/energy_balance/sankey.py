"""The yearly energy flows of a run as Sankey diagrams, one per carrier and one over all (hisim-9uoo.9).

The links come from the declared ports and their peers (:mod:`.ports`): an ``IN`` port is a link from its peer,
an ``OUT`` port a link to it, a ``LOSS`` a link to its environment node (``outdoors``), a storage change a link
to or from the component's store, and a residual a link to or from the red ``unaccounted`` node. A transfer
both sides declare is drawn once, at the value the receiver books; what the sender booked beyond that goes to
``unaccounted``. A peer that declares no ports is drawn as a grey ``undeclared`` node, so the diagram also shows
how much of the house the balance covers.

So every declared component node carries as much out as in over all carriers (the residual link closes it); in a
carrier's own diagram the same holds for a component whose ports all carry that carrier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hisim.postprocessing.energy_balance.check import ComponentBalance
from hisim.postprocessing.energy_balance.ports import DeclaredPorts, Peer
from hisim import loadtypes as lt


@dataclass
class SankeyLink:
    """One yearly flow, in kWh, always positive."""

    source: str
    target: str
    kilowatt_hours: float
    #: The carrier's value, or None for a residual of a component that converts between carriers.
    carrier: Optional[str]
    label: str


class SankeyNodes:
    """Names and kinds of the diagram nodes."""

    UNACCOUNTED = "unaccounted"
    UNDECLARED_PREFIX = "undeclared: "

    @classmethod
    def kind(cls, node: str, declared: DeclaredPorts) -> str:
        """``component``, ``undeclared``, ``environment``, ``store``, ``unaccounted`` or ``unlinked``."""
        if node == cls.UNACCOUNTED:
            return "unaccounted"
        if node.startswith(cls.UNDECLARED_PREFIX):
            return "undeclared"
        if node.endswith(" (store)"):
            return "store"
        if node in {environment.value for environment in lt.EnvironmentNode}:
            return "environment"
        if node == Peer.unlinked().name:
            return "unlinked"
        return "component" if declared.is_declared(node) else "undeclared"


class SankeyBuilder:
    """Builds the yearly links of a run from its declared ports and balances."""

    #: Links below this many kWh over the run are left out.
    SMALLEST_LINK_IN_KILOWATT_HOUR = 1e-9

    def __init__(self, declared: DeclaredPorts, balances: Sequence[ComponentBalance]) -> None:
        """Build from the ports and the balances of one run."""
        self.declared = declared
        self.balances = list(balances)
        self.links: List[SankeyLink] = []

    def node_of(self, peer: Peer) -> str:
        """The node a peer is drawn as."""
        if peer.kind == Peer.COMPONENT and not self.declared.is_declared(peer.name):
            return SankeyNodes.UNDECLARED_PREFIX + peer.name
        return peer.name

    def add(self, source: str, target: str, kilowatt_hours: float, carrier: Optional[str], label: str) -> None:
        """Add a link, turned round when its value is negative."""
        if abs(kilowatt_hours) < self.SMALLEST_LINK_IN_KILOWATT_HOUR:
            return
        if kilowatt_hours < 0:
            source, target, kilowatt_hours = target, source, -kilowatt_hours
        self.links.append(SankeyLink(source, target, kilowatt_hours, carrier, label))

    def received_from(self) -> Dict[Tuple[str, str, str], float]:
        """What each declared receiver books as coming from each declared sender, per carrier."""
        received: Dict[Tuple[str, str, str], float] = {}
        for component, ports in self.declared.by_component.items():
            for series in ports:
                if series.port.role != lt.EnergyRole.IN or series.peer.kind != Peer.COMPONENT:
                    continue
                if not self.declared.is_declared(series.peer.name):
                    continue
                key = (series.peer.name, component, series.port.carrier.value)
                received[key] = received.get(key, 0.0) + series.annual_kilowatt_hours
        return received

    def build(self) -> List[SankeyLink]:
        """All yearly links of the run."""
        self.links = []
        received = self.received_from()
        sent: Dict[Tuple[str, str, str], float] = {}
        for component, ports in self.declared.by_component.items():
            for series in ports:
                carrier = series.port.carrier.value
                total = series.annual_kilowatt_hours
                role = series.port.role
                label = f"{component}.{series.output.field_name}"
                if role == lt.EnergyRole.IN:
                    self.add(self.node_of(series.peer), component, total, carrier, label)
                elif role == lt.EnergyRole.STORED_CHANGE:
                    self.add(component, series.peer.name, total, carrier, label)
                elif role == lt.EnergyRole.OUT and (component, series.peer.name, carrier) in received:
                    key = (component, series.peer.name, carrier)
                    sent[key] = sent.get(key, 0.0) + total
                else:
                    self.add(component, self.node_of(series.peer), total, carrier, label)
        for (sender, receiver, carrier), total in sent.items():
            self.add(
                sender,
                SankeyNodes.UNACCOUNTED,
                total - received[(sender, receiver, carrier)],
                carrier,
                f"{sender} -> {receiver}: sent but not received",
            )
        for balance in self.balances:
            self.add(
                balance.component,
                SankeyNodes.UNACCOUNTED,
                balance.residual,
                balance.single_carrier,
                f"{balance.component}: residual of its balance",
            )
        return self.links


class SankeyDiagram:
    """One diagram's nodes and links as the JSON and plotly take them."""

    COLOURS: Dict[str, str] = {
        "component": "#4a6fa5",
        "undeclared": "#a0a0a0",
        "environment": "#3c9d5d",
        "store": "#c49a2c",
        "unaccounted": "#d62728",
        "unlinked": "#7f7f7f",
    }

    @classmethod
    def data(cls, links: Sequence[SankeyLink], declared: DeclaredPorts) -> Dict[str, Any]:
        """``{"nodes": [...], "links": [...]}`` with links referring to nodes by index."""
        nodes: List[str] = []
        for link in links:
            for node in (link.source, link.target):
                if node not in nodes:
                    nodes.append(node)
        return {
            "nodes": [{"name": node, "kind": SankeyNodes.kind(node, declared)} for node in nodes],
            "links": [
                {
                    "source": nodes.index(link.source),
                    "target": nodes.index(link.target),
                    "value_kwh": link.kilowatt_hours,
                    "carrier": link.carrier,
                    "label": link.label,
                }
                for link in links
            ],
        }

    @classmethod
    def per_carrier(cls, links: Sequence[SankeyLink], declared: DeclaredPorts) -> Dict[str, Dict[str, Any]]:
        """One diagram per carrier that has a link, plus ``overall`` with every link."""
        diagrams: Dict[str, Dict[str, Any]] = {}
        for carrier in sorted({link.carrier for link in links if link.carrier is not None}):
            diagrams[carrier] = cls.data([link for link in links if link.carrier == carrier], declared)
        diagrams["overall"] = cls.data(links, declared)
        return diagrams

    @classmethod
    def figure(cls, diagram: Dict[str, Any], title: str) -> Any:
        """The plotly figure of one diagram."""
        import plotly.graph_objects as go  # pylint: disable=import-outside-toplevel

        nodes = diagram["nodes"]
        links = diagram["links"]
        figure = go.Figure(
            go.Sankey(
                valueformat=".1f",
                valuesuffix=" kWh",
                node={
                    "label": [node["name"] for node in nodes],
                    "color": [cls.COLOURS[node["kind"]] for node in nodes],
                    "pad": 18,
                },
                link={
                    "source": [link["source"] for link in links],
                    "target": [link["target"] for link in links],
                    "value": [link["value_kwh"] for link in links],
                    "label": [link["label"] for link in links],
                },
            )
        )
        figure.update_layout(title_text=title, font_size=12)
        return figure
