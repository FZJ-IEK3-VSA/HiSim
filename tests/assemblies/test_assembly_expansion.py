"""Expansion of imports: parameters, presets, variants, nesting, addresses, order (``assemblies_spec.md`` §2.3–§2.7).

The expansion is a pure stage in front of the group expansion. These tests pin its four promises:
a file without imports comes back as the very same file, byte for byte; expanding twice changes
nothing; inner imports are expanded and bound before their importer offers them; and every member
comes out under its structured address in the evaluation sequence its ``order:`` paths give.
"""

from pathlib import Path
from typing import List

import pytest

from hisim.config import AddressStep, ComponentID
from hisim.energy_system.assemblies.expansion import ImportExpander, Unit, expand_imports
from hisim.energy_system.assemblies.parameters import ParameterSubstitution
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemFormatError
from hisim.energy_system.executor import EnergySystemExecutor
from hisim.energy_system.imports_model import PortState
from hisim.energy_system.loader import dump_energy_system, parse_energy_system
from hisim.energy_system.model import DefaultInputs
from hisim.simulationparameters import SimulationParameters
from tests.assemblies.helpers import (
    EMS,
    OCCUPANCY,
    WEATHER,
    Library,
    Mocks,
    build_text,
    expand_text,
    mock_resolver,
    read_system,
    site,
)


def committed_energy_systems() -> List[Path]:
    """Every committed energy-system file of the repository."""
    root = Path(__file__).resolve().parents[2] / "energy_systems"
    return sorted(root.glob("*.energy_system.yaml"))


@pytest.mark.base
def test_there_are_committed_energy_systems_to_check() -> None:
    """The byte-identity test below runs over a real set, not an empty one."""
    assert len(committed_energy_systems()) > 20


@pytest.mark.base
@pytest.mark.parametrize("path", committed_energy_systems(), ids=lambda path: path.name)
def test_a_file_without_imports_expands_to_itself_byte_for_byte(path: Path) -> None:
    """Every committed file: its dump is unchanged, the record is empty, and the very object comes back.

    The dump is compared with that of a second, independent read of the file; the identity is the
    documented guarantee of :func:`expand_imports` for a file without imports.
    """
    model = parse_energy_system(path)

    expanded, record = expand_imports(model, mock_resolver())

    assert dump_energy_system(expanded) == dump_energy_system(parse_energy_system(path))
    assert record.is_empty
    assert expanded is model


@pytest.mark.base
def test_expanding_an_expanded_file_changes_nothing() -> None:
    """Idempotency: the flat file has no imports left and expands to itself."""
    model = parse_energy_system(Mocks.HOUSE)
    once, _record = expand_imports(model, mock_resolver())

    twice, record = expand_imports(once, mock_resolver())

    assert twice == once
    assert record.is_empty
    assert dump_energy_system(twice) == dump_energy_system(once)


@pytest.mark.base
def test_the_house_expands_to_flat_named_members_in_the_declared_sequence() -> None:
    """Addresses, the sequence of ``order:`` paths, schema version 3 and no import left."""
    expanded, record = expand_imports(parse_energy_system(Mocks.HOUSE), mock_resolver())

    assert expanded.schema_version == 3
    assert expanded.imports == {}
    assert list(expanded.components) == [
        "Weather",
        "Occupancy",
        "pv-east-PVSystem",
        "pv-west-PVSystem",
        "hot_water-heater-Controller",
        "hot_water-heater-Heater",
        "hot_water-heater-tank-Tank",
        "hot_water-Panel",
        "backup-Heater",
        "Ems",
    ]
    assert record.sequence[4:7] == [
        ("hot_water-heater-Controller", (4, 1, 1)),
        ("hot_water-heater-Heater", (4, 1, 2)),
        ("hot_water-heater-tank-Tank", (4, 1, 3, 1)),
    ]
    assert expanded.addresses["hot_water-heater-tank-Tank"] == ComponentID(
        "Tank",
        path=(AddressStep("hot_water"), AddressStep("heater"), AddressStep("tank")),
        assembly="mock/hot_water_tank",
    )
    assert expanded.addresses["pv-west-PVSystem"].path == (AddressStep("pv", "west"),)


