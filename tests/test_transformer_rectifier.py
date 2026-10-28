"""Tests for the TransformerConfig preset and classname classmethods.

These tests pin down the pure, side-effect-free classmethods on
``TransformerConfig`` that are otherwise untested. They only construct
dataclass instances / call classmethods and assert field values - no
simulation, no I/O.
"""

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
def test_preset_standard_returns_config_with_documented_defaults() -> None:
    """``preset_standard()`` names the instance it is given and carries the field defaults."""
    config = TransformerConfig.preset_standard("GenericTransformerAndRectifier")
    assert isinstance(config, TransformerConfig)
    assert config.component_id.building is None
    assert config.component_id.name == "GenericTransformerAndRectifier"
    assert config.efficiency == pytest.approx(0.95)
    assert config.rated_power_in_kilowatt == pytest.approx(1000.0)
    assert config.received_carrier == lt.EnergyBalanceCarrier.ELECTRICITY
    # All five cost fields stay unset so that postprocessing looks the figures up from the device
    # database for the simulated year and country instead of freezing them into every caller.
    assert config.device_co2_footprint_in_kg is None
    assert config.investment_costs_in_euro is None
    assert config.lifetime_in_years is None
    assert config.maintenance_costs_in_euro_per_year is None
    assert config.subsidy_as_percentage_of_investment_costs is None


@pytest.mark.base
def test_preset_standard_is_deterministic_but_distinct() -> None:
    """Two calls return equal values but not the same object identity."""
    first = TransformerConfig.preset_standard("GenericTransformerAndRectifier")
    second = TransformerConfig.preset_standard("GenericTransformerAndRectifier")
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
    config = TransformerConfig.preset_standard("GenericTransformerAndRectifier")

    first = Transformer(my_simulation_parameters=mysim, config=config)
    second = Transformer(my_simulation_parameters=mysim, config=config)

    assert first.my_display_config is not second.my_display_config


@pytest.mark.base
def test_transformer_explicit_display_config_respected() -> None:
    """An explicitly passed ``DisplayConfig`` is used verbatim (not replaced)."""
    mysim = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = TransformerConfig.preset_standard("GenericTransformerAndRectifier")
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
    config = TransformerConfig.preset_standard("GenericTransformerAndRectifier")
    transformer = Transformer(my_simulation_parameters=mysim, config=config)
    transformer.i_save_state()
    transformer.i_restore_state()
    transformer.i_prepare_simulation()
    transformer.i_doublecheck(0, SingleTimeStepValues(0))


def one_step_of_a_transformer(offered_power_in_kilowatt: float) -> dict:
    """One step of a 100 kW transformer at 90 % offered ``offered_power_in_kilowatt``, as ``output name -> value``.

    Uses the hoisted-stsv direct-drive seam (KB-4557): a source ComponentOutput feeds the transformer's input, the
    value is injected directly into stsv.values, and i_simulate is called without an external orchestrator.
    """
    mysim = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = TransformerConfig(
        component_id=ComponentID(name="Transformer"), efficiency=0.9, rated_power_in_kilowatt=100.0
    )
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

    stsv.values[source_output.global_index] = offered_power_in_kilowatt

    transformer.i_simulate(timestep=0, stsv=stsv, force_convergence=False)

    return {output.field_name: stsv.values[output.global_index] for output in transformer.outputs}


@pytest.mark.base
def test_transformer_simulate_scales_input_by_efficiency() -> None:
    """Below its rating the unit takes the whole input, delivers input * efficiency in W and curtails nothing.

    10 kW offered at 90 % are 9,000 W delivered and 1,000 W lost. Catches the kW input published as W without its
    factor and a curtailment below the rating.
    """
    values = one_step_of_a_transformer(offered_power_in_kilowatt=10.0)

    assert values[Transformer.ElectricityDeliveredInWatt] == pytest.approx(9000.0, rel=1e-12)
    assert values[Transformer.ElectricityReceivedInWatt] == pytest.approx(10000.0, rel=1e-12)
    assert values[Transformer.ConversionLossInWatt] == pytest.approx(1000.0, rel=1e-12)
    assert values[Transformer.CurtailedInputInWatt] == 0.0


