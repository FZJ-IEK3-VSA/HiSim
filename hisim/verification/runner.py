"""Running one case at one time step: the components, the two passes and the outputs (FR8, FR9, spec §3.2.11).

A run is two full-year passes of the same system: the first from the case's heating set point as
the initial temperature, the second from the thermal-mass temperature the first ends with, so that
the reported year starts in a settled state (decision D3). The system is the weather, the building,
a constant internal gain, the ideal heater with its controller (none in a free-float case) and the
night-setback controller of the setback cases. Every quantity the reports need is written beside the
HiSim result directories: the derived parameters, the time series of the second pass and a summary.
"""

import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from hisim import loadtypes as lt
from hisim import simulator as sim
from hisim.components import idealized_electric_heater, weather
from hisim.components.building import Building
from hisim.components.csvloader import CSVLoader, CSVLoaderConfig
from hisim.components.night_setback_controller import NightSetbackController
from hisim.hisim_commit import HiSimCommit
from hisim.simulationparameters import SimulationParameters
from hisim.verification import aggregate, metrics
from hisim.verification.suite import RequestedOutputs
from hisim.verification.system_builder import BuiltCase

#: Operative temperature of the 5R1C model: 0.3 air plus 0.7 internal surface (ISO 13790, equation C.12).
OPERATIVE_AIR_WEIGHT: float = 0.3
OPERATIVE_SURFACE_WEIGHT: float = 0.7

#: The passes of a run: the second starts from the mass temperature the first ends with.
PASSES: int = 2

#: Instance names of the components, which the result columns are found by.
WEATHER_NAME = "Weather"
BUILDING_NAME = "Building"
GAINS_NAME = "InternalGains"
HEATER_NAME = "IdealizedHeater"
CONTROLLER_NAME = "IdealizedHeaterController"
SETBACK_NAME = "NightSetback"

#: The columns of the exported time series: (component, output, name in the export).
EXPORTED_COLUMNS: Tuple[Tuple[str, str, str], ...] = (
    (WEATHER_NAME, weather.Weather.TemperatureOutside, "outdoor_air_temperature_in_celsius"),
    (BUILDING_NAME, Building.TemperatureIndoorAir, metrics.AIR_TEMPERATURE_COLUMN),
    (BUILDING_NAME, Building.TemperatureInternalSurface, "internal_surface_temperature_in_celsius"),
    (BUILDING_NAME, Building.TemperatureMeanThermalMass, metrics.MASS_TEMPERATURE_COLUMN),
    (BUILDING_NAME, Building.SolarGainThroughWindows, metrics.SOLAR_GAIN_COLUMN),
    (BUILDING_NAME, Building.TheoreticalThermalBuildingDemand, "theoretical_thermal_demand_in_watt"),
    (HEATER_NAME, idealized_electric_heater.IdealizedElectricHeater.ThermalPowerDelivered, aggregate.DELIVERED_POWER_COLUMN),
    (SETBACK_NAME, NightSetbackController.BuildingTemperatureModifierInKelvin, "heating_set_point_modifier_in_kelvin"),
)


@dataclass(frozen=True)
class RunSettings:
    """What a run is told: the year, the time step and where to write."""

    year: int
    seconds_per_timestep: int
    output_directory: Path


@dataclass(frozen=True)
class PassResult:
    """One full-year pass: where it started, where its mass ended, and its time series."""

    index: int
    initial_temperature_in_celsius: float
    final_thermal_mass_temperature_in_celsius: float
    result_directory: str
    frame: pd.DataFrame


@dataclass(frozen=True)
class CaseRun:
    """The outcome of one case at one time step, with the files it was written to."""

    case_id: str
    seconds_per_timestep: int
    passes: Tuple[PassResult, ...]
    summary: Dict[str, Any]
    files: Dict[str, Path]


