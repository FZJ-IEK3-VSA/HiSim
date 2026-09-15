"""The per-car driving profiles the LoadProfileGenerator produces, and their hand-off.

A car in HiSim is driven by two full time series — where it is and how far it moved in every
minute of the year — that only exist once the LoadProfileGenerator has actually run for a
household. They are a *simulation result*, not a catalogue entry: no configuration file can carry
them, and no sizing law can derive them. This module is the one place that turns the occupancy's
raw LPG car report into one payload per car, and the one place that hands those payloads from the
occupancy component to the car components through the simulation repository.

The hand-off exists because :class:`~hisim.components.generic_car.Car` must be constructible from
``(my_simulation_parameters, config)`` like every other component, which rules out passing the
profile as a constructor argument. Instead the occupancy publishes every profile it produced
during its own ``i_prepare_simulation``, and each car looks its own profile up in the same phase,
identified by the household and car names its configuration carries. CLAUDE.md names exactly this
— "car location" — as the state the repository exists for.

The module is deliberately a leaf that neither the occupancy nor the car owns: it imports
:mod:`hisim.sim_repository` and nothing else from HiSim, so that
``loadprofilegenerator_utsp_connector`` (the publisher) and ``generic_car`` (the consumer) can both
import it without the import cycle that keeping :class:`GenericCarInformation` in ``generic_car``
would create the moment the occupancy needed it.
"""

# clean

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Protocol, Tuple, Union

from hisim.sim_repository import SimRepository

__authors__ = "Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class CarDataProvider(Protocol):
    """The single attribute :class:`GenericCarInformation` needs from an occupancy component.

    Typing the source structurally rather than as ``UtspLpgConnector`` is what keeps this module a
    leaf: the occupancy imports it in order to publish, so a nominal dependency the other way
    would be a cycle. It also states honestly how little of the occupancy is actually read — one
    dictionary of LPG result frames, nothing else.
    """

    #: The occupancy's raw LPG car report: ``car_states``, ``car_locations`` and
    #: ``driving_distances``, each a list with one entry per car of the simulated households.
    car_data_dict: Dict[str, Any]


@dataclass(frozen=True)
class CarProfileKey:
    """Which car of which household a published driving profile belongs to.

    The key is the pair rather than the household alone because a household may own several cars:
    keying by household would silently collapse them onto one profile, which is exactly the kind
    of quiet wrong answer this whole hand-off is written to avoid. It is frozen so it can be a
    dictionary key and so a published profile cannot be re-labelled after the fact.
    """

    #: Name of the LPG household the car belongs to, with spaces and punctuation normalised the
    #: way the occupancy's report normalises them.
    household_name: str

    #: Name of the car itself, normalised the same way; unique within its household.
    car_name: str

    def describe(self) -> str:
        """Renders the key the way an error message names a car the reader has to go and find."""
        return f"household '{self.household_name}', car '{self.car_name}'"


class CarProfileNotPublishedError(ValueError):
    """Raised when a car cannot find the driving profile its configuration names.

    A dedicated type rather than a bare ``ValueError`` because the two ways to get here are worth
    telling apart in a test: no occupancy published anything at all (the car was registered before
    its occupancy, or the occupancy has no car data because it reads a predefined profile), or an
    occupancy published profiles but none under this car's identity (a misspelled household or car
    name in the configuration). A car that silently simulated a parked vehicle instead would
    corrupt every electricity KPI of the run without a single warning.
    """


