"""Every twin the translator can select passes the simulator's pre-run cost-declaration check.

A RenoVisor calculation always asks for lifecycle costs, and `Simulator.check_cost_declarations`
refuses such a run before its first timestep when a component's class declares no
`cost_relevance`, or declares `PRICED` with nothing that can produce its cost facts. Several
component classes are in the second state today (the list is the bead hisim-l07.12), among them
the car pair and the air conditioner. The translator never selects the two twins that wire them
(an electric vehicle and an air conditioner are ``not_implemented_yet``), so no calculation is
refused (decision of 2026-09-23). This test keeps it that way: a twin added to `BaseFiles` with
such a class fails here, not in a user's cost run.

It applies the simulator's own rule, `simulator.cost_declaration_refusal`, to every class a twin
names -- in its components and in the options of its grouped variants alike -- so the test and the
refusal cannot disagree about which classes are refused. What the check does not cover is whether
the facts a class produces can be priced against the country's cost database; that is checked by
the end-to-end run tests, `tests/renovisor/test_run.py` and `tests/renovisor/test_staged_economics.py`.
"""

import importlib
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set

import pytest
import yaml

from hisim.renovisor.translate import BaseFiles
from hisim.simulator import cost_declaration_refusal

pytestmark = pytest.mark.base

REPOSITORY = Path(__file__).resolve().parents[2]


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


def classes_of(file_name: str) -> Set[type]:
    """The component classes one recorded twin wires, in its components and its variants' options."""
    path = REPOSITORY / BaseFiles.DIRECTORY / file_name
    document: Dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    classes = set()
    for dotted in class_paths(document):
        module, _, name = dotted.rpartition(".")
        classes.add(getattr(importlib.import_module(module), name))
    return classes


def refused_by_the_check(classes: Set[type]) -> List[str]:
    """The names of the classes the simulator's pre-run cost-declaration check refuses."""
    return sorted(
        component_class.__name__
        for component_class in classes
        if cost_declaration_refusal(component_class) is not None
    )


def test_the_translator_can_select_a_twin() -> None:
    """`BaseFiles` names at least one twin, so the parametrized check below is not vacuous."""
    assert BaseFiles.file_names()


@pytest.mark.parametrize("file_name", BaseFiles.file_names())
def test_every_selectable_twin_passes_the_cost_declaration_check(file_name: str) -> None:
    """No class of a twin the translator can select is refused by the pre-run cost check.

    Every class declares its cost relevance, and every `PRICED` one has a facts source, so the
    simulator's pre-run check lets a cost run through.

    Whether those facts are then priced against the country's cost database is checked by the
    end-to-end run tests, not here.
    """
    assert not refused_by_the_check(classes_of(file_name)), (
        f"{file_name} would be refused by the pre-run cost-declaration check"
    )


@pytest.mark.parametrize(
    "stem, refused",
    [
        ("household_heatpump_car_building_sizer.grouped", {"Car", "CarBattery"}),
        ("simple_air_conditioner_household_building_sizer.grouped", {"SimpleAirConditioner"}),
    ],
)
def test_the_two_unselected_twins_are_the_ones_the_check_refuses(stem: str, refused: Set[str]) -> None:
    """The check has teeth: the car and air-conditioner twins fail it, and are not selectable.

    It pins exactly the classes the check refuses in each, so when the car pair or the air
    conditioner gains a facts source this test fails and says the exclusion is no longer needed
    for cost reasons, and a further refused class in either twin is noticed too.
    """
    file_name = stem + BaseFiles.SUFFIX
    assert set(refused_by_the_check(classes_of(file_name))) == refused
    assert file_name not in BaseFiles.file_names()
