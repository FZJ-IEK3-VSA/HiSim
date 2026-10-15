""" Battery implementation built upon the bslib library. It contains a Battery Class together with its Configuration and State. """

# Import packages from standard library or the environment e.g. pandas, numpy etc.
import dataclasses
import math
from typing import ClassVar, List, Tuple, Optional
from dataclasses import dataclass
from bslib import bslib as bsl
from dataclasses_json import dataclass_json

import pandas as pd

# Import modules from HiSim
from hisim.component import (
    PhysicsComponent,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
    OpexCostDataClass,
    CapexCostDataClass,
)
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    Many,
    Sizable,
    Size,
    SizingLaw,
    Sum,
    concrete,
    preset,
    sized_field,
)
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.economics.facts import ComponentCostFacts, CostRelevance
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import LoadTypes, Units, InandOutputType, ComponentType
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry, KpiHelperClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions


@dataclass_json
@dataclass
class BatteryConfig(ConfigBase):
    """Battery Configuration.

    Both power numbers are sizable. The class laws size the battery to **every** PV array it
    stands beside: :data:`CAPACITY_LAW` and :data:`INVERTER_POWER_LAW` sum the peak power of the
    arrays its ``sizing_sources`` list names (``Sum(Many(...))``, ``assemblies_spec.md`` §6, D10),
    which is what an assembly's ``many: true`` fact port lowers to; without a ``sizing_sources``
    line they read every array of the system. The named default battery, :meth:`preset_sized_to_pv`,
    leaves both fields ``AUTO`` to these laws (D27). Over one array the sum is that array's peak
    power, so a battery beside one array — every recorded twin — is sized as before. An author who
    knows the device pins the two fields instead.
    """

    MAIN_CLASS = "hisim.components.advanced_battery_bslib.Battery"

    #: Sizing law of the battery's capacity: one kilowatt hour of storage per kilowatt peak of
    #: every PV array the battery is sized to, summed, rounded to two decimals. Named as a ClassVar
    #: so the field declaration reads as one line and the rule of thumb is written down in one place.
    #: See https://www.energieinstitut.at/die-richtige-groesse-von-batteriespeichern/
    CAPACITY_LAW: ClassVar[SizingLaw] = (Sum(Many(Size.PV_PEAK_POWER_IN_WATT)) * 1e-3).rounded(2)

    #: Sizing law of the charging and discharging power: a C-rate of 0.5 (half the capacity per
    #: hour) on the capacity the law above gives, which is the summed peak power in watt times
    #: 0.5. It reads the fact rather than the sibling capacity field on purpose: the capacity is
    #: rounded to two decimals before it is stored, and inverting that rounding into the inverter
    #: power would move the number by about a watt on a fleet-sized array.
    INVERTER_POWER_LAW: ClassVar[SizingLaw] = (Sum(Many(Size.PV_PEAK_POWER_IN_WATT)) * 0.5).rounded(2)

    #: structured identity (name, building, unit) of the component
    component_id: ComponentID
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int = 1
    #: The battery to look up in the bslib database, by that database's own identifier;
    #: SG1 is its generic lithium-ion system.
    system_id: str = "SG1"
    #: amount of energy used to charge the battery, i.e. the state it starts in
    charge_in_kwh: float = 0
    #: amount of energy discharged from the battery, i.e. the state it starts in
    discharge_in_kwh: float = 0
    #: CO2 footprint of investment in kg. Unset throughout the repository, which is what makes
    #: postprocessing look the device up in the cost database instead.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None
    #: lifetime of battery in full cycles; estimated, see
    #: https://pv-held.de/wie-lange-haelt-batteriespeicher-photovoltaik/
    lifetime_in_cycles: float = 5e3
    #: charging and discharging power in Watt. Sizable: left ``AUTO`` it is computed by
    #: :data:`INVERTER_POWER_LAW` from the peak power the arrays contribute.
    custom_pv_inverter_power_generic_in_watt: Sizable[float] = sized_field(rule=INVERTER_POWER_LAW, unit=Units.WATT)
    #: battery capacity in kWh. Sizable: left ``AUTO`` it is computed by :data:`CAPACITY_LAW`
    #: from the same facts. Marked as the capacity field for the cost engine.
    custom_battery_capacity_generic_in_kilowatt_hour: Sizable[float] = sized_field(
        rule=CAPACITY_LAW, unit=Units.KWH, metadata={"capacity": True}
    )

    @preset
    @classmethod
    def preset_sized_to_pv(cls, name: str) -> "BatteryConfig":
        """The fleet's home battery, scaled to the PV arrays it is installed beside.

        The field defaults are this battery: a single ``SG1`` lithium-ion system from the bslib
        database, first in the energy management hierarchy, starting half charged and rated for five
        thousand full cycles. What the preset does not fix is how big the device is:
        ``custom_battery_capacity_generic_in_kilowatt_hour`` and
        ``custom_pv_inverter_power_generic_in_watt`` stay ``AUTO`` so that :data:`CAPACITY_LAW`
        and :data:`INVERTER_POWER_LAW` derive them from the summed peak power of the arrays, and an
        author who knows the device pins the two fields instead.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            BatteryConfig: The preset configuration, with both power numbers unsized.
        """
        return cls(component_id=ComponentID(name=name))


