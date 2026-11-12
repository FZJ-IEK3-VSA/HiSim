"""The input-mapping rules of spec §3.2 as pure functions: from a case description to 5R1C parameters.

Rules 1 and 2 (the factor on the U-values, decision D28), rule 3 (κ by the effective-thickness rule
and C_m), rule 4 (A_m and A_tot), rule 6 (the altitude adjustment of the air change rate) and rule 8
(F_w per window) live here; rule 5 needs no derivation (HiSim's own coupling conductances). Every
function returns what it used beside what it found, so ``parameters/<case>.json`` can show the
derivation.
"""

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
import pvlib

from hisim.verification.schema import Case, Construction, Glazing, Surface, Window

#: The cap of the effective thickness for a one-day period, in m (ISO 13790, FDIS 2007 Table 11).
EFFECTIVE_THICKNESS_CAP_IN_M: float = 0.10

#: Annual average air density of the 1650 m site, and the standard sea-level density the
#: infiltration rates are corrected from, in kg/m³ (ANSI/ASHRAE 140-2023, Annex B3.1, Table 7-4).
SITE_AIR_DENSITY_IN_KG_PER_M3: float = 1.0156
STANDARD_AIR_DENSITY_IN_KG_PER_M3: float = 1.2255


@dataclass(frozen=True)
class EffectiveThickness:
    """The three conditions of the effective-thickness rule of spec §3.2.3 and which one won."""

    #: Condition (a): half the total thickness of the construction.
    half_total_in_m: float
    #: Condition (b): the thickness up to the first insulation layer; the total when there is none.
    up_to_insulation_in_m: float
    #: Condition (c): the cap for a one-day period.
    cap_in_m: float
    #: The smallest of the three.
    effective_in_m: float
    #: ``"a"``, ``"b"`` or ``"c"``: the first condition that gives the smallest value.
    decisive_condition: str


def effective_thickness(construction: Construction) -> EffectiveThickness:
    """Apply the effective-thickness rule to a construction, inside to outside.

    Args:
        construction: the layers, inside to outside, with the insulation flag per layer.

    Returns:
        EffectiveThickness: the three candidates and the one that applies.
    """
    total = construction.total_thickness_in_m
    up_to_insulation = total
    thickness_so_far = 0.0
    for layer in construction.layers:
        if layer.insulation:
            up_to_insulation = thickness_so_far
            break
        thickness_so_far += layer.thickness_in_m
    candidates = (("a", total / 2.0), ("b", up_to_insulation), ("c", EFFECTIVE_THICKNESS_CAP_IN_M))
    decisive, effective = min(candidates, key=lambda candidate: candidate[1])
    return EffectiveThickness(
        half_total_in_m=total / 2.0,
        up_to_insulation_in_m=up_to_insulation,
        cap_in_m=EFFECTIVE_THICKNESS_CAP_IN_M,
        effective_in_m=effective,
        decisive_condition=decisive,
    )


def areal_heat_capacity_in_joule_per_m2_per_kelvin(construction: Construction, thickness_in_m: float) -> float:
    """κ of a construction: Σ ρ·c·d over the layers from the inside, up to the given thickness.

    Args:
        construction: the layers, inside to outside.
        thickness_in_m: how deep into the construction the capacity counts.

    Returns:
        float: κ in J/(m²K).
    """
    remaining = thickness_in_m
    kappa = 0.0
    for layer in construction.layers:
        if remaining <= 0:
            break
        counted = min(layer.thickness_in_m, remaining)
        kappa += layer.density_in_kg_per_m3 * layer.specific_heat_in_joule_per_kg_per_kelvin * counted
        remaining -= counted
    return kappa


@dataclass(frozen=True)
class SurfaceCapacity:
    """One opaque surface with its κ and how the effective thickness was found."""

    name: str
    kind: str
    area_in_m2: float
    construction: str
    thickness: EffectiveThickness
    kappa_in_joule_per_m2_per_kelvin: float


