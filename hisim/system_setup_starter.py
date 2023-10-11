"""
The System Setup Starter reads setup parameters from a JSON file and builds a system setup for a specific example.
The setup is simulated and result files are stored in `/results`.
"""

from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import Optional, Union
import json
from marshmallow.exceptions import ValidationError
from hisim import log
from pprint import pprint
from hisim.simulator import SimulationParameters
from hisim.hisim_main import main
import os
from hisim.postprocessingoptions import PostProcessingOptions


@dataclass_json
@dataclass
class CostParameters:
    electricity_price: float
    gas_price: float


@dataclass_json
@dataclass
class SystemSetupParameters:
    system_setup_file: str
    system_setup_function: str
    cost_parameters: CostParameters
    building_type: str
    number_of_people: int
    heat_pump_power: Optional[float] = None


def make_and_execute_system_setup(
    parameters_json: Union[dict, list], result_directory: str = "result"
) -> None:
    """
    Read setup parameters from JSON and build a system setup for a specific example.
    The setup is simulated and result files are stored in `result_directory`.
    """
    if isinstance(parameters_json, list):
        raise NotImplementedError(
            "System Setup Starter can only handle one setup at a time for now."
        )

    # Read parameters from json
    parameters: SystemSetupParameters = SystemSetupParameters.schema().load(
        parameters_json, many=False
    )

    # Set system setup configuration depending on the selected setup_file
    if parameters.system_setup_file == "household_1_advanced_hp_diesel_car":
        from examples.household_1_advanced_hp_diesel_car import (
            HouseholdAdvancedHPDieselCarConfig,
        )
        from hisim.components.generic_heat_pump_modular import HeatPumpConfig

        # Read default values
        setup_config: HouseholdAdvancedHPDieselCarConfig = (
            HouseholdAdvancedHPDieselCarConfig.get_default()
        )

        #####
        # Set values from loaded parameters
        #####

        # Building type
        setup_config.building_type = parameters.building_type
        # Heat Pump
        if parameters.heat_pump_power != None:
            setup_config.dhw_heatpump_config: HeatPumpConfig = (
                HeatPumpConfig.get_default_config_waterheating()
            )
            setup_config.dhw_heatpump_config.power_th = 12.3
    else:
        raise ValueError(
            f"""The setup file {parameters.system_setup_file} is not supported 
            by the System Setup Starter. Please select a different setup file."""
        )

    # Run simulation
    path = f"examples/{parameters.system_setup_file}.py"
    func = parameters.system_setup_function
    mysimpar = SimulationParameters.one_day_only(
        year=2021,
        seconds_per_timestep=60,
    )
    mysimpar.result_directory = result_directory
    mysimpar.post_processing_options.append(
        PostProcessingOptions.COMPUTE_AND_WRITE_KPIS_TO_REPORT
    )
    mysimpar.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    mysimpar.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    mysimpar.post_processing_options.append(
        PostProcessingOptions.MAKE_RESULT_JSON_WITH_KPI_FOR_WEBTOOL
    )

    main(path, func, mysimpar)
    log.information(os.getcwd())


if __name__ == "__main__":
    from sys import argv

    with open(argv[1], "r") as c:
        config_json: Union[dict, list] = json.load(c)
    make_and_execute_system_setup(config_json)
