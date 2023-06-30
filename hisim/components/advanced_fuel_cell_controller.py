# pylint: skip-file
from typing import Any
from hisim.component import (
    Component,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
)
from hisim import loadtypes as lt

from hisim.components.configuration import (
    CHPControllerConfig,
    GasControllerConfig,
    AdvElectrolyzerConfig,
)
from hisim.components import advanced_fuel_cell as chp
from hisim.components.configuration import ExtendedControllerConfig
from hisim.simulationparameters import SimulationParameters
from hisim import log
from math import ceil
from copy import deepcopy


class ExtendedControllerSimulation:
    def __init__(self, config: ExtendedControllerConfig):
        pass

    def regulate_chp_mode_power(
        self,
        state_chp: Any,
        runtime_chp: Any,
        power_supply_pv: Any,
        electricity_demand_household: Any,
        seconds_per_timestep: Any,
    ) -> Any:
        """
        :param power_supply_pv:
        :param electricity_demand_household:
        :param seconds_per_timestep:
        :return:
        """
        demand = electricity_demand_household - power_supply_pv
        if chp.CHPConfig.is_modulating:
            power_states_possible = ExtendedControllerConfig.chp_power_states_possible
            min_power = chp.CHPConfig.P_el_min
            max_power = chp.CHPConfig.P_el_max
            power_delta = max_power - min_power
            # the first step is already the minimum power
            power_per_step_size = power_delta / (power_states_possible - 1)

            if demand <= 0:
                if state_chp == 0:
                    pass
                else:
                    minimum_timesteps = (
                        CHPControllerConfig.minimum_runtime_minutes * 60
                    ) / seconds_per_timestep
                    if runtime_chp >= minimum_timesteps:
                        # switch off chp
                        state_chp = 0
                        runtime_chp = 0
                    else:
                        state_chp = 1 / power_states_possible
            elif 0 < demand < chp.CHPConfig.P_el_min:
                # maximum_autarky --> production time if P > 0 or production only if P in range?
                if ExtendedControllerConfig.maximum_autarky:
                    state_chp = 1 / power_states_possible  # eg 0.1
                else:
                    if state_chp == 0:
                        pass  # state_chp = 0
                    else:
                        minimum_timesteps = (
                            CHPControllerConfig.minimum_runtime_minutes * 60
                        ) / seconds_per_timestep
                        if runtime_chp >= minimum_timesteps:
                            # switch off chp
                            state_chp = 0
                            runtime_chp = 0
                        else:
                            state_chp = 1 / power_states_possible
            elif chp.CHPConfig.P_el_min <= demand < chp.CHPConfig.P_el_max:
                # minimum power plus the demand depending power
                state_chp = (
                    1 + ceil((demand - chp.CHPConfig.P_el_min) / power_per_step_size)
                ) / power_states_possible
                assert (1 / power_states_possible) < state_chp <= 1
            else:  # demand >= chp.CHPConfig.P_el_max
                state_chp = 1

            if state_chp == 0:
                generated_electricity = 0
                power_from_or_to_grid = demand - generated_electricity
            elif 0 < state_chp <= 1:
                generated_electricity = chp.CHPConfig.P_el_min + power_per_step_size * (
                    state_chp * power_states_possible - 1
                )
                power_from_or_to_grid = demand - generated_electricity
            else:
                log.error("Wrong controller state")
                raise ValueError

        else:
            if demand <= 0:
                if state_chp == 0:
                    power_from_or_to_grid = demand
                else:
                    minimum_timesteps = (
                        CHPControllerConfig.minimum_runtime_minutes * 60
                    ) / seconds_per_timestep
                    if runtime_chp >= minimum_timesteps:
                        state_chp = 0
                        power_from_or_to_grid = demand
                    else:
                        state_chp = 1
                        power_from_or_to_grid = demand - chp.CHPConfig.P_el_max

            else:  # demand > 0
                state_chp = 1
                power_from_or_to_grid = demand - chp.CHPConfig.P_el_max

        # independent of modulation
        if state_chp > 0:
            runtime_chp += 1
        else:
            runtime_chp = 0

        return state_chp, runtime_chp, power_from_or_to_grid

    def regulate_chp_mode_heat(
        self,
        temperatures_in_tank,
        previous_state,
        runtime_chp,
        pv_production,
        electricity_demand_household,
        seconds_per_timestep,
    ):
        # the heat model has no modulation because there is a buffer (warm water storage)

        heights_in_tank = CHPControllerConfig.heights_in_tank

        if (
            CHPControllerConfig.height_upper_sensor
            or CHPControllerConfig.height_lower_sensor
        ) not in heights_in_tank:
            log.error(
                "Wrong sensor setting. Only 0, 20, 40, 60, 80, 100% are allowed.\n"
                "You tried "
                + str(CHPControllerConfig.height_upper_sensor)
                + " and "
                + str(CHPControllerConfig.height_lower_sensor)
            )
            raise ValueError

        # get temperatures at the chosen sensors
        for i in range(
            len(heights_in_tank)
        ):  # pylint: consider-using-enumerate # needs to be a range
            if CHPControllerConfig.height_upper_sensor == heights_in_tank[i]:
                temperature_upper_sensor = temperatures_in_tank[i]
            if CHPControllerConfig.height_lower_sensor == heights_in_tank[i]:
                temperature_lower_sensor = temperatures_in_tank[i]
        state_chp = previous_state
        # upper sensor
        if temperature_upper_sensor < CHPControllerConfig.temperature_switch_on:
            # switch on
            state_chp = 1

        # lower sensor (no check needed if chp is off)
        if state_chp == 1:
            if temperature_lower_sensor > CHPControllerConfig.temperature_switch_off:
                minimum_timesteps = (
                    CHPControllerConfig.minimum_runtime_minutes * 60
                ) / seconds_per_timestep
                if runtime_chp > minimum_timesteps:
                    # switch off is possible if chp has run at least xx min
                    state_chp = 0

        # increase counter
        if state_chp == 1:
            runtime_chp += 1
        elif state_chp == 0:
            runtime_chp = 0
        else:
            log.error("Wrong state_chp")
            raise ValueError
        # 'easy equation' --> no modulation
        power_from_or_to_grid = (
            electricity_demand_household
            - pv_production
            - chp.CHPConfig.P_el_max * state_chp
        )

        return state_chp, runtime_chp, power_from_or_to_grid

    def regulate_gas_heater(
        self,
        temperatures_in_tank: Any,
        previous_state: float,
        runtime_counter: int,
        seconds_per_timestep: int,
    ) -> Any:
        # the gas_heater model has no modulation because there is a buffer (warm water storage)
        # ToDo: future --> make modulating possible if the waste energy is to high? reduce power, increase mass_flow

        heights_in_tank = CHPControllerConfig.heights_in_tank
        if (
            GasControllerConfig.height_upper_sensor
            or GasControllerConfig.height_lower_sensor
        ) not in heights_in_tank:
            log.error(
                "Wrong sensor setting. Only 0, 20, 40, 60, 80, 100% are allowed.\n"
                "You tried "
                + str(GasControllerConfig.height_upper_sensor)
                + " and "
                + str(GasControllerConfig.height_lower_sensor)
            )
            raise ValueError

        for i in range(
            len(heights_in_tank)
        ):  # pylint: consider-using-enumerate # needs to be a range
            if GasControllerConfig.height_upper_sensor == heights_in_tank[i]:
                temperature_upper_sensor = temperatures_in_tank[i]
            if GasControllerConfig.height_lower_sensor == heights_in_tank[i]:
                temperature_lower_sensor = temperatures_in_tank[i]
        state_gas_heater = previous_state
        # upper sensor
        if temperature_upper_sensor < GasControllerConfig.temperature_switch_on:
            # switch on
            state_gas_heater = 1

        # lower sensor (no check needed if gas heater is off)
        if state_gas_heater == 1:
            if temperature_lower_sensor > GasControllerConfig.temperature_switch_off:
                minimum_timesteps = (
                    GasControllerConfig.minimum_runtime_minutes * 60
                ) / seconds_per_timestep
                if runtime_counter > minimum_timesteps:
                    # switch off is possible if gas_heater has run at least xx min
                    state_gas_heater = 0
            else:
                state_gas_heater = 1

        # increase counter
        if state_gas_heater == 1:
            runtime_counter += 1
        elif state_gas_heater == 0:
            runtime_counter = 0
        else:
            log.error("Wrong state_chp")
            raise ValueError

        return state_gas_heater, runtime_counter

    def power_distribution_to_electrolyzer(self, power_from_or_to_grid: Any) -> Any:
        """
        Hydrogen storage must be able to store the produced massflow of hydrogen.
        Otherwise the dimensioning of the system is incorrect --> is checked in hydrogen_storage
        power_from_or_to_grid is positive if there is a demand of energy and negative if there is a surplus of energy.
        power_available is defined the other way round --> negate the value
        """
        power_available = -power_from_or_to_grid

        if power_available < AdvElectrolyzerConfig.min_power:
            power_to_electrolyzer = 0
            # no change
            # power_from_or_to_grid = power_available
        elif (
            AdvElectrolyzerConfig.min_power
            <= power_available
            <= AdvElectrolyzerConfig.max_power
        ):
            power_to_electrolyzer = power_available
            power_from_or_to_grid = 0
        else:  # power_available > ElectrolyzerConfig.max_power:
            power_to_electrolyzer = AdvElectrolyzerConfig.max_power
            # not al the electricity can go to the electolyzer --> power_from_or_to_gridstays negative
            power_from_or_to_grid = power_to_electrolyzer - power_available

        return power_to_electrolyzer, power_from_or_to_grid


