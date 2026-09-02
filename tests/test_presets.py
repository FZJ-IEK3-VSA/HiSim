"""Tests of the typed preset machinery and the config introspection surface.

Covers ``hisim/config/presets.py`` — the ``@preset`` and ``@constructor`` decorators,
their registries and the preset-provenance stamp — and ``hisim/config/introspection.py``,
the ``describe_config`` description a schema exporter, a command line or a sweep reads
instead of the class internals. Also pins the two structural promises the design makes: a
preset name typo and a wrongly typed constructor argument are *static* type errors
(checked by running mypy over a snippet), and a config class living entirely outside
``hisim/config`` resolves through the kernel without the kernel knowing anything about it.
"""

# clean

import ast
import dataclasses
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import cast

import pytest
from dataclasses_json import dataclass_json

from hisim.config import (
    AUTO,
    ComponentID,
    ConfigBase,
    FactContribution,
    Self,
    Sizable,
    SizableFieldKind,
    Size,
    SizingContext,
    canonical_preset,
    constructor,
    constructors_of,
    describe_config,
    preset,
    preset_provenance,
    presets_of,
    resolve_all,
    sized_field,
)


@dataclass_json
@dataclass
class _StorageConfig(ConfigBase):
    """A test-only config exercising every declaration a converted class makes.

    It has a plain field, a sizable field derived from a scenario fact, a sizable field
    reading a sibling of its own config, an author note, a preset namespace and a fact
    contribution — the full vocabulary, in a module that is not part of ``hisim.config``
    and not part of ``hisim.components`` either.
    """

    component_id: ComponentID
    manufacturer: str = "generic"
    volume_in_liter: Sizable[float] = sized_field(
        rule=0.01 * Size.HEATING_LOAD_IN_WATT, note="VDI 4645 rule of thumb"
    )
    reserve_in_liter: Sizable[float] = sized_field(rule=Self("volume_in_liter") * 0.1)
    heat_loss_in_watt: Sizable[float] = sized_field(rule=25.0)

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_presets._StorageConfig"

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "_StorageConfig":
        """The canonical sizable template, leaving every sizable field to the resolver."""
        return cls(component_id=ComponentID(name=name))

    @preset(note="catalogue tank")
    @classmethod
    def preset_fixed_300l(cls, name: str) -> "_StorageConfig":
        """A catalogue tank pinning the volume, so the pinned/AUTO split has both cases."""
        return cls(component_id=ComponentID(name=name), volume_in_liter=300.0)

    @constructor
    @classmethod
    def for_volume(cls, name: str, volume_in_liter: float, manufacturer: str = "generic") -> "_StorageConfig":
        """Builds a tank of any volume, standing in for a real lookup-parameterised class."""
        return cls(component_id=ComponentID(name=name), volume_in_liter=volume_in_liter, manufacturer=manufacturer)


_StorageConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(
        facts=("water_mass_flow_rate_in_kg_per_second",),
        compute=lambda config, ctx: {"water_mass_flow_rate_in_kg_per_second": config.volume_in_liter / 1000.0},
    ),
)


@dataclass_json
@dataclass
class _UndecoratedConfig(ConfigBase):
    """A test-only config with a plain factory classmethod and no decorated builder.

    It exists to prove the negative half of the discovery contract: a classmethod that
    looks exactly like a preset — same signature, same return type, a name a heuristic
    would happily match — stays invisible to the registries because nothing decorated it.
    """

    component_id: ComponentID
    manufacturer: str = "generic"

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_presets._UndecoratedConfig"

    @classmethod
    def looks_like_a_preset(cls, name: str) -> "_UndecoratedConfig":
        """An ordinary factory classmethod, deliberately not decorated."""
        return cls(component_id=ComponentID(name=name))