@pytest.mark.base
def test_inner_imports_are_expanded_and_bound_before_their_importer() -> None:
    """Innermost first: the tank's heat need is bound inside its assembly before any top-level port."""
    _expanded, record = expand_imports(parse_energy_system(Mocks.HOUSE), mock_resolver())

    paths = [instance.path for instance in record.instances]
    assert paths.index("hot_water → heater → tank") < paths.index("hot_water → heater") < paths.index("hot_water")
    inner = record.decisions.index("hot_water → heater → tank.heat -> hot_water-heater-Heater (bind)")
    outer = record.decisions.index("hot_water.hot_water_demand -> Occupancy (default)")
    assert inner < outer


@pytest.mark.base
def test_the_simulator_adds_the_components_in_the_record_sequence(tmp_path: Path) -> None:
    """Registration follows the import record's sequence, which ``order:`` decides."""
    parameters = SimulationParameters.one_day_only(2021, 900)
    parameters.result_directory = str(tmp_path)

    built = EnergySystemExecutor(
        parse_energy_system(Mocks.HOUSE), parameters, assembly_resolver=mock_resolver()
    ).build()

    registered = [wrapper.my_component.component_name for wrapper in built.simulator.wrapped_components]
    assert tuple(registered) == built.imports.sequence_names
    assert registered[:3] == ["Weather", "Occupancy", "pv-east-PVSystem"]


@pytest.mark.base
def test_without_order_the_site_comes_first_then_the_imports_in_written_order() -> None:
    """Today's sequence: the site's components in written order, then each import."""
    expanded, record = expand_text(
        site(WEATHER, OCCUPANCY)
        + """
imports:
  pv: {assembly: mock/pv_array}
  tank: {assembly: mock/hot_water_tank, bind: {heat: backup}}
  backup: {assembly: mock/electric_heater, parameters: {with_thermostat: false}}
"""
    )

    assert list(expanded.components) == ["Weather", "Occupancy", "pv-PVSystem", "tank-Tank", "backup-Heater"]
    assert [path for _name, path in record.sequence] == [(1,), (2,), (3, 1), (4, 1), (5, 2)]


@pytest.mark.base
@pytest.mark.parametrize(
    "orders, message",
    [
        (("order: 1", "order: 1"), "both declare order: 1"),
        (("order: 1", ""), "numbers all its entries or none"),
    ],
)
def test_a_repeated_or_partial_numbering_is_refused(orders: tuple, message: str) -> None:
    """D23: a number once per level, and a level numbers all its entries or none."""
    weather = WEATHER.replace("Weather:\n", f"Weather:\n  {orders[0]}\n")
    occupancy = OCCUPANCY.replace("Occupancy:\n", f"Occupancy:\n  {orders[1]}\n") if orders[1] else OCCUPANCY
    with pytest.raises(EnergySystemAssemblyError, match=f"EF-7K .*{message}"):
        expand_text(site(weather, occupancy) + "imports:\n  pv: {order: 3, assembly: mock/pv_array}\n")


@pytest.mark.base
def test_order_with_components_in_a_group_is_refused() -> None:
    """``order:`` positions the top level only; a grouped component could not be placed."""
    text = (
        site(WEATHER.replace("Weather:\n", "Weather:\n  order: 1\n"))
        + "imports:\n  pv: {order: 2, assembly: mock/pv_array}\n"
        + "groups:\n  extra:\n    enabled: true\n    components:\n"
        + "      Occupancy: {class: tests.assemblies.mock_components.MockOccupancy, preset: standard}\n"
    )
    with pytest.raises(EnergySystemAssemblyError, match="EF-7K .*groups or variants hold components"):
        expand_text(text)


