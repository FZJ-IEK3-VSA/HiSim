"""Produces the solar heat gain through the building's windows, timestep by timestep.

What this module produces is one artifact: the series of solar heat gains entering the building
through its windows, one value per simulated timestep, in watt. It is the third of the three
producers the survey of ``roadmap/cache_service_spec.md`` §12 lists -- the weather series, the PV
series and these gains -- and, like them, a module of its own rather than a section of the
component, so that its cache key can carry a fingerprint of exactly the code that computes it and
of nothing else.

**The DTO contract.** :func:`produce_solar_gains` takes exactly one argument, the frozen
:class:`SolarGainsInputs`, and is a pure function of it. The window geometry travels as plain
numbers -- one :class:`WindowGeometry` record per window -- rather than as the ``BuildingConfig``
it was derived from, so that two buildings whose TABULA rows differ in everything but their
windows share one entry, and so that repricing a fuel or renaming a component cannot invalidate a
physics result.

**The weather as a reference, not as key material.** The gains depend on six full-year weather
series. They enter the DTO as payload fields, excluded from the key by
:attr:`hisim.caching.keys.KeyMaterial.PAYLOAD`, paired with :attr:`SolarGainsInputs.weather_artifact_key`
-- the digest of the weather series' own cache key, which the ``Weather`` component publishes under
:attr:`hisim.components.weather.Weather.SERIES_ARTIFACT_KEY`. That is the Merkle composition of spec
§3.1: the weather's key already stands for its producer's code, its libraries and every one of its
inputs, so naming it is enough. A changed weather file, a changed timestep or an edit to the weather
calculation moves the weather's digest, and with it every gains entry computed from it, without this
module knowing anything about how the series were made.

**The layering rule.** This module may import pandas and the window model; it may not import the
component base class, the simulator or a repository (:class:`hisim.caching.keys.ProducerLayering`
checks it and ``tests/test_building_solar_gains_producer.py`` asserts the closure as an exact set).
``hisim.components.building.window`` is in the closure on purpose: the window optics *are* the
calculation, so an edit to them must move the key. Its own closure is one further module,
``hisim.log``, which nothing edits.

**Computed up front.** The component looks the whole series up, or produces and files it, before the
first timestep it needs a value for. The cache it replaces was filled in ``i_simulate`` and written
only at the last timestep, so an interrupted run -- the common case on a cluster -- cached nothing
and the next one recomputed everything. That is the "deferred cache writes" defect of spec §12, and
a static producer fixes it as a side effect: compute, write, then simulate.
"""

# clean

from dataclasses import dataclass, field
from typing import ClassVar, List, Optional, Tuple

import pandas as pd

from hisim.caching.keys import KeyMaterial
from hisim.components.building.window import Window

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Noah Pflugradt"

#: The name this producer files its artifact under: the ``{component}`` half of the cache key and the
#: prefix of the entry's filename. It names the calculation, not the component instance, so two
#: differently named buildings with the same windows under the same weather share one entry.
ARTIFACT_KIND: str = "building_solar_gains"

#: The single column of the produced frame, and the column name in the cached CSV. The name is the
#: one the legacy cache used, because it is also the component's output name.
SERIES_COLUMN: str = "solar_gain_through_windows"


@dataclass(frozen=True)
class WindowGeometry:
    """One window of the building, as the optics need it: angles, area and the four reduction factors.

    A window is nothing but these seven numbers to the calculation. They are key material, so a
    refurbishment that changes the glazing or a building scaled to another floor area computes -- and
    caches -- its own series.
    """

    #: Tilt against the horizontal in degrees: 90 for a facade window, 0 for a roof light.
    tilt_angle_in_degrees: float

    #: Azimuth in degrees, 180 being south. ``None`` for a horizontal window, which has no azimuth;
    #: the optics treat that as south, because at a tilt of zero the azimuth cancels out anyway.
    azimuth_angle_in_degrees: Optional[float]

    #: The window's area in square metres, already scaled to the building's conditioned floor area.
    area_in_m2: float

    #: TABULA ``F_f``: the fraction of the window area taken up by the frame.
    frame_area_fraction_reduction_factor: float

    #: TABULA ``g_gl_n``: the total solar energy transmittance for perpendicular radiation.
    glass_solar_transmittance: float

    #: TABULA ``F_w``: the correction for radiation that does not arrive perpendicularly.
    nonperpendicular_reduction_factor: float

    #: TABULA ``F_sh_vert``: the correction for external shading of a vertical surface.
    external_shading_vertical_reduction_factor: float

    def as_window(self) -> Window:
        """Build the optics object for this geometry.

        Returns:
            Window: the window model, with its reduction factor and area precomputed.
        """
        return Window(
            window_tilt_angle=self.tilt_angle_in_degrees,
            window_azimuth_angle=self.azimuth_angle_in_degrees,
            area=self.area_in_m2,
            frame_area_fraction_reduction_factor=self.frame_area_fraction_reduction_factor,
            glass_solar_transmittance=self.glass_solar_transmittance,
            nonperpendicular_reduction_factor=self.nonperpendicular_reduction_factor,
            external_shading_vertical_reduction_factor=self.external_shading_vertical_reduction_factor,
        )


