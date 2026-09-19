""" Controller for the generic_electrolyzer_h2 component. """

from __future__ import annotations

from typing import Any, ClassVar
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.config import ConfigBase, ComponentID, DisplayConfig, constructor, preset
from hisim.component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues
from hisim.components.generic_electrolyzer_h2 import read_electrolyzer_variant

from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class ElectrolyzerControllerConfig(ConfigBase):
    """Load band and start-up times the L1 controller drives one electrolyzer within.

    The controller hands the machine whatever load it is offered, clipped into the band
    ``min_load`` … ``max_load``, holds it at ``standby_load`` when the offer falls short and
    counts the warm or cold start time off before it lets the machine produce again. Every
    figure is a property of the machine rather than of the scenario, so a controller is
    usually built from the same manufacturer-table row the electrolyzer itself is built from::

        ElectrolyzerControllerConfig.for_device("L1ElectrolyzerController", "HTecME450")

    :meth:`preset_standard` states a 100 kW machine instead of reading one.
    """

    MAIN_CLASS = "hisim.components.controller_l1_electrolyzer_h2.ElectrolyzerController"

    component_id: ComponentID
    #: Nominal electrical load of the controlled machine, in kW.
    nom_load: float = 100.0
    #: Lowest load the machine may be run at, in kW; below it the controller goes to standby.
    min_load: float = 10.0
    #: Highest load the controller hands over, in kW; anything above it is curtailed.
    max_load: float = 110.0
    #: Load the machine is held at while it is idle but not switched off, in kW.
    standby_load: float = 5.0
    #: Seconds a warm machine needs before it produces again.
    warm_start_time: float = 70.0
    #: Seconds a cold machine needs before it produces again.
    cold_start_time: float = 1800.0

    @preset(note="a 100 kW machine between 10 and 110 kW")
    @classmethod
    def preset_standard(cls, name: str) -> "ElectrolyzerControllerConfig":
        """The controller of a 100 kW electrolyzer running between 10 and 110 kW.

        The field defaults are that band, a 5 kW standby load and start times of 30 s warm and
        600 s cold. Nothing here names a technology or a manufacturer -- the band belongs to
        the electrolyzer this controller is sized to, and the name would only repeat it -- so
        the preset stays ``standard``.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            ElectrolyzerControllerConfig: The preset configuration.
        """
        return cls(component_id=ComponentID(name=name))

    #: the manufacturer-table fields this controller is built from, checked before any is read.
    TABLE_FIELDS: ClassVar[tuple[str, ...]] = (
        "nom_load",
        "min_load",
        "max_load",
        "standby_load",
        "warm_start_time",
        "cold_start_time",
    )

    @staticmethod
    def read_config(electrolyzer_name: str) -> dict[str, Any]:
        """Returns the manufacturer table's row for that device, refusing a name it does not carry.

        The lookup is the electrolyzer module's own, so the controller and the machine it controls
        accept and refuse exactly the same device names. It used to answer an unknown name with an
        empty dictionary, out of which the zero fallbacks below built a controller whose loads were
        all zero -- a mistyped name ran, and ran an electrolyzer that never left standby.

        Args:
            electrolyzer_name: the device name as written by the setup or the configuration file.

        Returns:
            The row of "Electrolyzer variants" belonging to that device, carrying every field in
            :attr:`TABLE_FIELDS`.

        Raises:
            ValueError: if no device of that name is in the table, or its row lacks one of the
                fields this controller reads.
        """
        return read_electrolyzer_variant(
            electrolyzer_name,
            required_fields=ElectrolyzerControllerConfig.TABLE_FIELDS,
        )

    @constructor(note="the band and start times one device of the manufacturer table states")
    @classmethod
    def for_device(cls, name: str, electrolyzer_name: str) -> "ElectrolyzerControllerConfig":
        """Builds the controller of the machine one row of the manufacturer table describes.

        Both the controller and the electrolyzer it drives are built by naming the same
        device, which is what keeps the band the controller clips to and the band the machine
        accepts from drifting apart::

            ElectrolyzerControllerConfig.for_device("L1ElectrolyzerController", "HTecME450")

        Every field is read straight out of the row, which :meth:`read_config` has already
        checked carries all of them: a table entry missing one is an error naming the field
        and the device, not a load of zero.

        Args:
            name: Instance name of the controller; its ``ComponentID`` is built from it.
            electrolyzer_name: The device name as the manufacturer table spells it, e.g.
                ``"HTecME450"``.

        Returns:
            A fresh configuration of that device's controller; nothing about it is shared with
            any other instance.

        Raises:
            ValueError: If no device of that name is in the table, or its row lacks one of the
                six fields this controller reads.
        """
        row = cls.read_config(electrolyzer_name)
        log.information(f"Electrolyzer config: {row}")
        return cls(
            component_id=ComponentID(name=name),
            nom_load=row["nom_load"],
            min_load=row["min_load"],
            max_load=row["max_load"],
            standby_load=row["standby_load"],
            warm_start_time=row["warm_start_time"],
            cold_start_time=row["cold_start_time"],
        )


