"""The gate of the first real assemblies (``assemblies_spec.md`` §13 step 4, D9): the composed file is its twin.

``energy_systems/household_heatpump_building_sizer.composed.energy_system.yaml`` — the site plus six imports of
``energy_systems/assemblies/`` — is built (expansion of imports, then the wiring, which selects the observers' feeds
and writes them as ordinary feeds) and its members renamed to the twin's names by :data:`RENAME`. The renamed file
must equal ``household_heatpump_building_sizer.energy_system.yaml``, the recorded twin of the Python setup, outside
exactly these intended differences:

- **G7** (owner, 2026-10-04): the battery's many fact port writes its one-element ``sizing_sources`` list, where the
  twin writes no line;
- **a neutral swap** of the sequence (dry run §9.1): the buffer before the DHW cylinder, which read nothing from each
  other, so every pass stays bit-identical (the heating block writes its controllers in the twin's order, so the
  second swap the dry run lists, ControllerDHW before ControllerSH, does not arise);
- the composed file's schema version 4 and the record metadata the expansion writes beside the flat file.

Every class, preset, config line, input item, feed tag, weight and dispatch is compared; a feed's written position
is not, since feed resolution sorts every observer's feeds (``hisim/energy_system/feed_resolution.py``). Both files
then run for one day, and every result column and every KPI value is equal under the rename, to the last bit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

import pandas as pd
import pytest
import yaml

from hisim.config.names import NameSyntax
from hisim.energy_system.executor import run_energy_system
from hisim.energy_system.loader import dump_energy_system

REPOSITORY = Path(__file__).resolve().parents[2]
TWIN = REPOSITORY / "energy_systems" / "household_heatpump_building_sizer.energy_system.yaml"
COMPOSED = REPOSITORY / "energy_systems" / "household_heatpump_building_sizer.composed.energy_system.yaml"

#: Every expanded member address to the twin's name (dry run §4); the site entries keep the twin's names.
RENAME: Mapping[str, str] = {
    "heating-ControllerDHW": "HeatPumpControllerDHW",
    "heating-ControllerSH": "MoreAdvancedHeatPumpHPLibControllerSH",
    "heating-HeatPump": "MoreAdvancedHeatPumpHPLib",
    "heating-Buffer": "SimpleHotWaterStorage",
    "dhw-DHWStorage": "DHWStorage",
    "pv-pv_system-PVSystem": "PVSystem",
    "battery-battery-Battery": "Battery",
    "control-EMS": "L2EMSElectricityController",
    "grid-ElectricityMeter": "ElectricityMeter",
}

#: The neutral swaps of the sequence (dry run §9.1), each a pair of twin names adjacent in the twin's sequence.
NEUTRAL_SWAPS: Tuple[Tuple[str, str], ...] = (("DHWStorage", "SimpleHotWaterStorage"),)

#: G7: the battery's one-element sizing_sources list, the one config-level intended difference.
G7_LINE: Mapping[str, Any] = {"pv_peak_power_in_watt": ["PVSystem.pv_peak_power_in_watt"]}

#: One day at 900 s with the KPIs written as JSON, for both runs.
PARAMETERS: Mapping[str, Any] = {
    "start_date": "2021-01-01T00:00:00",
    "end_date": "2021-01-02T00:00:00",
    "seconds_per_timestep": 900,
    "country": "DE",
    "logging_level": 1,
    "post_processing_options": ["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"],
}


def renamed(text: str) -> str:
    """A component reference (``Name`` or ``Name.Output``) with its component renamed to the twin's name."""
    component, dot, rest = text.partition(".")
    return RENAME.get(component, component) + dot + rest


def renamed_column(column: str) -> str:
    """A result column, ``<component> - <output> [<unit>]``, with the component and its port-name parts renamed."""
    component, _, output = column.partition(" - ")
    return f"{renamed(component)} - {renamed_part(output)}"


def renamed_part(text: str) -> str:
    """A derived port, column or KPI name with every member's port-name part renamed (``heating_HeatPump``)."""
    for address, twin in RENAME.items():
        text = text.replace(NameSyntax.port_name_part(address), twin)
    return text


