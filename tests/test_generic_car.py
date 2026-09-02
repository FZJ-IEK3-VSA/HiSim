"""Tests of the car's construction from configuration alone and of the driving-profile hand-off.

``Car`` used to demand a third constructor argument carrying two full-year time series, which made
it the only component a declarative energy-system file could not build. These tests pin the design
that removed it: the configuration carries the *identity* of the driving profile
(``household_name`` and ``car_name``), the occupancy publishes every profile it produced into the
per-simulation repository, and the car looks its own up in ``i_prepare_simulation``.

The failure modes get as much attention as the happy path on purpose. A car that finds no profile
and quietly simulated a vehicle that never moves would silently corrupt every electricity KPI of
the run, so the tests below check not only that the lookup raises but that the message names the
household, the car, and whichever of the two causes applied — nothing published at all (the
ordering assumption broke) or nothing under this identity (the names are wrong).
"""

# clean

import inspect
from typing import Any, Dict, List

import pytest

from hisim import loadtypes as lt
from hisim.components.generic_car import Car, CarConfig
from hisim.components.lpg_car_information import (
    CarProfileHandover,
    CarProfileKey,
    CarProfileNotPublishedError,
    GenericCarInformation,
)
from hisim.config import ComponentID, constructors_of
from hisim.sim_repository import SimRepository
from hisim.simulationparameters import SimulationParameters


class FakeOccupancy:
    """An occupancy stand-in holding a hand-written LPG car report.

    The real occupancy needs a LoadProfileGenerator run to produce car data, which is far too much
    machinery for a unit test of the hand-off. What :class:`GenericCarInformation` actually reads
    is one dictionary of result frames, so a class carrying exactly that dictionary is a faithful
    and complete substitute — and it is also what the ``CarDataProvider`` protocol declares.
    """

    def __init__(self, car_data_dict: Dict[str, Any]) -> None:
        """Stores the report this fake occupancy reports.

        Args:
            car_data_dict: The LPG car report, in the shape the real occupancy assembles.
        """
        self.car_data_dict = car_data_dict


class CarReports:
    """Builders of the LPG car reports the tests hand to :class:`FakeOccupancy`.

    Kept as one class of builders rather than as loose helpers so that the shape of an LPG car
    report — the one thing every test here depends on and none of them should restate — is written
    down exactly once.
    """

    #: Minutes of location and distance data a one-day, minute-resolution run consumes.
    MINUTES_PER_DAY: int = 1440

    @classmethod
    def report(cls, cars: List[Dict[str, str]], minutes: int = MINUTES_PER_DAY) -> Dict[str, Any]:
        """Builds a car report for the given cars, each with a trivial but valid profile.

        Args:
            cars: One mapping per car, with a ``household`` and a ``car`` display name.
            minutes: How many minutes of profile each car gets.

        Returns:
            A report in the shape ``GenericCarInformation`` reads.
        """
        locations = []
        distances = []
        for index, car in enumerate(cars):
            locations.append(
                {
                    "LoadTypeName": f"Location - {car['car']}",
                    "HouseKey": {"HouseholdName": car["household"]},
                    "TimeResolution": "00:01:00",
                    "Values": ["Home"] * (minutes - 1) + ["Workplace"],
                }
            )
            distances.append({"Values": [0.0] * (minutes - 1) + [float(1000 * (index + 1))]})
        return {"car_states": [{}], "car_locations": locations, "driving_distances": distances}

    @classmethod
    def empty(cls) -> Dict[str, Any]:
        """Builds the report a predefined-profile occupancy carries: present but entirely empty."""
        return {"car_states": [{}], "car_locations": [{}], "driving_distances": [{}]}


@pytest.mark.base
def test_the_car_constructor_takes_nothing_but_parameters_config_and_display():
    """The car is buildable the way the executor builds every component, and only that way.

    This is the whole point of the change: ``wiring.py`` calls
    ``component_class(my_simulation_parameters=…, config=…)`` and nothing else, so a third
    mandatory parameter is exactly what made this component unrecordable.
    """
    parameters = list(inspect.signature(Car.__init__).parameters)
    assert parameters == ["self", "my_simulation_parameters", "config", "my_display_config"]
    mandatory = [
        name
        for name, parameter in inspect.signature(Car.__init__).parameters.items()
        if name != "self" and parameter.default is inspect.Parameter.empty
    ]
    assert mandatory == ["my_simulation_parameters", "config"]


@pytest.mark.base
def test_for_household_is_the_configs_named_constructor_and_costs_both_fuels():
    """``for_household`` is discoverable as a constructor and reproduces the former defaults.

    The two ``get_default_*`` factories it replaced carried these exact numbers, so a difference
    here would silently change every car cost KPI in the fleet.
    """
    assert list(constructors_of(CarConfig)) == ["for_household"]

    electric = CarConfig.for_household(name="ElectricCar", household_name="CHR01", car_name="SmallCar")
    assert electric.component_id == ComponentID(name="ElectricCar")
    assert electric.household_name == "CHR01"
    assert electric.car_name == "SmallCar"
    assert electric.fuel == lt.LoadTypes.ELECTRICITY
    assert (electric.consumption_per_km, electric.device_co2_footprint_in_kg) == (0.15, 8899.4)
    assert (electric.investment_costs_in_euro, electric.maintenance_costs_in_euro_per_year) == (44498.0, 889.96)
    assert electric.lifetime_in_years == 18.0

    diesel = CarConfig.for_household(
        name="DieselCar", household_name="CHR01", car_name="SmallCar", fuel=lt.LoadTypes.DIESEL
    )
    assert (diesel.consumption_per_km, diesel.device_co2_footprint_in_kg) == (0.06, 9139.3)
    assert (diesel.investment_costs_in_euro, diesel.maintenance_costs_in_euro_per_year) == (32035.0, 640.7)


