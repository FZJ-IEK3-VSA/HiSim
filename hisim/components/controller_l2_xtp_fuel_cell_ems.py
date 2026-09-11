""" L2 Controller for PtX Buffer Battery operation. """

from __future__ import annotations

# clean
from enum import Enum, unique
import math
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.config import ConfigBase, ComponentID, DisplayConfig
from hisim.component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues

from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.economics.facts import CostRelevance

__authors__ = "Franz Oldopp"
__copyright__ = "Copyright 2023, IEK-3"
__credits__ = ["Franz Oldopp"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Franz Oldopp"
__email__ = "f.oldopp@fz-juelich.de"
__status__ = "development"


@unique
class XtpOperationMode(str, Enum):
    """How the XtP fuel cell is driven by the L2 controller.

    Every member carries the operating-mode name as its value, so a serialized
    configuration keeps spelling the mode out exactly as the string-typed field
    did before -- the wire format is unchanged.

    STANDBY_LOAD: serve the demand, but never fall below the standby load, so
        the system is not switched off.
    STANDBY_AND_OFF_LOAD: like STANDBY_LOAD, but switch off once the standby
        load has been held for the standby operation time.
    """

    STANDBY_LOAD = "StandbyLoad"
    STANDBY_AND_OFF_LOAD = "StandbyandOffLoad"


@dataclass_json
@dataclass
class XTPControllerConfig(ConfigBase):
    """Configuration of the PtX  Controller.

    This class has **no default builder**. Its only factory read
    `hisim/inputs/fuel_cell_manufacturer_config.json`, a file that is not in this repository
    and never was, so the factory raised `FileNotFoundError` on every call; component sweep
    decision D-25 removed it and archived its text in
    `obsolete/components/fuel_cell_manufacturer_table.py`. Until the conversion batch gives
    this class a preset, a caller builds it by naming every field.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return str(XTPController.get_full_classname())

    component_id: ComponentID
    nom_output: float
    min_output: float
    max_output: float
    standby_load: float
    operation_mode: XtpOperationMode

    def __post_init__(self) -> None:
        """Normalises the operation mode into a :class:`XtpOperationMode` member.

        The mode is wire format: a configuration read from JSON, from HDF5 or written by
        hand arrives carrying the plain string the field has always been serialized as,
        while a caller in Python passes the member. Both are accepted here and both leave
        as the member, so only one kind of value ever reaches the control law. A value
        that names no mode is refused where it was written, instead of travelling into a
        controller that has no branch for it.

        Raises:
            ValueError: For an ``operation_mode`` that is neither a member of
                :class:`XtpOperationMode` nor one of the members' wire values.
        """
        try:
            self.operation_mode = XtpOperationMode(self.operation_mode)
        except ValueError:
            raise ValueError(
                f"Unknown XtP controller operation mode {self.operation_mode!r}. "
                f"Write one of {[mode.value for mode in XtpOperationMode]}."
            ) from None


class XTPController(Component):
    """XtP  Controller."""

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    DemandLoad = "DemandLoad"
    StateOfCharge = "StateOfCharge"

    # Outputs
    PowerFromThird = "PowerFromThird"
    DemandToSystem = "DemandToSystem"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: XTPControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Initialize the class."""
        self.xtpcontrollerconfig = config

        self.nom_output = config.nom_output
        self.min_output = config.min_output
        self.max_output = config.max_output
        self.standby_load = config.standby_load
        self.operation_mode = config.operation_mode

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # =================================================================================================================================
        # Input channels

        self.demand_input: ComponentInput = self.add_input(
            self.component_name,
            XTPController.DemandLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        self.soc: ComponentInput = self.add_input(
            self.component_name,
            XTPController.StateOfCharge,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            False,
        )

        # =================================================================================================================================
        # Output channels

        self.load_from_battery: ComponentOutput = self.add_output(
            self.component_name,
            XTPController.PowerFromThird,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Discharges the battery in case of no power production",
        )

        self.demand_to_system: ComponentOutput = self.add_output(
            self.component_name,
            XTPController.DemandToSystem,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="distributes demand to the system",
        )

        # =================================================================================================================================
        # Initialize variables
        self.system_state: str = "OFF"
        self.threshold_exceeded: bool = False
        self.standby_time_count: float = 0.0

        self.system_state_previous: str = self.system_state
        self.threshold_exceeded_previous: bool = self.threshold_exceeded
        self.standby_time_count_previous: float = self.standby_time_count

    def system_operation(self, operation_mode: XtpOperationMode, demand_load: float) -> tuple[float, float]:
        """System operation."""
        if operation_mode == XtpOperationMode.STANDBY_LOAD:
            if self.min_output <= demand_load <= self.max_output:
                demand_to_system = demand_load
                power_from_battery = 0.0
            elif self.max_output < demand_load:
                demand_to_system = demand_load
                power_from_battery = 0.0
            elif self.standby_load <= demand_load < self.min_output:
                # standby_load <= demand_load < min_output:
                demand_to_system = demand_load
                power_from_battery = 0.0
            else:
                demand_to_system = self.standby_load
                power_from_battery = -self.standby_load

        elif operation_mode == XtpOperationMode.STANDBY_AND_OFF_LOAD:
            if self.min_output <= demand_load <= self.max_output:
                self.standby_time_count = 0.0
                demand_to_system = demand_load
                power_from_battery = 0.0
            elif self.max_output < demand_load:
                self.standby_time_count = 0.0
                demand_to_system = demand_load
                power_from_battery = 0.0
            elif self.standby_load <= demand_load < self.min_output:
                self.standby_time_count = 0.0
                demand_to_system = demand_load
                power_from_battery = 0.0
            else:  # demand_load <= self.standby_load
                standby_operation_time = 7200.0  # 7200.0
                if self.standby_time_count >= standby_operation_time:
                    demand_to_system = 0.0
                    power_from_battery = 0.0
                else:
                    demand_to_system = self.standby_load
                    power_from_battery = -self.standby_load
                    self.standby_time_count += self.my_simulation_parameters.seconds_per_timestep

        else:
            # Unreachable for every member above; it only guards a member added
            # later that nobody wrote a branch for.
            raise ValueError(f"XtP controller: unknown operation mode {operation_mode!r}")

        return demand_to_system, power_from_battery

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded
        self.standby_time_count_previous = self.standby_time_count

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.system_state = self.system_state_previous
        self.threshold_exceeded = self.threshold_exceeded_previous
        self.standby_time_count = self.standby_time_count_previous

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the component."""
        if force_convergence:
            return

        raw = stsv.get_input_value(self.demand_input)
        assert math.isfinite(raw), (
            f"Non-finite demand input at timestep {timestep}: {raw}"
        )
        demand_load = abs(raw / 1000)  # WATT input to KILOWATT

        """ Only for household testing
        if self.system_state == "OFF" and soc < 0.2:
            power_from_battery = demand_load
            demand_to_system = 0.0
        elif self.system_state == "OFF" and soc >= 0.2:
            (demand_to_system, power_from_battery) = self.system_operation(
                self.operation_mode, demand_load
            )
            self.system_state = "ON"
        else:
            (demand_to_system, power_from_battery) = self.system_operation(
                self.operation_mode, demand_load
            )
            self.system_state = "ON"
        """
        (demand_to_system, power_from_battery) = self.system_operation(self.operation_mode, demand_load)

        """
        if self.system_state == "OFF":
            if 0.30 < stsv.get_input_value(self.soc):
                print(stsv.get_input_value(self.soc))
                self.system_state = "ON"
                print(self.system_state)

            power_from_battery = demand_load
            demand_to_system = 0.0
        """

        stsv.set_output_value(self.load_from_battery, power_from_battery * 1000)  # Battery Output in WATT
        stsv.set_output_value(self.demand_to_system, demand_to_system)

    def write_to_report(self) -> list[str]:
        """Writes a report."""
        return self.xtpcontrollerconfig.get_string_dict()
