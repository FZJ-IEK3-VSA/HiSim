"""Unit tests for :mod:`hisim.components.generic_tankless_water_heater`.

Two kinds of test live here:

* ``i_simulate`` tests that drive the component with fake inputs (the pattern from
  ``test_simple_heat_source.py``) and check the energy balance, the rated-power cap
  and the handling of the optional cold-water-temperature input.
* Post-processing tests that call ``get_cost_opex`` / ``get_component_kpi_entries``
  directly with a hand-built ``all_outputs`` list and matching frame (the pattern
  from ``test_generic_electric_heating.py``), avoiding a full simulation.

Expected values are derived from first principles in each docstring rather than by
re-running the component's own formula, so an arithmetic regression is caught.
"""

# clean

from typing import List, Optional

import pandas as pd
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.controller_l2_energy_management_system import (
    EMSConfig,
    L2GenericEnergyManagementSystem,
)
from hisim.components.electricity_meter import ElectricityMeter, ElectricityMeterConfig
from hisim.components.generic_tankless_water_heater import (
    DENSITY_OF_WATER_AT_40_DEGREE_CELSIUS_IN_KG_PER_LITER,
    TanklessWaterHeater,
    TanklessWaterHeaterConfig,
)
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft

pytestmark: pytest.MarkDecorator = pytest.mark.base

_SECONDS_PER_TIMESTEP = 60
#: Specific heat capacity of water as declared in ``PhysicsConfig`` for ``LoadTypes.WATER``.
_CP_WATER_IN_JOULE_PER_KG_PER_KELVIN = 4180.0
#: 0.992 kg/l at 40 °C, matching ``SimpleDHWStorage``.
_DENSITY = DENSITY_OF_WATER_AT_40_DEGREE_CELSIUS_IN_KG_PER_LITER


def _make_heater(
    maximum_electric_power_in_watt: float = 21_000.0,
    efficiency: float = 1.0,
    set_water_temperature_in_celsius: float = 40.0,
    cold_water_temperature_in_celsius: float = 10.0,
) -> TanklessWaterHeater:
    """Build a heater with an explicit, easy-to-hand-check parameter set."""
    my_simulation_parameters = SimulationParameters.one_day_only(
        year=2021, seconds_per_timestep=_SECONDS_PER_TIMESTEP
    )
    config = TanklessWaterHeaterConfig.get_default_tankless_water_heater_config()
    config.maximum_electric_power_in_watt = maximum_electric_power_in_watt
    config.efficiency = efficiency
    config.set_water_temperature_in_celsius = set_water_temperature_in_celsius
    config.cold_water_temperature_in_celsius = cold_water_temperature_in_celsius
    return TanklessWaterHeater(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
        my_display_config=cp.DisplayConfig(display_in_webtool=True),
    )


def _simulate_one_timestep(
    heater: TanklessWaterHeater,
    water_consumption_in_liter: float,
    cold_water_temperature_in_celsius: Optional[float] = None,
) -> cp.SingleTimeStepValues:
    """Run one timestep with a fake water draw and return the values array.

    ``cold_water_temperature_in_celsius`` is only wired up when given; leaving it
    ``None`` reproduces a scenario where the optional input stays unconnected.
    """
    water_consumption = cp.ComponentOutput(
        "FakeOccupancy", "FakeWaterConsumption", lt.LoadTypes.WARM_WATER, lt.Units.LITER
    )
    fakes: List[cp.ComponentOutput] = [water_consumption]

    cold_water_temperature = None
    if cold_water_temperature_in_celsius is not None:
        cold_water_temperature = cp.ComponentOutput(
            "FakeMains", "FakeColdWaterTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS
        )
        fakes.append(cold_water_temperature)

    number_of_outputs = fft.get_number_of_outputs([heater, *fakes])
    time_step_values = cp.SingleTimeStepValues(number_of_outputs)

    heater.water_consumption_channel.source_output = water_consumption
    if cold_water_temperature is not None:
        heater.cold_water_temperature_channel.source_output = cold_water_temperature

    fft.add_global_index_of_components([heater, *fakes])

    time_step_values.values[water_consumption.global_index] = water_consumption_in_liter
    if cold_water_temperature is not None and cold_water_temperature_in_celsius is not None:
        time_step_values.values[cold_water_temperature.global_index] = cold_water_temperature_in_celsius

    heater.i_simulate(0, time_step_values, False)
    return time_step_values


