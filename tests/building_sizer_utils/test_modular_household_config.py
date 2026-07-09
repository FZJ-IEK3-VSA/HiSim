"""Tests for the ``ModularHouseholdConfig`` factory classmethods and ``get_hash``.

Covers GitLab issue #733: the deterministic, side-effect-free
``get_default_config_for_household_*`` classmethods and the ``get_hash``
instance method of
:class:`~hisim.building_sizer_utils.interface_configs.modular_household_config.ModularHouseholdConfig`
were previously untested. The file-I/O helpers ``write_config`` and
``read_in_configs`` are intentionally not exercised here.
"""

from __future__ import annotations

import pytest

from hisim.building_sizer_utils.interface_configs import (
    archetype_config,
    system_config,
)
from hisim.building_sizer_utils.interface_configs.modular_household_config import (
    ModularHouseholdConfig,
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
