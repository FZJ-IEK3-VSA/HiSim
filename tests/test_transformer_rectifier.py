"""Tests for the TransformerConfig factory and classname classmethods.

These tests pin down the pure, side-effect-free classmethods on
``TransformerConfig`` that are otherwise untested. They only construct
dataclass instances / call classmethods and assert field values - no
simulation, no I/O.
"""

# clean

import json

import pandas as pd
import pytest

from hisim.components.transformer_rectifier import Transformer, TransformerConfig
from hisim.component import (
    Component,
    ComponentOutput,
    SingleTimeStepValues,
    StatelessComponent,
)
from hisim.config import ComponentID, DisplayConfig
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_get_default_transformer_config_returns_config_with_documented_defaults() -> None:
    """``get_default_transformer_config()`` returns a ``TransformerConfig`` with the hardcoded fields."""
    config = TransformerConfig.get_default_transformer_config()
    assert isinstance(config, TransformerConfig)
    assert config.component_id.building is None
    assert config.component_id.name == "GenericTransformerAndRectifier"
    assert config.efficiency == pytest.approx(0.95)


@pytest.mark.base
def test_get_default_transformer_config_is_deterministic_but_distinct() -> None:
    """Two calls return equal values but not the same object identity."""
    first = TransformerConfig.get_default_transformer_config()
    second = TransformerConfig.get_default_transformer_config()
    assert first == second
    assert first is not second


@pytest.mark.base
def test_get_main_classname_is_str() -> None:
    """``get_main_classname()`` returns a string."""
    classname = TransformerConfig.get_main_classname()
    assert isinstance(classname, str)


@pytest.mark.base
def test_get_main_classname_delegates_to_transformer() -> None:
    """``get_main_classname()`` delegates to ``Transformer.get_full_classname()``."""
    classname = TransformerConfig.get_main_classname()
    assert classname == Transformer.get_full_classname()


@pytest.mark.base
def test_get_main_classname_ends_with_transformer() -> None:
    """The fully-qualified class path ends with the ``Transformer`` class name."""
    classname = TransformerConfig.get_main_classname()
    assert classname.endswith(".Transformer")
    # The exact full string is also known, so pin it exactly.
    assert classname == "hisim.components.transformer_rectifier.Transformer"


@pytest.mark.base
def test_transformer_default_display_config_not_shared() -> None:
    """Each ``Transformer`` built without an explicit display config gets its own ``DisplayConfig``.

    A mutable default ``DisplayConfig()`` evaluated once at definition time
    would otherwise be shared across instances, so mutations on one instance's
    display config would contaminate every other instance (and subsequent runs
    in a parametric study).
    """
    mysim = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = TransformerConfig.get_default_transformer_config()

    first = Transformer(my_simulation_parameters=mysim, config=config)
    second = Transformer(my_simulation_parameters=mysim, config=config)

    assert first.my_display_config is not second.my_display_config


@pytest.mark.base
def test_transformer_explicit_display_config_respected() -> None:
    """An explicitly passed ``DisplayConfig`` is used verbatim (not replaced)."""
    mysim = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = TransformerConfig.get_default_transformer_config()
    explicit = DisplayConfig()

    transformer = Transformer(
        my_simulation_parameters=mysim,
        config=config,
        my_display_config=explicit,
    )

    assert transformer.my_display_config is explicit


@pytest.mark.base
def test_transformer_inherits_from_stateless_component() -> None:
    """Transformer inherits from StatelessComponent (and transitively Component).

    This pins the ISP refactor: Transformer no longer inherits from Component
    directly but from StatelessComponent, which supplies no-op state-management
    hooks so that Transformer is not forced to provide stub implementations.
    """
    assert issubclass(Transformer, StatelessComponent)
    assert issubclass(Transformer, Component)


@pytest.mark.base
def test_transformer_does_not_override_state_lifecycle_hooks() -> None:
    """Transformer delegates i_save_state/i_restore_state/i_prepare_simulation/i_doublecheck.

    After the refactor these four lifecycle hooks are inherited as no-ops
    (i_save_state, i_restore_state, i_prepare_simulation from StatelessComponent;
    i_doublecheck from Component) rather than declared as pass-stubs on Transformer
    itself.
    """
    assert "i_save_state" not in Transformer.__dict__
    assert "i_restore_state" not in Transformer.__dict__
    assert "i_prepare_simulation" not in Transformer.__dict__
    assert "i_doublecheck" not in Transformer.__dict__
    # The inherited methods resolve to the base no-ops, not NotImplementedError.
    assert Transformer.i_save_state is StatelessComponent.i_save_state
    assert Transformer.i_restore_state is StatelessComponent.i_restore_state
    assert Transformer.i_prepare_simulation is StatelessComponent.i_prepare_simulation
    assert Transformer.i_doublecheck is Component.i_doublecheck


@pytest.mark.base
def test_transformer_inherited_lifecycle_hooks_are_noops() -> None:
    """Calling the inherited no-op lifecycle hooks on a Transformer instance does not raise."""
    mysim = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = TransformerConfig.get_default_transformer_config()
    transformer = Transformer(my_simulation_parameters=mysim, config=config)
    transformer.i_save_state()
    transformer.i_restore_state()
    transformer.i_prepare_simulation()
    transformer.i_doublecheck(0, SingleTimeStepValues(0))


