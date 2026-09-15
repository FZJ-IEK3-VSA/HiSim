"""Turning a pending sizing law into a number, and the demand estimates the laws are built on.

A measure that asks for "a battery covering two days" has not said how many kilowatt hours that
is: the answer depends on how much electricity this dwelling uses in a day, which depends on the
occupancy profile, on whether the heating is a heat pump, and on whether a car is charged at home.
The measure layer therefore records a :class:`~hisim.renovisor.effects.LawRequest` and this module
resolves it, once, immediately before the energy-system file is written::

    estimator = PreRunDemandEstimator(inventory, base_file_key, match, parameters)
    LawResolver.resolve(law, inventory, estimator)
    # LawResult(value=25.2, rule='BATTERY_FROM_DAYS_TO_COVER: 2 day(s) x (8.4 household
    #                             + 4.2 heat pump + 0 vehicle) kWh/day = 25.2 kWh')

Two things about the design matter. The **rule string is part of the result**, not a comment: the
translation report has to show the arithmetic so that a caller can see 25.3 kWh was not measured
but computed from three estimates (requirement R7, decision Q12). And the estimator is a
**protocol**, so the trace page and the unit tests can state three numbers instead of loading an
occupancy profile, while a real calculation reads the profile the run itself will use.

Not every sizing that looks like a law is one. ``PV_SHARE_OF_ROOF`` is deliberately absent: the
measure writes the share into the inventory and the recorded base file's own ``rooftop`` law turns
it into an installed power, so HiSim sizes the array with the code that already exists rather than
with a second implementation here (decision Q18).
"""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Protocol, Tuple

from hisim import utils
from hisim.components.loadprofilegenerator_utsp_connector import (
    LpgDataAcquisitionMode,
    UtspLpgConnector,
    UtspLpgConnectorConfig,
)
from hisim.renovisor.base_files import BaseFileKey
from hisim.renovisor.effects import LawRequest, SizingLaw
from hisim.renovisor.envelope import EnvelopePaths, InventoryThenTabula
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.occupancy import HouseholdMatch
from hisim.renovisor.vocabulary import HeatGenerator
from hisim.simulationparameters import SimulationParameters


class DemandEstimator(Protocol):
    """What a sizing law is allowed to know about how much electricity a dwelling will use.

    Three daily figures, each in kilowatt hours per day, because that is the unit every law this
    layer has is expressed in and because a daily figure is the coarsest thing that can still size
    a store. An implementation is free to compute them however it likes — from a loaded occupancy
    profile, from a typology row, or from three numbers a test states — as long as it answers the
    same way twice (requirement R10).
    """

    def household_electricity_in_kwh_per_day(self) -> float:
        """Return the residents' own electricity use, per day, excluding heating and vehicles."""

    def heat_pump_electricity_in_kwh_per_day(self) -> float:
        """Return the electricity the heating draws per day; zero when the heating is not electric."""

    def vehicle_electricity_in_kwh_per_day(self) -> float:
        """Return the electricity the dwelling's electric vehicles draw at home, per day."""


class HeatPumpTerm:
    """Whether the heat-pump term of a sizing law contributes anything at all.

    A gas boiler draws no electricity worth sizing a battery against, so the term is not merely
    small for a boiler house, it is zero — and getting that wrong would oversize every battery in
    the seven of eleven base files that burn something. The rule lives here so that both the real
    estimator and the stated-number one used by the trace and the tests apply the same one.
    """

    #: The generators whose heating demand reaches the electricity meter.
    ELECTRIC_GENERATORS: ClassVar[Tuple[HeatGenerator, ...]] = (
        HeatGenerator.HEAT_PUMP,
        HeatGenerator.HYBRID_HEAT_PUMP,
        HeatGenerator.ELECTRIC_HEATING,
    )

    @classmethod
    def applies_to(cls, generator: HeatGenerator) -> bool:
        """Return whether a dwelling heated by *generator* draws heating electricity.

        Args:
            generator: The heat generator the dwelling ends up with, after every measure.

        Returns:
            ``True`` for the three electric generators, ``False`` for the eight that burn a fuel
            or take heat from a network.
        """
        return generator in cls.ELECTRIC_GENERATORS