class ElectrolyzerController(Component):
    """Electrolyzer Controller class."""

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    ProvidedLoad: str = "ProvidedLoad"

    # Outputs
    DistributedLoad: str = "DistributedLoad"
    ShutdownCount: str = "ShutdownCount"
    StandbyCount: str = "StandbyCount"
    CurrentMode: str = "CurrentMode"
    CurtailedLoad: str = "CurtailedLoad"
    OffCount: str = "OffCount"

    # A controller decides, it does not convert energy: there is no device behind it to
    # buy or to run, and the indicators of the plant it controls belong to the
    # electrolyzer itself. Declaring that is what lets a setup built from it compute
    # costs and KPIs at all; see Component.MODELS_NO_DEVICE.
    MODELS_NO_DEVICE: ClassVar[bool] = True

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectrolyzerControllerConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Initialize the class."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.controllerconfig: ElectrolyzerControllerConfig = config

        self.nom_load: float = config.nom_load
        self.min_load: float = config.min_load
        self.max_load: float = config.max_load
        self.standby_load: float = config.standby_load
        self.warm_start_time: float = config.warm_start_time
        self.cold_start_time: float = config.cold_start_time

        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: ElectrolyzerControllerConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # =================================================================================================================================
        # Input channels

        # Getting the load input
        self.load_input: ComponentInput = self.add_input(
            self.component_name,
            ElectrolyzerController.ProvidedLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            True,
        )

        # =================================================================================================================================
        # Output channels

        self.standby_count_total: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.StandbyCount,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="standby count",
        )

        self.distributed_load: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.DistributedLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="Load to electrolyzer",
        )

        self.current_mode_electrolyzer: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.CurrentMode,
            lt.LoadTypes.ACTIVATION,
            lt.Units.ANY,
            output_description="current mode of electrolyzer",
        )

        self.curtailed_load: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.CurtailedLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="amount of curtailed load due to min and max load tresholds",
        )

        self.total_off_count: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.OffCount,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Total count of switching off",
        )
        # =================================================================================================================================
        # Initialize variables

        self.standby_count: float = 0.0
        self.current_state: str = "OFF"  # standby
        self.curtailed_load_count: float = 0.0
        self.off_count: float = 0.0
        self.activation_runtime: float = 0.0

        self.standby_count_previous: float = self.standby_count
        self.current_state_previous: str = self.current_state
        self.curtailed_load_count_previous: float = self.curtailed_load_count
        self.off_count_previous: float = self.off_count
        self.activation_runtime_previous: float = self.activation_runtime

    def load_check(
        self,
        current_load: float | None,
        min_load: float | None,
        max_load: float | None,
        standby_load: float | None,
    ) -> tuple[float, str, float]:
        """Load check."""
        if current_load is None or min_load is None or max_load is None or standby_load is None:
            raise ValueError(f"None type not accepted. {current_load}, {min_load}, {max_load}, {standby_load}")
        if current_load > max_load:
            current_load_to_system = max_load
            self.curtailed_load_count += current_load - max_load
            state = "ON"

        elif min_load <= current_load <= max_load:
            current_load_to_system = current_load
            self.curtailed_load_count += 0.0
            state = "ON"

        elif standby_load <= current_load < min_load:
            current_load_to_system = standby_load
            self.curtailed_load_count += current_load - standby_load
            state = "STANDBY"

        else:
            current_load_to_system = 0.0
            self.curtailed_load_count += current_load
            state = "OFF"

        return current_load_to_system, state, self.curtailed_load_count

    def state_check(
        self,
        target_state: str,
        cold_start_time_to_min: float,
        warm_start_time_to_min: float,
    ) -> tuple[str, float]:
        """State check."""
        if target_state == "OFF":
            # System switches OFF
            if self.current_state == "ON":
                self.current_state = "SwitchingOFF"
                self.off_count += 1
            else:
                self.current_state = "OFF"

        elif target_state == "STANDBY":
            # System switches STANDY
            if self.current_state in ("OFF", "StartingfromOFF"):
                self.current_state = "OFF"
                self.off_count += 1
            if self.current_state == "ON":
                self.current_state = "SwitchingSTANDBY"
                self.standby_count += 1
            else:
                self.current_state = "STANDBY"

        else:
            # Test start
            if self.current_state in ["StartingfromOFF", "StartingfromSTANDBY"]:
                # pdb.set_trace()
                if self.activation_runtime <= self.my_simulation_parameters.seconds_per_timestep:
                    self.current_state = "Startingtomin"
                    # pdb.set_trace()
                else:
                    self.activation_runtime -= self.my_simulation_parameters.seconds_per_timestep
                    # pdb.set_trace()
                    # self.current_state = self.current_state
                    # starting to min auch unten aufnehmen um so min_load zu verteilen. "Starting to min" kann verwendet werden,
                    # da wenn wir wir durch die else: bedingungen da wieder raus kommen (theoretisch ;))

            # Test end
            elif self.current_state == "OFF":
                self.current_state = "StartingfromOFF"
                self.activation_runtime = cold_start_time_to_min
            elif self.current_state == "STANDBY":
                self.current_state = "StartingfromSTANDBY"
                self.activation_runtime = warm_start_time_to_min
            else:
                if self.activation_runtime > self.my_simulation_parameters.seconds_per_timestep:
                    self.activation_runtime -= self.my_simulation_parameters.seconds_per_timestep
                    # self.current_state = self.current_state
                # elif self.activation_runtime <= self.my_simulation_parameters.seconds_per_timestep:
                #    self.activation_runtime -= self.my_simulation_parameters.seconds_per_timestep
                #    self.current_state = self.current_state
                # elif self.activation_runtime <= 0.0:
                else:
                    self.activation_runtime = 0.0
                    self.current_state = "ON"

        return self.current_state, self.activation_runtime

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.standby_count_previous = self.standby_count
        self.current_state_previous = self.current_state
        self.curtailed_load_count_previous = self.curtailed_load_count
        self.off_count_previous = self.off_count
        self.activation_runtime_previous = self.activation_runtime

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.standby_count = self.standby_count_previous
        self.current_state = self.current_state_previous
        self.curtailed_load_count = self.curtailed_load_count_previous
        self.off_count = self.off_count_previous
        self.activation_runtime = self.activation_runtime_previous

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the component."""
        if force_convergence:
            return
        """
        self.nom_load = config.nom_load
        self.min_load = config.min_load
        self.max_load = config.max_load
        self.warm_start_time = config.warm_start_time
        self.cold_start_time = config.cold_start_time
        """
        if self.nom_load == 0.0:
            self.nom_load = 1.0
        warm_start_time_to_min = self.warm_start_time * (self.min_load / self.nom_load)
        cold_start_time_to_min = self.cold_start_time * (self.min_load / self.nom_load)

        (current_load_to_system, state, self.curtailed_load_count) = self.load_check(
            (stsv.get_input_value(self.load_input)),
            self.min_load,
            self.max_load,
            self.standby_load,
        )  # change standby time
        # pdb.set_trace()
        (self.current_state, self.activation_runtime) = self.state_check(
            state, cold_start_time_to_min, warm_start_time_to_min
        )

        if self.current_state in ["OFF", "StartingfromOFF", "SwitchingOFF"]:
            stsv.set_output_value(self.distributed_load, 0.0)
            stsv.set_output_value(self.current_mode_electrolyzer, -1)
        elif self.current_state in [
            "STANDBY",
            "StartingfromSTANDBY",
            "SwitchingSTANDBY",
        ]:
            stsv.set_output_value(self.distributed_load, (self.nom_load * 0.05))
            stsv.set_output_value(self.current_mode_electrolyzer, 0)
        elif self.current_state == "Startingtomin":
            # pdb.set_trace()
            stsv.set_output_value(self.distributed_load, self.min_load)
            stsv.set_output_value(self.current_mode_electrolyzer, 1)

        else:
            stsv.set_output_value(self.distributed_load, current_load_to_system)
            stsv.set_output_value(self.current_mode_electrolyzer, 1)

        stsv.set_output_value(self.curtailed_load, self.curtailed_load_count)
        stsv.set_output_value(self.total_off_count, self.off_count)
        stsv.set_output_value(self.standby_count_total, self.standby_count)

    def write_to_report(self) -> list[str]:
        """Writes a report."""
        lines = list(self.controllerconfig.get_string_dict())
        lines.append(f"Component Name{self.component_name}")
        lines.append(f"Total curtailed load: {self.curtailed_load_count} [kW]")
        lines.append(f"Number of times the system was switched off: {self.off_count} [#]")
        lines.append(f"Number of times the system was switched to standby mode: {self.standby_count} [#]")
        return lines
