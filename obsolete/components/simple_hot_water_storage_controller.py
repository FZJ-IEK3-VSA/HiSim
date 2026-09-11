"""The zero-call-site hot water storage controller (D-16).

Moved out of `hisim/components/simple_water_storage.py` by component sweep decision D-16
(2026-09-10). `SimpleHotWaterStorageController` and its configuration had no call site
anywhere in the repository — no system setup, no test, no other component, no scenario JSON
and no energy-system file ever named either of them. Converting them would have minted a
preset name into the wire format for a controller nothing builds; the rest of
`simple_water_storage.py` (`SimpleWaterStorage`, `SimpleHotWaterStorage`, `SimpleDHWStorage`
and their configurations) is live and stays there.

The controller also carried one of the two reads of the `WATERMASSFLOWRATEOFHEATGENERATOR`
singleton key that no component writes any more. That read leaves `hisim/` with this file;
the second one, in `SimpleHotWaterStorage.__init__`, stays behind with the live class.

The classes below are the file's own text, unchanged. The imports they need are recorded as a
comment rather than as live imports: nothing here is maintained, and nothing imports from
`obsolete/`.

    import hisim.component as cp
    from dataclasses import dataclass
    from typing import Any
    from dataclasses_json import dataclass_json

    from hisim import loadtypes as lt
    from hisim.component import SingleTimeStepValues, ComponentInput, ComponentOutput
    from hisim.config import ConfigBase, ComponentID, DisplayConfig
    from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
    from hisim.simulationparameters import SimulationParameters
    from hisim.economics.facts import CostRelevance
"""


@dataclass_json
@dataclass
class SimpleHotWaterStorageControllerConfig(ConfigBase):
    """Configuration of the SimpleHotWaterStorageController class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return SimpleHotWaterStorageController.get_full_classname()

    component_id: ComponentID

    @classmethod
    def get_default_simplehotwaterstoragecontroller_config(
        cls,
    ) -> Any:
        """Get a default simplehotwaterstorage controller config."""
        config = SimpleHotWaterStorageControllerConfig(
            component_id=ComponentID(name="SimpleHotWaterStorageController"),
        )
        return config


class SimpleHotWaterStorageController(cp.Component):
    """SimpleHotWaterStorageController Class."""

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    WaterMassFlowRateFromHeatGenerator = "WaterMassFlowRateFromHeatGenerator"

    # Outputs
    State = "State"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleHotWaterStorageControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        if SingletonSimRepository().entry_exists(key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR):
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo = (
                SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR)
            )
        else:
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo = None

        self.controller_mode: str = "off"
        # Inputs
        self.water_mass_flow_rate_heat_generator_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterMassFlowRateFromHeatGenerator,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            False,
        )
        # Outputs
        self.state_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.State,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description=f"here a description for {self.State} will follow.",
        )

    def build(self) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        pass

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> None:
        """Write important variables to report."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the heat pump comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs

            # get water mass flow rate of heat generator either from singleton sim repo or from input value
            if self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo is not None:
                water_mass_flow_rate_from_heat_generator_in_kg_per_second = (
                    self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo
                )
            else:
                water_mass_flow_rate_from_heat_generator_in_kg_per_second = stsv.get_input_value(
                    self.water_mass_flow_rate_heat_generator_input_channel
                )

            self.conditions_on_off(
                water_mass_flow_rate_from_heat_generator_in_kg_per_second=water_mass_flow_rate_from_heat_generator_in_kg_per_second
            )

            if self.controller_mode == "on":
                state = 1
            elif self.controller_mode == "off":
                state = 0

            else:
                raise ValueError("Controller State unknown.")

            stsv.set_output_value(self.state_channel, state)

    def conditions_on_off(
        self,
        water_mass_flow_rate_from_heat_generator_in_kg_per_second: float,
    ) -> None:
        """Set conditions for the simple hot water storage controller mode."""

        if self.controller_mode == "on":
            # turn mode off when heat generator delivers no water
            if water_mass_flow_rate_from_heat_generator_in_kg_per_second == 0:
                self.controller_mode = "off"
                return

        elif self.controller_mode == "off":
            # turn mode on if water from heat generator is flowing
            if water_mass_flow_rate_from_heat_generator_in_kg_per_second != 0:
                self.controller_mode = "on"
                return

        else:
            raise ValueError("unknown controller mode")