@dataclass(frozen=True)
class MassParameters:
    """C_m, A_m and A_tot of a case, with the per-surface values they are summed from (spec §3.2.3, §3.2.4)."""

    surfaces: Tuple[SurfaceCapacity, ...]
    #: C_m = Σ κ_j·A_j over the opaque surfaces, in J/K.
    thermal_capacity_in_joule_per_kelvin: float
    #: Σ A_j·κ_j², the divisor of A_m, in J²/(m²K²).
    sum_area_kappa_squared: float
    #: A_m = C_m² / Σ A_j·κ_j², in m².
    effective_mass_area_in_m2: float
    #: A_tot: the area of every surface facing the zone, windows included, in m².
    total_internal_surface_area_in_m2: float


def mass_parameters(case: Case) -> MassParameters:
    """Derive C_m, A_m and A_tot of a case from its surfaces, constructions and windows.

    The windows have no heat capacity and contribute to A_tot only; a window's κ of 0 would add 0
    to both sums of C_m and A_m anyway.

    Args:
        case: the resolved case.

    Returns:
        MassParameters: the three quantities and their per-surface terms.
    """
    capacities = []
    for surface in case.surfaces:
        construction = case.construction_of(surface)
        thickness = effective_thickness(construction)
        capacities.append(
            SurfaceCapacity(
                name=surface.name,
                kind=surface.kind,
                area_in_m2=surface.area_in_m2,
                construction=construction.name,
                thickness=thickness,
                kappa_in_joule_per_m2_per_kelvin=areal_heat_capacity_in_joule_per_m2_per_kelvin(construction, thickness.effective_in_m),
            )
        )
    thermal_capacity = sum(entry.kappa_in_joule_per_m2_per_kelvin * entry.area_in_m2 for entry in capacities)
    sum_area_kappa_squared = sum(entry.area_in_m2 * entry.kappa_in_joule_per_m2_per_kelvin**2 for entry in capacities)
    total_area = sum(surface.area_in_m2 for surface in case.surfaces) + sum(window.area_in_m2 for window in case.windows)
    return MassParameters(
        surfaces=tuple(capacities),
        thermal_capacity_in_joule_per_kelvin=thermal_capacity,
        sum_area_kappa_squared=sum_area_kappa_squared,
        effective_mass_area_in_m2=thermal_capacity**2 / sum_area_kappa_squared,
        total_internal_surface_area_in_m2=total_area,
    )


def altitude_adjusted_air_change_rate_per_hour(specified_rate_per_hour: float) -> float:
    """Correct an air change rate for a program that assumes standard sea-level air density.

    ANSI/ASHRAE 140-2023, 7.2.1.6.1 with Table 7-4 and Annex B3.1: the corrected rate is the
    specified rate times the ratio of the site's annual average air density to the standard density,
    so that the mass flow is the site's. HiSim's ventilation conductance uses a fixed
    0.34 Wh/(m³K), the standard density's value, so the correction applies.

    Args:
        specified_rate_per_hour: the rate the case states, in 1/h.

    Returns:
        float: the rate to pass to HiSim, in 1/h.
    """
    return specified_rate_per_hour * SITE_AIR_DENSITY_IN_KG_PER_M3 / STANDARD_AIR_DENSITY_IN_KG_PER_M3


def shgc_at_incidence(glazing: Glazing, incidence_angle_in_degrees: np.ndarray) -> np.ndarray:
    """The solar heat gain coefficient at each incidence angle, interpolated linearly in its cosine.

    ANSI/ASHRAE 140-2023, 7.2.1.11.3 permits interpolation between the tabulated angles using the
    cosine of the incidence angle. Angles of 90° and beyond give the table's value at 90°.

    Args:
        glazing: the glazing with its table by angle.
        incidence_angle_in_degrees: the angles, any shape.

    Returns:
        np.ndarray: the coefficients, same shape.
    """
    angles = np.array(sorted(glazing.shgc_by_incidence_angle), dtype=float)
    coefficients = np.array([glazing.shgc_by_incidence_angle[int(angle)] for angle in angles])
    cosines = np.cos(np.radians(angles))
    order = np.argsort(cosines)
    clipped = np.clip(incidence_angle_in_degrees, 0.0, 90.0)
    interpolated: np.ndarray = np.interp(np.cos(np.radians(clipped)), cosines[order], coefficients[order])
    return interpolated


