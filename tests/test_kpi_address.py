"""Tests for stable KPI addresses: the source of a component KPI, its key, and the finder.

``roadmap/kpi_address_spec.md``: every KPI has one address that is a function of the KPI itself
-- building, tag, name and, for a component KPI, its structured source -- and callers never build
or split a key string. Each test states the failure mode it catches.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pytest

from hisim.cli import ExitCodes, main
from hisim.component import Component
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.postprocessing.kpi_computation.kpi_address import KpiAddress, KpiFinder
from hisim.postprocessing.kpi_computation.kpi_preparation import KpiPreparation
from hisim.postprocessing.kpi_computation.kpi_structure import KpiAddressStep, KpiEntry, KpiSource, KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters
from scripts.golden_kpis import golden_leaves


class _ReportingComponent(Component):
    """A component reporting the KPIs it is handed, the way the component library builds them."""

    def __init__(self, component_id: ComponentID, display_config: DisplayConfig, entries: List[KpiEntry]) -> None:
        """Builds the component under the given identity, reporting ``entries``."""
        super().__init__(
            name=component_id.key,
            my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
            my_config=ConfigBase(component_id=component_id),
            my_display_config=display_config,
        )
        self._entries = entries

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: object) -> List[KpiEntry]:
        """Reports the entries it was built with."""
        return self._entries


def _collection(entries: List[KpiEntry], building: str = "BUI1") -> Dict[str, Dict[str, Dict[str, Any]]]:
    """One building's tag-sorted collection, keyed and sorted the way the KPI generator does it.

    The component entries go through the production keying; the derived ones are keyed by name.
    """
    component_entries = KpiPreparation.keyed_component_entries([entry for entry in entries if entry.source])
    derived_entries = {entry.name: entry.to_dict() for entry in entries if entry.source is None}
    sorted_collection: Dict[str, Dict[str, Dict[str, Any]]] = {building: {}}
    for key, entry in {**derived_entries, **component_entries}.items():
        sorted_collection[building].setdefault(entry["tag"], {})[key] = entry
    return sorted_collection


def _stamped(component_id: ComponentID, *names: str, tag: KpiTagEnumClass = KpiTagEnumClass.CAR) -> List[KpiEntry]:
    """The entries a plain component of that identity reports, stamped by the base class."""
    component = _ReportingComponent(
        component_id,
        DisplayConfig(),
        [KpiEntry(name=name, unit="kWh", value=float(index), tag=tag) for index, name in enumerate(names)],
    )
    return component.component_kpi_entries(all_outputs=[], postprocessing_results=pd.DataFrame())


@pytest.mark.base
def test_the_source_serializes_under_the_exact_names_of_the_contract() -> None:
    """Catches the camelCase entry leaking its case into the source, or ``import`` losing its name.

    ``import`` is a Python keyword, so the field is ``import_key`` in Python and must be pinned to
    ``import`` in JSON, in the source and in each step of its path; ``display_name`` sits inside a
    camelCase entry and must stay snake_case. The path has two steps, so their order round-trips.
    """
    source = KpiSource(
        import_key="pv",
        instance="east",
        path=(KpiAddressStep(import_key="pv", instance="east"), KpiAddressStep(import_key="inverter", instance=None)),
        member="PVSystem",
        assembly="pv/array",
        name="pv-east-PVSystem",
        display_name="PV array, east",
        label="Garage roof panels",
    )
    entry = KpiEntry(name="Electricity production", unit="kWh", value=1.0, source=source)

    written = entry.to_dict()

    assert written["source"] == {
        "import": "pv",
        "instance": "east",
        "path": [{"import": "pv", "instance": "east"}, {"import": "inverter", "instance": None}],
        "member": "PVSystem",
        "assembly": "pv/array",
        "name": "pv-east-PVSystem",
        "display_name": "PV array, east",
        "label": "Garage roof panels",
    }
    assert "nameOfSourceComponent" in written
    assert KpiEntry.from_dict(json.loads(json.dumps(written))).source == source
    assert KpiSource.from_json_object(json.loads(json.dumps(written["source"])), "here") == source


@pytest.mark.base
def test_a_source_needs_a_name() -> None:
    """Catches a source that could not qualify a key."""
    with pytest.raises(ValueError, match="non-empty runtime name"):
        KpiSource(name="")


@pytest.mark.base
def test_a_plain_component_source_is_its_identity_and_its_display_name() -> None:
    """Catches the source mixing up the member (the plain name) and the runtime name (the key)."""
    source = KpiSource.for_component(ComponentID("HeatPump", building="BUI2"), DisplayConfig.show("Heat pump"))

    assert source == KpiSource(
        import_key=None,
        instance=None,
        member="HeatPump",
        assembly=None,
        name="BUI2_HeatPump",
        display_name="Heat pump",
        label=None,
    )
    assert KpiSource.for_component(ComponentID("HeatPump"), DisplayConfig()).display_name == "HeatPump"


@pytest.mark.base
def test_the_base_class_stamps_its_source_on_every_entry_and_keeps_a_source_already_given() -> None:
    """Catches an entry without a source, or one reported on another's behalf being relabelled."""
    foreign = KpiSource.for_component(ComponentID("Battery"), DisplayConfig())
    component = _ReportingComponent(
        ComponentID("EMS"),
        DisplayConfig.show("Energy manager"),
        [
            KpiEntry(name="Own", unit="-", value=1.0),
            KpiEntry(name="On behalf", unit="-", value=2.0, source=foreign),
        ],
    )

    own, on_behalf = component.component_kpi_entries(all_outputs=[], postprocessing_results=pd.DataFrame())

    assert own.source == component.kpi_source()
    assert own.source.display_name == "Energy manager"
    assert own.name_of_source_component == "EMS"
    assert on_behalf.source == foreign
    assert on_behalf.name_of_source_component == "Battery"


