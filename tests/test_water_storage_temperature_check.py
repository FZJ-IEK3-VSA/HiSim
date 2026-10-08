"""The water storages check their temperature once per timestep, on the value the timestep converged to.

hisim-4g9.11: the gas building-sizer setup of an Irish 1950-66 detached house
(TABULA ``IE.N.SFH.04.Gen.ReEx.001.001``) aborted at 3600 s with "The water temperature in the DHW
water storage is with 93 degC way too high or too low." It did so at timestep 0, on an
intermediate iterate of the convergence loop: the boiler controller's first iteration answers the
zero-initialised storage temperature with a 70 K lift (its 1800 s start-up lockout is
``int(1800 / 3600) == 0`` timesteps long at an hourly resolution), the boiler adds that lift to the
storage's real 60 degC, and one hour of the resulting 130 degC flow mixes the vessel above 90 degC.
The next iteration discards that value, but the range check sat at the top of ``i_simulate`` and
read it. The check now runs in ``i_doublecheck``, on the converged value only.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import simple_water_storage
from hisim.config import ComponentID
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


def dhw_storage_with_fake_inputs(seconds_per_timestep: int) -> Any:
    """Build a 250 l DHW storage whose five inputs are fed by fake outputs, all zero."""
    parameters = SimulationParameters.one_day_only(2021, seconds_per_timestep)
    config = simple_water_storage.SimpleDHWStorageConfig(
        component_id=ComponentID(name="DHWStorage"), volume_heating_water_storage_in_liter=250.0
    )
    storage = simple_water_storage.SimpleDHWStorage(my_simulation_parameters=parameters, config=config)
    channels = [
        (storage.water_consumption_channel, lt.LoadTypes.WARM_WATER, lt.Units.LITER),
        (storage.water_temperature_heat_generator_input_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
        (storage.water_mass_flow_rate_heat_generator_input_channel, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC),
        (
            storage.water_temperature_secondary_heat_generator_input_channel,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
        ),
        (
            storage.water_mass_flow_rate_secondary_heat_generator_input_channel,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
        ),
    ]
    fakes: List[Any] = []
    for number, (channel, load_type, unit) in enumerate(channels):
        fake = cp.ComponentOutput(
            f"Fake{number}", f"Fake{number}", load_type, unit, component_id=ComponentID(f"Fake{number}")
        )
        channel.source_output = fake
        fakes.append(fake)
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, storage]))
    fft.add_global_index_of_components([*fakes, storage])
    return storage, stsv


@pytest.mark.base
def test_an_intermediate_iterate_above_the_range_does_not_abort_the_run() -> None:
    """What the loop computed in an earlier iteration of the timestep is no reason to fail."""
    storage, stsv = dhw_storage_with_fake_inputs(3600)
    storage.state.mean_water_temperature_in_celsius = 60.0
    storage.mean_water_temperature_in_water_storage_in_celsius = 93.25  # the discarded iterate

    storage.i_simulate(0, stsv, False)

    # The hour without flows only loses the tank's standby heat: the step starts from the state's 60 °C, not from
    # the discarded iterate, and ends a little below it.
    assert 59.8 < storage.mean_water_temperature_in_water_storage_in_celsius < 60.0
    storage.i_doublecheck(0, stsv)


@pytest.mark.base
@pytest.mark.parametrize("converged_temperature", [93.25, -0.5])
def test_a_converged_temperature_outside_the_range_still_fails(converged_temperature: float) -> None:
    """The check is kept, only moved: a converged value outside 0..90 degC fails the timestep."""
    storage, stsv = dhw_storage_with_fake_inputs(900)
    storage.mean_water_temperature_in_water_storage_in_celsius = converged_temperature

    with pytest.raises(ValueError, match="DHW water storage is with"):
        storage.i_doublecheck(0, stsv)


@pytest.mark.base
def test_the_space_heating_storage_checks_its_converged_temperature_too() -> None:
    """The buffer storage had the same check in the same place and moved with it."""
    parameters = SimulationParameters.one_day_only(2021, 3600)
    config = simple_water_storage.SimpleHotWaterStorageConfig.preset_buffer("Buffer")
    config.volume_heating_water_storage_in_liter = 200.0
    storage = simple_water_storage.SimpleHotWaterStorage(my_simulation_parameters=parameters, config=config)
    storage.mean_water_temperature_in_water_storage_in_celsius = 95.0

    with pytest.raises(ValueError, match="water storage is with 95.0"):
        storage.i_doublecheck(0, cp.SingleTimeStepValues(1))


def irish_1950s_gas_house() -> Dict[str, Any]:
    """Return the vendored mockup as the house of the report: built 1960, gas, no measures."""
    from hisim.renovisor.contract import ContractFiles

    document = copy.deepcopy(ContractFiles.request_mockup())
    document["house"]["building"]["construction_year"] = 1960  # TABULA IE.N.SFH.04.Gen.ReEx.001.001
    document["measures"] = []
    return document


@pytest.fixture(name="translated_house", scope="module")
def fixture_translated_house(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Translate the house once into the recorded gas building-sizer twin."""
    from hisim.renovisor.run import Calculation, ExitCode

    directory = tmp_path_factory.mktemp("dhw_timestep")
    request = directory / "request.json"
    request.write_text(json.dumps(irish_1950s_gas_house()), encoding="utf-8")
    output = directory / "translated"
    assert Calculation(request_path=request, output_directory=output).translate_only() == ExitCode.FINISHED
    (energy_system,) = output.glob("renovisor_*.energy_system.yaml")
    assert "IE.N.SFH.04.Gen.ReEx.001.001" in energy_system.read_text(encoding="utf-8")
    assert "hisim.components.generic_boiler.GenericBoiler" in energy_system.read_text(encoding="utf-8")
    return energy_system


@pytest.mark.system_setups
@pytest.mark.parametrize(
    "seconds_per_timestep",
    [
        900,
        1800,
        pytest.param(
            3600,
            marks=pytest.mark.xfail(
                strict=True,
                raises=ValueError,
                reason=(
                    "hisim-fxix.9: the hot-water tank conserves the boiler's heat, and an hour-long full-power charge "
                    "decided on the start temperature heats it past 90 °C"
                ),
            ),
        ),
    ],
)
def test_the_gas_house_keeps_its_storages_in_range_for_a_winter_week(
    translated_house: Path, tmp_path: Path, seconds_per_timestep: int
) -> None:
    """The first January week of 2019 runs at every resolution, both storages between 0 and 90 degC."""
    from hisim.energy_system.executor import run_energy_system

    parameters = tmp_path / "winter_week.simulation.yaml"
    parameters.write_text(
        "start_date: '2019-01-01T00:00:00'\n"
        "end_date: '2019-01-08T00:00:00'\n"
        f"seconds_per_timestep: {seconds_per_timestep}\n"
        "country: IE\n"
        "logging_level: 3\n"
        "post_processing_options: []\n",
        encoding="utf-8",
    )

    built = run_energy_system(translated_house, parameters, result_directory=str(tmp_path / "results"))

    results = built.simulator.results_data_frame
    for column in (
        "DHWStorage - WaterMeanTemperatureInStorage [Temperature - °C]",
        "SimpleHotWaterStorage - WaterMeanTemperatureInStorage [Temperature - °C]",
    ):
        assert len(results[column]) == 7 * 24 * 3600 // seconds_per_timestep
        assert results[column].between(0.0, 90.0).all(), column
