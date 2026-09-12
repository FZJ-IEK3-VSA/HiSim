"""Tests for the PV producer: the layering rule it obeys, its fingerprint, and the chain to the weather.

The PV series is the second artifact keyed under ``roadmap/cache_service_spec.md`` §3, and the first one
that is computed from another artifact. That makes one promise worth pinning beyond the ones the weather
already carries: the keys chain. The PV series depends on the weather series, so a change to the weather
-- its code, its data file, its location -- has to reach the PV entry, and it does so through the
artifact key the weather publishes and the PV DTO carries as key material, not through anything the PV
knows about weather. Without that, a corrected irradiance would silently keep serving the PV series
computed from the old one, which is exactly the shape of the #628 finding this scheme exists for.

The other tests here are the PV counterparts of the weather's: the import closure stays small and free of
component machinery, so hashing all of it is affordable, and an edit to the producer's own source moves
its fingerprint with no version constant anywhere.
"""

# clean

import dataclasses
import importlib
import pathlib
import sys
from types import ModuleType
from typing import Any, Dict

import numpy as np
import pytest

from hisim.caching import CacheKey, CanonicalJson, Fingerprints, ImportClosure, ProducerLayering
from hisim.components.generic_pv_system import calculation as pv_calculation
from hisim.components.generic_pv_system.calculation import (
    ARTIFACT_KIND,
    PVLibModuleAndInverterEnum,
    PvSeriesInputs,
    PvWeatherSeries,
)
from hisim.components.weather import WeatherDataSourceEnum, WeatherSeriesInputs
from hisim.components.weather import calculation as weather_calculation

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class ProducerCopy:
    """A copy of a real producer module in a package of its own, so a test may edit it.

    The point of copying rather than writing a toy module is that the fingerprint is measured on the
    code that actually computes the artifact: a test that only proves a synthetic module's hash moves
    would pass even if the real producer had been made unhashable. The copy lives under its own root
    package, which is also the root the closure is computed against, so the real ``hisim`` imports
    inside it are recorded as outside names rather than followed.
    """

    def __init__(self, root: str, module: ModuleType, directory: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Write the package and put it on the import path for the test's lifetime.

        Args:
            root: the package name the copy lives under; unique per test.
            module: the producer module to copy.
            directory: the directory to write the package into.
            monkeypatch: used to extend ``sys.path``.
        """
        self.root = root
        self.directory = directory / root
        self.directory.mkdir()
        (self.directory / "__init__.py").write_text("", encoding="utf-8")
        self.write(pathlib.Path(module.__file__ or "").read_text(encoding="utf-8"))
        monkeypatch.syspath_prepend(str(directory))

    def write(self, source: str) -> None:
        """Write or overwrite the copied producer's source.

        Args:
            source: the source.
        """
        (self.directory / "producer.py").write_text(source, encoding="utf-8")

    def source(self) -> str:
        """Return the copied producer's current source.

        Returns:
            str: the source.
        """
        return (self.directory / "producer.py").read_text(encoding="utf-8")

    def load(self) -> ModuleType:
        """Import the copied producer freshly.

        Returns:
            ModuleType: the imported module.
        """
        for name in list(sys.modules):
            if name == self.root or name.startswith(self.root + "."):
                del sys.modules[name]
        importlib.invalidate_caches()
        return importlib.import_module(f"{self.root}.producer")

    def code_fingerprint(self) -> str:
        """Return the code fingerprint of the copy as it stands on disk.

        Returns:
            str: the fingerprint.
        """
        return Fingerprints.code(ImportClosure.of(self.load(), root_package=self.root))

    def digest_of(self, artifact_kind: str, dto: Any) -> str:
        """Return the key digest a DTO gets from the copied producer's code.

        Args:
            artifact_kind: the artifact kind to key under.
            dto: the calculation's inputs. The DTO class is the real one; only the code the key is
                fingerprinted from comes from the copy.

        Returns:
            str: the digest.
        """
        closure = ImportClosure.of(self.load(), root_package=self.root)
        return CacheKey(
            artifact_kind=artifact_kind,
            code_fingerprint=Fingerprints.code(closure),
            third_party_fingerprint=Fingerprints.third_party(closure),
            dto_json=CanonicalJson.dumps(dto),
        ).digest


def weather_inputs(**overrides: Any) -> WeatherSeriesInputs:
    """A baseline weather DTO for the Aachen test reference year, with any field replaced.

    Args:
        **overrides: fields to change from the baseline.

    Returns:
        WeatherSeriesInputs: the DTO.
    """
    baseline: Dict[str, Any] = {
        "data_source": WeatherDataSourceEnum.DWD_TRY,
        "source_content_hash": "0" * 64,
        "year": 2021,
        "seconds_per_timestep": 900,
        "latitude_in_degrees": 50.78,
        "longitude_in_degrees": 6.09,
        "duration_in_days": None,
        "source_path": "/home/somebody/HiSim/hisim/inputs/weather/aachen_center",
    }
    baseline.update(overrides)
    return WeatherSeriesInputs(**baseline)


def calculation_inputs(**overrides: Any) -> PvSeriesInputs:
    """A baseline PV DTO for the default rooftop array, with any field replaced.

    Args:
        **overrides: fields to change from the baseline.

    Returns:
        PvSeriesInputs: the DTO.
    """
    series = np.zeros(96)
    baseline: Dict[str, Any] = {
        "weather_artifact_key": "a" * 64,
        "module_database": PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE,
        "module_name": "Trina Solar TSM-435NE09RC.05",
        "inverter_database": PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE,
        "inverter_name": "Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]",
        "integrate_inverter": True,
        "load_module_data": False,
        "module_database_content_hash": "b" * 64,
        "inverter_database_content_hash": "c" * 64,
        "tilt_in_degrees": 30.0,
        "azimuth_in_degrees": 180.0,
        "number_of_timesteps": 96,
        "module_database_path": "/home/somebody/HiSim/hisim/inputs/photovoltaic/cec_modules.csv",
        "inverter_database_path": "/home/somebody/HiSim/hisim/inputs/photovoltaic/cec_inverters.csv",
        "weather_series": PvWeatherSeries.of(
            dni_extra=series,
            dni=series,
            dhi=series,
            ghi=series,
            azimuth=series,
            apparent_zenith=series,
            temperature=series,
            wind_speed=series,
        ),
    }
    baseline.update(overrides)
    return PvSeriesInputs(**baseline)


def digest_of(inputs: PvSeriesInputs) -> str:
    """The cache key digest the PV component would look its series up under.

    Args:
        inputs: the calculation's inputs.

    Returns:
        str: the digest.
    """
    return CacheKey.for_producer(ARTIFACT_KIND, pv_calculation, inputs).digest


@pytest.mark.base
def test_the_producer_obeys_the_layering_rule() -> None:
    """The producer imports nothing from the component or simulator machinery, and little else.

    This is the lint the whole scheme rests on (spec §3): the fingerprint hashes 100% of the closure,
    which is only affordable because the closure is tiny. It is asserted as an exact set rather than a
    mere absence of violations, so that an import which quietly drags a frequently edited module into
    the closure -- and would therefore throw every cached PV series away on every edit to it -- has to
    be justified here. ``hisim.utils`` is the one this producer had to be kept away from: it is where
    the paths of the module databases live, which is why the component resolves them and passes them
    in.

    Catches: a producer reaching for ``Component``, ``loadtypes``, ``utils`` or the singleton
    repository, which would make it uncallable without a simulator and its key hostage to unrelated
    edits.
    """
    closure = ImportClosure.of(pv_calculation)

    ProducerLayering.check(closure)
    assert not ProducerLayering.violations(closure)
    assert set(closure.package_modules) == {
        "hisim.components.generic_pv_system.calculation",
        "hisim.caching.keys",
    }, (
        "every HiSim module in the closure is hashed into the PV's cache key, so adding one means its "
        "edits invalidate every cached PV series; pass plain values through the DTO instead"
    )
    assert set(closure.third_party_top_levels) == {"numpy", "pandas", "pvlib"}
    assert not closure.dynamic_import_sites


@pytest.mark.base
def test_an_edit_to_the_producer_changes_its_code_fingerprint(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One changed constant in the producer's own source is a different fingerprint, with no bump anywhere.

    The edit is the kind #628 made: a constant inside the calculation. Under the old key nothing about
    it reached the filename, so a restored CI cache kept serving the series computed before the fix;
    under this key the series is filed somewhere else the moment the line changes.

    Catches: a fingerprint that hashes anything other than the producer's current source -- a module
    name, an mtime, a stale ``__pycache__`` -- which would put the staleness back.
    """
    probe = ProducerCopy("pv_producer_probe", pv_calculation, tmp_path, monkeypatch)
    before = probe.code_fingerprint()

    source = probe.source()
    edited = source.replace("ALBEDO: float = 0.2", "ALBEDO: float = 0.25")
    assert edited != source, "the line the edit targets has moved; point this test at another constant"
    probe.write(edited)

    assert probe.code_fingerprint() != before


@pytest.mark.base
def test_the_weather_reaches_the_pv_key_only_through_the_artifact_key() -> None:
    """The upstream artifact is a reference in the key and a payload beside it (spec §3.1).

    The PV series is a function of the weather series, and this is the whole of how that dependency is
    expressed: one string of key material. The arrays themselves travel as payload, excluded from the
    key -- hashing a year of floats per lookup would cost more than the calculation it saves.

    Catches: the reference falling out of the DTO (a weather change serving stale PV values, the #628
    failure one level down), and the payload arrays creeping into the key material (a key that is
    expensive to build and impossible to compute without the weather first).
    """
    baseline = calculation_inputs()
    other_weather = calculation_inputs(weather_artifact_key="d" * 64)

    assert digest_of(baseline) != digest_of(other_weather)

    material = CacheKey.for_producer(ARTIFACT_KIND, pv_calculation, baseline).material
    assert '"weather_artifact_key":"' + "a" * 64 + '"' in material
    assert "weather_series" not in material, "the weather arrays are payload; only their key is material"


@pytest.mark.base
def test_an_edit_to_the_weather_producer_moves_the_pv_key(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chain end to end: edit the weather calculation, and the PV entry is a different entry.

    This is the Merkle property the spec asks for, exercised rather than asserted: the weather's key
    is built from a copy of the real weather producer, the copy's direct normal irradiance is changed
    the way #628 changed it, and the resulting weather digest -- the string the weather component
    publishes and the PV DTO carries -- is followed into the PV key.

    Catches: a PV key that pins the weather by configuration rather than by artifact, which is what
    ``weather_identity`` used to do and what let a corrected irradiance leave every downstream cache
    entry in place.
    """
    probe = ProducerCopy("weather_producer_probe_for_pv", weather_calculation, tmp_path, monkeypatch)
    inputs = weather_inputs()
    weather_key_before = probe.digest_of(weather_calculation.ARTIFACT_KIND, inputs)

    source = probe.source()
    edited = source.replace("zenith_tol_in_degrees: float = 87.0", "zenith_tol_in_degrees: float = 88.0")
    assert edited != source, "the line the edit targets has moved; point this test at another constant"
    probe.write(edited)
    weather_key_after = probe.digest_of(weather_calculation.ARTIFACT_KIND, inputs)

    assert weather_key_before != weather_key_after
    assert digest_of(calculation_inputs(weather_artifact_key=weather_key_before)) != digest_of(
        calculation_inputs(weather_artifact_key=weather_key_after)
    )


@pytest.mark.base
def test_what_changes_the_series_changes_the_key() -> None:
    """Every field of the DTO moves the key, because every field decides the numbers.

    Catches: a field that decides the series being dropped from the DTO, which would serve one array's
    series for another -- a differently tilted array, another module, an inverter that was bypassed.
    """
    baseline = calculation_inputs()

    assert digest_of(baseline) != digest_of(calculation_inputs(tilt_in_degrees=35.0))
    assert digest_of(baseline) != digest_of(calculation_inputs(azimuth_in_degrees=170.0))
    assert digest_of(baseline) != digest_of(calculation_inputs(module_name="Hanwha HSL60P6-PA-4-250T [2013]"))
    another_inverter = calculation_inputs(inverter_name="ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_")
    assert digest_of(baseline) != digest_of(another_inverter)
    assert digest_of(baseline) != digest_of(calculation_inputs(integrate_inverter=False))
    assert digest_of(baseline) != digest_of(calculation_inputs(load_module_data=True))
    assert digest_of(baseline) != digest_of(
        calculation_inputs(module_database=PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE)
    )
    assert digest_of(baseline) != digest_of(
        calculation_inputs(inverter_database=PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE)
    )
    assert digest_of(baseline) != digest_of(calculation_inputs(number_of_timesteps=10080))


@pytest.mark.base
def test_the_databases_enter_the_key_by_their_contents_and_not_by_their_paths() -> None:
    """Two checkouts of the same module table share a key; two different tables do not.

    Spec §3.1: an input data file is referenced by content hash, and the path travels as a payload
    field. That is what lets a laptop, the cluster and a container share one entry, and what makes an
    edited module or inverter database invalidate everything computed from it without any version
    discipline.

    Catches: the paths slipping back into the key (no cross-machine hit ever again), or the content
    hashes falling out of it (an edited database served from the old entry).
    """
    here = calculation_inputs()
    there = calculation_inputs(
        module_database_path="/opt/container/hisim/inputs/photovoltaic/cec_modules.csv",
        inverter_database_path="/opt/container/hisim/inputs/photovoltaic/cec_inverters.csv",
    )
    other_modules = calculation_inputs(module_database_content_hash="e" * 64)
    other_inverters = calculation_inputs(inverter_database_content_hash="f" * 64)

    assert digest_of(here) == digest_of(there)
    assert digest_of(here) != digest_of(other_modules)
    assert digest_of(here) != digest_of(other_inverters)

    material = CacheKey.for_producer(ARTIFACT_KIND, pv_calculation, here).material
    assert "/home/somebody" not in material


@pytest.mark.base
def test_the_content_hash_reads_the_file_and_refuses_a_missing_one(tmp_path: pathlib.Path) -> None:
    """A database's bytes decide its hash, and a path that names nothing is an error rather than a None.

    Silently hashing a missing file to nothing would file two different databases under one key, which
    is the one outcome a content hash exists to prevent. Fetching the databases online has no file at
    all, and says so with ``None``; the pvlib version then stands for them in the key's third-party
    fingerprint.
    """
    database = tmp_path / "cec_modules.csv"
    database.write_text("Name,I_mp_ref\nA,5\n", encoding="utf-8")
    before = pv_calculation.content_hash(str(database))

    database.write_text("Name,I_mp_ref\nA,6\n", encoding="utf-8")

    assert before != pv_calculation.content_hash(str(database))
    assert pv_calculation.content_hash(None) is None
    with pytest.raises(FileNotFoundError, match="names no file that exists"):
        pv_calculation.content_hash(str(tmp_path / "nothing_here.csv"))


@pytest.mark.base
def test_the_dto_holds_nothing_that_does_not_decide_the_series() -> None:
    """The array's size, its price and its identity are not fields, and must not become fields.

    The produced series is a *ratio* -- AC power over peak power -- which the component multiplies by
    the configured power in ``i_simulate``, so two arrays of different size on the same roof share one
    entry. Cost, CO2 and identity fields never decide a physics result (spec §3.1), and
    ``weather_identity`` is superseded by the artifact key, which covers the weather's code and data
    as well as its configuration.

    Catches: the whole ``PVSystemConfig`` being poured into the DTO, which is how the legacy key
    worked and why repricing a fuel used to invalidate a year of pvlib.
    """
    fields = {field.name for field in dataclasses.fields(PvSeriesInputs)}

    assert not fields & {
        "power_in_watt",
        "share_of_maximum_pv_potential",
        "source_weight",
        "location",
        "time",
        "predictive_control",
        "prediction_horizon",
        "component_id",
        "weather_identity",
        "device_co2_footprint_in_kg",
        "investment_costs_in_euro",
        "lifetime_in_years",
        "maintenance_costs_in_euro_per_year",
        "subsidy_as_percentage_of_investment_costs",
    }
    material = CacheKey.for_producer(ARTIFACT_KIND, pv_calculation, calculation_inputs()).material
    for absent in ("power", "cost", "co2", "component_id", "weather_identity"):
        assert absent not in material


@pytest.mark.base
def test_the_producer_refuses_a_weather_payload_that_does_not_match_the_key() -> None:
    """The artifact's length is key material, so the payload has to be as long as the key promises.

    Catches: a component that keys for a week and hands over a year (or the reverse), which would file
    a series of the wrong length under a key nothing else would ever recompute.
    """
    series = np.zeros(48)
    mismatched = calculation_inputs(
        number_of_timesteps=96,
        weather_series=PvWeatherSeries.of(
            dni_extra=series,
            dni=series,
            dhi=series,
            ghi=series,
            azimuth=series,
            apparent_zenith=series,
            temperature=series,
            wind_speed=series,
        ),
    )

    with pytest.raises(ValueError, match="48 values"):
        pv_calculation.produce_pv_series(mismatched)
