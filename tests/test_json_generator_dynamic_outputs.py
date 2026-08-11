"""Unit tests for which dynamic outputs :mod:`hisim.json_generator` exports.

A ``DynamicComponent`` can end up with two kinds of dynamic outputs: the ones its own
constructor creates (the EMS builds one per default connection) and the ones a system setup
adds afterwards via ``add_component_output``. Only the latter belong in the scenario JSON --
the constructor makes its own again when the JSON is reloaded, so exporting those would
duplicate them, while dropping a setup-added one leaves the scenario JSON with a connection
pointing at an output that no longer exists.

The boundary between the two used to be a hard-coded output index in
``convert_component_to_json``. Removing a single default connection from the EMS constructor
shifted every index by one and silently pushed the first setup-added output below that
threshold, so it was no longer exported. Each output now records for itself whether it was
created during construction, and ``DynamicComponent`` sets that flag without the component
having to take part. The tests below therefore cover two things: that the real components get
classified correctly, and that the constructor shapes which defeat a hand-placed marker --
an output created last, a subclass adding outputs of its own, a subclass without its own
constructor -- are still classified correctly.

These are construction-only tests: no simulation is run and no input data is read.
"""

# clean

from dataclasses import dataclass
from typing import Any, Dict, List

import pytest

import hisim.loadtypes as lt
from hisim.component import ConfigBase, DisplayConfig
from hisim.components.controller_l2_energy_management_system import (
    EMSConfig,
    L2GenericEnergyManagementSystem,
)
from hisim.components.electricity_meter import ElectricityMeter, ElectricityMeterConfig
from hisim.components.fuel_meter import FuelMeter, FuelMeterConfig
from hisim.components.gas_meter import GasMeter, GasMeterConfig
from hisim.components.heating_meter import HeatingMeter, HeatingMeterConfig
from hisim.dynamic_component import (
    DynamicComponent,
    DynamicConnectionInput,
    DynamicConnectionOutput,
)
from hisim.json_generator import convert_component_to_json
from hisim.simulationparameters import SimulationParameters


def _simulation_parameters() -> SimulationParameters:
    """Gives the cheapest simulation parameters that let a component be constructed."""
    return SimulationParameters.one_week_only(year=2021, seconds_per_timestep=60 * 60)


def _make_ems() -> L2GenericEnergyManagementSystem:
    """Constructs an EMS from its default config, as a system setup would."""
    # Its constructor carries an untyped decorator, so what it builds is Any as far as mypy
    # is concerned. Naming the result pins the type back down.
    ems: L2GenericEnergyManagementSystem = L2GenericEnergyManagementSystem(
        my_simulation_parameters=_simulation_parameters(),
        config=EMSConfig.get_default_config_ems(),
    )
    return ems


def _add_dynamic_output(component: DynamicComponent, source_output_name: str = "LoadingPowerInputForBattery_") -> str:
    """Adds one dynamic output the way a constructor or a system setup does, returns its field name."""
    return component.add_component_output(
        source_output_name=source_output_name,
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=6,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for Battery Control. ",
    ).field_name


@dataclass
class ComponentUnderTestConfig(ConfigBase):
    """Config of the throwaway components below, which only ever get constructed."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the class this config configures."""
        return ComponentUnderTest.get_full_classname()


class ComponentUnderTest(DynamicComponent):
    """A dynamic component that creates an output as the very last statement of its constructor.

    That is the position a hand-placed end-of-construction marker cannot cover: whoever adds an
    output down here does not necessarily notice that the marker further up has to move.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: ComponentUnderTestConfig) -> None:
        """Initializes."""
        self.my_component_inputs: List[DynamicConnectionInput] = []
        self.my_component_outputs: List[DynamicConnectionOutput] = []
        super().__init__(
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            name=config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        _add_dynamic_output(self, "CreatedLastByBaseConstructor_")


class SubComponentUnderTest(ComponentUnderTest):
    """A subclass that creates an output of its own, after the base class constructor has run.

    A marker placed at the end of the base class constructor fires while this subclass is still
    constructing itself, so it would classify the output below as added from the outside.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: ComponentUnderTestConfig) -> None:
        """Initializes."""
        super().__init__(my_simulation_parameters=my_simulation_parameters, config=config)
        _add_dynamic_output(self, "CreatedBySubclassConstructor_")


class SubComponentWithoutConstructorUnderTest(ComponentUnderTest):
    """A subclass that inherits its constructor, which still has to end the construction."""


def _make_component_under_test(component_class: type) -> DynamicComponent:
    """Constructs one of the throwaway components above."""
    # Calling a bare ``type`` gives mypy an Any, so name the result to pin the type back down.
    component: DynamicComponent = component_class(
        my_simulation_parameters=_simulation_parameters(),
        config=ComponentUnderTestConfig(building_name="BUI1", name=component_class.__name__),
    )
    return component


# Every DynamicComponent in hisim/components together with the way a system setup builds it.
# A new dynamic component belongs in here, so that it is covered by the export tests below.
REAL_COMPONENTS = [
    (L2GenericEnergyManagementSystem, EMSConfig.get_default_config_ems),
    (ElectricityMeter, ElectricityMeterConfig.get_electricity_meter_default_config),
    (GasMeter, GasMeterConfig.get_gas_meter_default_config),
    (FuelMeter, FuelMeterConfig.get_fuel_meter_default_config),
    (HeatingMeter, HeatingMeterConfig.get_heating_meter_default_config),
]


