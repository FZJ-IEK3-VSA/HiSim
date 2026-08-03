"""Tests for the dynamic-input harvest in ``tools/generate_component_db.py``.

A dynamic component grows one input per source class listed in its
``dynamic_default_connections``, and the editor derives those from the registry. Some wiring
however exists only as a hand-written ``add_component_input_and_connect`` call in a Python
setup, so nothing declares it — the ElectricityMeter reading the EMS's
``TotalElectricityToOrFromGrid`` being the case that matters. ``_collect_dynamic_inputs``
mines those out of the shipped scenarios so the editor can offer them.

The unit tests below drive the collector with synthetic scenario dicts (no file I/O, so the
edge cases are stated rather than hoped for); the last two check the harvest that actually
shipped in ``usage_db.json``.
"""

# These tests deliberately exercise the generator's private harvesting functions.
# pylint: disable=protected-access

from __future__ import annotations

import importlib.util
import json
import os

import pytest

pytestmark: pytest.MarkDecorator = pytest.mark.base

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GEN_PATH = os.path.join(_REPO_ROOT, "tools", "generate_component_db.py")
_DATA_DIR = os.path.join(_REPO_ROOT, "system_setups", "editor", "public", "data")

_spec = importlib.util.spec_from_file_location("generate_component_db", _GEN_PATH)
assert _spec is not None and _spec.loader is not None
_gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen)

_METER = "hisim.components.electricity_meter.ElectricityMeter"
_EMS = "hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem"
_PV = "hisim.components.generic_pv_system.PVSystem"


def _scenario(*components: dict) -> dict:
    """Wrap component dicts into the shape a scenario file has."""
    return {"components": list(components), "connections": []}


def _component(classname: str, name: str, inputs: list | None = None) -> dict:
    """One entry of a scenario's ``components`` array."""
    return {
        "component_full_classname": classname,
        "configuration": {"name": name},
        "inputs": inputs or [],
    }


def _dynamic_input(source_name: str, output: str, **overrides) -> dict:
    """A ``"dynamic": true`` entry of a component's ``inputs`` array."""
    entry = {
        "dynamic": True,
        "source_object_name": source_name,
        "source_component_output": output,
        "source_load_type": "Electricity",
        "source_unit": "W",
        "source_tags": ["ElectricityProduction"],
        "source_weight": 999,
    }
    entry.update(overrides)
    return entry


def _collect(scenarios: list, declared: dict | None = None) -> dict:
    """Run the collector over several scenarios and return what it accumulated."""
    collected: dict = {}
    for scenario in scenarios:
        _gen._collect_dynamic_inputs(scenario, {_METER, _EMS, _PV}, declared or {}, collected)
    return collected


# ---------------------------------------------------------------------------
# _is_declared_dynamic_input
# ---------------------------------------------------------------------------


def test_declared_check_matches_a_bare_class_name_against_a_full_path() -> None:
    """``dynamic_default_connections`` is keyed by bare class name, scenarios use full paths."""
    declared = {("PVSystem", "ElectricityOutput")}

    assert _gen._is_declared_dynamic_input(declared, _PV, "ElectricityOutput") is True
    assert _gen._is_declared_dynamic_input(declared, _PV, "SomeOtherOutput") is False
    assert _gen._is_declared_dynamic_input(declared, _EMS, "ElectricityOutput") is False


def test_declared_check_does_not_match_a_partial_class_name() -> None:
    """The suffix match has to be on a dotted boundary, or ``System`` would match ``PVSystem``."""
    assert _gen._is_declared_dynamic_input({("System", "ElectricityOutput")}, _PV, "ElectricityOutput") is False


def test_declared_pairs_are_read_off_the_registry() -> None:
    """Every (source class key, field) a component declares ends up in the lookup set."""
    components = [
        {
            "component_full_classname": _METER,
            "dynamic_default_connections": {
                "PVSystem": [{"source_component_field_name": "ElectricityOutput"}],
                "UtspLpgConnector": [{"source_component_field_name": "ElectricalPowerConsumption"}],
            },
        },
        {"component_full_classname": _EMS, "dynamic_default_connections": {}},
    ]

    declared = _gen._declared_dynamic_inputs(components)

    assert declared[_METER] == {
        ("PVSystem", "ElectricityOutput"),
        ("UtspLpgConnector", "ElectricalPowerConsumption"),
    }
    assert declared[_EMS] == set()


# ---------------------------------------------------------------------------
# _collect_dynamic_inputs
# ---------------------------------------------------------------------------


