"""Test for advanced fuel cell."""

# clean

import json
import math
from typing import NamedTuple

import pandas as pd
import pytest

from hisim import component as cp
from hisim.components import advanced_fuel_cell
from hisim import loadtypes as lt
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.config import ComponentID
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass
from tests import functions_for_testing as fft


class _ChpTestSetup(NamedTuple):
    """A wired-up CHP system under test plus its fake input channels."""

    chp: advanced_fuel_cell.CHP
    stsv: cp.SingleTimeStepValues
    control_signal: cp.ComponentOutput
    massflow_input_temperature: cp.ComponentOutput
    electricity_target: cp.ComponentOutput


def build_chp_system(
    operating_mode: str,
    gas_type: str = "Hydrogen",
    seconds_per_timestep: int = 60,
) -> _ChpTestSetup:
    """Build and wire a CHP system with fake control, mass-flow, and target inputs.

    Constructs an ``advanced_fuel_cell.CHP`` from the default config with the
    requested ``operating_mode`` and ``gas_type``, connects three fake
    ``ComponentOutput`` sources to its input channels, allocates a
    ``SingleTimeStepValues`` buffer, and assigns global indices. The returned
    fake outputs' ``global_index`` attributes can be used to inject input
    values before calling ``chp.i_simulate``.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    my_chp_system_config = advanced_fuel_cell.CHPConfig.get_default_config()
    my_chp_system_config.operating_mode = operating_mode
    my_chp_system_config.gas_type = gas_type

    my_chp_system = advanced_fuel_cell.CHP(
        config=my_chp_system_config, my_simulation_parameters=my_simulation_parameters
    )

    # Set Fake Outputs for CHP System
    control_signal = cp.ComponentOutput(
        "FakeControlSignal",
        "ControlSignal",
        lt.LoadTypes.ANY,
        lt.Units.PERCENT,
        component_id=ComponentID("FakeControlSignal"),
    )
    massflow_input_temperature = cp.ComponentOutput(
        "FakeMassflowInputTemperature",
        "MassflowInputTemperature",
        lt.LoadTypes.WATER,
        lt.Units.CELSIUS,
        component_id=ComponentID("FakeMassflowInputTemperature"),
    )
    electricity_from_chp_target = cp.ComponentOutput(
        "FakeElectricityFromCHPTarget",
        "ElectricityFromCHPTarget",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
        component_id=ComponentID("FakeElectricityFromCHPTarget"),
    )

    my_chp_system.control_signal_channel.source_output = control_signal
    my_chp_system.mass_inp_temp_channel.source_output = massflow_input_temperature
    my_chp_system.electricity_target_channel.source_output = electricity_from_chp_target

    components = [
        control_signal,
        massflow_input_temperature,
        electricity_from_chp_target,
        my_chp_system,
    ]
    number_of_outputs = fft.get_number_of_outputs(components)
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index for fake Inputs
    fft.add_global_index_of_components(components)

    return _ChpTestSetup(
        chp=my_chp_system,
        stsv=stsv,
        control_signal=control_signal,
        massflow_input_temperature=massflow_input_temperature,
        electricity_target=electricity_from_chp_target,
    )


@pytest.mark.base
def test_chp_system() -> None:
    """Test the advanced fuel cell CHP system simulation outputs.

    Configures an advanced fuel cell CHP system with methane fuel, electricity-led
    operating mode, and specific operational constraints (min operation time 60 s,
    min idle time 15 s, max electrical power 3 kW). Provides fake control, mass-flow
    input temperature, and electricity-target signals, then simulates a single
    timestep and asserts that mass flow, output temperature, gas demand, electrical
    power, thermal power, and cycle count match expected values.
    """
    setup = build_chp_system(operating_mode="electricity", gas_type="Methan")

    setup.stsv.values[setup.control_signal.global_index] = 0
    setup.stsv.values[setup.massflow_input_temperature.global_index] = 50
    setup.stsv.values[setup.electricity_target.global_index] = 300

    timestep = 100

    # Simulate
    setup.chp.i_simulate(timestep, setup.stsv, False)
    log.information(str(setup.stsv.values))

    # Check if the delivered electricity demand got produced by chp
    #
    assert setup.stsv.values[setup.chp.mass_out_channel.global_index] == 0.011
    assert setup.stsv.values[setup.chp.mass_out_temp_channel.global_index] == 82.6072779444372
    assert setup.stsv.values[setup.chp.gas_demand_target_channel.global_index] == 9.994428193341691e-05
    assert setup.stsv.values[setup.chp.el_power_channel.global_index] == 400.0
    assert setup.stsv.values[setup.chp.number_of_cycles_channel.global_index] == 1
    assert setup.stsv.values[setup.chp.th_power_channel.global_index] == 1500.0
    assert setup.stsv.values[setup.chp.gas_demand_real_used_channel.global_index] == 9.994428193341691e-05


@pytest.mark.base
def test_chp_raises_value_error_for_out_of_range_control_signal() -> None:
    """A control signal outside [0, 1] in heat-led mode raises ValueError.

    In ``heat`` operating mode the control signal is taken verbatim from the
    connected input channel (no clamping), so an out-of-range value must be
    rejected with a specific, catchable exception rather than the bare
    ``Exception`` base class.
    """
    setup = build_chp_system(operating_mode="heat")

    setup.stsv.values[setup.control_signal.global_index] = 1.5  # out of range (> 1)
    setup.stsv.values[setup.massflow_input_temperature.global_index] = 50
    setup.stsv.values[setup.electricity_target.global_index] = 300

    with pytest.raises(ValueError, match="control signal between 0 and 1"):
        setup.chp.i_simulate(100, setup.stsv, False)


@pytest.mark.base
@pytest.mark.parametrize(
    ("target", "expected"),
    [
        # Below the shutdown threshold -> control signal 0.
        (0.0, 0),
        (29.0, 0),
        # Static deadband [30, p_el_min * eff_el_min) -> fixed 0.4 signal.
        (30.0, 0.4),
        (200.0, 0.4),
        (399.0, 0.4),
        # Above the saturation point p_el_max * eff_el_max -> full output 1.
        (1200.1, 1),
        (2000.0, 1),
    ],
)
def test_calculate_control_signal_branches(target: float, expected: float) -> None:
    """calculate_control_signal returns the documented early-return values.

    Covers the three guard branches (shutdown, deadband, saturation) that the
    hoisted implementation must preserve unchanged.
    """
    setup = build_chp_system(operating_mode="electricity")
    setup.stsv.values[setup.electricity_target.global_index] = target
    assert setup.chp.calculate_control_signal(setup.stsv) == expected


@pytest.mark.base
@pytest.mark.parametrize("target", [400.0, 500.0, 800.0, 1000.0, 1200.0])
def test_calculate_control_signal_quadratic_matches_reference(target: float) -> None:
    """The hoisted quadratic solution is bit-identical to the original formula.

    The refactor only caches invariants (input lookup, p_el_max, the efficiency
    span, the discriminant/square root, the denominator); the arithmetic and
    its evaluation grouping are unchanged. This locks that invariant in by
    comparing against the original un-hoisted expression for targets in the
    modulating region [p_el_min * eff_el_min, p_el_max * eff_el_max].
    """
    setup = build_chp_system(operating_mode="electricity")
    chp = setup.chp
    setup.stsv.values[setup.electricity_target.global_index] = target

    p_el_max = chp.p_el_max
    eff_el_min = chp.eff_el_min
    eff_el_max = chp.eff_el_max
    d_eff = eff_el_max - eff_el_min

    # Reference: the original pre-hoisting arithmetic (identical grouping).
    discriminant = (p_el_max * eff_el_min) ** 2 + 4 * (target * p_el_max * d_eff)
    sqrt_disc = math.sqrt(discriminant)
    denom = 2 * p_el_max * d_eff
    x_1 = (-p_el_max - sqrt_disc) / denom
    x_2 = (-p_el_max + sqrt_disc) / denom
    if 0 < x_1 < 1:
        if 0 < x_2 < 1:
            expected = x_2 if x_1 < x_2 else x_1
        else:
            expected = x_1
    else:
        expected = x_2

    assert chp.calculate_control_signal(setup.stsv) == expected


@pytest.mark.base
def test_chp_state_defaults() -> None:
    """Class CHPState initialises to zero-valued int fields when given no arguments.

    The original untyped signature used ``None`` defaults for
    ``start_timestep`` and ``cycle_number``.  These were changed to ``int``
    defaults (0) because the fields participate in arithmetic and are
    assigned to int counters without null checks, so ``None`` would raise
    TypeError at runtime.  All production callers pass explicit values, but
    CHPState is a module-level class that downstream code may instantiate
    with no arguments, so the zero defaults must be verified.
    """
    state = advanced_fuel_cell.CHPState()
    assert state.start_timestep == 0
    assert state.electricity_output == 0.0
    assert state.cycle_number == 0
    assert state.activation == 0

    # Positive electricity output activates the CHP.
    active = advanced_fuel_cell.CHPState(start_timestep=5, electricity_output=400.0, cycle_number=2)
    assert active.start_timestep == 5
    assert active.electricity_output == 400.0
    assert active.cycle_number == 2
    assert active.activation == 1

    # Negative electricity output is rejected.
    with pytest.raises(ValueError, match="Impossible CHPState"):
        advanced_fuel_cell.CHPState(electricity_output=-1.0)


@pytest.mark.base
def test_chp_kpi_entries_integrate_the_period() -> None:
    """The four CHP KPIs integrate power to energy, mass flow to mass, and read the final cycle count.

    Two timesteps of 60 s at 3000 W electrical and 4000 W thermal are 0.1 kWh electrical and
    (4000+4000)*60/3600/1000 kWh thermal; a fuel draw of 0.001 kg/s over both steps is 0.12 kg; a
    cycle counter ending at 2 reports 2 cycles. The values are computed here by hand, so a broken
    integration cannot agree with itself.

    A foreign component's 'ElectricityOutput' column in watt is prepended the way the real caller
    hands the whole run's outputs over, because the component_name filter is what keeps the CHP
    from summing another device's power — a mutation dropping that filter must fail here, not in
    a live setup.
    """
    setup = build_chp_system(operating_mode="electricity", gas_type="Methan")
    chp = setup.chp
    foreign = cp.ComponentOutput(
        "PVSystem",
        "ElectricityOutput",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
        component_id=ComponentID(name="PVSystem"),
    )
    outputs = [
        foreign,
        chp.el_power_channel,
        chp.th_power_channel,
        chp.gas_demand_real_used_channel,
        chp.number_of_cycles_channel,
    ]
    frame = pd.DataFrame(
        {
            0: [5000.0, 5000.0],
            1: [3000.0, 3000.0],
            2: [4000.0, 4000.0],
            3: [0.001, 0.001],
            4: [1.0, 2.0],
        }
    )

    entries = {entry.name: entry for entry in chp.get_component_kpi_entries(outputs, frame)}

    assert entries["Electrical energy produced"].value == pytest.approx(round(3000.0 * 2 * 60 / 3600 / 1000, 3))
    assert entries["Thermal energy produced"].value == pytest.approx(round(4000.0 * 2 * 60 / 3600 / 1000, 3))
    assert entries["Fuel consumed"].value == pytest.approx(round(0.001 * 2 * 60, 6))
    assert entries["Number of activation cycles"].value == 2.0
    assert all(entry.tag is KpiTagEnumClass.CHP for entry in entries.values())
    assert all(entry.name_of_source_component == chp.component_name for entry in entries.values()), (
        "the source component is the disambiguator a future multi-CHP collision fix keys on"
    )
    for entry in entries.values():
        json.dumps(entry.to_dict())  # the webtool writer serializes exactly this; it must not raise


@pytest.mark.base
def test_chp_kpi_entries_refuse_a_missing_output() -> None:
    """A KPI whose output column is absent raises naming the KPI instead of reporting nothing.

    Catches: an output dropped from the run's column list silently dropping its KPI. (A consistent
    rename would update the matcher and the channel together — both read the same ClassVar — so a
    rename is not what this guards; absence is.)
    """
    setup = build_chp_system(operating_mode="electricity", gas_type="Methan")
    chp = setup.chp

    with pytest.raises(ValueError, match="Electrical energy produced"):
        chp.get_component_kpi_entries([chp.th_power_channel], pd.DataFrame({0: [4000.0]}))


@pytest.mark.base
def test_chp_kpi_entries_refuse_nan_instead_of_understating() -> None:
    """A NaN in a KPI column raises instead of being silently dropped from the sum.

    pandas sums with skipna by default, so an all-NaN column is 0.0 and a partial NaN column
    understates the energy while looking exactly like a real value — the silent absence the
    missing-output guard exists to prevent, arriving through the values. Never reachable in a
    normal run (outputs initialize to 0.0), which is why it must be loud when it does happen.
    """
    setup = build_chp_system(operating_mode="electricity", gas_type="Methan")
    chp = setup.chp
    outputs = [
        chp.el_power_channel,
        chp.th_power_channel,
        chp.gas_demand_real_used_channel,
        chp.number_of_cycles_channel,
    ]
    frame = pd.DataFrame(
        {
            0: [3000.0, float("nan")],
            1: [4000.0, 4000.0],
            2: [0.001, 0.001],
            3: [1.0, 2.0],
        }
    )

    with pytest.raises(ValueError, match="Electrical energy produced"):
        chp.get_component_kpi_entries(outputs, frame)
