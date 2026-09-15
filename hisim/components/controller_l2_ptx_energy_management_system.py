""" L2 Controller for PtX Buffer Battery operation. """

# clean
from enum import Enum, unique
from typing import Any, ClassVar, Dict, List, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.config import ConfigBase, ComponentID, DisplayConfig
from hisim.component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues
from hisim.components.generic_electrolyzer_h2 import read_electrolyzer_variant

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
class PtxOperationMode(str, Enum):
    """How the PtX system is driven by the L2 controller.

    Every member carries the operating-mode name as its value, so a serialized
    configuration keeps spelling the mode out exactly as the string-typed field
    did before -- the wire format is unchanged.

    NOMINAL_LOAD: run at the constant nominal load.
    MINIMUM_LOAD: follow the load within the part-load range.
    STANDBY_LOAD: follow the load, but never fall below the standby load, so
        the system is not switched off.
    STANDBY_AND_OFF_LOAD: like STANDBY_LOAD, but switch off once the standby
        load has been held for the standby operation time.
    """

    NOMINAL_LOAD = "NominalLoad"
    MINIMUM_LOAD = "MinimumLoad"
    STANDBY_LOAD = "StandbyLoad"
    STANDBY_AND_OFF_LOAD = "StandbyandOffLoad"


@dataclass_json
@dataclass
class PTXControllerConfig(ConfigBase):
    """Configutation of the PtX  Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return PTXController.get_full_classname()

    component_id: ComponentID
    nom_load: float
    min_load: float
    max_load: float
    standby_load: float
    operation_mode: PtxOperationMode

    def __post_init__(self) -> None:
        """Normalises the operation mode into a :class:`PtxOperationMode` member.

        The mode is wire format: a configuration read from JSON, from HDF5 or written by
        hand arrives carrying the plain string the field has always been serialized as,
        while a caller in Python passes the member. Both are accepted here and both leave
        as the member, so only one kind of value ever reaches the control law. A value
        that names no mode is refused where it was written, instead of travelling into a
        controller that has no branch for it.

        Raises:
            ValueError: For an ``operation_mode`` that is neither a member of
                :class:`PtxOperationMode` nor one of the members' wire values.
        """
        try:
            self.operation_mode = PtxOperationMode(self.operation_mode)
        except ValueError:
            raise ValueError(
                f"Unknown PtX controller operation mode {self.operation_mode!r}. "
                f"Write one of {[mode.value for mode in PtxOperationMode]}."
            ) from None

    #: the manufacturer-table fields this controller is built from, checked before any is read.
    TABLE_FIELDS: ClassVar[tuple[str, ...]] = ("nom_load", "min_load", "max_load", "standby_load")

    @staticmethod
    def read_config(electrolyzer_name: str) -> Dict[str, Any]:
        """Returns the manufacturer table's row for that device, refusing a name it does not carry.

        The lookup is the electrolyzer module's own, so this controller, the L1 controller and the
        machine itself accept and refuse exactly the same device names. It used to answer an
        unknown name with an empty dictionary, out of which the zero fallbacks below built a PtX
        system whose four loads were all zero -- a mistyped name ran, and ran nothing.

        Args:
            electrolyzer_name: the device name as written by the setup or the configuration file.

        Returns:
            The row of "Electrolyzer variants" belonging to that device, carrying every field in
            :attr:`TABLE_FIELDS`.

        Raises:
            ValueError: if no device of that name is in the table, or its row lacks one of the
                fields this controller reads.
        """
        return read_electrolyzer_variant(electrolyzer_name, required_fields=PTXControllerConfig.TABLE_FIELDS)

    @classmethod
    def control_electrolyzer(
        cls,
        electrolyzer_name: str,
        operation_mode: PtxOperationMode,
        component_id: Optional[ComponentID] = None,
    ) -> Any:
        """Sets the according parameters for the chosen electrolyzer.

        The operation mode selects how the electrolyser is operated; see
        :class:`PtxOperationMode` for what each member means. The four loads are read straight
        out of the row, which :meth:`read_config` has already checked carries all of them: a
        table entry missing one is an error naming the field and the device, not a load of zero.

        Args:
            electrolyzer_name: the device name to look up in the manufacturer table.
            operation_mode: how the PtX system is to be driven.
            component_id: the identity to give the controller, defaulted when not supplied.

        Returns:
            The PtX controller configuration of that device.
        """
        if component_id is None:
            component_id = ComponentID(name="L2PtXController")
        config_json = cls.read_config(electrolyzer_name)

        config = PTXControllerConfig(
            component_id=component_id,  # config_json.get("name", "")
            nom_load=config_json["nom_load"],
            min_load=config_json["min_load"],
            max_load=config_json["max_load"],
            standby_load=config_json["standby_load"],
            operation_mode=operation_mode,
        )
        return config


class PTXController(Component):
    """PtX  Controller."""

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    RESLoad = "RESLoad"
    StateOfCharge = "StateOfCharge"

    # Outputs
    PowerToBattery = "PowerToBattery"
    PowerToSystem = "PowerToSystem"
    EnergyToBattery = "EnergyToBattery"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: PTXControllerConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Initialize the class."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.ptx_controller_config = config

        self.nom_load = config.nom_load
        self.min_load = config.min_load
        self.max_load = config.max_load
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

        self.load_input: ComponentInput = self.add_input(
            self.component_name,
            PTXController.RESLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,  # for EMS
            True,
        )

        self.soc: ComponentInput = self.add_input(
            self.component_name,
            PTXController.StateOfCharge,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            False,
        )

        # =================================================================================================================================
        # Output channels

        self.load_to_battery: ComponentOutput = self.add_output(
            self.component_name,
            PTXController.PowerToBattery,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="Charges or discharges the battery",
        )
        self.energy_to_battery: ComponentOutput = self.add_output(
            self.component_name,
            PTXController.EnergyToBattery,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KWH,
            output_description="Charges or discharges the battery",
        )

        self.load_to_system: ComponentOutput = self.add_output(
            self.component_name,
            PTXController.PowerToSystem,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="distributes RES load to the system",
        )

        # =================================================================================================================================
        # Initialize variables
        self.system_state = "OFF"
        self.threshold_exceeded = False
        self.standby_time_count = 0.0
        self.total_energy_to_battery = 0.0

        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded
        self.standby_time_count_previous = self.standby_time_count
        self.total_energy_to_battery_previous = self.total_energy_to_battery

    def system_operation(self, operation_mode: PtxOperationMode, res_load: float) -> tuple[float, float]:
        """System operation."""
        if operation_mode == PtxOperationMode.NOMINAL_LOAD:
            load_to_system = self.nom_load
            power_to_battery = res_load - self.nom_load  # postive battery charge, negative battery discharges

        elif operation_mode == PtxOperationMode.MINIMUM_LOAD:
            if self.min_load <= res_load <= self.max_load:
                load_to_system = res_load
                power_to_battery = 0.0
            elif res_load < self.min_load:
                load_to_system = self.min_load
                power_to_battery = res_load - self.min_load
            else:
                load_to_system = self.max_load
                power_to_battery = res_load - self.max_load

        elif operation_mode == PtxOperationMode.STANDBY_LOAD:
            if self.min_load <= res_load <= self.max_load:
                load_to_system = res_load
                power_to_battery = 0.0
            elif self.max_load < res_load:
                load_to_system = self.max_load
                power_to_battery = res_load - self.max_load
            else:
                # standby_load <= res_load < min_load and res_load < standby_load:
                load_to_system = self.standby_load
                power_to_battery = res_load - self.standby_load  # if

        elif operation_mode == PtxOperationMode.STANDBY_AND_OFF_LOAD:
            if self.min_load <= res_load <= self.max_load:
                self.standby_time_count = 0.0
                load_to_system = res_load
                power_to_battery = 0.0
            elif self.max_load < res_load:
                self.standby_time_count = 0.0
                load_to_system = self.max_load
                power_to_battery = res_load - self.max_load
            elif self.standby_load <= res_load < self.min_load:
                self.standby_time_count = 0.0
                load_to_system = self.standby_load
                power_to_battery = res_load - self.standby_load
            else:  # res_load <= self.standby_load
                standby_operation_time = 3600.0  # 7200.0
                if self.standby_time_count >= standby_operation_time:
                    load_to_system = 0.0
                    power_to_battery = 0.0
                else:
                    load_to_system = self.standby_load
                    power_to_battery = res_load - self.standby_load
                    self.standby_time_count += self.my_simulation_parameters.seconds_per_timestep

        else:
            # Unreachable for every member above; it only guards a member added
            # later that nobody wrote a branch for.
            raise ValueError(f"PtX controller: unknown operation mode {operation_mode!r}")

        return load_to_system, power_to_battery

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded
        self.standby_time_count_previous = self.standby_time_count
        self.total_energy_to_battery_previous = self.total_energy_to_battery

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.system_state = self.system_state_previous
        self.threshold_exceeded = self.threshold_exceeded_previous
        self.standby_time_count = self.standby_time_count_previous
        self.total_energy_to_battery = self.total_energy_to_battery_previous

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the component."""
        if force_convergence:
            return

        res_load = stsv.get_input_value(self.load_input)

        """ Only for household testing
        if self.system_state == "OFF" and soc < 0.2:
            power_to_battery = res_load
            load_to_system = 0.0
        elif self.system_state == "OFF" and soc >= 0.2:
            (load_to_system, power_to_battery) = self.system_operation(
                self.operation_mode, res_load
            )
            self.system_state = "ON"
        else:
            (load_to_system, power_to_battery) = self.system_operation(
                self.operation_mode, res_load
            )
            self.system_state = "ON"

        """
        (load_to_system, power_to_battery) = self.system_operation(self.operation_mode, res_load)

        """
        if self.system_state == "OFF":
            if 0.30 < stsv.get_input_value(self.soc):
                print(stsv.get_input_value(self.soc))
                self.system_state = "ON"
                print(self.system_state)

            power_to_battery = res_load
            load_to_system = 0.0
        """
        self.total_energy_to_battery += power_to_battery * (self.my_simulation_parameters.seconds_per_timestep / 3600)

        stsv.set_output_value(self.load_to_battery, power_to_battery)
        stsv.set_output_value(self.load_to_system, load_to_system)
        stsv.set_output_value(self.energy_to_battery, self.total_energy_to_battery)

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        return self.ptx_controller_config.get_string_dict()