class MypyProbe:
    """The snippet, invocation and expectations of the in-test static-typing check.

    The whole point of declaring presets and constructors as decorated classmethods is that
    a misspelled name or a wrongly typed argument is caught by the type checker rather than
    at run time, which no run-time assertion can demonstrate — so the check runs mypy over a
    small generated module and inspects its diagnostics. Everything the check needs is
    grouped here so the snippet, the error codes it must produce and the skip condition are
    read in one place.
    """

    #: The snippet handed to mypy: one correct preset call, one misspelled preset name and
    #: one constructor call with a wrongly typed argument. Written to a temporary file so
    #: mypy sees a real module.
    SNIPPET: str = '''
"""Generated snippet: the builder typing contract, checked by mypy in a test."""

from dataclasses import dataclass

from hisim.config import ComponentID, constructor, preset


@dataclass
class ProbeConfig:
    """A minimal config class with a preset and a constructor, standing in for a real one."""

    component_id: ComponentID
    fuel: str = "gas"
    volume_in_liter: float = 100.0

    @preset
    @classmethod
    def preset_condensing_gas(cls, name: str) -> "ProbeConfig":
        """The one preset, whose name the snippet spells right once and wrong once."""
        return cls(component_id=ComponentID(name=name))

    @constructor
    @classmethod
    def for_volume(cls, name: str, volume_in_liter: float) -> "ProbeConfig":
        """The one constructor, called once with a wrongly typed argument."""
        return cls(component_id=ComponentID(name=name), volume_in_liter=volume_in_liter)


def use() -> None:
    """Uses the builders correctly, then in the two ways that must not type-check."""
    good: ProbeConfig = ProbeConfig.preset_condensing_gas("boiler_1")
    bad = ProbeConfig.preset_condensing_gaz("boiler_2")
    wrong_argument = ProbeConfig.for_volume("tank", volume_in_liter="300 litres")
    print(good, bad, wrong_argument)
'''

    #: Error codes mypy must report for the snippet: the misspelled preset name and the
    #: wrongly typed constructor argument.
    EXPECTED_CODES: tuple = ("attr-defined", "arg-type")

    @staticmethod
    def run(snippet_path: pathlib.Path) -> str:
        """Runs mypy over the snippet with the repository configuration and returns stdout."""
        repository_root = pathlib.Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-m", "mypy", "--config-file", str(repository_root / "mypy.ini"), str(snippet_path)],
            capture_output=True,
            text=True,
            cwd=str(repository_root),
            check=False,
        )
        return completed.stdout + completed.stderr


@pytest.mark.base
def test_a_preset_builds_only_with_an_instance_name_and_stamps_its_provenance():
    """A preset is a builder taking the instance name, and stamps the name it was declared as.

    Failure mode caught: a preset that carries a hard-coded ``component_id`` again (two
    instances of the same class would then collide), or a build that forgets the provenance
    stamp so a template creator can no longer tell which preset a config came from.
    """
    first = _StorageConfig.preset_standard("TankA")
    second = _StorageConfig.preset_standard("TankB")
    assert first is not second
    assert first.component_id.name == "TankA"
    assert second.component_id.name == "TankB"
    assert preset_provenance(first) == "standard"
    assert preset_provenance(_StorageConfig.preset_fixed_300l("TankC")) == "fixed_300l"
    assert preset_provenance(_StorageConfig(component_id=ComponentID(name="Manual"))) is None
    with pytest.raises(TypeError):
        _StorageConfig.preset_standard()  # type: ignore[call-arg]  # pylint: disable=no-value-for-parameter


@pytest.mark.base
def test_a_preset_builder_varies_in_nothing_but_the_instance_name():
    """Two builds of one preset differ only in their component_id, never in a field value.

    Failure mode caught: a builder that reads the clock, a global or the environment, which
    would make a scenario's ``"preset": "<name>"`` reference mean different things in two
    runs.
    """
    first = _StorageConfig.preset_fixed_300l("TankA")
    second = _StorageConfig.preset_fixed_300l("TankB")
    assert dataclasses.replace(first, component_id=second.component_id) == second


