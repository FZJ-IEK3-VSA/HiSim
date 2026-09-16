# -*- coding: utf-8 -*-
"""Controller of EV battery with configuration and state."""

from typing import ClassVar, List, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

import pandas as pd

from utspclient.helpers.lpgpythonbindings import JsonReference

from hisim.simulationparameters import SimulationParameters
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import generic_car
from hisim.components import advanced_ev_battery_bslib
from hisim.loadtypes import Units, ComponentType, InandOutputType
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.config import ConfigBase, ComponentID, DisplayConfig, constructor
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class ChargingStationConfig(ConfigBase):
    """Configuration of the wallbox one electric car charges at, and the SOC it charges to.

    The charging station is picked out of the LoadProfileGenerator's ``ChargingStationSets``,
    whose members carry their rating in their name — "Charging At Home with 11 kW". That rating
    is the identity of the station, so there is no default station to name and no preset: a
    charger is built by naming its set::

        ChargingStationConfig.for_charging_station_set(
            "L1EVChargeControl_1", ChargingStationSets.Charging_At_Home_with_11_kW
        )

    The controller charges the car whenever it is at the station and its state of charge is
    below :attr:`battery_set_soc`, and refuses a charging power below
    :attr:`lower_threshold_charging_power_in_watt`, at which the station's efficiency collapses.
    """

    MAIN_CLASS = "hisim.components.controller_l1_generic_ev_charge.L1Controller"

    #: Share of a station's rated power below which charging is not worth starting: below it
    #: the conversion efficiency goes down. 10 % of the rating.
    LOWER_THRESHOLD_AS_SHARE_OF_CHARGING_POWER: ClassVar[float] = 0.1

    component_id: ComponentID
    #: definition of the charging station, in line with definitions from LoadProfileGenerator
    charging_station_set: JsonReference
    #: lower threshold for charging power (below efficiency goes down), in Watt. Computed by
    #: :meth:`for_charging_station_set` from the rating in the station set's name.
    lower_threshold_charging_power_in_watt: float
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int = 1
    #: set point for state of charge of battery
    battery_set_soc: float = 0.8
    #: CO2 footprint of investment in kg. estimated value  # Todo: check value
    device_co2_footprint_in_kg: float = 100.0
    #: cost for investment in Euro  # Todo: check value
    investment_costs_in_euro: float = 1000.0
    #: lifetime of charging station in years. estimated value  # Todo: check value
    lifetime_in_years: float = 10.0
    #: maintenance cost in euro per year: 5 % of the investment costs.
    #: SOURCE: https://photovoltaik.one/wallbox-kosten (estimated value)
    maintenance_costs_in_euro_per_year: float = 50.0
    #: subsidies as percentage of investment
    subsidy_as_percentage_of_investment_costs: float = 0.0

    @classmethod
    def charging_power_in_watt_of(cls, charging_station_set: JsonReference) -> float:
        """Reads the rated charging power, in Watt, out of a station set's name.

        The LoadProfileGenerator states a charging station's rating nowhere but in its display
        name, spelled "Charging At Home with 11 kW", so the number has to be parsed back out of
        it::

            ChargingStationConfig.charging_power_in_watt_of(
                ChargingStationSets.Charging_At_Home_with_11_kW
            )  # 11000.0

        Args:
            charging_station_set: One member of ``utspclient.helpers.lpgdata
                .ChargingStationSets``.

        Returns:
            The rated charging power in Watt.

        Raises:
            ValueError: If the set has no name, or if its name does not spell a rating in the
                "… with <number> kW" form this parser knows.
        """
        name = charging_station_set.Name or ""
        _, separator, remainder = name.partition("with ")
        rating, kilowatt, _ = remainder.partition(" kW")
        if not separator or not kilowatt:
            raise ValueError(
                f"the charging station set {name!r} does not state its rating: a set's name has "
                "to read '… with <number> kW', which is how the LoadProfileGenerator spells the "
                "rating and the only place it states it."
            )
        try:
            return float(rating) * 1e3
        except ValueError as error:
            raise ValueError(
                f"the charging station set {name!r} states {rating!r} where its rating in kW "
                "belongs, which is no number."
            ) from error

    @constructor(
        note="one of ChargingStationSets; the rating in its name sets the lower charging threshold"
    )
    @classmethod
    def for_charging_station_set(
        cls, name: str, charging_station_set: JsonReference
    ) -> "ChargingStationConfig":
        """Builds the configuration of the wallbox one car charges at.

        The station set is the only thing that distinguishes one charger from another — it names
        the rating, and the rating is what the lower charging threshold is
        :attr:`LOWER_THRESHOLD_AS_SHARE_OF_CHARGING_POWER` of — so this is the only builder the
        class has::

            ChargingStationConfig.for_charging_station_set(
                "L1EVChargeControl_1", ChargingStationSets.Charging_At_Home_with_11_kW
            )

        Args:
            name: Instance name of the charge controller; its ``ComponentID`` is built from it.
            charging_station_set: One member of ``utspclient.helpers.lpgdata
                .ChargingStationSets``, the same catalogue the car's driving profile is
                generated against.

        Returns:
            A fresh configuration of that station; nothing about it is shared with any other
            instance.

        Raises:
            ValueError: If the set's name does not state a rating, see
                :meth:`charging_power_in_watt_of`.
        """
        return cls(
            component_id=ComponentID(name=name),
            charging_station_set=charging_station_set,
            lower_threshold_charging_power_in_watt=(
                cls.charging_power_in_watt_of(charging_station_set)
                * cls.LOWER_THRESHOLD_AS_SHARE_OF_CHARGING_POWER
            ),
        )