@pytest.mark.base
@pytest.mark.parametrize(
    "component_class, config_factory",
    REAL_COMPONENTS,
    ids=lambda value: getattr(value, "__name__", value),
)
def test_untouched_component_exports_no_dynamic_outputs(component_class: type, config_factory: Any) -> None:
    """A component that nobody touched after constructing it exports no dynamic outputs at all.

    Everything it has at this point is created again by its constructor when the scenario JSON is
    executed, so exporting any of it would duplicate the output.
    """
    component = component_class(my_simulation_parameters=_simulation_parameters(), config=config_factory())

    _, _, outs = convert_component_to_json(config=component.config, component=component)

    assert component.is_under_construction is False
    assert all(output.created_during_construction for output in component.my_component_outputs)
    assert not outs


@pytest.mark.base
def test_ems_creates_dynamic_outputs_while_constructing() -> None:
    """The EMS really does create dynamic outputs itself, which keeps the test above meaningful.

    Its default connections each add one, so an empty export there is a decision rather than the
    trivial result of the component having no dynamic outputs in the first place.
    """
    ems = _make_ems()

    assert len(ems.my_component_outputs) > 0


@pytest.mark.base
def test_setup_added_output_is_exported() -> None:
    """An output added after construction is exported, and only that one.

    This is the case the hard-coded threshold used to drop: the export stayed empty while the
    scenario JSON still contained a connection referencing the missing output, which made the
    reloaded simulator fail to connect.
    """
    ems = _make_ems()
    field_name = _add_dynamic_output(ems)

    _, _, outs = convert_component_to_json(config=ems.config, component=ems)

    assert ems.my_component_outputs[-1].created_during_construction is False
    assert len(outs) == 1
    assert outs[0]["source_output_name"] == "LoadingPowerInputForBattery_"
    assert outs[0]["source_weight"] == 6
    # The exported name carries no output index; the index is re-derived on reload and only
    # matches again because the constructor-made outputs were left out above.
    assert field_name.endswith(f"Output{len(ems.outputs)}")


@pytest.mark.base
def test_reloading_an_exported_output_reproduces_the_same_export() -> None:
    """Rebuilding a component from its export and exporting it again gives the same result.

    This is the round trip hisim.json_executor performs: it constructs the component and replays
    the exported outputs on top. Should the replayed output ever count as constructor-made, the
    next export would silently drop it and the scenario would lose an output per round trip.
    """
    exported_from_setup = _export_of_ems_with_one_setup_output()

    reloaded = _make_ems()
    reloaded.add_component_output(
        source_output_name=exported_from_setup["source_output_name"],
        source_tags=exported_from_setup["source_tags"],
        source_load_type=lt.LoadTypes(exported_from_setup["source_load_type"]),
        source_unit=lt.Units(exported_from_setup["source_unit"]),
        source_weight=exported_from_setup["source_weight"],
        output_description=exported_from_setup["output_description"],
        source_component_class=exported_from_setup["source_component_class"],
    )
    _, _, outs = convert_component_to_json(config=reloaded.config, component=reloaded)

    assert outs == [exported_from_setup]


def _export_of_ems_with_one_setup_output() -> Dict[str, Any]:
    """Gives the single exported output of an EMS that a system setup added one output to."""
    ems = _make_ems()
    _add_dynamic_output(ems)
    _, _, outs = convert_component_to_json(config=ems.config, component=ems)
    return outs[0]  # type: ignore[no-any-return]


@pytest.mark.base
def test_output_created_last_in_a_constructor_is_constructor_made() -> None:
    """An output created as the last statement of a constructor still counts as constructor-made.

    Nothing in the component marks the end of its construction, so an output added at the very
    bottom of the constructor cannot end up on the wrong side of such a mark.
    """
    component = _make_component_under_test(ComponentUnderTest)

    assert component.is_under_construction is False
    assert [output.created_during_construction for output in component.my_component_outputs] == [True]


@pytest.mark.base
def test_subclass_constructor_outputs_are_constructor_made() -> None:
    """A subclass creating outputs of its own is still constructing, not setting up.

    Its base class constructor has already returned at that point, which is exactly what a marker
    at the end of the base class constructor would have taken for the end of the construction.
    """
    component = _make_component_under_test(SubComponentUnderTest)

    assert component.is_under_construction is False
    assert [output.created_during_construction for output in component.my_component_outputs] == [True, True]


@pytest.mark.base
def test_subclass_without_own_constructor_ends_construction() -> None:
    """A subclass that inherits its constructor gets its construction ended by that constructor."""
    component = _make_component_under_test(SubComponentWithoutConstructorUnderTest)

    assert component.is_under_construction is False
    assert [output.created_during_construction for output in component.my_component_outputs] == [True]

    assert _add_dynamic_output(component, "AddedBySetup_")
    assert component.my_component_outputs[-1].created_during_construction is False


@pytest.mark.base
@pytest.mark.parametrize(
    "component_class",
    [ComponentUnderTest, SubComponentUnderTest, SubComponentWithoutConstructorUnderTest],
    ids=lambda component_class: component_class.__name__,
)
def test_only_setup_added_outputs_are_exported(component_class: type) -> None:
    """Whatever a constructor created stays out of the export, whatever came after it goes in."""
    component = _make_component_under_test(component_class)
    _add_dynamic_output(component, "AddedBySetup_")

    _, _, outs = convert_component_to_json(config=component.config, component=component)

    assert [out["source_output_name"] for out in outs] == ["AddedBySetup_"]
