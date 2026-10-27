"""Contains a component that uses the UTSP to provide LoadProfileGenerator data."""

import datetime
import io
import json
import os
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, NamedTuple, Optional, Tuple, Union, Set
import copy
import enum
import pandas as pd
from dataclasses_json import dataclass_json

from utspclient import client, datastructures, result_file_filters
from utspclient.helpers import lpg_helper
from utspclient.helpers.lpgpythonbindings import HouseCreationAndCalculationJob
from utspclient.datastructures import TimeSeriesRequest
from utspclient.client import calculate_multiple_requests
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
    HouseTypes,
    LoadTypes,
    TransportationDeviceSets,
    TravelRouteSets,
    EnergyIntensityType,
)
from utspclient.helpers.lpgpythonbindings import CalcOption, JsonReference

from pylpg import lpg_execution

from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.components.pylpg_workspace import PylpgWorkspace
# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log, utils
from hisim.components import lpg_local_time
from hisim.caching import CacheEntry
from hisim.components.configuration import HouseholdWarmWaterDemandConfig, PhysicsConfig
from hisim.simulationparameters import SimulationParameters
from hisim.component import OpexCostDataClass
from hisim.config import ConfigBase, ComponentID, DisplayConfig, FactContribution, constructor, preset
from hisim.components.lpg_car_information import CarProfileHandover, GenericCarInformation
from hisim.economics.facts import CostRelevance
from hisim.energy_port import EnergyPort

# Constants for warm water fallback values used in i_simulate
DEFAULT_WW_TEMPERATURE_INPUT: float = 40.45  # °C - default warm water temperature fallback
DEFAULT_WW_MASS_INPUT: float = 9.3  # kg/s - default warm water mass input fallback


class LpgDataAcquisitionMode(enum.Enum):
    """Set LPG Data Acquisition Mode."""

    USE_PREDEFINED_PROFILE = "use_predefined_profile"
    USE_UTSP = "use_utsp"
    USE_LOCAL_LPG = "use_local_lpg"


@dataclass_json
@dataclass
class UtspLpgConnectorConfig(ConfigBase):
    """Config class for UtspLpgConnector. Contains LPG parameters and UTSP connection parameters.

    The one preset is :meth:`preset_couple_both_at_work`, the household every example and every
    fixture of this repository runs; any other household of the LoadProfileGenerator catalogue
    comes from :meth:`for_household`.
    """

    MAIN_CLASS = "hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector"

    component_id: ComponentID
    #: Where the profile comes from: the shipped predefined one, a local LoadProfileGenerator or
    #: the UTSP.
    data_acquisition_mode: LpgDataAcquisitionMode
    #: The catalogue household to simulate, or one reference per apartment.
    household: Union[JsonReference, List[JsonReference]]
    energy_intensity: EnergyIntensityType
    travel_route_set: Optional[JsonReference]
    transportation_device_set: Optional[JsonReference]
    charging_station_set: Optional[JsonReference]
    profile_with_washing_machine_and_dishwasher: bool
    predictive_control: bool
    #: Where the result file is written. Not part of the cache key; see
    #: :meth:`_clear_non_key_fields`.
    result_dir_path: str
    cache_dir_path: Optional[str] = None
    name_of_predefined_loadprofile: Optional[str] = "CHR01 Couple both at Work"
    predefined_loadprofile_filepaths: Optional[str] = None
    guid: str = ""
    calculation_index_for_local_lpg: Optional[int] = None
    cars: Optional[List[str]] = None

    def identity(self) -> str:
        """Return a string that says which occupancy this is: everything that decides the LoadProfileGenerator run.

        The car component stores this string in its configuration, sized from the occupancy through the
        sizing engine, so that its cache key includes which occupancy produced its driving profile. The
        household name alone is not enough: the energy intensity, the travel-route and device sets, the
        appliance flag, the predefined profile (name and file) and the request guid all change the
        profile. The local LPG's ``random_seed`` is deliberately not part of it, because it is wired
        nowhere (both call sites pass ``None``); if it ever becomes configurable it must join this list.
        ``calculation_index_for_local_lpg`` is deliberately not part of it either: it selects the
        scratch directory a profile is computed in, not the profile's content, and parallel drivers
        vary it per worker slot.

        Catalogue references are named by their ``Name``, which is unique within the generator's catalogue
        and readable; the string is written into every recorded energy-system file.

        Returns:
            str: the acquisition mode, household names, energy intensity, the three sets, the appliance flag,
            the predefined profile name, the predefined profile file and the request guid, separated by ``/``.
        """
        households = self.household if isinstance(self.household, list) else [self.household]
        parts = [
            str(self.data_acquisition_mode.value),
            "+".join(self._reference_name(household) for household in households),
            str(self.energy_intensity.value),
            self._reference_name(self.travel_route_set),
            self._reference_name(self.transportation_device_set),
            self._reference_name(self.charging_station_set),
            "with-appliances" if self.profile_with_washing_machine_and_dishwasher else "no-appliances",
            str(self.name_of_predefined_loadprofile) if self.name_of_predefined_loadprofile else "-",
            os.path.basename(str(self.predefined_loadprofile_filepaths)) if self.predefined_loadprofile_filepaths else "-",
            self.guid or "-",
        ]
        return "/".join(parts)

    @staticmethod
    def _reference_name(reference: Any) -> str:
        """Return the ``Name`` of a catalogue reference, or ``-`` if the reference is ``None``.

        Args:
            reference: a ``JsonReference`` or ``None``.

        Returns:
            str: the name.
        """
        return "-" if reference is None else str(getattr(reference, "Name", reference))

    @staticmethod
    def identity_facts(config: "UtspLpgConnectorConfig", ctx: Any) -> Dict[str, Any]:
        """Provide :meth:`identity` as the sizing fact ``occupancy_identity``.

        Registered in :attr:`SIZING_CONTRIBUTIONS`; the sizing engine calls it with the resolved config.

        Args:
            config: this connector configuration.
            ctx: the sizing context; unused.

        Returns:
            Dict[str, Any]: ``{"occupancy_identity": config.identity()}``.
        """
        del ctx
        return {"occupancy_identity": config.identity()}

    #: A scenario normally has one occupancy, so the bare fact ``occupancy_identity`` binds
    #: without the car naming a source; a scenario with several names the one its car belongs to
    #: in the car's ``sizing_sources``, which is exactly the case the engine refuses to guess.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(facts=("occupancy_identity",), compute=identity_facts),
    )

    @preset(note="CHR01, a couple both at work, on the shipped predefined profile")
    @classmethod
    def preset_couple_both_at_work(cls, name: str) -> "UtspLpgConnectorConfig":
        """The reference household of this repository: CHR01, a couple both at work.

        Every example household and every occupancy fixture in HiSim runs this profile, and it
        is shipped with the repository as a predefined result so that a simulation needs neither
        a running UTSP server nor a local LoadProfileGenerator installation. Any other household
        of the LoadProfileGenerator catalogue is an identifier rather than a variant, which is
        why this class ships this one preset and :meth:`for_household` for everything else.
        """
        return cls.for_household(name, household=Households.CHR01_Couple_both_at_Work)

    @classmethod
    def predefined_households(cls) -> Tuple[str, ...]:
        """Names the LoadProfileGenerator households whose profiles ship with the repository.

        The predefined mode reads a precomputed profile out of ``hisim/inputs/loadprofiles``
        instead of asking the LoadProfileGenerator for one, which is what lets the examples and
        the test suite run without a UTSP server and without a local installation. Only a
        household whose profile is actually shipped can be simulated that way; every other
        household of the catalogue has to be computed by one of the two other modes.

        Returns:
            The names, sorted, of the catalogue households the predefined mode can serve.
        """
        catalogue = {
            reference.Name
            for reference in vars(Households).values()
            if isinstance(reference, JsonReference) and reference.Name is not None
        }
        return tuple(sorted(catalogue & set(utils.HISIMPATH["occupancy"])))

    @classmethod
    def predefined_profile_of(
        cls,
        household: Union[JsonReference, List[JsonReference]],
        data_acquisition_mode: LpgDataAcquisitionMode,
    ) -> Optional[str]:
        """Returns the predefined profile that serves one household, or ``None``.

        Only the predefined mode reads a profile by name; the two computing modes hand the
        household references to the LoadProfileGenerator itself and leave the name empty. The
        check exists because the name, not the household, decides what the predefined mode
        actually simulates: without it, asking for a household whose profile is not shipped
        would quietly produce the profile of a different household.

        Args:
            household: The catalogue household, or one reference per apartment.
            data_acquisition_mode: Where the profile comes from.

        Returns:
            The name of the profile to read, or ``None`` when the LoadProfileGenerator computes
            the household.

        Raises:
            ValueError: If the predefined mode cannot serve the request, because several
                households were given or because no profile for this one is shipped.
        """
        if data_acquisition_mode is not LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE:
            return None
        computing_modes = ", ".join(
            mode.value
            for mode in LpgDataAcquisitionMode
            if mode is not LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE
        )
        if isinstance(household, list):
            raise ValueError(
                f"{len(household)} households were given, and only the LoadProfileGenerator itself "
                f"can simulate more than one; pass data_acquisition_mode as one of "
                f"{computing_modes}."
            )
        shipped = cls.predefined_households()
        if household.Name not in shipped:
            raise ValueError(
                f"no predefined profile for the household '{household.Name}' ships with this "
                f"repository, and the predefined mode would therefore simulate a different "
                f"household; shipped households: {', '.join(shipped)}. Pass "
                f"data_acquisition_mode as one of {computing_modes} to have the "
                f"LoadProfileGenerator compute this household."
            )
        return household.Name

    def _clear_non_key_fields(self, view: "UtspLpgConnectorConfig") -> None:
        """Clear the fields that only place the run from the copy hashed into the occupancy cache key.

        ``result_dir_path`` (where the result file is written), ``cache_dir_path`` (where the entry goes) and
        ``calculation_index_for_local_lpg`` (which working directory the generator used) do not change the
        profile. The first is an absolute path that differs between machines, which is why an occupancy entry
        computed on one machine was never found on another. Every other field stays key material, including
        ``predefined_loadprofile_filepaths``: an external profile directory decides the result, and it is
        machine-specific by nature, so an entry computed from one is not portable.

        Args:
            view: the copy to adjust.
        """
        view.result_dir_path = ""
        view.cache_dir_path = None
        view.calculation_index_for_local_lpg = None

    @constructor(note="the household catalogue of the LoadProfileGenerator")
    @classmethod
    def for_household(
        cls,
        name: str,
        household: Union[JsonReference, List[JsonReference]],
        data_acquisition_mode: LpgDataAcquisitionMode = LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE,
        energy_intensity: EnergyIntensityType = EnergyIntensityType.EnergySaving,
        travel_route_set: Optional[JsonReference] = None,
        transportation_device_set: Optional[JsonReference] = None,
        charging_station_set: Optional[JsonReference] = None,
    ) -> "UtspLpgConnectorConfig":
        """Builds an occupancy reading one household — or several — of the LPG catalogue.

        The LoadProfileGenerator's households are a catalogue of hundreds of references, and a
        multi-apartment building passes a list of them for one occupancy component, so the
        household is an identifier space rather than a set of variants. The mobility references
        default to the ones every HiSim setup uses, because they only matter once a car or a
        charging station is part of the system and are then usually left as they are.

        The household is only honoured as far as the chosen mode can honour it. In the default
        predefined mode the profile is read from disk by name, so the household must be one of
        the shipped ones (:meth:`predefined_households`) and anything else is refused rather
        than silently replaced; the two computing modes pass the references to the
        LoadProfileGenerator and accept the whole catalogue, a list of apartments included.

        Args:
            name: Instance name of the component being configured; it becomes its identity.
            household: The catalogue household to simulate, or one reference per apartment.
            data_acquisition_mode: Where the profile comes from — the shipped predefined
                profile, a local LoadProfileGenerator, or the UTSP.
            energy_intensity: How economically the residents are assumed to behave.
            travel_route_set: Commuting distances of the residents; the ten-kilometre set when
                omitted.
            transportation_device_set: Vehicles available to the residents; a bus and one small
                car when omitted.
            charging_station_set: Charging equipment at home; the 11 kW home charger when
                omitted.

        Returns:
            A configuration simulating that household in the requested mode.

        Raises:
            ValueError: If the predefined mode cannot serve the requested household.
        """
        return cls(
            component_id=ComponentID(name=name),
            data_acquisition_mode=data_acquisition_mode,
            name_of_predefined_loadprofile=cls.predefined_profile_of(household, data_acquisition_mode),
            household=household,
            result_dir_path=utils.HISIMPATH["utsp_results"],
            energy_intensity=energy_intensity,
            travel_route_set=(
                travel_route_set
                if travel_route_set is not None
                else TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance
            ),
            transportation_device_set=(
                transportation_device_set
                if transportation_device_set is not None
                else TransportationDeviceSets.Bus_and_one_30_km_h_Car
            ),
            charging_station_set=(
                charging_station_set
                if charging_station_set is not None
                else ChargingStationSets.Charging_At_Home_with_11_kW
            ),
            profile_with_washing_machine_and_dishwasher=True,
            predictive_control=False,
        )


