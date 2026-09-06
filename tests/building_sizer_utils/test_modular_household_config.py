"""Tests for the ``ModularHouseholdConfig`` factory classmethods and ``get_hash``.

Covers GitLab issue #733: the deterministic, side-effect-free
``get_default_config_for_household_*`` classmethods and the ``get_hash``
instance method of
:class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`
were previously untested. The file-writing helper ``write_config`` is intentionally
not exercised here.

The last family of tests covers ``read_in_configs`` and the line it draws between
"no config was given" and "a config was given but cannot be read": the first answers
``None`` so the calling setup uses its own default household, the second raises and
names the source and the reason. The reader used to swallow every read failure and
answer ``None`` either way, so a typo'd path silently simulated the default household.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hisim.building_sizer_utils.interface_configs import (
    archetype_config,
    system_config,
)
from hisim.building_sizer_utils.interface_configs.modular_household_config import (
    ModularHouseholdConfig,
    read_in_configs,
)
from hisim.loadtypes import ComponentType, HeatingSystems

#: The ten ``get_default_config_for_household_*`` factories paired with the
#: ``HeatingSystems`` member each one is documented to configure. Keeping this
#: table in one place lets the parametrized assertions below cover every variant
#: without hand-writing a test per factory.
HOUSEHOLD_FACTORIES: list[tuple[str, HeatingSystems]] = [
    ("get_default_config_for_household_gas", HeatingSystems.GAS_HEATING),
    ("get_default_config_for_household_oil", HeatingSystems.OIL_HEATING),
    ("get_default_config_for_household_heatpump", HeatingSystems.HEAT_PUMP),
    (
        "get_default_config_for_household_district_heating",
        HeatingSystems.DISTRICT_HEATING,
    ),
    (
        "get_default_config_for_household_pellet",
        HeatingSystems.PELLET_HEATING,
    ),
    (
        "get_default_config_for_household_wood_chips",
        HeatingSystems.WOOD_CHIP_HEATING,
    ),
    (
        "get_default_config_for_household_hydrogen",
        HeatingSystems.HYDROGEN_HEATING,
    ),
    (
        "get_default_config_for_household_electric_heating",
        HeatingSystems.ELECTRIC_HEATING,
    ),
    (
        "get_default_config_for_household_gas_solar_thermal",
        HeatingSystems.GAS_SOLAR_THERMAL,
    ),
    (
        "get_default_config_for_household_heatpump_solar_thermal",
        HeatingSystems.HEAT_PUMP_SOLAR_THERMAL,
    ),
]


@pytest.mark.base
@pytest.mark.parametrize(
    "factory_name,expected_heating_system",
    HOUSEHOLD_FACTORIES,
    ids=[name for name, _ in HOUSEHOLD_FACTORIES],
)
def test_factory_returns_well_formed_config(
    factory_name: str, expected_heating_system: HeatingSystems
) -> None:
    """Each factory returns a ``ModularHouseholdConfig`` with the expected heating system.

    The returned config must bundle a non-``None`` :class:`EnergySystemConfig`
    (configured for the variant's heating system) and a non-``None``
    :class:`ArcheTypeConfig`.
    """
    factory = getattr(ModularHouseholdConfig, factory_name)
    cfg = factory()

    # The factory itself returns the advertised type.
    assert isinstance(cfg, ModularHouseholdConfig)
    # Both sub-configs are populated (not the dataclass field default of None).
    assert isinstance(cfg.energy_system_config_, system_config.EnergySystemConfig)
    assert isinstance(cfg.archetype_config_, archetype_config.ArcheTypeConfig)
    # The heating system matches the variant documented for this factory.
    assert cfg.energy_system_config_.heating_system == expected_heating_system


@pytest.mark.base
@pytest.mark.parametrize(
    "factory_name",
    [name for name, _ in HOUSEHOLD_FACTORIES],
    ids=[name for name, _ in HOUSEHOLD_FACTORIES],
)
def test_factory_sub_configs_are_not_shared(factory_name: str) -> None:
    """Repeated factory calls return distinct instances with no shared sub-configs."""
    first = getattr(ModularHouseholdConfig, factory_name)()
    second = getattr(ModularHouseholdConfig, factory_name)()

    assert first is not second
    assert first.energy_system_config_ is not second.energy_system_config_
    assert first.archetype_config_ is not second.archetype_config_


@pytest.mark.base
@pytest.mark.parametrize(
    "factory_name",
    [
        "get_default_config_for_household_gas",
        "get_default_config_for_household_heatpump",
        "get_default_config_for_household_district_heating",
    ],
    ids=["gas", "heatpump", "district_heating"],
)
def test_factory_shared_non_heating_defaults(factory_name: str) -> None:
    """The non-heating energy-system defaults documented in the class docstring hold.

    For the representative variants (gas, heat pump, district heating) the
    energy system config must keep the full rooftop PV potential, enable the
    battery and EMS, and use floor heating as the heat distribution system.
    """
    cfg = getattr(ModularHouseholdConfig, factory_name)()
    energy_system = cfg.energy_system_config_
    assert energy_system is not None

    assert energy_system.share_of_maximum_pv_potential == 1.0
    assert energy_system.use_battery_and_ems is True
    assert (
        energy_system.heat_distribution_system
        == ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
    )


@pytest.mark.base
def test_factories_produce_pairwise_distinct_heating_systems() -> None:
    """The ten factories produce configs whose ``heating_system`` fields are all distinct.

    This guards in particular against the gas vs. gas-solar-thermal and the
    heat-pump vs. heat-pump-solar-thermal pairs collapsing onto the same value.
    """
    heating_systems = [
        getattr(ModularHouseholdConfig, factory_name)().energy_system_config_.heating_system
        for factory_name, _ in HOUSEHOLD_FACTORIES
    ]
    assert len(heating_systems) == len(HOUSEHOLD_FACTORIES)
    assert len(set(heating_systems)) == len(HOUSEHOLD_FACTORIES)
    # Every value is a genuine HeatingSystems member (not a stray string).
    for heating_system in heating_systems:
        assert isinstance(heating_system, HeatingSystems)


@pytest.mark.base
def test_factories_cover_all_documented_variants() -> None:
    """Guard against a new factory being added without a corresponding test entry."""
    public_factories = {
        name
        for name in dir(ModularHouseholdConfig)
        if name.startswith("get_default_config_for_household_")
        and callable(getattr(ModularHouseholdConfig, name))
    }
    tested_factories = {name for name, _ in HOUSEHOLD_FACTORIES}
    assert public_factories == tested_factories


@pytest.mark.base
def test_get_hash_is_deterministic_and_int() -> None:
    """``get_hash`` returns the same ``int`` when called twice on the same instance."""
    cfg = ModularHouseholdConfig.get_default_config_for_household_gas()
    first = cfg.get_hash()
    second = cfg.get_hash()

    assert isinstance(first, int)
    assert isinstance(second, int)
    assert first == second


@pytest.mark.base
def test_get_hash_discriminates_variants() -> None:
    """``get_hash`` distinguishes configs built by different factories.

    Two distinct pairs are checked: gas vs. heat pump, and oil vs. district
    heating. If the underlying defaults hashed identically this would surface a
    real bug, so the inequality is treated as part of the contract.
    """
    gas_hash = ModularHouseholdConfig.get_default_config_for_household_gas().get_hash()
    heatpump_hash = (
        ModularHouseholdConfig.get_default_config_for_household_heatpump().get_hash()
    )
    assert gas_hash != heatpump_hash

    oil_hash = ModularHouseholdConfig.get_default_config_for_household_oil().get_hash()
    district_hash = (
        ModularHouseholdConfig.get_default_config_for_household_district_heating().get_hash()
    )
    assert oil_hash != district_hash


@pytest.mark.base
def test_get_hash_with_empty_config_returns_int() -> None:
    """A ``ModularHouseholdConfig`` with both sub-configs ``None`` still hashes to an ``int``."""
    cfg = ModularHouseholdConfig()
    assert cfg.energy_system_config_ is None
    assert cfg.archetype_config_ is None

    value = cfg.get_hash()
    assert isinstance(value, int)


@pytest.mark.base
def test_direct_construction_leaves_sub_configs_none() -> None:
    """Constructing ``ModularHouseholdConfig`` directly leaves the sub-configs as ``None``.

    This confirms the populated defaults come from the factories, not from the
    dataclass field defaults themselves.
    """
    cfg = ModularHouseholdConfig()
    assert cfg.energy_system_config_ is None
    assert cfg.archetype_config_ is None


#: The unreadable-config cases ``read_in_configs`` has to refuse, each as a file body to write
#: (``None`` meaning "write no file at all", i.e. the path does not exist) paired with the
#: fragments the refusal must contain. Every message has to name the source, so the file name is
#: asserted separately for all of them and only the case-specific reason is listed here.
UNREADABLE_CONFIGS: list[tuple[str, str | None, list[str]]] = [
    ("missing_file", None, ["Could not open", "No such file"]),
    ("invalid_json", "{not json at all", ["not valid JSON", "Expecting property name"]),
    ("truncated_json", '{"archetype_config_": {', ["not valid JSON"]),
    ("wrong_shape", "[1, 2, 3]", ["Could not decode", "ModularHouseholdConfig"]),
    (
        "wrong_field_type",
        '{"archetype_config_": {"lpg_households": 5}}',
        ["Could not decode", "ModularHouseholdConfig"],
    ),
    (
        "both_modules_missing",
        "{}",
        ["declares neither an energy system config nor an archetype config"],
    ),
]


@pytest.mark.base
@pytest.mark.parametrize(
    "case_name, file_body, expected_fragments",
    UNREADABLE_CONFIGS,
    ids=[case_name for case_name, _, _ in UNREADABLE_CONFIGS],
)
def test_read_in_configs_refuses_a_config_it_cannot_read(
    case_name: str, file_body: str | None, expected_fragments: list[str], tmp_path: Path
) -> None:
    """A named config that cannot be read raises and names both the path and the reason.

    A missing file, a syntax error, a payload of the wrong shape and a config declaring neither of
    its two modules used to be indistinguishable from "no config given": all four answered ``None``
    and the calling setup then simulated its shipped default household with only a warning. Each is
    now a refusal that quotes the path so the caller can see which file was rejected, plus the
    underlying reason so they can tell a typo'd path from a stray comma.
    """
    config_path = tmp_path / f"{case_name}.json"
    if file_body is not None:
        config_path.write_text(file_body, encoding="utf8")

    with pytest.raises(ValueError) as refusal:
        read_in_configs(str(config_path))

    message = str(refusal.value)
    assert str(config_path) in message
    for fragment in expected_fragments:
        assert fragment in message


@pytest.mark.base
def test_read_in_configs_reads_a_valid_config_file(tmp_path: Path) -> None:
    """A well-formed config file is read back with both of its modules intact."""
    written = ModularHouseholdConfig.get_default_config_for_household_gas()
    config_path = tmp_path / "valid_config.json"
    config_path.write_text(json.dumps(written.to_dict()), encoding="utf8")

    read_back = read_in_configs(str(config_path))

    assert read_back is not None
    assert read_back.energy_system_config_ is not None
    assert read_back.archetype_config_ is not None
    assert read_back.energy_system_config_.heating_system == HeatingSystems.GAS_HEATING
    assert read_back.to_dict() == written.to_dict()


@pytest.mark.base
def test_read_in_configs_strips_whitespace_around_the_path(tmp_path: Path) -> None:
    """Carriage returns and newlines around the path are stripped before the file is opened.

    The building sizer hands the path through shell plumbing that can append a line ending, so a
    path with surrounding whitespace still has to name a readable file rather than refuse.
    """
    config_path = tmp_path / "valid_config.json"
    config_path.write_text(
        json.dumps(ModularHouseholdConfig.get_default_config_for_household_heatpump().to_dict()),
        encoding="utf8",
    )

    read_back = read_in_configs(f" {config_path}\r\n")

    assert read_back is not None
    assert read_back.energy_system_config_ is not None
    assert read_back.energy_system_config_.heating_system == HeatingSystems.HEAT_PUMP


@pytest.mark.base
@pytest.mark.parametrize(
    "no_config",
    [None, "", "   ", "\r\n"],
    ids=["none", "empty_string", "blanks", "line_ending_only"],
)
def test_read_in_configs_answers_none_when_no_config_was_given(
    no_config: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every spelling of "no config given" answers ``None`` and says so in the log.

    ``None`` is the answer the eleven building-sizer setups read as "use my own default household",
    so this is the one path that must keep working unchanged after the reader started refusing
    unreadable configs. The logged line is asserted too: it is what tells a reader of the run log
    that the default was chosen deliberately rather than fallen back to after a failed read.
    """
    assert read_in_configs(no_config) is None

    assert "No modular household config was given" in capsys.readouterr().out


@pytest.mark.base
def test_read_in_configs_accepts_an_already_decoded_config_dict() -> None:
    """A configuration handed over as a dictionary is decoded instead of being refused.

    Nine of the building-sizer setups write the configuration they used back into
    ``Simulator.my_module_config`` -- the very attribute that is passed to ``read_in_configs`` -- so
    a second setup call on the same simulator hands over a decoded mapping rather than a path.
    """
    written = ModularHouseholdConfig.get_default_config_for_household_oil()

    read_back = read_in_configs(written.to_dict())

    assert read_back is not None
    assert read_back.to_dict() == written.to_dict()


@pytest.mark.base
def test_read_in_configs_refuses_an_empty_config_dict() -> None:
    """An empty mapping is a config that declares nothing, so it is refused rather than defaulted."""
    with pytest.raises(ValueError) as refusal:
        read_in_configs({})

    assert "declares neither an energy system config nor an archetype config" in str(refusal.value)
