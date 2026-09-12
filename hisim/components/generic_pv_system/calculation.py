"""Produces the AC power ratio series that :mod:`hisim.components.generic_pv_system` simulates from.

What this module produces is one artifact: the AC power of the array divided by its peak power, one
value per simulated timestep, computed in a single vectorized pvlib run over the whole simulation
period. It is the second producer under ``roadmap/cache_service_spec.md`` (§3, §12), after the weather
series it consumes, which is why it is a module of its own inside the component's package rather than a
section of the component.

**The Merkle reference.** The PV series is a function of the weather series, and the spec's answer to
that (§3.1, "Upstream artifacts as references, not payloads") is a reference rather than a copy: the DTO
carries :attr:`PvSeriesInputs.weather_artifact_key` -- the digest of the key the weather series is filed
under, published by :class:`hisim.components.weather.Weather` -- as key material, and the eight series
themselves as a payload field that is excluded from hashing. The keys therefore chain: an edit to
the weather's producer, to a weather data file, or to the location moves the weather key, which moves
this key with it, without this module knowing anything about how the weather is computed. That is what
#628 was missing, where a corrected direct normal irradiance changed nothing in CI because the caches
were keyed on configuration JSON, which says nothing about the code that produced the numbers.

**The DTO contract.** :func:`produce_pv_series` takes exactly one argument, the frozen
:class:`PvSeriesInputs`, and is a pure function of it: everything the calculation depends on is a field
and nothing else is. The module and inverter databases are identified by the hash of their contents
rather than by their paths, so editing a bundled database changes the key and moving a checkout does not;
the paths travel as payload fields beside the hashes (spec §3.1, "Input data files by content hash"). The
array's peak power, the share of the rooftop potential it was sized with, its cost, CO2, display and
identity fields are not fields here: the series is a *ratio*, which the component multiplies by the peak
power in ``i_simulate``, so repricing a fuel or renaming a component no longer invalidates a physics
result.

**The layering rule.** This module may import numpy, pandas, pvlib and plain values; it may not import
the component base class, the simulator or a repository (:class:`hisim.caching.keys.ProducerLayering`
checks it, and ``tests/test_pv_series_producer.py`` runs the check). Two reasons: the producer stays
callable without a running HiSim, so the prewarm CLI of the spec can fill the cache without simulating;
and every module in the closure is hashed into the key, so importing a frequently edited registry like
``loadtypes`` would throw the PV cache away on every enum addition. Anything a calculation needs from the
component's world is passed as a plain value through the DTO instead -- which is why
:class:`PVLibModuleAndInverterEnum` lives here rather than in ``config.py`` (whose ``hisim.config`` import
reaches the component machinery), and why the component, not this module, resolves a database enum to a
path under ``hisim/inputs``.

**Extraction only.** The calculation is the one the component ran before, moved verbatim: the same
vectorized pvlib run on the same arrays, so the produced values are bit-identical. The numeric change the
spec's survey lists for the PV -- the vectorization win -- already landed; anything further follows
separately.
"""

# clean

import hashlib
import os
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd
import pvlib

from hisim.caching.keys import KeyMaterial

__authors__ = "Vitor Hugo Bellotto Zago, Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt", "Kristina Dabrock"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"

"""
The functions in this module are to some degree based on the tsib project:

[tsib-kotzur]:
Kotzur, Leander, Detlef Stolten, and Hermann-Josef Wagner.
Future grid load of the residential building sector. No. RWTH-2018-231872.
Lehrstuhl für Brennstoffzellen (FZ Jülich), 2019.
ID: http://hdl.handle.net/2128/21115
    http://nbn-resolving.org/resolver?verb=redirect&identifier=urn:nbn:de:0001-2019020614

The implementation of the tsib project can be found under the following
repository: https://github.com/FZJ-IEK3-VSA/tsib

The CEC module and inverter database was downloaded from:
https://github.com/NREL/SAM/tree/patch/deploy/libraries
"""

#: The name this producer files its artifact under: the ``{component}`` half of the cache key and the
#: prefix of the entry's filename. It names the calculation, not the component instance, so two
#: differently named PV components with the same geometry and weather share one entry.
ARTIFACT_KIND: str = "pv_series"

#: The single column of the produced frame, and the column the cached CSV is read back by. The AC power
#: of the array divided by its peak power, dimensionless, one row per simulated timestep.
OUTPUT_COLUMN: str = "output_power"