@pytest.mark.base
def test_parameters_presets_and_substitution() -> None:
    """Defaults, a preset, a given value over the preset, each written into the member's config."""
    expanded, record = expand_text(
        site(WEATHER, OCCUPANCY)
        + """
imports:
  dhw: {assembly: mock/storage_water_heater, preset: large, parameters: {volume_in_liter: 250}, none: [ems_modifier]}
"""
    )

    assert expanded.components["dhw-tank-Tank"].config == {"volume_in_liter": 250}
    assert expanded.components["dhw-Heater"].config == {"power_in_watt": 3000}
    instance = record.instance("dhw")
    assert instance is not None
    assert instance.preset == "large"
    assert instance.parameters_given == {"volume_in_liter": 250}
    assert instance.parameters_resolved == {"volume_in_liter": 250, "heater_power_in_watt": 3000}
    port = instance.port("ems_modifier")
    assert port is not None and port.state == PortState.DECLINED


@pytest.mark.base
def test_a_variant_selects_its_members_and_drops_the_references_to_the_others() -> None:
    """Without a thermostat the controller is gone, and so is the heater's input from it."""
    expanded, record = expand_text(
        site(WEATHER)
        + "imports:\n  heater: {assembly: mock/electric_heater, parameters: {with_thermostat: false}}\n"
    )

    assert list(expanded.components) == ["Weather", "heater-Heater"]
    assert expanded.components["heater-Heater"].inputs == ()
    instance = record.instance("heater")
    assert instance is not None and instance.variants == {"thermostat": "none"}
    assert {port.port: port.state for port in instance.ports}["tank_temperature"] == PortState.INACTIVE


@pytest.mark.base
def test_a_preset_named_by_a_parameter_and_a_display_name() -> None:
    """``preset: {$param: control}`` and the ``display:`` template both follow the parameters."""
    expanded, record = expand_text(
        site(WEATHER, OCCUPANCY, EMS)
        + """
imports:
  tank: {assembly: mock/hot_water_tank, bind: {heat: heater}}
  heater: {assembly: mock/electric_heater, preset: eco, bind: {ems_modifier: Ems}}
  pv: {assembly: mock/pv_array, preset: east_facing}
"""
    )

    assert expanded.components["heater-Controller"].preset == "eco"
    assert expanded.components["heater-Controller"].config == {"set_temperature_in_celsius": 40}
    pv = record.instance("pv")
    assert pv is not None and pv.members["pv-PVSystem"]["display_name"] == "PV array, east, azimuth 90"


@pytest.mark.base
@pytest.mark.parametrize(
    "parameters, message",
    [
        ("{nope: 1}", "'nope', which is not a parameter of 'mock/pv_array'"),
        ("{azimuth_in_degree: north}", "'north' is not a number"),
        ("{azimuth_in_degree: 400}", "outside the range \\[0.0, 360.0\\]"),
        ("{facing: up}", "'up' is not one of the allowed values"),
        (
            "{power_in_watt: 3000, share_of_roof: 0.5}",
            "EF-77 .*exactly one of power_in_watt, share_of_roof is stated, but 2 are",
        ),
    ],
)
def test_a_parameter_that_does_not_fit_is_refused(parameters: str, message: str) -> None:
    """Type, range, allowed values, unknown names and constraints are checked per import."""
    with pytest.raises(EnergySystemAssemblyError, match=message):
        expand_text(site(WEATHER) + f"imports:\n  pv: {{assembly: mock/pv_array, parameters: {parameters}}}\n")


@pytest.mark.base
def test_an_unknown_preset_and_a_parameter_reference_at_the_top_are_refused() -> None:
    """An energy-system file has presets to name, but no parameters to refer to."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-76 .*no preset 'north'.*Valid presets: east_facing, south"):
        expand_text(site(WEATHER) + "imports:\n  pv: {assembly: mock/pv_array, preset: north}\n")
    with pytest.raises(EnergySystemAssemblyError, match="EF-76 .*has no parameters"):
        expand_text(
            site(WEATHER) + "imports:\n  pv: {assembly: mock/pv_array, parameters: {tilt_in_degree: {$param: x}}}\n"
        )


UNIT_ASSEMBLY = """
schema_version: 4
kind: assembly
name: broken/{name}
parameters:
  rating: {{type: float, unit: {unit}, default: 1, range: {{min: 0, max: 2}}, description: A rating.}}
