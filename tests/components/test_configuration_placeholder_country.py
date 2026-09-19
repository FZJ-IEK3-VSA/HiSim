"""A country without reviewed legacy cost data is registered with sentinel values, never with German ones.

The legacy tables in ``hisim/components/configuration.py`` are read by every component's opex method
during KPI collection. Ireland has no reviewed rows there yet; instead of crashing, or silently pricing
an Irish house with German numbers, it is registered as a placeholder whose every number is minus one
billion. These tests pin that contract: the shape equals Germany's, every number is the sentinel, and
the getters serve it.
"""

import pytest

from hisim.components.configuration import (
    EmissionFactorsAndCostsForDevicesConfig,
    EmissionFactorsAndCostsForFuelsConfig,
    PlaceholderCountryFactors,
    capex_techno_economic_parameters,
    opex_techno_economic_parameters,
)
from hisim.loadtypes import ComponentType

pytestmark = pytest.mark.base


def _numbers(node):
    """Yield every non-boolean number below *node*."""
    if isinstance(node, dict):
        for value in node.values():
            yield from _numbers(value)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield node


def test_ireland_is_registered_as_a_placeholder() -> None:
    """IE exists in both tables and is marked as a placeholder."""
    assert PlaceholderCountryFactors.is_placeholder("IE")
    assert not PlaceholderCountryFactors.is_placeholder("DE")
    assert "IE" in opex_techno_economic_parameters
    assert "IE" in capex_techno_economic_parameters


def test_the_placeholder_has_germanys_shape_and_only_sentinels() -> None:
    """Same years, same fields, same devices as Germany; every number is the sentinel."""
    for table in (opex_techno_economic_parameters, capex_techno_economic_parameters):
        assert set(table["IE"]) == set(table["DE"])
        for year in table["DE"]:
            assert set(table["IE"][year]) == set(table["DE"][year])
        numbers = list(_numbers(table["IE"]))
        assert numbers, "the placeholder carries no numbers at all"
        assert all(value == PlaceholderCountryFactors.SENTINEL for value in numbers)


def test_the_getters_serve_the_sentinel_instead_of_crashing() -> None:
    """The two legacy getters answer for IE, and every field they return is the sentinel."""
    fuels = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(2021, "IE")
    assert fuels.electricity_costs_in_euro_per_kwh == PlaceholderCountryFactors.SENTINEL
    assert fuels.gas_footprint_in_kg_per_kwh == PlaceholderCountryFactors.SENTINEL
    devices = EmissionFactorsAndCostsForDevicesConfig.get_values_for_year(2024, ComponentType.HEAT_PUMP, "IE")
    assert devices.investment_costs_in_euro_per_kw == PlaceholderCountryFactors.SENTINEL


def test_a_reviewed_country_is_never_overwritten() -> None:
    """Registering a country that has data is refused."""
    with pytest.raises(ValueError):
        PlaceholderCountryFactors.register("DE")
    assert opex_techno_economic_parameters["DE"][2019]["electricity_costs_in_euro_per_kwh"] == 0.295