class ExtendedController(Component):
    # inputs
    ElectricityDemand = "Electricity Demand"  # W
    PV_Production = "PV Production"  # W

    # temperatures (input)
    Temperature0Percent = "Temperature 0 Percent"  # °C
    Temperature20Percent = "Temperature 20 Percent"  # °C
    Temperature40Percent = "Temperature 40 Percent"  # °C
    Temperature60Percent = "Temperature 60 Percent"  # °C
    Temperature80Percent = "Temperature 80 Percent"  # °C
    Temperature100Percent = "Temperature 100 Percent"  # °C

    # Output
    ControllerCHP = "Controller CHP"
    ControllerGasHeater = "Controller Gas Heater"
    PowerToElectrolyzer = "Power To Electrolyzer"
    PowerFromOrToGrid = "Power From Or To Grid"

    RuntimeCounterCHP = "RuntimeCounterCHP"
    RuntimeCounterGasHeater = "RuntimeCounterGasHeater"

    def __init__(
        self,
        component_name: str,
        config: ExtendedControllerConfig,
        my_simulation_parameters: SimulationParameters,
    ) -> None:
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        # Input
        self.electricity_demand_household: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.ElectricityDemand,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )
        self.pv_production: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.PV_Production,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        self.temperature_0_percent: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature0Percent,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_20_percent: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature20Percent,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_40_percent: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature40Percent,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_60_percent: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature60Percent,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_80_percent: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature80Percent,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_100_percent: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature100Percent,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
            True,
        )

        # Output
        self.controller_chp: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.ControllerCHP,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
        )
        self.controller_gas_heater: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.ControllerGasHeater,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
        )
        self.power_to_electrolyzer: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.PowerToElectrolyzer,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
        )
        self.power_from_or_to_grid: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.PowerFromOrToGrid,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
        )

        self.runtime_counter_chp: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.RuntimeCounterCHP,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
        )
        self.runtime_counter_gas_heater: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.RuntimeCounterGasHeater,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
        )

        self.extended_controller = ExtendedControllerSimulation(config)
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep

        # CHP state/runtime & Gas state/runtime
        self.state_chp1 = 0
        self.runtime_chp1 = 0
        self.state_gas_heater1 = 0
        self.runtime_gas_heater1 = 0

        # self.previous_state = [self.state_chp1, self.runtime_chp1, self.state_gas_heater1, self.runtime_gas_heater1]
        self.previous_state_chp1 = self.state_chp1
        self.previous_runtime_chp1 = self.runtime_chp1
        self.previous_state_gas_heater1 = self.state_gas_heater1
        self.previous_runtime_gas_heater1 = self.runtime_gas_heater1

        self.test_pv: float = 0
        self.test_grid: float = 0
        self.test_electrolyzer: float = 0
        self.test_demand: float = 0
        self.test_state: float = 0

    def i_save_state(self) -> None:
        # self.previous_state = self.extended_controller.begin_new_timestep()
        self.previous_state_chp1 = deepcopy(self.state_chp1)
        self.previous_runtime_chp1 = deepcopy(self.runtime_chp1)
        self.previous_state_gas_heater1 = deepcopy(self.state_gas_heater1)
        self.previous_runtime_gas_heater1 = deepcopy(self.runtime_gas_heater1)

    def i_restore_state(self) -> None:
        # self.extended_controller.reset_to_last_timestep(self.previous_state)
        self.state_chp1 = deepcopy(self.previous_state_chp1)
        self.runtime_chp1 = deepcopy(self.previous_runtime_chp1)
        self.state_gas_heater1 = deepcopy(self.previous_state_gas_heater1)
        self.runtime_gas_heater1 = deepcopy(self.previous_runtime_gas_heater1)

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        if force_convergence:
            return

        # Inputs
        electricity_demand_household = stsv.get_input_value(
            self.electricity_demand_household
        )
        pv_production: float = stsv.get_input_value(self.pv_production)

        # not needed for power
        if (
            ExtendedControllerConfig.chp_mode == "heat"
            or ExtendedControllerConfig.gas_heater
        ):
            temperature_0_percent = stsv.get_input_value(self.temperature_0_percent)
            temperature_20_percent = stsv.get_input_value(self.temperature_20_percent)
            temperature_40_percent = stsv.get_input_value(self.temperature_40_percent)
            temperature_60_percent = stsv.get_input_value(self.temperature_60_percent)
            temperature_80_percent = stsv.get_input_value(self.temperature_80_percent)
            temperature_100_percent = stsv.get_input_value(self.temperature_100_percent)

            temperatures_in_tank = [
                temperature_0_percent,
                temperature_20_percent,
                temperature_40_percent,
                temperature_60_percent,
                temperature_80_percent,
                temperature_100_percent,
            ]

        # Combined heat and power plant
        if ExtendedControllerConfig.chp:
            if ExtendedControllerConfig.chp_mode == "power":
                # chp is running depending on the delta between pv_production and electricity_demand_household
                (
                    self.state_chp1,
                    self.runtime_chp1,
                    power_from_or_to_grid,
                ) = self.extended_controller.regulate_chp_mode_power(
                    self.state_chp1,
                    self.runtime_chp1,
                    pv_production,
                    electricity_demand_household,
                    self.seconds_per_timestep,
                )
            elif ExtendedControllerConfig.chp_mode == "heat":
                (
                    self.state_chp1,
                    self.runtime_chp1,
                    power_from_or_to_grid,
                ) = self.extended_controller.regulate_chp_mode_heat(
                    temperatures_in_tank,
                    self.state_chp1,
                    self.runtime_chp1,
                    pv_production,
                    electricity_demand_household,
                    self.seconds_per_timestep,
                )
            else:
                log.error(
                    "Wrong chp controller settings! Choose between heat and power"
                )
                raise ValueError
        else:
            power_from_or_to_grid = pv_production - electricity_demand_household

        # Gas heater
        if ExtendedControllerConfig.gas_heater:
            (
                self.state_gas_heater1,
                self.runtime_gas_heater1,
            ) = self.extended_controller.regulate_gas_heater(
                temperatures_in_tank,
                self.state_gas_heater1,
                self.runtime_gas_heater1,
                self.seconds_per_timestep,
            )

        if not ExtendedControllerConfig.chp or not ExtendedControllerConfig.gas_heater:
            log.error("Choose a energy source")
            raise ValueError

        # Electrolyzer
        # ToDo: Sollte er laufen wenn die KWK auch läuft... (stromgeführt nein, wärmegefüht evtl ja slange keine Batterie da ist)

        if ExtendedControllerConfig.electrolyzer:
            (
                power_to_electrolyzer,
                power_from_or_to_grid,
            ) = self.extended_controller.power_distribution_to_electrolyzer(
                power_from_or_to_grid
            )
        else:
            power_to_electrolyzer = 0

        # Outputs
        stsv.set_output_value(self.controller_chp, self.state_chp1)
        # stsv.set_output_value(self.controller_gas_heater, self.extended_controller.state_gas_heater)
        stsv.set_output_value(self.controller_gas_heater, self.state_gas_heater1)

        stsv.set_output_value(self.power_to_electrolyzer, power_to_electrolyzer)
        stsv.set_output_value(self.power_from_or_to_grid, power_from_or_to_grid)

        stsv.set_output_value(self.runtime_counter_chp, self.runtime_chp1)
        stsv.set_output_value(self.runtime_counter_gas_heater, self.runtime_gas_heater1)

        # test
        self.test_demand = electricity_demand_household
        self.test_pv = pv_production
        self.test_grid = power_from_or_to_grid
        self.test_electrolyzer = power_to_electrolyzer
        self.test_state = self.state_chp1

        # self.test_state = self.extended_controller.state_chp

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        # check the electricity balance
        if chp.CHPConfig.is_modulating:
            if self.test_state > 0:
                power_chp_test = chp.CHPConfig.P_el_min + (
                    chp.CHPConfig.P_el_max - chp.CHPConfig.P_el_min
                ) / (ExtendedControllerConfig.chp_power_states_possible - 1) * (
                    self.test_state * ExtendedControllerConfig.chp_power_states_possible
                    - 1
                )
            else:
                power_chp_test = 0

            if 0.00001 > (
                self.test_pv
                + power_chp_test
                + self.test_grid
                - self.test_demand
                - self.test_electrolyzer
            ):
                pass
            else:
                log.error("Wrong energy balance:")
                log.error("State CHP: " + str(self.test_state))
                log.error("test_pv: " + str(self.test_pv))
                log.error("power_chp_test: " + str(power_chp_test))
                log.error("test_grid: " + str(self.test_grid))
                log.error("test_demand: " + str(self.test_demand))
                log.error("test_electrolyzer: " + str(self.test_electrolyzer))
                raise ValueError

        else:
            if 0.00001 > (
                self.test_pv
                + chp.CHPConfig.P_el_max * self.test_state
                + self.test_grid
                - self.test_electrolyzer
                - self.test_demand
            ):
                pass
            else:
                log.error("Wrong energy balance:")
                log.error("State CHP: " + str(self.test_state))
                log.error("test_pv: " + str(self.test_pv))
                log.error("power_chp_test: " + str(chp.CHPConfig.P_el_max))
                log.error("test_grid: " + str(self.test_grid))
                log.error("test_demand: " + str(self.test_demand))
                log.error("test_electrolyzer: " + str(self.test_electrolyzer))
                raise ValueError