@pytest.mark.base
def test_transformer_delivers_at_most_its_rating_and_curtails_the_rest_of_the_input() -> None:
    """Above its rating the unit delivers its rating, receives rating / efficiency and curtails the rest of the input.

    A 100 kW unit at 90 % offered 500 kW delivers 100 kW, receives 111.1 kW, loses 11.1 kW and curtails 388.9 kW;
    received equals delivered plus lost, and offered equals received plus curtailed. Catches a unit that passes more
    than its rating (bead hisim-component-physics-4g9.69) and a curtailment counted as a loss.
    """
    values = one_step_of_a_transformer(offered_power_in_kilowatt=500.0)

    received_in_watt = 100_000.0 / 0.9
    assert values[Transformer.ElectricityDeliveredInWatt] == pytest.approx(100_000.0, rel=1e-12)
    assert values[Transformer.ElectricityReceivedInWatt] == pytest.approx(received_in_watt, rel=1e-12)
    assert values[Transformer.ConversionLossInWatt] == pytest.approx(received_in_watt - 100_000.0, rel=1e-12)
    assert values[Transformer.CurtailedInputInWatt] == pytest.approx(500_000.0 - received_in_watt, rel=1e-12)


@pytest.mark.base
def test_transformer_books_a_wind_turbines_electricity_from_the_wind_turbine_node() -> None:
    """A unit fed by a wind turbine's series books what it receives as wind electricity from the "wind turbine" node.

    Catches the received port of such a unit booked as electricity, which the electricity meter would read as
    consumption, and a carrier the unit cannot receive accepted by the configuration.
    """
    config = TransformerConfig.preset_standard("Transformer")
    config.received_carrier = lt.EnergyBalanceCarrier.WIND_ELECTRICITY
    transformer = Transformer(SimulationParameters.one_day_only(2021, 60), config)

    port = transformer.electricity_received_channel.energy_port
    assert port is not None
    assert (port.role, port.carrier, port.environment) == (
        lt.EnergyRole.CONSUMPTION,
        lt.EnergyBalanceCarrier.WIND_ELECTRICITY,
        lt.EnvironmentNode.WIND_TURBINE,
    )
    assert not port.is_metered
    with pytest.raises(ValueError, match="receives electricity or wind_electricity, not hydrogen"):
        TransformerConfig(component_id=ComponentID(name="Transformer"), received_carrier=lt.EnergyBalanceCarrier.HYDROGEN)


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
            TransformerConfig(
                component_id=ComponentID(name="Transformer"), efficiency=wrong, rated_power_in_kilowatt=100.0
            )
    assert (
        TransformerConfig(
            component_id=ComponentID(name="Transformer"), efficiency=1.0, rated_power_in_kilowatt=100.0
        ).efficiency
        == 1.0
    )


def two_steps_of_transformer_columns(transformer: Transformer) -> tuple:
    """The transformer's three integrated outputs over two steps of 60 s, and a frame of their columns in W.

    100 kW delivered, 25 kW lost and 50 kW curtailed in each step: 3.333 kWh, 0.833 kWh and 1.667 kWh.
    """
    outputs = [transformer.electricity_output, transformer.conversion_loss_channel, transformer.curtailed_input_channel]
    frame = pd.DataFrame({0: [100_000.0, 100_000.0], 1: [25_000.0, 25_000.0], 2: [50_000.0, 50_000.0]})
    return outputs, frame