#: What one weather series may arrive as: the component reads lists out of the repository, a caller
#: without a simulation (the prewarm CLI, a test) has arrays. Both are turned into float64 arrays by
#: :meth:`PvWeatherSeries.of`, so the calculation sees exactly one of the two.
SeriesLike = Union[Sequence[float], np.ndarray]

#: The cell-temperature model parameters each performance model runs with: PVsyst for the CEC single
#: diode, SAPM for the Sandia model, each with the mounting preset this calculation has always used.
#: They are paired with their model rather than chosen by the module database, because the database is
#: what picks the model in the first place -- so the two can no longer drift apart, and the keyword
#: arguments at each call site match the function they are passed to.
PVSYST_CELL_PARAMETERS: Mapping[str, float] = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["pvsyst"][
    "freestanding"
]
SAPM_CELL_PARAMETERS: Mapping[str, float] = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"][
    "open_rack_glass_glass"
]

#: Reflection coefficient of the ground around the array. Not a configuration field: it has always been
#: the pvlib default of this calculation, and it is part of the producer's source, hence of its
#: fingerprint. Making it configurable is a wire-format change and belongs in its own commit.
ALBEDO: float = 0.2


@unique
class PVLibModuleAndInverterEnum(str, Enum):
    """Module and inverter database options.

    Class to determine what pvlib database for phtotovoltaic modules
    and inverters should be used. Every member carries its own name as its
    value so that a serialized PV configuration names the database explicitly;
    the pvlib database keys themselves are hard-coded at the call sites in
    :func:`read_module` and :func:`read_inverter`, so no ordinal is needed.

    https://pvlib-python.readthedocs.io/en/v0.9.0/generated/pvlib.pvsystem.retrieve_sam.html.

    It lives in the producer module because the database choice decides both the parameters and the
    model the calculation runs, and its value is key material; the package ``__init__`` re-exports it,
    so ``generic_pv_system.PVLibModuleAndInverterEnum`` keeps working for configurations and system
    setups.
    """

    SANDIA_MODULE_DATABASE = "SANDIA_MODULE_DATABASE"
    SANDIA_INVERTER_DATABASE = "SANDIA_INVERTER_DATABASE"
    CEC_MODULE_DATABASE = "CEC_MODULE_DATABASE"
    CEC_INVERTER_DATABASE = "CEC_INVERTER_DATABASE"
    ANTON_DRIESSE_INVERTER_DATABASE = "ANTON_DRIESSE_INVERTER_DATABASE"


#: Which bundled database file each database is read from when ``load_module_data`` is false, named by
#: its key in ``hisim.utils.HISIMPATH["photovoltaic"]``. The mapping lives here because which file
#: belongs to which database is part of the calculation; resolving the key to a path on this machine is
#: the component's job, because ``HISIMPATH`` is component-layer knowledge. A database that is missing
#: from this mapping has no bundled file and can only be read online -- :func:`read_module` and
#: :func:`read_inverter` then refuse it, as they always have.
DATABASE_FILE_KEYS: Mapping[PVLibModuleAndInverterEnum, str] = {
    PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE: "sandia_modules_new",
    PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE: "cec_modules",
    PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE: "sandia_inverters",
    PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE: "cec_inverters",
}

#: How much of a database file is read at a time by :func:`content_hash`. A chunked read costs nothing
#: and keeps the five-megabyte CEC module table from being held in memory twice.
HASH_BLOCK_SIZE: int = 1 << 20