# ---------------------------------------------------------------------------
# i_simulate
# ---------------------------------------------------------------------------


def test_draw_within_rated_power_is_heated_to_the_set_temperature() -> None:
    """A 3 l/min draw heated 10 -> 40 °C needs 6.2 kW, well below the 21 kW rating.

    m_dot = 3 l * 0.992 kg/l / 60 s = 0.0496 kg/s
    Q     = 0.0496 kg/s * 4180 J/(kg K) * 30 K = 6219.84 W
    With efficiency 1.0 the electric power equals the thermal power, the outlet
    reaches the full set temperature and nothing is left unmet.
    """
    heater = _make_heater()
    values = _simulate_one_timestep(heater, water_consumption_in_liter=3.0)

    expected_mass_flow = 3.0 * _DENSITY / _SECONDS_PER_TIMESTEP
    expected_thermal_power = expected_mass_flow * _CP_WATER_IN_JOULE_PER_KG_PER_KELVIN * 30.0

    assert values.values[heater.water_output_mass_flow_rate_channel.global_index] == pytest.approx(
        expected_mass_flow
    )
    assert values.values[heater.thermal_output_power_channel.global_index] == pytest.approx(
        expected_thermal_power
    )
    assert values.values[heater.electric_output_power_channel.global_index] == pytest.approx(
        expected_thermal_power
    )
    assert values.values[heater.water_output_temperature_channel.global_index] == pytest.approx(40.0)
    assert values.values[heater.unmet_thermal_demand_power_channel.global_index] == pytest.approx(0.0)


def test_electric_power_accounts_for_efficiency() -> None:
    """At 80 % efficiency the electric draw is the thermal power divided by 0.8.

    The delivered thermal power (and hence the outlet temperature) is unchanged;
    only the electricity consumption grows.
    """
    heater = _make_heater(efficiency=0.8)
    values = _simulate_one_timestep(heater, water_consumption_in_liter=3.0)

    expected_thermal_power = 3.0 * _DENSITY / _SECONDS_PER_TIMESTEP * _CP_WATER_IN_JOULE_PER_KG_PER_KELVIN * 30.0

    assert values.values[heater.thermal_output_power_channel.global_index] == pytest.approx(
        expected_thermal_power
    )
    assert values.values[heater.electric_output_power_channel.global_index] == pytest.approx(
        expected_thermal_power / 0.8
    )
    assert values.values[heater.water_output_temperature_channel.global_index] == pytest.approx(40.0)


def test_energy_outputs_are_the_power_outputs_over_the_timestep() -> None:
    """Energy outputs equal power * 60 s / 3600 s/h, i.e. one sixtieth of the wattage."""
    heater = _make_heater()
    values = _simulate_one_timestep(heater, water_consumption_in_liter=3.0)

    thermal_power = values.values[heater.thermal_output_power_channel.global_index]
    electric_power = values.values[heater.electric_output_power_channel.global_index]

    assert values.values[heater.thermal_output_energy_channel.global_index] == pytest.approx(
        thermal_power / 60.0
    )
    assert values.values[heater.electric_output_energy_channel.global_index] == pytest.approx(
        electric_power / 60.0
    )