components:
  PVSystem:
    class: tests.assemblies.mock_components.MockPVSystem
    preset: rooftop
    config: {{{field}: {{$param: rating}}}}
tests:
  bounds: []
  monotone:
    - {{parameter: rating, kpi: PV production, member: PVSystem, direction: increasing}}
"""


@pytest.mark.base
@pytest.mark.parametrize(
    "name, unit, field, message",
    [
        (
            "kilowatt",
            "KILOWATT",
            "power_in_watt",
            "EF-75 at broken/kilowatt.assembly.yaml: the assembly 'broken/kilowatt' has 1 problem: "
            "broken/kilowatt.assembly.yaml:10: EF-78: the parameter 'rating' is in KILOWATT, but the field "
            "MockPVSystemConfig.power_in_watt it feeds \\(member 'PVSystem'\\) is in WATT",
        ),
        (
            "unitless",
            "ANY",
            "shading_factor",
            "EF-75 at broken/unitless.assembly.yaml: .* 1 problem: broken/unitless.assembly.yaml:10: EF-79: the "
            "parameter 'rating' feeds the field 'shading_factor' of MockPVSystemConfig \\(member 'PVSystem'\\), "
            "which declares no unit; declare it on the field: sized_field\\(..., unit=lt.Units",
        ),
    ],
)
def test_a_parameter_unit_is_checked_against_the_field_it_feeds(
    tmp_path: Path, name: str, unit: str, field: str, message: str
) -> None:
    """D16 (b): a mismatch is a load error, never a conversion; a fed field without a unit is refused.

    The units are the library check's: the expansion refuses the assembly with the list the library
    test prints, each unit problem under its own identifier.
    """
    library = Library(tmp_path)
    library.add(f"broken/{name}", UNIT_ASSEMBLY.format(name=name, unit=unit, field=field))

    with pytest.raises(EnergySystemAssemblyError, match=message):
        expand_text(site(WEATHER) + f"imports:\n  x: {{assembly: broken/{name}}}\n", library.resolver())


@pytest.mark.base
def test_a_sized_field_declares_its_unit() -> None:
    """``sized_field(unit=...)`` records the unit under the key a plain field uses too."""
    import dataclasses  # noqa: PLC0415

    from hisim import loadtypes as lt  # noqa: PLC0415
    from hisim.config.sizing import declared_field_unit, sized_field  # noqa: PLC0415
    from tests.assemblies.mock_components import MockTankConfig  # noqa: PLC0415

    @dataclasses.dataclass
    class Sized:
        """A configuration with one sized field that declares its unit."""

        component_id: ComponentID
        volume: float = sized_field(rule=1.0, unit=lt.Units.LITER)

    assert declared_field_unit(Sized, "volume") is lt.Units.LITER
    assert declared_field_unit(MockTankConfig, "volume_in_liter") is lt.Units.LITER
    assert declared_field_unit(MockTankConfig, "component_id") is None


CYCLE = """
schema_version: 4
kind: assembly
name: cycle/{name}
imports:
  inner: {{assembly: cycle/{other}}}
