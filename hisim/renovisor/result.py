"""``result.json``: everything a caller needs to read one calculation's answer and judge it.

The three files step 5 writes say what was *asked* and what was *built*; this one says what came
out. It is assembled after the simulation from four sources, none of which is a new computation:
the run's own KPI export, the lifecycle cost engine's exports, the insulation-material table, and
the realized energy-system record -- plus the contract's own schema, which supplies the constants
for the fields no model stands behind (decision A12).

What the document is for, beyond the numbers: a reader has to be able to tell a simulated figure
from a constant and an extrapolation from a measurement without asking anybody. So every leaf
carries its provenance and its source (decision Q21), every annual figure carries the period it
was extrapolated from (requirement A14), the weather the run used is named (requirement A4), the
translator version, the contract revision and the container image digest say which rules produced
it (requirement R14, decision Q26), and every contract field that could not be produced is listed
under ``missing`` with a reason rather than being filled with a plausible zero (decision R8)::

    {
      "translator_version": "...", "calculation_version": {"image_digest": null},
      "contract": {"commit": "...", "files": {...}},
      "base_file": "household_heatpump_building_sizer.grouped.energy_system.yaml",
      "weather_basis": {"location": "IE", "dataset": "NSRDB_15MIN", "year": 2019},
      "period": {"start": "...", "end": "...", "fraction_of_year": 0.0027},
      "kpis": {...},
      "missing": [{"field": "costs.grant_in_euro", "reason": "..."}]
    }

A failure inside the builder is a crash, not a degraded payload: the run's records and its report
are already on disk and stay there, the outcome becomes ``failed`` with reason code
``RESULT_DERIVATION_FAILED``, and nobody is handed a ``result.json`` that is missing a field for a
reason it does not state.
"""

import re
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional

from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.apply import AppliedPackage
from hisim.renovisor.costs import CostBuilder
from hisim.renovisor.kpis import KpiBuilder, KpiDocument, LifecycleCo2
from hisim.renovisor.layers import ElementAreas, EnvelopeLayers
from hisim.renovisor.request import House, Request
from hisim.renovisor.translate import TranslatedSystem
from hisim.renovisor.provenance import MissingField, Period
from hisim.simulationparameters import SimulationParameters


class WeatherBasis:
    """Which weather year the calculation was run against (requirement A4).

    A dwelling's simulated energy demand is a statement about one weather year, and two results
    computed against different years are not comparable however carefully everything else was
    matched. So the year, the data set and the location travel with the result. All three are read
    off the run itself -- the constructor argument the parametriser wrote and the ``WeatherConfig``
    the run resolved -- rather than restated here, so the document cannot claim a weather the
    simulation did not use.
    """

    #: The component whose configuration carries the weather.
    COMPONENT: ClassVar[str] = "Weather"

    #: The constructor argument naming the country the location was chosen for.
    LOCATION_ARGUMENT: ClassVar[str] = "location"

    #: The resolved config key naming the station.
    STATION_KEY: ClassVar[str] = "location"

    #: The resolved config key naming the data set family.
    DATASET_KEY: ClassVar[str] = "data_source"

    #: The resolved config key holding the weather file.
    SOURCE_PATH_KEY: ClassVar[str] = "source_path"

    #: The four-digit year at the end of an NSRDB file stem, e.g. ``…_-6.26_2019.csv``.
    YEAR_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(r"_(\d{4})\.csv$")

    @classmethod
    def of(cls, translated: Any, realized: Any) -> Dict[str, Any]:
        """Return the weather block of ``result.json``.

        Args:
            translated: The translated energy-system model, whose ``Weather`` constructor
                names the country the location was chosen for.
            realized: The realized record's model, whose ``Weather`` config holds the station, the
                data set and the file that was read; ``None`` when the record could not be read.

        Returns:
            ``{"location": …, "station": …, "dataset": …, "year": …}``, with ``None`` for anything
            neither model states.
        """
        arguments = cls._constructor_arguments(translated)
        config = cls._config(realized)
        source_path = config.get(cls.SOURCE_PATH_KEY)
        match = cls.YEAR_PATTERN.search(str(source_path)) if source_path is not None else None
        return {
            "location": arguments.get(cls.LOCATION_ARGUMENT),
            "station": config.get(cls.STATION_KEY),
            "dataset": config.get(cls.DATASET_KEY),
            "year": int(match.group(1)) if match is not None else None,
        }

    @classmethod
    def _constructor_arguments(cls, model: Any) -> Mapping[str, Any]:
        """Return the ``Weather`` constructor's arguments of a model, or an empty mapping."""
        entry = cls._entry(model)
        constructor = getattr(entry, "constructor", None) if entry is not None else None
        arguments = getattr(constructor, "arguments", None)
        return arguments if isinstance(arguments, Mapping) else {}

    @classmethod
    def _config(cls, model: Any) -> Mapping[str, Any]:
        """Return the ``Weather`` config block of a model, or an empty mapping."""
        entry = cls._entry(model)
        config = getattr(entry, "config", None) if entry is not None else None
        return config if isinstance(config, Mapping) else {}

    @classmethod
    def _entry(cls, model: Any) -> Any:
        """Return one model's ``Weather`` component entry, or ``None``."""
        components = getattr(model, "components", None)
        if not isinstance(components, Mapping):
            return None
        return components.get(cls.COMPONENT)