@dataclass(frozen=True)
class StaticDemandEstimator:
    """Three stated daily demands, for a trace, a test or a worked example.

    It exists so that the translation map's trace page and the law tests can show the arithmetic
    of a law without loading an occupancy profile or reading a typology table — both of which
    would make a documentation page depend on a cache and a test depend on a data file. Every page
    that uses it says so beside the numbers, because they are stated, not simulated.

    Args:
        household_in_kwh_per_day: The residents' own electricity use per day.
        heat_pump_in_kwh_per_day: The heating's electricity use per day, as if the dwelling were
            heated electrically; :class:`HeatPumpTerm` decides whether it is handed on.
        vehicle_in_kwh_per_day: The vehicles' charging energy per day.
        generator: The heat generator the dwelling ends up with.
    """

    household_in_kwh_per_day: float
    heat_pump_in_kwh_per_day: float
    vehicle_in_kwh_per_day: float
    generator: HeatGenerator = HeatGenerator.HEAT_PUMP

    def household_electricity_in_kwh_per_day(self) -> float:
        """Return the stated household demand."""
        return self.household_in_kwh_per_day

    def heat_pump_electricity_in_kwh_per_day(self) -> float:
        """Return the stated heating demand, or zero when this dwelling does not heat electrically."""
        return self.heat_pump_in_kwh_per_day if HeatPumpTerm.applies_to(self.generator) else 0.0

    def vehicle_electricity_in_kwh_per_day(self) -> float:
        """Return the stated vehicle demand."""
        return self.vehicle_in_kwh_per_day


class TabulaHeatingDemand:
    """The annual space-heating need of one TABULA archetype, in kilowatt hours per square metre.

    TABULA's ``q_h_nd`` column is the archetype's net heating need as built, which is the only
    estimate of a dwelling's heating demand available before the simulation has run — and the
    simulation is exactly what the estimate is needed before. The column is read from the same
    processed table the ``Building`` component reads, so the estimate and the simulation are at
    least talking about the same archetype.
    """

    #: The column holding the net heating need, in kWh/(m2·a).
    COLUMN: ClassVar[str] = "q_h_nd"

    #: The column carrying the typology code of each row.
    CODE_COLUMN: ClassVar[str] = "Code_BuildingVariant"

    #: The processed TABULA table is Latin-1, semicolon-delimited, with a decimal comma.
    ENCODING: ClassVar[str] = "latin-1"
    DELIMITER: ClassVar[str] = ";"

    @classmethod
    def for_code(cls, building_code: str) -> float:
        """Return one archetype's net heating need in kWh/(m2·a).

        Args:
            building_code: A TABULA code, e.g. ``"IE.N.SFH.06.Gen.ReEx.001.001"``.

        Returns:
            The value of the ``q_h_nd`` column for that row.

        Raises:
            KeyError: When the processed table has no row with that code.
        """
        return cls._table()[building_code]

    @classmethod
    @lru_cache(maxsize=1)
    def _table(cls) -> Dict[str, float]:
        """Parse the processed TABULA table into ``code -> q_h_nd``, once per process."""
        table: Dict[str, float] = {}
        with open(utils.HISIMPATH["housing"], encoding=cls.ENCODING) as csv_file:
            for row in csv.DictReader(csv_file, delimiter=cls.DELIMITER):
                code = (row.get(cls.CODE_COLUMN) or "").strip()
                if code:
                    table[code] = cls._parse_decimal(row.get(cls.COLUMN))
        return table

    @classmethod
    def _parse_decimal(cls, raw: Any) -> float:
        """Parse a TABULA numeric cell, accepting the German decimal comma; blanks become 0."""
        try:
            return float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0