def test_rated_power_caps_the_draw_and_lowers_the_outlet_temperature() -> None:
    """A 30 l/min draw needs 62.2 kW; a 21 kW device delivers lukewarm water instead.

    m_dot = 30 l * 0.992 kg/l / 60 s = 0.496 kg/s
    heat capacity flow = 0.496 * 4180 = 2073.28 W/K
    Q_demand = 2073.28 * 30 K = 62198.4 W, capped at the 21 kW rating.
    T_out = 10 °C + 21000 W / 2073.28 W/K = 20.129 °C
    The 41198.4 W shortfall is reported, not raised: an undersized Durchlauferhitzer
    delivers cooler water rather than failing.
    """
    heater = _make_heater(maximum_electric_power_in_watt=21_000.0)
    values = _simulate_one_timestep(heater, water_consumption_in_liter=30.0)

    heat_capacity_flow = 30.0 * _DENSITY / _SECONDS_PER_TIMESTEP * _CP_WATER_IN_JOULE_PER_KG_PER_KELVIN
    expected_demand = heat_capacity_flow * 30.0

    assert values.values[heater.electric_output_power_channel.global_index] == pytest.approx(21_000.0)
    assert values.values[heater.thermal_output_power_channel.global_index] == pytest.approx(21_000.0)
    assert values.values[heater.water_output_temperature_channel.global_index] == pytest.approx(
        10.0 + 21_000.0 / heat_capacity_flow
    )
    assert values.values[heater.water_output_temperature_channel.global_index] < 40.0
    assert values.values[heater.unmet_thermal_demand_power_channel.global_index] == pytest.approx(
        expected_demand - 21_000.0
    )


def test_no_draw_means_no_consumption_and_no_standby_loss() -> None:
    """Without a draw the device is idle: no power, no flow, outlet at mains temperature."""
    heater = _make_heater()
    values = _simulate_one_timestep(heater, water_consumption_in_liter=0.0)

    assert values.values[heater.thermal_output_power_channel.global_index] == pytest.approx(0.0)
    assert values.values[heater.electric_output_power_channel.global_index] == pytest.approx(0.0)
    assert values.values[heater.electric_output_energy_channel.global_index] == pytest.approx(0.0)
    assert values.values[heater.water_output_mass_flow_rate_channel.global_index] == pytest.approx(0.0)
    assert values.values[heater.unmet_thermal_demand_power_channel.global_index] == pytest.approx(0.0)
    assert values.values[heater.water_output_temperature_channel.global_index] == pytest.approx(10.0)


def test_connected_cold_water_temperature_input_is_used() -> None:
    """A connected mains temperature of 25 °C shrinks the lift to 15 K.

    Q = 0.0496 kg/s * 4180 J/(kg K) * 15 K = 3109.92 W, half of the 10 °C case, and
    the outlet still reaches the 40 °C set temperature.
    """
    heater = _make_heater(cold_water_temperature_in_celsius=10.0)
    values = _simulate_one_timestep(
        heater, water_consumption_in_liter=3.0, cold_water_temperature_in_celsius=25.0
    )

    expected_thermal_power = 3.0 * _DENSITY / _SECONDS_PER_TIMESTEP * _CP_WATER_IN_JOULE_PER_KG_PER_KELVIN * 15.0

    assert values.values[heater.thermal_output_power_channel.global_index] == pytest.approx(
        expected_thermal_power
    )
    assert values.values[heater.water_output_temperature_channel.global_index] == pytest.approx(40.0)


def test_unconnected_cold_water_temperature_input_falls_back_to_the_config() -> None:
    """An unconnected optional input must not be read as 0 °C.

    ``SingleTimeStepValues.get_input_value`` returns 0 for an unconnected input, so
    without the explicit ``source_output is None`` guard the device would heat from
    0 °C instead of the configured 10 °C and overstate the electricity demand by a
    third (40 K lift instead of 30 K).
    """
    heater = _make_heater(cold_water_temperature_in_celsius=10.0)
    values = _simulate_one_timestep(heater, water_consumption_in_liter=3.0)

    heat_capacity_flow = 3.0 * _DENSITY / _SECONDS_PER_TIMESTEP * _CP_WATER_IN_JOULE_PER_KG_PER_KELVIN

    assert values.values[heater.thermal_output_power_channel.global_index] == pytest.approx(
        heat_capacity_flow * 30.0
    )
    assert values.values[heater.water_output_temperature_channel.global_index] == pytest.approx(40.0)