@pytest.mark.base
def test_the_preset_registry_lists_the_presets_in_declaration_order():
    """``presets_of`` keys, the canonical entry and the notes all follow declaration order.

    Failure mode caught: a registry whose canonical default silently becomes another preset
    (every setup that takes the canonical would change behaviour), or an enumeration that a
    contract test and a GUI palette would see in different orders.
    """
    presets = presets_of(_StorageConfig)
    assert list(presets) == ["standard", "fixed_300l"]
    assert [entry.method_name for entry in presets.values()] == ["preset_standard", "preset_fixed_300l"]
    assert {name: entry.note for name, entry in presets.items()} == {"standard": None, "fixed_300l": "catalogue tank"}
    canonical = canonical_preset(_StorageConfig)
    assert canonical is not None and canonical.name == "standard"
    assert preset_provenance(canonical.build("Tank")) == "standard"
    assert canonical_preset(_UndecoratedConfig) is None


@pytest.mark.base
def test_the_constructor_registry_is_separate_and_callable_with_keywords():
    """A constructor is discoverable, callable through the registry and stamps no provenance.

    Failure mode caught: a constructor discovered as a preset — its wire name would become
    file format and a probe would call it without the arguments it needs — or a constructor
    that stamps a provenance it cannot honour, since what identifies its result is the
    arguments it was called with, not a name.
    """
    constructors = constructors_of(_StorageConfig)
    assert list(constructors) == ["for_volume"]
    assert "for_volume" not in presets_of(_StorageConfig)
    built = constructors["for_volume"].build("TankD", volume_in_liter=450.0, manufacturer="acme")
    assert built.component_id.name == "TankD"
    assert built.volume_in_liter == 450.0
    assert built.manufacturer == "acme"
    assert preset_provenance(built) is None
    assert preset_provenance(_StorageConfig.for_volume("TankE", volume_in_liter=1.0)) is None


@pytest.mark.base
def test_undecorated_classmethods_are_invisible_to_the_registries():
    """Only decorated classmethods are builders; a plain helper is not discovered.

    Failure mode caught: a discovery that matches on the method name or on "returns the
    class", which would drag every helper classmethod of every config into the wire format.
    """
    assert not presets_of(_UndecoratedConfig)
    assert not constructors_of(_UndecoratedConfig)
    assert _UndecoratedConfig.looks_like_a_preset("Tank").component_id.name == "Tank"
    assert preset_provenance(_UndecoratedConfig.looks_like_a_preset("Tank")) is None


@pytest.mark.base
def test_a_builder_name_colliding_with_a_field_is_rejected_at_class_creation():
    """A preset whose wire name is also a field name fails while the class is being created.

    Failure mode caught: ``@dataclass`` silently adopting the classmethod as that field's
    default value, so the field of every instance holds a bound method — a corruption that
    surfaces far away from the class that caused it. The check runs in
    ``ConfigBase.__init_subclass__``, before ``@dataclass`` sees the class.
    """
    with pytest.raises(ValueError, match="collides with field 'standard'"):

        @dataclass
        class _FieldClash(ConfigBase):
            """A config whose field name is exactly the wire name of its preset."""

            component_id: ComponentID
            standard: str = "a field, not a preset"

            @preset
            @classmethod
            def preset_standard(cls, name: str) -> "_FieldClash":
                """The preset whose wire name collides with the field above."""
                return cls(component_id=ComponentID(name=name))