class RealizedRecord:
    """The realized energy-system record, read back for the two facts the payload needs from it.

    The record is the system the simulation actually built, with every sized value resolved, and
    it is written before the first timestep so it is on disk whatever the run then did. Two things
    are read out of it: the ``Building`` element areas the envelope figures multiply by, and the
    resolved weather. Reading it back rather than carrying the numbers along from the parametriser
    is deliberate -- what the payload describes is the run, and the record is the run's own
    account of itself.
    """

    #: The record ``write_records`` produces; the payload reads the first of the three.
    FILE_NAME: ClassVar[str] = "realized.energy_system.yaml"

    #: The component whose config carries the envelope areas.
    BUILDING_COMPONENT: ClassVar[str] = "Building"

    @classmethod
    def load(cls, output_directory: Path) -> Any:
        """Read the realized record, or return ``None`` when it cannot be read.

        A record that will not load does not fail the payload: the areas then come from the home
        inventory and the weather block carries nulls, both of which the sources say.

        Args:
            output_directory: The calculation's output directory.

        Returns:
            The parsed ``EnergySystemFile``, or ``None``.
        """
        path = output_directory / cls.FILE_NAME
        if not path.is_file():
            return None
        try:
            from hisim.energy_system.loader import load_energy_system

            return load_energy_system(path)
        except Exception:  # pylint: disable=broad-except  # an unreadable record is not a crash
            return None

    @classmethod
    def building_config(cls, model: Any) -> Mapping[str, Any]:
        """Return the ``Building`` config of a record, or an empty mapping when it has none."""
        components = getattr(model, "components", None)
        if not isinstance(components, Mapping):
            return {}
        entry = components.get(cls.BUILDING_COMPONENT)
        config = getattr(entry, "config", None) if entry is not None else None
        return config if isinstance(config, Mapping) else {}


class ResultBuilder:
    """Assembles ``result.json`` from one finished calculation.

    Everything it needs is either handed in or is in the output directory, and none of it is a
    simulation: the builder reads files the run wrote and tables the repository ships, and
    produces one document. That is what makes it testable without a simulation and re-runnable
    against an archived output directory.

    Args:
        request: The validated request, for the country the cost engine prices against.
        applied: What applying the package produced; the insulation layers come from it.
        translated: The translated system, for the base file it came from and the weather
            location the constructor named.
        output_directory: The calculation's output directory, holding the records and ``results/``.
        simulation_parameters: The parameters the run used, for the period and the emission-factor
            country.
        image_digest: The container image digest the caller passed in, or ``None`` (decision Q26).
        subsidy_catalogue_path: The country subsidy catalogue this run was configured with, or
            ``None`` when the country has none.
    """

    #: The file this builder writes, in the calculation's output directory.
    FILE_NAME: ClassVar[str] = "result.json"

    #: The subdirectory the simulation's own outputs are in.
    RESULTS_DIRECTORY: ClassVar[str] = "results"

    def __init__(
        self,
        request: Request,
        applied: AppliedPackage,
        translated: TranslatedSystem,
        output_directory: Path,
        simulation_parameters: SimulationParameters,
        image_digest: Optional[str] = None,
        subsidy_catalogue_path: Optional[Path] = None,
    ) -> None:
        """Store the inputs; nothing is read until :meth:`build`."""
        self._request = request
        self._applied = applied
        self._translated = translated
        self._output = Path(output_directory)
        self._parameters = simulation_parameters
        self._image_digest = image_digest
        self._catalogue = subsidy_catalogue_path

    def build(self, contract: Mapping[str, Any]) -> Dict[str, Any]:
        """Return the finished payload.

        Args:
            contract: The contract revision block, as the translation report writes it, so the
                two documents cannot disagree about which contract this HiSim speaks.

        Returns:
            The JSON-ready ``result.json`` document.
        """
        results = self._output / self.RESULTS_DIRECTORY
        realized = RealizedRecord.load(self._output)
        house = House.from_dict(self._applied.house)
        areas = ElementAreas.resolve(RealizedRecord.building_config(realized), house)
        layers = EnvelopeLayers.of(self._applied.layers, areas)
        period = Period.from_parameters(self._parameters)
        country = self._country()

        kpis = KpiBuilder(
            document=KpiDocument.load(results),
            period=period,
            layers=layers,
            lifecycle_co2=LifecycleCo2.load(results),
            country=country,
        ).build()
        # No figures, only the reasons: the money moved to economics_result.json in step 10, and
        # `missing` is where the payload says so field by field.
        costs = CostBuilder().build()

        missing: List[MissingField] = list(kpis.missing) + list(costs.missing)
        return {
            "translator_version": TRANSLATOR_VERSION,
            "calculation_version": {"image_digest": self._image_digest},
            "contract": dict(contract),
            "base_file": self._translated.base_file_name,
            "weather_basis": WeatherBasis.of(self._translated.model, realized),
            "period": period.to_json(),
            "kpis": kpis.values,
            "missing": [entry.to_json() for entry in missing],
        }

    def _country(self) -> str:
        """Return the country code the dwelling is in, as the request stated it.

        It is ``location.country``, which is what selects the cost engine's data files and the
        subsidy catalogue. The simulation parameters' ``country`` is a different thing -- the
        country whose legacy emission and price factors the KPI path uses -- and the two are
        read separately on purpose.

        Returns:
            The country code.
        """
        return self._request.country.value
