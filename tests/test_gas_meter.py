"""Test for gas meter."""

# clean

import dataclasses
import os
import json
from typing import Optional
import pytest
import numpy as np
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim import loadtypes as lt
from hisim.config import SizingContext, concrete
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
from hisim import log


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
    my_building_config.heating_reference_temperature_in_celsius = heating_reference_temperature_in_celsius
    my_building_config.weather_identity = my_weather_config.identity()
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

    jsondata = jsondata["BUI1"]

    gas_consumption_in_kilowatt_hour = jsondata["Gas Meter"]["Total gas demand from grid"].get("value")
    gas_consumption_of_boiler_in_kilowatt_hour = jsondata["Gas Boiler"][
        f"Total {my_gas_heater.energy_carrier.value} consumption (energy)"
    ].get("value")

    opex_costs_for_gas_in_euro = jsondata["Gas Meter"]["Opex costs of gas consumption from grid"].get("value")

    co2_footprint_due_to_gas_use_in_kg = jsondata["Gas Meter"]["CO2 footprint of gas consumption from grid"].get("value")

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
