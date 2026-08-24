"""Advanced fuel cell controller module."""

# clean
from math import ceil
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim.config import DisplayConfig
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


class ExtendedControllerSimulation:
    """Extended Controller Simulation."""

    def __init__(self) -> None:
        """Initialize the class.

        Precomputes the tank-layer indices for the upper/lower temperature
        sensors of both the CHP and the gas heater. The sensor heights and tank
        layers are invariant config constants, so the matching indices are
        computed once here instead of scanning ``heights_in_tank`` every
        timestep in the regulate methods. All four sensor heights are validated
        unconditionally at construction (regardless of ``chp_mode`` or the
        ``gas_heater`` flag) so that a misconfigured height fails loudly at
        setup time rather than silently each timestep; this also closes the
        previous ``or``-short-circuit bug that never checked the lower sensors.
        """
        # sensor indices are precomputed once here (config constants are invariant)
        self._chp_upper_idx: int = self._sensor_index(
            CHPControllerConfig.heights_in_tank,
            CHPControllerConfig.height_upper_sensor,
            "CHP",
            "upper",
        )
        self._chp_lower_idx: int = self._sensor_index(
            CHPControllerConfig.heights_in_tank,
            CHPControllerConfig.height_lower_sensor,
            "CHP",
            "lower",
        )
        self._gas_upper_idx: int = self._sensor_index(
            CHPControllerConfig.heights_in_tank,
            GasControllerConfig.height_upper_sensor,
            "gas heater",
            "upper",
        )
        self._gas_lower_idx: int = self._sensor_index(
            CHPControllerConfig.heights_in_tank,
            GasControllerConfig.height_lower_sensor,
            "gas heater",
            "lower",
        )

    @staticmethod
    def _sensor_index(
        heights_in_tank: list[int],
        sensor_height: int,
        controller_name: str,
        sensor_label: str,
    ) -> int:
        """Return the index of ``sensor_height`` within ``heights_in_tank``.

        Validates that the configured sensor height is one of the available tank
        layers, raising a clear error on misconfiguration instead of silently
        scanning every timestep.
        """
        for i, height_in_tank in enumerate(heights_in_tank):
            if height_in_tank == sensor_height:
                return i
        msg = (
            f"Wrong sensor setting for the {controller_name} {sensor_label} sensor: "
            f"{sensor_height} not in {heights_in_tank}. "
            f"Only {heights_in_tank} are allowed."
        )
        log.error(msg)
        raise ValueError(msg)

    def regulate_chp_mode_power(
        self,
        state_chp: float,
        runtime_chp: float,
        power_supply_pv: float,
        electricity_demand_household: float,
        seconds_per_timestep: int,
    ) -> tuple[float, float, float]:
        """Regulate the power.

        :param power_supply_pv:
        :param electricity_demand_household:
        :param seconds_per_timestep:
        :return:
        """
        demand = electricity_demand_household - power_supply_pv
        if chp.CHPConfig.is_modulating:
            power_states_possible = ExtendedControllerConfig.chp_power_states_possible
            min_power = chp.CHPConfig.p_el_min
            max_power = chp.CHPConfig.p_el_max
            power_delta = max_power - min_power
            # the first step is already the minimum power
            power_per_step_size = power_delta / (power_states_possible - 1)

            if demand <= 0:
                if state_chp == 0:
                    pass
                else:
                    minimum_timesteps = (CHPControllerConfig.minimum_runtime_minutes * 60) / seconds_per_timestep
                    if runtime_chp >= minimum_timesteps:
                        # switch off chp
                        state_chp = 0
                        runtime_chp = 0
                    else:
                        state_chp = 1 / power_states_possible
            elif 0 < demand < chp.CHPConfig.p_el_min:
                # maximum_autarky --> production time if P > 0 or production only if P in range?
                if ExtendedControllerConfig.maximum_autarky:
                    state_chp = 1 / power_states_possible  # eg 0.1
                else:
                    if state_chp == 0:
                        pass  # state_chp = 0
                    else:
                        minimum_timesteps = (CHPControllerConfig.minimum_runtime_minutes * 60) / seconds_per_timestep
                        if runtime_chp >= minimum_timesteps:
                            # switch off chp
                            state_chp = 0
                            runtime_chp = 0
                        else:
                            state_chp = 1 / power_states_possible
            elif chp.CHPConfig.p_el_min <= demand < chp.CHPConfig.p_el_max:
                # minimum power plus the demand depending power
                state_chp = (1 + ceil((demand - chp.CHPConfig.p_el_min) / power_per_step_size)) / power_states_possible
                assert (1 / power_states_possible) < state_chp <= 1
            else:  # demand >= chp.CHPConfig.P_el_max
                state_chp = 1

            if state_chp == 0:
                generated_electricity = 0.0
                power_from_or_to_grid = demand - generated_electricity
            elif 0 < state_chp <= 1:
                generated_electricity = chp.CHPConfig.p_el_min + power_per_step_size * (
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
                    minimum_timesteps = (CHPControllerConfig.minimum_runtime_minutes * 60) / seconds_per_timestep
                    if runtime_chp >= minimum_timesteps:
                        state_chp = 0
                        power_from_or_to_grid = demand
                    else:
                        state_chp = 1
                        power_from_or_to_grid = demand - chp.CHPConfig.p_el_max

            else:  # demand > 0
                state_chp = 1
                power_from_or_to_grid = demand - chp.CHPConfig.p_el_max

        # independent of modulation
        if state_chp > 0:
            runtime_chp += 1
        else:
            runtime_chp = 0

        return state_chp, runtime_chp, power_from_or_to_grid

    def regulate_chp_mode_heat(
        self,
        temperatures_in_tank: list[float],
        previous_state: float,
        runtime_chp: float,
        pv_production: float,
        electricity_demand_household: float,
        seconds_per_timestep: int,
    ) -> tuple[float, float, float]:
        """Regulate chp mode heat."""

        # the heat model has no modulation because there is a buffer (warm water storage)

        # sensor indices are precomputed once in __init__ (config constants are invariant)
        temperature_upper_sensor = temperatures_in_tank[self._chp_upper_idx]
        temperature_lower_sensor = temperatures_in_tank[self._chp_lower_idx]
        state_chp = previous_state
        # upper sensor
        if temperature_upper_sensor < CHPControllerConfig.temperature_switch_on:
            # switch on
            state_chp = 1

        # lower sensor (no check needed if chp is off)
        if state_chp == 1:
            if temperature_lower_sensor > CHPControllerConfig.temperature_switch_off:
                minimum_timesteps = (CHPControllerConfig.minimum_runtime_minutes * 60) / seconds_per_timestep
                if runtime_chp >= minimum_timesteps:
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
        power_from_or_to_grid = electricity_demand_household - pv_production - chp.CHPConfig.p_el_max * state_chp

        return state_chp, runtime_chp, power_from_or_to_grid

    def regulate_gas_heater(
        self,
        temperatures_in_tank: list[float],
        previous_state: float,
        runtime_counter: float,
        seconds_per_timestep: int,
    ) -> tuple[float, float]:
        """Regulate gas heater."""

        # the gas_heater model has no modulation because there is a buffer (warm water storage)
        # ToDo: future --> make modulating possible if the waste energy is to high? reduce power, increase mass_flow

        # sensor indices are precomputed once in __init__ (config constants are invariant)
        temperature_upper_sensor = temperatures_in_tank[self._gas_upper_idx]
        temperature_lower_sensor = temperatures_in_tank[self._gas_lower_idx]
        state_gas_heater = previous_state
        # upper sensor
        if temperature_upper_sensor < GasControllerConfig.temperature_switch_on:
            # switch on
            state_gas_heater = 1

        # lower sensor (no check needed if gas heater is off)
        if state_gas_heater == 1:
            if temperature_lower_sensor > GasControllerConfig.temperature_switch_off:
                minimum_timesteps = (GasControllerConfig.minimum_runtime_minutes * 60) / seconds_per_timestep
                if runtime_counter >= minimum_timesteps:
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

    def power_distribution_to_electrolyzer(self, power_from_or_to_grid: float) -> tuple[float, float]:
        """Power distribution to electrolyzer.

        Hydrogen storage must be able to store the produced massflow of hydrogen.
        Otherwise the dimensioning of the system is incorrect --> is checked in hydrogen_storage
        power_from_or_to_grid is positive if there is a demand of energy and negative if there is a surplus of energy.
        power_available is defined the other way round --> negate the value
        """
        power_available = -power_from_or_to_grid

        if power_available < AdvElectrolyzerConfig.min_power:
            power_to_electrolyzer = 0.0
            # no change
            # power_from_or_to_grid = power_available
        elif AdvElectrolyzerConfig.min_power <= power_available <= AdvElectrolyzerConfig.max_power:
            power_to_electrolyzer = power_available
            power_from_or_to_grid = 0
        else:  # power_available > ElectrolyzerConfig.max_power:
            power_to_electrolyzer = AdvElectrolyzerConfig.max_power
            # not al the electricity can go to the electolyzer --> power_from_or_to_gridstays negative
            power_from_or_to_grid = power_to_electrolyzer - power_available

        return power_to_electrolyzer, power_from_or_to_grid


class ExtendedController(Component):
    """Extended Controller class."""

    # inputs
    ElectricityDemand: str = "Electricity Demand"  # W
    PV_Production: str = "PV Production"  # W

    # temperatures (input)
    Temperature0Percent: str = "Temperature 0 Percent"  # °C
    Temperature20Percent: str = "Temperature 20 Percent"  # °C
    Temperature40Percent: str = "Temperature 40 Percent"  # °C
    Temperature60Percent: str = "Temperature 60 Percent"  # °C
    Temperature80Percent: str = "Temperature 80 Percent"  # °C
    Temperature100Percent: str = "Temperature 100 Percent"  # °C

    # Output
    ControllerCHP: str = "Controller CHP"
    ControllerGasHeater: str = "Controller Gas Heater"
    PowerToElectrolyzer: str = "Power To Electrolyzer"
    PowerFromOrToGrid: str = "Power From Or To Grid"

    RuntimeCounterCHP: str = "RuntimeCounterCHP"
    RuntimeCounterGasHeater: str = "RuntimeCounterGasHeater"

    def __init__(
        self,
        config: ExtendedControllerConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Initialize the class. The component name is derived from the config's identity."""
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: ExtendedControllerConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        # Input
        self.electricity_demand_household_channel: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.ElectricityDemand,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )
        self.pv_production_channel: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.PV_Production,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        self.temperature_0_percent_channel: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature0Percent,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_20_percent_channel: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature20Percent,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_40_percent_channel: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature40Percent,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_60_percent_channel: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature60Percent,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_80_percent_channel: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature80Percent,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_100_percent_channel: ComponentInput = self.add_input(
            self.component_name,
            ExtendedController.Temperature100Percent,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        # Output
        self.controller_chp_channel: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.ControllerCHP,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="CHP modulation signal in percent (0 = off, 100 = full power).",
        )
        self.controller_gas_heater_channel: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.ControllerGasHeater,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="Gas heater modulation signal in percent (0 = off, 100 = full power).",
        )
        self.power_to_electrolyzer_channel: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.PowerToElectrolyzer,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Electric power routed to the electrolyzer in watt.",
        )
        self.power_from_or_to_grid_channel: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.PowerFromOrToGrid,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Electric power exchanged with the grid in watt (sign follows the household balance).",
        )

        self.runtime_counter_chp_channel: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.RuntimeCounterCHP,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Consecutive-timestep runtime counter of the CHP.",
        )
        self.runtime_counter_gas_heater_channel: ComponentOutput = self.add_output(
            self.component_name,
            ExtendedController.RuntimeCounterGasHeater,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Consecutive-timestep runtime counter of the gas heater.",
        )

        self.extended_controller: ExtendedControllerSimulation = ExtendedControllerSimulation()
        self.seconds_per_timestep: int = my_simulation_parameters.seconds_per_timestep

        # CHP state/runtime & Gas state/runtime
        self.state_chp: float = 0
        self.runtime_chp: float = 0
        self.state_gas_heater: float = 0
        self.runtime_gas_heater: float = 0

        # self.previous_state = [self.state_chp, self.runtime_chp, self.state_gas_heater, self.runtime_gas_heater]
        self.previous_state_chp: float = self.state_chp
        self.previous_runtime_chp: float = self.runtime_chp
        self.previous_state_gas_heater: float = self.state_gas_heater
        self.previous_runtime_gas_heater: float = self.runtime_gas_heater

        self.test_pv: float = 0
        self.test_grid: float = 0
        self.test_electrolyzer: float = 0
        self.test_demand: float = 0
        self.test_state: float = 0

    def i_save_state(self) -> None:
        """Saves the state."""
        # self.previous_state = self.extended_controller.begin_new_timestep()
        self.previous_state_chp = self.state_chp
        self.previous_runtime_chp = self.runtime_chp
        self.previous_state_gas_heater = self.state_gas_heater
        self.previous_runtime_gas_heater = self.runtime_gas_heater

    def i_restore_state(self) -> None:
        """Restores the state."""
        # self.extended_controller.reset_to_last_timestep(self.previous_state)
        self.state_chp = self.previous_state_chp
        self.runtime_chp = self.previous_runtime_chp
        self.state_gas_heater = self.previous_state_gas_heater
        self.runtime_gas_heater = self.previous_runtime_gas_heater

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the state."""
        if force_convergence:
            return

        # Inputs
        electricity_demand_household = stsv.get_input_value(self.electricity_demand_household_channel)
        pv_production: float = stsv.get_input_value(self.pv_production_channel)

        # not needed for power
        if ExtendedControllerConfig.chp_mode == "heat" or ExtendedControllerConfig.gas_heater:
            temperature_0_percent = stsv.get_input_value(self.temperature_0_percent_channel)
            temperature_20_percent = stsv.get_input_value(self.temperature_20_percent_channel)
            temperature_40_percent = stsv.get_input_value(self.temperature_40_percent_channel)
            temperature_60_percent = stsv.get_input_value(self.temperature_60_percent_channel)
            temperature_80_percent = stsv.get_input_value(self.temperature_80_percent_channel)
            temperature_100_percent = stsv.get_input_value(self.temperature_100_percent_channel)

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
                    self.state_chp,
                    self.runtime_chp,
                    power_from_or_to_grid,
                ) = self.extended_controller.regulate_chp_mode_power(
                    self.state_chp,
                    self.runtime_chp,
                    pv_production,
                    electricity_demand_household,
                    self.seconds_per_timestep,
                )
            elif ExtendedControllerConfig.chp_mode == "heat":
                (
                    self.state_chp,
                    self.runtime_chp,
                    power_from_or_to_grid,
                ) = self.extended_controller.regulate_chp_mode_heat(
                    temperatures_in_tank,
                    self.state_chp,
                    self.runtime_chp,
                    pv_production,
                    electricity_demand_household,
                    self.seconds_per_timestep,
                )
            else:
                log.error("Wrong chp controller settings! Choose between heat and power")
                raise ValueError
        else:
            power_from_or_to_grid = pv_production - electricity_demand_household

        # Gas heater
        if ExtendedControllerConfig.gas_heater:
            (
                self.state_gas_heater,
                self.runtime_gas_heater,
            ) = self.extended_controller.regulate_gas_heater(
                temperatures_in_tank,
                self.state_gas_heater,
                self.runtime_gas_heater,
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
            ) = self.extended_controller.power_distribution_to_electrolyzer(power_from_or_to_grid)
        else:
            power_to_electrolyzer = 0

        # Outputs
        stsv.set_output_value(self.controller_chp_channel, self.state_chp)
        # stsv.set_output_value(self.controller_gas_heater, self.extended_controller.state_gas_heater)
        stsv.set_output_value(self.controller_gas_heater_channel, self.state_gas_heater)

        stsv.set_output_value(self.power_to_electrolyzer_channel, power_to_electrolyzer)
        stsv.set_output_value(self.power_from_or_to_grid_channel, power_from_or_to_grid)

        stsv.set_output_value(self.runtime_counter_chp_channel, self.runtime_chp)
        stsv.set_output_value(self.runtime_counter_gas_heater_channel, self.runtime_gas_heater)

        # test
        self.test_demand = electricity_demand_household
        self.test_pv = pv_production
        self.test_grid = power_from_or_to_grid
        self.test_electrolyzer = power_to_electrolyzer
        self.test_state = self.state_chp

        # self.test_state = self.extended_controller.state_chp

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        # check the electricity balance
        if chp.CHPConfig.is_modulating:
            if self.test_state > 0:
                power_chp_test = chp.CHPConfig.p_el_min + (chp.CHPConfig.p_el_max - chp.CHPConfig.p_el_min) / (
                    ExtendedControllerConfig.chp_power_states_possible - 1
                ) * (self.test_state * ExtendedControllerConfig.chp_power_states_possible - 1)
            else:
                power_chp_test = 0

            if 0.00001 > (self.test_pv + power_chp_test + self.test_grid - self.test_demand - self.test_electrolyzer):
                pass
            else:
                log.error("Wrong energy balance:")
                log.error(f"State CHP: {self.test_state}")
                log.error(f"test_pv: {self.test_pv}")
                log.error(f"power_chp_test: {power_chp_test}")
                log.error(f"test_grid: {self.test_grid}")
                log.error(f"test_demand: {self.test_demand}")
                log.error(f"test_electrolyzer: {self.test_electrolyzer}")
                raise ValueError

        else:
            if 0.00001 > (
                self.test_pv
                + chp.CHPConfig.p_el_max * self.test_state
                + self.test_grid
                - self.test_electrolyzer
                - self.test_demand
            ):
                pass
            else:
                log.error("Wrong energy balance:")
                log.error(f"State CHP: {self.test_state}")
                log.error(f"test_pv: {self.test_pv}")
                log.error(f"power_chp_test: {chp.CHPConfig.p_el_max}")
                log.error(f"test_grid: {self.test_grid}")
                log.error(f"test_demand: {self.test_demand}")
                log.error(f"test_electrolyzer: {self.test_electrolyzer}")
                raise ValueError
