"""Car Battery implementation built upon the bslib library. It contains a CarBattery Class together with its Configuration and State."""

# Import packages from standard library or the environment e.g. pandas, numpy etc.
import importlib
from dataclasses import dataclass
from typing import Any, ClassVar, List

import pandas as pd
from bslib import bslib as bsl
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim.component import (
    PhysicsComponent,
    ComponentConnection,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
    OpexCostDataClass,
    CapexCostDataClass,
)
from hisim.components.advanced_battery_bslib import Battery
from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.energy_port import EnergyPort
from hisim.loadtypes import ComponentType, EnergyBalanceCarrier, EnergyRole, InandOutputType, LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry, KpiHelperClass
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class CarBatteryConfig(ConfigBase):
    """Configuration of the battery of one electric car.

    The battery is an AC-coupled storage simulated by the bslib library: ``system_id`` picks the
    inverter characteristic out of that library's database, and the two ``*_custom`` numbers
    override the rated power and capacity that come with it. The named default is
    :meth:`preset_standard`, a 30 kWh pack charging and discharging at 10 kW::

        CarBatteryConfig.preset_standard("CarBattery_1")

    A system with more than one car gives each battery its own ``source_weight``, which is what
    the charge controller and the energy management system match a car to its battery by.
    """

    MAIN_CLASS = "hisim.components.advanced_ev_battery_bslib.CarBattery"

    component_id: ComponentID
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int = 1
    #: name of battery to search in database (bslib)
    system_id: str = "SG1"
    #: charging and discharging power in Watt
    p_inv_custom: float = 1e4
    #: battery capacity in in kWh
    e_bat_custom: float = 30.0
    #: amount of energy used to charge the car battery. Not an author's input: the component
    #: writes the figure into its own configuration during post-processing (see
    #: ``get_cost_opex`` and ``get_component_kpi_entries``), so it starts at zero and holds a
    #: result once the run is over.
    total_charged_energy_in_kilowatthour: float = 0.0
    #: amount of energy discharged from the battery. Not an author's input either, written by
    #: the component in the same two places.
    total_discharged_energy_in_kilowatthour: float = 0.0

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "CarBatteryConfig":
        """The one car battery the fleet runs: a 30 kWh pack charging at 10 kW.

        The field defaults are that pack — bslib's ``SG1`` inverter characteristic, 10 kW of
        charging and discharging power, 30 kWh of capacity — at source weight 1, which is the
        single-car case. A second car overrides the weight after building.

        Args:
            name: Instance name of the battery in the simulation.

        Returns:
            The configuration, fully concrete -- the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


class CarBattery(PhysicsComponent):
    """The battery of one electric car: it charges from the house and discharges into the car's drive.

    Simulate state of charge and realized power of a ac coupled battery
    storage system with the bslib library. Relevant simulation parameters
    are loaded within the init for a specific or generic battery type.

    Its energy ports: the AC charging power is electricity the electricity meter reads (``CONSUMPTION``); the
    discharging power is electricity the car reads straight from the battery, past every meter (``PRODUCTION`` of
    the carrier ``VEHICLE_ELECTRICITY``, which no meter reads); the inverter and the cells lose energy while it charges
    (two ``LOSS`` ports) and the stored energy changes (``STORED_CHANGE``). The discharge is modelled without loss:
    the car's consumption per kilometre already contains it.

    Components to connect to:
    (1) CarBattery controller (controller_l1_generic_ev_charge)
    """

    cost_relevance = CostRelevance.PRICED

    # Inputs
    TargetPowerToOrFromBattery = "TargetPowerToOrFromBattery"  # W

    # Outputs
    AcBatteryChargingPower = "AcBatteryChargingPower"  # W
    DcBatteryChargingPower = "DcBatteryChargingPower"  # W
    StateOfCharge = "StateOfCharge"  # [0..1]
    #: The state of charge the step starts with, between 0 and 1: the committed state, which no pass of the step
    #: moves. The charge controller decides on it (``controller_l1_generic_ev_charge.L1Controller``).
    StateOfChargeAtStartOfStep = "StateOfChargeAtStartOfStep"  # [0..1]
    AcBatteryDischargingPower = "AcBatteryDischargingPower"  # W
    #: The increase of the energy stored in the cells over the step, in Wh; negative while the battery discharges.
    StoredEnergyIncreaseInWattHour = "StoredEnergyIncreaseInWattHour"
    #: The inverter's loss over the step, in W: the AC charging power minus the DC charging power.
    InverterLossInWatt = "InverterLossInWatt"
    #: The cells' loss over the step, in W: the DC power into the cells minus the increase of the stored energy per
    #: second.
    CellLossInWatt = "CellLossInWatt"

    #: Watt hours per kilowatt hour: the configuration states the capacity in kWh, the stored energy is in Wh.
    WATT_HOURS_PER_KILOWATT_HOUR: ClassVar[float] = 1000.0

    #: The state of charge before the first step, dimensionless between 0 and 1: a half-charged car battery, which
    #: can take a charge and cover a first trip from the first step, rather than an empty or a full one.
    INITIAL_STATE_OF_CHARGE: ClassVar[float] = 0.5

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: CarBatteryConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Loads the parameters of the specified battery storage."""
        self.battery_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.source_weight = self.battery_config.source_weight

        self.system_id = self.battery_config.system_id

        self.p_inv_custom = self.battery_config.p_inv_custom

        self.e_bat_custom = self.battery_config.e_bat_custom
        #: The capacity the state of charge is a share of, in Wh.
        self.capacity_in_watt_hour = self.e_bat_custom * self.WATT_HOURS_PER_KILOWATT_HOUR

        # Component has states
        self.state = EVBatteryState(soc=self.INITIAL_STATE_OF_CHARGE)
        self.previous_state = self.state.clone()

        # Load battery object with parameters from bslib database
        self.bat = bsl.ACBatMod(
            system_id=self.system_id,
            p_inv_custom=self.p_inv_custom,
            e_bat_custom=self.e_bat_custom,
        )

        # Define component inputs
        self.p_set: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TargetPowerToOrFromBattery,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            mandatory=True,
        )

        # Define component outputs
        self.p_bs: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.AcBatteryChargingPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[
                InandOutputType.CHARGE,
                ComponentType.CAR_BATTERY,
                InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
            ],
            output_description=(
                "The AC power the battery charges with, in W, >= 0: the electricity the car takes from the house, "
                "which the electricity meter reads and the KPIs count as consumption."
            ),
            energy_port=EnergyPort(EnergyRole.CONSUMPTION, EnergyBalanceCarrier.ELECTRICITY),
        )

        self.p_bat: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DcBatteryChargingPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description="Charging power of the battery in Watt (Direct current).",
        )

        self.soc: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.StateOfCharge,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            postprocessing_flag=[InandOutputType.STORAGE_CONTENT],
            output_description="State of charge of the battery.",
        )
        self.state_of_charge_at_start_of_step_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.StateOfChargeAtStartOfStep,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description=(
                "State of charge of the battery at the start of the step, between 0 and 1. It is the state the "
                "previous step ended with, so it is the same in every pass of a step."
            ),
        )
        self.p_bs_discharge: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.AcBatteryDischargingPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[
                InandOutputType.DISCHARGE,
                ComponentType.CAR_BATTERY,
            ],
            output_description=(
                "The power the battery discharges into the car's drive, in W, >= 0. The car reads it; it passes no "
                "meter, and the KPIs do not count it as production."
            ),
            # the car takes this electricity straight from its battery, past every meter
            energy_port=EnergyPort(EnergyRole.PRODUCTION, EnergyBalanceCarrier.VEHICLE_ELECTRICITY),
        )
        self.stored_energy_increase_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.StoredEnergyIncreaseInWattHour,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT_HOUR,
            postprocessing_flag=[InandOutputType.STORED_ENERGY_INCREASE, ComponentType.CAR_BATTERY],
            output_description=(
                "The increase of the energy stored in the cells over the step, in Wh: the change of the state of "
                "charge times the capacity, negative while the battery discharges."
            ),
            energy_port=EnergyPort(EnergyRole.STORED_CHANGE, EnergyBalanceCarrier.ELECTRICITY),
        )
        # bslib is a black box that publishes the AC power, the DC power and the state of charge; the two losses are
        # their differences, so the energy balance checks that the published flows agree with each other.
        self.inverter_loss_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.InverterLossInWatt,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=(
                "The inverter's loss over the step, in W: the AC charging power minus the DC charging power. The "
                "discharge is modelled without loss, so it is zero while the battery discharges."
            ),
            energy_port=EnergyPort(EnergyRole.LOSS, EnergyBalanceCarrier.ELECTRICITY),
        )
        self.cell_loss_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CellLossInWatt,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=(
                "The cells' loss over the step, in W: the DC power into the cells minus the increase of the stored "
                "energy divided by the step's duration."
            ),
            energy_port=EnergyPort(EnergyRole.LOSS, EnergyBalanceCarrier.ELECTRICITY),
        )

        self.add_default_connections(self.get_default_connections_from_charge_controller())

    def get_default_connections_from_charge_controller(self) -> Any:
        """Get default connections from charge controller."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.controller_l1_generic_ev_charge"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "L1Controller")
        connections: List[ComponentConnection] = []
        ev_charge_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                CarBattery.TargetPowerToOrFromBattery,
                ev_charge_controller_classname,
                component_class.ElectricityTargetToOrFromCarBattery,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """No-op hook for post-convergence verification.

        This component does not perform a double-check pass; the method exists
        to satisfy the Component interface.

        Args:
            timestep: Current simulation timestep index.
            stsv: Single-time-step values container (unused).
        """
        pass

    def i_prepare_simulation(self) -> None:
        """No-op hook for simulation preparation.

        This component does not perform any pre-simulation setup; the method
        exists to satisfy the Component interface.
        """
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate one timestep of AC-coupled EV battery charging or discharging.

        When the target power is positive the bslib ACBatMod model is used to
        compute AC/DC charging power and the resulting state of charge. When the
        target power is negative the battery is discharged with a simple loss-free
        model (losses are accounted for in the car consumption component) and a
        ValueError is raised if the SOC would drop below zero.

        Beside the end-of-step state of charge the battery publishes the state of charge the step starts
        with. For example, a battery at 0.396 that charges 0.005 in the step publishes 0.396 as its start and
        0.401 as its end. Why: a controller that decides "charge while below 0.4" on the end-of-step value
        has no consistent answer in that step (charging lifts the value above 0.4, which stops the charging,
        which leaves it below 0.4), so the passes of the step alternate until the simulator forces them.

        Args:
            timestep: Current simulation timestep index.
            stsv: Single-time-step values container used to read inputs and write
                outputs (AcBatteryChargingPower, DcBatteryChargingPower,
                StateOfCharge, StateOfChargeAtStartOfStep, AcBatteryDischargingPower,
                StoredEnergyIncreaseInWattHour, InverterLossInWatt, CellLossInWatt).
            force_convergence: Unused by this component.
        """
        # Parameters
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep

        # Load input values
        target_power_to_or_from_car_battery_in_watt = stsv.get_input_value(self.p_set)
        soc = self.state.soc
        state_of_charge_at_start_of_step = soc
        stsv.set_output_value(self.state_of_charge_at_start_of_step_channel, soc)

        # Simulate battery charging
        if target_power_to_or_from_car_battery_in_watt >= 0.0:
            results = self.bat.simulate(
                p_load=target_power_to_or_from_car_battery_in_watt, soc=soc, dt=seconds_per_timestep
            )
            ac_charging_power_in_watt = results[0]
            dc_charging_power_in_watt = results[1]
            soc = results[2]
            discharging_power_ac_in_watt = 0.0

        # Simulate battery discharge without losses (this is included in the car consumption of the car component)
        else:
            # make it positive
            discharging_power_ac_in_watt = (-1) * target_power_to_or_from_car_battery_in_watt
            soc = soc - (discharging_power_ac_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3600) / (
                self.e_bat_custom * 1e3
            )
            if soc < 0:
                raise ValueError(
                    "Car cannot drive, because battery is empty."
                    "This points towards a major problem in the battery configuration - or the consumption pattern of the car."
                )
            ac_charging_power_in_watt = 0.0
            dc_charging_power_in_watt = 0.0

        # write values for output time series
        stsv.set_output_value(self.p_bs, ac_charging_power_in_watt)
        stsv.set_output_value(self.p_bat, dc_charging_power_in_watt)
        stsv.set_output_value(self.soc, soc)
        stsv.set_output_value(self.p_bs_discharge, discharging_power_ac_in_watt)
        stored_energy_increase_in_watt_hour = (soc - state_of_charge_at_start_of_step) * self.capacity_in_watt_hour
        stsv.set_output_value(self.stored_energy_increase_channel, stored_energy_increase_in_watt_hour)
        # the discharge is loss-free, so it is the same power on the AC and on the DC side
        inverter_loss_in_watt, cell_loss_in_watt = Battery.losses_in_watt(
            ac_power_in_watt=ac_charging_power_in_watt - discharging_power_ac_in_watt,
            dc_power_in_watt=dc_charging_power_in_watt - discharging_power_ac_in_watt,
            stored_energy_increase_in_watt_hour=stored_energy_increase_in_watt_hour,
            seconds_per_timestep=seconds_per_timestep,
        )
        stsv.set_output_value(self.inverter_loss_channel, inverter_loss_in_watt)
        stsv.set_output_value(self.cell_loss_channel, cell_loss_in_watt)

        # write values to state
        self.state.soc = soc

    def write_to_report(self) -> List[str]:
        """Writes Car Battery values to report."""
        return self.battery_config.get_string_dict()

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs.

        No electricity costs for components except for Electricity Meter,
        because part of electricity consumption is feed by PV

        electricity_consumption is considered vor generic_car already
        """
        battery_losses_in_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            # get charged energy
            if (
                output.postprocessing_flag is not None
                and output.component_name == self.component_name
                and output.field_name == self.AcBatteryChargingPower
                and output.load_type == LoadTypes.ELECTRICITY
                and output.unit == Units.WATT
            ):

                self.battery_config.total_charged_energy_in_kilowatthour = round(
                    KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=postprocessing_results.iloc[:, index],
                        time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                    ),
                    1,
                )

            # get discharged energy
            if (
                output.postprocessing_flag is not None
                and output.component_name == self.component_name
                and output.field_name == self.AcBatteryDischargingPower
                and output.load_type == LoadTypes.ELECTRICITY
                and output.unit == Units.WATT
            ):
                # AcBatteryDischargingPower is non-negative: it is 0.0 while
                # charging and a positive power (W) while discharging, so
                # summing the full timeseries yields the total discharged energy.
                self.battery_config.total_discharged_energy_in_kilowatthour = round(
                    KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=postprocessing_results.iloc[:, index],
                        time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                    ),
                    1,
                )
        # calculate battery losses
        battery_losses_in_kwh = (
            self.battery_config.total_charged_energy_in_kilowatthour
            - self.battery_config.total_discharged_energy_in_kilowatthour
        )
        # Todo: Battery Aging like in component advanced_battery_bslib? Or is this considered in maintenance cost of car?

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=battery_losses_in_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.CAR_BATTERY,
        )

        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: CarBatteryConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []

        battery_losses_in_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            # get charged energy
            if (
                output.postprocessing_flag is not None
                and output.component_name == self.component_name
                and output.field_name == self.AcBatteryChargingPower
                and output.load_type == LoadTypes.ELECTRICITY
                and output.unit == Units.WATT
            ):

                self.battery_config.total_charged_energy_in_kilowatthour = round(
                    KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=postprocessing_results.iloc[:, index],
                        time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                    ),
                    1,
                )
                my_kpi_entry_1 = KpiEntry(
                    name="Total charged electricity for electric car",
                    unit="kWh",
                    value=self.battery_config.total_charged_energy_in_kilowatthour,
                    tag=KpiTagEnumClass.CAR_BATTERY,
                    description=self.component_name,
                )
                list_of_kpi_entries.append(my_kpi_entry_1)

            # get discharged energy
            if (
                output.postprocessing_flag is not None
                and output.component_name == self.component_name
                and output.field_name == self.AcBatteryDischargingPower
                and output.load_type == LoadTypes.ELECTRICITY
                and output.unit == Units.WATT
            ):
                # AcBatteryDischargingPower is non-negative: it is 0.0 while
                # charging and a positive power (W) while discharging, so
                # summing the full timeseries yields the total discharged energy.
                self.battery_config.total_discharged_energy_in_kilowatthour = round(
                    KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=postprocessing_results.iloc[:, index],
                        time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                    ),
                    1,
                )
                my_kpi_entry_2 = KpiEntry(
                    name="Total discharged electricity for electric car",
                    unit="kWh",
                    value=self.battery_config.total_discharged_energy_in_kilowatthour,
                    tag=KpiTagEnumClass.CAR_BATTERY,
                    description=self.component_name,
                )
                list_of_kpi_entries.append(my_kpi_entry_2)

        # calculate battery losses
        battery_losses_in_kwh = round(
            self.battery_config.total_charged_energy_in_kilowatthour
            - self.battery_config.total_discharged_energy_in_kilowatthour,
            1,
        )
        my_kpi_entry = KpiEntry(
            name="Total losses of car battery",
            unit="kWh",
            value=battery_losses_in_kwh,
            tag=KpiTagEnumClass.CAR_BATTERY,
            description=self.component_name,
        )
        list_of_kpi_entries.append(my_kpi_entry)

        return list_of_kpi_entries


@dataclass
class EVBatteryState:
    """The car battery's state between steps: its state of charge.

    The state of charge has no default: the car battery states its start value once, as
    :attr:`CarBattery.INITIAL_STATE_OF_CHARGE`.
    """

    #: State of charge of the battery, dimensionless between 0 and 1.
    soc: float

    def clone(self):
        """Creates a copy of the Car Battery State."""
        return EVBatteryState(soc=self.soc)
