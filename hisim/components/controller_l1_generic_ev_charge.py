# -*- coding: utf-8 -*-
"""Charge controller of an electric car's battery, with its configuration."""

import math
from typing import ClassVar, List, Optional
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json

import pandas as pd

from utspclient.helpers.lpgpythonbindings import JsonReference

from hisim.simulationparameters import SimulationParameters
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import generic_car
from hisim.components import advanced_ev_battery_bslib
from hisim.loadtypes import Units, ComponentType
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

    The controller charges the car at full station power whenever it is at the station and its
    state of charge is below :attr:`battery_set_soc`. At or above the set point it charges only
    with the surplus the energy manager offers, and only when that offer exceeds
    :attr:`lower_threshold_charging_power_in_watt`, below which the station's efficiency collapses.
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
    #: set point for the battery's state of charge, from 0 to 1 (the controller refuses any other value): below it
    #: the car charges at full station power, at or above it only with an offered surplus above the lower threshold
    battery_set_soc: float = field(default=0.8, metadata={"unit": lt.Units.ANY})
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


class L1Controller(cp.StatelessComponent, cp.L1ControllerComponent):
    """Commands an electric car's battery: its charging at the station and its discharging while the car drives.

    The controller decides every pass from its inputs alone and keeps no state from one step to the next, so it
    inherits :class:`~hisim.component.StatelessComponent`, whose save and restore do nothing:

    * the car drives: the battery gives the car's consumption;
    * the car is parked away from its charging station: nothing;
    * the car is parked at its station and the battery's state of charge at the start of the step is below
      :attr:`ChargingStationConfig.battery_set_soc`: full station power, whatever surplus the energy manager offers;
    * the car is parked at its station at or above its set point: the surplus the energy manager offers the car, up
      to the station power, when that offer is above
      :attr:`ChargingStationConfig.lower_threshold_charging_power_in_watt`, and nothing otherwise.

    For example, with an 11 kW station and a set point of 0.4, a car starting the step at 0.396 charges at 11 kW
    whether it is offered nothing or 3,000 W of surplus, a car at 0.6 offered 3,000 W charges at 3,000 W, and a car
    at 0.6 offered 500 W, below the 1,100 W threshold, does not charge.

    Why the set point comes first: it is there to have the car charged before it drives. Charged with a surplus
    below the station power, a car below its set point might not reach the set point before it leaves, and its
    charging power would fall from the station power to the offer as soon as the offer crossed the threshold, to a
    tenth of the station power at the threshold itself.

    Why both inputs are taken as they are: the start-of-step state of charge does not move while the step
    iterates, and the offer is the surplus before the car takes its share, so the car's own charging changes
    neither. A decision on the end-of-step state of charge, or on the surplus left after the car, would feed the
    car's charging back into its own decision and alternate between the passes of a step.

    Components to connect to:
    (1) Car (generic_car)
    (2) Car Battery (advanced_ev_battery_bslib)
    (3) EMS (controller_l2_energy_management_system) - optional
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    ElectricityNeededByCar = "ElectricityNeededByCar"
    CarLocation = "CarLocation"
    StateOfChargeAtStartOfStep = "StateOfChargeAtStartOfStep"
    ElectricityTargetFromEMS = "ElectricityTargetFromEMS"
    AcBatteryChargingPower = "AcBatteryChargingPower"

    # Outputs
    ElectricityTargetToOrFromCarBattery = "ElectricityTargetToOrFromCarBattery"
    BatteryChargingPowerToEMS = "BatteryChargingPowerToEMS"

    # Instance attributes declared for static type analysis
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
        self.state_of_charge_at_start_of_step_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.StateOfChargeAtStartOfStep,
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
            output_description=(
                "The car battery's AC charging power, in W, >= 0, as the controller reports it to the energy manager. "
                "A signal for the manager's decisions: the electricity meter and the KPIs read the battery's own "
                "AcBatteryChargingPower."
            ),
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
                L1Controller.StateOfChargeAtStartOfStep,
                battery_classname,
                # the state of charge the step starts with: the controller decides on a value the step's
                # iteration does not move
                advanced_ev_battery_bslib.CarBattery.StateOfChargeAtStartOfStep,
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
        """Return the charging power of a car parked at its charging station, in W.

        Below its set point the car charges at full station power, whatever surplus it is offered. At or above its
        set point it charges with the offered surplus, up to the station power, when the offer exceeds the lower
        charging threshold, and does not charge otherwise. For example, with an 11 kW station, a set point of 0.4
        and a threshold of 1,100 W: at 0.3 offered 3,000 W the car charges at 11,000 W; at 0.6 offered 3,000 W at
        3,000 W; at 0.6 offered 1,000 W not at all.

        Why the set point comes first: the set point is there to have the car charged before it drives, so a
        surplus never slows the charging below it.

        Args:
            soc: The battery's state of charge at the start of the step, between 0 and 1.
            electricity_target_in_watt: The surplus the energy manager offers the car, in W: what is left before
                the car takes its share, negative when there is none.

        Returns:
            The power to charge the car battery with, in W, at least 0.
        """
        if soc < self.config.battery_set_soc:
            return self.power_delivered_at_charging_station_in_watt
        if electricity_target_in_watt > self.config.lower_threshold_charging_power_in_watt:
            return min(electricity_target_in_watt, self.power_delivered_at_charging_station_in_watt)
        return 0.0

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
            soc: The battery's state of charge at the start of the step, between 0 and 1.
            electricity_target_in_watt: The surplus the energy manager offers the car, in W, before the car takes
                its share; negative when there is none.

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
        """Command the car battery's power for this pass, and report the battery's charging power to the energy manager.

        The command follows from the inputs alone (:meth:`control_power_in_watt`): the car's consumption and
        location, the battery's state of charge at the start of the step and the surplus the energy manager offers
        the car. For example, a parked car at its station starting the step at 0.396, below a set point of 0.4,
        is commanded the station's 11,000 W in every pass of the step.

        Under ``force_convergence`` the controller decides as in any other pass. Why: it keeps no state, so there
        is no earlier decision to hold, and its decision on the inputs of the forced pass is the one consistent
        with them.

        Args:
            timestep: The index of the current step.
            stsv: The step values the inputs are read from and the outputs written to.
            force_convergence: Whether the simulator forces the step to converge; it does not change the decision.
        """
        car_location = int(stsv.get_input_value(self.car_location_channel))
        car_consumption_in_watt = stsv.get_input_value(self.car_consumption_channel)
        state_of_charge_at_start_of_step = stsv.get_input_value(self.state_of_charge_at_start_of_step_channel)
        if self.electricity_target_from_ems_channel.source_output is not None:
            surplus_offered_in_watt = stsv.get_input_value(self.electricity_target_from_ems_channel)
        else:
            surplus_offered_in_watt = 0
        power_in_watt = self.control_power_in_watt(
            car_consumption_in_watt,
            car_location=car_location,
            soc=state_of_charge_at_start_of_step,
            electricity_target_in_watt=surplus_offered_in_watt,
        )
        stsv.set_output_value(self.electricity_to_or_from_car_battery_channel, power_in_watt)

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
        """Translates and assigns config parameters to controller class and completes initialization.

        The set point is checked here rather than when the configuration is made, because setups assign it after
        building the configuration (``config.battery_set_soc = 0.4``). For example, 0.4 and 1.0 (always charge at
        full station power) are accepted; 80, meant as a percentage, is refused: the battery's state of charge would
        always be below it, so the car would charge at full station power and ignore every offered surplus.

        Raises:
            ValueError: If :attr:`ChargingStationConfig.battery_set_soc` is not a finite number from 0 to 1.
        """
        if not math.isfinite(config.battery_set_soc) or not 0.0 <= config.battery_set_soc <= 1.0:
            raise ValueError(
                f"The charge controller {config.component_id.name}'s battery_set_soc must be a state of charge from 0 "
                f"to 1, got {config.battery_set_soc!r}: below it the car charges at full station power whatever "
                "surplus it is offered."
            )
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