def test_mains_water_above_set_temperature_keeps_the_device_off() -> None:
    """If the mains water already exceeds the set temperature the device does not fire.

    The water is passed through unchanged; the heater must never act as a cooler.
    """
    heater = _make_heater(set_water_temperature_in_celsius=40.0)
    values = _simulate_one_timestep(
        heater, water_consumption_in_liter=3.0, cold_water_temperature_in_celsius=45.0
    )

    assert values.values[heater.thermal_output_power_channel.global_index] == pytest.approx(0.0)
    assert values.values[heater.electric_output_power_channel.global_index] == pytest.approx(0.0)
    assert values.values[heater.water_output_temperature_channel.global_index] == pytest.approx(45.0)
    assert values.values[heater.water_output_mass_flow_rate_channel.global_index] == pytest.approx(
        3.0 * _DENSITY / _SECONDS_PER_TIMESTEP
    )
    assert values.values[heater.unmet_thermal_demand_power_channel.global_index] == pytest.approx(0.0)


def test_repeated_iterations_within_a_timestep_are_stable() -> None:
    """The component is stateless, so a second iteration reproduces the same outputs.

    The simulator iterates each timestep until convergence; a device whose outputs
    depend on a hidden state would never settle.
    """
    heater = _make_heater()
    values = _simulate_one_timestep(heater, water_consumption_in_liter=3.0)
    first_pass = list(values.values)

    heater.i_simulate(0, values, False)

    assert values.values == pytest.approx(first_pass)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_default_config_matches_the_lpg_warm_water_definition() -> None:
    """The default set/cold temperatures must match the LPG warm-water reference points.

    ``SimpleDHWStorage`` interprets the LPG water volumes as 40 °C water drawn from
    10 °C mains water. The tankless heater has to use the same reference points or
    the two DHW paths would not be interchangeable in a scenario.
    """
    config = TanklessWaterHeaterConfig.get_default_tankless_water_heater_config()

    assert config.set_water_temperature_in_celsius == pytest.approx(40.0)
    assert config.cold_water_temperature_in_celsius == pytest.approx(10.0)


def test_scaled_config_gives_each_apartment_its_own_device() -> None:
    """Three apartments get three times the rated power of a single device."""
    single = TanklessWaterHeaterConfig.get_default_tankless_water_heater_config()
    scaled = TanklessWaterHeaterConfig.get_scaled_tankless_water_heater_config(number_of_apartments=3)

    assert scaled.maximum_electric_power_in_watt == pytest.approx(
        single.maximum_electric_power_in_watt * 3
    )


def test_scaled_config_rejects_a_non_positive_apartment_count() -> None:
    """Scaling to zero apartments is a configuration error, not a 0 W device."""
    with pytest.raises(ValueError, match="number_of_apartments"):
        TanklessWaterHeaterConfig.get_scaled_tankless_water_heater_config(number_of_apartments=0)


@pytest.mark.parametrize(
    "kwargs, expected_message",
    [
        ({"maximum_electric_power_in_watt": 0.0}, "maximum electric power"),
        ({"efficiency": 0.0}, "efficiency"),
        ({"efficiency": 1.5}, "efficiency"),
        (
            {"set_water_temperature_in_celsius": 10.0, "cold_water_temperature_in_celsius": 10.0},
            "cannot provide cooling",
        ),
    ],
)
def test_invalid_configurations_are_rejected_at_construction(kwargs, expected_message) -> None:
    """Physically impossible parameter sets fail fast instead of producing silent nonsense."""
    with pytest.raises(ValueError, match=expected_message):
        _make_heater(**kwargs)


# ---------------------------------------------------------------------------
# Post-processing: OPEX and KPIs
# ---------------------------------------------------------------------------


def _make_ems() -> L2GenericEnergyManagementSystem:
    """Build an EMS with its default dynamic connections registered."""
    ems: L2GenericEnergyManagementSystem = L2GenericEnergyManagementSystem(
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
        config=EMSConfig.get_default_config_ems(),
    )
    return ems


def _connect_heater_to_ems(ems: L2GenericEnergyManagementSystem) -> None:
    """Wire the heater into the EMS the way connect_everything_automatically would."""
    connection = _heater_connections(ems)[0]
    ems.add_component_input_and_connect(
        source_object_name=TanklessWaterHeater.get_classname(),
        source_component_output=connection.source_component_field_name,
        source_load_type=connection.source_load_type,
        source_unit=connection.source_unit,
        source_tags=connection.source_tags,
        source_weight=connection.source_weight,
    )