@pytest.mark.base
def test_transformer_simulate_scales_input_by_efficiency() -> None:
    """i_simulate reads the input power and writes input * efficiency to the output.

    Uses the hoisted-stsv direct-drive seam (KB-4557): a source ComponentOutput
    feeds the transformer's input, values are injected directly into stsv.values,
    and i_simulate is called without an external orchestrator.
    """
    mysim = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = TransformerConfig(component_id=ComponentID(name="Transformer"), efficiency=0.9)
    transformer = Transformer(my_simulation_parameters=mysim, config=config)

    # Create a source output that feeds the transformer's single input.
    source_output = ComponentOutput(
        object_name="Source",
        field_name="Input1",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.KILOWATT,
        output_description="Source power",
        component_id=ComponentID("Source"),
    )
    transformer.electricity_input.source_output = source_output

    number_of_outputs = fft.get_number_of_outputs([transformer, source_output])
    stsv = SingleTimeStepValues(number_of_outputs)
    fft.add_global_index_of_components([transformer, source_output])

    input_power = 10.0
    stsv.values[source_output.global_index] = input_power

    transformer.i_simulate(timestep=0, stsv=stsv, force_convergence=False)

    expected_output = input_power * config.efficiency
    assert stsv.values[transformer.electricity_output.global_index] == pytest.approx(expected_output)


@pytest.mark.base
def test_transformer_config_refuses_an_efficiency_outside_the_fraction_range() -> None:
    """An efficiency that is not a fraction in (0, 1] is refused at construction, by value.

    A percentage (95) would silently scale the output a hundredfold, a zero would make the
    conversion-loss indicator divide by zero, and a negative value would invert the power flow --
    all three used to run as plausible simulations and could only be discovered in the numbers.
    The configuration is where they stop.
    """
    for wrong in (0.0, -0.5, 1.5, 95.0):
        with pytest.raises(ValueError, match="fraction"):
            TransformerConfig(component_id=ComponentID(name="Transformer"), efficiency=wrong)
    assert TransformerConfig(component_id=ComponentID(name="Transformer"), efficiency=1.0).efficiency == 1.0


@pytest.mark.base
def test_transformer_kpi_entries_integrate_output_and_derive_losses() -> None:
    """The two transformer KPIs integrate the delivered kilowatts and derive the losses from the efficiency.

    Two timesteps of 60 s at 100 kW are 3.333 kWh delivered; at 80 % efficiency one quarter of
    the delivered energy was lost on the way (1/0.8 - 1 = 0.25), so the losses are 0.833 kWh.
    Both are hand-derived literals over an explicit non-default efficiency, so a wrong-but-
    consistent losses formula, or a drifted config default, cannot agree with the test.

    A foreign component's kilowatt column is prepended the way the real caller hands the whole
    run's outputs over, so the component_name filter is load-bearing here too.
    """
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = TransformerConfig(component_id=ComponentID(name="Transformer"), efficiency=0.8)
    transformer = Transformer(my_simulation_parameters=mysim, config=config)
    foreign = ComponentOutput(
        object_name="OtherUnit",
        field_name="TransformerOutput",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.KILOWATT,
        output_description="a stranger's output",
        component_id=ComponentID(name="OtherUnit"),
    )
    frame = pd.DataFrame({0: [999.0, 999.0], 1: [100.0, 100.0]})

    entries = {
        e.name: e
        for e in transformer.get_component_kpi_entries([foreign, transformer.electricity_output], frame)
    }

    assert entries["Electrical energy delivered"].value == pytest.approx(3.333)
    assert entries["Conversion losses"].value == pytest.approx(0.833)
    assert all(e.name_of_source_component == transformer.component_name for e in entries.values()), (
        "the source component is the disambiguator a future multi-instance collision fix keys on"
    )
    for entry in entries.values():
        json.dumps(entry.to_dict())  # the webtool writer serializes exactly this; it must not raise


@pytest.mark.base
def test_transformer_kpi_entries_refuse_nan_instead_of_understating() -> None:
    """A NaN in the output column raises instead of being silently dropped from the sum.

    pandas sums with skipna by default, so a NaN timestep would understate the delivered energy
    and the derived losses while looking exactly like a real figure; a KPI is either computed
    from complete values or refused by name.
    """
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = TransformerConfig(component_id=ComponentID(name="Transformer"), efficiency=0.8)
    transformer = Transformer(my_simulation_parameters=mysim, config=config)
    frame = pd.DataFrame({0: [100.0, float("nan")]})

    with pytest.raises(ValueError, match="NaN"):
        transformer.get_component_kpi_entries([transformer.electricity_output], frame)


@pytest.mark.base
def test_transformer_kpi_entries_refuse_a_missing_output() -> None:
    """A missing output column raises naming the component instead of reporting nothing."""
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    transformer = Transformer(my_simulation_parameters=mysim, config=TransformerConfig.get_default_transformer_config())

    with pytest.raises(ValueError, match="transformer output column"):
        transformer.get_component_kpi_entries([], pd.DataFrame())