@pytest.mark.base
def test_an_entry_naming_another_component_by_the_deprecated_field_alone_is_refused() -> None:
    """Catches a component reporting for another one without saying which component that is."""
    component = _ReportingComponent(
        ComponentID("EMS"),
        DisplayConfig(),
        [KpiEntry(name="On behalf", unit="-", value=2.0, name_of_source_component="Battery")],
    )

    with pytest.raises(ValueError, match="by name only"):
        component.component_kpi_entries(all_outputs=[], postprocessing_results=pd.DataFrame())


@pytest.mark.base
def test_the_key_of_a_component_kpi_is_qualified_whoever_else_is_present() -> None:
    """Catches the key scheme: one component, two of one class, and a derived KPI."""
    one_car = _collection(_stamped(ComponentID("Car1"), "Distance driven"))
    two_cars = _collection(
        _stamped(ComponentID("Car1"), "Distance driven") + _stamped(ComponentID("Car2"), "Distance driven")
    )
    derived = _collection([KpiEntry(name="Self-consumption rate", unit="%", value=41.2, tag=KpiTagEnumClass.GENERAL)])

    assert list(one_car["BUI1"]["Car"]) == ["Distance driven (Car1)"]
    assert list(two_cars["BUI1"]["Car"]) == ["Distance driven (Car1)", "Distance driven (Car2)"]
    assert list(derived["BUI1"]["General"]) == ["Self-consumption rate"]
    assert derived["BUI1"]["General"]["Self-consumption rate"]["source"] is None


@pytest.mark.base
def test_the_address_builds_the_key_and_the_golden_form() -> None:
    """Catches the one place that builds a key disagreeing with the golden references' flat form."""
    source = KpiSource(
        name="pv-east-PVSystem", import_key="pv", instance="east", path=_steps(("pv", "east")), member="PVSystem"
    )
    address = KpiAddress(building="BUI1", tag="PV", name="Electricity production", source=source)

    assert address.key == "Electricity production (pv-east-PVSystem)"
    assert address.dotted == "BUI1.PV.Electricity production (pv-east-PVSystem)"
    assert KpiAddress(building="BUI1", tag="General", name="Self-consumption rate").dotted == (
        "BUI1.General.Self-consumption rate"
    )