@pytest.mark.base
def test_transformer_kpi_entries_integrate_its_delivered_lost_and_curtailed_power() -> None:
    """The three transformer KPIs integrate the delivered, lost and curtailed power columns in W.

    Two timesteps of 60 s at 100 kW are 3.333 kWh delivered, at 25 kW 0.833 kWh lost and at 50 kW 1.667 kWh
    curtailed: hand-derived literals, so a column read in the wrong unit or under the wrong KPI cannot agree.

    A foreign component's column of the same name is prepended the way the real caller hands the whole
    run's outputs over, so the component_name filter is load-bearing here too.
    """
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = TransformerConfig(
        component_id=ComponentID(name="Transformer"), efficiency=0.8, rated_power_in_kilowatt=100.0
    )
    transformer = Transformer(my_simulation_parameters=mysim, config=config)
    foreign = ComponentOutput(
        object_name="OtherUnit",
        field_name=Transformer.ElectricityDeliveredInWatt,
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        output_description="a stranger's output",
        component_id=ComponentID(name="OtherUnit"),
    )
    outputs, columns = two_steps_of_transformer_columns(transformer)
    frame = pd.concat([pd.DataFrame({0: [999.0, 999.0]}), columns.rename(columns=lambda index: index + 1)], axis=1)

    entries = {e.name: e for e in transformer.component_kpi_entries([foreign, *outputs], frame)}

    assert entries["Electrical energy delivered"].value == pytest.approx(3.333)
    assert entries["Conversion losses"].value == pytest.approx(0.833)
    assert entries["Curtailed input energy"].value == pytest.approx(1.667)
    assert all(e.source == transformer.kpi_source() for e in entries.values()), (
        "every entry carries its component's source, the address its key is qualified with"
    )
    assert all(e.name_of_source_component == transformer.component_name for e in entries.values())
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
    config = TransformerConfig(
        component_id=ComponentID(name="Transformer"), efficiency=0.8, rated_power_in_kilowatt=100.0
    )
    transformer = Transformer(my_simulation_parameters=mysim, config=config)
    outputs, frame = two_steps_of_transformer_columns(transformer)
    frame.iloc[1, 0] = float("nan")

    with pytest.raises(ValueError, match="NaN"):
        transformer.component_kpi_entries(outputs, frame)


@pytest.mark.base
def test_transformer_kpi_entries_refuse_a_missing_output() -> None:
    """A missing output column raises naming the component instead of reporting nothing."""
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    transformer = Transformer(
        my_simulation_parameters=mysim,
        config=TransformerConfig.preset_standard("GenericTransformerAndRectifier"),
    )

    with pytest.raises(ValueError, match="transformer output columns .* were not found"):
        transformer.component_kpi_entries([], pd.DataFrame())


@pytest.mark.base
def test_transformer_config_refuses_a_non_positive_rated_power() -> None:
    """A rating of zero or less is refused at construction, by value.

    The investment cost is the rating times a price per kilowatt, so an unrated unit would be
    costed at zero euros and reported as an answer -- a device that reads as free rather than as
    unsized. The configuration is where that stops.
    """
    for wrong in (0.0, -1.0):
        with pytest.raises(ValueError, match="rated power"):
            TransformerConfig(
                component_id=ComponentID(name="Transformer"), efficiency=0.95, rated_power_in_kilowatt=wrong
            )


