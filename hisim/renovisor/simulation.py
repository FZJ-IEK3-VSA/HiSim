"""The simulation parameters a RenoVisor calculation runs with, built in process.

They are backend constants, not request fields: the request describes a house, and how long and
how finely that house is simulated is a property of the release. ``build_energy_system`` takes
the object, so nothing is read from a ``*.simulation.yaml`` and no file has to be shipped beside
the request.

Two of the choices are worth their own sentence.

**The year is 2019**, because the NSRDB 15-minute weather files HiSim ships for Dublin, Malaga
and Amsterdam are 2019 files. Simulating a 2021 house against 2019 weather would be a silent
mismatch between the period a result names and the weather it was computed with.

**``country`` is the dwelling's**, and the legacy tables know it. ``SimulationParameters.country``
selects the *legacy* fuel prices, emission factors and device costs of
``hisim/components/configuration.py``, whose reviewed rows are Germany's and Austria's. Ireland
is registered there as a **placeholder** by ``PlaceholderCountryFactors``: every number is the
sentinel ``-1e9``, so a run completes and every legacy cost or CO2 KPI of an Irish run reads as
visible nonsense instead of as a German number nobody noticed. The payload never publishes one
of those leaves -- the operational CO2 comes from the lifecycle cost engine, which has its own
Irish data files -- and the mapping report says ``legacy_factors: placeholder`` so that a reader
of ``all_kpis.json`` knows why the cost and CO2 entries there are ``-1e9``-scaled.

The lifecycle engine is reached separately, by attaching an ``EconomicParameters`` for the same
country, which is what :class:`EconomicSetup` does.
"""

import datetime
import os
from enum import Enum
from pathlib import Path
from typing import ClassVar, Dict, List, Optional, Tuple

from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters


class Period(str, Enum):
    """How long one calculation runs.

    ``FULL_YEAR`` is what the backend calls; the two short periods exist for the verification
    harness and for the end-to-end tests, which need a run that finishes in seconds. All three
    start on the 1st of January, and the two short ones deliberately do *not* start mid-January
    as the frontend side's spec proposed: every profile-driven component of HiSim indexes its
    year-long profile by the timestep number, so a mid-January start reproduces the 1st of
    January anyway while labelling the result with the wrong dates.
    """

    FULL_YEAR = "full_year"
    ONE_DAY_15MIN = "one_day_15min"
    ONE_WEEK_15MIN = "one_week_15min"

    @property
    def days(self) -> Optional[int]:
        """Return how many days the period covers, or ``None`` for the whole year."""
        if self is Period.ONE_DAY_15MIN:
            return 1
        if self is Period.ONE_WEEK_15MIN:
            return 7
        return None


class SimulationSetup:
    """Builds the parameters of one calculation and points everything they decide at it.

    Every number here is a release constant with its reason in the module docstring; nothing is
    read from the request but the output directory the caller chose.
    """

    #: The weather year the shipped NSRDB 15-minute files cover.
    YEAR: ClassVar[int] = 2019

    #: The resolution every RenoVisor calculation runs at.
    SECONDS_PER_TIMESTEP: ClassVar[int] = 900

    #: The logging verbosity of a container run: warnings and above.
    LOGGING_LEVEL: ClassVar[int] = 3

    #: The post-processing options the result payload cannot be assembled without.
    #: ``COMPUTE_KPIS`` produces the KPI collection, ``WRITE_KPIS_TO_JSON`` writes it as
    #: ``all_kpis.json``, and ``COMPUTE_LIFECYCLE_COSTS`` produces the cost engine's exports
    #: (decision D-F). The legacy ``COMPUTE_OPEX``/``COMPUTE_CAPEX`` pair is deliberately not
    #: here: it is the old cost stack, and the figures the payload publishes come from the
    #: lifecycle engine.
    POST_PROCESSING: ClassVar[Tuple[PostProcessingOptions, ...]] = (
        PostProcessingOptions.COMPUTE_KPIS,
        PostProcessingOptions.WRITE_KPIS_TO_JSON,
        PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS,
    )

    #: The subdirectory of the output directory the simulation's own outputs go into.
    RESULTS_DIRECTORY: ClassVar[str] = "results"

    @classmethod
    def parameters(
        cls,
        period: Period,
        output_directory: Path,
        cache_directory: Optional[Path] = None,
        country: str = "DE",
    ) -> SimulationParameters:
        """Return the parameters of one calculation.

        Args:
            period: How long the run covers.
            output_directory: Where everything the calculation writes goes; the simulation's own
                outputs land in its ``results/`` subdirectory.
            cache_directory: Where the occupancy and weather caches live, when a container maps
                one in. It is the one location outside the output directory a calculation writes
                to, and it is shared state rather than output (decision Q25).
            country: The dwelling's country, which selects the legacy fuel-price and emission
                tables. For a country registered as a placeholder the numbers in them are the
                sentinel; see the module docstring.

        Returns:
            The parameters, with the result directory created and the post-processing options
            set.
        """
        start = datetime.datetime(cls.YEAR, 1, 1)
        days = period.days
        end = datetime.datetime(cls.YEAR + 1, 1, 1) if days is None else start + datetime.timedelta(days=days)
        results = output_directory / cls.RESULTS_DIRECTORY
        os.makedirs(results, exist_ok=True)
        parameters = SimulationParameters(
            start_date=start,
            end_date=end,
            seconds_per_timestep=cls.SECONDS_PER_TIMESTEP,
            country=country,
            result_directory=str(results),
            post_processing_options=list(cls.POST_PROCESSING),
            logging_level=cls.LOGGING_LEVEL,
        )
        if cache_directory is not None:
            cache_directory.mkdir(parents=True, exist_ok=True)
            parameters.cache_dir_path = str(cache_directory)
        return parameters

    @classmethod
    def option_names(cls) -> List[str]:
        """Return the names of the post-processing options, for the success manifest."""
        return [option.name for option in cls.POST_PROCESSING]