def content_hash(path: Optional[str]) -> Optional[str]:
    """Hash the contents of a database file, so that the key names the data and not the machine.

    Spec §3.1: an input data file enters a key by its content hash. The path differs between a
    checkout, a container and the cluster while naming the same bytes, so hashing the path would mean
    no shared entry ever hits; hashing the contents means an edited database invalidates every series
    computed from it, with no version discipline anywhere.

    Args:
        path: the file to hash, or ``None`` when the databases are fetched from pvlib online instead of
            read from a bundled file. In that case what decides the parameters is the pvlib version,
            which the key's third-party fingerprint already pins.

    Returns:
        Optional[str]: the sha256 hex digest of the file's bytes, or ``None`` for ``None``.

    Raises:
        FileNotFoundError: if the path names no file. A silently skipped hash would file two different
            databases under one key.
    """
    if path is None:
        return None
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"The photovoltaic database {path!r} names no file that exists, so its contents cannot "
            "enter the cache key that stands for them."
        )
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(HASH_BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, eq=False)
class PvWeatherSeries:
    """The eight weather series the calculation consumes, already cut to the simulated period.

    This is the payload half of the Merkle reference: the numbers the producer computes from, paired
    with :attr:`PvSeriesInputs.weather_artifact_key`, which is what identifies them in the key. The
    component fills both from the same weather component, which is the invariant spec §3.1 states and
    the author has to keep.

    Equality is switched off (``eq=False``): comparing two of these would compare numpy arrays with
    ``==`` and raise on the ambiguous truth value of an array. Nothing needs it -- the key material is
    what compares, and these fields are not key material.
    """

    #: Extraterrestrial direct normal irradiance per timestep, W/m².
    dni_extra: np.ndarray

    #: Direct normal irradiance per timestep, W/m².
    dni: np.ndarray

    #: Diffuse horizontal irradiance per timestep, W/m².
    dhi: np.ndarray

    #: Global horizontal irradiance per timestep, W/m².
    ghi: np.ndarray

    #: Solar azimuth per timestep, degrees.
    azimuth: np.ndarray

    #: Apparent solar zenith per timestep, degrees.
    apparent_zenith: np.ndarray

    #: Air temperature per timestep, °C.
    temperature: np.ndarray

    #: Wind speed per timestep, m/s.
    wind_speed: np.ndarray

    @classmethod
    def of(
        cls,
        dni_extra: SeriesLike,
        dni: SeriesLike,
        dhi: SeriesLike,
        ghi: SeriesLike,
        azimuth: SeriesLike,
        apparent_zenith: SeriesLike,
        temperature: SeriesLike,
        wind_speed: SeriesLike,
    ) -> "PvWeatherSeries":
        """Build the payload from eight sequences, as float64 arrays.

        Args:
            dni_extra: extraterrestrial direct normal irradiance per timestep.
            dni: direct normal irradiance per timestep.
            dhi: diffuse horizontal irradiance per timestep.
            ghi: global horizontal irradiance per timestep.
            azimuth: solar azimuth per timestep.
            apparent_zenith: apparent solar zenith per timestep.
            temperature: air temperature per timestep.
            wind_speed: wind speed per timestep.

        Returns:
            PvWeatherSeries: the payload.
        """
        return cls(
            dni_extra=np.asarray(dni_extra, dtype=float),
            dni=np.asarray(dni, dtype=float),
            dhi=np.asarray(dhi, dtype=float),
            ghi=np.asarray(ghi, dtype=float),
            azimuth=np.asarray(azimuth, dtype=float),
            apparent_zenith=np.asarray(apparent_zenith, dtype=float),
            temperature=np.asarray(temperature, dtype=float),
            wind_speed=np.asarray(wind_speed, dtype=float),
        )