@dataclass(frozen=True)
class LpgResultFiles:
    """The result files of one household that the occupancy reads, as the profile source hands them over.

    The UTSP hands over each file's content, a local LoadProfileGenerator and the shipped profile hand over each
    file's path; the acquisition mode says which. A file the source did not produce is an empty string: the shipped
    profile has no flexibility and no car files, and a household without a car has no car files either.
    """

    #: The electricity sum profile, ``SumProfiles.HH1.Electricity.csv`` (or its ``NoFlex`` variant).
    electricity: str
    #: The warm-water sum profile, ``SumProfiles.HH1.Warm Water.csv``.
    warm_water: str
    #: The sum profile of the devices' heat gains, ``SumProfiles.HH1.Inner Device Heat Gains.csv``.
    inner_device_heat_gains: str
    #: The persons at a high activity level per minute, ``BodilyActivityLevel.High.HH1.json``.
    high_activity: str
    #: The persons at a low activity level per minute, ``BodilyActivityLevel.Low.HH1.json``.
    low_activity: str
    #: The flexibility events, or an empty string.
    flexibility: str = ""
    #: The car's state per minute, or an empty string.
    car_states: str = ""
    #: The car's location per minute, or an empty string.
    car_locations: str = ""
    #: The car's driven distance per minute, or an empty string.
    driving_distances: str = ""


@dataclass(frozen=True)
class LpgMinuteProfile:
    """One household's profile as the LoadProfileGenerator wrote it: one value per wall-clock minute it simulated.

    This is what the occupancy caches for a household it had the LoadProfileGenerator compute: every series it reads
    from the result files, before anything of the run is applied to it. Putting the minutes onto the run's
    standard-time clock, cutting out the run's window and averaging them to the run's timestep happen when the
    profile is read (:meth:`UtspLpgConnector.series_of_the_run`), so one entry serves every window and every
    timestep of the year it was computed for. For example, the local generator's profile of CHR01 for any run in
    2021 has 527 040 minutes, from 1 January 2021 00:00 to 1 January 2022 23:59 on the wall clock.

    The series hold the values exactly as read, as plain lists, so that a JSON round trip gives the same values
    bit for bit (Python writes every float in its shortest exact form).
    """

    #: The wall-clock minute the profile starts at, ``StartTime`` of its activity files, ISO 8601 without a time zone.
    start_time: str
    #: The number of residents at a high activity level (awake) in each minute.
    persons_at_high_activity: List[float]
    #: The number of residents at a low activity level (asleep) in each minute.
    persons_at_low_activity: List[float]
    #: The household's electricity consumption in each minute, in kWh.
    electricity_per_minute_in_kwh: List[float]
    #: The household's warm-water draw in each minute, in l.
    warm_water_per_minute_in_liter: List[float]
    #: The heat the devices release into the building in each minute, in kWh.
    inner_device_heat_gains_per_minute_in_kwh: List[float]
    #: The flexibility events, the list the generator's JSON file holds; an empty dictionary if it wrote none.
    flexibility: Union[Dict[str, Any], List[Any]]
    #: The car's state per minute, the object the generator's JSON file holds; empty without a car.
    car_states: Union[Dict[str, Any], List[Any]]
    #: The car's location per minute, the object the generator's JSON file holds; empty without a car.
    car_locations: Union[Dict[str, Any], List[Any]]
    #: The car's driven distance per minute, the object the generator's JSON file holds; empty without a car.
    driving_distances: Union[Dict[str, Any], List[Any]]


class OccupancySeries(NamedTuple):
    """The five series an occupancy publishes, one value per timestep of the run."""

    #: The electrical power the household draws, in W.
    electricity_consumption: List[float]
    #: The heat the devices release into the building, in W.
    heating_by_devices: List[float]
    #: The warm water drawn in each timestep, in l.
    water_consumption: List[float]
    #: The residents' body heat, in W.
    heating_by_residents: List[float]
    #: The number of residents at home.
    number_of_residents: List[float]