@pytest.mark.base
def test_a_builder_without_its_name_prefix_or_with_a_wrong_signature_is_rejected():
    """The method-name prefixes and the ``name``-first signature are enforced at declaration.

    Failure mode caught: a call site that no longer says what it is doing
    (``Config.oil("B")`` instead of ``Config.preset_oil("B")``), a preset that quietly takes
    parameters and is therefore no default at all, and a decorator applied below
    ``@classmethod``, which would leave an unbound function behind.
    """
    with pytest.raises(ValueError, match="preset_<wire_name>"):

        class _NoPresetPrefix(ConfigBase):
            """A preset declared without the mandatory ``preset_`` method-name prefix."""

            @preset
            @classmethod
            def standard(cls, name: str) -> "_NoPresetPrefix":
                """A preset whose method name lacks the prefix."""
                return cls(component_id=ComponentID(name=name))

    with pytest.raises(ValueError, match="for_<...>|from_<...>"):

        class _NoConstructorPrefix(ConfigBase):
            """A constructor declared without the mandatory ``for_``/``from_`` prefix."""

            @constructor
            @classmethod
            def make_it(cls, name: str, volume_in_liter: float) -> "_NoConstructorPrefix":
                """A constructor whose method name lacks the prefix."""
                del volume_in_liter  # the declaration, not the value, is under test
                return cls(component_id=ComponentID(name=name))

    with pytest.raises(ValueError, match="declare it as a @constructor"):

        class _ParameterisedPreset(ConfigBase):
            """A preset taking more than the instance name, which makes it a constructor."""

            @preset
            @classmethod
            def preset_sized(cls, name: str, volume_in_liter: float) -> "_ParameterisedPreset":
                """A preset with a parameter it has no business taking."""
                del volume_in_liter  # the declaration, not the value, is under test
                return cls(component_id=ComponentID(name=name))

    with pytest.raises(TypeError, match="applied above @classmethod"):

        class _NotAClassmethod(ConfigBase):
            """A preset decorator applied to a plain function instead of a classmethod."""

            @preset
            def preset_standard(self, name: str) -> "_NotAClassmethod":
                """A preset that is not a classmethod at all."""
                return _StorageConfig(component_id=ComponentID(name=name))  # type: ignore[return-value]


@pytest.mark.base
def test_a_misspelled_preset_and_a_wrongly_typed_constructor_argument_are_static_errors():
    """Mypy rejects an unknown preset method and a constructor argument of the wrong type.

    Failure mode caught: the typed-builder promise quietly regressing to ``Any`` — a
    decorator returning ``Callable[..., Any]`` or a ``__getattr__``-based namespace would
    let both mistakes through every check in this repository and only fail in a simulation
    run.
    """
    if shutil.which("mypy") is None and not (pathlib.Path(sys.prefix) / "bin" / "mypy").exists():
        pytest.skip("mypy is not installed in this environment")
    scratch = pathlib.Path(__file__).resolve().parent / "_preset_typing_probe.py"
    scratch.write_text(MypyProbe.SNIPPET, encoding="utf-8")
    try:
        output = MypyProbe.run(scratch)
    finally:
        scratch.unlink(missing_ok=True)
    if "No module named mypy" in output:
        pytest.skip("mypy is not installed in this environment")
    for code in MypyProbe.EXPECTED_CODES:
        assert f"[{code}]" in output, output
    assert "condensing_gaz" in output, output
    assert "volume_in_liter" in output, output


@pytest.mark.base
def test_describe_config_reports_fields_presets_laws_and_facts_of_the_pilots():
    """Every pilot class describes its fields, presets, sizable fields and provided facts.

    Failure mode caught: an introspection surface that cannot answer what a schema exporter
    or a ``describe`` command needs — most importantly which sizable fields a preset pins
    versus leaves to the sizing, and which facts a class contributes to its siblings.
    """
    from hisim.components.generic_boiler import GenericBoilerConfig

    description = describe_config(GenericBoilerConfig)
    assert description.config_class_name == "GenericBoilerConfig"
    field_names = {field.name for field in description.fields}
    assert {"component_id", "energy_carrier", "eff_th_min"} <= field_names
    assert {field.name for field in description.fields if field.sizable} == {
        "minimal_thermal_power_in_watt",
        "maximal_thermal_power_in_watt",
    }
    presets = {preset.name: preset for preset in description.presets}
    assert presets["condensing_gas"].canonical is True
    assert presets["condensing_gas"].auto == ("minimal_thermal_power_in_watt", "maximal_thermal_power_in_watt")
    assert presets["condensing_gas"].pinned == ()
    assert presets["condensing_gas_12kw"].auto == ()
    assert presets["condensing_gas_12kw"].note == "nominal catalogue device"
    # the pellet preset overrides one field's law, which is still "to be sized", not pinned
    assert presets["pellets"].auto == ("minimal_thermal_power_in_watt", "maximal_thermal_power_in_watt")
    assert description.facts_provided == ("maximal_thermal_power_in_watt", "minimal_thermal_power_in_watt")
    maximal = next(f for f in description.sizable_fields if f.name == "maximal_thermal_power_in_watt")
    assert maximal.kind is SizableFieldKind.LAW
    assert maximal.facts_read == (("heating_load_in_watt", "ONE"), ("number_of_apartments", "ONE"))