class L1ControllerState:
    """Data class, which saves the state of the controller."""

    def __init__(self, power_in_watt: float) -> None:
        """Initializes power for battery charge/discharge in state."""
        self.power_in_watt = power_in_watt

    def clone(self) -> "L1ControllerState":
        """Copy state efficiently."""
        return L1ControllerState(power_in_watt=self.power_in_watt)


class L1Controller(cp.Component):
    """Simulates EV charging and battery losses due to driving. Control according to constant SOC threshold and optional surplus control.

    Components to connect to:
    (1) Car (generic_car)
    (2) Car Battery (advanced_ev_battery_bslib)
    (3) EMS (controller_l2_energy_management_system) - optional
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    ElectricityNeededByCar = "ElectricityNeededByCar"
    CarLocation = "CarLocation"
    StateOfCharge = "StateOfCharge"
    ElectricityTargetFromEMS = "ElectricityTargetFromEMS"
    AcBatteryChargingPower = "AcBatteryChargingPower"

    # Outputs
    ElectricityTargetToOrFromCarBattery = "ElectricityTargetToOrFromCarBattery"
    BatteryChargingPowerToEMS = "BatteryChargingPowerToEMS"

    # Instance attributes declared for static type analysis
    state: L1ControllerState
    previous_state: L1ControllerState
    processed_state: L1ControllerState
    location: str
    charging_location: Optional[int]
    power_delivered_at_charging_station_in_watt: float

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ChargingStationConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Initializes Car."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.state = L1ControllerState(power_in_watt=0)
        self.previous_state = self.state.clone()
        self.processed_state = self.state.clone()
        self.charging_location = None
        self.build(config=config)

        # add inputs
        self.car_consumption_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityNeededByCar,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            mandatory=True,
        )
        self.car_location_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.CarLocation,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            mandatory=True,
        )
        self.state_of_charge_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.StateOfCharge,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            mandatory=True,
        )

        self.ac_battery_power_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.AcBatteryChargingPower,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            mandatory=True,
        )

        self.electricity_target_from_ems_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityTargetFromEMS,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            mandatory=False,
        )

        # add outputs
        self.electricity_to_or_from_car_battery_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityTargetToOrFromCarBattery,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description="Set power for EV charging (and discharging) in Watt.",
        )

        self.battery_charging_power_to_ems_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.BatteryChargingPowerToEMS,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description="Real Power for EV charging in Watt. Signal send to L2EMSElectricityController",
            postprocessing_flag=[InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        )

        self.add_default_connections(self.get_default_connections_from_generic_car())
        self.add_default_connections(self.get_default_connections_advanced_battery())

    def get_default_connections_from_generic_car(self) -> List[cp.ComponentConnection]:
        """Default connections of car in ev charge controller."""

        connections: List[cp.ComponentConnection] = []
        car_classname = generic_car.Car.get_classname()
        connections.append(
            cp.ComponentConnection(
                L1Controller.ElectricityNeededByCar,
                car_classname,
                generic_car.Car.ElectricityOutput,
            )
        )
        connections.append(cp.ComponentConnection(L1Controller.CarLocation, car_classname, generic_car.Car.CarLocation))
        return connections

    def get_default_connections_advanced_battery(self) -> List[cp.ComponentConnection]:
        """Default connections of car battery in ev charge controller."""

        connections: List[cp.ComponentConnection] = []
        battery_classname = advanced_ev_battery_bslib.CarBattery.get_classname()
        connections.append(
            cp.ComponentConnection(
                L1Controller.StateOfCharge,
                battery_classname,
                advanced_ev_battery_bslib.CarBattery.StateOfCharge,
            )
        )
        connections.append(
            cp.ComponentConnection(
                L1Controller.AcBatteryChargingPower,
                battery_classname,
                advanced_ev_battery_bslib.CarBattery.AcBatteryChargingPower,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Saves actual state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Checks statements."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def _handle_discharging_power_in_watt(self, car_consumption_in_watt: float) -> float:
        """Handle discharging case when car is consuming energy.

        When the car is driving or has standby losses, it consumes energy from the battery.
        Returns negative power value indicating discharge.
        """
        return car_consumption_in_watt * (-1)

    def _handle_parking_power_in_watt(self, _car_location: int) -> float:
        """Handle parking case when car is not at charging location.

        When the car is parked but not at the charging location, no charging occurs.
        Returns 0 (no power flow).
        """
        return 0.0

    def _handle_charging_power_in_watt(
        self,
        soc: float,
        electricity_target_in_watt: float
    ) -> float:
        """Handle charging case when car is at charging location.

        Charging logic:
        - If SOC is below threshold, charge at full power
        - If EMS has surplus energy above threshold, charge with surplus (may override SOC-based charging)

        Note: The original implementation had both conditions as separate if statements,
        meaning EMS surplus logic takes precedence over SOC threshold. This behavior is
        preserved for backward compatibility, though the precedence is now explicit in the code.

        Args:
            soc: Current state of charge of the battery (0-1)
            electricity_target_in_watt: Surplus energy from EMS in watts

        Returns:
            Power to charge the car battery in watts (positive value)
        """
        charging_power_in_watt = 0.0

        # If SOC is below threshold, charge at full power
        if soc < self.config.battery_set_soc:
            charging_power_in_watt = self.power_delivered_at_charging_station_in_watt

        # If EMS has surplus energy above threshold, use it for charging
        # Note: This takes precedence over SOC-based charging (original behavior)
        if electricity_target_in_watt > self.config.lower_threshold_charging_power_in_watt:
            charging_power_in_watt = min(
                electricity_target_in_watt,
                self.power_delivered_at_charging_station_in_watt
            )

        return charging_power_in_watt

    def control_power_in_watt(
        self,
        car_consumption_in_watt: float,
        car_location: int,
        soc: float,
        electricity_target_in_watt: float,
    ) -> float:
        """Control the EV charging and discharging based on car state and energy availability.

        This method determines the power flow to/from the EV battery based on:
        - Car consumption (driving vs parked)
        - Car location (at charging station or not)
        - Battery state of charge
        - Available surplus energy from EMS

        Args:
            car_consumption_in_watt: Current power consumption of the car in watts (positive when consuming)
            car_location: Current location of the car (1=Home, 2=Work, etc.)
            soc: Current state of charge of the battery (0-1)
            electricity_target_in_watt: Surplus energy from EMS in watts

        Returns:
            Power to/from the car battery in watts. Positive = charging, Negative = discharging

        Raises:
            ValueError: If car_consumption_in_watt is negative (car cannot produce energy)
        """
        # DISCHARGING: car is consuming energy (driving or standby losses)
        if car_consumption_in_watt > 0.0:
            return self._handle_discharging_power_in_watt(car_consumption_in_watt)

        # CHARGING or PARKING: car is not driving
        if car_consumption_in_watt == 0.0:
            # PARKING: car is not at charging location
            if car_location != self.charging_location:
                return self._handle_parking_power_in_watt(car_location)

            # CHARGING: car is at charging location
            return self._handle_charging_power_in_watt(soc, electricity_target_in_watt)

        raise ValueError(
            f"Car consumption cannot be negative, otherwise car would be producing energy: {car_consumption_in_watt}"
        )

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Returns battery charge and discharge (energy consumption of car) of battery at each timestep."""
        if force_convergence:
            self.state = self.processed_state.clone()
        else:
            car_location = int(stsv.get_input_value(self.car_location_channel))
            car_consumption_in_watt = stsv.get_input_value(self.car_consumption_channel)
            soc = stsv.get_input_value(self.state_of_charge_channel)
            if self.electricity_target_from_ems_channel.source_output is not None:
                electricity_target_from_ems_in_watt = stsv.get_input_value(self.electricity_target_from_ems_channel)
            else:
                electricity_target_from_ems_in_watt = 0
            self.state.power_in_watt = self.control_power_in_watt(
                car_consumption_in_watt,
                car_location=car_location,
                soc=soc,
                electricity_target_in_watt=electricity_target_from_ems_in_watt,
            )
            self.processed_state = self.state.clone()
        stsv.set_output_value(self.electricity_to_or_from_car_battery_channel, self.state.power_in_watt)

        # get current charging power from car battery
        ac_battery_power_in_watt = stsv.get_input_value(self.ac_battery_power_channel)
        if ac_battery_power_in_watt > 0:
            # charging of EV is communicated to EMS
            stsv.set_output_value(self.battery_charging_power_to_ems_channel, ac_battery_power_in_watt)
        else:
            # no charging of EV
            stsv.set_output_value(self.battery_charging_power_to_ems_channel, 0)

    def build(
        self,
        config: ChargingStationConfig,
    ) -> None:
        """Translates and assigns config parameters to controller class and completes initialization."""
        self.name = config.component_id.name
        self.source_weight = config.source_weight
        self.config = config
        # get charging station location and charging station power out of ChargingStationSet
        if config.charging_station_set.Name is not None:
            charging_station_string = config.charging_station_set.Name.partition("At ")[2]
            self.location = charging_station_string.partition(" with")[0]
            if self.location == "Home":
                self.charging_location = 1
            elif self.location == "Work":
                self.charging_location = 2
        else:
            log.error(
                'Charging location not known, check the input on the charging station set. It was set to "charging at home per default.'
            )
            self.location = "Home"
            self.charging_location = 1
            charging_station_string = "At Home with 0.37 kW"
        power_in_watt = float(charging_station_string.partition("with ")[2].partition(" kW")[0]) * 1e3
        self.power_delivered_at_charging_station_in_watt = power_in_watt

    def write_to_report(self) -> List[str]:
        """Writes EV charge controller values to report."""
        lines = []
        lines.append(f"{self.name}_w{self.source_weight}charging controller: ")
        lines.append(f"Power [kW]: {self.power_delivered_at_charging_station_in_watt * 1e-3:2.1f}")
        if self.charging_location == 1:
            lines.append("At Home")
        elif self.charging_location == 2:
            lines.append("At Work")
        return lines

    @staticmethod
    def get_cost_capex(
        config: ChargingStationConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""

        component_type = ComponentType.CAR_BATTERY
        kpi_tag = KpiTagEnumClass.CAR_BATTERY
        unit = Units.ANY
        size_of_energy_system = 1

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=component_type,
            unit=unit,
            size_of_energy_system=size_of_energy_system,
            config=config,
            kpi_tag=kpi_tag,
        )

        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs snd write total energy consumption to component-config.

        No electricity costs for components except for Electricity Meter,
        because part of electricity consumption is feed by PV

        elecricity_consumption is calculated for generic_car already
        """
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.CAR_BATTERY,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries = []
        car_location_kpi_entry = KpiEntry(
            name="Car charging location",
            unit="-",
            value=self.location,
            tag=KpiTagEnumClass.CAR,
            description=self.component_name,
        )
        list_of_kpi_entries.append(car_location_kpi_entry)

        charging_power_kpi_entry = KpiEntry(
            name="Power delivered at charging station",
            unit="kW",
            value=self.power_delivered_at_charging_station_in_watt * 1e-3,
            tag=KpiTagEnumClass.CAR,
            description=self.component_name,
        )
        list_of_kpi_entries.append(charging_power_kpi_entry)
        return list_of_kpi_entries