@pytest.mark.base
def test_transformer_capex_scales_with_the_rated_power() -> None:
    """The investment cost, embodied CO2 and maintenance cost are all linear in the rating.

    The device database prices this unit per kilowatt of nameplate rating, so doubling the rating
    has to double all three figures exactly. Asserting the ratio rather than the euros keeps the
    test meaningful when the owner revises the proposed price per kilowatt, while still catching
    a capex that ignores the rating (the failure this whole cost model exists to prevent) or one
    that scales by a squared or halved rating.
    """
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    small = Transformer.get_cost_capex(
        TransformerConfig(
            component_id=ComponentID(name="Small"), efficiency=0.95, rated_power_in_kilowatt=500.0
        ),
        mysim,
    )
    large = Transformer.get_cost_capex(
        TransformerConfig(
            component_id=ComponentID(name="Large"), efficiency=0.95, rated_power_in_kilowatt=1000.0
        ),
        mysim,
    )

    assert small.capex_investment_cost_in_euro > 0.0
    assert small.device_co2_footprint_in_kg > 0.0
    assert small.maintenance_costs_in_euro_per_year > 0.0
    assert 20.0 < small.lifetime_in_years < 40.0, "a transformer and rectifier are a multi-decade asset"
    assert large.capex_investment_cost_in_euro == pytest.approx(2 * small.capex_investment_cost_in_euro)
    assert large.device_co2_footprint_in_kg == pytest.approx(2 * small.device_co2_footprint_in_kg)
    assert large.maintenance_costs_in_euro_per_year == pytest.approx(2 * small.maintenance_costs_in_euro_per_year)
    # The rating is stated in kilowatts, so the price per kilowatt has to land in the range the
    # database entry cites (100-200 EUR/kW) rather than being off by a factor of a thousand.
    assert 100.0 <= small.capex_investment_cost_in_euro / 500.0 <= 200.0


@pytest.mark.base
def test_transformer_capex_prefers_explicit_config_values_over_the_database() -> None:
    """A configuration carrying all five cost fields is used verbatim, with no database lookup.

    This is the escape hatch for a specific quoted unit, and it is all-or-nothing: the helper
    consults the database only while all five fields are ``None``. Pinning it here keeps a future
    change to the shared helper from silently overriding a user's own numbers.
    """
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = TransformerConfig(
        component_id=ComponentID(name="Quoted"),
        efficiency=0.95,
        rated_power_in_kilowatt=500.0,
        device_co2_footprint_in_kg=1234.0,
        investment_costs_in_euro=45000.0,
        lifetime_in_years=30.0,
        maintenance_costs_in_euro_per_year=900.0,
        subsidy_as_percentage_of_investment_costs=0.1,
    )

    capex = Transformer.get_cost_capex(config, mysim)

    assert capex.capex_investment_cost_in_euro == pytest.approx(45000.0)
    assert capex.device_co2_footprint_in_kg == pytest.approx(1234.0)
    assert capex.lifetime_in_years == pytest.approx(30.0)
    assert capex.maintenance_costs_in_euro_per_year == pytest.approx(900.0)
    assert capex.subsidy_as_percentage_of_investment_costs == pytest.approx(0.1)


@pytest.mark.base
def test_transformer_opex_prices_the_conversion_losses_only() -> None:
    """The operating cost covers the losses, not the throughput or the curtailed input, and carries a maintenance share.

    Everything the unit does not lose it passes on, and the consumer downstream reports that
    share as its own consumption, so the two are disjoint and the system total may add them.
    Reporting the throughput here instead would count the same kilowatt-hours twice, which is the
    failure this test catches: two timesteps of 60 s at 100 kW are 3.333 kWh delivered, at 25 kW
    lost 0.833 kWh -- the consumption reported must be the latter.
    """
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = TransformerConfig(
        component_id=ComponentID(name="Transformer"), efficiency=0.8, rated_power_in_kilowatt=500.0
    )
    transformer = Transformer(my_simulation_parameters=mysim, config=config)
    outputs, frame = two_steps_of_transformer_columns(transformer)

    opex = transformer.get_cost_opex(outputs, frame)

    assert opex.total_consumption_in_kwh == pytest.approx(0.833)
    assert opex.loadtype == lt.LoadTypes.ELECTRICITY
    # Both the cost and the footprint are the same kilowatt-hours times a positive per-kWh
    # factor, so each must be a positive fraction of a euro / kilogram rather than zero.
    assert 0.0 < opex.opex_energy_cost_in_euro < 1.0
    assert 0.0 < opex.co2_footprint_in_kg < 1.0
    assert opex.opex_maintenance_cost_in_euro > 0.0
