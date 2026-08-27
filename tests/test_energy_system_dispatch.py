"""The controlled-participant path of an aggregator, driven end to end from a file.

An energy management system does two things with a battery: it measures what the battery draws
and it tells the battery what to draw. The first is an ordinary feed. The second is the dispatch
back-channel — an output the aggregator grows for exactly this participant and a wire from that
output into the participant's own input — and it is the one part of the wiring stage that a
minimal household never exercises, because a meter only measures.

The tests here build the smallest system that has a controlled participant: a weather station,
an occupancy, a photovoltaic array, a battery, the energy management system that ranks them, and
the meter that measures the grid exchange. Neither the array nor the battery has presets yet, so
both are written as complete ``config`` blocks, which the format allows for exactly this reason.
The positive test runs a simulated day and looks at the wires and the results; the two negative
tests pin the rejections that make the rule unambiguous — a controlled participant whose signal
nobody consumes leaves a mandatory input unfed, and an author may not reach the derived output
from the participant's side, because that name is not in the file they are reading.

Each test states the failure mode it catches.
"""

# clean

from pathlib import Path
from typing import ClassVar

import pytest

from hisim.energy_system.errors import EnergySystemWiringError
from hisim.energy_system.executor import build_energy_system, run_energy_system
from hisim.simulationparameters import SimulationParameters


class Household:
    """The PV-battery-EMS household the tests vary one line at a time.

    The array and the battery are complete ``config`` blocks copied from their legacy default
    factories, so that the test depends on nothing the component sweep has yet to deliver. The
    aggregator's participants are written out in full — production, uncontrolled consumption and
    the controlled battery — because a controlled participant cannot be a bare item: the class's
    default feeds carry no dispatch target, and the storage channel requires one.
    """

    #: The battery's controlled feed as it must be written: the dispatch block names the input.
    BATTERY_FEED_WITH_TARGET: ClassVar[str] = """      - from: battery.AcBatteryPowerUsed
        component_type: BATTERY
        tags: [ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
        weight: 6
        dispatch: {target_input: LoadingPowerInput}
"""

    #: The same feed with a dispatch signal nobody consumes.
    BATTERY_FEED_WITHOUT_TARGET: ClassVar[str] = """      - from: battery.AcBatteryPowerUsed
        component_type: BATTERY
        tags: [ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
        weight: 6
        dispatch: {}
"""

    #: An author trying to wire the derived output from the battery's side.
    BATTERY_WIRE_ONTO_DERIVED_PORT: ClassVar[str] = """    inputs:
      - input: LoadingPowerInput
        from: ems.DispatchTobattery_LoadingPowerInput
"""

    @classmethod
    def text(cls, battery_feed: str, battery_inputs: str = "") -> str:
        """Assembles the file.

        Args:
            battery_feed: The battery's item in the aggregator's ``inputs`` list.
            battery_inputs: An optional ``inputs`` block on the battery entry itself.

        Returns:
            The complete energy-system text.
        """
        return f"""schema_version: 3
name: pv battery ems household
components:
  weather:
    class: hisim.components.weather.Weather
    preset: standard
  occupancy:
    class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector
    preset: standard
  pv:
    class: hisim.components.generic_pv_system.PVSystem
    config:
      time: 2019
      location: Aachen
      module_name: Trina Solar TSM-435NE09RC.05
      integrate_inverter: true
      inverter_name: "Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]"
      module_database: CEC_MODULE_DATABASE
      inverter_database: CEC_INVERTER_DATABASE
      power_in_watt: 10000.0
      azimuth: 180
      tilt: 30
      share_of_maximum_pv_potential: 1.0
      load_module_data: false
      source_weight: 0
      device_co2_footprint_in_kg: null
      investment_costs_in_euro: null
      lifetime_in_years: null
      maintenance_costs_in_euro_per_year: null
      subsidy_as_percentage_of_investment_costs: null
      predictive: false
      predictive_control: false
      prediction_horizon: null
    inputs:
      - weather
  battery:
    class: hisim.components.advanced_battery_bslib.Battery
    config:
      source_weight: 1
      system_id: SG1
      custom_pv_inverter_power_generic_in_watt: 5000.0
      custom_battery_capacity_generic_in_kilowatt_hour: 10
      charge_in_kwh: 0
      discharge_in_kwh: 0
      device_co2_footprint_in_kg: null
      investment_costs_in_euro: null
      lifetime_in_years: null
      maintenance_costs_in_euro_per_year: null
      subsidy_as_percentage_of_investment_costs: null
      lifetime_in_cycles: 5000.0
{battery_inputs}  ems:
    class: hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem
    preset: optimize_own_consumption
    inputs:
      - from: occupancy.ElectricalPowerConsumption
        tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        weight: 999
      - from: pv.ElectricityOutput
        component_type: PV
        tags: [ELECTRICITY_PRODUCTION]
        weight: 999
{battery_feed}  meter:
    class: hisim.components.electricity_meter.ElectricityMeter
    preset: standard
    inputs:
      - from: ems.TotalElectricityToOrFromGrid
        tags: [ELECTRICITY_PRODUCTION]
        weight: 999
"""

    @classmethod
    def write(cls, directory: Path, battery_feed: str, battery_inputs: str = "") -> Path:
        """Writes the file into a test directory.

        Args:
            directory: The test's temporary directory.
            battery_feed: See :meth:`text`.
            battery_inputs: See :meth:`text`.

        Returns:
            The path of the written file.
        """
        path = directory / "pv_battery_ems.energy_system.yaml"
        path.write_text(cls.text(battery_feed, battery_inputs), encoding="utf-8")
        return path

    #: The shipped one-day parameters, resolved from this file rather than from the working
    #: directory: the test suite is run from ``tests/`` in CI and from the repository root
    #: locally, so a relative path would find the file in only one of the two.
    SHIPPED_PARAMETERS: ClassVar[Path] = (
        Path(__file__).resolve().parents[1] / "energy_systems" / "one_day_15min.simulation.yaml"
    )

    @classmethod
    def parameters(cls, directory: Path) -> SimulationParameters:
        """One simulated day at a quarter-hour, writing into the test directory.

        Args:
            directory: The test's temporary directory.

        Returns:
            The parameters.
        """
        parameters = SimulationParameters.one_day_only(2021, 900)
        parameters.result_directory = str(directory / "results")
        return parameters


