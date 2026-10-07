"""The machinery of the twin gates (``assemblies_spec.md`` §13 steps 4 and 5, D9, D28): a composed file is its twin.

A composed file — the site plus imports of ``energy_systems/assemblies/`` — is built (expansion of imports, then the
wiring, which selects the observers' feeds and writes them as ordinary feeds) and its members renamed to the twin's
names by the rename map of its entry in the library table
:data:`~hisim.energy_system.assemblies.twins.COMPOSED_TWINS`, which the golden gate's ``composed`` mode reads too.
The renamed file must equal the recorded twin of the Python setup outside exactly the gate's intended differences:

- **G7** (owner, 2026-10-04): the battery's many fact port writes its one-element ``sizing_sources`` list, where the
  twin writes no line;
- **the neutral swaps** of the sequence (dry run §9.1), each two components adjacent in the twin that read nothing from
  each other, so every pass stays bit-identical;
- the composed file's schema version 4 and the record metadata the expansion writes beside the flat file.

Every class, preset, config line, input item, feed tag, weight and dispatch is compared; a feed's written position
is not, since feed resolution sorts every observer's feeds (``hisim/energy_system/feed_resolution.py``). Both files
then run for one day, and every result column and every KPI value is equal under the rename, to the last bit.

A gate module states its :class:`TwinGate` and calls the four checks below from its own tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

import pandas as pd
import yaml

from hisim.energy_system.assemblies.twins import ComposedTwin, rename_column, rename_port_parts, rename_reference
from hisim.energy_system.executor import run_energy_system
from hisim.energy_system.loader import dump_energy_system

#: The directory of the twins and the composed files.
ENERGY_SYSTEMS = Path(__file__).resolve().parents[2] / "energy_systems"

#: One day at 900 s with the KPIs written as JSON, for both runs.
PARAMETERS: Mapping[str, Any] = {
    "start_date": "2021-01-01T00:00:00",
    "end_date": "2021-01-02T00:00:00",
    "seconds_per_timestep": 900,
    "country": "DE",
    "logging_level": 1,
    "post_processing_options": ["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"],
}


@dataclass(frozen=True)
class TwinGate:
    """One gate: a composed twin of the library table and the intended differences.

    Attributes:
        entry: The twin, its composed file and the rename map, from
            :data:`~hisim.energy_system.assemblies.twins.COMPOSED_TWINS`; the site entries keep the twin's names.
        neutral_swaps: The neutral swaps of the sequence, each a pair of twin names adjacent in the twin's sequence.
        g7_component: The twin's name of the component whose many fact port writes the one-element list (G7).
        g7_line: That component's ``sizing_sources`` as the composed file writes it, renamed.
    """

    entry: ComposedTwin
    neutral_swaps: Tuple[Tuple[str, str], ...]
    g7_component: str
    g7_line: Mapping[str, Any]

    @property
    def twin_path(self) -> Path:
        """The twin's path."""
        return ENERGY_SYSTEMS / self.entry.twin

    @property
    def composed_path(self) -> Path:
        """The composed file's path."""
        return ENERGY_SYSTEMS / self.entry.composed

    def renamed(self, text: str) -> str:
        """A component reference (``Name`` or ``Name.Output``) with its component renamed to the twin's name."""
        return rename_reference(text, self.entry.rename)

    def renamed_column(self, column: str) -> str:
        """A result column, ``<component> - <output> [<unit>]``, with the component and its port-name parts renamed."""
        return rename_column(column, self.entry.rename)

    def renamed_part(self, text: str) -> str:
        """A derived port, column or KPI name with every member's port-name part renamed (``heating_HeatPump``)."""
        return rename_port_parts(text, self.entry.rename)

    def renamed_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """One component of a document, every reference renamed, its feeds sorted by source.

        An input item is a bare name, an explicit wire (``input`` and ``from``) or a feed (``from`` without
        ``input``); the feeds are compared as a set, since feed resolution sorts them.
        """
        inputs: List[Any] = []
        feeds: List[Dict[str, Any]] = []
        for item in entry.get("inputs", []):
            if isinstance(item, str):
                inputs.append(self.renamed(item))
            elif "input" in item:
                inputs.append({**item, "from": self.renamed(item["from"])})
            else:
                feeds.append({**item, "from": self.renamed(item["from"])})
        result = {key: value for key, value in entry.items() if key not in ("inputs", "sizing_sources")}
        if inputs or feeds:
            result["inputs"] = inputs + sorted(feeds, key=lambda feed: feed["from"])
        if "sizing_sources" in entry:
            result["sizing_sources"] = {
                fact: [self.renamed(item) for item in value] if isinstance(value, list) else self.renamed(value)
                for fact, value in entry["sizing_sources"].items()
            }
        return result