"""


@pytest.mark.base
def test_a_cycle_of_imports_is_refused(tmp_path: Path) -> None:
    """An assembly importing itself through another is refused, naming the chain."""
    library = Library(tmp_path)
    library.add("cycle/a", CYCLE.format(name="a", other="b"), contract=True)
    library.add("cycle/b", CYCLE.format(name="b", other="a"), contract=True)

    with pytest.raises(EnergySystemAssemblyError, match="EF-73 .*cycle/a → cycle/b → cycle/a"):
        expand_text(site(WEATHER) + "imports:\n  x: {assembly: cycle/a}\n", library.resolver())


@pytest.mark.base
def test_nesting_deeper_than_four_is_refused(tmp_path: Path) -> None:
    """D7: a depth beyond four is a safeguard refusal."""
    library = Library(tmp_path)
    for level in range(1, 6):
        library.add(
            f"deep/l{level}",
            CYCLE.format(name=f"l{level}", other=f"l{level + 1}").replace("cycle/", "deep/"),
            contract=True,
        )

    with pytest.raises(EnergySystemAssemblyError, match="EF-74 .*nest 5 deep"):
        expand_text(site(WEATHER) + "imports:\n  x: {assembly: deep/l1}\n", library.resolver())


@pytest.mark.base
def test_a_construct_a_later_step_lowers_is_refused_and_listed(tmp_path: Path) -> None:
    """Selectors, controllable outputs, many-reads, fact exports and ``$fact`` are recorded and refused (EF-7L)."""
    library = Library(tmp_path)
    library.add(
        "later/everything",
        """
        schema_version: 4
        kind: assembly
        name: later/everything
        components:
          Tank:
            class: tests.assemblies.mock_components.MockTank
            preset: standard
            config: {volume_in_liter: {$fact: storage_volume}}
          Battery:
            class: tests.assemblies.mock_components.MockBattery
            preset: sized_to_pv
        interface:
          needs:
            pv_power: {fact: pv_peak_power_in_watt, many: true, into: [Battery]}
          provides:
            loss: {output: Tank.HeatLoss, controllable: {via: x}}
            capacity: {fact: pv_peak_power_in_watt, export: true}
        """,
        contract=True,
    )

    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(
            site(WEATHER) + "imports:\n  x: {assembly: later/everything, observes: [{output: Y}]}\n", library.resolver()
        )

    message = str(raised.value)
    assert "EF-7L" in message
    for construct in (
        "import x: observes — not lowered yet, delivered by hisim-lt0b.3",
        "x: port pv_power (fact, many: true) — not lowered yet, delivered by hisim-lt0b.4",
        "x: port capacity (fact export) — not lowered yet, delivered by hisim-lt0b.4",
        "x: port loss (controllable)",
        "x: member Tank config.volume_in_liter ($fact) — not lowered yet, delivered by no step: the sizing engine "
        "reads a fact only through a law its class declares on the field",
    ):
        assert construct in message, construct


@pytest.mark.base
def test_an_import_key_may_not_share_a_name_with_the_site() -> None:
    """A verb names a partner by a bare name, so the two namespaces stay apart."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-52 .*the import 'Weather'"):
        expand_text(site(WEATHER) + "imports:\n  Weather: {assembly: mock/pv_array}\n")


@pytest.mark.base
def test_the_expanded_house_items_name_the_serialized_partners() -> None:
    """Internal references are rewritten; lowered ports write bare partner names."""
    expanded, _record = expand_imports(parse_energy_system(Mocks.HOUSE), mock_resolver())

    assert expanded.components["hot_water-heater-Heater"].inputs == (
        DefaultInputs(source="hot_water-heater-Controller"),
    )
    assert expanded.components["hot_water-heater-tank-Tank"].inputs == (
        DefaultInputs(source="Occupancy"),
        DefaultInputs(source="hot_water-heater-Heater"),
    )
    assert expanded.components["hot_water-heater-Controller"].inputs == (
        DefaultInputs(source="hot_water-heater-tank-Tank"),
        DefaultInputs(source="Ems"),
    )