class PreRunDemandEstimator:
    """The three daily demands of one dwelling, computed before its simulation starts.

    Each of the three comes from a different place, and each is honest about being an estimate:

    *household* — the LoadProfileGenerator profile of the matched household, loaded through
    :meth:`~hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector.
    electricity_consumption_of` with the configuration and the simulation parameters the run
    itself will use, so the profile is loaded once and the run finds it in the cache (decision
    Q20). The daily figure is the mean over the simulated period, which is the annual sum over 365
    exactly when the run covers a year and the period's own mean otherwise; the rule string says
    which period it was.

    *heat pump* — the TABULA archetype's net heating need times the conditioned floor area,
    divided by :attr:`SEASONAL_PERFORMANCE_FACTOR` and by 365. Zero unless the dwelling ends up
    heated electrically (:class:`HeatPumpTerm`).

    *vehicle* — the annual mileage times the vehicle's consumption. The contract does not carry a
    mileage yet (pending path A5), so the term is zero today and the rule says so rather than
    silently contributing nothing.

    Everything is computed lazily and remembered, because a package without a battery needs none
    of it and loading an occupancy profile is the most expensive thing this layer does.

    Args:
        inventory: The post-measure inventory.
        base_file_key: Which system the dwelling ends up with, for the heat-pump term.
        household_match: The occupancy the run will simulate.
        simulation_parameters: The parameters of the run itself.
        occupancy_component_name: The recorded key of the occupancy component, which is part of
            the profile's cache key and therefore has to be the run's own.
        data_acquisition_mode: Where the profile comes from; the recorded files' local generator.
        cache_directory: Where profiles are cached; the simulation parameters' own when omitted.
    """

    #: PROVISIONAL -- assumed seasonal performance of an air/water heat pump; replace by the hplib
    #: model's own figure once the run has happened. It is the one invented number in this layer,
    #: and it only ever scales a battery's size, never a simulated result.
    SEASONAL_PERFORMANCE_FACTOR: ClassVar[float] = 3.0

    #: Days in the year the annual figures are divided by.
    DAYS_PER_YEAR: ClassVar[float] = 365.0

    #: Watt seconds in a kilowatt hour, for turning a power profile into an energy.
    WATT_SECONDS_PER_KILOWATT_HOUR: ClassVar[float] = 3_600_000.0

    #: Inventory paths this estimator reads.
    FLOOR_AREA: ClassVar[str] = "building_config.general.conditioned_floor_area_m2"
    ELECTRIC_VEHICLES: ClassVar[str] = "energy_system_config.vehicles.electric_vehicles"
    VEHICLE_CONSUMPTION: ClassVar[str] = "consumption_in_kwh_per_km"

    #: The per-vehicle annual mileage the contract does not carry yet (pending path A5). Named so
    #: that the contract PR of step 6 has one place to point at.
    VEHICLE_MILEAGE_PATH: ClassVar[str] = "energy_system_config.vehicles.electric_vehicles[].km_per_year"

    def __init__(
        self,
        inventory: Inventory,
        base_file_key: BaseFileKey,
        household_match: HouseholdMatch,
        simulation_parameters: SimulationParameters,
        occupancy_component_name: str = "UTSPConnector",
        data_acquisition_mode: LpgDataAcquisitionMode = LpgDataAcquisitionMode.USE_LOCAL_LPG,
        cache_directory: Optional[Path] = None,
    ) -> None:
        """Store what the three estimates are computed from; nothing is computed here."""
        self._inventory = inventory
        self._base_file_key = base_file_key
        self._match = household_match
        self._parameters = simulation_parameters
        self._occupancy_component_name = occupancy_component_name
        self._data_acquisition_mode = data_acquisition_mode
        self._cache_directory = cache_directory
        self._household: Optional[float] = None
        self._heat_pump: Optional[float] = None
        self._vehicle: Optional[float] = None
        self._notes: List[str] = []

    @property
    def notes(self) -> Tuple[str, ...]:
        """Return what each estimate was read from, in the order the estimates were asked for.

        The law's own ``rule`` names the three numbers; these say where each came from, which is
        what a reader needs to judge whether a battery was sized against anything real.
        """
        return tuple(self._notes)

    def household_electricity_in_kwh_per_day(self) -> float:
        """Return the residents' mean daily electricity use, from the occupancy profile.

        Returns:
            Kilowatt hours per day, the mean over the simulated period.

        Raises:
            ValueError: If the configured profile source cannot serve the matched household.
        """
        if self._household is None:
            watts = UtspLpgConnector.electricity_consumption_of(
                self._occupancy_config(), self._parameters
            )
            energy_in_kwh = (
                sum(watts)
                * self._parameters.seconds_per_timestep
                / self.WATT_SECONDS_PER_KILOWATT_HOUR
            )
            self._household = energy_in_kwh / max(self._simulated_days(), 1.0)
            self._notes.append(
                f"household: the LoadProfileGenerator profile of '{self._match.household.Name}', "
                f"{energy_in_kwh:.4g} kWh over {self._simulated_days():g} simulated day(s)"
            )
        return self._household

    def heat_pump_electricity_in_kwh_per_day(self) -> float:
        """Return the heating's mean daily electricity use, from the TABULA archetype.

        Returns:
            Kilowatt hours per day; ``0.0`` when the dwelling does not heat electrically.

        Raises:
            KeyError: When the processed TABULA table has no row for the selected archetype.
            TabulaLookupError: When no archetype exists for the country and building type.
        """
        if self._heat_pump is None:
            if not HeatPumpTerm.applies_to(self._base_file_key.generator):
                self._heat_pump = 0.0
                self._notes.append(
                    f"heat pump: zero, because the dwelling is heated by "
                    f"{self._base_file_key.generator.value}"
                )
            else:
                archetype = InventoryThenTabula(self._inventory)
                need_in_kwh_per_m2 = TabulaHeatingDemand.for_code(archetype.building_code)
                area = float(self._inventory.get(self.FLOOR_AREA) or 0.0)
                self._heat_pump = (
                    need_in_kwh_per_m2 * area / self.SEASONAL_PERFORMANCE_FACTOR / self.DAYS_PER_YEAR
                )
                self._notes.append(
                    f"heat pump: TABULA row {archetype.building_code} q_h_nd "
                    f"{need_in_kwh_per_m2:g} kWh/m2a x {area:g} m2 / "
                    f"{self.SEASONAL_PERFORMANCE_FACTOR:g} (PROVISIONAL seasonal performance "
                    f"factor) / {self.DAYS_PER_YEAR:g} d"
                )
        return self._heat_pump

    def vehicle_electricity_in_kwh_per_day(self) -> float:
        """Return the electric vehicles' mean daily charging energy.

        Returns:
            Kilowatt hours per day; ``0.0`` while the contract carries no annual mileage.
        """
        if self._vehicle is None:
            total = 0.0
            missing_mileage = False
            for vehicle in self._vehicles():
                consumption = vehicle.get("consumption_in_kwh_per_km")
                mileage = vehicle.get("km_per_year")
                if isinstance(mileage, (int, float)) and isinstance(consumption, (int, float)):
                    total += float(mileage) * float(consumption) / self.DAYS_PER_YEAR
                else:
                    missing_mileage = True
            self._vehicle = total
            if missing_mileage:
                self._notes.append(
                    f"vehicle: zero, because the contract carries no {self.VEHICLE_MILEAGE_PATH} "
                    "yet (pending path A5)"
                )
            elif not self._vehicles():
                self._notes.append("vehicle: zero, because the dwelling charges no electric vehicle")
            else:
                self._notes.append("vehicle: annual mileage times consumption, over 365 days")
        return self._vehicle

    def _vehicles(self) -> Tuple[Dict[str, object], ...]:
        """Return the inventory's electric vehicles as dictionaries, empty when it has none."""
        raw = self._inventory.get(self.ELECTRIC_VEHICLES)
        if not isinstance(raw, list):
            return ()
        return tuple(item for item in raw if isinstance(item, dict))

    def _simulated_days(self) -> float:
        """Return how many days the run covers, as a float, for turning an energy into a rate."""
        span = self._parameters.end_date - self._parameters.start_date
        return span.total_seconds() / (24 * 3600)

    def _occupancy_config(self) -> UtspLpgConnectorConfig:
        """Build the occupancy configuration the parametrised file will carry.

        It has to be the same configuration, field for field, or the profile is cached under a
        different key and the run loads it a second time. That is why the component's recorded key
        and the acquisition mode are arguments of this class rather than assumptions in it.

        Returns:
            The configuration, with the cache directory of the calculation when one was given.
        """
        config = UtspLpgConnectorConfig.for_household(
            self._occupancy_component_name,
            household=self._match.household,
            data_acquisition_mode=self._data_acquisition_mode,
            travel_route_set=self._match.travel_route_set,
        )
        if self._cache_directory is not None:
            config.cache_dir_path = str(self._cache_directory)
        return config