@dataclass(frozen=True)
class SolarGainsInputs:
    """Everything :func:`produce_solar_gains` depends on, and nothing else.

    Key material: the weather artifact key, the simulated span and the windows. Payload, excluded
    from the key: the six weather series themselves, which the artifact key already identifies
    (:class:`hisim.caching.keys.KeyMaterial`). Every payload field pairs with the one key field that
    says what it is, which is the invariant spec §3.1 asks of a chained producer.
    """

    #: The digest of the weather series' cache key, published by the ``Weather`` component. It stands
    #: for the whole upstream calculation -- data file, station, year, timestep, producer code -- so
    #: it is the only thing this key needs to say about the weather.
    weather_artifact_key: str

    #: The simulated year. Implied by the weather key already; kept as a field so that an entry's
    #: ``.meta`` says which year it belongs to without resolving the upstream key first.
    year: int

    #: The simulation's timestep in seconds. Implied by the weather key too, and kept for the same
    #: reason: the two together with :attr:`timesteps` are what makes the series' shape readable.
    seconds_per_timestep: int

    #: How many values the series has. Unlike the weather's frame, which always covers the full year,
    #: this series is produced for the simulated span only: it costs one pvlib call per window per
    #: daylight timestep, which for the default five-window house is some 1.3 million calls over a
    #: year of minutes. A one-week run and a one-year run of the same building therefore deliberately
    #: do not share an entry -- the cheaper of the two would have to pay for the longer one.
    timesteps: int

    #: The building's windows. Order matters only in that the gains are summed in it.
    windows: Tuple[WindowGeometry, ...]

    #: Sun azimuth in degrees, one value per timestep. Payload: identified by the artifact key.
    azimuth_in_degrees: Tuple[float, ...] = field(metadata=KeyMaterial.PAYLOAD)

    #: Direct normal irradiance in W/m², one value per timestep. Payload.
    direct_normal_irradiance_in_watt_per_square_meter: Tuple[float, ...] = field(metadata=KeyMaterial.PAYLOAD)

    #: Diffuse horizontal irradiance in W/m², one value per timestep. Payload. The component and the
    #: window model have always called this one "direct horizontal", which it is not; the name is
    #: corrected here and the wiring it comes from is unchanged.
    diffuse_horizontal_irradiance_in_watt_per_square_meter: Tuple[float, ...] = field(metadata=KeyMaterial.PAYLOAD)

    #: Global horizontal irradiance in W/m², one value per timestep. Payload.
    global_horizontal_irradiance_in_watt_per_square_meter: Tuple[float, ...] = field(metadata=KeyMaterial.PAYLOAD)

    #: Extraterrestrial direct normal irradiance in W/m², one value per timestep. Payload.
    direct_normal_irradiance_extra_in_watt_per_square_meter: Tuple[float, ...] = field(metadata=KeyMaterial.PAYLOAD)

    #: Apparent solar zenith in degrees, one value per timestep. Payload.
    apparent_zenith_in_degrees: Tuple[float, ...] = field(metadata=KeyMaterial.PAYLOAD)

    #: The names of the six payload series, in the order :meth:`weather_series` returns them. Only
    #: an error message needs them, and an index would not say which series is too short.
    SERIES_NAMES: ClassVar[Tuple[str, ...]] = (
        "azimuth",
        "direct normal irradiance",
        "diffuse horizontal irradiance",
        "global horizontal irradiance",
        "extraterrestrial direct normal irradiance",
        "apparent zenith",
    )

    def weather_series(self) -> Tuple[Tuple[float, ...], ...]:
        """Return the six payload series in the order :func:`produce_solar_gains` reads them.

        Returns:
            Tuple[Tuple[float, ...], ...]: azimuth, DNI, DHI, GHI, DNI extra, apparent zenith.
        """
        return (
            self.azimuth_in_degrees,
            self.direct_normal_irradiance_in_watt_per_square_meter,
            self.diffuse_horizontal_irradiance_in_watt_per_square_meter,
            self.global_horizontal_irradiance_in_watt_per_square_meter,
            self.direct_normal_irradiance_extra_in_watt_per_square_meter,
            self.apparent_zenith_in_degrees,
        )

    def check_series_cover_the_span(self) -> None:
        """Refuse inputs whose weather series are shorter than the span they are read over.

        Raises:
            ValueError: if any of the six series has fewer values than :attr:`timesteps`. Indexing
                past the end would otherwise fail deep inside the loop, with a message naming a
                tuple rather than the weather.
        """
        for name, series in zip(self.SERIES_NAMES, self.weather_series()):
            if len(series) < self.timesteps:
                raise ValueError(
                    f"The weather series for the {name} has {len(series)} values, but the solar gains "
                    f"are produced for {self.timesteps} timesteps. The weather series must cover the "
                    "simulated span; check the year and the timestep the weather was produced for."
                )