@pytest.mark.base
def test_describe_config_marks_a_fact_free_law_as_an_author_constant():
    """A law reading no fact and no sibling is reported as a constant, not as a derivation.

    Failure mode caught: a description telling its reader that a hard-coded author default
    ("the usual choice") was derived from the surrounding system, which is exactly the
    misunderstanding the two kinds exist to prevent.
    """
    description = describe_config(_StorageConfig)
    kinds = {field.name: field.kind for field in description.sizable_fields}
    assert kinds["heat_loss_in_watt"] is SizableFieldKind.CONSTANT
    assert kinds["volume_in_liter"] is SizableFieldKind.LAW
    assert kinds["reserve_in_liter"] is SizableFieldKind.LAW
    volume = next(f for f in description.sizable_fields if f.name == "volume_in_liter")
    assert volume.note == "VDI 4645 rule of thumb"
    reserve = next(f for f in description.sizable_fields if f.name == "reserve_in_liter")
    assert reserve.fields_read == ("volume_in_liter",)
    assert SizableFieldKind.CONSTANT.explain() == "author default (constant law)"


@pytest.mark.base
def test_describe_config_covers_the_other_pilots_and_rejects_a_non_dataclass():
    """The heat distribution, EMS and building classes describe as their design says they should.

    Failure mode caught: a description that works only for the class it was written against
    — a class with no preset at all, one with no sizable field, and one that only provides
    facts each exercise a different empty branch.
    """
    from hisim.components.building import BuildingConfig
    from hisim.components.controller_l2_energy_management_system import EMSConfig
    from hisim.components.heat_distribution_system import HeatDistributionConfig

    heat_distribution = describe_config(HeatDistributionConfig)
    assert [preset.name for preset in heat_distribution.presets] == ["standard"]
    assert heat_distribution.presets[0].pinned == ()
    assert len(heat_distribution.sizable_fields) == 3
    assert not heat_distribution.facts_provided

    assert not describe_config(HeatDistributionConfig).constructors

    energy_management = describe_config(EMSConfig)
    assert [preset.name for preset in energy_management.presets] == ["optimize_own_consumption"]
    assert not energy_management.sizable_fields

    building = describe_config(BuildingConfig)
    assert [info.name for info in building.presets] == ["standard"]
    assert [info.name for info in building.constructors] == ["for_tabula_code"]
    tabula = building.constructors[0]
    assert tabula.parameters[0].name == "building_code"
    assert tabula.parameters[0].default is dataclasses.MISSING
    assert {parameter.name for parameter in tabula.parameters} >= {"number_of_apartments", "building_code"}
    # The building sizes exactly one field from the system: which weather it is computed against.
    assert [field.name for field in building.sizable_fields] == ["weather_identity"]
    assert "heating_load_in_watt" in building.facts_provided

    with pytest.raises(TypeError, match="config dataclass"):
        describe_config(int)


@pytest.mark.base
def test_the_building_preset_is_exactly_its_tabula_constructor_call():
    """``preset_standard`` builds the same building as the constructor it delegates to.

    Failure mode caught: the preset and the constructor drifting apart — the reference
    single-family house is what every setup and every stored result of this repository was
    computed with, so a value that changes when the preset delegates is a silent change of
    every simulation the repository ships.
    """
    from hisim.components.building import BuildingConfig

    delegated = BuildingConfig.for_tabula_code(
        "Building",
        building_code="DE.N.SFH.05.Gen.ReEx.001.002",
        absolute_conditioned_floor_area_in_m2=121.2,
    )
    assert BuildingConfig.preset_standard("Building") == delegated
    assert preset_provenance(BuildingConfig.preset_standard("Building")) == "standard"
    assert preset_provenance(delegated) is None