@dataclass(frozen=True)
class PvSeriesInputs:
    """Everything :func:`produce_pv_series` depends on, and nothing else.

    Every field except the three payload ones at the end is key material: two runs whose inputs render
    to the same canonical JSON (:class:`hisim.caching.keys.CanonicalJson`) produce the same series, on
    any machine.

    What is deliberately *not* here, although ``PVSystemConfig`` carries it: the peak power and the
    share of the rooftop potential (the series is a ratio, and the component multiplies), the source
    weight, the location label, the ``time`` field, the predictive-control flag and its horizon (they
    decide what the component publishes, not what it computes), every cost, CO2, lifetime and subsidy
    field, the component identity -- and ``weather_identity``, whose whole purpose was to get the
    weather into the old configuration-JSON key and which :attr:`weather_artifact_key` now does
    properly.

    The simulated year and the timestep length are not fields either: they reach this key through
    :attr:`weather_artifact_key`, which covers both, and what remains of the simulated span is
    :attr:`number_of_timesteps`.
    """

    #: The identity of the weather series this run consumes: the digest of the weather producer's cache
    #: key, published by the weather component under
    #: :attr:`hisim.components.weather.Weather.SERIES_ARTIFACT_KEY`. Key material, and the link that
    #: makes the keys compose Merkle-style -- everything the weather depends on, its own code included,
    #: is already inside this string.
    weather_artifact_key: str

    #: Which module database the module parameters come from; also decides the cell-temperature model
    #: and which of the two performance models runs.
    module_database: PVLibModuleAndInverterEnum

    #: The module's name in that database.
    module_name: str

    #: Which inverter database the inverter parameters come from.
    inverter_database: PVLibModuleAndInverterEnum

    #: The inverter's name in that database.
    inverter_name: str

    #: Whether the inverter's efficiency is part of the result. False means the module's DC power is
    #: divided by the module's peak power and no inverter is consulted.
    integrate_inverter: bool

    #: Whether module and inverter parameters are fetched from pvlib online instead of being read from
    #: the bundled database files. Decides which of the two readers runs, so it is key material even
    #: though both are meant to yield the same parameters.
    load_module_data: bool

    #: :func:`content_hash` of the bundled module database, or ``None`` when it is fetched online.
    module_database_content_hash: Optional[str]

    #: :func:`content_hash` of the bundled inverter database, or ``None`` when it is fetched online.
    inverter_database_content_hash: Optional[str]

    #: Tilt of the array against the horizontal, in degrees.
    tilt_in_degrees: float

    #: Azimuth of the array from north, in degrees; 180 is south.
    azimuth_in_degrees: float

    #: How many timesteps the produced series covers. The weather series always spans the full year
    #: while a run may span a day or a week, so this is what decides the artifact's length -- and the
    #: resolution behind it is already carried by :attr:`weather_artifact_key`.
    number_of_timesteps: int

    #: Where the module database is on this machine, or ``None`` when it is fetched online. Payload, not
    #: key material: it pairs with :attr:`module_database_content_hash`, which identifies the bytes.
    module_database_path: Optional[str] = field(metadata=KeyMaterial.PAYLOAD)

    #: Where the inverter database is on this machine, or ``None``. Payload, paired with
    #: :attr:`inverter_database_content_hash`.
    inverter_database_path: Optional[str] = field(metadata=KeyMaterial.PAYLOAD)

    #: The weather numbers themselves, cut to the simulated period. Payload, paired with
    #: :attr:`weather_artifact_key`.
    weather_series: PvWeatherSeries = field(metadata=KeyMaterial.PAYLOAD)

    #: The databases that carry module parameters, as opposed to inverter parameters. Used to refuse a
    #: configuration that names an inverter database where a module database belongs.
    MODULE_DATABASES: ClassVar[Tuple[PVLibModuleAndInverterEnum, ...]] = (
        PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,
        PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE,
    )


def produce_pv_series(inputs: PvSeriesInputs) -> pd.DataFrame:
    """Produce the AC power ratio of the array for every timestep of the simulated period.

    Reads the module and inverter parameters, runs the performance model the module database implies
    over the whole period in one vectorized pvlib call, and returns the result as the one-column frame
    the cache stores. The result is a pure function of ``inputs``.

    Args:
        inputs: the calculation's inputs.

    Returns:
        pd.DataFrame: one row per timestep, with the single column :data:`OUTPUT_COLUMN`, holding the
            AC power divided by the array's peak power.

    Raises:
        ValueError: if the weather payload does not cover exactly the requested number of timesteps --
            the artifact's length is key material, so producing a shorter or longer one would file a
            series under a key that promises another.
        KeyError: if the module database is not one this producer implements.
    """
    weather = inputs.weather_series
    if len(weather.dni) != inputs.number_of_timesteps:
        raise ValueError(
            f"The weather series handed to the PV producer holds {len(weather.dni)} values, but the "
            f"artifact is keyed for {inputs.number_of_timesteps} timesteps. The component cuts the "
            "weather to the simulated period before building the inputs; these two must agree."
        )
    module = read_module(inputs)
    inverter = read_inverter(inputs)
    if inputs.module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE:
        simulate_fct = simulate_cec
    elif inputs.module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE:
        simulate_fct = simulate_sandia
    else:
        raise KeyError(
            f"""The module database '{inputs.module_database}'
                    is not available."""
        )

    # one vectorized pvlib run over the entire simulation period
    ac_power_ratios = simulate_fct(inputs=inputs, weather=weather, module=module, inverter=inverter)
    return pd.DataFrame({OUTPUT_COLUMN: list(ac_power_ratios)}, columns=[OUTPUT_COLUMN])