class SubsidyCatalogue:
    """Which countries ship a subsidy catalogue, as the translator asks the question.

    A country whose ``<COUNTRY>.json`` is not in the shipped directory gets no catalogue at all,
    which makes the engine run ``subsidy_mode: NONE`` and the result document publish one
    undetermined row saying so; a country whose file is there evaluates its schemes. Ireland used
    to be the first case and is now the second: ``IE.json`` exists since step 11, as an
    AI-generated placeholder every scheme of which is marked "needs examination" (see
    ``hisim/subsidy_catalog/README_IE.md``).

    The rule itself is not here: it is
    :meth:`hisim.economics.subsidies.SubsidyCatalog.shipped_catalog_file`, which the staged CLI
    applies as well, so a translated run and a staged plan over its outputs can never disagree
    about whether a country has a catalogue. This class is the translator's ``Path``-shaped view
    of it. The directory the answer names is absolute, which is what the catalogue loader
    requires: it refuses a relative path that could name two different directories.
    """

    @classmethod
    def path_for(cls, country: str, directory: Optional[Path] = None) -> Optional[Path]:
        """Return the catalogue file of one country, or ``None`` when it has none.

        Args:
            country: The ISO country code.
            directory: Where to look; the shipped directory when omitted.

        Returns:
            The path of ``<COUNTRY>.json``, or ``None``.
        """
        from hisim.economics.subsidies import SubsidyCatalog

        found = SubsidyCatalog.shipped_catalog_file(
            country, str(directory) if directory is not None else None
        )
        return Path(found) if found is not None else None


class EconomicSetup:
    """Points the lifecycle cost engine at the dwelling's own country.

    The engine prices a run against ``<country>`` data files -- ``devices_IE.json``,
    ``energy_prices_IE.json``, ``escalation_defaults_IE.json`` -- and picks the country either
    from an ``EconomicParameters`` the setup attached or, failing that, from
    ``SimulationParameters.country``. Both are set to the same country, and they reach two
    different stacks: the engine's own Irish data files here, and the legacy per-year tables of
    ``hisim/components/configuration.py`` there. The second holds sentinel placeholders for
    Ireland (``PlaceholderCountryFactors``), which is why the payload reads its operational CO2
    from this engine and never from the legacy KPI.
    """

    @classmethod
    def attach(
        cls,
        parameters: SimulationParameters,
        country: str,
        catalogue_directory: Optional[Path] = None,
    ) -> Optional[Path]:
        """Attach economic parameters for *country* and return the catalogue they name.

        Args:
            parameters: The run's parameters; its economic parameters are replaced.
            country: The dwelling's country code, from ``location.country``.
            catalogue_directory: Where to look for ``<COUNTRY>.json``; the shipped directory
                when omitted.

        Returns:
            The catalogue file the engine was pointed at, or ``None`` when the country has none.
        """
        from hisim.economics.parameters import EconomicParameters

        catalogue = SubsidyCatalogue.path_for(country, catalogue_directory)
        parameters.set_economic_parameters(
            EconomicParameters(
                country=country,
                subsidy_catalog_path=str(catalogue.parent) if catalogue is not None else None,
            )
        )
        return catalogue


class PeriodNames:
    """The command line's spelling of each period, for the ``--period`` argument."""

    #: The accepted values, in the order ``--help`` prints them.
    CHOICES: ClassVar[Tuple[str, ...]] = tuple(period.value for period in Period)

    #: The value used when the caller names none.
    DEFAULT: ClassVar[str] = Period.FULL_YEAR.value

    #: What each period covers, for the help text.
    DESCRIPTIONS: ClassVar[Dict[str, str]] = {
        Period.FULL_YEAR.value: "the whole of 2019 at 15 minutes",
        Period.ONE_DAY_15MIN.value: "the 1st of January 2019 at 15 minutes",
        Period.ONE_WEEK_15MIN.value: "the first week of January 2019 at 15 minutes",
    }