class Assembled:
    """The components of one pass, wired, in a simulator ready to run."""

    def __init__(self, built: BuiltCase, parameters: SimulationParameters, initial_temperature_in_celsius: float) -> None:
        """Build and wire every component of the case.

        Args:
            built: the configurations of the case.
            parameters: the simulation parameters of this pass, with its result directory set.
            initial_temperature_in_celsius: where the building's air and mass start.
        """
        self.simulator = sim.Simulator(
            module_directory=str(Path(parameters.result_directory).parent),
            module_filename=f"verification_case{built.case.case_id.lower()}",
            my_simulation_parameters=parameters,
        )
        self.simulator.set_simulation_parameters(parameters)
        building_config = dataclasses.replace(built.building, initial_internal_temperature_in_celsius=initial_temperature_in_celsius)
        self.weather = weather.Weather(config=built.weather, my_simulation_parameters=parameters)
        self.building = Building(config=building_config, my_simulation_parameters=parameters)
        self.gains = CSVLoader(
            config=CSVLoaderConfig.for_csv_file(
                GAINS_NAME,
                csv_filename="constant_internal_gains_of_the_case",
                column=0,
                loadtype=lt.LoadTypes.HEATING,
                unit=lt.Units.WATT,
                column_name="internal_gains_in_watt",
            ),
            my_simulation_parameters=parameters,
            dataframe=pd.DataFrame({"internal_gains_in_watt": [built.case.internal_gains_in_watt] * parameters.timesteps}),
        )
        self._wire_weather()
        self.building.connect_input(self.building.HeatingByDevices, self.gains.component_name, CSVLoader.Output1)
        for component in (self.weather, self.gains, self.building):
            self.simulator.add_component(component)

        self.heater: Optional[idealized_electric_heater.IdealizedElectricHeater] = None
        if not built.case.free_float:
            self.heater = idealized_electric_heater.IdealizedElectricHeater(
                my_simulation_parameters=parameters,
                config=idealized_electric_heater.IdealizedHeaterConfig.preset_standard(HEATER_NAME),
            )
            controller = idealized_electric_heater.IdealizedElectricHeaterController(
                my_simulation_parameters=parameters,
                config=idealized_electric_heater.IdealizedHeaterControllerConfig.preset_standard(CONTROLLER_NAME),
            )
            controller.connect_input(controller.TheoreticalThermalBuildingDemand, self.building.component_name, self.building.TheoreticalThermalBuildingDemand)
            self.heater.connect_input(self.heater.ThermalPowerSetInWatt, controller.component_name, controller.ThermalPowerSetInWatt)
            self.building.connect_input(self.building.ThermalPowerDelivered, self.heater.component_name, self.heater.ThermalPowerDelivered)
            self.simulator.add_component(controller)
            self.simulator.add_component(self.heater)

        self.setback: Optional[NightSetbackController] = None
        if built.setback is not None:
            self.setback = NightSetbackController(my_simulation_parameters=parameters, config=built.setback)
            self.building.connect_input(self.building.BuildingTemperatureModifierInKelvin, self.setback.component_name, self.setback.BuildingTemperatureModifierInKelvin)
            self.simulator.add_component(self.setback)

    def _wire_weather(self) -> None:
        """Connect the eight weather inputs of the building."""
        for building_input, weather_output in (
            (self.building.Altitude, self.weather.Altitude),
            (self.building.Azimuth, self.weather.Azimuth),
            (self.building.ApparentZenith, self.weather.ApparentZenith),
            (self.building.DirectNormalIrradiance, self.weather.DirectNormalIrradiance),
            (self.building.DirectNormalIrradianceExtra, self.weather.DirectNormalIrradianceExtra),
            (self.building.DiffuseHorizontalIrradiance, self.weather.DiffuseHorizontalIrradiance),
            (self.building.GlobalHorizontalIrradiance, self.weather.GlobalHorizontalIrradiance),
            (self.building.TemperatureOutside, self.weather.TemperatureOutside),
        ):
            self.building.connect_input(building_input, self.weather.component_name, weather_output)

    def run(self) -> pd.DataFrame:
        """Run every time step and return the exported columns, the operative temperature among them."""
        self.simulator.run_all_timesteps()
        results = self.simulator.results_data_frame
        exported: Dict[str, pd.Series] = {}
        for component, output, name in EXPORTED_COLUMNS:
            column = _find_column(results, component, output)
            if column is not None:
                exported[name] = results[column]
        frame = pd.DataFrame(exported, index=results.index)
        frame[metrics.OPERATIVE_TEMPERATURE_COLUMN] = (
            OPERATIVE_AIR_WEIGHT * frame[metrics.AIR_TEMPERATURE_COLUMN] + OPERATIVE_SURFACE_WEIGHT * frame["internal_surface_temperature_in_celsius"]
        )
        return frame