def read_module(inputs: PvSeriesInputs) -> Any:
    """Get the module parameters from the pvlib database named by the inputs.

    Args:
        inputs: the calculation's inputs; supplies the database, the module name and, for the bundled
            files, the path to read.

    Returns:
        Any: the module parameters, as a mapping of pvlib's parameter names to numbers.

    Raises:
        KeyError: if the database is not one this producer implements, or holds no module of that name.
    """
    module_database = inputs.module_database
    module_name = inputs.module_name

    # get modules from pvlib database online
    # (TODO: test if this works, it has not been fully tested yet)
    if inputs.load_module_data is True:
        if module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE:
            modules = pvlib.pvsystem.retrieve_sam(name="SandiaMod")
        elif module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE:
            modules = pvlib.pvsystem.retrieve_sam(name="CECMod")
        else:
            raise KeyError(
                f"""The module database {module_database} is not integrated
                    in the PV component here."""
            )

        # choose module from modules database
        module = modules[module_name]

    # get modules from input data csv files
    else:
        if module_database not in PvSeriesInputs.MODULE_DATABASES or inputs.module_database_path is None:
            raise KeyError(
                f"""The module database {module_database} is not integrated
                    in the PV component here."""
            )
        modules = pd.read_csv(inputs.module_database_path)

        # choose module from modules database
        module = modules.loc[modules["Name"] == module_name].copy()

        # transform column object types to numeric types
        for column in module.columns:
            if column == "Name":
                continue
            module[column] = pd.to_numeric(module[column], errors="coerce")

        # transform module dataframe to dict
        if len(module) != 1:
            raise KeyError(
                f"""No module {module_name} found in database
                    {module_database}."""
            )

        module = module.to_dict(orient="records")[0]

    return module


def read_inverter(inputs: PvSeriesInputs) -> Any:
    """Get the inverter parameters from the pvlib database named by the inputs.

    Args:
        inputs: the calculation's inputs; supplies the database, the inverter name and, for the bundled
            files, the path to read.

    Returns:
        Any: the inverter parameters.

    Raises:
        KeyError: if the database is not one this producer implements, or holds no inverter of that name.
    """
    inverter_database = inputs.inverter_database
    inverter_name = inputs.inverter_name

    # get inverters from pvlib database online
    if inputs.load_module_data is True:
        if inverter_database in (
            PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,
            PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE,
        ):
            # get inverter data (for both sandia and cec inverters the same
            # database is taken):
            # see docs: https://pvlib-python.readthedocs.io/en/v0.9.0/generated/pvlib.pvsystem.retrieve_sam.html  # noqa: E501
            inverters = pvlib.pvsystem.retrieve_sam("CECInverter")
            inverter = inverters[inverter_name]
        elif inverter_database == PVLibModuleAndInverterEnum.ANTON_DRIESSE_INVERTER_DATABASE:
            inverters = pvlib.pvsystem.retrieve_sam("ADRInverter")
            inverter = inverters[inverter_name]
        else:
            raise KeyError(
                f"""The inverter database {inverter_database} is not
                    integrated in the PV component here."""
            )

    # get inverters from input data csv files
    else:
        if inputs.inverter_database_path is None:
            # The component resolves a path for every database that has a bundled file; none means
            # this database is an online-only one, which is the same refusal as the branch below.
            raise KeyError(
                f"""The inverter database {inverter_database} is not
                    integrated in the PV component here."""
            )

        # this is the old csv file used in hisim
        if inverter_database == PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE:
            inverters = pd.read_csv(inputs.inverter_database_path, index_col=0)
            # choose inverter from inverters database
            inverter = inverters[inverter_name]
            # transform to numeric types
            inverter = pd.to_numeric(inverter, errors="coerce")

        # this would be the new one, but not tested yet
        elif inverter_database == PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE:
            inverters = pd.read_csv(inputs.inverter_database_path)
            # choose inverter from inverters database
            inverter = inverters.loc[inverters["Name"] == inverter_name].copy()

            # transform column object types to numeric types
            for column in inverter.columns:
                if column == "Name":
                    continue
                inverter[column] = pd.to_numeric(inverter[column], errors="coerce")

            # transform inverter dataframe to dict
            if len(inverter) != 1:
                raise KeyError(
                    f"""No inverter {inverter_name} found in database
                        {inverter_database}."""
                )

            inverter = inverter.to_dict(orient="records")[0]

        else:
            raise KeyError(
                f"""The inverter database {inverter_database} is not
                    integrated in the PV component here."""
            )

    return inverter