def _steps(*steps: Tuple[str, Optional[str]]) -> Tuple[KpiAddressStep, ...]:
    """An address path of ``(import, instance)`` steps, outermost first."""
    return tuple(KpiAddressStep(import_key=import_key, instance=instance) for import_key, instance in steps)


def _composed_collection() -> Dict[str, Any]:
    """The worked example of the spec: two PV arrays of one import, a heat pump, a Building, a derived KPI."""

    def entry(name: str, value: float, tag: str, source: KpiSource) -> Dict[str, Any]:
        written = KpiEntry(name=name, unit="kWh", value=value, source=source, name_of_source_component=source.name)
        return {**written.to_dict(), "tag": tag}

    east = KpiSource(
        import_key="pv",
        instance="east",
        path=_steps(("pv", "east")),
        member="PVSystem",
        assembly="pv/array",
        name="pv-east-PVSystem",
    )
    west = KpiSource(
        import_key="pv",
        instance="west",
        path=_steps(("pv", "west")),
        member="PVSystem",
        assembly="pv/array",
        name="pv-west-PVSystem",
    )
    heat_pump = KpiSource(
        import_key="heating", path=_steps(("heating", None)), member="HeatPump", name="heating-HeatPump"
    )
    building = KpiSource(member="Building", name="Building")
    production = "Electricity production"
    return {
        "BUI1": {
            "General": {
                "Self-consumption rate": KpiEntry(
                    name="Self-consumption rate", unit="%", value=41.2, tag=KpiTagEnumClass.GENERAL
                ).to_dict()
            },
            "PV": {
                KpiAddress.key_for(production, east): entry(production, 3120.5, "PV", east),
                KpiAddress.key_for(production, west): entry(production, 2875.0, "PV", west),
            },
            "Heat Pump For Space Heating": {
                KpiAddress.key_for("Seasonal performance factor", heat_pump): entry(
                    "Seasonal performance factor", 3.4, "Heat Pump For Space Heating", heat_pump
                )
            },
            "Building": {
                KpiAddress.key_for("Heating load", building): entry("Heating load", 7.9, "Building", building)
            },
        }
    }


@pytest.mark.base
def test_the_finder_filters_on_the_source_fields_and_never_splits_a_key() -> None:
    """Catches "every KPI of import pv" needing a string split, or returning a neighbour's KPI."""
    finder = KpiFinder(_composed_collection())

    assert len(finder.addresses()) == 5
    assert [getattr(address.source, "instance") for address in finder.addresses(import_key="pv")] == ["east", "west"]
    ((address, entry),) = finder.entries(import_key="pv", instance="east")
    assert entry["value"] == 3120.5
    assert address.dotted == "BUI1.PV.Electricity production (pv-east-PVSystem)"
    assert finder.value(name="Electricity production", source="pv-west-PVSystem") == 2875.0
    assert finder.value(name="Self-consumption rate") == 41.2
    assert finder.addresses(member="PVSystem", assembly="pv/array", tag="PV", building="BUI1") == finder.addresses(
        import_key="pv"
    )
    assert finder.addresses(source="Building")[0].name == "Heating load"
    assert finder.addresses(building="BUI2") == []


@pytest.mark.base
def test_one_fails_by_name_on_two_candidates_and_on_none() -> None:
    """Catches a lookup that meant "the array's production" in a two-array setup reading one of them."""
    finder = KpiFinder(_composed_collection())

    with pytest.raises(ValueError, match=r"2 KPIs match name='Electricity production'") as raised:
        finder.one(name="Electricity production")
    assert "BUI1.PV.Electricity production (pv-east-PVSystem)" in str(raised.value)
    assert "BUI1.PV.Electricity production (pv-west-PVSystem)" in str(raised.value)
    with pytest.raises(ValueError, match="No KPI matches name='Distance driven'"):
        finder.value(name="Distance driven")