def _find_column(results: pd.DataFrame, component: str, output: str) -> Optional[str]:
    """The result column of one component output, named ``<component> - <output> [<load type> - <unit>]``."""
    prefix = f"{component} - {output} ["
    matches = [column for column in results.columns if str(column).startswith(prefix)]
    return matches[0] if matches else None


def run_case(built: BuiltCase, settings: RunSettings, requested: Optional[RequestedOutputs] = None) -> CaseRun:
    """Run one case at one time step in two passes and write parameters, time series and summary.

    The summary holds the quantities of Section 7.3 computed from the hours of the second pass
    (``metrics``).

    Args:
        built: the configurations of the case.
        settings: year, time step and output directory.
        requested: the hourly days and the temperature bins the suite asks of the case.

    Returns:
        CaseRun: the passes, the summary of the second pass and the files written.
    """
    case_id = built.case.case_id
    label = f"case{case_id.lower()}_{settings.seconds_per_timestep}s"
    passes: List[PassResult] = []
    initial_temperature = built.building.initial_internal_temperature_in_celsius
    for pass_index in range(1, PASSES + 1):
        result_directory = settings.output_directory / "hisim_runs" / f"{label}_pass{pass_index}"
        result_directory.mkdir(parents=True, exist_ok=True)
        parameters = SimulationParameters.full_year(year=settings.year, seconds_per_timestep=settings.seconds_per_timestep)
        parameters.result_directory = str(result_directory)
        assembled = Assembled(built, parameters, initial_temperature)
        frame = assembled.run()
        final_mass_temperature = float(assembled.building.state.thermal_mass_temperature_in_celsius)
        passes.append(PassResult(pass_index, initial_temperature, final_mass_temperature, str(result_directory), frame))
        initial_temperature = final_mass_temperature

    reported = passes[-1]
    summary = {
        "case": case_id,
        "year": settings.year,
        "hisim_commit": HiSimCommit.or_unknown(),
        "passes": [
            {
                "index": entry.index,
                "initial_temperature_in_celsius": entry.initial_temperature_in_celsius,
                "final_thermal_mass_temperature_in_celsius": entry.final_thermal_mass_temperature_in_celsius,
                "result_directory": entry.result_directory,
            }
            for entry in passes
        ],
        "reported_pass": reported.index,
        **metrics.case_metrics(reported.frame, settings.seconds_per_timestep, requested),
    }
    files = _write_outputs(built, settings, label, reported.frame, summary)
    return CaseRun(case_id=case_id, seconds_per_timestep=settings.seconds_per_timestep, passes=tuple(passes), summary=summary, files=files)


def _write_outputs(built: BuiltCase, settings: RunSettings, label: str, frame: pd.DataFrame, summary: Dict[str, Any]) -> Dict[str, Path]:
    """Write ``parameters/<case>.json``, ``timeseries/<label>.csv`` and ``summary/<label>.json`` under ``BESTEST_output``."""
    root = settings.output_directory / "BESTEST_output"
    files = {
        "parameters": root / "parameters" / f"case{built.case.case_id.lower()}.json",
        "timeseries": root / "timeseries" / f"{label}.csv",
        "summary": root / "summary" / f"{label}.json",
    }
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    with open(files["parameters"], "w", encoding="utf-8") as parameters_file:
        json.dump(built.parameters.to_json_dict(), parameters_file, indent=2)
    frame.to_csv(files["timeseries"], index_label="time")
    with open(files["summary"], "w", encoding="utf-8") as summary_file:
        json.dump(summary, summary_file, indent=2)
    return files


def write_guard_collects() -> None:
    """Switch the write guard to collecting unless the environment says otherwise (decision D8)."""
    os.environ.setdefault("HISIM_WRITE_GUARD", "collect")