def _watt_output_index(all_outputs: List[cp.ComponentOutput], field_name: str) -> int:
    """Find the position of a WATT output by field name."""
    for index, output in enumerate(all_outputs):
        if output.unit == lt.Units.WATT and output.field_name == field_name:
            return index
    raise AssertionError(f"Expected output {field_name} not found in all_outputs")


def _results_frame(
    all_outputs: List[cp.ComponentOutput],
    columns: dict,
    number_of_timesteps: int,
) -> pd.DataFrame:
    """Build a results frame aligned with ``all_outputs``, zero-filling everything else."""
    data: dict = {i: ([0.0] * number_of_timesteps) for i in range(len(all_outputs))}
    for field_name, series in columns.items():
        data[_watt_output_index(all_outputs, field_name)] = list(series)
    return pd.DataFrame(data)


def _hourly_heater() -> TanklessWaterHeater:
    """A heater on a one-hour timestep so W -> kWh is a plain factor of 1e-3."""
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    return TanklessWaterHeater(
        my_simulation_parameters=my_simulation_parameters,
        config=TanklessWaterHeaterConfig.get_default_tankless_water_heater_config(),
        my_display_config=cp.DisplayConfig(display_in_webtool=True),
    )


def test_get_cost_opex_attributes_all_consumption_to_domestic_hot_water() -> None:
    """2000 W over two one-hour steps is 4 kWh, all of it DHW and none space heating."""
    heater = _hourly_heater()
    all_outputs = list(heater.outputs)
    results = _results_frame(
        all_outputs, {heater.ElectricOutputDhwPower: [2000.0, 2000.0]}, number_of_timesteps=2
    )

    opex = heater.get_cost_opex(all_outputs=all_outputs, postprocessing_results=results)

    assert opex.total_consumption_in_kwh == pytest.approx(4.0)
    assert opex.consumption_for_domestic_hot_water_in_kwh == pytest.approx(4.0)
    assert opex.consumption_for_space_heating_in_kwh == pytest.approx(0.0)
    assert opex.loadtype == lt.LoadTypes.ELECTRICITY
    assert opex.kpi_tag == KpiTagEnumClass.TANKLESS_WATER_HEATER


def test_get_cost_opex_raises_when_the_electric_output_is_missing() -> None:
    """A missing electric-power column raises ``ValueError`` naming the output.

    An explicit raise rather than a bare ``assert`` so the check survives ``python -O``.
    """
    heater = _hourly_heater()
    all_outputs = [
        output for output in heater.outputs if output.field_name != heater.ElectricOutputDhwPower
    ]
    results = pd.DataFrame({i: [0.0, 0.0] for i in range(len(all_outputs))})

    with pytest.raises(ValueError, match=heater.ElectricOutputDhwPower):
        heater.get_cost_opex(all_outputs=all_outputs, postprocessing_results=results)


def test_kpi_entries_report_peak_power_and_unmet_demand() -> None:
    """Sizing KPIs: the peak electric draw and how often the rating fell short.

    Electric power peaks at 21000 W (21 kW). Unmet thermal power is non-zero in one
    of four timesteps, i.e. 25 % of the period, totalling 1000 W * 1 h = 1 kWh.
    """
    heater = _hourly_heater()
    all_outputs = list(heater.outputs)
    results = _results_frame(
        all_outputs,
        {
            heater.ElectricOutputDhwPower: [0.0, 5000.0, 21_000.0, 0.0],
            heater.ThermalOutputDhwPower: [0.0, 5000.0, 21_000.0, 0.0],
            heater.UnmetThermalDemandPower: [0.0, 0.0, 1000.0, 0.0],
        },
        number_of_timesteps=4,
    )

    kpis = {entry.name: entry.value for entry in heater.get_component_kpi_entries(all_outputs, results)}

    assert kpis["Maximum electric power"] == pytest.approx(21.0)
    assert kpis["Unmet thermal energy for domestic hot water"] == pytest.approx(1.0)
    assert kpis["Share of timesteps with unmet domestic hot water demand"] == pytest.approx(25.0)
    assert kpis["Thermal energy delivered for domestic hot water"] == pytest.approx(26.0)
    assert kpis["Energy consumption for domestic hot water"] == pytest.approx(26.0)


