"""The heating curve: the supply temperature it sets, the heating season it decides, and its threshold law."""

import math

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import building
from hisim.components.heating_curve import (
    EmitterDesign,
    HeatDistributionSystemType,
    HeatingCurveController,
    HeatingCurveControllerConfig,
)
from hisim.config import ComponentID, SizingContext
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
@pytest.mark.parametrize(
    ("emitter", "outside_temperature_in_celsius", "raise_in_kelvin", "expected_supply_temperature_in_celsius"),
    [
        (HeatDistributionSystemType.FLOORHEATING, -7.0, 0.0, 35.0),  # the design day: the emitter's design supply
        (HeatDistributionSystemType.FLOORHEATING, 5.0, 0.0, 28.79073817878373),
        (HeatDistributionSystemType.FLOORHEATING, 20.0, 0.0, 20.0),  # no load: the room set temperature
        (HeatDistributionSystemType.FLOORHEATING, 25.0, 0.0, 20.0),  # cooling: the room set temperature, at least 19 °C
        (HeatDistributionSystemType.LOW_TEMPERATURE_RADIATOR, -7.0, 0.0, 55.0),
        (HeatDistributionSystemType.RADIATOR, 0.0, 0.0, 59.69293923798236),
        (
            HeatDistributionSystemType.RADIATOR,
            0.0,
            2.0,
            60.81079437589793,
        ),  # the energy manager's raise lifts the curve
    ],
)
def test_the_supply_temperature_follows_the_curve_of_the_emitter(
    emitter: HeatDistributionSystemType,
    outside_temperature_in_celsius: float,
    raise_in_kelvin: float,
    expected_supply_temperature_in_celsius: float,
) -> None:
    """Catches a curve that misses the emitter's design point, drops the manager's raise, or falls below the room."""
    assert HeatingCurveController.supply_temperature_set_in_celsius(
        daily_average_outside_temperature_in_celsius=outside_temperature_in_celsius,
        room_set_temperature_in_celsius=20.0,
        room_set_temperature_raise_in_kelvin=raise_in_kelvin,
        heating_reference_temperature_in_celsius=-7.0,
        emitter_design=EmitterDesign.for_type(emitter),
    ) == pytest.approx(expected_supply_temperature_in_celsius, rel=1e-15)


@pytest.mark.base
def test_the_cooling_supply_never_falls_below_19_celsius() -> None:
    """Catches a cooling supply below the dew-point floor of 19 °C, which would let room air condense on the floor."""
    assert (
        HeatingCurveController.supply_temperature_set_in_celsius(
            daily_average_outside_temperature_in_celsius=30.0,
            room_set_temperature_in_celsius=18.0,
            room_set_temperature_raise_in_kelvin=0.0,
            heating_reference_temperature_in_celsius=-7.0,
            emitter_design=EmitterDesign.for_type(HeatDistributionSystemType.FLOORHEATING),
        )
        == HeatingCurveController.MINIMAL_COOLING_SUPPLY_TEMPERATURE_IN_CELSIUS
    )


@pytest.mark.base
@pytest.mark.parametrize(
    ("outside_temperature_in_celsius", "expected"),
    [(17.9, True), (18.0, True), (18.1, False), (-10.0, True), (30.0, False)],
)
def test_the_heating_season_includes_a_day_exactly_at_the_threshold(
    outside_temperature_in_celsius: float, expected: bool
) -> None:
    """Catches the crash a day exactly at the threshold used to raise, and a season on the wrong side of it."""
    assert (
        HeatingCurveController.is_heating_season(
            daily_average_outside_temperature_in_celsius=outside_temperature_in_celsius,
            heating_threshold_in_celsius=18.0,
        )
        is expected
    )


@pytest.mark.base
def test_the_heating_threshold_is_computed_from_the_building_it_serves() -> None:
    """Catches a threshold that does not follow the building's specific heating load.

    The standard building asks for 7780.75 W over 121.2 m², 64.2 W/m², which lands in the middle band, 18 °C. The three
    bands are pinned through the context as well, so the law is tested where the setups meet it.
    """
    default_building = building.BuildingConfig.preset_german_single_family_home("Building")
    default_building.heating_reference_temperature_in_celsius = -7.0
    resolved = HeatingCurveControllerConfig.preset_building_derived("HeatingCurveController").resolve(
        SizingContext.for_building(default_building).with_facts(
            heating_reference_temperature_in_celsius=-7.0,
            heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
        )
    )
    assert resolved.set_heating_threshold_outside_temperature_in_celsius == 18.0

    floor_area_in_m2 = 100.0
    bands_in_watt_per_m2_to_threshold = {40.0: 16.0, 50.0: 16.0, 64.2: 18.0, 80.0: 18.0, 100.0: 20.0}
    for specific_load_in_watt_per_m2, expected_threshold_in_celsius in bands_in_watt_per_m2_to_threshold.items():
        banded = HeatingCurveControllerConfig.preset_building_derived("HeatingCurveController").resolve(
            SizingContext(
                heating_load_in_watt=specific_load_in_watt_per_m2 * floor_area_in_m2,
                conditioned_floor_area_in_m2=floor_area_in_m2,
                heating_reference_temperature_in_celsius=-7.0,
                set_heating_temperature_in_celsius=20.0,
                heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
            )
        )
        assert banded.set_heating_threshold_outside_temperature_in_celsius == expected_threshold_in_celsius, (
            f"{specific_load_in_watt_per_m2} W/m² must give {expected_threshold_in_celsius} °C"
        )


