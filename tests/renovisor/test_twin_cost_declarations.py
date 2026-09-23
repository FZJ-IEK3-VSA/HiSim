"""Every twin the translator can select can be priced (hisim-l07.12, decision of 2026-09-23).

A RenoVisor calculation always asks for lifecycle costs, and `Simulator.check_cost_declarations`
refuses such a run before its first timestep when a component's class declares no
`cost_relevance`, or declares `PRICED` with nothing that can produce its cost facts. Fourteen
classes are in the second state today, among them the car pair and the air conditioner. The
translator never selects the two twins that wire them (an electric vehicle and an air conditioner
are ``not_implemented_yet``), so no calculation fails. This test keeps it that way: a twin added
to `BaseFiles` with such a class fails here, not in a user's cost run.

It applies the simulator's own predicate to each class a twin names, so the test and the refusal
cannot disagree about which classes have a facts source.
"""

import importlib
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set

import pytest
import yaml

from hisim.economics.facts import CostRelevance
from hisim.renovisor.translate import BaseFiles
from hisim.simulator import _has_cost_facts_source  # pylint: disable=protected-access

pytestmark = pytest.mark.base

REPOSITORY = Path(__file__).resolve().parents[2]


def selectable_twins() -> List[str]:
    """Every twin stem `BaseFiles` can select, with or without solar thermal."""
    return sorted(set(BaseFiles.BY_GENERATOR.values()) | set(BaseFiles.WITH_SOLAR_THERMAL.values()))


def class_paths(node: Any) -> Iterator[str]:
    """Every ``class:`` value anywhere in a parsed energy-system file, grouped variants included."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "class" and isinstance(value, str):
                yield value
            else:
                yield from class_paths(value)
    elif isinstance(node, list):
        for item in node:
            yield from class_paths(item)


def classes_of(stem: str) -> Set[type]:
    """The component classes one recorded twin wires."""
    path = REPOSITORY / BaseFiles.DIRECTORY / f"{stem}{BaseFiles.SUFFIX}"
    document: Dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    classes = set()
    for dotted in class_paths(document["components"]):
        module, _, name = dotted.rpartition(".")
        classes.add(getattr(importlib.import_module(module), name))
    return classes


def undescribable(classes: Set[type]) -> List[str]:
    """The classes a lifecycle-cost run would refuse, by the simulator's own rule."""
    refused = []
    for component_class in classes:
        relevance = getattr(component_class, "cost_relevance", CostRelevance.UNDECLARED)
        if relevance is CostRelevance.UNDECLARED or (
            relevance is CostRelevance.PRICED and not _has_cost_facts_source(component_class)
        ):
            refused.append(component_class.__name__)
    return sorted(refused)


@pytest.mark.parametrize("stem", selectable_twins())
def test_every_selectable_twin_can_be_priced(stem: str) -> None:
    """No class of a twin the translator can select is refused by the cost-declaration check."""
    assert not undescribable(classes_of(stem)), f"{stem} would refuse a lifecycle-cost run"


@pytest.mark.parametrize(
    "stem, refused",
    [
        ("household_heatpump_car_building_sizer.grouped", {"Car", "CarBattery"}),
        ("simple_air_conditioner_household_building_sizer.grouped", {"SimpleAirConditioner"}),
    ],
)
def test_the_two_unselected_twins_are_the_ones_that_could_not(stem: str, refused: Set[str]) -> None:
    """The check has teeth: the car and air-conditioner twins fail it, and are not selectable.

    When the car pair or the air conditioner gains a facts source, this test fails and says the
    exclusion is no longer needed for cost reasons.
    """
    assert refused <= set(undescribable(classes_of(stem)))
    assert stem not in selectable_twins()