def produce_solar_gains(inputs: SolarGainsInputs) -> pd.DataFrame:
    """Produce the solar-gain series the building simulates from.

    One value per timestep: the sum over the windows of the plane-of-array direct irradiance times
    each window's reduction factor and area. The result is a pure function of ``inputs``; it is what
    the cache stores under the key built from them.

    Args:
        inputs: the calculation's inputs.

    Returns:
        pd.DataFrame: one row per timestep, with the single column :data:`SERIES_COLUMN`.
    """
    inputs.check_series_cover_the_span()
    windows = [geometry.as_window() for geometry in inputs.windows]
    (
        azimuth,
        direct_normal_irradiance,
        diffuse_horizontal_irradiance,
        global_horizontal_irradiance,
        direct_normal_irradiance_extra,
        apparent_zenith,
    ) = inputs.weather_series()
    series: List[float] = [
        solar_heat_gain_through_windows(
            windows=windows,
            azimuth=azimuth[timestep],
            direct_normal_irradiance=direct_normal_irradiance[timestep],
            direct_horizontal_irradiance=diffuse_horizontal_irradiance[timestep],
            global_horizontal_irradiance=global_horizontal_irradiance[timestep],
            direct_normal_irradiance_extra=direct_normal_irradiance_extra[timestep],
            apparent_zenith=apparent_zenith[timestep],
        )
        for timestep in range(inputs.timesteps)
    ]
    return pd.DataFrame(series, columns=[SERIES_COLUMN])


def solar_heat_gain_through_windows(
    windows,
    azimuth,
    direct_normal_irradiance,
    direct_horizontal_irradiance,
    global_horizontal_irradiance,
    direct_normal_irradiance_extra,
    apparent_zenith,
):
    """Calculate the thermal solar gain passed to the building through the windows, for one timestep.

    Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header).

    Moved here from ``Building.get_solar_heat_gain_through_windows`` unchanged, the short circuit on
    a dark timestep included: with no irradiance at all there is no gain, and skipping the pvlib call
    is what keeps a full year of minutes affordable.
    """
    solar_heat_gains = 0.0

    if direct_normal_irradiance != 0 or direct_horizontal_irradiance != 0 or global_horizontal_irradiance != 0:
        for window in windows:
            solar_heat_gain = window.calc_solar_heat_gains(
                sun_azimuth=azimuth,
                direct_normal_irradiance=direct_normal_irradiance,
                direct_horizontal_irradiance=direct_horizontal_irradiance,
                global_horizontal_irradiance=global_horizontal_irradiance,
                direct_normal_irradiance_extra=direct_normal_irradiance_extra,
                apparent_zenith=apparent_zenith,
                window_tilt_angle=window.window_tilt_angle,
                window_azimuth_angle=window.window_azimuth_angle,
                reduction_factor_with_area=window.reduction_factor_with_area,
            )
            solar_heat_gains += solar_heat_gain
    return solar_heat_gains