def simulate_sandia(
    inputs: PvSeriesInputs,
    weather: PvWeatherSeries,
    module: Any,
    inverter: Any,
) -> np.ndarray:
    """Simulates with the Sandia PV Array Performance Model, vectorized.

    All weather parameters are numpy arrays covering the whole simulation
    period; the function is called exactly once per produced artifact and
    returns the AC power ratio (AC power divided by the module peak load) for
    every timestep in one array. Night timesteps carry NaN through the pvlib
    chain (the relative airmass is undefined for zenith angles beyond 90
    degrees) and are mapped to a power ratio of 0.0 at the end, exactly like the
    scalar per-timestep implementation this replaced.

    The implementation is done in accordance with following tutorial:
    https://github.com/pvlib/pvlib-python/blob/master/docs/tutorials/tmy_to_power.ipynb
    https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.pvsystem.sapm.html#pvlib.pvsystem.sapm

    Based on the tsib project @[tsib-kotzur] (Check header)

    Args:
        inputs: the calculation's inputs; supplies tilt, azimuth and whether the inverter counts.
        weather: the weather series, one value per timestep.
        module: the module parameters from :func:`read_module`.
        inverter: the inverter parameters from :func:`read_inverter`.

    Returns:
        np.ndarray: AC power ratio per timestep, NaN-free.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        poa_irrad, airmass, aoi = calculate_irradiance(inputs, weather)

        pvtemps = pvlib.temperature.sapm_cell(
            poa_irrad["poa_global"],
            weather.temperature,
            weather.wind_speed,
            **SAPM_CELL_PARAMETERS,
        )

        # calculate effective irradiance on pv module
        sapm_irr = pvlib.pvsystem.sapm_effective_irradiance(
            module=module,
            poa_direct=poa_irrad["poa_direct"],
            poa_diffuse=poa_irrad["poa_diffuse"],
            airmass_absolute=airmass,
            aoi=aoi,
        )
        # calculate pv performance
        sapm_out = pvlib.pvsystem.sapm(
            sapm_irr,
            module=module,
            temp_cell=pvtemps,
        )
        # calculate peak load of single module [W]
        module_peak_load_in_watt = module["Impo"] * module["Vmpo"]

        if inputs.integrate_inverter:
            # calculate load after inverter
            inverter_load_in_watt = pvlib.inverter.sandia(
                inverter=inverter,
                v_dc=sapm_out["v_mp"],
                p_dc=sapm_out["p_mp"],
            )
            # if inverter load is nan, make it zero otherwise ac_power_ratio
            # will be nan also
            inverter_load_in_watt = np.where(np.isnan(inverter_load_in_watt), 0.0, inverter_load_in_watt)
            ac_power_ratio = inverter_load_in_watt / module_peak_load_in_watt
        else:
            # load in [kW/kWp]
            ac_power_ratio = np.asarray(sapm_out["p_mp"], dtype=float) / module_peak_load_in_watt

    return np.where(np.isnan(ac_power_ratio), 0.0, ac_power_ratio)


def simulate_cec(
    inputs: PvSeriesInputs,
    weather: PvWeatherSeries,
    module: Any,
    inverter: Any,
) -> np.ndarray:
    """Simulates a defined PV array using the single-diode model.

    This simulation works with data from the CEC database.
    The implementation is done in accordance with following tutorial:
    https://github.com/pvlib/pvlib-python/blob/master/docs/tutorials/tmy_to_power.ipynb
    https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.pvsystem.sapm.html#pvlib.pvsystem.sapm

    Args:
        inputs: the calculation's inputs; supplies tilt, azimuth and whether the inverter counts.
        weather: the weather series, one value per timestep.
        module: the module parameters from :func:`read_module`.
        inverter: the inverter parameters from :func:`read_inverter`.

    Returns:
        np.ndarray: AC power ratio per timestep, NaN-free.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        # Calculate irradiance
        poa_irrad, _, _ = calculate_irradiance(inputs, weather)

        # Calculate cell temperature
        pvtemps = pvlib.temperature.pvsyst_cell(
            poa_irrad["poa_global"],
            weather.temperature,
            weather.wind_speed,
            **PVSYST_CELL_PARAMETERS,
        )

        # Calculate maximum power point
        d = {
            k: module[k]
            for k in [
                "alpha_sc",
                "a_ref",
                "I_L_ref",
                "I_o_ref",
                "R_sh_ref",
                "R_s",
                "Adjust",
            ]
        }

        # Where the global irradiation is undefined (typically at night,
        # when the relative airmass and hence the Perez sky-diffuse model
        # yield NaN), the PV output is zero. Restricting the single-diode
        # solve to the defined timesteps both reproduces the scalar
        # behavior (which returned 0.0 for NaN irradiance) and skips the
        # expensive brentq root search for roughly half of all timesteps.
        poa_global = np.asarray(poa_irrad["poa_global"], dtype=float)
        pvtemps = np.asarray(pvtemps, dtype=float)
        ac_power_ratio = np.zeros(len(poa_global))
        valid = ~np.isnan(poa_global)

        if np.any(valid):
            (
                photocurrent,
                saturation_current,
                resistance_series,
                resistance_shunt,
                n_ns_v_th,
            ) = pvlib.pvsystem.calcparams_cec(
                effective_irradiance=poa_global[valid],
                temp_cell=pvtemps[valid],
                **d,
            )

            # The vectorized newton solver is ~60x faster than brentq here
            # (1 s instead of 58 s for a minutely year) and agrees with it
            # to below 1e-11 W on a 10 kW system over a full year of
            # weather data.
            mp = pvlib.pvsystem.max_power_point(
                photocurrent,
                saturation_current,
                resistance_series,
                resistance_shunt,
                n_ns_v_th,
                d2mutau=0,
                NsVbi=np.inf,
                method="newton",
            )

            # Calculate peak load of single module [W]
            module_peak_load_in_watt = module["I_mp_ref"] * module["V_mp_ref"]

            if inputs.integrate_inverter:
                # calculate load after inverter
                inverter_load_in_watt = pvlib.inverter.sandia(inverter=inverter, v_dc=mp["v_mp"], p_dc=mp["p_mp"])
                # if inverter load is nan, make it zero otherwise
                # ac_power_ratio will be nan also
                inverter_load_in_watt = np.where(np.isnan(inverter_load_in_watt), 0.0, inverter_load_in_watt)
                valid_ac_power_ratio = inverter_load_in_watt / module_peak_load_in_watt
            else:
                # load in [kW/kWp]
                valid_ac_power_ratio = np.asarray(mp["p_mp"], dtype=float) / module_peak_load_in_watt

            ac_power_ratio[valid] = np.where(np.isnan(valid_ac_power_ratio), 0.0, valid_ac_power_ratio)

    return ac_power_ratio


