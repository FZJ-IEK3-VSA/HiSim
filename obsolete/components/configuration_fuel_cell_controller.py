"""The three legacy configuration classes of the retired fuel-cell controller (D-8).

Moved out of `hisim/components/configuration.py` when
`hisim/components/advanced_fuel_cell_controller.py` was retired to
`obsolete/components/` (component sweep decision D-8, 2026-09-10). That controller was their
only user: `ExtendedControllerSimulation` and `ExtendedController` read `CHPControllerConfig`
and `GasControllerConfig` as bare class attributes and take an `ExtendedControllerConfig` as
their configuration, and nothing else in the repository ever named any of the three.

`ExtendedControllerConfig` is a `@dataclass` with no field defaults, so the class-attribute
reads in the controller (`ExtendedControllerConfig.chp`, `.chp_mode`,
`.chp_power_states_possible`, `.maximum_autarky`) raise `AttributeError` on the first call —
which is why the controller was retired rather than converted, and why these three classes
have no future in the wire format.

The classes below are the file's own text, unchanged. Nothing here is maintained.
"""

from typing import Any, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

from hisim.config import ConfigBase, ComponentID


class CHPControllerConfig:
    """Chp controller config.

    The CHP controller is used to implement an on and off hysteresis
    Decide if its heat- or electricity-led

    Two temperature sensors in the tank are giving the needed information.
    They can be set at a height percentage in the tank. =% is the top, 100 % s the bottom of the tank.
    If the T at the upper sensor is below temperature_switch_on the chp will run until the lower sensor is above temperature_switch_off.
    A minimum runtime in minutes can be defined for the chp.

    If the chp is electric-led, ths is not needed and the electricity demand is provided directly to the chp
    """

    method_of_operation = "heat"
    temperature_switch_on = 60  # [°C]
    temperature_switch_off = 65  # [°C]

    # in steps of 20 % [0, 20, 40 ,60, 80, 100]
    heights_in_tank = [0, 20, 40, 60, 80, 100]
    height_upper_sensor = 20  # [%]
    height_lower_sensor = 60  # [%]

    minimum_runtime_minutes = 4000  # [min]


class GasControllerConfig:
    """Gas controller config class.

    This controller works like the CHP controller, but switches on later so the CHP is used more often.
    Gas heater is used as a backup if the CHP power is not high enough.
    If the minimum_runtime is smaller than the timestep, the minimum_runtime is 1 timestep --> generic_gas_heater.py
    """

    temperature_switch_on = 55  # [°C]
    temperature_switch_off = 70  # [°C]

    # in steps of 20 % [0, 20, 40 ,60, 80, 100]
    height_upper_sensor = 20  # [%]
    height_lower_sensor = 80  # [%]

    # minimal timestep is minute
    minimum_runtime_minutes = 7000  # [min]


@dataclass_json
@dataclass
class ExtendedControllerConfig(ConfigBase):
    """Extended controller config class."""

    component_id: ComponentID
    # Active Components
    chp: bool
    gas_heater: bool
    electrolyzer: bool
    # electrolyzer: bool

    # power mode chp
    # chp_mode: str
    chp_mode: str
    chp_power_states_possible: int
    maximum_autarky: bool

    @classmethod
    def get_default_config(
        cls,
        component_id: Optional[ComponentID] = None,
    ) -> Any:
        """Gets a default ExtendedControllerConfig."""
        if component_id is None:
            component_id = ComponentID(name="ExtendedController")
        return ExtendedControllerConfig(
            component_id=component_id,
            chp=True,
            gas_heater=True,
            electrolyzer=True,
            # electrolyzer = False,
            # power mode chp,
            # chp_mode = "heat",
            chp_mode="power",
            chp_power_states_possible=10,
            maximum_autarky=False,
        )