@pytest.mark.base
def test_the_derived_filter_keeps_the_kpis_without_a_source_and_refuses_a_source_filter_beside_it() -> None:
    """Catches a derived lookup reading a component's KPI of the same name, or a caller re-counting matches."""
    finder = KpiFinder(_composed_collection())

    assert [address.name for address, _ in finder.entries(derived=True)] == ["Self-consumption rate"]
    assert finder.value(name="Self-consumption rate", derived=True) == 41.2
    with pytest.raises(ValueError, match="No KPI matches name='Electricity production', derived=True"):
        finder.one(name="Electricity production", derived=True)
    with pytest.raises(ValueError, match=r"A derived KPI has no source, so it matches no source filter \(member\)"):
        finder.entries(member="PVSystem", derived=True)


@pytest.mark.base
def test_the_finder_reads_a_json_written_before_the_source_existed() -> None:
    """Catches the finder failing on, or misreading, an ``all_kpis.json`` of an earlier run.

    Such a file has no ``source``; its component entries name their component in
    ``nameOfSourceComponent`` and are keyed by their bare name while unique in the building.
    """
    legacy = {
        "BUI1": {
            "Car": {
                "Distance driven": {
                    "name": "Distance driven",
                    "value": 42.0,
                    "tag": "Car",
                    "nameOfSourceComponent": "Car1",
                },
                "Battery losses (Car1)": {
                    "name": "Battery losses",
                    "value": 1.0,
                    "tag": "Car",
                    "nameOfSourceComponent": "Car1",
                },
            },
            "General": {"Self-consumption rate": {"name": "Self-consumption rate", "value": 41.2, "tag": "General"}},
        }
    }

    finder = KpiFinder(legacy)

    address, _ = finder.one(name="Distance driven")
    assert address.source == KpiSource(name="Car1")
    assert address.source.member is None and address.source.import_key is None
    assert finder.value(source="Car1", name="Battery losses") == 1.0
    assert finder.one(name="Self-consumption rate")[0].source is None


@pytest.mark.base
def test_the_finder_refuses_a_key_that_disagrees_with_its_entry() -> None:
    """Catches a collection whose keys were built by hand drifting from the entries they hold."""
    source = KpiSource.for_component(ComponentID("Car1"), DisplayConfig())
    entry = KpiEntry(
        name="Distance driven",
        unit="km",
        value=1.0,
        tag=KpiTagEnumClass.CAR,
        source=source,
        name_of_source_component="Car1",
    )

    with pytest.raises(ValueError, match="addresses itself as 'BUI1.Car.Distance driven \\(Car1\\)'"):
        KpiFinder({"BUI1": {"Car": {"Distance driven": entry.to_dict()}}})


@pytest.mark.base
def test_an_entry_whose_two_source_fields_disagree_cannot_be_read() -> None:
    """Catches a reader of ``source`` and a reader of ``nameOfSourceComponent`` reading two components."""
    entry = KpiEntry(
        name="Distance driven",
        unit="km",
        value=1.0,
        source=KpiSource.for_component(ComponentID("Car1"), DisplayConfig()),
        name_of_source_component="Car2",
    ).to_dict()

    with pytest.raises(ValueError, match="names two different sources"):
        KpiSource.from_entry_dict(entry)


@pytest.mark.base
def test_the_golden_leaf_form_carries_the_address_fields_without_the_presentation() -> None:
    """Catches the golden references losing the source's identity, or churning on its presentation.

    ``scripts/golden_kpis.py::golden_leaves`` keys every leaf by its dotted address and stores the
    value, the unit and the address fields; of the source only the identity fields, its path
    included, never ``display_name`` or ``label``.
    """
    leaves = golden_leaves(_composed_collection())

    assert set(leaves) == {address.dotted for address in KpiFinder(_composed_collection()).addresses()}
    assert leaves["BUI1.PV.Electricity production (pv-east-PVSystem)"] == {
        "value": 3120.5,
        "unit": "kWh",
        "building": "BUI1",
        "tag": "PV",
        "name": "Electricity production",
        "source": {
            "import": "pv",
            "instance": "east",
            "path": [{"import": "pv", "instance": "east"}],
            "member": "PVSystem",
            "assembly": "pv/array",
            "name": "pv-east-PVSystem",
        },
    }
    assert leaves["BUI1.General.Self-consumption rate"]["source"] is None
    assert leaves["BUI1.General.Self-consumption rate"]["unit"] == "%"