def test_undeclared_wiring_is_recorded_verbatim() -> None:
    """The declaration is kept as written so the editor need not invent tags or a weight."""
    collected = _collect([
        _scenario(
            _component(_EMS, "L2EMSElectricityController"),
            _component(_METER, "ElectricityMeter", [
                _dynamic_input("L2EMSElectricityController", "TotalElectricityToOrFromGrid")
            ]),
        )
    ])

    assert list(collected) == [_METER]
    (declaration,) = collected[_METER]
    assert declaration["source_component_class"] == _EMS
    assert declaration["source_component_field_name"] == "TotalElectricityToOrFromGrid"
    assert declaration["source_load_type"] == "Electricity"
    assert declaration["source_unit"] == "W"
    assert declaration["source_tags"] == ["ElectricityProduction"]
    assert declaration["source_weight"] == 999
    assert declaration["scenarios"] == 1


def test_wiring_the_registry_already_declares_is_skipped() -> None:
    """Re-listing a default connection would only duplicate what the editor already offers."""
    collected = _collect(
        [
            _scenario(
                _component(_PV, "PVSystem"),
                _component(_METER, "ElectricityMeter", [
                    _dynamic_input("PVSystem", "ElectricityOutput")
                ]),
            )
        ],
        declared={_METER: {("PVSystem", "ElectricityOutput")}},
    )

    assert not collected


def test_repeated_wiring_is_counted_not_duplicated() -> None:
    """Three scenarios wiring the same pair give one declaration with a count of three."""
    scenario = _scenario(
        _component(_EMS, "L2EMSElectricityController"),
        _component(_METER, "ElectricityMeter", [
            _dynamic_input("L2EMSElectricityController", "TotalElectricityToOrFromGrid")
        ]),
    )

    collected = _collect([scenario, scenario, scenario])

    assert len(collected[_METER]) == 1
    assert collected[_METER][0]["scenarios"] == 3


def test_static_inputs_are_ignored() -> None:
    """Only ``"dynamic": true`` entries describe a port that has to be created."""
    collected = _collect([
        _scenario(
            _component(_EMS, "L2EMSElectricityController"),
            _component(_METER, "ElectricityMeter", [
                _dynamic_input("L2EMSElectricityController", "TotalElectricityToOrFromGrid", dynamic=False)
            ]),
        )
    ])

    assert not collected


def test_a_source_outside_the_registry_is_ignored() -> None:
    """An unknown source class cannot be offered, so recording it would be dead weight."""
    collected = _collect([
        _scenario(
            _component("hisim.components.obsolete.Gone", "Ghost"),
            _component(_METER, "ElectricityMeter", [_dynamic_input("Ghost", "ElectricityOutput")]),
        )
    ])

    assert not collected


def test_a_dangling_source_name_is_ignored() -> None:
    """A ``source_object_name`` naming no component in the file resolves to no class."""
    collected = _collect([
        _scenario(
            _component(_METER, "ElectricityMeter", [_dynamic_input("NotOnCanvas", "Whatever")])
        )
    ])

    assert not collected


# ---------------------------------------------------------------------------
# The harvest that shipped
# ---------------------------------------------------------------------------


def _load(filename: str) -> dict:
    with open(os.path.join(_DATA_DIR, filename), "r", encoding="utf-8") as fh:
        data: dict = json.load(fh)
    return data


def test_shipped_usage_db_offers_the_ems_total_to_the_meter() -> None:
    """The wiring every ``*_building_sizer`` setup writes by hand must be mined and offered.

    Without it the editor cannot connect an ElectricityMeter to an EMS in a scenario built
    from scratch, because nothing in the component registry declares that pairing.
    """
    declarations = _load("usage_db.json").get("dynamic_inputs", {}).get(_METER, [])

    ems_total = [d for d in declarations if d["source_component_class"] == _EMS]
    assert len(ems_total) == 1, "the EMS total must be mined exactly once"
    assert ems_total[0]["source_component_field_name"] == "TotalElectricityToOrFromGrid"
    assert ems_total[0]["source_weight"] == 999
    assert ems_total[0]["source_tags"] == ["ElectricityProduction"]
    assert ems_total[0]["scenarios"] >= 1


def test_shipped_usage_db_never_repeats_a_declared_connection() -> None:
    """The mined table holds only what introspection cannot supply.

    Anything the registry already declares is offered from there, so a duplicate here would
    show the same option twice in the editor's panel.
    """
    usage_db = _load("usage_db.json")
    declared = _gen._declared_dynamic_inputs(_load("component_db.json")["components"])

    for target, declarations in usage_db.get("dynamic_inputs", {}).items():
        for declaration in declarations:
            assert not _gen._is_declared_dynamic_input(
                declared.get(target, set()),
                declaration["source_component_class"],
                declaration["source_component_field_name"],
            ), f"{target} already declares {declaration['source_component_field_name']}"
