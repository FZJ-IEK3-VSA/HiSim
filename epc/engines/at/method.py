"""The Austrian engine: the heating demand of a residential building by the 2024-03 ÖNORM package.

:class:`AustrianEngine` is what the registry hands out for ``AT``. It loads the local tables once
(``E-11``, ``E-12``, ``E-19``) and computes one building in the order of the norm: the site fields it
reads (``E-09``, ``E-10``), the reference area and volume (:mod:`~epc.engines.at.defaults`), the usage
profile, the conductances, the thermal inertia, the gains, the recoverable hot-water losses
(:mod:`~epc.engines.at.hot_water`) and the monthly balance under the reference and under the site
climate, and finally the result keys (:mod:`~epc.engines.at.indicators`). Every norm
default it applies stands in the result's assumptions, and the digests of the tables in its provenance.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import ClassVar, Optional, Tuple

from epc.engines.at import balance, climate, defaults, envelope, gains, hot_water, indicators, usage
from epc.engines.at.climate_tables import CLIMATE_REGIONS
from epc.engines.at.data_tables import DEFAULT_DATA_FOLDER, AustrianTables, load_tables
from epc.model.building import BuildingDescription
from epc.model.errors import EpcError, EpcErrorKind
from epc.model.result import ClimateBasis, EpcResult

#: The site fields the Austrian method reads.
SITE_FIELDS = ("climate_region", "elevation_in_m")


class AustrianEngine:
    """The engine of Austria: OENORM B 8110-6-1 with B 8110-5, H 5050-1 and H 5056-1, edition 2024-03 (D5)."""

    country: ClassVar[str] = "AT"
    edition: ClassVar[str] = "OENORM B 8110-6-1:2024-03 / B 8110-5:2024-03 / H 5050-1:2024-03 / H 5056-1:2024-03"
    climate_basis: ClassVar[ClimateBasis] = "reference+site"

    def __init__(self, data_folder: Path = DEFAULT_DATA_FOLDER) -> None:
        """An engine reading its tables from ``data_folder`` (``epc/data/at`` unless told otherwise)."""
        self.data_folder = Path(data_folder)
        self._tables: Optional[AustrianTables] = None

    def load_data(self) -> None:
        """Loads the tables once; idempotent."""
        if self._tables is None:
            self._tables = load_tables(self.data_folder)

    def compute(self, building: BuildingDescription) -> EpcResult:
        """The heating demand of one building under the reference and the site climate.

        Args:
            building: The building as HiSim computed with it.

        Returns:
            The result with the common core, the site-climate key, the Austrian keys and the assumptions.

        Raises:
            EpcError: ``E-09``, ``E-10`` for the site; ``E-13`` for a window angle off the grid; ``E-14`` for a
                reference-climate month without positive losses; ``E-18`` for a building without envelope area;
                ``E-20`` for a hot-water system stage 1b does not compute.
        """
        self.load_data()
        tables = self._tables
        assert tables is not None
        region, elevation = _site(building)
        constants = tables.constants
        area = defaults.gross_floor_area(constants, building)
        volume = defaults.gross_volume(constants, building, area.value)
        profile = usage.usage(tables, building)
        conductances = envelope.envelope(tables, building, profile, area.value, volume.value)
        construction = defaults.construction(constants, building)
        inertia = balance.dynamics(constants, conductances, construction.value, volume.value)
        glass = defaults.glass_share(constants, building.windows)
        shading = defaults.shading_factor(constants, building)
        collectors = gains.collector_areas(constants, building.windows, glass.value, shading.value)
        internal = gains.internal_gains(constants, profile, area.value)
        reference_climate = climate.reference_climate(tables, building.windows)
        site_climate = climate.site_climate(tables, building.windows, region, elevation)
        system, system_assumption = defaults.hot_water_system(building)
        recoverable = hot_water.recoverable_losses(
            tables.hot_water, building, system, tables.operation[profile.dwelling_unit_class],
            profile.set_temperature_heating_in_c, constants[defaults.REFERENCE_AREA_SHARE] * area.value,
        )
        balances = [
            balance.balance(
                conductances, inertia, profile.set_temperature_heating_in_c, each,
                tuple(q_i + q_s for q_i, q_s in zip(internal, gains.solar_gains(collectors, each))),
                reference=is_reference, recoverable=None if is_reference else recoverable.total,
            )
            for each, is_reference in ((reference_climate, True), (site_climate, False))
        ]
        result = EpcResult(building)
        indicators.fill(result, indicators.Computation(
            area.value, volume.value, profile.set_temperature_heating_in_c, conductances, inertia, internal,
            reference_climate, site_climate, balances[0], balances[1], recoverable, constants,
        ))
        for defaulted in (area, volume, construction, glass, shading):
            if defaulted.assumption is not None:
                result.assume(defaulted.assumption.key, defaulted.assumption.value, defaulted.assumption.source)
        stated = (system_assumption,) if system_assumption is not None else ()
        for assumption in (defaults.thermal_bridges(), defaults.ventilation()) + stated + conductances.assumptions:
            result.assume(assumption.key, assumption.value, assumption.source)
        result.tables.update(tables.digests)
        return result


def _site(building: BuildingDescription) -> Tuple[str, float]:
    """The climate region and the elevation the method reads; ``E-09`` when missing, ``E-10`` when not admissible."""
    site = building.site
    for field in SITE_FIELDS:
        if getattr(site, field) is None:
            raise EpcError(EpcErrorKind.SITE_FIELD_MISSING, name=building.name, field=field,
                           cc=AustrianEngine.country, fields=", ".join(SITE_FIELDS))
    region, elevation = site.climate_region, site.elevation_in_m
    assert region is not None and elevation is not None
    if region not in CLIMATE_REGIONS:
        raise EpcError(EpcErrorKind.SITE_VALUE_NOT_ADMISSIBLE, name=building.name, field="climate_region",
                       value=region, cc=AustrianEngine.country, admissible=", ".join(CLIMATE_REGIONS))
    if not (math.isfinite(elevation) and elevation >= 0):
        raise EpcError(EpcErrorKind.SITE_VALUE_NOT_ADMISSIBLE, name=building.name, field="elevation_in_m",
                       value=elevation, cc=AustrianEngine.country, admissible="a finite number >= 0 (m)")
    return region, elevation