# ---------------------------------------------------------------------------
# Default connections into the electricity meter and the EMS
# ---------------------------------------------------------------------------


def _heater_connections(component) -> List:
    """Return the dynamic default connections a component declares for the heater."""
    connections = component.dynamic_default_connections.get(TanklessWaterHeater.get_classname(), [])
    return list(connections)


def test_electricity_meter_declares_a_default_connection_from_the_heater() -> None:
    """The meter must pick up the heater's electricity when auto-connected."""
    meter = ElectricityMeter(
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
        config=ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    connections = _heater_connections(meter)

    assert len(connections) == 1
    assert connections[0].source_component_field_name == TanklessWaterHeater.ElectricOutputDhwPower
    assert lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED in connections[0].source_tags


def test_ems_declares_the_heater_as_an_uncontrolled_consumer() -> None:
    """The EMS registers the heater at weight 999 with the UNCONTROLLED tag.

    Both are required by the convention stated in the ``L2GenericEnergyManagementSystem``
    docstring: non-controllable consumption is recognised by the CONSUMPTION_UNCONTROLLED
    flag together with source weight 999. A Durchlauferhitzer fires when a tap opens and
    has no storage to shift into, so it is genuinely non-controllable.
    """
    ems = _make_ems()

    connections = _heater_connections(ems)

    assert len(connections) == 1
    assert connections[0].source_component_field_name == TanklessWaterHeater.ElectricOutputDhwPower
    assert lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED in connections[0].source_tags
    assert connections[0].source_weight == 999


def test_ems_sorts_the_heater_into_uncontrolled_consumption() -> None:
    """Once wired, the heater lands in the uncontrolled bucket, not the dispatchable one.

    That bucket is subtracted from production before the surplus is distributed, which is
    the correct treatment for a load the EMS cannot defer.
    """
    ems = _make_ems()
    _connect_heater_to_ems(ems)

    (*_, consumption_uncontrolled, consumption_ems_controlled) = ems.sort_source_weights_and_components()[3:]

    assert len(consumption_uncontrolled) == 1
    assert not consumption_ems_controlled


def test_ems_sorting_survives_the_heater_alongside_a_dispatchable_consumer() -> None:
    """Adding the heater must not break target resolution for controllable devices.

    ``sort_source_weights_and_components`` raises when a dynamic input other than weight
    999 has no matching ELECTRICITY_TARGET output. Registering the heater at any weight
    below 999 would therefore blow up the whole EMS, because no target output is declared
    for it. This pins that the 999 choice keeps a normal, dispatchable consumer working.
    """
    ems = _make_ems()
    _connect_heater_to_ems(ems)
    # The occupancy is EMS-controlled at weight 1; the EMS declared its matching
    # ElectricityToOrFromGridOfUtspLpgConnector_ target output during construction.
    ems.add_component_input_and_connect(
        source_object_name="UtspLpgConnector",
        source_component_output="ElectricalPowerConsumption",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.RESIDENTS, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
        source_weight=1,
    )

    (_, _, outputs_sorted, _, consumption_uncontrolled, consumption_ems_controlled) = (
        ems.sort_source_weights_and_components()
    )

    assert len(consumption_uncontrolled) == 1
    assert len(consumption_ems_controlled) == 1
    assert outputs_sorted, "the dispatchable consumer must still resolve to a target output"


def test_kpi_entries_are_all_tagged_for_the_tankless_water_heater() -> None:
    """Every KPI must carry the dedicated tag so the report groups them separately."""
    heater = _hourly_heater()
    all_outputs = list(heater.outputs)
    results = _results_frame(
        all_outputs, {heater.ElectricOutputDhwPower: [1000.0, 1000.0]}, number_of_timesteps=2
    )

    entries = heater.get_component_kpi_entries(all_outputs, results)

    assert entries
    assert all(entry.tag == KpiTagEnumClass.TANKLESS_WATER_HEATER for entry in entries)
    assert all(entry.description == heater.component_name for entry in entries)