@dataclass(frozen=True)
class WindowFactor:
    """F_w of one window: the irradiance-weighted annual mean of SHGC(θ)/SHGC_n (spec §3.2.8)."""

    window: str
    glazing: str
    #: Σ over the year of the beam irradiance on the plane, in Wh/m².
    beam_on_plane_in_watt_hour_per_m2: float
    #: Σ over the year of the sky-diffuse and ground-reflected irradiance on the plane, in Wh/m².
    diffuse_on_plane_in_watt_hour_per_m2: float
    #: Σ beam·SHGC(θ) + diffuse·SHGC_hemispherical, in Wh/m².
    weighted_gain_in_watt_hour_per_m2: float
    #: The factor: the weighted gain over SHGC_n times the total irradiance on the plane.
    nonperpendicular_reduction_factor: float
    #: SHGC_hemispherical / SHGC_n, reported beside the factor.
    hemispherical_ratio: float


def nonperpendicular_reduction_factor(
    window: Window,
    glazing: Glazing,
    weather: pd.DataFrame,
    latitude_in_degrees: float,
    longitude_in_degrees: float,
    ground_reflectance: float,
) -> WindowFactor:
    """F_w of a window from the hourly weather: beam from the angular table, diffuse from the hemispherical value.

    The irradiance on the plane is split as HiSim's window optics split it: beam, isotropic sky
    diffuse and ground-reflected (``pvlib.irradiance.get_total_irradiance`` under the isotropic
    model), with the sun position of ``pvlib.solarposition.get_solarposition`` at the hourly
    labels of the weather frame. F_w is the annual gain with the angle-dependent coefficients over
    the annual gain with SHGC_n, so that HiSim's constant F_w per window reproduces the annual
    gain of the angular model on the same plane irradiance.

    Args:
        window: the window; a horizontal one without an azimuth is taken as facing south, which
            at a tilt of 0 changes nothing.
        glazing: its glazing.
        weather: the hourly frame with the columns ``DNI``, ``DHI`` and ``GHI`` in W/m², indexed by
            time-zone-aware labels.
        latitude_in_degrees: the site's latitude.
        longitude_in_degrees: the site's longitude.
        ground_reflectance: the albedo the ground-reflected share uses.

    Returns:
        WindowFactor: the factor and the sums it is made of.
    """
    azimuth = 180.0 if window.azimuth_in_degrees is None else window.azimuth_in_degrees
    position = pvlib.solarposition.get_solarposition(weather.index, latitude_in_degrees, longitude_in_degrees)
    plane = pvlib.irradiance.get_total_irradiance(
        window.tilt_in_degrees,
        azimuth,
        position["apparent_zenith"],
        position["azimuth"],
        weather["DNI"],
        weather["GHI"],
        weather["DHI"],
        albedo=ground_reflectance,
        model="isotropic",
    )
    incidence = pvlib.irradiance.aoi(window.tilt_in_degrees, azimuth, position["apparent_zenith"], position["azimuth"])
    beam = np.nan_to_num(plane["poa_direct"].to_numpy(dtype=float))
    diffuse = np.nan_to_num((plane["poa_sky_diffuse"] + plane["poa_ground_diffuse"]).to_numpy(dtype=float))
    weighted = float(np.sum(beam * shgc_at_incidence(glazing, incidence.to_numpy(dtype=float))) + np.sum(diffuse) * glazing.shgc_hemispherical)
    total = float(np.sum(beam) + np.sum(diffuse))
    return WindowFactor(
        window=window.name,
        glazing=glazing.name,
        beam_on_plane_in_watt_hour_per_m2=float(np.sum(beam)),
        diffuse_on_plane_in_watt_hour_per_m2=float(np.sum(diffuse)),
        weighted_gain_in_watt_hour_per_m2=weighted,
        nonperpendicular_reduction_factor=weighted / (glazing.shgc_normal * total),
        hemispherical_ratio=glazing.shgc_hemispherical / glazing.shgc_normal,
    )


