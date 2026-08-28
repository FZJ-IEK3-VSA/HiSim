"""L2 Controller for PtX Buffer Battery operation."""

# clean
from pathlib import Path
from typing import Optional, Any
import json
from dataclasses import dataclass, field
from dataclasses_json import config as dc_json_config
from dataclasses_json import dataclass_json
from hisim.config import ConfigBase, ComponentID, DisplayConfig
from hisim.component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues

from hisim import loadtypes as lt
from hisim import utils
from hisim.simulationparameters import SimulationParameters

__authors__ = "Franz Oldopp"
__copyright__ = "Copyright 2023, IEK-3"
__credits__ = ["Franz Oldopp"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Franz Oldopp"
__email__ = "f.oldopp@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class RsocBatteryControllerConfig(ConfigBase):
    """Configutation of the rSOC and Battery Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return RsocBatteryController.get_full_classname()

    component_id: ComponentID
    # Python attributes use snake_case ("_in_kw") to satisfy the prospector
    # naming gate (pycodestyle N815 / pylint invalid-name). The dataclasses_json
    # ``field_name`` aliases keep the legacy mixedCase ("_in_kW") serialization
    # keys so existing JSON/HDF5 configs and reports still load unchanged.
    nom_load_soec_in_kw: float = field(metadata=dc_json_config(field_name="nom_load_soec_in_kW"))
    min_load_soec_in_kw: float = field(metadata=dc_json_config(field_name="min_load_soec_in_kW"))
    max_load_soec_in_kw: float = field(metadata=dc_json_config(field_name="max_load_soec_in_kW"))
    standby_load_in_kw: float = field(metadata=dc_json_config(field_name="standby_load_in_kW"))
    nom_power_sofc_in_kw: float = field(metadata=dc_json_config(field_name="nom_power_sofc_in_kW"))
    min_power_sofc_in_kw: float = field(metadata=dc_json_config(field_name="min_power_sofc_in_kW"))
    max_power_sofc_in_kw: float = field(metadata=dc_json_config(field_name="max_power_sofc_in_kW"))
    # standby_load_sofc: float

    operation_mode: str

    @staticmethod
    def read_config(
        rsoc_name: str, config_path: Path | None = None
    ) -> dict[str, Any]:
        """Open the manufacturer config JSON and return the variant for ``rsoc_name``.

        When ``config_path`` is ``None`` the config shipped with HiSim
        (``utils.HISIMPATH["inputs"] / "rSOC_manufacturer_config.json"``) is
        used, preserving the original behaviour. Passing an explicit
        ``config_path`` keeps the lookup independent of the module-global
        ``HISIMPATH`` and the default inputs directory, which makes it usable
        from tests.
        """

        config_file = (
            config_path
            if config_path is not None
            else Path(utils.HISIMPATH["inputs"]) / "rSOC_manufacturer_config.json"
        )
        with open(config_file, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            variant: dict[str, Any] = data.get("rSOC variants", {}).get(rsoc_name, {})
            return variant

    @classmethod
    def config_rsoc(
        cls,
        rsoc_name: str,
        operation_mode: str,
        component_id: Optional[ComponentID] = None,
        config_data: dict[str, Any] | None = None,
    ) -> Any:
        """Configure rsoc.

        When ``config_data`` is ``None`` the manufacturer config is read from
        disk via :meth:`read_config`, preserving the original behaviour.
        Passing an in-memory ``config_data`` dict keeps the construction
        independent of the filesystem and the module-global ``HISIMPATH``, so
        the config can be built in tests without the inputs directory or JSON
        file being present.
        """
        if component_id is None:
            component_id = ComponentID(name="RsocAndBatteryController")
        if config_data is None:
            config_data = cls.read_config(rsoc_name)

        config = RsocBatteryControllerConfig(
            component_id=component_id,  # config_data.get("name", "")
            nom_load_soec_in_kw=config_data.get("nom_load_soec", 0.0),
            min_load_soec_in_kw=config_data.get("min_load_soec", 0.0),
            max_load_soec_in_kw=config_data.get("max_load_soec", 0.0),
            standby_load_in_kw=config_data.get("standby_load", 0.0),
            nom_power_sofc_in_kw=config_data.get("nom_power_sofc", 0.0),
            min_power_sofc_in_kw=config_data.get("min_power_sofc", 0.0),
            max_power_sofc_in_kw=config_data.get("max_power_sofc", 0.0),
            # standby_load_sofc=config_data.get("standby_load_sofc", 0.0),
            operation_mode=operation_mode,
        )
        return config


class RsocBatteryController(Component):
    """rSOC and Battery  Controller."""

    # Inputs
    RESLoad = "RESLoad"
    Demand = "Demand"
    StateOfCharge = "StateOfCharge"

    # Outputs
    PowerToBattery = "PowerToBattery"
    PowerToSystem = "PowerToSystem"
    Power = "Power"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: RsocBatteryControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Initialize the class."""
        self.ptxcontrollerconfig = config

        self.nom_load_soec_in_kw = config.nom_load_soec_in_kw
        self.min_load_soec_in_kw = config.min_load_soec_in_kw
        self.max_load_soec_in_kw = config.max_load_soec_in_kw
        self.standby_load_soec_in_kw = config.standby_load_in_kw
        self.nom_power_sofc_in_kw = config.nom_power_sofc_in_kw
        self.min_power_sofc_in_kw = config.min_power_sofc_in_kw
        self.max_power_sofc_in_kw = config.max_power_sofc_in_kw
        self.standby_load_sofc_in_kw = config.standby_load_in_kw
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
            RsocBatteryController.RESLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        self.demand_input: ComponentInput = self.add_input(
            self.component_name,
            RsocBatteryController.Demand,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        self.soc: ComponentInput = self.add_input(
            self.component_name,
            RsocBatteryController.StateOfCharge,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            False,
        )

        # =================================================================================================================================
        # Output channels

        self.load_to_battery: ComponentOutput = self.add_output(
            self.component_name,
            RsocBatteryController.PowerToBattery,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Charges or discharges the battery",
        )

        self.load_to_system: ComponentOutput = self.add_output(
            self.component_name,
            RsocBatteryController.PowerToSystem,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="distributes RES load to the system",
        )
        self.power: ComponentOutput = self.add_output(
            self.component_name,
            RsocBatteryController.Power,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="power delta between drovided and demand.",
        )

        # =================================================================================================================================
        # Initialize variables
        self.system_state = "OFF"
        self.threshold_exceeded = False

        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded

    def system_operation(
        self,
        operation_mode,
        power_delta_in_kw,
        nom_power_in_kw,
        min_power_in_kw,
        max_power_in_kw,
    ):
        """System operation."""

        if operation_mode == "NominalLoad":
            load_to_system_in_kw = nom_power_in_kw
            power_to_battery_in_kw = power_delta_in_kw - nom_power_in_kw  # postive battery charge, negative battery discharges

            # pdb.set_trace()
        elif operation_mode == "MinimumLoad":
            # pdb.set_trace()
            if min_power_in_kw <= power_delta_in_kw <= max_power_in_kw:
                load_to_system_in_kw = power_delta_in_kw
                power_to_battery_in_kw = 0.0
            elif power_delta_in_kw < min_power_in_kw:
                load_to_system_in_kw = min_power_in_kw
                power_to_battery_in_kw = power_delta_in_kw - min_power_in_kw
            else:
                load_to_system_in_kw = max_power_in_kw
                power_to_battery_in_kw = power_delta_in_kw - max_power_in_kw

        elif operation_mode == "StandbyLoad":
            if min_power_in_kw <= power_delta_in_kw <= max_power_in_kw:
                load_to_system_in_kw = power_delta_in_kw
                power_to_battery_in_kw = 0.0
            elif max_power_in_kw < power_delta_in_kw:
                load_to_system_in_kw = max_power_in_kw
                power_to_battery_in_kw = power_delta_in_kw - max_power_in_kw
            else:
                # standby_load_in_kw <= power_delta_in_kw < min_load and power_delta_in_kw < standby_load_in_kw:
                load_to_system_in_kw = min_power_in_kw
                power_to_battery_in_kw = power_delta_in_kw - min_power_in_kw  # if
        else:
            if power_delta_in_kw <= max_power_in_kw:
                load_to_system_in_kw = power_delta_in_kw
                power_to_battery_in_kw = 0.0
            else:  # max_power_in_kw < power_delta_in_kw:
                load_to_system_in_kw = max_power_in_kw
                power_to_battery_in_kw = power_delta_in_kw - max_power_in_kw

        return load_to_system_in_kw, power_to_battery_in_kw

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.system_state = self.system_state_previous
        self.threshold_exceeded = self.threshold_exceeded_previous

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the component."""
        if force_convergence:
            return

        # first a power deman evaluation
        res_load_in_kw = stsv.get_input_value(self.load_input) / 1000  # to use KILOWATT
        demand_in_kw = stsv.get_input_value(self.demand_input) / 1000  # to use KILOWATT
        power_delta_in_kw = demand_in_kw - res_load_in_kw

        if power_delta_in_kw < 0.0:
            # pdb.set_trace()
            # SOEC
            (load_to_system_in_kw, power_to_battery_in_kw) = self.system_operation(
                self.operation_mode,
                abs(power_delta_in_kw),
                self.nom_load_soec_in_kw,
                self.min_load_soec_in_kw,
                self.max_load_soec_in_kw,
            )
            load_to_system_in_kw = -load_to_system_in_kw
        elif power_delta_in_kw > 0.0:
            # pdb.set_trace()
            # SOFC
            (load_to_system_in_kw, power_to_battery_in_kw) = self.system_operation(
                self.operation_mode,
                abs(power_delta_in_kw),
                self.nom_power_sofc_in_kw,
                self.min_power_sofc_in_kw,
                self.max_power_sofc_in_kw,
            )
        else:
            # pdb.set_trace()
            # power_delta_in_kw = 0
            load_to_system_in_kw = 0.0
            power_to_battery_in_kw = 0.0

        """
        (load_to_system, power_to_battery) = self.system_operation(
                self.operation_mode, res_load
            )

        if self.system_state == "OFF":
            if 0.30 < stsv.get_input_value(self.soc):
                print(stsv.get_input_value(self.soc))
                self.system_state = "ON"
                print(self.system_state)

            power_to_battery = res_load
            load_to_system = 0.0
            # pdb.set_trace()
        """

        stsv.set_output_value(self.load_to_battery, (power_to_battery_in_kw * 1000))  # Output: WATT
        stsv.set_output_value(self.load_to_system, load_to_system_in_kw)
        stsv.set_output_value(self.power, power_delta_in_kw)

    def write_to_report(self) -> list[str]:
        """Writes a report."""
        return self.ptxcontrollerconfig.get_string_dict()