def calculate_irradiance(
    inputs: PvSeriesInputs,
    weather: PvWeatherSeries,
) -> Tuple[Dict[str, Any], np.ndarray, np.ndarray]:
    """Calculate the plane-of-array irradiance for all timesteps at once.

    Takes the whole-period solar position and irradiance arrays and returns
    the plane-of-array irradiance components (as a mapping with the keys
    ``poa_global``, ``poa_direct`` and ``poa_diffuse``), the relative
    airmass, and the angle of incidence, each as an array over all
    timesteps. At night the relative airmass — and consequently the Perez
    sky-diffuse model and the global plane-of-array irradiance — is NaN;
    the callers translate those timesteps into zero power output.

    Args:
        inputs: the calculation's inputs; supplies the array's tilt and azimuth.
        weather: the weather series, one value per timestep.

    Returns:
        Tuple[Dict[str, Any], np.ndarray, np.ndarray]: the plane-of-array irradiance components, the
            relative airmass and the angle of incidence.
    """
    surface_tilt = inputs.tilt_in_degrees
    surface_azimuth = inputs.azimuth_in_degrees
    dni = np.asarray(weather.dni, dtype=np.float64)

    # calculate airmass
    airmass = pvlib.atmosphere.get_relative_airmass(weather.apparent_zenith)

    # calculate diffuse irradiance
    poa_sky_diffuse = pvlib.irradiance.perez(
        surface_tilt,
        surface_azimuth,
        weather.dhi,
        dni,
        weather.dni_extra,
        weather.apparent_zenith,
        weather.azimuth,
        airmass,
    )

    # calculate ground diffuse with specified albedo
    poa_ground_diffuse = pvlib.irradiance.get_ground_diffuse(surface_tilt, weather.ghi, albedo=ALBEDO)
    # calculate angle of incidence
    aoi = pvlib.irradiance.aoi(surface_tilt, surface_azimuth, weather.apparent_zenith, weather.azimuth)
    # calculate plane of array irradiance
    poa_irrad = pvlib.irradiance.poa_components(aoi, dni, poa_sky_diffuse, poa_ground_diffuse)

    return poa_irrad, airmass, aoi
