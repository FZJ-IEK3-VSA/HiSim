"""Unit tests for :mod:`hisim.components.generic_electric_heating`.

These tests pin down the data-validation behaviour of
:meth:`ElectricHeating.get_cost_opex`, which previously relied on bare
``assert`` statements for runtime checks that depend on simulation data.
Bare asserts are stripped under ``python -O``, so the checks are replaced
with explicit :class:`ValueError` raises that survive optimization flags and
carry a meaningful message.

The tests construct an :class:`ElectricHeating` instance and call
``get_cost_opex`` directly with a hand-built ``all_outputs`` list and a
matching :class:`pandas.DataFrame`, avoiding the cost of a full simulation.
"""

# clean

from typing import List

import pandas as pd
import pytest

from hisim import loadtypes as lt
from hisim.component import ComponentOutput, DisplayConfig
from hisim.components.generic_electric_heating import ElectricHeating, ElectricHeatingConfig
from hisim.simulationparameters import SimulationParameters

# Mark every test in this module as a fast ``base`` test (see pytest.ini).
pytestmark: pytest.MarkDecorator = pytest.mark.base

# One-hour timestep keeps the Watt -> kWh conversion arithmetic simple:
#   sum(values) * seconds_per_timestep / 3.6e6 == sum(values) * 3600 / 3.6e6
#   == sum(values) * 1e-3.
_SECONDS_PER_TIMESTEP = 3600
_N_TIMESTEPS = 2


def _make_electric_heating() -> ElectricHeating:
    """Build an :class:`ElectricHeating` with DHW preparation enabled.

    Enabling domestic-hot-water preparation ensures both the space-heating and
    DHW electric power outputs are declared, which is what ``get_cost_opex``
    searches for in ``all_outputs``.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(
        year=2021, seconds_per_timestep=_SECONDS_PER_TIMESTEP
    )
    config = ElectricHeatingConfig.get_default_electric_heating_config(
        with_domestic_hot_water_preparation=True
    )
    return ElectricHeating(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
        my_display_config=DisplayConfig(display_in_webtool=True),
    )


def _matching_outputs(component: ElectricHeating) -> List[ComponentOutput]:
    """Return the component's own outputs to use as the ``all_outputs`` argument.

    ``get_cost_opex`` matches outputs by ``component_name``, ``load_type``,
    ``field_name`` and ``unit``; the component's declared outputs already carry
    the correct ``component_name`` and field-name constants, so reusing them
    keeps the test robust against renaming.
    """
    return list(component.outputs)


def _postprocessing_results(
    all_outputs: List[ComponentOutput],
    component: ElectricHeating,
    sh_power_watts: List[float],
    dhw_power_watts: List[float],
) -> pd.DataFrame:
    """Build a ``postprocessing_results`` frame aligned with ``all_outputs``.

    The column at position ``index`` corresponds to ``all_outputs[index]`` (the
    same convention ``get_cost_opex`` uses via ``postprocessing_results.iloc[:, index]``).
    The space-heating and DHW electric-power columns are set to the provided
    wattage profiles; every other column is zero-filled so it does not affect
    the consumption sums.
    """
    sh_index = _electric_power_index(all_outputs, component.ElectricOutputShPower)
    dhw_index = _electric_power_index(all_outputs, component.ElectricOutputDhwPower)
    data: dict = {
        i: ([0.0] * _N_TIMESTEPS) for i in range(len(all_outputs))
    }
    data[sh_index] = list(sh_power_watts)
    data[dhw_index] = list(dhw_power_watts)
    return pd.DataFrame(data)


def _electric_power_index(all_outputs: List[ComponentOutput], field_name: str) -> int:
    """Find the position of an ELECTRICITY/WATT output by field name."""
    for index, output in enumerate(all_outputs):
        if (
            output.load_type == lt.LoadTypes.ELECTRICITY
            and output.unit == lt.Units.WATT
            and output.field_name == field_name
        ):
            return index
    raise AssertionError(f"Expected output {field_name} not found in all_outputs")


def test_get_cost_opex_computes_consumption_from_both_outputs() -> None:
    """With both electric-power outputs present, OPEX consumption is summed correctly.

    1000 W for 2 h == 2.0 kWh (space heating); 2000 W for 2 h == 4.0 kWh (DHW);
    total == 6.0 kWh. The conversion is sum(W) * seconds_per_timestep / 3.6e6.
    """
    electric_heating = _make_electric_heating()
    all_outputs = _matching_outputs(electric_heating)
    postprocessing_results = _postprocessing_results(
        all_outputs,
        electric_heating,
        sh_power_watts=[1000.0, 1000.0],
        dhw_power_watts=[2000.0, 2000.0],
    )

    opex = electric_heating.get_cost_opex(
        all_outputs=all_outputs, postprocessing_results=postprocessing_results
    )

    assert opex.consumption_for_space_heating_in_kwh == pytest.approx(2.0)
    assert opex.consumption_for_domestic_hot_water_in_kwh == pytest.approx(4.0)
    assert opex.total_consumption_in_kwh == pytest.approx(6.0)
    assert opex.loadtype == lt.LoadTypes.ELECTRICITY


def test_get_cost_opex_raises_when_space_heating_output_missing() -> None:
    """A missing ``ElectricOutputShPower`` raises ``ValueError``, not ``AssertionError``.

    This is the regression guard for replacing the bare ``assert`` with an
    explicit raise: the validation must survive ``python -O`` and name the
    missing output channel.
    """
    electric_heating = _make_electric_heating()
    all_outputs = [
        output
        for output in _matching_outputs(electric_heating)
        if output.field_name != electric_heating.ElectricOutputShPower
    ]
    # Provide a valid DHW column so the failure is attributable to the missing
    # space-heating output, not the missing DHW output.
    dhw_index = _electric_power_index(all_outputs, electric_heating.ElectricOutputDhwPower)
    data: dict = {i: ([0.0] * _N_TIMESTEPS) for i in range(len(all_outputs))}
    data[dhw_index] = [2000.0, 2000.0]
    postprocessing_results = pd.DataFrame(data)

    with pytest.raises(ValueError, match=electric_heating.ElectricOutputShPower):
        electric_heating.get_cost_opex(
            all_outputs=all_outputs, postprocessing_results=postprocessing_results
        )


def test_get_cost_opex_raises_when_dhw_output_missing() -> None:
    """A missing ``ElectricOutputDhwPower`` raises ``ValueError`` naming the output."""
    electric_heating = _make_electric_heating()
    all_outputs = [
        output
        for output in _matching_outputs(electric_heating)
        if output.field_name != electric_heating.ElectricOutputDhwPower
    ]
    sh_index = _electric_power_index(all_outputs, electric_heating.ElectricOutputShPower)
    data = {i: ([0.0] * _N_TIMESTEPS) for i in range(len(all_outputs))}
    data[sh_index] = [1000.0, 1000.0]
    postprocessing_results = pd.DataFrame(data)

    with pytest.raises(ValueError, match=electric_heating.ElectricOutputDhwPower):
        electric_heating.get_cost_opex(
            all_outputs=all_outputs, postprocessing_results=postprocessing_results
        )