class UtspLpgConnector(cp.DataSourceComponent):
    """Component that provides data from the LoadProfileGenerator.

    This component provides the heating generated, the electricity and water consumed
    by the residents. Furthermore, transportation and device flexibility data is stored
    in separate files, configurable via the config object.
    The data is retrieved from the UTSP, which executes the LoadProfileGenerator to simulate
    the specified household.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    WW_MassInput: str = "WarmWaterMassInput"  # kg/s
    WW_TemperatureInput: str = "WarmWaterTemperatureInput"  # °C

    # Outputs
    WW_MassOutput: str = "MassOutput"  # kg/s
    WW_TemperatureOutput: str = "TemperatureOutput"  # °C
    EnergyDischarged: str = "EnergyDischarged"  # W
    DemandSatisfied: str = "DemandSatisfied"  # 0 or 1

    NumberOfResidents: str = "NumberOfResidents"
    HeatingByResidents: str = "HeatingByResidents"
    HeatingByDevices: str = "HeatingByDevices"
    ElectricalPowerConsumption: str = "ElectricalPowerConsumption"
    ElectricalEnergyConsumption: str = "ElectricalEnergyConsumption"
    WaterConsumption: str = "WaterConsumption"
    #: The residents' body heat, released into the building, in W; equal to ``HeatingByResidents``.
    BodyHeatToBuildingInWatt: str = "BodyHeatToBuildingInWatt"
    #: The appliances' electricity that does not heat the building, in W: consumption minus device heat gains.
    ApplianceElectricityNotReleasedIndoorsInWatt: str = "ApplianceElectricityNotReleasedIndoorsInWatt"

    Electricity_Demand_Forecast_24h: str = "Electricity_Demand_Forecast_24h"

    #: How a request names its first and last day to the LoadProfileGenerator: ISO 8601, which it reads
    #: unambiguously. A day-first ``01-07-2021`` is read month-first, as 7 January.
    LPG_DATE_FORMAT: ClassVar[str] = "%Y-%m-%d"

    #: The resolution a request asks the LoadProfileGenerator to write its result files in, ``h:mm:ss``: one
    #: minute, its own resolution, at which it writes no files beside the one-minute files the occupancy reads.
    #: The occupancy averages the minutes to the run's timestep itself (:meth:`series_of_the_run`). The request
    #: used to ask for the run's timestep, so a 900 s run had the generator write ``SumProfiles_900s.*`` files
    #: that nothing read, and a request that differed from that of a 60 s run.
    LPG_EXTERNAL_TIME_RESOLUTION: ClassVar[str] = "0:01:00"

    #: What an occupancy cache entry holds: the minute profiles of one request (:class:`LpgMinuteProfile`), as a
    #: JSON list. Part of the entry's key (:meth:`minute_profile_key_material`), so that entries of another layout,
    #: such as the windowed and averaged series cached before, are never read as one.
    MINUTE_PROFILE_CACHE_CONTENT: ClassVar[str] = "lpg_minute_profiles"

    #: The heat one resident gives off, in W: at a high activity level (awake) and at a low one (asleep).
    BODY_HEAT_PER_PERSON_IN_WATT: ClassVar[Tuple[float, float]] = (150, 100)

    @classmethod
    def electricity_consumption_of(
        cls,
        config: UtspLpgConnectorConfig,
        my_simulation_parameters: SimulationParameters,
    ) -> Tuple[float, ...]:
        """Return one household's electricity consumption in watt per timestep, without a simulation.

        A caller that has to know how much electricity a household uses before any simulation
        exists -- the RenoVisor translation layer sizes a battery from it, decision Q12 of
        ``roadmap/renovisor/challenges.md`` -- would otherwise have to reach into this component's
        attributes, or run a whole system to learn one number about one household. This is the
        supported way to ask.

        The profile is obtained through the component's own loading path, with the configuration
        and the simulation parameters the run itself will use, so the cache entry it warms is the
        one the run then finds: asking costs the profile once rather than twice. Which source the
        profile comes from is the configuration's, exactly as in a run.

        Example::

            watts = UtspLpgConnector.electricity_consumption_of(config, parameters)
            kilowatt_hours = sum(watts) * parameters.seconds_per_timestep / 3_600_000

        Args:
            config: The occupancy configuration, as the run's own energy-system file builds it.
            my_simulation_parameters: The parameters of the run, which decide the period and the
                resolution the profile is loaded for.

        Returns:
            The electrical power of the household, in watt, one value per timestep of the period.

        Raises:
            ValueError: If the configured profile source cannot serve the requested household;
                there is no fallback to a different household here either (see :meth:`build`).
        """
        connector = cls(my_simulation_parameters=my_simulation_parameters, config=config)
        return tuple(float(value) for value in connector.electricity_consumption)

    # Similar components to connect to:
    # None
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: UtspLpgConnectorConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Initializes the component and retrieves the LPG data."""
        self.utsp_config: UtspLpgConnectorConfig = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.name_of_predefined_loadprofile: Optional[str] = config.name_of_predefined_loadprofile
        self.predefined_loadprofile_filepaths: Optional[str] = config.predefined_loadprofile_filepaths

        # The base index decides which C<index> directories (below the cache) this process computes in. It used
        # to default to the constant 1, so every local-LPG run in one virtual environment shared one
        # directory; the default is now derived from the process instead. See PylpgWorkspace.
        self.calculation_index_for_local_lpg: int = (
            config.calculation_index_for_local_lpg or PylpgWorkspace.default_base_index()
        )
        # Every index this run has claimed a pylpg working directory for. The cleanup is driven from
        # here rather than from the result folder, because a run that fails before producing one
        # still has a directory to remove -- see PylpgWorkspace.release.
        self.claimed_pylpg_calculation_indices: List[int] = []

        self.build()
        # dummy value as long as there is no way to consider multiple households in one house
        self.scaling_factor_according_to_number_of_apartments: float = 1.0

        # Inputs - Not Mandatory
        self.ww_mass_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WW_MassInput,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            False,
        )
        self.ww_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WW_TemperatureInput,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            False,
        )

        self.number_of_residents_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.NumberOfResidents,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Number of people at home.",
        )
        self.heating_by_residents_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingByResidents,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for LPG UTSP {self.HeatingByResidents} will follow.",
            energy_port=EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ROOM_HEAT),
        )
        self.heating_by_devices_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingByDevices,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description="Inner device heat gains, which heat the building (not intentionally)",
            energy_port=EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ROOM_HEAT),
        )
        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalPowerConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            output_description="Electrical power consumption by residents.",
            energy_port=EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY),
        )

        self.electricity_energy_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalEnergyConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
            output_description="Electrical energy consumption by residents.",
        )

        self.water_consumption_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.WaterConsumption,
            lt.LoadTypes.WARM_WATER,
            lt.Units.LITER,
            output_description=f"here a description for LPG UTSP {self.WaterConsumption} will follow.",
        )

        # The occupancy moves energy (complete Sankey spec, §5.3): it takes the appliances' electricity from the
        # electricity meter that reads it and the residents' body heat from the residents, and releases the device heat
        # gains and the body heat into the building, which reads both. The LoadProfileGenerator gives the device
        # heat gains as a profile of its own, so the electricity they do not account for leaves the house with
        # waste water, exhaust air and light. An output carries one port, so the body heat is booked on two: its way
        # out to the building on HeatingByResidents, which the building reads, and its way in from the residents on
        # an output of its own, which needs no reader.
        self.body_heat_to_building_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.BodyHeatToBuildingInWatt,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            output_description=(
                "The residents' body heat the occupancy takes in from the residents, in W; equal to "
                "HeatingByResidents, which the building reads as its gain."
            ),
            energy_port=EnergyPort(
                lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ROOM_HEAT, environment=lt.EnvironmentNode.RESIDENTS
            ),
        )
        self.appliance_electricity_not_released_indoors_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ApplianceElectricityNotReleasedIndoorsInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description=(
                "The appliances' electricity that does not become a heat gain of the building, in W: the electrical "
                "power consumption minus the device heat gains. It leaves the house with waste water, exhaust air "
                "and light."
            ),
            energy_port=EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY),
        )

    def i_save_state(self) -> None:
        """Empty method as component has no state."""
        pass

    def i_restore_state(self) -> None:
        """Empty method as component has no state."""
        pass

    def i_prepare_simulation(self) -> None:
        """Publishes the driving profile of every car the LoadProfileGenerator reported.

        The car components are built from their configurations alone and therefore cannot be
        handed a time series through their constructors; they look their own profile up in the
        simulation repository instead, and this is where those profiles get there. Publishing in
        the preparation phase rather than in ``__init__`` is necessary because the repository only
        reaches a component once it has been registered with a simulator.

        An occupancy reading a predefined profile carries no car data at all, which is a perfectly
        ordinary household that simply owns no simulated car; it publishes nothing and stays
        silent. The complaint belongs to the car that cannot find itself, not to the occupancy.
        """
        if not GenericCarInformation.has_car_data(self.car_data_dict):
            return
        CarProfileHandover.publish(
            repository=self.simulation_repository,
            profiles=GenericCarInformation(my_occupancy_instance=self).data_dict_for_car_component,
        )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Gets called after the iterations are finished at each time step for potential debugging purposes."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Sets the current output values with data retrieved during initialization."""
        if self.ww_mass_input_channel.source_output is not None:
            # ww demand
            ww_temperature_demand = HouseholdWarmWaterDemandConfig.ww_temperature_demand

            # From Thermal Energy Storage
            ww_mass_input_per_sec = stsv.get_input_value(self.ww_mass_input_channel)  # kg/s
            # ww_mass_input = ww_mass_input_per_sec * self.seconds_per_timestep           # kg
            ww_mass_input: float = ww_mass_input_per_sec
            ww_temperature_input = stsv.get_input_value(self.ww_temperature_input_channel)  # °C

            # Information import
            freshwater_temperature = HouseholdWarmWaterDemandConfig.freshwater_temperature
            temperature_difference_cold = HouseholdWarmWaterDemandConfig.temperature_difference_cold
            energy_losses = 0
            specific_heat = 4180 / 3600

            ww_energy_demand = (
                specific_heat * self.water_consumption[timestep] * (ww_temperature_demand - freshwater_temperature)
            )

            if ww_energy_demand > 0 and (ww_mass_input == 0 and ww_temperature_input == 0):
                # nothing published yet --> placeholder values: the first pass of step 0 starts from zeros, so this
                # connector reads 0 from a warm-water storage later in the order, which has not published yet; every
                # later step starts from the values the previous step converged to (the simulator's warm start)
                ww_temperature_input = DEFAULT_WW_TEMPERATURE_INPUT
                ww_mass_input = DEFAULT_WW_MASS_INPUT

            # Warm water is provided by the warmwater stoage.
            # The household needs water at a certain temperature. To get the correct temperature the amount of water from
            # the wws is regulated and is depending on the temperature provided by the wws. The backflowing water to wws
            # is cooled down to the temperature of (freshwater+temperature_difference_cold) --> ww_temperature_output.
            if ww_energy_demand > 0:
                # heating up the freshwater. The mass is consistent
                energy_discharged = ww_energy_demand + energy_losses
                ww_temperature_output: float = freshwater_temperature + temperature_difference_cold
                ww_mass_input = energy_discharged / (
                    PhysicsConfig.get_properties_for_energy_carrier(
                        energy_carrier=lt.LoadTypes.WATER
                    ).specific_heat_capacity_in_joule_per_kg_per_kelvin
                    * (ww_temperature_input - ww_temperature_output)
                )
            else:
                ww_temperature_output = ww_temperature_input
                ww_mass_input = 0
                energy_discharged = 0

        stsv.set_output_value(self.number_of_residents_channel, self.number_of_residents[timestep])
        stsv.set_output_value(self.heating_by_residents_channel, self.heating_by_residents[timestep])
        stsv.set_output_value(self.heating_by_devices_channel, self.heating_by_devices[timestep])
        stsv.set_output_value(self.electricity_output_channel, self.electricity_consumption[timestep])
        stsv.set_output_value(self.body_heat_to_building_channel, self.heating_by_residents[timestep])
        stsv.set_output_value(
            self.appliance_electricity_not_released_indoors_channel,
            self.electricity_consumption[timestep] - self.heating_by_devices[timestep],
        )
        stsv.set_output_value(
            self.electricity_energy_output_channel,
            self.electricity_consumption[timestep] * self.my_simulation_parameters.seconds_per_timestep / 3600,
        )
        stsv.set_output_value(self.water_consumption_channel, self.water_consumption[timestep])

        if self.config.predictive_control:
            last_forecast_timestep = int(timestep + 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep)
            if last_forecast_timestep > len(self.electricity_consumption):
                last_forecast_timestep = len(self.electricity_consumption)
            demandforecast = self.electricity_consumption[timestep:last_forecast_timestep]
            self.simulation_repository.set_entry(self.Electricity_Demand_Forecast_24h, demandforecast)

    def get_profiles_from_utsp(
        self, lpg_households: Union[JsonReference, List[JsonReference]], guid: str, utsp_url: str, utsp_api_key: str
    ) -> List[LpgResultFiles]:
        """Request the households' profiles from a UTSP server and return the contents of their result files.

        The UTSP is asked for the same days as a local LoadProfileGenerator, the run's whole calendar year
        (:meth:`lpg_request_days`). A list of households is requested in parallel.

        Args:
            lpg_households: one household reference, or a list of them.
            guid: the unique identifier of the calculation request.
            utsp_url: the address of the UTSP server.
            utsp_api_key: the key the UTSP server accepts.

        Returns:
            List[LpgResultFiles]: one entry per household, in the order requested, holding each file's content.

        Raises:
            TypeError: if ``lpg_households`` is neither a reference nor a list of them.
        """
        start_date, end_date = self.lpg_request_days(self.my_simulation_parameters)

        if isinstance(lpg_households, JsonReference):
            simulation_config = self.prepare_lpg_simulation_config_for_utsp_request(
                start_date=start_date,
                end_date=end_date,
                household=lpg_households,
            )
            return [
                LpgResultFiles(
                    *self.calculate_one_utsp_request(
                        simulation_config=simulation_config, guid=guid, url=utsp_url, api_key=utsp_api_key
                    )
                )
            ]

        if isinstance(lpg_households, List):
            simulation_configs = [
                self.prepare_lpg_simulation_config_for_utsp_request(
                    start_date=start_date, end_date=end_date, household=household
                )
                for household in lpg_households
            ]
            files_per_kind = self.calculate_multiple_utsp_requests(
                url=utsp_url,
                api_key=utsp_api_key,
                lpg_configs=simulation_configs,
                guid=guid,
            )
            return [LpgResultFiles(*files_of_one_household) for files_of_one_household in zip(*files_per_kind)]

        raise TypeError(
            f"Type of lpg_households {type(lpg_households)} is invalid. It should be a JSONReference or a list of JSONReference."
        )

    def get_profiles_from_local_lpg(self, lpg_households: Union[JsonReference, List[JsonReference]]) -> List[LpgResultFiles]:
        """Compute the households' profiles with the local LoadProfileGenerator and return the paths of their result files.

        The files lie in the generator's working directories, which :meth:`build` deletes once the profiles are
        read, so the paths are valid only until then.

        Args:
            lpg_households: one household reference, or a list of them, computed one after another.

        Returns:
            List[LpgResultFiles]: one entry per household, in the order requested, holding each file's path.

        Raises:
            TypeError: if ``lpg_households`` is neither a reference nor a list of them.
        """
        if isinstance(lpg_households, JsonReference):
            # The first element is the result folder, which nothing reads.
            return [LpgResultFiles(*self.calculate_one_lpg_request(household=lpg_households)[1:])]

        if isinstance(lpg_households, List):
            files_per_kind = self.calculate_multiple_lpg_request(households=lpg_households)[1:]
            return [LpgResultFiles(*files_of_one_household) for files_of_one_household in zip(*files_per_kind)]

        raise TypeError(
            f"Type of lpg_households {type(lpg_households)} is invalid. It should be a JSONReference or a list of JSONReference."
        )

    def get_profiles_from_predefined_profile(self) -> LpgResultFiles:
        """Return the paths of the shipped profile's files, from ``hisim/inputs/loadprofiles`` or an external directory.

        The shipped profile has no flexibility and no car files, so those stay empty.

        Returns:
            LpgResultFiles: the paths of the five files the shipped profile has.

        Raises:
            ValueError: if an external profile directory is configured and does not exist or is empty.
        """
        if self.name_of_predefined_loadprofile is None:
            self.name_of_predefined_loadprofile = "CHR01 Couple both at Work"
            log.information(f"You choose {self.utsp_config.data_acquisition_mode}, "
                            f"but did not specify a predefined loadprofile."
                            f" Using default profile {self.name_of_predefined_loadprofile}.")

        if self.predefined_loadprofile_filepaths is None:
            log.information("Filepath of predefined loadprofile defined in hisim.utils.py is used")
            predefined_profile_filepaths = utils.HISIMPATH["occupancy"][self.name_of_predefined_loadprofile]
            # get first bodily activity files
            bodily_activity_filepaths = predefined_profile_filepaths["number_of_residents"]

            high_activity_file = bodily_activity_filepaths[0]
            low_activity_file = bodily_activity_filepaths[1]
            # get other files
            if self.utsp_config.profile_with_washing_machine_and_dishwasher:
                electricity_file = predefined_profile_filepaths["electricity_consumption"]
            else:
                electricity_file = predefined_profile_filepaths[
                    "electricity_consumption_without_washing_machine_and_dishwasher"
                ]

            warm_water_file = predefined_profile_filepaths["water_consumption"]
            inner_device_heat_gains_file = predefined_profile_filepaths["heating_by_devices"]

        else:
            clean_basename = os.path.basename(self.predefined_loadprofile_filepaths).replace("_", " ").lower().strip()
            clean_target = self.name_of_predefined_loadprofile.replace("_", " ").lower().strip()

            if clean_target in clean_basename:
                predefined_profile_filepaths = self.predefined_loadprofile_filepaths
            else:
                predefined_profile_filepaths = os.path.join(self.predefined_loadprofile_filepaths,
                                                            self.name_of_predefined_loadprofile)

            if os.path.isdir(predefined_profile_filepaths):
                files_in_dir = os.listdir(predefined_profile_filepaths)
                if not files_in_dir:
                    raise ValueError("Something in name or path of external predefinded loadprofile is wrong!")
                log.information(f"{len(files_in_dir)} files of loadprofles found.")
            else:
                raise ValueError(f"External path of loadprofle {predefined_profile_filepaths} doesn´t exist!")

            log.information(f"External Filepath of predefined loadprofile is used: {predefined_profile_filepaths}")

            high_activity_file = os.path.join(predefined_profile_filepaths, "BodilyActivityLevel.High.HH1.json")
            low_activity_file = os.path.join(predefined_profile_filepaths, "BodilyActivityLevel.Low.HH1.json")
            if self.utsp_config.profile_with_washing_machine_and_dishwasher:
                electricity_file = os.path.join(predefined_profile_filepaths, "SumProfiles.HH1.Electricity.csv")
            else:
                electricity_file = os.path.join(predefined_profile_filepaths, "SumProfiles.NoFlex.HH1.Electricity.csv")

            warm_water_file = os.path.join(predefined_profile_filepaths, "SumProfiles.HH1.Warm Water.csv")
            inner_device_heat_gains_file = os.path.join(predefined_profile_filepaths, "SumProfiles.HH1.Inner Device Heat Gains.csv")

        return LpgResultFiles(
            electricity=electricity_file,
            warm_water=warm_water_file,
            inner_device_heat_gains=inner_device_heat_gains_file,
            high_activity=high_activity_file,
            low_activity=low_activity_file,
        )

    def describe_unreachable_profile_source(self) -> str:
        """Builds the guidance printed when the configured profile source cannot deliver a profile.

        The connector used to answer an unreachable profile source by rewriting its own
        ``data_acquisition_mode`` and continuing with a shipped profile, which produced a full set of
        plausible results for a household nobody had asked for. That chain is gone, so the remaining
        job is to make the resulting failure worth reading: name the mode that was configured, and
        say exactly which one-line configuration change reaches a source that works.

        The note about the predefined profile is the important half. It is not a degraded version of
        the requested household but a different household altogether, so anyone switching to it has
        to know that the numbers stop being comparable with the other two modes.

        Returns:
            str: a multi-line message meant to be logged next to the original traceback, never in
                place of it.
        """
        return (
            f"Occupancy profile could not be obtained in {self.utsp_config.data_acquisition_mode.name} mode; "
            "the underlying error follows this message.\n"
            "\n"
            "USE_UTSP needs UTSP_URL and UTSP_API_KEY in a .env file at the repository root.\n"
            "If you do not have UTSP access, set data_acquisition_mode on UtspLpgConnectorConfig to one of\n"
            "  USE_LOCAL_LPG          - generate the profile locally with pylpg (slower)\n"
            "  USE_PREDEFINED_PROFILE - use a shipped profile; NOTE this is a DIFFERENT household\n"
            "                           and results are not comparable with the other two modes."
        )

    def build(self) -> None:
        """Read the occupancy's series for the run: from the shipped profile, or from the cache or the LoadProfileGenerator.

        A household the LoadProfileGenerator computes is computed once per configuration and year: the cache holds
        its minute profile of the whole requested year (:class:`LpgMinuteProfile`), and every run, whatever its
        window and timestep, reads the profile from there and cuts its own series from it
        (:meth:`series_of_the_run`). Several households, given as a list, are read one by one and summed. The shipped
        profile is read from its files on every run; it needs no generator and is not cached.

        There is deliberately no fallback chain here. The configured mode is the mode that runs: a profile source
        that cannot be reached is a configuration error, not an invitation to substitute a different household
        (roadmap/pylpg_flakiness.md F1).
        """
        self.car_data_dict: Dict[str, Any] = {
            "car_states": [],
            "car_locations": [],
            "driving_distances": [],
        }
        self.flexibility_data_dict: Dict = {"flexibility": []}

        if self.utsp_config.data_acquisition_mode == LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE:
            log.information(
                f"LPG data acquisition mode: {self.utsp_config.data_acquisition_mode}. "
                f"This means the {self.name_of_predefined_loadprofile} from hisim/inputs/loadprofiles/ is taken."
            )
            (
                self.electricity_consumption,
                self.heating_by_devices,
                self.water_consumption,
                self.heating_by_residents,
                self.number_of_residents,
            ) = self.series_of_the_run(self.read_minute_profile(self.get_profiles_from_predefined_profile()))
            self.max_hot_water_demand = max(self.water_consumption)
            return

        self.minute_profile_cache_entries, household_configs = self.minute_profile_cache_entries_of_households(
            cache_dir_path=self.utsp_config.cache_dir_path
        )
        value_dict: Dict[str, List[List[float]]] = {
            "electricity_consumption": [],
            "water_consumption": [],
            "heating_by_devices": [],
            "heating_by_residents": [],
            "number_of_residents": [],
        }
        for cache_entry, household_config in zip(self.minute_profile_cache_entries, household_configs):
            log.information("Lpg cache filepath " + cache_entry.path)
            if cache_entry.exists:
                log.information("LPG data taken from cache.")
                minute_profiles = self.read_cached_minute_profiles(cache_entry.path)
            else:
                log.information(f"LPG data is not in the cache; it is computed in {self.utsp_config.data_acquisition_mode}.")
                minute_profiles = self.minute_profiles_from_the_generator(household_config)
                self.write_minute_profiles(cache_entry, minute_profiles)

            for minute_profile in minute_profiles:
                series = self.series_of_the_run(minute_profile)
                for name, values_of_the_households in value_dict.items():
                    values_of_the_households.append(getattr(series, name))
                self.flexibility_data_dict["flexibility"].append(minute_profile.flexibility)
                self.car_data_dict["car_states"].append(minute_profile.car_states)
                self.car_data_dict["car_locations"].append(minute_profile.car_locations)
                self.car_data_dict["driving_distances"].append(minute_profile.driving_distances)

        # sum over all household profiles
        (
            self.electricity_consumption,
            self.heating_by_residents,
            self.water_consumption,
            self.heating_by_devices,
            self.number_of_residents,
        ) = self.get_result_lists_by_summing_over_value_dict(value_dict=value_dict)
        self.max_hot_water_demand = max(self.water_consumption)

    def minute_profiles_from_the_generator(self, household_config: UtspLpgConnectorConfig) -> List[LpgMinuteProfile]:
        """Have the LoadProfileGenerator compute one configuration's households and read their minute profiles.

        The configuration is the effective one the request is made with -- one household, or a list of them that
        share a request guid -- so the cache entry the profiles are written to is named after what produced them.
        A local generator's working directories are deleted afterwards, whether the calculation succeeded or not.

        Args:
            household_config: the configuration of the request, from :meth:`minute_profile_cache_entries_of_households`.

        Returns:
            List[LpgMinuteProfile]: one profile per household of the request, in its order.

        Raises:
            Exception: whatever the profile source raised, unchanged, after the guidance of
                :meth:`describe_unreachable_profile_source` was logged.
        """
        try:
            log.information(f"LPG data acquisition mode: {self.utsp_config.data_acquisition_mode}")
            if self.utsp_config.data_acquisition_mode == LpgDataAcquisitionMode.USE_UTSP:
                # These raise when the .env is missing, and that is the intent: an unconfigured UTSP must stop the
                # run rather than quietly become a different profile source.
                result_files = self.get_profiles_from_utsp(
                    lpg_households=household_config.household,
                    guid=household_config.guid,
                    utsp_url=utils.get_environment_variable("UTSP_URL"),
                    utsp_api_key=utils.get_environment_variable("UTSP_API_KEY"),
                )
            else:
                result_files = self.get_profiles_from_local_lpg(lpg_households=household_config.household)
            # Read before the finally block deletes the local generator's working directories.
            return [self.read_minute_profile(files) for files in result_files]

        except Exception:
            # Unwrapped on purpose: the original exception and its traceback reach the caller untouched, with the
            # guidance printed beside it rather than instead of it. Narrowing or re-wrapping here would only
            # mistranslate failures that nobody has observed yet.
            log.error(self.describe_unreachable_profile_source())
            raise

        finally:
            # Runs whether the attempt succeeded, raised or was interrupted. The directories are resolved from the
            # indices claimed above, which are known before the calculation starts, so a failure cannot leave
            # debris for the next run to trip over.
            PylpgWorkspace.release(self.claimed_pylpg_calculation_indices, self.pylpg_work_root())
            self.claimed_pylpg_calculation_indices.clear()

    def get_result_lists_by_summing_over_value_dict(
        self, value_dict: Dict[Any, Any]
    ) -> Tuple[List, List, List, List, List]:
        """Get the result lists by summing over the value dict entries."""

        electricity_consumption = [sum(x) for x in zip(*value_dict["electricity_consumption"])]
        heating_by_residents = [sum(x) for x in zip(*value_dict["heating_by_residents"])]
        water_consumption = [sum(x) for x in zip(*value_dict["water_consumption"])]
        heating_by_devices = [sum(x) for x in zip(*value_dict["heating_by_devices"])]
        number_of_residents = [sum(x) for x in zip(*value_dict["number_of_residents"])]

        return (
            electricity_consumption,
            heating_by_residents,
            water_consumption,
            heating_by_devices,
            number_of_residents,
        )

    def write_to_report(self):
        """Adds a report entry for this component."""
        return self.utsp_config.get_string_dict()

    def minute_profile_cache_entries_of_households(
        self, cache_dir_path: Optional[str]
    ) -> Tuple[List[CacheEntry], List[UtspLpgConnectorConfig]]:
        """Return the cache entry of every request this occupancy makes, and the configuration of each request.

        A list of households without a request guid is requested, and cached, household by household, each with a
        guid of its own so that duplicates get different profiles
        (:meth:`vary_guids_for_lpg_utsp_requests_if_config_household_is_a_list_and_contains_duplicated_household_types`).
        Any other configuration is one request. Each entry is named after its request's configuration
        (:meth:`minute_profile_key_material`).

        Args:
            cache_dir_path: a directory that overrides the run's cache locations, or ``None``.

        Returns:
            Tuple[List[CacheEntry], List[UtspLpgConnectorConfig]]: the entries and the configurations, in the same
                order.
        """
        if isinstance(self.utsp_config.household, List) and self.utsp_config.guid == "":
            guid_list = (
                self.vary_guids_for_lpg_utsp_requests_if_config_household_is_a_list_and_contains_duplicated_household_types()
            )
            household_configs = []
            for index, household in enumerate(self.utsp_config.household):
                household_config = copy.deepcopy(self.utsp_config)
                household_config.household = household
                household_config.guid = guid_list[index]
                household_configs.append(household_config)
        else:
            household_configs = [self.utsp_config]

        cache_entries = [
            utils.get_cache_entry(
                component_key=self.config.component_id.name,
                key_material=self.minute_profile_key_material(household_config, self.my_simulation_parameters),
                my_simulation_parameters=self.my_simulation_parameters,
                cache_dir_path=cache_dir_path,
            )
            for household_config in household_configs
        ]
        return cache_entries, household_configs

    @classmethod
    def minute_profile_key_material(cls, household_config: UtspLpgConnectorConfig, parameters: SimulationParameters) -> str:
        """Return the string a minute-profile cache entry is named after, and that its ``.meta`` file records.

        An entry holds the minute profiles the LoadProfileGenerator computed for one request
        (:class:`LpgMinuteProfile`), so the key names what decides them and nothing else: the request's
        configuration (household, acquisition mode, energy intensity, mobility sets, appliances, guid; the fields
        that only place the run are cleared, :meth:`UtspLpgConnectorConfig.cache_key_view`) and the days the
        generator simulates, which are the run's calendar year (:meth:`lpg_request_days`). The run's window, its
        timestep and its part-load threshold are not part of it, because they are applied when the profile is
        read. :attr:`MINUTE_PROFILE_CACHE_CONTENT` names what the entry holds, so an entry of another layout is a
        miss.

        Example: a week, a July week and the whole of 2021, at 60 s or 900 s, all give
        ``'{...configuration...}:lpg_minute_profiles:lpg_request=2021-01-01..2022-01-01'``.

        Args:
            household_config: the configuration the request is made with.
            parameters: the run's simulation parameters; only their year is read.

        Returns:
            str: the configuration's key JSON, the content name and the requested days.
        """
        return (
            household_config.cache_key_view().to_json()
            + f":{cls.MINUTE_PROFILE_CACHE_CONTENT}:lpg_request="
            + "..".join(cls.lpg_request_days(parameters))
        )

    def vary_guids_for_lpg_utsp_requests_if_config_household_is_a_list_and_contains_duplicated_household_types(
        self,
    ) -> List[str]:
        """In case the lpg_utsp_connector config is given a list of households, it will be checked if the list contains any duplicates.

        If so, for each duplicate the guid will be varied to make sure that each lpg request delivers a unique profile.
        """
        # check if household is list and return guid list
        if isinstance(self.utsp_config.household, List):
            copied_households = copy.deepcopy(self.utsp_config.household)
            guid_list = []

            for household in self.utsp_config.household:
                number_of_duplicated_households = copied_households.count(household)
                if number_of_duplicated_households == 1:
                    guid_list.append(str(1))
                elif number_of_duplicated_households > 1:
                    guid_list.append(str(number_of_duplicated_households))
                    copied_households.remove(household)

        return guid_list

    def pylpg_cache_directory(self) -> str:
        """Returns the cache directory this component's local LoadProfileGenerator runs use.

        The directory its cache entries are written to: the profile is built while the component is
        constructed, before the run's result directory exists, and the installed ``pylpg`` package is
        not a place a calculation may write to. Both the binaries (``PylpgWorkspace.binary_root``)
        and the calculation directories (``PylpgWorkspace.work_root``) live below it.
        """
        # A missing entry's path names the write directory; an existing one may sit in a read-only seed.
        missing = [entry.path for entry in self.minute_profile_cache_entries if not entry.exists]
        cache_filepath = missing[0] if missing else self.minute_profile_cache_entries[0].path
        return str(os.path.dirname(cache_filepath))

    def pylpg_work_root(self) -> Path:
        """Returns where this component's local LoadProfileGenerator calculations compute.

        See ``PylpgWorkspace.work_root`` and :meth:`pylpg_cache_directory`.
        """
        return PylpgWorkspace.work_root(self.pylpg_cache_directory())

    def execute_local_lpg_single_household(self,
                                           calculation_index,
                                           household: JsonReference,
                                           random_seed: Optional[int] = None
                                           ) -> str:
        """Runs one local LPG calculation, serialised against every other in this installation.

        The LoadProfileGenerator cannot have two calculations running at once inside one
        installation: separate working directories were not enough and separate copies of the
        executable and its database were not either, so the lock is held for the whole calculation
        and they take turns. See PylpgWorkspace.exclusive_generator_access for why, and for why this
        is expected to be deleted rather than maintained.
        """
        with PylpgWorkspace.exclusive_generator_access():
            return self._execute_local_lpg_single_household(calculation_index, household, random_seed)

    def _execute_local_lpg_single_household(self,
                                            calculation_index,
                                            household: JsonReference,
                                            random_seed: Optional[int] = None
                                            ) -> str:
        """Using local (offline) LPG to calculate the profiles for one household."""

        mobility_set: Set[Optional[str]] = set()
        for elem in [
            self.utsp_config.charging_station_set,
            self.utsp_config.transportation_device_set,
            self.utsp_config.travel_route_set,
        ]:
            if elem is None:
                mobility_set.add(None)
            else:
                mobility_set.add(elem.Name)

        if len(mobility_set) == 1 and None in mobility_set:
            simulate_transportation = False
        elif len(mobility_set) == 3:
            for entry in mobility_set:
                if entry is None:
                    log.warning(
                        "One element in the mobility configuration is None. Simulation of mobility will be deactivated."
                    )
                    simulate_transportation = False
                    break
            else:
                simulate_transportation = True
        else:
            log.warning("Mobility configuration incomplete. Simulation of mobility  will be deactivated.")
            simulate_transportation = False

        with contextlib.redirect_stdout(None):
            with contextlib.redirect_stderr(None):

                householdref = household
                housetype = HouseTypes.HT23_No_Infrastructure_at_all
                startdate, enddate = self.lpg_request_days(self.my_simulation_parameters)
                geographic_location = None

                chargingset = self.utsp_config.charging_station_set
                transportation_device_set = self.utsp_config.transportation_device_set
                travel_route_set = self.utsp_config.travel_route_set
                energy_intensity = self.utsp_config.energy_intensity

                calc_options = [
                    CalcOption.JsonHouseholdSumFiles,
                    CalcOption.HouseholdSumProfilesFromDetailedDats,
                    CalcOption.HouseholdSumProfilesCsvNoFlex,
                    CalcOption.BodilyActivityStatistics,
                    CalcOption.FlexibilityEvents,
                ]

                # Claimed before the executor is built, because LPGExecutor clears whatever it finds
                # and starts writing: a directory that already exists belongs either to a run still
                # using it or to one that died, and both deserve a named failure rather than a shared
                # folder.
                # Before the executor exists, and into the cache directory rather than the installed
                # package (hisim.write_guard): LPGExecutor.__init__ would install the binaries into
                # the package when they are missing, and doing that in several processes at once is
                # how one of them ends up writing the executable another is running (ETXTBSY).
                cache_directory = self.pylpg_cache_directory()
                PylpgWorkspace.install_binaries_if_missing(cache_directory)
                PylpgWorkspace.claim(calculation_index, PylpgWorkspace.work_root(cache_directory))
                self.claimed_pylpg_calculation_indices.append(calculation_index)
                # Built by the workspace rather than by LPGExecutor(...), whose constructor copies the
                # toolchain into the installed package; this copies it from the binaries below the
                # cache directory to a calculation directory beside them, and runs that copy rather
                # than the shared original.
                lpe: lpg_execution.LPGExecutor = PylpgWorkspace.start_executor(calculation_index, cache_directory)

                request = lpe.make_default_lpg_settings(self.my_simulation_parameters.year)
                if random_seed is not None and request.CalcSpec is not None:
                    request.CalcSpec.RandomSeed = random_seed
                assert request.House is not None, "HouseData was None"

                request.House.HouseTypeCode = housetype
                hhnamespec = lpg_execution.HouseholdNameSpecification(householdref)
                hhn = lpg_execution.HouseholdData(
                    HouseholdDataPersonSpec=None,
                    HouseholdTemplateSpec=None,
                    HouseholdNameSpec=hhnamespec,
                    UniqueHouseholdId="hhid",
                    Name="hhname",
                    ChargingStationSet=chargingset,
                    TransportationDeviceSet=transportation_device_set,
                    TravelRouteSet=travel_route_set,
                    TransportationDistanceModifiers=None,
                    HouseholdDataSpecification=lpg_execution.HouseholdDataSpecificationType.ByHouseholdName,
                )
                request.House.Households.append(hhn)

                if request.CalcSpec is None:
                    raise Exception("Failed to initialize the calculation spec")
                if startdate is not None:
                    request.CalcSpec.set_StartDate(startdate)
                if enddate is not None:
                    request.CalcSpec.set_EndDate(enddate)
                request.CalcSpec.EnergyIntensityType = energy_intensity
                request.CalcSpec.GeographicLocation = geographic_location
                request.CalcSpec.set_EnableFlexibility(True)
                if calc_options:
                    request.CalcSpec.CalcOptions = calc_options
                if simulate_transportation:
                    request.CalcSpec.set_EnableTransportation(True)
                    request.CalcSpec.CalcOptions.append(CalcOption.TansportationDeviceJsons)

                request.CalcSpec.ExternalTimeResolution = self.LPG_EXTERNAL_TIME_RESOLUTION
                calcspecfilename = Path(lpe.calculation_directory, "calcspec.json")
                with open(calcspecfilename, "w", encoding="utf-8") as calcspecfile:
                    jsonrequest = request.to_json(indent=4)
                    calcspecfile.write(jsonrequest)
                path_to_result_folder = os.path.join(lpe.calculation_directory, request.CalcSpec.OutputDirectory)
                # The results are checked here, not by the readers downstream. The binary's return
                # code does not decide success, so a failed calculation is indistinguishable from a
                # successful one until somebody opens a file that was never written -- and the FileNotFoundError
                # that results names one arbitrary json and never mentions the LoadProfileGenerator.
                # The names, not merely the count: a calculation that dies partway leaves some of
                # its outputs behind, and a directory that is non-empty tells the reader nothing
                # about whether what it needs is in it. Which files are indispensable is already
                # recorded against each name, so it is read from there rather than from a position
                # in the returned tuple, which would go wrong silently if that tuple ever changed.
                # Run and check are one call, because whether to retry (only for a locked database)
                # is known only after the check. See PylpgWorkspace.execute_and_verify.
                declared_result_files = self.define_required_result_files()[0]
                required_result_files = [
                    file_name
                    for file_name, requirement in declared_result_files.items()
                    if requirement == datastructures.ResultFileRequirement.REQUIRED
                ]
                PylpgWorkspace.execute_and_verify(
                    lpe, calculation_index, str(path_to_result_folder), required_files=required_result_files
                )

        return str(path_to_result_folder)

    @classmethod
    def lpg_request_days(cls, parameters: SimulationParameters) -> Tuple[str, str]:
        """Return the first and the last day the LoadProfileGenerator simulates for a run, as it reads them.

        The same days are asked of a local LoadProfileGenerator and of the UTSP. The generator always simulates
        the run's whole calendar year, whatever the run's length or start, and the run's window is cut from that
        year when the profile is read (:meth:`series_of_the_run`). The household a
        generator simulates depends on how many days it simulates, so a week asked for on its own differs from
        the same week of the year; with the year always asked for, a window gets the same profile as the same
        days of a full-year run (owner decision of 2026-10-19).

        The request starts on 1 January of the run's year and ends on 1 January of the next year, that day
        included, as the generator simulates its end day. The extra 1 January is the end day the local full-year
        request always had, so a local full-year run's request, and its profile, stay as they were. The UTSP
        full-year request ended on 31 December before (``2021-01-01..2021-12-31``) and now ends on 1 January of
        the next year too, so a UTSP full-year profile may differ from an earlier one. No window needs
        it to cover the hour the wall clock runs ahead in summer: a run lies within one calendar year
        (:class:`hisim.simulationparameters.SimulationParameters` refuses one that does not), and its last
        minute, on 31 December at the latest, is on standard time.

        Example: a day, a week, a July week and the whole of 2021 are all requested as
        ``("2021-01-01", "2022-01-01")``. Written day-first, ``08-01-2021`` was read as 1 August.

        Args:
            parameters: the run's simulation parameters.

        Returns:
            Tuple[str, str]: the first and the last day, in :attr:`LPG_DATE_FORMAT`.
        """
        first_day = datetime.date(parameters.start_date.year, 1, 1)
        last_day = datetime.date(parameters.start_date.year + 1, 1, 1)
        return first_day.strftime(cls.LPG_DATE_FORMAT), last_day.strftime(cls.LPG_DATE_FORMAT)

    def calculate_one_lpg_request(self, household: JsonReference) -> Tuple[str, str, str, str, str, str, str, str,
    str, str]:
        """Calculate one lpg request."""

        # define required results files
        (
            _,
            electricity,
            warm_water,
            inner_device_heat_gains,
            high_activity,
            low_activity,
            flexibility,
            car_states,
            car_locations,
            driving_distances,
        ) = self.define_required_result_files()

        log.information("Requesting LPG profiles from local lpg for one household.")

        calculation_index = PylpgWorkspace.calculation_index(self.calculation_index_for_local_lpg, 0)
        result_folder = self.execute_local_lpg_single_household(calculation_index=calculation_index,
                                                                household=household,
                                                                random_seed=None)

        # decode required result files
        electricity_file = os.path.join(result_folder, electricity)
        warm_water_file = os.path.join(result_folder, warm_water)
        inner_device_heat_gains_file = os.path.join(result_folder, inner_device_heat_gains)
        high_activity_file = os.path.join(result_folder, high_activity)
        low_activity_file = os.path.join(result_folder, low_activity)
        flexibility_file = os.path.join(result_folder, flexibility) if os.path.exists(
            os.path.join(result_folder, flexibility)) else ""

        car_states_file = ""
        for car_state in car_states.keys():
            car_state_path = os.path.join(result_folder, car_state)
            if os.path.exists(car_state_path):
                car_states_file = car_state_path
                break

        car_location_file = ""
        for car_location in car_locations.keys():
            car_location_path = os.path.join(result_folder, car_location)
            if os.path.exists(car_location_path):
                car_location_file = car_location_path
                break

        driving_distance_file = ""
        for driving_distance in driving_distances.keys():
            driving_distance_path = os.path.join(result_folder, driving_distance)
            if os.path.exists(driving_distance_path):
                driving_distance_file = driving_distance_path
                break

        return (
            result_folder,
            electricity_file,
            warm_water_file,
            inner_device_heat_gains_file,
            high_activity_file,
            low_activity_file,
            flexibility_file,
            car_states_file,
            car_location_file,
            driving_distance_file,
        )

    def calculate_multiple_lpg_request(
        self, households: List[JsonReference]
    ) -> Tuple[List[str], List[str], List[str], List[str], List[str], List[str], List[str], List[str], List[str], List[str]]:
        """Calculate one lpg request."""
        # define required results files
        (
            _,
            electricity,
            warm_water,
            inner_device_heat_gains,
            high_activity,
            low_activity,
            flexibility,
            car_states,
            car_locations,
            driving_distances,
        ) = self.define_required_result_files()

        log.information("Requesting LPG profiles from local lpg for multiple household.")
        result_folder_list = []
        # The households of one request are computed one after another but read together at the end,
        # so each needs a directory of its own that outlives its calculation. They come out of this
        # run's base index rather than restarting at 1, which is what used to make two concurrent
        # multi-household requests compute in the same folders.
        for household_ordinal, household in enumerate(households):
            calculation_index = PylpgWorkspace.calculation_index(
                self.calculation_index_for_local_lpg, household_ordinal
            )
            result_folder = self.execute_local_lpg_single_household(calculation_index=calculation_index,
                                                                    household=household,
                                                                    random_seed=None)
            result_folder_list.append(result_folder)

        # append all results in lists
        electricity_file: List = []
        warm_water_file: List = []
        inner_device_heat_gains_file: List = []
        high_activity_file: List = []
        low_activity_file: List = []
        flexibility_file: List = []
        car_states_file: List = []
        car_locations_file: List = []
        driving_distances_file: List = []

        for result_folder in result_folder_list:
            electricity_file_one_result = os.path.join(result_folder, electricity)
            warm_water_file_one_result = os.path.join(result_folder, warm_water)
            inner_device_heat_gains_file_one_result = os.path.join(result_folder, inner_device_heat_gains)
            high_activity_file_one_result = os.path.join(result_folder, high_activity)
            low_activity_file_one_result = os.path.join(result_folder, low_activity)

            flexibility_file_one_result = os.path.join(result_folder, flexibility) if os.path.exists(
                os.path.join(result_folder, flexibility)) else ""

            car_states_file_one_result = ""
            for car_state in car_states.keys():
                car_state_path = os.path.join(result_folder, car_state)
                if os.path.exists(car_state_path):
                    car_states_file_one_result = car_state_path
                    break

            car_locations_file_one_result = ""
            for car_location in car_locations.keys():
                car_location_path = os.path.join(result_folder, car_location)
                if os.path.exists(car_location_path):
                    car_locations_file_one_result = car_location_path
                    break

            driving_distances_file_one_result = ""
            for driving_distance in driving_distances.keys():
                driving_distance_path = os.path.join(result_folder, driving_distance)
                if os.path.exists(driving_distance_path):
                    driving_distances_file_one_result = driving_distance_path
                    break

            # append to lists
            electricity_file.append(electricity_file_one_result)
            warm_water_file.append(warm_water_file_one_result)
            inner_device_heat_gains_file.append(inner_device_heat_gains_file_one_result)
            high_activity_file.append(high_activity_file_one_result)
            low_activity_file.append(low_activity_file_one_result)
            flexibility_file.append(flexibility_file_one_result)
            car_states_file.append(car_states_file_one_result)
            car_locations_file.append(car_locations_file_one_result)
            driving_distances_file.append(driving_distances_file_one_result)

        return (
            result_folder_list,
            electricity_file,
            warm_water_file,
            inner_device_heat_gains_file,
            high_activity_file,
            low_activity_file,
            flexibility_file,
            car_states_file,
            car_locations_file,
            driving_distances_file,
        )

    def calculate_one_utsp_request(
        self, simulation_config: HouseCreationAndCalculationJob, guid: str, url: str, api_key: str
    ) -> Tuple[str, str, str, str, str, str, str, str, str]:
        """Calculate one lpg request."""

        # define required results files
        (
            result_files,
            electricity,
            warm_water,
            inner_device_heat_gains,
            high_activity,
            low_activity,
            flexibility,
            car_states,
            car_locations,
            driving_distances,
        ) = self.define_required_result_files()

        # Prepare the time series request
        request = datastructures.TimeSeriesRequest(
            simulation_config.to_json(), "LPG", required_result_files=result_files, guid=guid  # type: ignore
        )

        log.information("Requesting LPG profiles from the UTSP for one household.")

        # Request the time series
        result = client.request_time_series_and_wait_for_delivery(
            url, request, api_key, timeout=100
        )

        # decode required result files
        electricity_file = result.data[electricity].decode()
        warm_water_file = result.data[warm_water].decode()
        inner_device_heat_gains_file = result.data[inner_device_heat_gains].decode()
        high_activity_file = result.data[high_activity].decode()
        low_activity_file = result.data[low_activity].decode()
        if flexibility in result.data.keys():
            flexibility_file = result.data[flexibility].decode()
        else:
            flexibility_file = ""

        for car_state in car_states.keys():
            if car_state in result.data:
                car_states_file = result.data[car_state].decode()
        for car_location in car_locations.keys():
            if car_location in result.data:
                car_locations_file = result.data[car_location].decode()
        for driving_distance in driving_distances.keys():
            if driving_distance in result.data:
                driving_distances_file = result.data[driving_distance].decode()

        return (
            electricity_file,
            warm_water_file,
            inner_device_heat_gains_file,
            high_activity_file,
            low_activity_file,
            flexibility_file,
            car_states_file,
            car_locations_file,
            driving_distances_file,
        )

    def calculate_multiple_utsp_requests(
        self,
        lpg_configs: List,
        url: str,
        api_key: str,
        guid: str,
        raise_exceptions: bool = True,
        result_files: Any = None,
    ) -> Tuple[List[str], List[str], List[str], List[str], List[str], List[str], List[str], List[str], List[str]]:
        """Sends multiple lpg requests for parallel calculation and collects their results."""

        (
            result_files,
            electricity,
            warm_water,
            inner_device_heat_gains,
            high_activity,
            low_activity,
            flexibility,
            car_states,
            car_locations,
            driving_distances,
        ) = self.define_required_result_files()

        # Create all request objects
        all_requests: List[TimeSeriesRequest] = [
            TimeSeriesRequest(
                config.to_json(),
                "LPG",
                required_result_files=result_files,
                guid=guid,
            )
            for config in lpg_configs
        ]

        log.information("Requesting LPG profiles from the UTSP for multiple household.")

        results = calculate_multiple_requests(
            url,
            all_requests,
            api_key,
            raise_exceptions,
        )

        # append all results in lists
        electricity_file: List = []
        warm_water_file: List = []
        inner_device_heat_gains_file: List = []
        high_activity_file: List = []
        low_activity_file: List = []
        flexibility_file: List = []
        car_states_file: List = []
        car_locations_file: List = []
        driving_distances_file: List = []

        for result in results:
            if isinstance(result, Exception):
                raise ValueError("result is an exception. Something went wrong during the utsp request.")

            electricity_file_one_result = result.data[electricity].decode()
            warm_water_file_one_result = result.data[warm_water].decode()
            inner_device_heat_gains_file_one_result = result.data[inner_device_heat_gains].decode()
            high_activity_file_one_result = result.data[high_activity].decode()
            low_activity_file_one_result = result.data[low_activity].decode()

            try:
                flexibility_file_one_result = result.data[flexibility].decode()
            except Exception:
                pass

            for car_state in car_states.keys():
                if car_state in result.data:
                    car_states_file_one_result = result.data[car_state].decode()

            for car_location in car_locations.keys():
                if car_location in result.data:
                    car_locations_file_one_result = result.data[car_location].decode()

            for driving_distance in driving_distances.keys():
                if driving_distance in result.data:
                    driving_distances_file_one_result = result.data[driving_distance].decode()

            # append to lists
            electricity_file.append(electricity_file_one_result)
            warm_water_file.append(warm_water_file_one_result)
            inner_device_heat_gains_file.append(inner_device_heat_gains_file_one_result)
            high_activity_file.append(high_activity_file_one_result)
            low_activity_file.append(low_activity_file_one_result)
            flexibility_file.append(flexibility_file_one_result)
            car_states_file.append(car_states_file_one_result)
            car_locations_file.append(car_locations_file_one_result)
            driving_distances_file.append(driving_distances_file_one_result)

        return (
            electricity_file,
            warm_water_file,
            inner_device_heat_gains_file,
            high_activity_file,
            low_activity_file,
            flexibility_file,
            car_states_file,
            car_locations_file,
            driving_distances_file,
        )

    def prepare_lpg_simulation_config_for_utsp_request(
        self, start_date: Any, end_date: Any, household: JsonReference
    ) -> HouseCreationAndCalculationJob:
        """Prepare lpg simulation config for the utsp request."""

        simulation_config = lpg_helper.create_basic_lpg_config(
            householdref=household,
            housetype=HouseTypes.HT23_No_Infrastructure_at_all,
            startdate=start_date,
            enddate=end_date,
            external_resolution=self.LPG_EXTERNAL_TIME_RESOLUTION,
            energy_intensity=self.utsp_config.energy_intensity,
            travel_route_set=self.utsp_config.travel_route_set,
            transportation_device_set=self.utsp_config.transportation_device_set,
            charging_station_set=self.utsp_config.charging_station_set,
            calc_options=[
                CalcOption.HouseholdSumProfilesFromDetailedDats,
                CalcOption.HouseholdSumProfilesCsvNoFlex,
                CalcOption.BodilyActivityStatistics,
                CalcOption.TansportationDeviceJsons,
                CalcOption.FlexibilityEvents,
            ],
        )
        assert simulation_config.CalcSpec is not None

        # Enable simulation of transportation and flexible devices
        simulation_config.CalcSpec.EnableTransportation = True
        simulation_config.CalcSpec.EnableFlexibility = True

        return simulation_config

    def define_required_result_files(self):
        """Define required result files."""

        # Define required result files
        electricity = result_file_filters.LPGFilters.sum_hh1(
            LoadTypes.Electricity,
            no_flex=not self.utsp_config.profile_with_washing_machine_and_dishwasher,
        )
        warm_water = result_file_filters.LPGFilters.sum_hh1(LoadTypes.Warm_Water, no_flex=False)
        inner_device_heat_gains = result_file_filters.LPGFilters.sum_hh1(
            LoadTypes.Inner_Device_Heat_Gains, no_flex=False
        )
        high_activity = result_file_filters.LPGFilters.BodilyActivity.HIGH
        low_activity = result_file_filters.LPGFilters.BodilyActivity.LOW
        flexibility = result_file_filters.LPGFilters.FLEXIBILITY_EVENTS
        required_files = {
            f: datastructures.ResultFileRequirement.REQUIRED
            for f in [electricity, warm_water, inner_device_heat_gains, high_activity, low_activity]
        }
        optional_files = {flexibility: datastructures.ResultFileRequirement.OPTIONAL}
        # Define transportation result files
        car_states = result_file_filters.LPGFilters.all_car_states_optional()
        car_locations = result_file_filters.LPGFilters.all_car_locations_optional()
        driving_distances = result_file_filters.LPGFilters.all_driving_distances_optional()
        result_files: Dict[str, Optional[datastructures.ResultFileRequirement]] = {
            **required_files,
            **optional_files,
            **car_states,
            **car_locations,
            **driving_distances,
        }

        return (
            result_files,
            electricity,
            warm_water,
            inner_device_heat_gains,
            high_activity,
            low_activity,
            flexibility,
            car_states,
            car_locations,
            driving_distances,
        )

    def read_minute_profile(self, result_files: LpgResultFiles) -> LpgMinuteProfile:
        """Read one household's minute profile from its result files, the values exactly as the files state them.

        The UTSP hands over the files' contents, a local LoadProfileGenerator and the shipped profile their paths
        (:class:`LpgResultFiles`). Nothing of the run is applied here: the minutes stay on the generator's wall
        clock and in its units, so the profile can be cached once and serve every window and timestep.

        Args:
            result_files: the household's result files.

        Returns:
            LpgMinuteProfile: the profile; flexibility and car data are empty where their files are.

        Raises:
            ValueError: if the activity files state different starts (:meth:`common_start_time`).
        """
        reads_contents = self.utsp_config.data_acquisition_mode == LpgDataAcquisitionMode.USE_UTSP
        activity_profiles: List[Dict[str, Any]] = []
        for activity_file in (result_files.high_activity, result_files.low_activity):
            if reads_contents:
                activity_profiles.append(json.loads(activity_file))
            else:
                with open(activity_file, encoding="utf-8") as json_file:
                    activity_profiles.append(json.load(json_file))

        sum_columns: Dict[str, List[float]] = {}
        for name, result_file, column in (
            ("electricity", result_files.electricity, "Sum [kWh]"),
            ("warm_water", result_files.warm_water, "Sum [L]"),
            ("inner_device_heat_gains", result_files.inner_device_heat_gains, "Sum [kWh]"),
        ):
            if reads_contents:
                sum_profile = pd.read_csv(io.StringIO(result_file), sep=";", decimal=".", encoding="cp1252")
            else:
                sum_profile = pd.read_csv(result_file, sep=";", decimal=".", encoding="utf-8", usecols=[column])
            sum_columns[name] = sum_profile[column].tolist()

        return LpgMinuteProfile(
            start_time=self.common_start_time(activity_profiles),
            persons_at_high_activity=activity_profiles[0]["Values"],
            persons_at_low_activity=activity_profiles[1]["Values"],
            electricity_per_minute_in_kwh=sum_columns["electricity"],
            warm_water_per_minute_in_liter=sum_columns["warm_water"],
            inner_device_heat_gains_per_minute_in_kwh=sum_columns["inner_device_heat_gains"],
            flexibility=self.read_result_json(result_files.flexibility),
            car_states=self.read_result_json(result_files.car_states),
            car_locations=self.read_result_json(result_files.car_locations),
            driving_distances=self.read_result_json(result_files.driving_distances),
        )

    @classmethod
    def minute_values_of(cls, minute_profile: LpgMinuteProfile) -> pd.DataFrame:
        """Return a household's minute profile in the occupancy's quantities, one row per minute of the profile.

        The residents' number is the sum of the persons at a high and at a low activity level, and their body heat
        is :attr:`BODY_HEAT_PER_PERSON_IN_WATT` per person at each level. An energy per minute in kWh becomes a
        power in W by a factor of 1000 * 60. The rows run as long as the shortest series. Example: two persons at a
        high and one at a low activity level, 0.001 kWh of electricity and 0.5 l of warm water in a minute give 3
        residents, 400 W of body heat, 60 W of electricity and 0.5 l.

        The arithmetic and its order are the ones the occupancy has always used, so a profile gives the same values
        bit for bit whether it was just computed or read from the cache.

        Args:
            minute_profile: the household's profile as the LoadProfileGenerator wrote it.

        Returns:
            pd.DataFrame: the columns ``number_of_residents``, ``heating_by_residents`` (W),
                ``electricity_consumption`` (W), ``water_consumption`` (l) and ``inner_device_heat_gains`` (W).
        """
        activity_values = [minute_profile.persons_at_high_activity, minute_profile.persons_at_low_activity]
        minutes_of_activity = min(len(values) for values in activity_values)
        heating_by_residents = [0.0] * minutes_of_activity
        number_of_residents: List[float] = [0] * minutes_of_activity
        for level, gain in enumerate(cls.BODY_HEAT_PER_PERSON_IN_WATT):
            for minute in range(minutes_of_activity):
                number_of_residents[minute] += activity_values[level][minute]
                heating_by_residents[minute] = heating_by_residents[minute] + gain * activity_values[level][minute]

        minute_columns = {
            "number_of_residents": number_of_residents,
            "heating_by_residents": heating_by_residents,
            # 1 kWh per minute is 60 000 W
            "electricity_consumption": pd.to_numeric(
                pd.Series(minute_profile.electricity_per_minute_in_kwh) * 1000 * 60
            ).tolist(),
            "water_consumption": pd.to_numeric(pd.Series(minute_profile.warm_water_per_minute_in_liter)).tolist(),
            "inner_device_heat_gains": pd.to_numeric(
                pd.Series(minute_profile.inner_device_heat_gains_per_minute_in_kwh) * 1000 * 60
            ).tolist(),
        }
        minutes_in_common = min(len(values) for values in minute_columns.values())
        return pd.DataFrame({name: values[:minutes_in_common] for name, values in minute_columns.items()})

    def series_of_the_run(self, minute_profile: LpgMinuteProfile) -> OccupancySeries:
        """Return the run's series of one household: its minutes on the run's clock, cut to the window and averaged.

        The minutes (:meth:`minute_values_of`) are put onto the run's standard-time clock and the run's window is
        cut out (:meth:`hisim.components.lpg_local_time.LpgLocalTime.window_in_standard_time`); the profile starts
        where its activity files say, laid onto the run's year. A timestep of several minutes then gets the mean of
        its minutes for a power, the sum for the warm water and the mean rounded down for the residents. Example: a
        900 s run of a week reads the week's 10 080 minutes and averages them to 672 timesteps.

        This runs on every read, of a profile just computed and of one from the cache alike, which is why the cache
        can serve every window and timestep of a year.

        Args:
            minute_profile: the household's profile as the LoadProfileGenerator wrote it.

        Returns:
            OccupancySeries: one value per timestep of the run.

        Raises:
            ValueError: if the run's timestep is not a whole number of minutes.
            hisim.window_offset.WindowOffsetError: if the profile does not cover the run's window.
        """
        # A timestep averages whole minutes of the profile: at 90 s a timestep would read one minute, and a
        # day of 960 timesteps 16 hours of the household, without an error.
        if self.my_simulation_parameters.seconds_per_timestep % lpg_local_time.LpgLocalTime.SECONDS_PER_MINUTE:
            raise ValueError(
                f"The LoadProfileGenerator profile of {self.component_name} has one value per minute, so the "
                "run's timestep must be a whole multiple of a minute; "
                f"{self.my_simulation_parameters.seconds_per_timestep} s is not."
            )
        simulation_time_span = self.my_simulation_parameters.end_date - self.my_simulation_parameters.start_date
        minutes_per_timestep = int(self.my_simulation_parameters.seconds_per_timestep / 60)
        steps_desired = int(
            simulation_time_span.days * 24 * (3600 / self.my_simulation_parameters.seconds_per_timestep)
        )
        steps_desired_in_minutes = steps_desired * minutes_per_timestep

        initial_data = lpg_local_time.LpgLocalTime.window_in_standard_time(
            minute_values=self.minute_values_of(minute_profile),
            first_label=lpg_local_time.LpgLocalTime.first_label_in_year(
                minute_profile.start_time, self.my_simulation_parameters.year
            ),
            window_start=self.my_simulation_parameters.start_date,
            window_minutes=steps_desired_in_minutes,
            source=(
                f"The LoadProfileGenerator profile of {self.component_name} "
                f"({self.utsp_config.data_acquisition_mode.name})"
            ),
        )

        electricity_consumption = initial_data["electricity_consumption"].tolist()
        heating_by_residents = initial_data["heating_by_residents"].tolist()
        number_of_residents = initial_data["number_of_residents"].tolist()
        water_consumption = initial_data["water_consumption"].tolist()
        heating_by_devices = initial_data["inner_device_heat_gains"].tolist()

        # average data, when time resolution of inputs is coarser than time resolution of simulation
        if minutes_per_timestep > 1:
            # power needs averaging, not sum
            electricity_consumption = [
                sum(electricity_consumption[n : n + minutes_per_timestep]) / minutes_per_timestep
                for n in range(0, steps_desired_in_minutes, minutes_per_timestep)
            ]
            heating_by_devices = [
                sum(heating_by_devices[n : n + minutes_per_timestep]) / minutes_per_timestep
                for n in range(0, steps_desired_in_minutes, minutes_per_timestep)
            ]
            water_consumption = [
                sum(water_consumption[n : n + minutes_per_timestep])
                for n in range(0, steps_desired_in_minutes, minutes_per_timestep)
            ]
            heating_by_residents = [
                sum(heating_by_residents[n : n + minutes_per_timestep]) / minutes_per_timestep
                for n in range(0, steps_desired_in_minutes, minutes_per_timestep)
            ]
            number_of_residents = [
                int(sum(number_of_residents[n : n + minutes_per_timestep]) / minutes_per_timestep)
                for n in range(0, steps_desired_in_minutes, minutes_per_timestep)
            ]

        return OccupancySeries(
            electricity_consumption=electricity_consumption,
            heating_by_devices=heating_by_devices,
            water_consumption=water_consumption,
            heating_by_residents=heating_by_residents,
            number_of_residents=number_of_residents,
        )

    def common_start_time(self, activity_profiles: List[Dict[str, Any]]) -> str:
        """Return the wall-clock minute a household's profile starts at, the ``StartTime`` its activity files state.

        All files of a household come from one calculation and start together; the activity files say where.
        Example: the shipped CHR01 profile states ``2021-01-01T00:00:00``.

        Args:
            activity_profiles: the household's bodily-activity files, as read from their JSON.

        Returns:
            str: the stated ``StartTime``, ISO 8601 without a time zone.

        Raises:
            ValueError: if the files state different starts, so the household's profile has no one start.
        """
        start_times = {profile["StartTime"] for profile in activity_profiles}
        if len(start_times) != 1:
            raise ValueError(
                f"The bodily-activity files of {self.component_name} state different starts, {sorted(start_times)}; "
                "the files of one household come from one LoadProfileGenerator calculation and start together."
            )
        return str(start_times.pop())

    @staticmethod
    def write_minute_profiles(cache_entry: CacheEntry, minute_profiles: List[LpgMinuteProfile]) -> None:
        """Write the minute profiles of one request into its cache entry, as a JSON list of their fields.

        The entry lands atomically, with its ``.meta`` file recording the key material it was looked up under
        (:meth:`hisim.caching.CacheEntry.writing`). That material names the configuration the request was made with,
        so the metadata names what produced the profiles (roadmap/pylpg_flakiness.md F5). JSON keeps every float
        exact, so :meth:`read_cached_minute_profiles` gives back the same values bit for bit.

        Args:
            cache_entry: the entry, from :meth:`minute_profile_cache_entries_of_households`.
            minute_profiles: the profiles of the request's households, in its order.
        """
        with cache_entry.writing() as temporary_cache_filepath:
            with open(temporary_cache_filepath, "w", encoding="utf-8") as file:
                json.dump([vars(minute_profile) for minute_profile in minute_profiles], file)
        log.information(f"Caching of lpg utsp results finished. Cache filepath is {cache_entry.path}.")

    @staticmethod
    def read_cached_minute_profiles(cache_filepath: str) -> List[LpgMinuteProfile]:
        """Read the minute profiles of one request from its cache entry, as :meth:`write_minute_profiles` wrote them.

        Args:
            cache_filepath: the path of a validated entry.

        Returns:
            List[LpgMinuteProfile]: the profiles of the request's households, in its order.

        Raises:
            TypeError: if the entry's fields are not those of :class:`LpgMinuteProfile`.
        """
        with open(cache_filepath, encoding="utf-8") as file:
            households = json.load(file)
        return [LpgMinuteProfile(**household) for household in households]

    def read_result_json(self, result_file: str) -> Union[Dict[str, Any], List[Any]]:
        """Read one of the LoadProfileGenerator's JSON result files: from its content (UTSP) or from its path.

        A file the source did not produce is an empty string and reads as an empty dictionary, which is how a
        household without a car or without flexibility events shows. Single quotes are read as double quotes.

        Args:
            result_file: the file's content in the UTSP mode, its path otherwise, or an empty string.

        Returns:
            Union[Dict[str, Any], List[Any]]: the file's JSON object or list, or an empty dictionary.
        """
        json_acceptable_string = result_file.replace("'", '"')
        if json_acceptable_string == "":
            return {}
        if self.utsp_config.data_acquisition_mode == LpgDataAcquisitionMode.USE_UTSP:
            content: Union[Dict[str, Any], List[Any]] = json.loads(json_acceptable_string)
            return content
        with open(json_acceptable_string, encoding="utf-8") as json_file:
            content = json.load(json_file)
        return content

    def get_component_kpi_entries(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        occupancy_total_electricity_consumption_in_kilowatt_hour: Optional[float] = None
        occupancy_total_water_consumption_in_liter: Optional[float] = None
        occupancy_total_water_consumption_in_kwh: Optional[float] = None
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.ElectricalPowerConsumption and output.unit == lt.Units.WATT:
                    occupancy_total_electricity_consumption_in_watt_series = postprocessing_results.iloc[:, index]
                    occupancy_total_electricity_consumption_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=occupancy_total_electricity_consumption_in_watt_series,
                            time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                if output.field_name == self.WaterConsumption and output.unit == lt.Units.LITER:
                    occupancy_total_water_consumption_in_liter = round(sum(postprocessing_results.iloc[:, index]), 1)
                    # calculate warm water energy consumption
                    if occupancy_total_water_consumption_in_liter is not None:
                        # https://www.internetchemie.info/chemie-lexikon/daten/w/wasser-dichtetabelle.php
                        density_water_at_40_degree_celsius_in_kg_per_liter = 0.992
                        drain_water_temperature = HouseholdWarmWaterDemandConfig.freshwater_temperature
                        warm_water_temperature = (
                            HouseholdWarmWaterDemandConfig.ww_temperature_demand
                            - HouseholdWarmWaterDemandConfig.temperature_difference_hot
                        )
                        specific_heat_capacity_of_water_in_watthour_per_kilogram_per_celsius = (
                            PhysicsConfig.get_properties_for_energy_carrier(
                                energy_carrier=lt.LoadTypes.WATER
                            ).specific_heat_capacity_in_watthour_per_kg_per_kelvin
                        )
                        occupancy_total_water_consumption_in_kwh = (
                            1e-3
                            * specific_heat_capacity_of_water_in_watthour_per_kilogram_per_celsius
                            * occupancy_total_water_consumption_in_liter
                            * density_water_at_40_degree_celsius_in_kg_per_liter
                            * (warm_water_temperature - drain_water_temperature)
                        )

        # make kpi entry
        occupancy_total_electricity_consumption_entry = KpiEntry(
            name="Residents' total electricity consumption",
            unit="kWh",
            value=occupancy_total_electricity_consumption_in_kilowatt_hour,
            tag=KpiTagEnumClass.RESIDENTS,
            description=self.component_name,
        )
        occupancy_total_water_consumption_entry = KpiEntry(
            name="Residents' total warm water consumption",
            unit="l",
            value=occupancy_total_water_consumption_in_liter,
            tag=KpiTagEnumClass.RESIDENTS,
            description=self.component_name,
        )
        occupancy_total_water_consumption_energy_entry = KpiEntry(
            name="Residents' total warm water energy consumption",
            unit="kWh",
            value=occupancy_total_water_consumption_in_kwh,
            tag=KpiTagEnumClass.RESIDENTS,
            description=self.component_name,
        )

        list_of_kpi_entries = [
            occupancy_total_electricity_consumption_entry,
            occupancy_total_water_consumption_entry,
            occupancy_total_water_consumption_energy_entry,
        ]
        return list_of_kpi_entries

    @staticmethod
    def get_cost_capex(
        config: UtspLpgConnectorConfig, simulation_parameters: SimulationParameters
    ) -> cp.CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = cp.CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, snd write total energy consumption to component-config.

        No electricity costs for components except for Electricity Meter,
        because part of electricity consumption is feed by PV
        """
        consumption_in_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.config.component_id.name
                and output.load_type == lt.LoadTypes.ELECTRICITY
                and output.field_name == self.ElectricalPowerConsumption
                and output.unit == lt.Units.WATT
            ):
                occupancy_total_electricity_consumption_in_watt_series = postprocessing_results.iloc[:, index]
                consumption_in_kwh = KpiHelperClass.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=occupancy_total_electricity_consumption_in_watt_series,
                    time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                )
        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
        )
        co2_per_unit = emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.electricity_costs_in_euro_per_kwh
        co2_per_simulated_period_in_kg = consumption_in_kwh * co2_per_unit
        opex_energy_cost_per_simulated_period_in_euro = consumption_in_kwh * euro_per_unit
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=consumption_in_kwh,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.RESIDENTS,
        )

        return opex_cost_data_class
