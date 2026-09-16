"""Example Component."""

# Generic/Built-in
from typing import ClassVar, List, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.simulationparameters import SimulationParameters
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim.config import ConfigBase, ComponentID, DisplayConfig, Sizable, Size, concrete, preset, sized_field
from hisim import loadtypes as lt
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class ExampleComponentConfig(ConfigBase):
    """Configuration of the example component: a fictitious thermal mass with an electrical load.

    The component stands for nothing real -- it is the file a reader opens to see what a HiSim
    component looks like -- so its configuration is one named default::

        ExampleComponentConfig.preset_standard("ExampleComponent")

    which is the 1 kW heating load at 25 degrees Celsius every caller in this repository builds.

    ``capacity`` is a *sizable* field: its value is not a number written here but a law declared at
    the field, which the sizing kernel evaluates against the facts of the surrounding system (see
    :mod:`hisim.config.sizing`). The preset therefore leaves it at ``AUTO``, and a caller resolves
    the configuration against a :class:`~hisim.config.SizingContext` before handing it to the
    component.
    """

    MAIN_CLASS = "hisim.components.example_component.ExampleComponent"

    #: Specific heat capacity of the fictitious thermal mass this component stands for, in joule
    #: per kelvin and square metre of conditioned floor area. It is the constant half of the
    #: ``capacity`` sizing law below; the other half is the floor area of the building the
    #: component sits in. The number, and the law it forms, are where the literal ``45 * 121.2``
    #: that this config used to carry came from: 121.2 m2 is the conditioned floor area of the
    #: default TABULA building (``BuildingConfig.preset_german_single_family_home``,
    #: ``DE.N.SFH.05.Gen.ReEx.001.002``), so the law reproduces the old value exactly for that
    #: building and scales with any other one.
    SPECIFIC_HEAT_CAPACITY_IN_JOULE_PER_KELVIN_PER_M2: ClassVar[float] = 45.0

    component_id: ComponentID
    #: Physical quantity the component's electrical port carries.
    loadtype: lt.LoadTypes = lt.LoadTypes.HEATING
    #: Unit that quantity is in.
    unit: lt.Units = lt.Units.WATT
    #: Electrical load of the mass in watts, negated by :meth:`ExampleComponent.build` and drawn
    #: in two blocks of the day. ``None`` falls back to the same 1 kW inside that method.
    electricity: Optional[float] = -1e3
    #: Temperature the mass starts the simulation at, in degrees Celsius. ``None`` falls back to
    #: the same 25 degrees inside :meth:`ExampleComponent.build`.
    initial_temperature: Optional[float] = 25.0
    #: Thermal capacity of the modelled mass in J/K: the specific capacity above times the
    #: conditioned floor area of the building. Declared last because a sizable field carries
    #: a default (``AUTO``) and must follow the fields that do not.
    capacity: Sizable[float] = sized_field(
        rule=Size.CONDITIONED_FLOOR_AREA_IN_M2 * SPECIFIC_HEAT_CAPACITY_IN_JOULE_PER_KELVIN_PER_M2,
        value_type=float,
        note=f"{SPECIFIC_HEAT_CAPACITY_IN_JOULE_PER_KELVIN_PER_M2} J/K per m² of conditioned floor area",
    )

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "ExampleComponentConfig":
        """The fictitious mass of the examples: a 1 kW heating load starting at 25 degrees Celsius.

        Args:
            name: Instance name of the component in the simulation.

        Returns:
            The configuration, with ``capacity`` left as ``AUTO`` for the sizing kernel to fill in
            from the conditioned floor area of the building the component is placed in.
        """
        return cls(component_id=ComponentID(name=name))