@pytest.mark.base
def test_a_car_running_on_anything_else_is_refused():
    """Only the two vehicles the cost database knows can be configured, and loudly so."""
    with pytest.raises(ValueError, match="no other vehicle is costed"):
        CarConfig.for_household(name="GasCar", household_name="CHR01", car_name="SmallCar", fuel=lt.LoadTypes.GAS)


@pytest.mark.base
def test_two_cars_of_one_household_keep_separate_profiles():
    """The profile key is the household *and* the car, so a two-car household does not collapse.

    Keying by household alone — which the old payload dictionary did — silently dropped every car
    but the last of a household, which is precisely the kind of quiet wrong answer the hand-off is
    written to make impossible.
    """
    occupancy = FakeOccupancy(
        CarReports.report([{"household": "CHR01 Couple", "car": "Small Car"}, {"household": "CHR01 Couple", "car": "Van"}])
    )
    profiles = GenericCarInformation(my_occupancy_instance=occupancy).data_dict_for_car_component
    assert set(profiles) == {
        CarProfileKey(household_name="CHR01_Couple", car_name="Small_Car"),
        CarProfileKey(household_name="CHR01_Couple", car_name="Van"),
    }


@pytest.mark.base
def test_publishing_twice_under_one_key_is_refused():
    """Two occupancies claiming one car is a modelling error, not a last-writer-wins race."""
    repository = SimRepository()
    profiles = GenericCarInformation(
        my_occupancy_instance=FakeOccupancy(CarReports.report([{"household": "CHR01", "car": "Small Car"}]))
    ).data_dict_for_car_component
    CarProfileHandover.publish(repository=repository, profiles=profiles)
    with pytest.raises(ValueError, match="Two occupancy components published"):
        CarProfileHandover.publish(repository=repository, profiles=profiles)


@pytest.mark.base
def test_a_car_prepared_before_its_occupancy_says_so():
    """With nothing published, the failure names the car and points at the component order.

    This is the ordering hazard the design introduces: preparation runs in registration order, so a
    car registered before its occupancy would look up a profile that does not exist yet.
    """
    car = Car(
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
        config=CarConfig.for_household(name="Car", household_name="CHR01", car_name="Small_Car"),
    )
    car.set_sim_repo(SimRepository())
    with pytest.raises(CarProfileNotPublishedError) as raised:
        car.i_prepare_simulation()
    message = str(raised.value)
    assert "household 'CHR01', car 'Small_Car'" in message
    assert "before its occupancy" in message


@pytest.mark.base
def test_a_car_naming_a_profile_nobody_published_lists_the_ones_that_exist():
    """A misspelled household or car name is reported together with what was actually published."""
    repository = SimRepository()
    CarProfileHandover.publish(
        repository=repository,
        profiles=GenericCarInformation(
            my_occupancy_instance=FakeOccupancy(CarReports.report([{"household": "CHR01", "car": "Small Car"}]))
        ).data_dict_for_car_component,
    )
    car = Car(
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
        config=CarConfig.for_household(name="Car", household_name="CHR99", car_name="Truck"),
    )
    car.set_sim_repo(repository)
    with pytest.raises(CarProfileNotPublishedError) as raised:
        car.i_prepare_simulation()
    message = str(raised.value)
    assert "household 'CHR99', car 'Truck'" in message
    assert "household 'CHR01', car 'Small_Car'" in message


@pytest.mark.base
def test_a_car_registered_with_a_simulator_finds_the_published_profile():
    """The whole hand-off end to end: publish, prepare, and the car holds its own series."""
    repository = SimRepository()
    CarProfileHandover.publish(
        repository=repository,
        profiles=GenericCarInformation(
            my_occupancy_instance=FakeOccupancy(
                CarReports.report([{"household": "CHR01", "car": "Small Car"}, {"household": "CHR02", "car": "Van"}])
            )
        ).data_dict_for_car_component,
    )
    car = Car(
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
        config=CarConfig.for_household(name="VanOfCHR02", household_name="CHR02", car_name="Van"),
    )
    car.set_sim_repo(repository)
    car.i_prepare_simulation()
    assert car.car_information_dict["car_name"] == "Van"
    assert len(car.meters_driven) == CarReports.MINUTES_PER_DAY
    # The second car drives 2000 m in the last minute of the LPG day; the profile is shifted from
    # the LPG's local time to UTC, so the total is what survives that shift, never nothing at all.
    assert sum(car.meters_driven) > 0


@pytest.mark.base
def test_an_occupancy_without_car_data_publishes_nothing_and_the_car_complains_instead():
    """A predefined-profile household owns no car; the occupancy stays silent, the car does not.

    Splitting the complaint this way is deliberate. An occupancy with no cars is entirely legal,
    so it must not raise on behalf of cars nobody asked for; a car that cannot find itself must.
    """
    assert GenericCarInformation.has_car_data(CarReports.empty()) is False
    with pytest.raises(ValueError, match="only empty dictionaries"):
        GenericCarInformation(my_occupancy_instance=FakeOccupancy(CarReports.empty()))