@dataclass(frozen=True)
class BslibChargingParameters:
    """The parameters of a bslib battery that bound how much it can charge in one step.

    Adapter for the bslib library: bslib publishes no accessor for its capacity, its rated and minimum charging
    powers or its recharge threshold, so :meth:`of_battery` reads them from the private attributes of its
    ``ACBatMod``, in this one place. For example, the generic system ``SG1`` with 11.1 kWh and an 11.1 kW inverter
    gives a capacity of 11,100 Wh, a rated charging power of 11,100 W, a minimum charging power of 202.4 W and a
    recharge threshold of 0.98.

    The bounds describe bslib's generic systems exactly: these have no peripheral consumption, no stationary
    deviation of the charging power and no standby consumption. A system with any of them takes a few watts more or
    less than :meth:`Battery.maximum_charging_power_in_watt` says.
    """

    #: The usable capacity, in Wh.
    capacity_in_watt_hour: float
    #: The rated AC charging power of the converter, in W.
    rated_charging_power_in_watt: float
    #: The smallest AC power the converter charges with, in W; bslib charges nothing at or below it.
    minimum_charging_power_in_watt: float
    #: The state of charge, dimensionless between 0 and 1, the battery must fall below before it charges again once
    #: bslib's recharge hysteresis has been set, which happens only when the state of charge passes 1 (see
    #: :meth:`recharging_is_blocked`).
    recharge_threshold_state_of_charge: float

    def __post_init__(self) -> None:
        """Refuse a parameter that is not a finite number of at least 0, or a recharge threshold above 1.

        bslib describes some systems only partly: the DC-coupled systems ``S3`` and ``S4`` give NaN rated and minimum
        charging powers, and ``INV1`` also a NaN capacity. A NaN bound would reach the energy manager as the battery's
        published charging power and fail there in the first step; refusing it here fails the battery's construction
        instead, with the field that is wrong. For example, SG1 with 10 kWh and 5 kW (10,000 Wh, 5,000 W, 91.15 W,
        0.98) is accepted; a NaN minimum charging power, a capacity of -1 Wh or a recharge threshold of 1.5 is
        refused. The recharge threshold is a state of charge, so it must also be at most 1: above 1, a battery whose
        recharge hysteresis is set would be let charge past full.

        Raises:
            ValueError: If any field is not finite or is below 0, or the recharge threshold is above 1.
        """
        for field_name, value in (
            ("capacity_in_watt_hour", self.capacity_in_watt_hour),
            ("rated_charging_power_in_watt", self.rated_charging_power_in_watt),
            ("minimum_charging_power_in_watt", self.minimum_charging_power_in_watt),
            ("recharge_threshold_state_of_charge", self.recharge_threshold_state_of_charge),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(
                    f"The bslib battery's {field_name} must be a finite number of at least 0, got {value!r}: the "
                    "battery's charging limits are computed from it."
                )
        if self.recharge_threshold_state_of_charge > 1.0:
            raise ValueError(
                "The bslib battery's recharge_threshold_state_of_charge must be a state of charge of at most 1, got "
                f"{self.recharge_threshold_state_of_charge!r}."
            )

    @classmethod
    def of_battery(cls, battery: bsl.ACBatMod, *, system_id: str) -> "BslibChargingParameters":
        """Return the charging parameters of a bslib battery, read from its private attributes.

        Args:
            battery: The bslib battery model.
            system_id: The bslib system the model was built for, e.g. ``"SG1"``; it names the system in a refusal.

        Returns:
            Its charging parameters.

        Raises:
            ValueError: If bslib gives a parameter that is not a finite number of at least 0 for this system, as it
                does for the DC-coupled systems ``S3``, ``S4`` and ``INV1``.
        """
        # bslib keeps these only as private attributes and has no accessor for them.
        # pylint: disable=protected-access
        capacity_in_watt_hour = float(battery._E_BAT)
        rated_charging_power_in_watt = float(battery._P_AC2BAT_IN)
        minimum_charging_power_in_watt = float(battery._P_AC2BAT_MIN)
        recharge_threshold_state_of_charge = float(battery._SOC_THRESHOLD)
        # pylint: enable=protected-access
        try:
            return cls(
                capacity_in_watt_hour=capacity_in_watt_hour,
                rated_charging_power_in_watt=rated_charging_power_in_watt,
                minimum_charging_power_in_watt=minimum_charging_power_in_watt,
                recharge_threshold_state_of_charge=recharge_threshold_state_of_charge,
            )
        except ValueError as error:
            raise ValueError(
                f"The bslib system {system_id!r} cannot be simulated as an AC-coupled battery: {error}"
            ) from error

    @staticmethod
    def recharging_is_blocked(battery: bsl.ACBatMod) -> bool:
        """Return whether a bslib battery waits, after its last simulated step, to fall below its recharge threshold.

        bslib sets this flag only when a step ends with a state of charge above 1, and clears it when the state of
        charge falls below the recharge threshold; while it is set, the battery does not charge. The battery never
        passes 1: bslib caps the AC power at what fills the battery (``avoid_overcharging``), and the minimum charging
        power stops it at about 0.99985 (SG1, 60 s steps). So for SG1 and S2 the flag stays False, and the battery
        reads it only as a defensive half of the adapter, in case a bslib system or version sets it.

        Args:
            battery: The bslib battery model, after a call of its ``simulate``.

        Returns:
            Whether the recharge hysteresis blocks charging.
        """
        return bool(battery._threshold)  # pylint: disable=protected-access  # bslib has no accessor


class Battery(PhysicsComponent):
    """Battery class.

    Simulate state of charge and realized power of a ac coupled battery
    storage system with the bslib library. Relevant simulation parameters
    are loaded within the init for a specific or generic battery type.

    Components to connect to:
    (1) Energy Management System
    """

    # Lifecycle cost engine declaration (roadmap/cost_spec.md §9.2).
    cost_relevance = CostRelevance.PRICED

    #: The seconds in an hour, which convert between energy in Wh and power over a step in s.
    SECONDS_PER_HOUR: ClassVar[float] = 3600.0

    #: The state of charge before the first step, dimensionless between 0 and 1: a half-charged battery. A run
    #: that starts on an empty battery cannot discharge on its first night and one that starts full cannot store
    #: its first day's PV surplus; half charged, the battery can do either from the first step. The energy it holds
    #: at the start is not counted as a loss: the losses subtract the change of the stored energy.
    INITIAL_STATE_OF_CHARGE: ClassVar[float] = 0.5

    # Inputs
    LoadingPowerInput = "LoadingPowerInput"  # W

    # Outputs
    AcBatteryPowerUsed = "AcBatteryPowerUsed"  # W
    DcBatteryPowerUsed = "DcBatteryPowerUsed"  # W
    StateOfCharge = "StateOfCharge"  # [0..1]
    ChargingPower = "ChargingPower"
    DischargingPower = "DischargingPower"
    MaximumChargingPowerInWatt = "MaximumChargingPowerInWatt"
    MinimumChargingPowerInWatt = "MinimumChargingPowerInWatt"
    #: The increase of the energy stored in the cells over the step, in Wh; negative while the battery discharges.
    StoredEnergyIncreaseInWattHour = "StoredEnergyIncreaseInWattHour"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: BatteryConfig,
        my_display_config: Optional[DisplayConfig] = None,
    ):
        """Loads the parameters of the specified battery storage."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
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
        self.negative_soa_warning: bool = True

        self.source_weight = self.battery_config.source_weight

        self.system_id = self.battery_config.system_id

        self.custom_pv_inverter_power_generic_in_watt = concrete(
            self.battery_config.custom_pv_inverter_power_generic_in_watt
        )

        self.custom_battery_capacity_generic_in_kilowatt_hour = concrete(
            self.battery_config.custom_battery_capacity_generic_in_kilowatt_hour
        )

        # Component has states
        self.state = BatteryState(state_of_charge=self.INITIAL_STATE_OF_CHARGE)
        self.previous_state = self.state.clone()

        # Load battery object with parameters from bslib database
        self.ac_coupled_battery_object = bsl.ACBatMod(
            system_id=self.system_id,
            p_inv_custom=self.custom_pv_inverter_power_generic_in_watt,
            e_bat_custom=self.custom_battery_capacity_generic_in_kilowatt_hour,
        )
        self.charging_parameters = BslibChargingParameters.of_battery(
            self.ac_coupled_battery_object, system_id=self.system_id
        )

        # Define component inputs
        self.loading_power_input_channel: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.LoadingPowerInput,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            mandatory=True,
        )

        # Define component outputs
        self.ac_battery_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.AcBatteryPowerUsed,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[InandOutputType.CHARGE_DISCHARGE, ComponentType.BATTERY],
            output_description=f"here a description for {self.AcBatteryPowerUsed} will follow.",
        )

        self.dc_battery_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DcBatteryPowerUsed,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=f"here a description for {self.DcBatteryPowerUsed} will follow.",
        )

        self.state_of_charge_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.StateOfCharge,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            postprocessing_flag=[InandOutputType.STORAGE_CONTENT],
            output_description=f"here a description for {self.StateOfCharge} will follow.",
        )
        self.charging_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ChargingPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=f"here a description for {self.ChargingPower} will follow.",
        )

        self.discharging_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DischargingPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=f"here a description for {self.DischargingPower} will follow.",
        )
        self.maximum_charging_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.MaximumChargingPowerInWatt,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=(
                "The largest AC power the battery can take in this step, from its state at the step's start, in W; "
                "zero or positive. Zero while it is full, while it waits to fall below its recharge threshold, and "
                "while what it could take is not above its minimum charging power."
            ),
        )
        self.minimum_charging_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.MinimumChargingPowerInWatt,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=(
                "The smallest AC power the battery charges with, in W; positive. An offer at or below it is not taken."
            ),
        )
        self.stored_energy_increase_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.StoredEnergyIncreaseInWattHour,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT_HOUR,
            postprocessing_flag=[InandOutputType.STORED_ENERGY_INCREASE, ComponentType.BATTERY],
            output_description=(
                "The increase of the energy stored in the cells over the step, in Wh: the change of the state of "
                "charge times the capacity, negative while the battery discharges. Summed over the run it is the "
                "stored energy at the end minus the stored energy at the start, which the battery's losses subtract."
            ),
        )

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        # Parameters
        time_increment_in_seconds = self.my_simulation_parameters.seconds_per_timestep

        # The limits come from the state at the step's start, so they do not depend on this pass's set point.
        stsv.set_output_value(
            self.maximum_charging_power_channel,
            self.maximum_charging_power_in_watt(
                self.charging_parameters,
                state_of_charge=self.state.state_of_charge,
                recharging_is_blocked=self.state.recharging_is_blocked,
                seconds_per_timestep=time_increment_in_seconds,
            ),
        )
        stsv.set_output_value(self.minimum_charging_power_channel, self.charging_parameters.minimum_charging_power_in_watt)

        # Load input values
        set_point_for_ac_battery_power_in_watt = stsv.get_input_value(self.loading_power_input_channel)
        state_of_charge = self.state.state_of_charge

        # Simulate on timestep.
        # The bslib simulation returns how much of loading power input was actually used
        # for charging and discharging and the resulting state of charge.
        (
            ac_battery_power_used_for_charging_or_discharging_in_watt,
            dc_battery_power_used_for_charging_or_discharging_in_watt,
            state_of_charge,
        ) = self.ac_coupled_battery_object.simulate(
            p_load=set_point_for_ac_battery_power_in_watt,
            soc=state_of_charge,
            dt=time_increment_in_seconds,
        )

        if state_of_charge < 0 and self.negative_soa_warning:
            log.warning("SOC of Battery cannot be negative. Check your configuration.")
            self.negative_soa_warning = False  # make sure that the warning comes only once, otherwise too much logging

        # get charging and discharging power
        if ac_battery_power_used_for_charging_or_discharging_in_watt > 0:
            charging_power_in_watt = ac_battery_power_used_for_charging_or_discharging_in_watt
            discharging_power_in_watt = 0
        elif ac_battery_power_used_for_charging_or_discharging_in_watt < 0:
            charging_power_in_watt = 0
            discharging_power_in_watt = ac_battery_power_used_for_charging_or_discharging_in_watt
        else:
            charging_power_in_watt = 0
            discharging_power_in_watt = 0

        # write values for output time series
        stsv.set_output_value(self.ac_battery_power_channel, ac_battery_power_used_for_charging_or_discharging_in_watt)
        stsv.set_output_value(self.dc_battery_power_channel, dc_battery_power_used_for_charging_or_discharging_in_watt)
        stsv.set_output_value(self.state_of_charge_channel, state_of_charge)
        stsv.set_output_value(
            self.stored_energy_increase_channel,
            (state_of_charge - self.state.state_of_charge) * self.charging_parameters.capacity_in_watt_hour,
        )
        stsv.set_output_value(self.charging_power_channel, charging_power_in_watt)
        stsv.set_output_value(self.discharging_power_channel, discharging_power_in_watt)

        # write values to state
        self.state = BatteryState(
            state_of_charge=state_of_charge,
            recharging_is_blocked=BslibChargingParameters.recharging_is_blocked(self.ac_coupled_battery_object),
        )

    @staticmethod
    def maximum_charging_power_in_watt(
        parameters: BslibChargingParameters, *, state_of_charge: float, recharging_is_blocked: bool, seconds_per_timestep: int
    ) -> float:
        """Return the largest AC power a bslib battery takes in one step, from its state at the step's start, in W.

        The battery charges only while its state of charge lies below 1, or below the recharge threshold while the
        recharge hysteresis blocks it. It then takes at most its rated charging power and at most the power that
        fills its remaining capacity within the step. For example, 11,100 Wh at a state of charge of 0.999 leave
        11.1 Wh, which fill within a 60 s step at 666 W; at 0.5 the rated 11,100 W bound it.

        bslib charges nothing with a power at or below its minimum charging power
        (:attr:`BslibChargingParameters.minimum_charging_power_in_watt`). So when the bound is not above that minimum,
        which happens just below full, the battery takes nothing and the maximum is 0 W. For example, the same battery
        at 0.999 and 900 s steps could fill its 11.1 Wh at 44.4 W, but its minimum of 202.4 W stops it, so the maximum
        is 0 W. Publishing 44.4 W instead would announce a power the battery refuses. An offer at or below the
        minimum is also not taken when the bound is larger.

        Args:
            parameters: The battery's capacity, rated and minimum charging powers and recharge threshold.
            state_of_charge: The state of charge at the step's start, dimensionless between 0 and 1.
            recharging_is_blocked: Whether the recharge hysteresis blocks charging at the step's start.
            seconds_per_timestep: The step length, in s.

        Returns:
            The largest AC power the battery takes in this step, in W; either 0 or above the minimum charging power.

        Raises:
            ValueError: If the step length is not positive.
        """
        if seconds_per_timestep <= 0:
            raise ValueError(f"The step length must be positive, got {seconds_per_timestep} s.")
        charging_limit_state_of_charge = parameters.recharge_threshold_state_of_charge if recharging_is_blocked else 1.0
        if state_of_charge >= charging_limit_state_of_charge:
            return 0.0
        remaining_capacity_in_watt_hour = parameters.capacity_in_watt_hour * (1.0 - state_of_charge)
        filling_power_in_watt = remaining_capacity_in_watt_hour * Battery.SECONDS_PER_HOUR / seconds_per_timestep
        bound_in_watt = min(parameters.rated_charging_power_in_watt, filling_power_in_watt)
        if bound_in_watt <= parameters.minimum_charging_power_in_watt:
            return 0.0
        return bound_in_watt

    def write_to_report(self) -> List[str]:
        """Write to report."""
        return self.battery_config.get_string_dict()

    @staticmethod
    def get_battery_aging_information(config: BatteryConfig) -> Tuple[float, float]:
        """Calculate battery aging.

        This is used to calculate investment costs for battery per simulated period.
        Battery aging is ROUGHLY approximated by costs for each virtual charging cycle used in simulated period
        (costs_per_cycle = investment / lifetime_in_cycles).
        """
        # Todo: Think about better approximation for costs of battery aging

        virtual_number_of_full_charge_cycles = (
            config.charge_in_kwh / concrete(config.custom_battery_capacity_generic_in_kilowatt_hour)
        )
        # virtual_number_of_full_discharge_cycles = self.battery_config.discharge_in_kwh / self.battery_config.custom_battery_capacity_generic_in_kilowatt_hour

        return virtual_number_of_full_charge_cycles, config.lifetime_in_cycles

    @staticmethod
    def get_cost_capex(
        config: BatteryConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""

        # Note for the parity harness: the legacy computation below scales the kWh capacity by
        # 1e-3 (latent unit bug, roadmap/cost_module_issues.md #20a); get_cost_facts() declares the
        # physically correct size, so the parity report shows an explained battery delta.
        component_type = ComponentType.BATTERY
        kpi_tag = KpiTagEnumClass.BATTERY
        unit = Units.KWH
        size_of_energy_system = concrete(config.custom_battery_capacity_generic_in_kilowatt_hour) * 1e-3

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=simulation_parameters,
        component_type=component_type,
        unit=unit,
        size_of_energy_system=size_of_energy_system,
        config=config,
        kpi_tag=kpi_tag
        )

        # Todo: think about livetime in cycles not in years
        (virtual_number_of_full_charge_cycles, lifetime_in_cycles) = Battery.get_battery_aging_information(
            config=config
        )
        if lifetime_in_cycles > 0:
            capex_per_simulated_period = (capex_cost_data_class.capex_investment_cost_in_euro / lifetime_in_cycles) * (virtual_number_of_full_charge_cycles)
            device_co2_footprint_per_simulated_period = (capex_cost_data_class.device_co2_footprint_in_kg / lifetime_in_cycles) * (
                virtual_number_of_full_charge_cycles
            )

        else:
            log.warning("Capex calculation not valid. Check lifetime_in_cycles in Configuration of Battery.")
            return capex_cost_data_class

        # overwrite capex and emission based on battery cycles
        capex_cost_data_class.capex_investment_cost_for_simulated_period_in_euro = capex_per_simulated_period
        capex_cost_data_class.device_co2_footprint_for_simulated_period_in_kg = device_co2_footprint_per_simulated_period

        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(config=config, capex_cost_data_class=capex_cost_data_class)
        return capex_cost_data_class

    def get_cost_facts(self) -> ComponentCostFacts:
        """Cost facts for the lifecycle cost engine (roadmap/cost_spec.md §3.3, §9.1).

        Declares the battery as one priced subject: a `BATTERY` entry of the cost database, sized
        by its **capacity in kWh** — the unit batteries are quoted in (EUR/kWh), and the
        unit the database row must therefore declare as its `per_unit`. The config field is
        already in kilowatt-hours, so no conversion happens here; a factor slipped in at this line
        would silently move the whole battery investment by three orders of magnitude.

        Everything monetary comes from the database unless the config carries an explicit figure
        (from the building sizer or a RenoVisor request), in which case it is passed through as a
        per-field override together with the `override_source` the provenance ledger requires.
        The legacy `get_cost_capex` above is deliberately left as it is; the parity report
        compares the two.

        Returns:
            The facts for this battery; never None, since the class declares `PRICED`.
        """
        config = self.battery_config
        return ComponentCostFacts(
            asset_class=ComponentType.BATTERY,
            size=concrete(config.custom_battery_capacity_generic_in_kilowatt_hour),
            size_unit=Units.KWH,
            kpi_tag=KpiTagEnumClass.BATTERY,
            investment_cost_override_in_euro=(
                UncertainValue.exact(config.investment_costs_in_euro)
                if config.investment_costs_in_euro is not None
                else None
            ),
            lifetime_override_in_years=config.lifetime_in_years,
            embodied_co2_override_in_kg=config.device_co2_footprint_in_kg,
            # See generic_pv_system: any of the three overrides needs the provenance, not just
            # the investment.
            override_source=(
                "component config (e.g. building_sizer / RenoVisor request)"
                if (
                    config.investment_costs_in_euro is not None
                    or config.lifetime_in_years is not None
                    or config.device_co2_footprint_in_kg is not None
                )
                else None
            ),
        )

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> OpexCostDataClass:
        """Calculate the OPEX: the maintenance, and the electricity the battery lost, priced and with its CO2.

        The lost electricity is what was charged minus what was discharged minus the increase of the stored energy over
        the run (:meth:`KpiHelperClass.compute_storage_losses_in_kilowatt_hour`), so energy the battery still holds at
        the end, or held already at the start, is not booked as a loss. The three energies enter unrounded, the same
        sums the KPI "Battery losses" uses, and only the loss is rounded, to 0.01 kWh, the resolution of the OPEX
        table. Rounding each term first could put the loss up to 0.15 kWh off the KPI, and below zero for a battery
        that barely cycles.

        The charged and the discharged energy are also stored on the config, rounded to 0.1 kWh, as
        ``charge_in_kwh`` and ``discharge_in_kwh``; :meth:`get_battery_aging_information` counts the charge cycles from
        them.

        Raises:
            ValueError: If ``all_outputs`` holds no stored-energy output of this battery, which would book the energy
                it holds at the end as lost.
        """
        stored_energy_increase_in_kwh: Optional[float] = None
        charged_energy_in_kwh = 0.0
        discharged_energy_in_kwh = 0.0
        for index, output in enumerate(all_outputs):
            if output.postprocessing_flag is not None and output.component_name == self.component_name:
                if InandOutputType.STORED_ENERGY_INCREASE in output.postprocessing_flag:
                    stored_energy_increase_in_kwh = float(postprocessing_results.iloc[:, index].sum()) / 1e3
                if InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag:
                    charged_energy_in_kwh = float(
                        postprocessing_results.iloc[:, index].clip(lower=0).sum()
                        * self.my_simulation_parameters.seconds_per_timestep
                        / 3.6e6
                    )
                    discharged_energy_in_kwh = float(
                        postprocessing_results.iloc[:, index].clip(upper=0).sum()
                        * self.my_simulation_parameters.seconds_per_timestep
                        / 3.6e6
                    ) * (-1)
                    self.battery_config.charge_in_kwh = round(charged_energy_in_kwh, 1)
                    self.battery_config.discharge_in_kwh = round(discharged_energy_in_kwh, 1)
        if stored_energy_increase_in_kwh is None:
            raise ValueError(
                f"The outputs passed to the OPEX of battery {self.component_name} hold no output flagged "
                f"{InandOutputType.STORED_ENERGY_INCREASE.name}, so its losses cannot subtract the change of its "
                "stored energy."
            )
        battery_losses_in_kwh = round(
            KpiHelperClass.compute_storage_losses_in_kilowatt_hour(
                charged_energy_in_kilowatt_hour=charged_energy_in_kwh,
                discharged_energy_in_kilowatt_hour=discharged_energy_in_kwh,
                stored_energy_increase_in_kilowatt_hour=stored_energy_increase_in_kwh,
            ),
            2,
        )

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
        )
        co2_per_unit = emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.electricity_costs_in_euro_per_kwh
        co2_per_simulated_period_in_kg = battery_losses_in_kwh * co2_per_unit
        opex_energy_cost_per_simulated_period_in_euro = battery_losses_in_kwh * euro_per_unit

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=battery_losses_in_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.BATTERY,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []


@dataclass(frozen=True)
class BatteryState:
    """The battery's state from one step to the next: its state of charge and its recharge hysteresis.

    ``i_simulate`` starts from the saved state and writes the state the step ends with; ``i_save_state`` makes the
    converged pass's state the next step's start.
    """

    #: State of charge of the battery, dimensionless between 0 and 1. No default: the battery states its start value
    #: once, as :attr:`Battery.INITIAL_STATE_OF_CHARGE`.
    state_of_charge: float
    #: Whether bslib's recharge hysteresis blocks charging: set only when a step ended above a state of charge of 1,
    #: cleared below the threshold (see :meth:`BslibChargingParameters.recharging_is_blocked`).
    recharging_is_blocked: bool = False

    def clone(self) -> "BatteryState":
        """Return a copy of the battery state."""
        return dataclasses.replace(self)