@pytest.mark.base
def test_the_cli_lists_one_dotted_address_per_matching_kpi(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Catches ``hisim kpis list`` printing anything but the addresses the filters select."""
    (tmp_path / "all_kpis.json").write_text(json.dumps(_composed_collection()), encoding="utf-8")

    code = main(["kpis", "list", str(tmp_path), "--import", "pv"])
    printed = capsys.readouterr().out.splitlines()

    assert code == ExitCodes.OK
    assert printed == [
        "BUI1.PV.Electricity production (pv-east-PVSystem) = 3120.5 kWh",
        "BUI1.PV.Electricity production (pv-west-PVSystem) = 2875.0 kWh",
    ]
    assert main(["kpis", "list", str(tmp_path / "all_kpis.json"), "--tag", "General"]) == ExitCodes.OK
    assert capsys.readouterr().out.splitlines() == ["BUI1.General.Self-consumption rate = 41.2 %"]
    assert main(["kpis", "list", str(tmp_path), "--instance", "west", "--building", "BUI1"]) == ExitCodes.OK
    assert capsys.readouterr().out.splitlines() == ["BUI1.PV.Electricity production (pv-west-PVSystem) = 2875.0 kWh"]
    assert main(["kpis", "list", str(tmp_path), "--source", "heating-HeatPump"]) == ExitCodes.OK
    assert capsys.readouterr().out.splitlines() == [
        "BUI1.Heat Pump For Space Heating.Seasonal performance factor (heating-HeatPump) = 3.4 kWh"
    ]


@pytest.mark.base
def test_the_cli_writes_a_value_that_is_not_a_number_as_json_and_omits_an_empty_unit(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Catches a KPI without a computed value printing as Python's ``None``, or a dangling blank unit."""
    collection = {
        "BUI1": {
            "General": {
                "Not computed": KpiEntry(
                    name="Not computed", unit="", value=None, tag=KpiTagEnumClass.GENERAL
                ).to_dict(),
                "Heating system": KpiEntry(
                    name="Heating system", unit="-", value="Gas boiler", tag=KpiTagEnumClass.GENERAL
                ).to_dict(),
            }
        }
    }
    (tmp_path / "all_kpis.json").write_text(json.dumps(collection), encoding="utf-8")

    assert main(["kpis", "list", str(tmp_path)]) == ExitCodes.OK
    assert capsys.readouterr().out.splitlines() == [
        "BUI1.General.Not computed = null",
        'BUI1.General.Heating system = "Gas boiler" -',
    ]


@pytest.mark.base
@pytest.mark.parametrize("document, json_type", [([1, 2], "list"), ("text", "string"), (None, "null"), (3, "number")])
def test_the_cli_refuses_a_document_that_is_not_a_kpi_collection(
    tmp_path: Path, capsys: pytest.CaptureFixture, document: Any, json_type: str
) -> None:
    """Catches ``hisim kpis list`` on a malformed ``all_kpis.json`` ending in a traceback instead of a refusal."""
    (tmp_path / "all_kpis.json").write_text(json.dumps(document), encoding="utf-8")

    assert main(["kpis", "list", str(tmp_path)]) == ExitCodes.FILE_REJECTED
    assert f"all_kpis.json holds a JSON {json_type}, not a KPI collection" in capsys.readouterr().err


@pytest.mark.base
def test_the_cli_refuses_a_path_without_a_kpi_collection(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Catches the command printing nothing, and succeeding, for a directory that holds no KPIs."""
    code = main(["kpis", "list", str(tmp_path)])

    assert code == ExitCodes.FILE_REJECTED
    assert "No KPI collection" in capsys.readouterr().err


@pytest.mark.base
def test_the_tag_filter_takes_the_tag_enum_as_well_as_its_value() -> None:
    """Catches ``tag=KpiTagEnumClass.CAR`` silently matching nothing while ``tag="Car"`` matches."""
    finder = KpiFinder(_collection(_stamped(ComponentID("Car1"), "Distance driven", "Battery losses")))

    assert finder.addresses(tag=KpiTagEnumClass.CAR) == finder.addresses(tag="Car")
    assert len(finder.addresses(tag=KpiTagEnumClass.CAR)) == 2
    assert finder.value(tag=KpiTagEnumClass.CAR, name="Battery losses") == 1.0
    assert finder.addresses(tag=KpiTagEnumClass.BATTERY) == []
    with pytest.raises(ValueError, match="No KPI matches tag='Battery'"):
        finder.one(tag=KpiTagEnumClass.BATTERY)


@pytest.mark.base
def test_an_entry_without_a_value_is_not_a_kpi_entry() -> None:
    """Catches ``value()`` failing with a bare KeyError on an entry that has a name but no value."""
    with pytest.raises(ValueError, match=r"BUI1\.General\.Rate has no 'value'"):
        KpiFinder({"BUI1": {"General": {"Rate": {"name": "Rate", "unit": "%"}}}})


@pytest.mark.base
def test_an_untagged_component_entry_is_refused_by_name_when_keyed() -> None:
    """Catches an entry without a tag being filed under the tag ``"None"`` instead of failing."""
    source = KpiSource.for_component(ComponentID("Car1"), DisplayConfig())
    untagged = KpiEntry(name="Distance driven", unit="km", value=1.0, source=source)

    with pytest.raises(
        ValueError, match="The component KPI entry 'Distance driven' of the component 'Car1' carries no tag"
    ):
        KpiPreparation.keyed_component_entries([untagged])


@pytest.mark.base
@pytest.mark.parametrize("tag_field", [{"tag": None}, {}])
def test_the_finder_refuses_an_untagged_entry_by_name(tag_field: Dict[str, Any]) -> None:
    """Catches the finder filing a collection's untagged entry under the tag ``"None"``."""
    entry = {
        "name": "Distance driven",
        "value": 1.0,
        "source": KpiSource.for_component(ComponentID("Car1"), DisplayConfig()).to_dict(),
        **tag_field,
    }

    with pytest.raises(
        ValueError,
        match="the KPI entry 'Distance driven' under 'BUI1.None.Distance driven \\(Car1\\)', reported for the "
        "component 'Car1', carries no tag",
    ):
        KpiFinder({"BUI1": {None: {"Distance driven (Car1)": entry}}})


@pytest.mark.base
def test_keying_fills_an_unset_deprecated_source_name_and_refuses_a_different_one() -> None:
    """Catches an entry with a source but no ``name_of_source_component`` being refused, or a mismatch passing."""
    source = KpiSource.for_component(ComponentID("Car1"), DisplayConfig())
    unset = KpiEntry(name="Distance driven", unit="km", value=1.0, tag=KpiTagEnumClass.CAR, source=source)

    keyed = KpiPreparation.keyed_component_entries([unset])

    assert keyed["Distance driven (Car1)"]["nameOfSourceComponent"] == "Car1"
    different = KpiEntry(
        name="Distance driven",
        unit="km",
        value=1.0,
        tag=KpiTagEnumClass.CAR,
        source=source,
        name_of_source_component="Car2",
    )
    with pytest.raises(ValueError, match="names two different sources"):
        KpiPreparation.keyed_component_entries([different])


@pytest.mark.base
def test_a_component_reporting_a_mismatched_deprecated_source_name_is_refused() -> None:
    """Catches the base class letting an entry name one component in ``source`` and another in the old field."""
    foreign = KpiSource.for_component(ComponentID("Battery"), DisplayConfig())
    component = _ReportingComponent(
        ComponentID("EMS"),
        DisplayConfig(),
        [KpiEntry(name="On behalf", unit="-", value=2.0, source=foreign, name_of_source_component="Car")],
    )

    with pytest.raises(ValueError, match="EMS: the KPI entry 'On behalf' names two different sources"):
        component.component_kpi_entries(all_outputs=[], postprocessing_results=pd.DataFrame())


@pytest.mark.base
@pytest.mark.parametrize(
    "raw, message",
    [
        ({"member": "Car1", "display_name": "Car"}, "the KPI source has no 'name'"),
        ({"imports": "pv", "name": "Car1"}, r"the KPI source carries the unknown key\(s\) 'imports'"),
        ({"name": "Car1", "member": 3}, "source.member is neither a string nor null"),
        (["Car1"], "a KPI source must be a JSON object"),
    ],
)
def test_a_serialized_source_is_decoded_strictly(raw: Any, message: str) -> None:
    """Catches a misspelt source key being dropped, or a missing name failing as a bare KeyError."""
    with pytest.raises(ValueError, match=f"the KPI entry 'Distance driven': {message}"):
        KpiSource.from_entry_dict({"name": "Distance driven", "value": 1.0, "source": raw})
    with pytest.raises(ValueError, match=f"somewhere: {message}"):
        KpiSource.from_json_object(raw, "somewhere")


#: How a source refuses a path whose first step is not its own import and instance.
_NOT_OUTERMOST = "The KPI source 'pv-east-PVSystem': import 'pv' and instance 'east' are not the outermost step"


@pytest.mark.base
@pytest.mark.parametrize(
    "path, message",
    [
        ({"import": "pv"}, "source.path is not a list of address steps"),
        (["pv"], r"source.path\[0\]: an address step must be an object with exactly the keys import, instance"),
        ([{"import": "pv"}], r"source.path\[0\]: an address step must be an object with exactly the keys"),
        (
            [{"import": "pv", "instance": None, "member": "x"}],
            r"source.path\[0\]: an address step must be an object with exactly the keys",
        ),
        ([{"import": None, "instance": None}], r"source.path\[0\]: the address step's import is not a non-empty"),
        ([{"import": "pv", "instance": 3}], r"source.path\[0\]: the address step's instance is neither a string"),
        (
            [{"import": "pv", "instance": "west"}],
            f"{_NOT_OUTERMOST} of its path \\[\\{{'import': 'pv', 'instance': 'west'",
        ),
        ([], f"{_NOT_OUTERMOST} of its path \\[\\]"),
    ],
)
def test_a_serialized_path_is_decoded_strictly(path: Any, message: str) -> None:
    """Catches a malformed path, or one whose first step is not the source's import, being read."""
    raw = {"import": "pv", "instance": "east", "path": path, "member": "PVSystem", "name": "pv-east-PVSystem"}
    with pytest.raises(ValueError, match=f"somewhere: {message}"):
        KpiSource.from_json_object(raw, "somewhere")


@pytest.mark.base
def test_a_source_without_a_path_is_a_site_component() -> None:
    """Catches an absent path read as anything but ``[]``, and a source naming an import without one."""
    assert KpiSource.from_json_object({"name": "Car1"}, "here").path == ()
    with pytest.raises(ValueError, match="import 'pv' and instance None are not the outermost step"):
        KpiSource(import_key="pv", name="pv-PVSystem")


@pytest.mark.base
def test_a_component_id_with_a_path_gives_the_source_every_step() -> None:
    """Catches ``for_component`` dropping the path an assembly-built identity carries, or its order.

    ``ComponentID`` carries no path on this branch; the stand-in has the shape the assemblies
    work gives it (``path`` of steps with ``import_key`` and ``instance``, outermost first).
    """
    from types import SimpleNamespace

    component_id = SimpleNamespace(
        name="HeatPump",
        key="heating-sys-1-hp-HeatPump",
        path=(SimpleNamespace(import_key="heating", instance="sys-1"), SimpleNamespace(import_key="hp", instance=None)),
    )

    source = KpiSource.for_component(component_id, DisplayConfig())  # type: ignore[arg-type]

    assert source.path == _steps(("heating", "sys-1"), ("hp", None))
    assert (source.import_key, source.instance) == ("heating", "sys-1")
    assert source.member == "HeatPump" and source.name == "heating-sys-1-hp-HeatPump"
    assert KpiSource.for_component(ComponentID("Car1"), DisplayConfig()).path == ()


@pytest.mark.base
def test_a_serialized_source_may_leave_out_every_field_but_its_name() -> None:
    """Catches the strict decoder demanding presentation fields a golden leaf never stores."""
    assert KpiSource.from_json_object({"name": "Car1", "member": "Car1"}, "here") == KpiSource(
        name="Car1", member="Car1"
    )


@pytest.mark.base
@pytest.mark.parametrize("floor_area, skipped", [(None, True), (140.0, False)])
def test_the_building_sizer_json_is_skipped_for_a_floor_area_without_a_value(floor_area: Any, skipped: bool) -> None:
    """Catches the sizer JSON being written, or failing, for a Building whose floor area was not computed.

    A floor area of ``None`` normalizes nothing: the building object is skipped as if it had no
    Building. A computed one goes on to read the cost KPIs, which this collection lacks, so the
    writer refuses it by name.
    """
    from types import SimpleNamespace

    from hisim.postprocessing.postprocessing_main import BUILDING_OWN_KPI_NAME, PostProcessor
    from hisim.postprocessingoptions import PostProcessingOptions

    building = KpiSource.for_component(ComponentID("Building"), DisplayConfig())
    entry = KpiEntry(
        name=BUILDING_OWN_KPI_NAME, unit="m2", value=floor_area, tag=KpiTagEnumClass.BUILDING, source=building
    )
    ppdt = SimpleNamespace(
        kpi_collection_dict=_collection([entry]),
        post_processing_options=[PostProcessingOptions.COMPUTE_KPIS],
    )

    if skipped:
        PostProcessor().write_kpis_to_json_for_building_sizer(ppdt, ["BUI1"])  # type: ignore[arg-type]
    else:
        with pytest.raises(ValueError, match="No KPI matches building='BUI1', name='Total costs for simulated period'"):
            PostProcessor().write_kpis_to_json_for_building_sizer(ppdt, ["BUI1"])  # type: ignore[arg-type]


@pytest.mark.base
@pytest.mark.parametrize("floor_area", [0.0, -12.5, float("nan"), float("inf")])
def test_the_building_sizer_json_refuses_a_floor_area_that_cannot_normalize(floor_area: float) -> None:
    """Catches a computed floor area of zero (or negative, or not finite) reaching the per-m² divisions.

    Only ``None`` means "no Building, skip"; a computed floor area that cannot divide is refused by
    name, building and KPI, before any cost KPI is read.
    """
    from types import SimpleNamespace

    from hisim.postprocessing.postprocessing_main import BUILDING_OWN_KPI_NAME, PostProcessor
    from hisim.postprocessingoptions import PostProcessingOptions

    building = KpiSource.for_component(ComponentID("Building"), DisplayConfig())
    entry = KpiEntry(
        name=BUILDING_OWN_KPI_NAME, unit="m2", value=floor_area, tag=KpiTagEnumClass.BUILDING, source=building
    )
    ppdt = SimpleNamespace(
        kpi_collection_dict=_collection([entry]),
        post_processing_options=[PostProcessingOptions.COMPUTE_KPIS],
    )

    with pytest.raises(
        ValueError,
        match=f"Building-sizer KPI JSON for BUI1: the KPI '{BUILDING_OWN_KPI_NAME}' is .* m², but every per-m² field",
    ):
        PostProcessor().write_kpis_to_json_for_building_sizer(ppdt, ["BUI1"])  # type: ignore[arg-type]