def area_weighted_u_value(pairs: Tuple[Tuple[float, float], ...]) -> Optional[float]:
    """The area-weighted mean of (area, U-value) pairs, ``None`` for no pairs.

    Pairs that all carry the same U-value return it untouched, so a printed value reaches the
    building as printed instead of as the last bit of a weighted sum.
    """
    if not pairs:
        return None
    u_values = {u_value for _, u_value in pairs}
    if len(u_values) == 1:
        return u_values.pop()
    total_area = sum(area for area, _ in pairs)
    return sum(area * u_value for area, u_value in pairs) / total_area


@dataclass(frozen=True)
class SteadyStateTransmission:
    """Rules 1 and 2 (spec §3.2.1, decision D28): the factor that keeps a case's U·A sum in steady state.

    The 5R1C network puts H_tr,is in series with H_tr,w and H_tr,op. The case's U-values reach from
    the zone air to the outdoor air, so a network built from them as they are loses less than their
    U·A sum in steady state. Every U-value HiSim gets is the case's times ``factor``, which makes the
    network's steady-state transmission conductance equal to that sum.
    """

    #: Σ U·A over the opaque surfaces and the windows, with the case's air-to-air U-values, in W/K.
    specified_conductance_in_watt_per_kelvin: float
    #: H_tr,is = h_is · A_tot, the conductance the network puts in series, in W/K.
    coupling_conductance_in_watt_per_kelvin: float
    #: f = H_tr,is / (H_tr,is − Σ U·A).
    factor: float


def steady_state_transmission(specified_conductance_in_watt_per_kelvin: float, coupling_conductance_in_watt_per_kelvin: float) -> SteadyStateTransmission:
    """The factor on a case's U-values, from their U·A sum and the coupling conductance.

    With H = Σ U·A and H_tr,is in series, the network's steady-state transmission conductance is
    1 / (1/H_tr,is + 1/(f·H)), which equals H for f = H_tr,is / (H_tr,is − H).

    Args:
        specified_conductance_in_watt_per_kelvin: H, the U·A sum with the case's U-values.
        coupling_conductance_in_watt_per_kelvin: H_tr,is of the case's building.

    Returns:
        SteadyStateTransmission: the factor and the two conductances it is made of.

    Raises:
        ValueError: if H is not positive or not below H_tr,is; no factor lets the series reach it then.
    """
    specified = specified_conductance_in_watt_per_kelvin
    coupling = coupling_conductance_in_watt_per_kelvin
    if not 0.0 < specified < coupling:
        raise ValueError(
            f"the U·A sum of {specified} W/K must be positive and below the coupling conductance of {coupling} W/K; "
            "no factor on the U-values lets the network reach it in steady state"
        )
    return SteadyStateTransmission(
        specified_conductance_in_watt_per_kelvin=specified,
        coupling_conductance_in_watt_per_kelvin=coupling,
        factor=coupling / (coupling - specified),
    )


def surfaces_by_kind(case: Case) -> Dict[str, Tuple[Surface, ...]]:
    """The case's opaque surfaces grouped by kind, in file order."""
    grouped: Dict[str, list] = {kind: [] for kind in Surface.KINDS}
    for surface in case.surfaces:
        grouped[surface.kind].append(surface)
    return {kind: tuple(surfaces) for kind, surfaces in grouped.items()}


def is_close(left: float, right: float, relative: float = 1e-9) -> bool:
    """Float equality for the derivation tests."""
    return math.isclose(left, right, rel_tol=relative, abs_tol=0.0)
