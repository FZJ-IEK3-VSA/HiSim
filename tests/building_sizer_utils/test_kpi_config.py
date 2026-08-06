"""Tests for :class:`KPIConfig` and its ``get_kpi_for_rating`` method.

Covers GitLab issue #360: the pure, side-effect-free ``get_kpi_for_rating``
method of :class:`KPIConfig` was previously untested. The method maps each
handled :class:`KPIForRatingInOptimization` enum member to the matching
numeric field of the dataclass and raises ``ValueError`` for the members that
are not handled.
"""

from __future__ import annotations

import dataclasses

import pytest

from hisim.building_sizer_utils.interface_configs.kpi_config import (
    KPIConfig,
    KPIForRatingInOptimization,
)

#: Pairs of (handled enum member, the dataclass field name it maps to).
HANDLED_KPI_TO_FIELD: list[tuple[KPIForRatingInOptimization, str]] = [
    (KPIForRatingInOptimization.SELFSUFFICIENCY_ELECTRICITY,
     "self_sufficiency_rate_electricity_in_percent"),
    (KPIForRatingInOptimization.SELFSUFFICIENCY_ALL_ENERGY,
     "self_sufficiency_rate_all_energy_in_percent"),
    (KPIForRatingInOptimization.TOTAL_UPFRONT_NET_INVESTMENT_COSTS,
     "total_upfront_net_investment_costs_in_euro"),
    (KPIForRatingInOptimization.ANNUALIZED_TOTAL_COSTS,
     "annualized_total_costs_in_euro_per_m2"),
    (KPIForRatingInOptimization.ANNUALIZED_ENERGY_COSTS,
     "annualized_energy_costs_in_euro_per_m2"),
    (KPIForRatingInOptimization.ANNUALIZED_MAINTENANCE_COSTS,
     "annualized_maintenance_costs_in_euro_per_m2"),
    (KPIForRatingInOptimization.ANNUALIZED_INVESTMENT_COSTS,
     "annualized_investment_costs_in_euro_per_m2"),
    (KPIForRatingInOptimization.ANNUALIZED_NET_INVESTMENT_COSTS,
     "annualized_net_investment_costs_in_euro_per_m2"),
    (KPIForRatingInOptimization.ANNUALIZED_TOTAL_CO2_EMISSION,
     "annualized_total_co2_emissions_in_kg_per_m2"),
    (KPIForRatingInOptimization.ANNUALIZED_ENERGY_CO2_EMISSION,
     "annualized_energy_co2_emissions_in_kg_per_m2"),
    (KPIForRatingInOptimization.ANNUALIZED_PURCHASED_ENERGY_CONSUMPTION,
     "annualized_purchased_energy_consumption_in_kwh_per_m2"),
]

#: Enum members that ``get_kpi_for_rating`` does not handle and must reject.
UNHANDLED_KPIS: list[KPIForRatingInOptimization] = [
    KPIForRatingInOptimization.ANNUALIZED_ELECTRICITY_TO_GRID,
    KPIForRatingInOptimization.ANNUALIZED_ELECTRICITY_FROM_GRID,
    KPIForRatingInOptimization.MIN_BUILDING_INDOOR_TEMP,
    KPIForRatingInOptimization.MAX_BUILDING_INDOOR_TEMP,
    KPIForRatingInOptimization.DEV_FROM_MIN_BUILDING_INDOOR_TEMP,
    KPIForRatingInOptimization.DEV_FROM_MAX_BUILDING_INDOOR_TEMP,
]


def make_kpi_config(**overrides: float) -> KPIConfig:
    """Build a :class:`KPIConfig` with every field set to a distinct value.

    Each field receives a unique, easily-assertable integer value (an
    incrementing counter keyed by declaration order) so that every branch of
    ``get_kpi_for_rating`` can be verified independently. Keyword arguments in
    ``overrides`` replace the default value for the named field, which the
    edge-case tests use to pin a field to ``0.0`` or a negative number.
    """
    field_names = [f.name for f in dataclasses.fields(KPIConfig)]
    if unknown := set(overrides) - set(field_names):
        raise ValueError(f"Unknown KPIConfig field(s): {sorted(unknown)}")
    values: dict[str, float] = {name: float(idx) for idx, name in enumerate(field_names, start=1)}
    values.update(overrides)
    return KPIConfig(**values)


@pytest.mark.base
@pytest.mark.parametrize(
    "chosen_kpi, field_name",
    HANDLED_KPI_TO_FIELD,
    ids=[member.name for member, _ in HANDLED_KPI_TO_FIELD],
)
def test_get_kpi_for_rating_returns_matching_field(
    chosen_kpi: KPIForRatingInOptimization, field_name: str
) -> None:
    """Each handled enum member returns the value of its mapped field."""
    config = make_kpi_config()
    expected = getattr(config, field_name)
    assert config.get_kpi_for_rating(chosen_kpi) == expected
    # sanity: the helper really assigned a distinct value per field
    assert expected == float([f.name for f in dataclasses.fields(KPIConfig)].index(field_name) + 1)