@pytest.mark.base
def test_a_config_class_outside_the_kernel_resolves_through_resolve_all():
    """A config declared in this test module — presets, laws, contribution — resolves untouched.

    Failure mode caught: the kernel growing a registry, a lookup table or a special case per
    converted class, which would mean converting a component's config could no longer be a
    change to that component's module alone.
    """
    from hisim.components.building import BuildingConfig

    building = BuildingConfig.preset_standard("Building")
    storage = _StorageConfig.preset_standard("Tank")
    # No weather in this scenario, so the fact the building now reads is seeded by the test.
    resolved = resolve_all([building, storage], seed=SizingContext(weather_identity="test weather"))
    resolved_storage = next(config for config in resolved if isinstance(config, _StorageConfig))
    volume = cast(float, resolved_storage.volume_in_liter)  # resolved: nothing left to size
    assert volume > 0.0
    assert resolved_storage.reserve_in_liter == pytest.approx(volume * 0.1)
    assert resolved_storage.heat_loss_in_watt == 25.0
    assert preset_provenance(resolved_storage) == "standard"
    assert resolved_storage.volume_in_liter is not AUTO


@pytest.mark.base
def test_a_preset_that_pins_a_field_wins_over_its_declared_law():
    """A pinned sizable field survives resolution and still feeds the siblings that read it.

    Failure mode caught: the resolver re-deriving a value the preset author deliberately
    pinned, which would make "preset plus sparse override" — the whole point of presets —
    unreliable.
    """
    resolved = _StorageConfig.preset_fixed_300l("Tank").resolve(SizingContext(heating_load_in_watt=8_000.0))
    assert resolved.volume_in_liter == 300.0
    assert resolved.reserve_in_liter == pytest.approx(30.0)


@pytest.mark.base
def test_the_config_kernel_imports_nothing_from_the_component_layer():
    """No module under ``hisim/config`` imports ``hisim.components`` at module level.

    Failure mode caught: the layering rule quietly breaking — a module-level component
    import in the kernel closes an import cycle, because every component config imports the
    kernel. The one sanctioned exception is a function-body import, which this check ignores
    by looking only at the module's top-level statements.
    """
    package = pathlib.Path(__file__).resolve().parents[1] / "hisim" / "config"
    offenders = []
    for module_path in sorted(package.glob("*.py")):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("hisim.components"):
                offenders.append(f"{module_path.name}:{node.lineno}")
            if isinstance(node, ast.Import):
                offenders += [
                    f"{module_path.name}:{node.lineno}"
                    for alias in node.names
                    if alias.name.startswith("hisim.components")
                ]
    assert offenders == []


@pytest.mark.base
def test_the_deleted_scoping_vocabulary_is_gone_from_the_source_tree():
    """No trace of the fact scoping, adjacency or pre-seeding the binding rule replaced.

    Failure mode caught: a leftover ``FactScope``, ``adjacency`` or ``preseeded_facts``
    keeping the superseded three-way scope alive next to the binding rule, which would give
    a reader two contradictory answers to "how does a consumer find its provider".
    """
    package = pathlib.Path(__file__).resolve().parents[1] / "hisim"
    forbidden = ("FactScope", "preseeded_facts", "adjacency")
    offenders = [
        f"{path}:{word}"
        for path in package.rglob("*.py")
        for word in forbidden
        if word in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


@pytest.mark.base
def test_the_description_reports_exactly_what_the_registries_hold():
    """``describe_config`` and the registries agree on names, order and canonical preset.

    Failure mode caught: the description and the registry drifting apart — a description
    built from a second, private discovery would keep reporting presets that the executor
    can no longer find, or lose the declaration order that decides which preset is
    canonical.
    """
    from hisim.components.generic_boiler import GenericBoilerConfig

    description = describe_config(GenericBoilerConfig)
    assert [info.name for info in description.presets] == list(presets_of(GenericBoilerConfig))
    assert [info.name for info in description.constructors] == list(constructors_of(GenericBoilerConfig))
    canonical = canonical_preset(GenericBoilerConfig)
    assert canonical is not None
    assert [info.name for info in description.presets if info.canonical] == [canonical.name]