class GenericCarInformation:
    """Turns an occupancy's raw LPG car report into one ready-to-use payload per car.

    The LoadProfileGenerator reports car locations and driving distances as parallel lists of
    result frames, each carrying its own household key, load-type name and time resolution. This
    class reads those lists once and rearranges them into a mapping from :class:`CarProfileKey` to
    the five values a car needs: its own name, its household's name, the profile's time
    resolution, the location series and the driven-metres series.

    It is used in two places and they want the same thing for different reasons. The occupancy
    builds one in order to publish every profile it holds; a setup building N cars in a loop
    builds one in order to learn what N is, because the number of cars is a property of the LPG
    result and never something a configuration could state.
    """

    #: What :meth:`normalise` strips out of an LPG display name. Held here rather than rebuilt at
    #: every call so that the spelling a configuration must use is written down exactly once.
    NAME_REPLACEMENTS: ClassVar[Dict[str, Union[str, int, None]]] = {" ": "_", ",": "", "/": "", ".": ""}

    def __init__(self, my_occupancy_instance: CarDataProvider) -> None:
        """Reads the occupancy's car report and prepares one payload per car.

        Args:
            my_occupancy_instance: The occupancy component holding the LPG car report.

        Raises:
            ValueError: If the report holds no car data at all; see :meth:`build`.
        """
        self.my_occupancy_instance = my_occupancy_instance
        self.build(my_occupancy_instance=my_occupancy_instance)
        self.data_dict_for_car_component = self.prepare_data_dict_for_car_component(
            car_names=self.car_names,
            household_names=self.household_names,
            time_resolutions=self.time_resolutions,
            car_locations=self.car_location_value_list,
            driven_meters=self.driven_meters,
        )

    @staticmethod
    def has_car_data(car_data_dict: Mapping[str, Any]) -> bool:
        """Reports whether an occupancy's car report holds anything at all.

        The predefined-profile mode produces an occupancy with no car data whatsoever, which is a
        perfectly legal household that simply owns no simulated car. The publisher asks this
        before building the information, so that an occupancy without cars stays silent instead of
        raising on behalf of cars nobody asked for.

        Args:
            car_data_dict: The occupancy's raw LPG car report.

        Returns:
            True if at least one non-empty car entry is present.
        """
        return not all(
            isinstance(value_list, list) and all(not bool(car_info_dict) for car_info_dict in value_list)
            for value_list in car_data_dict.values()
        )

    def build(self, my_occupancy_instance: CarDataProvider) -> None:
        """Extracts the names, resolutions and series from the occupancy's car report.

        Args:
            my_occupancy_instance: The occupancy component holding the LPG car report.

        Raises:
            ValueError: If the report holds only empty entries, which means the occupancy never
                produced car data — the message lists the reasons that cause it.
        """
        car_data_dict = my_occupancy_instance.car_data_dict
        if not self.has_car_data(car_data_dict):
            raise ValueError(
                "The car data from occupancy contains only empty dictionaries in its value lists. "
                "This is likely caused by one of the following reasons: "
                "(1) You are using the predefined occupancy profile, no car data is currently available. "
                "(2) The UTSP request failed (e.g., due to missing or incorrect UTSP configuration). "
                "(3) USE_LOCAL_LPG is currently not supported on macOS (darwin). "
                "Please switch to USE_UTSP with a reachable UTSP endpoint with correct UTSP configuration."
            )

        (
            self.car_names,
            self.household_names,
            self.time_resolutions,
            self.car_location_value_list,
        ) = self.get_important_parameters_from_occupancy_car_data(car_data_dict=car_data_dict)

        self.driven_meters = self.get_meters_driven_from_occupancy_car_data(car_data_dict=car_data_dict)

    def get_important_parameters_from_occupancy_car_data(
        self, car_data_dict: Mapping[str, Any]
    ) -> Tuple[List, List, List, List]:
        """Reads car names, household names, time resolutions and location series, in car order.

        Args:
            car_data_dict: The occupancy's raw LPG car report.

        Returns:
            Four parallel lists — car names, household names, time resolutions and location value
            series — with one entry per car, in the order the report lists them.
        """
        car_location_list = car_data_dict["car_locations"]

        car_names = []
        household_names = []
        time_resolutions = []
        car_location_value_list = []
        for car_location in car_location_list:
            car_names.append(self.normalise(car_location["LoadTypeName"].split(" - ")[1]))
            household_names.append(self.normalise(car_location["HouseKey"]["HouseholdName"]))
            time_resolutions.append(car_location["TimeResolution"])
            car_location_value_list.append(car_location["Values"])

        return car_names, household_names, time_resolutions, car_location_value_list

    @staticmethod
    def normalise(lpg_name: str) -> str:
        """Turns an LPG display name into the identifier a configuration and a component name use.

        The LoadProfileGenerator spells its households and cars with spaces, commas, slashes and
        full stops, none of which survive a component name or a YAML key comfortably. Stripping
        them here, once, is what lets a car's configuration name its household in exactly the
        spelling the published key uses.

        Args:
            lpg_name: The display name as the LoadProfileGenerator wrote it.

        Returns:
            The same name with spaces turned into underscores and punctuation removed.
        """
        return lpg_name.translate(str.maketrans(GenericCarInformation.NAME_REPLACEMENTS))

    def get_meters_driven_from_occupancy_car_data(self, car_data_dict: Mapping[str, Any]) -> List:
        """Reads the driven-metres series of every car, in the same order as the other lists.

        Args:
            car_data_dict: The occupancy's raw LPG car report.

        Returns:
            One value series per car.
        """
        driven_distances_list = car_data_dict["driving_distances"]
        return [driven_distance_data["Values"] for driven_distance_data in driven_distances_list]

    def prepare_data_dict_for_car_component(
        self, car_names: List, household_names: List, time_resolutions: List, car_locations: List, driven_meters: List
    ) -> Dict[CarProfileKey, Dict[str, Any]]:
        """Rearranges the parallel lists into one payload per car, keyed by household and car.

        Args:
            car_names: Normalised car name of every car.
            household_names: Normalised household name of every car.
            time_resolutions: Time resolution of every car's profile, as an ``%H:%M:%S`` string.
            car_locations: Location value series of every car.
            driven_meters: Driven-metres value series of every car.

        Returns:
            The payloads, keyed by :class:`CarProfileKey`.
        """
        data_dict_for_car_component: Dict[CarProfileKey, Dict[str, Any]] = {}
        for index, household_name in enumerate(household_names):
            key = CarProfileKey(household_name=household_name, car_name=car_names[index])
            data_dict_for_car_component[key] = {
                "car_name": car_names[index],
                "household_name": household_name,
                "time_resolution": time_resolutions[index],
                "car_location": car_locations[index],
                "driven_meters": driven_meters[index],
            }
        return data_dict_for_car_component


