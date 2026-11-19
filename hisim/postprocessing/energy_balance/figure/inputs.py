"""The four inputs of the Sankey figure, read from a finished result directory.

A result directory holds the ``overall`` view of the energy Sankey (``energy_sankeys/energy_sankey.json``), the
balance report (``balance_report.json``), the KPIs (``all_kpis.json``) and, for a run started from an
energy-system file, the realized configuration (``realized.energy_system.yaml``). The figure needs only a few
things of each; :class:`SankeyInputs` holds exactly those, typed, so the layout engine never reads a file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

import yaml


class SankeyInputError(ValueError):
    """A result directory lacks an input of the figure, or an input is not what the figure expects."""


@dataclass(frozen=True)
class ViewNode:
    """One node of the ``overall`` view: its name and its kind (``component``, ``meter``, ``loss``, ...)."""

    name: str
    kind: str


@dataclass(frozen=True)
class ViewLink:
    """One edge of the ``overall`` view: a flow over the run, in kWh, always positive.

    Attributes:
        source: The name of the node the energy leaves.
        target: The name of the node it enters.
        value_kwh: The energy over the run, in kWh.
        carrier: The carrier's value (``electricity``, ``space_heating_heat``, ...), None for a residual.
        kind: The edge kind (``transfer``, ``metered``, ``loss``, ``storage``, ``boundary``, ``residual``, ...).
        label: The port the edge comes from, for example ``Building.HeatReceivedInWatt``.
    """

    source: str
    target: str
    value_kwh: float
    carrier: Optional[str]
    kind: str
    label: str


@dataclass(frozen=True)
class SankeyInputs:
    """What the figure reads of one run: the overall view, the balance facts, the KPIs and two configuration fields.

    Attributes:
        nodes: The nodes of the ``overall`` view, in the order the view lists them.
        links: The edges of the ``overall`` view, in the order the view lists them.
        class_of: The component class of every declared component and of every undeclared one, by name; a meter has
            no class and is missing.
        verdict_of: The balance verdict of every declared component (``closes``, ``does_not_close``, ...), by name.
        undeclared: The names of the components that declare no energy ports.
        verdict: The verdict of the whole balance.
        run_hours: The run's length, timesteps times seconds per step, in h.
        seconds_per_timestep: The step length of the run, in s.
        kpis: Every KPI of ``all_kpis.json`` by its full name ("Total energy from grid (ElectricityMeter)"), the
            value only; empty when the run wrote no KPI file.
        peak_power_in_watt: The configured ``power_in_watt`` of every component whose realized configuration has
            one, by component name; the PV's peak power is read from it. Empty without a realized record.
        title: The run's name for the figure title (the energy system's name, else the directory's).
    """

    nodes: Tuple[ViewNode, ...]
    links: Tuple[ViewLink, ...]
    class_of: Mapping[str, str]
    verdict_of: Mapping[str, str]
    undeclared: Tuple[str, ...]
    verdict: str
    run_hours: float
    seconds_per_timestep: int
    kpis: Mapping[str, float] = field(default_factory=dict)
    peak_power_in_watt: Mapping[str, float] = field(default_factory=dict)
    title: str = ""

    #: The prefix the view gives an undeclared component's node.
    UNDECLARED_PREFIX: ClassVar[str] = "undeclared: "
    #: The configuration field that holds a generator's peak power; the PV's kWp is read from it.
    PEAK_POWER_FIELD: ClassVar[str] = "power_in_watt"
    #: Where the view file may lie in a result directory, tried in this order.
    VIEW_PATHS: ClassVar[Tuple[str, ...]] = ("energy_sankeys/energy_sankey.json", "energy_sankey.json")
    #: The file names of the other three inputs in a result directory.
    BALANCE_FILE: ClassVar[str] = "balance_report.json"
    KPI_FILE: ClassVar[str] = "all_kpis.json"
    REALIZED_FILE: ClassVar[str] = "realized.energy_system.yaml"

    @staticmethod
    def from_result_directory(directory: Path) -> "SankeyInputs":
        """Read the figure's inputs from a result directory (or a directory holding the four files flat).

        Example: ``SankeyInputs.from_result_directory(Path("results/household_heatpump_building_sizer"))``.

        Args:
            directory: The result directory of a finished run.

        Returns:
            The inputs; the KPIs and the peak powers are empty when their files are missing.

        Raises:
            SankeyInputError: If the view or the balance report is missing.
        """
        view_path = next((directory / name for name in SankeyInputs.VIEW_PATHS if (directory / name).is_file()), None)
        balance_path = directory / SankeyInputs.BALANCE_FILE
        if view_path is None or not balance_path.is_file():
            raise SankeyInputError(f"{directory} holds no energy_sankey.json or no balance_report.json")
        view = json.loads(view_path.read_text(encoding="utf-8"))["overall"]
        balance = json.loads(balance_path.read_text(encoding="utf-8"))
        kpi_path = directory / SankeyInputs.KPI_FILE
        kpis = SankeyInputs.flat_kpis(json.loads(kpi_path.read_text(encoding="utf-8"))) if kpi_path.is_file() else {}
        realized_path = directory / SankeyInputs.REALIZED_FILE
        realized = yaml.safe_load(realized_path.read_text(encoding="utf-8")) if realized_path.is_file() else {}
        nodes, links = SankeyInputs.parse_view(view)
        return SankeyInputs(
            nodes=nodes,
            links=links,
            class_of=SankeyInputs.classes_from_balance(balance),
            verdict_of={entry["component"]: entry["verdict"] for entry in balance.get("components", [])},
            undeclared=tuple(entry["component"] for entry in balance.get("undeclared", [])),
            verdict=str(balance.get("verdict", "")),
            run_hours=float(balance["timesteps"]) * float(balance["seconds_per_timestep"]) / 3600.0,
            seconds_per_timestep=int(balance["seconds_per_timestep"]),
            kpis=kpis,
            peak_power_in_watt=SankeyInputs.peak_powers(realized or {}),
            title=str((realized or {}).get("name") or directory.name),
        )

    @staticmethod
    def parse_view(view: Mapping[str, Any]) -> Tuple[Tuple[ViewNode, ...], Tuple[ViewLink, ...]]:
        """Turn one view of ``energy_sankey.json`` into typed nodes and edges, the edges naming their nodes.

        Raises:
            SankeyInputError: If an edge names a node index the view does not have.
        """
        nodes = tuple(ViewNode(str(node["name"]), str(node["kind"])) for node in view["nodes"])
        links: List[ViewLink] = []
        for link in view["links"]:
            if not (0 <= link["source"] < len(nodes) and 0 <= link["target"] < len(nodes)):
                raise SankeyInputError(f"edge {link.get('label')!r} names a node index outside the view")
            links.append(
                ViewLink(
                    source=nodes[link["source"]].name,
                    target=nodes[link["target"]].name,
                    value_kwh=float(link["value_kwh"]),
                    carrier=link.get("carrier"),
                    kind=str(link["kind"]),
                    label=str(link["label"]),
                )
            )
        return nodes, tuple(links)

    @staticmethod
    def classes_from_balance(balance: Mapping[str, Any]) -> Dict[str, str]:
        """Return the class of every declared and undeclared component by its node name in the view.

        An undeclared component's node is named with the prefix "undeclared: ", so its class is filed under that
        name too.
        """
        classes = {entry["component"]: entry["class"] for entry in balance.get("components", [])}
        for entry in balance.get("undeclared", []):
            classes[SankeyInputs.UNDECLARED_PREFIX + entry["component"]] = entry["class"]
        return classes

    @staticmethod
    def flat_kpis(document: Mapping[str, Any]) -> Dict[str, float]:
        """Return every numeric KPI of an ``all_kpis.json`` document by name, over all buildings and categories.

        Example: ``{"BUI1": {"General": {"Self-sufficiency rate of electricity": {"value": 70.7}}}}`` gives
        ``{"Self-sufficiency rate of electricity": 70.7}``. A name met twice keeps its first value.
        """
        result: Dict[str, float] = {}
        for categories in document.values():
            if not isinstance(categories, dict):
                continue
            for entries in categories.values():
                if not isinstance(entries, dict):
                    continue
                for name, entry in entries.items():
                    value = entry.get("value") if isinstance(entry, dict) else None
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and name not in result:
                        result[name] = float(value)
        return result

    @staticmethod
    def peak_powers(realized: Mapping[str, Any]) -> Dict[str, float]:
        """Return the configured ``power_in_watt`` of every component of a realized record that has one, in W."""
        result: Dict[str, float] = {}
        for name, spec in (realized.get("components") or {}).items():
            config = (spec or {}).get("config") or {}
            value = config.get(SankeyInputs.PEAK_POWER_FIELD)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[str(name)] = float(value)
        return result

    def kpi(self, prefix: str) -> Optional[float]:
        """Return the first KPI whose name starts with ``prefix``, or None when the run has none."""
        return next((value for name, value in self.kpis.items() if name.startswith(prefix)), None)
