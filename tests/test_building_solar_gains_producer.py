"""Tests for the building's solar-gains producer: its layering, its fingerprint and its chain to the weather.

The gains through the windows are the third artifact keyed under ``roadmap/cache_service_spec.md`` §3,
and the first one that is *chained*: it is computed from the weather series, which have a key of their
own. These tests pin the three promises that makes. The producer's import closure stays small and free
of component machinery, so hashing all of it is affordable -- and it contains the window optics, so an
edit to them moves the key by itself. The weather enters the key as a reference and not as data: a
different weather artifact key is a different entry, while the series themselves are payload and never
touch the key. And the window geometry is key material, so two buildings with different windows cannot
share a series.
"""

# clean

import importlib
import pathlib
import sys
from types import ModuleType
from typing import Any, ClassVar, Dict, Tuple

import pytest

from hisim.caching import CacheKey, Fingerprints, ImportClosure, ProducerLayering
from hisim.components.building import solar_gains
from hisim.components.building.solar_gains import (
    ARTIFACT_KIND,
    SERIES_COLUMN,
    SolarGainsInputs,
    WindowGeometry,
    produce_solar_gains,
)

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class ProducerCopy:
    """A copy of the real producer and the real window optics, in a package a test may edit.

    Copying rather than writing toy modules is the point: the fingerprint is measured on the code that
    actually computes the gains, so a test that only proved a synthetic module's hash moves would pass
    even if the real producer had been made unhashable. The copy lives under its own root package,
    which is also the root the closure is computed against, so that the real ``hisim`` imports inside
    it are recorded as outside names rather than followed -- :meth:`code_fingerprint` therefore hashes
    the copied sources alone. The producer's import of the window module is rewritten to the copy, so
    that editing the optics is an edit inside the closure, exactly as it is in the package.
    """

    #: The package the copy lives in.
    ROOT: ClassVar[str] = "solar_gains_probe"

    #: The module name the producer is copied to.
    PRODUCER: ClassVar[str] = "producer"

    #: The module name the window optics are copied to.
    WINDOW: ClassVar[str] = "window"

    #: The import the copied producer must reach the copied optics through.
    REAL_WINDOW_IMPORT: ClassVar[str] = "from hisim.components.building.window import Window"

    def __init__(self, directory: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Write the package and put it on the import path for the test's lifetime.

        Args:
            directory: the directory to write the package into.
            monkeypatch: used to extend ``sys.path``.
        """
        self.directory = directory / self.ROOT
        self.directory.mkdir()
        (self.directory / "__init__.py").write_text("", encoding="utf-8")
        window_module = importlib.import_module("hisim.components.building.window")
        self.write(self.WINDOW, pathlib.Path(window_module.__file__ or "").read_text(encoding="utf-8"))
        producer_source = pathlib.Path(solar_gains.__file__ or "").read_text(encoding="utf-8")
        assert self.REAL_WINDOW_IMPORT in producer_source, (
            "the producer no longer imports the window optics the way this probe rewrites; point it at "
            "the new import, or the copy would measure the real module instead of the copied one"
        )
        self.write(
            self.PRODUCER,
            producer_source.replace(self.REAL_WINDOW_IMPORT, f"from {self.ROOT}.{self.WINDOW} import Window"),
        )
        monkeypatch.syspath_prepend(str(directory))

    def write(self, module: str, source: str) -> None:
        """Write or overwrite one module of the package.

        Args:
            module: the module name below the root.
            source: its source.
        """
        (self.directory / f"{module}.py").write_text(source, encoding="utf-8")

    def source(self, module: str) -> str:
        """Return one module's current source.

        Args:
            module: the module name below the root.

        Returns:
            str: the source.
        """
        return (self.directory / f"{module}.py").read_text(encoding="utf-8")

    def load(self, module: str) -> ModuleType:
        """Import one module of the package freshly.

        Args:
            module: the module name below the root.

        Returns:
            ModuleType: the imported module.
        """
        for name in list(sys.modules):
            if name == self.ROOT or name.startswith(self.ROOT + "."):
                del sys.modules[name]
        importlib.invalidate_caches()
        return importlib.import_module(f"{self.ROOT}.{module}")

    def code_fingerprint(self) -> str:
        """Return the code fingerprint of the copied producer as it stands on disk.

        Returns:
            str: the fingerprint.
        """
        return Fingerprints.code(ImportClosure.of(self.load(self.PRODUCER), root_package=self.ROOT))


def window_geometry(**overrides: Any) -> WindowGeometry:
    """A south-facing window of the default TABULA house, with any field replaced.

    Args:
        **overrides: fields to change from the baseline.

    Returns:
        WindowGeometry: the window.
    """
    baseline: Dict[str, Any] = {
        "tilt_angle_in_degrees": 90.0,
        "azimuth_angle_in_degrees": 180.0,
        "area_in_m2": 6.5,
        "frame_area_fraction_reduction_factor": 0.3,
        "glass_solar_transmittance": 0.6,
        "nonperpendicular_reduction_factor": 0.9,
        "external_shading_vertical_reduction_factor": 0.6,
    }
    baseline.update(overrides)
    return WindowGeometry(**baseline)


def daylight_series(value: float) -> Tuple[float, ...]:
    """Two timesteps of one constant, so a DTO can be built without a weather file.

    Args:
        value: the value both timesteps carry.

    Returns:
        Tuple[float, ...]: the series.
    """
    return (value, value)


def calculation_inputs(**overrides: Any) -> SolarGainsInputs:
    """A baseline DTO for two timesteps of one south-facing window, with any field replaced.

    Args:
        **overrides: fields to change from the baseline.

    Returns:
        SolarGainsInputs: the DTO.
    """
    baseline: Dict[str, Any] = {
        "weather_artifact_key": "a" * 64,
        "year": 2021,
        "seconds_per_timestep": 900,
        "timesteps": 2,
        "windows": (window_geometry(),),
        "azimuth_in_degrees": daylight_series(180.0),
        "direct_normal_irradiance_in_watt_per_square_meter": daylight_series(620.0),
        "diffuse_horizontal_irradiance_in_watt_per_square_meter": daylight_series(145.0),
        "global_horizontal_irradiance_in_watt_per_square_meter": daylight_series(480.0),
        "direct_normal_irradiance_extra_in_watt_per_square_meter": daylight_series(1361.0),
        "apparent_zenith_in_degrees": daylight_series(40.0),
    }
    baseline.update(overrides)
    return SolarGainsInputs(**baseline)


def digest_of(inputs: SolarGainsInputs) -> str:
    """The cache key digest the Building would look the gains series up under.

    Args:
        inputs: the calculation's inputs.

    Returns:
        str: the digest.
    """
    return CacheKey.for_producer(ARTIFACT_KIND, solar_gains, inputs).digest


@pytest.mark.base
def test_the_producer_obeys_the_layering_rule() -> None:
    """The producer imports nothing from the component or simulator machinery, and little else.

    This is the lint the whole scheme rests on (spec §3): the fingerprint hashes 100% of the closure,
    which is only affordable because the closure is tiny. It is asserted as an exact set rather than a
    mere absence of violations, so that an import which quietly drags a frequently edited module into
    the closure -- and would therefore throw every cached gains series away on every edit to it -- has
    to be justified here. ``hisim.components.building.window`` is in it on purpose: the optics are the
    calculation. ``hisim.log`` comes with them and imports nothing further.

    Catches: a producer reaching for ``Component``, ``BuildingInformation`` or ``loadtypes``, which
    would make it uncallable without a simulator and its key hostage to unrelated edits.
    """
    closure = ImportClosure.of(solar_gains)

    ProducerLayering.check(closure)
    assert not ProducerLayering.violations(closure)
    assert set(closure.package_modules) == {
        "hisim.components.building.solar_gains",
        "hisim.components.building.window",
        "hisim.caching.keys",
        "hisim.log",
    }, (
        "every HiSim module in the closure is hashed into the gains' cache key, so adding one means its "
        "edits invalidate every cached gains series; pass plain values through the DTO instead"
    )
    assert set(closure.third_party_top_levels) == {"pandas", "pvlib"}
    assert not closure.dynamic_import_sites


@pytest.mark.base
def test_an_edit_to_the_producer_changes_its_code_fingerprint(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One changed line in the producer's own source is a different fingerprint, with no bump anywhere.

    Catches: a fingerprint that hashes anything other than the producer's current source -- a module
    name, an mtime, a stale ``__pycache__`` -- which would serve gains computed by the previous version
    of the calculation, the #628 failure one component further down.
    """
    probe = ProducerCopy(tmp_path, monkeypatch)
    before = probe.code_fingerprint()

    source = probe.source(ProducerCopy.PRODUCER)
    edited = source.replace(
        "if direct_normal_irradiance != 0 or direct_horizontal_irradiance != 0",
        "if direct_normal_irradiance > 0 or direct_horizontal_irradiance != 0",
    )
    assert edited != source, "the line the edit targets has moved; point this test at another line"
    probe.write(ProducerCopy.PRODUCER, edited)

    assert probe.code_fingerprint() != before


@pytest.mark.base
def test_an_edit_to_the_window_optics_changes_the_fingerprint(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The optics are part of the calculation, so editing them moves the key of every gains series.

    The window model is a module of its own and is imported rather than inlined, which is only safe
    because the closure follows the import. If it ever stopped following it, a change to the angle of
    incidence or to the reduction factors would be served from entries computed before it.

    Catches: the closure being narrowed to the producer's own file.
    """
    probe = ProducerCopy(tmp_path, monkeypatch)
    before = probe.code_fingerprint()

    source = probe.source(ProducerCopy.WINDOW)
    edited = source.replace(
        "return (1 + math.cos(self.window_tilt_angle_rad)) / 2",
        "return (1 + math.cos(self.window_tilt_angle_rad)) / 2.0",
    )
    assert edited != source, "the line the edit targets has moved; point this test at another line"
    probe.write(ProducerCopy.WINDOW, edited)

    assert probe.code_fingerprint() != before


@pytest.mark.base
def test_an_edit_outside_the_closure_leaves_the_fingerprint_alone(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A module the producer does not import cannot invalidate its artifacts.

    The other half of the promise: if every edit anywhere invalidated the cache, the scheme would be
    commit-keying under another name and nobody would ever get a hit.

    Catches: a fingerprint widened to the package, the repository or the commit.
    """
    probe = ProducerCopy(tmp_path, monkeypatch)
    probe.write("stranger", '"""A module nothing imports."""\n\nVALUE = 1\n')
    before = probe.code_fingerprint()

    probe.write("stranger", '"""A module nothing imports."""\n\nVALUE = 2\n')

    assert probe.code_fingerprint() == before


@pytest.mark.base
def test_the_weather_enters_the_key_as_a_reference_and_not_as_data() -> None:
    """The upstream artifact key moves this key; the series it stands for never touch it.

    This is the Merkle composition of spec §3.1. The weather's own digest already stands for its data
    file, its station, its year, its timestep and its producer's code, so naming it is enough -- and
    hashing the series instead would mean hashing half a million floats on every lookup for no
    additional promise.

    Catches: the chain being cut (an edited weather file served with gains computed from the old one)
    and the payload fields leaking into the key material (a key that changes for no reason and never
    hits).
    """
    baseline = calculation_inputs()
    other_weather = calculation_inputs(weather_artifact_key="b" * 64)
    other_series = calculation_inputs(
        azimuth_in_degrees=daylight_series(90.0),
        direct_normal_irradiance_in_watt_per_square_meter=daylight_series(10.0),
        diffuse_horizontal_irradiance_in_watt_per_square_meter=daylight_series(11.0),
        global_horizontal_irradiance_in_watt_per_square_meter=daylight_series(12.0),
        direct_normal_irradiance_extra_in_watt_per_square_meter=daylight_series(13.0),
        apparent_zenith_in_degrees=daylight_series(14.0),
    )

    assert digest_of(baseline) != digest_of(other_weather)
    assert digest_of(baseline) == digest_of(other_series)
    assert "620.0" not in CacheKey.for_producer(ARTIFACT_KIND, solar_gains, baseline).material


@pytest.mark.base
def test_what_changes_the_series_changes_the_key() -> None:
    """Every window number and every span field is key material.

    A refurbishment that changes the glazing, a building scaled to another floor area, a roof light
    instead of a facade window: all of them compute a different series and must therefore be filed
    under a different key.

    Catches: a field that decides the result being left out of the DTO -- two different series under
    one key, which is the one failure a shared cache cannot recover from.
    """
    baseline = calculation_inputs()

    assert digest_of(baseline) != digest_of(calculation_inputs(timesteps=3))
    assert digest_of(baseline) != digest_of(calculation_inputs(seconds_per_timestep=3600))
    assert digest_of(baseline) != digest_of(calculation_inputs(year=2022))
    for field_name, changed_value in (
        ("tilt_angle_in_degrees", 0.0),
        ("azimuth_angle_in_degrees", None),
        ("area_in_m2", 7.0),
        ("frame_area_fraction_reduction_factor", 0.25),
        ("glass_solar_transmittance", 0.55),
        ("nonperpendicular_reduction_factor", 0.85),
        ("external_shading_vertical_reduction_factor", 0.7),
    ):
        changed = calculation_inputs(windows=(window_geometry(**{field_name: changed_value}),))
        assert digest_of(baseline) != digest_of(changed), f"{field_name} does not reach the key"
    assert digest_of(baseline) != digest_of(calculation_inputs(windows=(window_geometry(), window_geometry())))


@pytest.mark.base
def test_the_produced_series_has_one_value_per_timestep_and_is_dark_at_night() -> None:
    """The series covers the simulated span, and a timestep with no irradiance at all carries no gain.

    The short circuit is what makes a full year of minutes affordable -- it skips the pvlib call for
    every dark timestep -- and it is also load-bearing for the result: the optics return NaN for a sun
    below the horizon, which the component would otherwise propagate into the thermal model.

    Catches: an off-by-one in the produced length, and a short circuit that stops short-circuiting.
    """
    inputs = calculation_inputs(
        timesteps=3,
        azimuth_in_degrees=(180.0, 180.0, 180.0),
        direct_normal_irradiance_in_watt_per_square_meter=(620.0, 0.0, 620.0),
        diffuse_horizontal_irradiance_in_watt_per_square_meter=(145.0, 0.0, 145.0),
        global_horizontal_irradiance_in_watt_per_square_meter=(480.0, 0.0, 480.0),
        direct_normal_irradiance_extra_in_watt_per_square_meter=(1361.0, 0.0, 1361.0),
        apparent_zenith_in_degrees=(40.0, 95.0, 40.0),
    )

    produced = produce_solar_gains(inputs)

    assert list(produced.columns) == [SERIES_COLUMN]
    gains = produced[SERIES_COLUMN].tolist()
    assert len(gains) == 3
    assert gains[1] == 0.0
    assert gains[0] > 0.0
    assert gains[0] == gains[2]


@pytest.mark.base
def test_weather_series_shorter_than_the_span_are_refused() -> None:
    """A series that does not cover the simulated span stops the producer, naming what is wrong.

    Catches: an IndexError from the middle of the loop, which would say ``tuple index out of range``
    and nothing about the weather.
    """
    inputs = calculation_inputs(timesteps=5)

    with pytest.raises(ValueError, match="weather series"):
        produce_solar_gains(inputs)