class CarProfileHandover:
    """Publishes the LPG driving profiles into the simulation repository, and reads them back.

    Both halves live together because the failure message of the lookup is only useful if it can
    say what *was* published, and that in turn only holds if there is exactly one repository entry
    that every occupancy of the run merges into. Keeping the key and the two operations in one
    class is what makes that single entry impossible to spell two ways.

    The repository is the per-simulation :class:`~hisim.sim_repository.SimRepository` rather than
    the process-wide singleton on purpose: a profile belongs to one run, and a stale profile
    surviving into the next simulation of the same interpreter — the recorder builds a system
    twice in one process — would be far harder to notice than a missing one.
    """

    #: The single repository entry every occupancy merges its profiles into. One entry rather than
    #: one per car so that a car that cannot find itself can list every car that does exist.
    REPOSITORY_KEY: ClassVar[str] = "lpg_car_driving_profiles"

    @classmethod
    def publish(cls, repository: SimRepository, profiles: Mapping[CarProfileKey, Dict[str, Any]]) -> None:
        """Merges one occupancy's profiles into the run's single profile entry.

        Merging rather than overwriting matters as soon as a system has two occupancies, each with
        its own households; a duplicate key means two occupancies claim the same car, which is a
        modelling error rather than something to resolve by last-writer-wins.

        Args:
            repository: The simulation repository of the run.
            profiles: The profiles this occupancy produced, keyed by household and car.

        Raises:
            ValueError: If a key is already published by another occupancy.
        """
        published: Dict[CarProfileKey, Dict[str, Any]] = (
            repository.get_entry(cls.REPOSITORY_KEY) if repository.entry_exists(cls.REPOSITORY_KEY) else {}
        )
        for key, payload in profiles.items():
            if key in published:
                raise ValueError(
                    f"Two occupancy components published a driving profile for {key.describe()}. "
                    "A car profile belongs to exactly one occupancy; rename the household or the "
                    "car so that the two can be told apart."
                )
            published[key] = payload
        repository.set_entry(cls.REPOSITORY_KEY, published)

    @classmethod
    def lookup(cls, repository: Optional[SimRepository], household_name: str, car_name: str) -> Dict[str, Any]:
        """Finds one car's driving profile, or explains loudly why it is not there.

        Args:
            repository: The simulation repository of the run, or None when the car was never
                registered with a simulator at all.
            household_name: The household the car's configuration names.
            car_name: The car the configuration names.

        Returns:
            The payload the occupancy published for that car.

        Raises:
            CarProfileNotPublishedError: If nothing was published yet, or nothing under this
                identity. The message names the car, says which of the two happened and lists the
                profiles that do exist.
        """
        wanted = CarProfileKey(household_name=household_name, car_name=car_name)
        if repository is None or not repository.entry_exists(cls.REPOSITORY_KEY):
            raise CarProfileNotPublishedError(
                f"No LoadProfileGenerator driving profile is available for {wanted.describe()}: "
                "no occupancy component has published any. Either the system has no occupancy "
                "reading car data (a predefined profile carries none — use USE_UTSP or "
                "USE_LOCAL_LPG), or the car was registered before its occupancy and is therefore "
                "prepared first. The occupancy must come before every car in the component order."
            )
        published: Dict[CarProfileKey, Dict[str, Any]] = repository.get_entry(cls.REPOSITORY_KEY)
        if wanted not in published:
            raise CarProfileNotPublishedError(
                f"No LoadProfileGenerator driving profile was published for {wanted.describe()}. "
                f"The occupancy published {len(published)} profile(s): {cls.render(published)}. "
                "Correct the household_name and car_name of the car's configuration to name one "
                "of them."
            )
        return published[wanted]

    @staticmethod
    def render(published: Mapping[CarProfileKey, Dict[str, Any]]) -> str:
        """Renders the published keys for an error message, in the order they were published.

        Args:
            published: The repository entry's contents.

        Returns:
            A readable, comma-separated list of the keys, or a phrase saying there are none.
        """
        if not published:
            return "(none)"
        return ", ".join(key.describe() for key in published)