class TestWiredCurve:

    """The heating curve as a component: what it publishes from its inputs, and that forced convergence freezes it."""

    @staticmethod
    def curve_with_inputs() -> tuple:
        """Build a floor-heating curve for an 18 °C threshold with its two inputs wired to stand-in outputs.

        Returns:
            The curve, the day-mean output, the raise output and the step values.
        """
        config = HeatingCurveControllerConfig(
            component_id=ComponentID(name="HeatingCurveController"),
            set_heating_threshold_outside_temperature_in_celsius=18.0,
            heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
            heating_reference_temperature_in_celsius=-7.0,
            set_heating_temperature_for_building_in_celsius=20.0,
        )
        curve = HeatingCurveController(
            my_simulation_parameters=SimulationParameters.one_day_only(2021, 60), config=config
        )
        day_mean = cp.ComponentOutput(
            "Weather", "DayMean", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, component_id=ComponentID(name="Weather")
        )
        raise_output = cp.ComponentOutput(
            "EMS", "Raise", lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN, component_id=ComponentID(name="EMS")
        )
        curve.daily_average_outside_temperature_channel.source_output = day_mean
        curve.building_temperature_modifier_channel.source_output = raise_output
        outputs = [day_mean, raise_output, *curve.outputs]
        for index, output in enumerate(outputs):
            output.global_index = index
        return curve, day_mean, raise_output, cp.SingleTimeStepValues(len(outputs))

    @pytest.mark.base
    def test_it_publishes_the_supply_temperature_and_the_season(self) -> None:
        """Catches a curve whose outputs do not follow its inputs: a missing raise, or a season on the wrong side."""
        curve, day_mean, raise_output, stsv = self.curve_with_inputs()
        stsv.set_output_value(day_mean, 5.0)
        stsv.set_output_value(raise_output, 0.0)
        curve.i_simulate(0, stsv, False)
        assert stsv.values[curve.supply_temperature_set_channel.global_index] == pytest.approx(
            28.79073817878373, rel=1e-15
        )
        assert stsv.values[curve.heating_season_channel.global_index] == 1.0
        stsv.set_output_value(day_mean, 18.5)
        curve.i_simulate(0, stsv, False)
        assert stsv.values[curve.heating_season_channel.global_index] == 0.0

    @pytest.mark.base
    def test_it_is_frozen_under_forced_convergence(self) -> None:
        """Catches a curve that keeps moving the set point after the simulator froze the controllers of a step."""
        curve, day_mean, raise_output, stsv = self.curve_with_inputs()
        stsv.set_output_value(day_mean, 5.0)
        stsv.set_output_value(raise_output, 0.0)
        curve.i_simulate(0, stsv, False)
        stsv.set_output_value(raise_output, 2.0)
        curve.i_simulate(0, stsv, True)
        assert stsv.values[curve.supply_temperature_set_channel.global_index] == pytest.approx(
            28.79073817878373, rel=1e-15
        )


@pytest.mark.base
@pytest.mark.parametrize(
    ("outside_temperature_in_celsius", "raise_in_kelvin", "reference_temperature_in_celsius"),
    [
        (19.0, -2.0, -7.0),  # a negative raise puts the raised room below the day mean: a negative load fraction
        (5.0, math.nan, -7.0),  # a NaN raise
        (5.0, 0.0, 20.0),  # a design day as warm as the room: a division by zero
    ],
)
def test_a_curve_point_off_the_curve_is_refused(
    outside_temperature_in_celsius: float, raise_in_kelvin: float, reference_temperature_in_celsius: float
) -> None:
    """Catches a supply set point that is complex, infinite or NaN instead of a refusal that names the values."""
    with pytest.raises(ValueError, match="heating curve"):
        HeatingCurveController.supply_temperature_set_in_celsius(
            daily_average_outside_temperature_in_celsius=outside_temperature_in_celsius,
            room_set_temperature_in_celsius=20.0,
            room_set_temperature_raise_in_kelvin=raise_in_kelvin,
            heating_reference_temperature_in_celsius=reference_temperature_in_celsius,
            emitter_design=EmitterDesign.for_type(HeatDistributionSystemType.FLOORHEATING),
        )


@pytest.mark.base
def test_a_curve_whose_design_day_is_not_colder_than_the_room_is_refused_when_built() -> None:
    """Catches a curve built for a design day at the room set temperature, which would divide by zero in the run."""
    config = HeatingCurveControllerConfig(
        component_id=ComponentID(name="HeatingCurveController"),
        set_heating_threshold_outside_temperature_in_celsius=18.0,
        heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
        heating_reference_temperature_in_celsius=20.0,
        set_heating_temperature_for_building_in_celsius=20.0,
    )
    with pytest.raises(ValueError, match="design day must be colder"):
        HeatingCurveController(my_simulation_parameters=SimulationParameters.one_day_only(2021, 60), config=config)


@pytest.mark.base
@pytest.mark.parametrize(
    "field_name",
    [
        "set_heating_threshold_outside_temperature_in_celsius",
        "heating_reference_temperature_in_celsius",
        "set_heating_temperature_for_building_in_celsius",
    ],
)
@pytest.mark.parametrize("temperature_in_celsius", [float("nan"), float("inf")])
def test_a_curve_temperature_that_is_not_finite_is_refused_when_built(
    field_name: str, temperature_in_celsius: float
) -> None:
    """Catches a NaN threshold that would silently keep every day out of the heating season."""
    fields = {
        "set_heating_threshold_outside_temperature_in_celsius": 18.0,
        "heating_reference_temperature_in_celsius": -7.0,
        "set_heating_temperature_for_building_in_celsius": 20.0,
    }
    fields[field_name] = temperature_in_celsius
    config = HeatingCurveControllerConfig(
        component_id=ComponentID(name="HeatingCurveController"),
        heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
        **fields,
    )
    with pytest.raises(ValueError, match=rf"{field_name} must be a finite temperature"):
        HeatingCurveController(my_simulation_parameters=SimulationParameters.one_day_only(2021, 60), config=config)