def renamed_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One component of the expanded document, every reference renamed, its feeds sorted by source.

    An input item is a bare name, an explicit wire (``input`` and ``from``) or a feed (``from`` without ``input``);
    the feeds are compared as a set, since feed resolution sorts them.
    """
    inputs: List[Any] = []
    feeds: List[Dict[str, Any]] = []
    for item in entry.get("inputs", []):
        if isinstance(item, str):
            inputs.append(renamed(item))
        elif "input" in item:
            inputs.append({**item, "from": renamed(item["from"])})
        else:
            feeds.append({**item, "from": renamed(item["from"])})
    result = {key: value for key, value in entry.items() if key not in ("inputs", "sizing_sources")}
    if inputs or feeds:
        result["inputs"] = inputs + sorted(feeds, key=lambda feed: feed["from"])
    if "sizing_sources" in entry:
        result["sizing_sources"] = {
            fact: [renamed(item) for item in value] if isinstance(value, list) else renamed(value)
            for fact, value in entry["sizing_sources"].items()
        }
    return result


@dataclass
class Run:
    """What one of the two runs produced: its file as built, its result columns and its KPIs."""

    document: Dict[str, Any]
    results: pd.DataFrame
    kpis: Dict[Tuple[str, str, str, str], Tuple[Any, Any]]


def kpi_values(path: Path, rename: bool) -> Dict[Tuple[str, str, str, str], Tuple[Any, Any]]:
    """Every KPI of a run by building, tag, name and source component, renamed for the composed run."""
    values: Dict[Tuple[str, str, str, str], Tuple[Any, Any]] = {}
    for building, tags in json.loads(path.read_text(encoding="utf-8")).items():
        for tag, entries in tags.items():
            for entry in entries.values():
                source = (entry.get("source") or {}).get("name") or ""
                name = entry["name"]
                if rename:
                    source, name = renamed(source), renamed_part(name)
                values[(building, tag, name, source)] = (entry["value"], entry["unit"])
    return values


@pytest.fixture(scope="module", name="runs")
def fixture_runs(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Run]:
    """Both files run for one day, each in its own result directory."""
    directory = tmp_path_factory.mktemp("heatpump_twin_gate")
    parameters = directory / "one_day.simulation.yaml"
    parameters.write_text(yaml.safe_dump(dict(PARAMETERS), sort_keys=False), encoding="utf-8")
    runs: Dict[str, Run] = {}
    for key, path in (("twin", TWIN), ("composed", COMPOSED)):
        built = run_energy_system(path, parameters, result_directory=str(directory / key))
        rename = key == "composed"
        results = built.simulator.results_data_frame
        if rename:
            results = results.rename(columns=renamed_column)
        runs[key] = Run(
            document=yaml.safe_load(dump_energy_system(built.model)),
            results=results,
            kpis=kpi_values(directory / key / "all_kpis.json", rename),
        )
    return runs


@pytest.mark.base
def test_the_composed_file_is_the_twin_outside_the_intended_differences(runs: Dict[str, Run]) -> None:
    """Catches any class, preset, config line, input item, feed tag, weight or dispatch the assemblies get wrong."""
    twin = yaml.safe_load(TWIN.read_text(encoding="utf-8"))
    composed = runs["composed"].document
    assert yaml.safe_load(COMPOSED.read_text(encoding="utf-8"))["schema_version"] == 4
    assert (composed["schema_version"], composed["name"], composed["description"]) == (
        twin["schema_version"],
        twin["name"],
        twin["description"],
    )
    components = {renamed(name): renamed_entry(entry) for name, entry in composed["components"].items()}
    # G7, the one intended difference inside a component: the battery's one-element list, where the twin writes none.
    assert components["Battery"].pop("sizing_sources") == G7_LINE
    assert "sizing_sources" not in twin["components"]["Battery"]
    # The twin's names are no addresses, so renaming leaves them as they are and only sorts the feeds.
    expected = {name: renamed_entry(entry) for name, entry in twin["components"].items()}
    assert sorted(components) == sorted(expected)
    for name, entry in expected.items():
        assert components[name] == entry, f"{name}: the composed file writes {components[name]}, the twin {entry}"


@pytest.mark.base
def test_the_sequence_is_the_twins_up_to_the_neutral_swap(runs: Dict[str, Run]) -> None:
    """Catches an evaluation order other than the twin's, or a swap of two components that read each other."""
    twin = yaml.safe_load(TWIN.read_text(encoding="utf-8"))["components"]
    sequence = list(twin)
    for first, second in NEUTRAL_SWAPS:
        position = sequence.index(first)
        assert sequence[position + 1] == second, f"{first} and {second} are not adjacent in the twin"
        for reader, read in ((first, second), (second, first)):
            items = twin[reader].get("inputs", [])
            sources = {(item if isinstance(item, str) else item["from"]).split(".")[0] for item in items}
            assert read not in sources, f"{reader} reads {read}: no neutral swap"
        sequence[position], sequence[position + 1] = second, first
    assert [renamed(name) for name in runs["composed"].document["components"]] == sequence


@pytest.mark.base
def test_every_result_column_of_one_day_is_the_twins(runs: Dict[str, Run]) -> None:
    """Catches a composed system that computes anything else than the twin, to the last bit."""
    twin, composed = runs["twin"].results, runs["composed"].results
    assert sorted(composed.columns) == sorted(twin.columns)
    unequal = [column for column in twin.columns if not twin[column].equals(composed[column])]
    assert not unequal, f"columns differing from the twin: {unequal}"


@pytest.mark.base
def test_every_kpi_of_one_day_is_the_twins(runs: Dict[str, Run]) -> None:
    """Catches a KPI that differs from the twin's under the address rename."""
    twin, composed = runs["twin"].kpis, runs["composed"].kpis
    assert sorted(composed) == sorted(twin)
    unequal = {key: (twin[key], composed[key]) for key in twin if twin[key] != composed[key]}
    assert not unequal, f"KPIs differing from the twin: {unequal}"