@pytest.mark.base
def test_a_controlled_battery_is_ranked_told_what_to_draw_and_simulated(tmp_path: Path) -> None:
    """Catches the dispatch back-channel existing on paper only.

    The wiring tests prove that a dispatch block grows an output; nothing else proves that the
    output reaches the participant and that the aggregator's weight-matched lookups find it at
    run time. If the back wire were missing, or the derived ports carried the wrong tags or
    weight, the battery would be ranked, never told anything, and the day would still simulate.
    """
    path = Household.write(tmp_path, Household.BATTERY_FEED_WITH_TARGET)
    parameters_path = Household.SHIPPED_PARAMETERS

    built = run_energy_system(path, parameters_path, result_directory=str(tmp_path / "results"))

    wires = {
        f"{wire.source_name}.{wire.source_output} -> {wire.target_name}.{wire.target_input}"
        for wire in built.wired.wires
    }
    assert "battery.AcBatteryPowerUsed -> ems.AcBatteryPowerUsedFrombattery" in wires
    assert "ems.DispatchTobattery_LoadingPowerInput -> battery.LoadingPowerInput" in wires
    ems = built.wired.component_of("ems")
    dispatch = next(port for port in ems.outputs if port.field_name == "DispatchTobattery_LoadingPowerInput")
    assert dispatch is not None
    resolved = dict(built.wired.resolved_feeds)["ems"]
    assert [connection.weight for connection in resolved if connection.dispatch is not None] == [6]
    assert (tmp_path / "results" / "finished.flag").is_file()
    assert list((tmp_path / "results").glob("*.csv")), "the run wrote no result table"


@pytest.mark.base
def test_a_controlled_battery_whose_signal_nobody_consumes_leaves_its_input_unfed(
    tmp_path: Path,
) -> None:
    """Catches a battery that is ranked and served but never told anything.

    ``dispatch: {}`` is legal — it records a signal — but the battery's power input is
    mandatory, so a file that stops there describes a battery that cannot work. The rejection
    has to name the battery and its input, because the author's mistake is on the aggregator's
    line, three entries away.
    """
    path = Household.write(tmp_path, Household.BATTERY_FEED_WITHOUT_TARGET)

    with pytest.raises(EnergySystemWiringError) as caught:
        build_energy_system(path, Household.parameters(tmp_path))

    message = str(caught.value)
    assert "battery" in message
    assert "LoadingPowerInput" in message


@pytest.mark.base
def test_an_author_may_not_wire_the_derived_dispatch_output_from_the_battery(
    tmp_path: Path,
) -> None:
    """Catches the derived port names leaking into hand-written files.

    The aggregator's outputs are named by a template the author never sees, so a wire that
    names one binds the file to an implementation detail; the rule is that the back-channel is
    written once, on the aggregator's feed. Both spellings at once would also feed one input
    twice.
    """
    path = Household.write(
        tmp_path,
        Household.BATTERY_FEED_WITH_TARGET,
        battery_inputs=Household.BATTERY_WIRE_ONTO_DERIVED_PORT,
    )

    with pytest.raises(EnergySystemWiringError) as caught:
        build_energy_system(path, Household.parameters(tmp_path))

    assert "DispatchTobattery_LoadingPowerInput" in str(caught.value)
