"""Test for gas meter."""

import dataclasses
import os
import json
from typing import Dict, List, Optional, Tuple
import pytest
import numpy as np
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.config import ComponentID, SizingContext, concrete
from hisim.energy_port import EnergyPort
from hisim.components import heating_curve
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import (
    building,
    electricity_meter,
    gas_meter,
    generic_boiler,
    simple_water_storage,
    heat_distribution_system,
    generic_pv_system,
)
from hisim import utils

from hisim.postprocessingoptions import PostProcessingOptions
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder
from hisim import log
from tests import functions_for_testing as fft


# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_for_test_gas_meter.py"


@utils.measure_execution_time
@pytest.mark.extendedbase
def test_house(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Run a full-year simulation with a gas boiler and verify gas meter KPIs.

    Builds a household energy system (weather, PV, building, occupancy,
    heat distribution, gas boiler, hot water storage, electricity and gas
    meters) and runs all timesteps. Then asserts that the gas meter's
    total gas demand matches the gas boiler's consumption within a 5%
    relative tolerance, and logs KPI values (total consumption, opex costs,
    CO2 footprint).

    Args:
        my_simulation_parameters: Optional simulation parameters. If None,
            defaults to a full-year simulation at 1-hour timesteps for 2021.
    """

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60 * 60

    # =========================================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)

        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
        my_simulation_parameters.logging_level = 4

    # this part is copied from hisim_main
    # Build Simulator
    path_to_be_added = os.path.dirname(os.path.abspath(PATH))

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_test_gas_meter",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)
    # Set some parameters
    heating_reference_temperature_in_celsius: float = -7.0

    # Build Weather
    my_weather_config = weather.WeatherConfig.preset_aachen("Weather")
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.preset_rooftop("PVSystem").resolve(
        SizingContext(roof_area_in_m2=120, weather_identity=my_weather_config.identity())
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Building
    my_building_config = building.BuildingConfig.preset_german_single_family_home("Building")
    my_building_config.weather_identity = my_weather_config.identity()
    # The design outside temperature is the weather's, not the building's: the building reads
    # it as a sized field (D-21), so it is resolved before the component is built.
    my_building_config = my_building_config.resolve(
        SizingContext(heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius)
    )
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    my_building_information = building.BuildingInformation(config=my_building_config)

    # Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.preset_couple_both_at_work("UTSPConnector")
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Heat Distribution Controller
    my_heat_distribution_controller_config = (
        heat_distribution_system.HeatDistributionControllerConfig.preset_building_derived(
            "HeatDistributionController"
        ).resolve(
            SizingContext(
                heating_load_in_watt=my_building_information.max_thermal_building_demand_in_watt,
                conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
                heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
                set_heating_temperature_in_celsius=(
                    my_building_information.set_heating_temperature_for_building_in_celsius
                ),
                set_cooling_temperature_in_celsius=(
                    my_building_information.set_cooling_temperature_for_building_in_celsius
                ),
            )
        )
    )

    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_heat_distribution_controller_config,
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_heat_distribution_controller_config
    )
    # Build Heat Distribution System
    my_heat_distribution_system_config = (
        heat_distribution_system.HeatDistributionConfig.preset_building_derived("HeatDistributionSystem").resolve(
            SizingContext(
                water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kg_per_second,
                conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
                heat_distribution_system_type=my_hds_controller_information.hds_controller_config.heating_system,
                heat_distribution_cooling_enabled=my_hds_controller_information.hds_controller_config.cooling_enabled,
            )
        )
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Gas boiler and controller
    my_gas_heater_config = generic_boiler.GenericBoilerConfig.preset_condensing_gas("CondensingGasBoiler").resolve(
        SizingContext(heating_load_in_watt=my_building_information.max_thermal_building_demand_in_watt)
    )
    my_gas_heater = generic_boiler.GenericBoiler(
        config=my_gas_heater_config, my_simulation_parameters=my_simulation_parameters,
    )

    my_gas_heater_controller_config = generic_boiler.GenericBoilerControllerConfig.preset_modulating(
        "ModulatingBoilerController"
    ).resolve(
        SizingContext(
            minimal_thermal_power_in_watt=concrete(my_gas_heater_config.minimal_thermal_power_in_watt),
            maximal_thermal_power_in_watt=concrete(my_gas_heater_config.maximal_thermal_power_in_watt),
        )
    )
    my_gas_heater_controller_config.with_domestic_hot_water_preparation = True
    my_gas_heater_controller = generic_boiler.GenericBoilerController(
        my_simulation_parameters=my_simulation_parameters, config=my_gas_heater_controller_config,
    )

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.preset_buffer(
        "SimpleHotWaterStorage"
    )
    # The litres-per-kilowatt figure is per kind of generator, and the volume law reads the field,
    # so the option is set on the preset before the configuration is resolved.
    my_simple_heat_water_storage_config.sizing_option = (
        simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GAS_HEATER
    )
    my_simple_heat_water_storage_config = my_simple_heat_water_storage_config.resolve(
        SizingContext(
            maximal_thermal_power_in_watt=concrete(my_gas_heater_config.maximal_thermal_power_in_watt)
        )
    )
    my_simple_hot_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # DHW storage
    my_dhw_storage_config = simple_water_storage.SimpleDHWStorageConfig.preset_standard("DHWStorage").resolve(
        SizingContext(number_of_apartments=my_building_information.number_of_apartments)
    )

    my_dhw_storage = simple_water_storage.SimpleDHWStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
    )

    my_sim.add_component(my_dhw_storage, connect_automatically=True)

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.preset_standard("ElectricityMeter"),
    )

    # Build Gas Meter
    my_gas_meter_config = gas_meter.GasMeterConfig.preset_standard("GasMeter").resolve(
        SizingContext(energy_carrier=my_gas_heater_config.energy_carrier)
    )
    my_gas_meter = gas_meter.GasMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=my_gas_meter_config,
    )

    # =========================================================================================================================================================
    # Add Components to Simulator and run all timesteps

    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)

    # Build the heating curve, the L2 controller that sends the supply temperature and the heating season to the
    # generator's controller and the heat distribution controller
    my_heating_curve_config = heating_curve.HeatingCurveControllerConfig.preset_building_derived(
        "HeatingCurveController"
    ).resolve(
        SizingContext(
            heating_load_in_watt=my_building_information.max_thermal_building_demand_in_watt,
            conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            set_heating_temperature_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
            heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type,
        )
    )
    my_heating_curve = heating_curve.HeatingCurveController(
        my_simulation_parameters=my_simulation_parameters, config=my_heating_curve_config
    )
    my_sim.add_component(my_heating_curve, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_gas_heater_controller, connect_automatically=True)
    my_sim.add_component(my_gas_heater, connect_automatically=True)

    my_sim.add_component(my_electricity_meter, connect_automatically=True)
    my_sim.add_component(my_gas_meter, connect_automatically=True)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Compare with kpi computation results

    # read kpi data
    with open(
        os.path.join(my_sim._simulation_parameters.result_directory, "all_kpis.json"),  # pylint: disable=W0212
        "r",
        encoding="utf-8",
    ) as file:
        jsondata = json.load(file)

    finder = KpiFinder(jsondata)

    gas_consumption_in_kilowatt_hour = finder.value(building="BUI1", tag="Gas Meter", name="Total gas demand from grid")
    gas_consumption_of_boiler_in_kilowatt_hour = finder.value(
        building="BUI1", tag="Gas Boiler", name=f"Total {my_gas_heater.energy_carrier.value} consumption (energy)"
    )

    opex_costs_for_gas_in_euro = finder.value(
        building="BUI1", tag="Gas Meter", name="Opex costs of gas consumption from grid"
    )

    co2_footprint_due_to_gas_use_in_kg = finder.value(
        building="BUI1", tag="Gas Meter", name="CO2 footprint of gas consumption from grid"
    )

    # The carrier is a sizable field now, so the log lines read it through ``concrete``.
    gas_carrier = concrete(my_gas_meter_config.gas_loadtype)
    log.information(
        f"Total {gas_carrier.value} consumption [kWh] {gas_consumption_of_boiler_in_kilowatt_hour}"
    )

    log.information(f"Total {gas_carrier.value} consumption measured by gas meter [kWh] {gas_consumption_in_kilowatt_hour}")
    log.information(f"Opex costs for total {gas_carrier.value} consumption [€] {opex_costs_for_gas_in_euro}")
    log.information(f"CO2 footprint for total {gas_carrier.value} consumption [kg] {co2_footprint_due_to_gas_use_in_kg}")

    # test and compare with relative error of 5%
    np.testing.assert_allclose(
        gas_consumption_in_kilowatt_hour,
        gas_consumption_of_boiler_in_kilowatt_hour,
        rtol=0.05,
    )


@pytest.mark.base
def test_the_preset_copies_the_carrier_from_the_generator_beside_it() -> None:
    """Pins the one law the gas meter has: its carrier is the generator's, never its own choice.

    ``preset_standard`` leaves ``gas_loadtype`` ``AUTO`` and the boiler contributes
    ``energy_carrier``, so a gas boiler produces a gas meter and a green-hydrogen boiler a
    green-hydrogen meter with nothing said twice. The two carriers are checked through the
    same context the setups build, which is what the deleted factory's ``gas_loadtype``
    argument used to carry.
    """
    for carrier in (lt.LoadTypes.GAS, lt.LoadTypes.GREEN_HYDROGEN):
        resolved = gas_meter.GasMeterConfig.preset_standard("GasMeter").resolve(
            SizingContext(energy_carrier=carrier)
        )
        assert resolved.gas_loadtype is carrier
        assert resolved.component_id.name == "GasMeter"
        assert resolved.investment_costs_in_euro is None


@pytest.mark.base
def test_a_pinned_carrier_survives_a_context_that_names_another() -> None:
    """Pins that the law only fills an open field, so an author can still state the carrier.

    A system whose generator is not converted yet contributes no ``energy_carrier`` fact, and
    the meter then has to be told what it measures. Writing the field is how that is said, and
    a written value is never overwritten by a law.
    """
    pinned = dataclasses.replace(
        gas_meter.GasMeterConfig.preset_standard("GasMeter"), gas_loadtype=lt.LoadTypes.GREEN_HYDROGEN
    )

    resolved = pinned.resolve(SizingContext(energy_carrier=lt.LoadTypes.GAS))

    assert resolved.gas_loadtype is lt.LoadTypes.GREEN_HYDROGEN


@pytest.mark.base
def test_the_opex_record_reports_the_gas_the_meter_actually_measured() -> None:
    """Catches the meter's operational-cost record going back to reporting a constant zero.

    ``get_cost_opex`` computes the metered kilowatt hours into a local and used to hand a
    configuration field on to the record instead, which nothing ever wrote the running total
    into -- so the operational-costs table showed ``0.0`` kWh for a meter whose own costs and
    emissions on the same row were computed from the real sum. The consumption on the record
    has to be that same sum.
    """
    import pandas as pd
    from hisim.components.gas_meter import GasMeter

    meter = GasMeter(
        my_simulation_parameters=SimulationParameters.one_day_only(2021, 900),
        config=gas_meter.GasMeterConfig.preset_standard("GasMeter").resolve(
            SizingContext(energy_carrier=lt.LoadTypes.GAS)
        ),
    )
    outputs = [meter.gas_from_grid_channel]
    # One column per output, in watt hours: 4 000 Wh in every one of the day's 96 steps.
    results = pd.DataFrame({0: [4_000.0] * 96})

    opex = meter.get_cost_opex(all_outputs=outputs, postprocessing_results=results)

    assert opex.total_consumption_in_kwh == pytest.approx(384.0)
    assert opex.opex_energy_cost_in_euro > 0.0


class MassFlowFixtures:
    """A hydrogen meter fed by a boiler's energy per step in Wh, a CHP's and an electrolyzer's mass flow in kg/s.

    The boiler and the CHP burn hydrogen, the electrolyzer produces it; the meter is run for one step.
    """

    #: The step's duration, in s.
    SECONDS_PER_TIMESTEP: int = 900

    #: The three feeds as ``(source, output, unit)``; the CHP's tags are the test's to choose.
    FEEDS: Tuple[Tuple[str, str, lt.Units], ...] = (
        ("Boiler", "EnergyDemandSh", lt.Units.WATT_HOUR),
        ("CHP", "GasDemandReal", lt.Units.KG_PER_SEC),
        ("Electrolyzer", "CurrentHydrogenFlowRate", lt.Units.KG_PER_SEC),
    )
    #: The tags of the feeds whose tags the test does not choose, by source.
    FIXED_TAGS: Dict[str, List[lt.InandOutputType]] = {
        "Boiler": [lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED],
        "Electrolyzer": [lt.InandOutputType.GAS_PRODUCTION, lt.InandOutputType.MASS_FLOW],
    }

    def __init__(self, mass_flow_tags: List[lt.InandOutputType]) -> None:
        """Build the meter with one feed from each source, the CHP's tagged ``mass_flow_tags``.

        Every input reads a fake source output of its own unit, indexed for one step's values.
        """
        self.meter = gas_meter.GasMeter(
            my_simulation_parameters=SimulationParameters.one_day_only(2021, self.SECONDS_PER_TIMESTEP),
            config=gas_meter.GasMeterConfig.preset_standard("HydrogenMeter").resolve(
                SizingContext(energy_carrier=lt.LoadTypes.GREEN_HYDROGEN)
            ),
        )
        #: The fake output each source publishes, by source name.
        self.sources: Dict[str, cp.ComponentOutput] = {}
        for source, output, unit in self.FEEDS:
            tags = mass_flow_tags if source == "CHP" else self.FIXED_TAGS[source]
            self.meter.add_component_input_and_connect(
                source_object_name=source,
                source_component_output=output,
                source_load_type=lt.LoadTypes.GREEN_HYDROGEN,
                source_unit=unit,
                source_tags=list(tags),
                source_weight=999,
            )
            self.sources[source] = cp.ComponentOutput(source, output, lt.LoadTypes.GREEN_HYDROGEN, unit, component_id=ComponentID(source))
            self.meter.inputs[-1].source_output = self.sources[source]
        fft.add_global_index_of_components([self.meter, *self.sources.values()])

    def run(self, values: Dict[str, float]) -> Dict[str, float]:
        """Simulate the meter's first step with each source's value by source name, 0 if not named; return its outputs."""
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([self.meter, *self.sources.values()]))
        for source, output in self.sources.items():
            stsv.values[output.global_index] = values.get(source, 0.0)
        self.meter.i_simulate(0, stsv, False)
        return {output.field_name: stsv.values[output.global_index] for output in self.meter.outputs}


@pytest.mark.base
def test_the_meter_counts_a_mass_flow_by_the_lower_heating_value_of_its_gas() -> None:
    """A CHP's 0.001 kg/s of hydrogen over 900 s is 0.9 kg, 29,989 Wh by hydrogen's 33.32 kWh/kg, beside a boiler's Wh.

    The factor is the one the energy balance counts the CHP's fuel port by, so the meter publishes what the port books.
    Catches a mass flow summed as watt-hours, a conversion by another heating value, and the boiler's feed counted twice
    or dropped once the mass flow is split off the consumption channel.
    """
    fixture = MassFlowFixtures([lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED, lt.InandOutputType.MASS_FLOW])
    outputs = fixture.run({"Boiler": 500.0, "CHP": 0.001})
    factor_in_kilowatt_hours = EnergyPort.kilowatt_hours_per_step(
        lt.Units.KG_PER_SEC, MassFlowFixtures.SECONDS_PER_TIMESTEP, lt.EnergyBalanceCarrier.HYDROGEN
    )
    assert factor_in_kilowatt_hours * 0.001 == pytest.approx(29.989, rel=1e-4)
    assert outputs[gas_meter.GasMeter.GasConsumption] == pytest.approx(500.0 + 1e3 * factor_in_kilowatt_hours * 0.001, rel=1e-12)
    assert outputs[gas_meter.GasMeter.GasFromGrid] == outputs[gas_meter.GasMeter.GasConsumption]
    assert [component_input.src_object_name for component_input in fixture.meter.consumption_uncontrolled_inputs] == ["Boiler"]
    assert [component_input.src_object_name for component_input in fixture.meter.consumption_mass_flow_inputs] == ["CHP"]
    assert [component_input.src_object_name for component_input in fixture.meter.production_mass_flow_inputs] == [
        "Electrolyzer"
    ]
    assert not fixture.meter.production_inputs


@pytest.mark.base
def test_the_meter_passes_out_what_a_producer_gives_beyond_the_consumption() -> None:
    """An electrolyzer's hydrogen beyond what the burners take leaves through the meter's export, and a shortfall is bought.

    0.002 kg/s produced against 0.001 kg/s and 500 Wh burned leaves 0.001 kg/s minus 500 Wh to the export and buys
    nothing; with 0.0005 kg/s produced, 0.0005 kg/s plus 500 Wh are bought and nothing leaves. In both steps the
    import minus the export is the consumption minus the production, which the energy balance checks the meter by.
    Catches a production mass flow summed as watt-hours, a surplus that leaves no output, and an export and an
    import published in the same step.
    """
    fixture = MassFlowFixtures([lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED, lt.InandOutputType.MASS_FLOW])
    watt_hours_per_kilogram_per_second = 1e3 * EnergyPort.kilowatt_hours_per_step(
        lt.Units.KG_PER_SEC, MassFlowFixtures.SECONDS_PER_TIMESTEP, lt.EnergyBalanceCarrier.HYDROGEN
    )
    surplus = fixture.run({"Boiler": 500.0, "CHP": 0.001, "Electrolyzer": 0.002})
    assert surplus[gas_meter.GasMeter.GasProduction] == pytest.approx(0.002 * watt_hours_per_kilogram_per_second, rel=1e-12)
    assert surplus[gas_meter.GasMeter.GasToGridInWattHour] == pytest.approx(
        0.001 * watt_hours_per_kilogram_per_second - 500.0, rel=1e-12
    )
    assert surplus[gas_meter.GasMeter.GasFromGrid] == 0.0
    shortfall = fixture.run({"Boiler": 500.0, "CHP": 0.001, "Electrolyzer": 0.0005})
    assert shortfall[gas_meter.GasMeter.GasToGridInWattHour] == 0.0
    assert shortfall[gas_meter.GasMeter.GasFromGrid] == pytest.approx(
        0.0005 * watt_hours_per_kilogram_per_second + 500.0, rel=1e-12
    )
    exchange = fixture.meter.metered_exchanges()[0]
    assert (exchange.carrier, exchange.import_output, exchange.export_output) == (
        lt.EnergyBalanceCarrier.HYDROGEN,
        gas_meter.GasMeter.GasFromGrid,
        gas_meter.GasMeter.GasToGridInWattHour,
    )
    assert exchange.export_node() == "hydrogen export"


@pytest.mark.base
def test_a_mass_flow_fed_without_its_tag_is_refused() -> None:
    """A kg/s feed tagged only as consumption would be summed as watt-hours: the meter refuses it at its first step."""
    fixture = MassFlowFixtures([lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED])
    with pytest.raises(ValueError, match="CHP.GasDemandReal in kg/s on its channel 'consumption_uncontrolled'"):
        fixture.run({"Boiler": 500.0, "CHP": 0.001})