@pytest.mark.base
def test_two_members_whose_addresses_serialize_alike_are_refused() -> None:
    """EF-52: the serialization drops the step boundaries, so the expansion refuses a collision by name.

    ``pv`` importing ``east`` and ``pv`` with the instance ``east`` both serialize to ``pv-east-…``.
    One file cannot hold both (an import has instances or none), so the two units are built directly.
    """
    nested = ComponentID(name="PVSystem", path=(AddressStep("pv"), AddressStep("east")))
    instance = ComponentID(name="PVSystem", path=(AddressStep("pv", "east"),))
    assert nested != instance and nested.address == instance.address == "pv-east-PVSystem"
    model, lines = read_system(site(WEATHER))
    expander = ImportExpander(model, mock_resolver(), lines)
    units = [
        Unit(
            name=identity.address,
            entry=model.components["Weather"],
            identity=identity,
            chain=(),
            import_path=ImportExpander.path_text(identity.path),
            local_names={},
            dropped_names=frozenset(),
            order_path=(),
            lines=lines,
            block_path=(),
        )
        for identity in (nested, instance)
    ]

    for first, second in (units, units[::-1]):
        with pytest.raises(EnergySystemAssemblyError) as raised:
            expander.check_unique_names([first, second])
        message = str(raised.value)
        assert message.startswith("EF-52 at components.pv-east-PVSystem: two components serialize to the name ")
        assert "the member 'PVSystem' of import pv → east" in message
        assert "the member 'PVSystem' of import pv[east]" in message


@pytest.mark.base
def test_a_file_with_imports_and_a_group_builds(tmp_path: Path) -> None:
    """Without ``order:`` the imports' members come first in the sequence, then the group's components."""
    text = (
        site(WEATHER)
        + "imports:\n  pv: {assembly: mock/pv_array}\n"
        + "groups:\n  extra:\n    enabled: true\n    components:\n"
        + "      Occupancy: {class: tests.assemblies.mock_components.MockOccupancy, preset: standard}\n"
    )

    built = build_text(text, tmp_path)

    assert built.imports.sequence_names == ("Weather", "pv-PVSystem", "Occupancy")
    assert [name for name, _component in built.wired.components] == ["Weather", "pv-PVSystem", "Occupancy"]
    assert built.model.components["pv-PVSystem"].inputs == (DefaultInputs(source="Weather"),)


@pytest.mark.base
def test_a_version_3_file_with_imports_is_refused() -> None:
    """EF-01: importing is what version 4 adds; a version-3 file naming imports is read for nothing else."""
    with pytest.raises(EnergySystemFormatError, match="EF-01 at inline.energy_system.yaml.imports: the document "
                       "imports assemblies but declares schema_version 3. .*Valid schema versions that import: 4."):
        read_system(
            site(WEATHER).replace("schema_version: 4", "schema_version: 3") + "imports:\n  pv: {assembly: x/y}\n"
        )


@pytest.mark.base
def test_the_substitution_keeps_the_type_of_a_sequence() -> None:
    """A tuple node comes back a tuple and a list a list, references substituted in both."""
    substitution = ParameterSubstitution({"x": 5})

    substituted = substitution.apply({"pair": ({"a": 1}, {"$param": "x"}), "list": [{"$param": "x"}]})

    assert substituted == {"pair": ({"a": 1}, 5), "list": [5]}
    assert isinstance(substituted["pair"], tuple) and isinstance(substituted["list"], list)


@pytest.mark.base
def test_a_display_template_that_does_not_render_the_resolved_values_is_refused(tmp_path: Path) -> None:
    """EF-76: the spec fits a float, the resolved value is none."""
    library = Library(tmp_path)
    library.add(
        "mock/displayed",
        (Mocks.LIBRARY / "mock" / "pv_array.assembly.yaml")
        .read_text(encoding="utf-8")
        .replace("name: mock/pv_array", "name: mock/displayed")
        .replace('display: "PV array, {facing}, azimuth {azimuth_in_degree}"', 'display: "Share {share_of_roof:.2f}"'),
    )

    with pytest.raises(EnergySystemAssemblyError, match=r"EF-76 at mock/displayed.assembly.yaml: components.PVSystem."
                       r"display: the display template 'Share \{share_of_roof:.2f\}' of 'PVSystem' does not render "
                       r"with share_of_roof=None"):
        expand_text(site(WEATHER) + "imports:\n  pv: {assembly: mock/displayed}\n", library.resolver())