@pytest.mark.base
@pytest.mark.parametrize(
    "chosen_kpi", UNHANDLED_KPIS, ids=[member.name for member in UNHANDLED_KPIS]
)
def test_get_kpi_for_rating_raises_for_unhandled(
    chosen_kpi: KPIForRatingInOptimization,
) -> None:
    """Unhandled enum members raise ``ValueError``."""
    config = make_kpi_config()
    with pytest.raises(ValueError, match="not recognized"):
        config.get_kpi_for_rating(chosen_kpi)


@pytest.mark.base
@pytest.mark.parametrize(
    "chosen_kpi, field_name",
    HANDLED_KPI_TO_FIELD,
    ids=[member.name for member, _ in HANDLED_KPI_TO_FIELD],
)
def test_get_kpi_for_rating_zero_value(
    chosen_kpi: KPIForRatingInOptimization, field_name: str
) -> None:
    """A field set to ``0.0`` is returned as ``0.0`` (no clamping)."""
    config = make_kpi_config(**{field_name: 0.0})
    assert config.get_kpi_for_rating(chosen_kpi) == 0.0


@pytest.mark.base
@pytest.mark.parametrize(
    "chosen_kpi, field_name",
    HANDLED_KPI_TO_FIELD,
    ids=[member.name for member, _ in HANDLED_KPI_TO_FIELD],
)
def test_get_kpi_for_rating_negative_value(
    chosen_kpi: KPIForRatingInOptimization, field_name: str
) -> None:
    """A negative field value is returned unchanged (no abs / clamping)."""
    config = make_kpi_config(**{field_name: -12.5})
    assert config.get_kpi_for_rating(chosen_kpi) == -12.5


@pytest.mark.base
@pytest.mark.parametrize("member", list(KPIForRatingInOptimization))
def test_kpi_for_rating_in_optimization_is_str_enum(
    member: KPIForRatingInOptimization,
) -> None:
    """``KPIForRatingInOptimization`` is a ``str``-based enum with readable values."""
    assert isinstance(member, KPIForRatingInOptimization)
    assert isinstance(member.value, str)
    # str mixin: the member itself behaves as its value string.
    assert member == member.value


@pytest.mark.base
def test_enum_member_values_are_pinned() -> None:
    """Pin the human-readable ``.value`` strings of every enum member."""
    expected_values = {
        KPIForRatingInOptimization.TOTAL_UPFRONT_NET_INVESTMENT_COSTS: "Total Upfront Net Investment Costs [€]",
        KPIForRatingInOptimization.ANNUALIZED_TOTAL_COSTS: "Annualized Total Costs [€/m2]",
        KPIForRatingInOptimization.ANNUALIZED_ENERGY_COSTS: "Annualized Energy Costs [€/m2]",
        KPIForRatingInOptimization.ANNUALIZED_MAINTENANCE_COSTS: "Annualized Maintenance Costs [€/m2]",
        KPIForRatingInOptimization.ANNUALIZED_INVESTMENT_COSTS: "Annualized Investment Costs [€/m2]",
        KPIForRatingInOptimization.ANNUALIZED_NET_INVESTMENT_COSTS: "Annualized Net Investment Costs [€/m2]",
        KPIForRatingInOptimization.ANNUALIZED_TOTAL_CO2_EMISSION: "Annualized Total CO2 Emissions [kg/m2]",
        KPIForRatingInOptimization.ANNUALIZED_ENERGY_CO2_EMISSION: "Annualized Energy CO2 Emissions [kg/m2]",
        KPIForRatingInOptimization.SELFSUFFICIENCY_ELECTRICITY: "Self-Sufficiency Rate For Electricity [%]",
        KPIForRatingInOptimization.SELFSUFFICIENCY_ALL_ENERGY: "Self-Sufficiency Rate All Energy [%]",
        KPIForRatingInOptimization.ANNUALIZED_PURCHASED_ENERGY_CONSUMPTION: "Annualized Energy Consumption [kWh/m2]",
        KPIForRatingInOptimization.ANNUALIZED_ELECTRICITY_TO_GRID: "Annualized Electricity To Grid [kWh/m2]",
        KPIForRatingInOptimization.ANNUALIZED_ELECTRICITY_FROM_GRID: "Annualized Electricity From Grid [kWh/m2]",
        KPIForRatingInOptimization.MIN_BUILDING_INDOOR_TEMP: "Minimum Indoor Temperature [°C]",
        KPIForRatingInOptimization.MAX_BUILDING_INDOOR_TEMP: "Maximum Indoor Temperature [°C]",
        KPIForRatingInOptimization.DEV_FROM_MIN_BUILDING_INDOOR_TEMP: "Deviation From Minimum Indoor Temperature [°C*h]",
        KPIForRatingInOptimization.DEV_FROM_MAX_BUILDING_INDOOR_TEMP: "Deviation From Maximum Indoor Temperature [°C*h]",
    }
    for member, value in expected_values.items():
        assert member.value == value


@pytest.mark.base
def test_all_enum_members_are_covered_by_test_suites() -> None:
    """Guard against new enum members silently slipping past these tests."""
    all_members = set(KPIForRatingInOptimization)
    handled = {member for member, _ in HANDLED_KPI_TO_FIELD}
    unhandled = set(UNHANDLED_KPIS)
    assert handled.isdisjoint(unhandled)
    assert handled | unhandled == all_members
    assert len(HANDLED_KPI_TO_FIELD) == len(handled)
    assert len(UNHANDLED_KPIS) == len(unhandled)