@dataclass(frozen=True)
class LawResult:
    """One resolved sizing law: the number, and the arithmetic that produced it.

    Args:
        value: The value to write into the inventory, in the inventory's own unit.
        rule: The formula with its inputs filled in, e.g. ``"BATTERY_FROM_DAYS_TO_COVER: 2 day(s)
            x (8.4 household + 4.2 heat pump + 0.0 vehicle) kWh/day"``. The translation report
            carries it verbatim.
    """

    value: float
    rule: str


class LawResolver:
    """Resolves the sizing laws the measure layer leaves pending.

    One class for what is today one law, because the shape is what matters: a law takes the
    request's own argument, the post-measure inventory and a :class:`DemandEstimator`, and returns
    a number together with the sentence that explains it. A second law is a second method and a
    second table entry, not a second mechanism.
    """

    @classmethod
    def resolve(cls, law: LawRequest, inventory: Inventory, estimator: DemandEstimator) -> LawResult:
        """Resolve one law request into a value.

        Args:
            law: The law and the argument the measure recorded, e.g. two days to cover.
            inventory: The post-measure inventory, for laws that read the dwelling.
            estimator: Where the daily demands come from.

        Returns:
            The :class:`LawResult`.

        Raises:
            NotImplementedError: For a law this resolver has no method for, which is a programming
                error rather than a request error: the measure layer only ever records laws from
                :class:`~hisim.renovisor.effects.SizingLaw`.
        """
        del inventory
        if law.law is SizingLaw.BATTERY_FROM_DAYS_TO_COVER:
            return cls.battery_from_days_to_cover(law.argument, estimator)
        raise NotImplementedError(
            f"{law.law.value} has no resolver; decision Q18 keeps PV sizing in the base file's own "
            "rooftop law, so no other law reaches here."
        )

    @classmethod
    def battery_from_days_to_cover(cls, days: float, estimator: DemandEstimator) -> LawResult:
        """Size a battery from the number of days of demand it should cover (decision Q12).

        The capacity is the days times the sum of the three daily demands: the residents' own
        electricity, the heating's when the dwelling heats electrically, and the vehicles'. It is
        a gross sizing rule and nothing more — no depth of discharge, no round-trip efficiency, no
        seasonal profile — which is why the report line is ``approximated`` and carries this rule.

        Example::

            LawResolver.battery_from_days_to_cover(2.0, StaticDemandEstimator(8.4, 4.2, 0.0))
            # LawResult(value=25.2, rule='BATTERY_FROM_DAYS_TO_COVER: 2 day(s) x (8.4 household + ...')

        Args:
            days: How many days of demand the battery should cover.
            estimator: Where the three daily demands come from.

        Returns:
            The capacity in kilowatt hours, with the arithmetic in the rule.
        """
        household = estimator.household_electricity_in_kwh_per_day()
        heat_pump = estimator.heat_pump_electricity_in_kwh_per_day()
        vehicle = estimator.vehicle_electricity_in_kwh_per_day()
        total = household + heat_pump + vehicle
        return LawResult(
            value=days * total,
            rule=(
                f"{SizingLaw.BATTERY_FROM_DAYS_TO_COVER.value}: {days:g} day(s) x "
                f"({household:.4g} household + {heat_pump:.4g} heat pump + {vehicle:.4g} vehicle) "
                f"kWh/day = {days * total:.4g} kWh"
            ),
        )


class LawPaths:
    """Where the values of the pending laws land, so the parametriser and the trace agree.

    A pending law is keyed by the inventory path the measure would have written, which is the same
    path the bindings resolve to a component and a field. Naming that here keeps the parametriser
    from having to know anything about which law wrote which field.
    """

    #: The envelope block, which no law writes; named so the trace can say so.
    ENVELOPE: ClassVar[str] = EnvelopePaths.ENVELOPE_DETAILS

    #: The battery capacity the days-to-cover law produces.
    BATTERY_CAPACITY: ClassVar[str] = "energy_system_config.battery_storage.capacity_in_kwh"