@dataclass
class Run:
    """What one of the two runs produced: its file as built, its result columns and its KPIs."""

    document: Dict[str, Any]
    results: pd.DataFrame
    kpis: Dict[Tuple[str, str, str, str], Tuple[Any, Any]]


def kpi_values(gate: TwinGate, path: Path, rename: bool) -> Dict[Tuple[str, str, str, str], Tuple[Any, Any]]:
    """Every KPI of a run by building, tag, name and source component, renamed for the composed run."""
    values: Dict[Tuple[str, str, str, str], Tuple[Any, Any]] = {}
    for building, tags in json.loads(path.read_text(encoding="utf-8")).items():
        for tag, entries in tags.items():
            for entry in entries.values():
                source = (entry.get("source") or {}).get("name") or ""
                name = entry["name"]
                if rename:
                    source, name = gate.renamed(source), gate.renamed_part(name)
                values[(building, tag, name, source)] = (entry["value"], entry["unit"])
    return values


def run_both(gate: TwinGate, directory: Path) -> Dict[str, Run]:
    """Both files of a gate run for one day, each in its own result directory below ``directory``."""
    parameters = directory / "one_day.simulation.yaml"
    parameters.write_text(yaml.safe_dump(dict(PARAMETERS), sort_keys=False), encoding="utf-8")
    runs: Dict[str, Run] = {}
    for key, path in (("twin", gate.twin_path), ("composed", gate.composed_path)):
        built = run_energy_system(path, parameters, result_directory=str(directory / key))
        rename = key == "composed"
        results = built.simulator.results_data_frame
        if rename:
            results = results.rename(columns=gate.renamed_column)
        runs[key] = Run(
            document=yaml.safe_load(dump_energy_system(built.model)),
            results=results,
            kpis=kpi_values(gate, directory / key / "all_kpis.json", rename),
        )
    return runs


def check_components(gate: TwinGate, runs: Dict[str, Run]) -> None:
    """Every component of the built composed file is the twin's under the rename, G7 aside."""
    twin = yaml.safe_load(gate.twin_path.read_text(encoding="utf-8"))
    composed = runs["composed"].document
    assert yaml.safe_load(gate.composed_path.read_text(encoding="utf-8"))["schema_version"] == 4
    assert (composed["schema_version"], composed["name"], composed["description"]) == (
        twin["schema_version"],
        twin["name"],
        twin["description"],
    )
    components = {gate.renamed(name): gate.renamed_entry(entry) for name, entry in composed["components"].items()}
    # G7, the one intended difference inside a component: the one-element list, where the twin writes none.
    assert components[gate.g7_component].pop("sizing_sources") == gate.g7_line
    assert "sizing_sources" not in twin["components"][gate.g7_component]
    # The twin's names are no addresses, so renaming leaves them as they are and only sorts the feeds.
    expected = {name: gate.renamed_entry(entry) for name, entry in twin["components"].items()}
    assert sorted(components) == sorted(expected)
    for name, entry in expected.items():
        assert components[name] == entry, f"{name}: the composed file writes {components[name]}, the twin {entry}"


def check_sequence(gate: TwinGate, runs: Dict[str, Run]) -> None:
    """The built composed file evaluates the twin's sequence up to the neutral swaps, each checked to be neutral."""
    twin = yaml.safe_load(gate.twin_path.read_text(encoding="utf-8"))["components"]
    sequence = list(twin)
    for first, second in gate.neutral_swaps:
        position = sequence.index(first)
        assert sequence[position + 1] == second, f"{first} and {second} are not adjacent in the twin"
        for reader, read in ((first, second), (second, first)):
            items = twin[reader].get("inputs", [])
            sources = {(item if isinstance(item, str) else item["from"]).split(".")[0] for item in items}
            assert read not in sources, f"{reader} reads {read}: no neutral swap"
        sequence[position], sequence[position + 1] = second, first
    assert [gate.renamed(name) for name in runs["composed"].document["components"]] == sequence


def check_columns(runs: Dict[str, Run]) -> None:
    """Every result column of the composed run equals the twin's, to the last bit."""
    twin, composed = runs["twin"].results, runs["composed"].results
    assert sorted(composed.columns) == sorted(twin.columns)
    unequal = [column for column in twin.columns if not twin[column].equals(composed[column])]
    assert not unequal, f"columns differing from the twin: {unequal}"


def check_kpis(runs: Dict[str, Run]) -> None:
    """Every KPI of the composed run equals the twin's under the address rename."""
    twin, composed = runs["twin"].kpis, runs["composed"].kpis
    assert sorted(composed) == sorted(twin)
    unequal = {key: (twin[key], composed[key]) for key in twin if twin[key] != composed[key]}
    assert not unequal, f"KPIs differing from the twin: {unequal}"