class ExampleComponent(Component):
    """Example Component class.

    It supports multiple Example Component values for fictitious scenarios.
    The configuration values held by ``config`` (electricity, capacity, and
    initial temperature) are taken as constants to build the load profile for
    the entire simulation duration.

    Parameters
    ----------
    my_simulation_parameters : SimulationParameters
        Passed to initialize :py:class:`~hisim.component.Component`.

    config : ExampleComponentConfig
        The :py:class:`ExampleComponentConfig` object that holds the example
        component configuration (building name, load type, unit, electricity,
        capacity, and initial temperature).

    my_display_config : DisplayConfig, optional
        A :py:class:`~hisim.config.DisplayConfig` object that controls
        how the component is displayed in the simulation results.
        Defaults to an empty :py:class:`~hisim.config.DisplayConfig`.

    """

    cost_relevance = CostRelevance.FREE_OF_COST

    ThermalEnergyDelivered: str = "ThermalEnergyDelivered"

    # Outputs
    ElectricityOutput: str = "ElectricityOutput"
    TemperatureMean: str = "ResidenceTemperature"
    StoredEnergy: str = "StoredEnergy"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ExampleComponentConfig,
        my_display_config: Optional[DisplayConfig] = None,
    ) -> None:
        """Constructs all the necessary attributes."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.examplecomponentconfig: ExampleComponentConfig = config
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: ExampleComponentConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # Initialized variables
        self.temperature: float = -300
        self.previous_temperature: float

        self.build(
            electricity=config.electricity,
            # heat=config.heat,
            # The central check in Component.__init__ has already refused any config that
            # still carries AUTO, so the sized field is a number by now; ``concrete`` is the
            # read-side idiom that says so to the type checker as well.
            capacity=concrete(config.capacity),
            initial_temperature=config.initial_temperature,
        )

        self.thermal_energy_delivered_c: ComponentInput = self.add_input(
            self.examplecomponentconfig.component_id.name,
            self.ThermalEnergyDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            False,
        )

        self.t_m_c: ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureMean,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="Temperature mean",
        )

        self.electricity_output_c: ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Electricity output",
        )
        self.stored_energy_c: ComponentOutput = self.add_output(
            self.component_name,
            self.StoredEnergy,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description="Stored Energy",
        )

    def build(
        self,
        electricity: Optional[float],
        # heat: float,
        capacity: float,
        initial_temperature: Optional[float],
    ) -> None:
        """Build load profile for entire simulation duration."""
        self.time_correction_factor: float = 1 / self.my_simulation_parameters.seconds_per_timestep
        self.seconds_per_timestep: float = self.my_simulation_parameters.seconds_per_timestep

        if electricity is None:
            self.electricity_output: float = -1e3
        else:
            self.electricity_output = -1e3 * electricity

        # No None fallback: ``capacity`` is sized, and a config that still carried AUTO
        # never reaches a component, so the value is always a real number here.
        self.capacity: float = capacity

        if initial_temperature is None:
            self.temperature = 25.0
            self.initial_temperature: float = 25.0
        else:
            self.temperature = initial_temperature
            self.initial_temperature = initial_temperature
        self.previous_temperature = self.temperature

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        lines: List[str] = []
        return lines

    def i_prepare_simulation(self) -> None:
        """No-op: everything this component needs is already built in ``__init__``.

        The ``Simulator`` calls this once on every component before the first timestep.
        This one has nothing left to do here: :meth:`build` has already fixed the load
        profile, the capacity and the initial temperature at construction time, from
        constants in the config. A component whose preparation is expensive, or that
        depends on something only known once every component of the system exists — a
        data file, a precomputed profile, a fact left in the simulation repository —
        does that work here instead.

        The method cannot simply be left out: :meth:`hisim.component.Component.i_prepare_simulation`
        raises ``NotImplementedError``, so a component without it fails before its first
        timestep.
        """

    def i_save_state(self) -> None:
        """Saves the current state of the temperature."""
        self.previous_temperature = self.temperature

    def i_restore_state(self) -> None:
        """Restores previous state of the temperature."""
        self.temperature = self.previous_temperature

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        electricity_output: float = 0
        if 60 * 6 <= timestep < 60 * 9:
            electricity_output = self.electricity_output
        elif 60 * 15 <= timestep < 60 * 18:
            electricity_output = -self.electricity_output

        stsv.set_output_value(self.electricity_output_c, electricity_output)

        if timestep <= 60 * 12:
            thermal_delivered_energy: float = 0
            temperature: float = self.initial_temperature
            current_stored_energy: float = (self.initial_temperature + 273.15) * self.capacity
        else:
            thermal_delivered_energy = stsv.get_input_value(self.thermal_energy_delivered_c)
            previous_stored_energy: float = (self.previous_temperature + 273.15) * self.capacity
            current_stored_energy = previous_stored_energy + thermal_delivered_energy
            self.temperature = current_stored_energy / self.capacity - 273.15
            temperature = self.temperature

        # thermal_delivered_energy = 0
        # temperature = self.initial_temperature
        # current_stored_energy = ( self.initial_temperature + 273.15) * self.capacity
        #    else:
        # thermal_delivered_energy = stsv.get_input_value(self.thermal_energy_deliveredC)
        # previous_stored_energy = (self.previous_temperature + 273.15) * self.capacity
        # current_stored_energy = previous_stored_energy + thermal_delivered_energy
        # self.temperature = current_stored_energy / self.capacity - 273.15
        # temperature = self.temperature

        stsv.set_output_value(self.stored_energy_c, current_stored_energy)
        stsv.set_output_value(self.t_m_c, temperature)
