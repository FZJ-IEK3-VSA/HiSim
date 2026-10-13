"""The result keys of the Austrian engine: indicators, annual terms and monthly series.

The heating demand per gross floor area under both climates, HWB_RK and HWB_SK (B 8110-6-1:2024-03
Eq. (51), (64); H 5050-1:2024-03 Eq. (5)), and the reference area BGF form the common core and its
site-climate key (D22). Beside them stand the building's characteristic values U_m, ℓ_c and LEK_T
(Eq. (63a), (1), (63b)), the heating days of each climate and the heating degree days HGT 22/14 of the
site (B 8110-5:2024-03 Eq. (4) with the days of each month, Q-D2). The terms carry what the indicators
were computed from, so that a reader can follow every step.
"""

from __future__ import annotations

from dataclasses import dataclass

from epc.engines.at.balance import MONTH_DAYS, Balance, Dynamics
from epc.engines.at.climate import Climate
from epc.engines.at.envelope import Envelope
from epc.engines.at.table_reading import Monthly
from epc.model.result import EpcResult

#: B 8110-5:2024-03 Eq. (4): the months counted in HGT 22/14 have a mean outdoor temperature of at most 14 °C.
HEATING_DEGREE_DAY_LIMIT_IN_C = 14.0

#: B 8110-6-1:2024-03 Eq. (63b): LEK_T = 300 · U_m / (2 + ℓ_c).
LEK_FACTOR = 300.0
LEK_LENGTH_IN_M = 2.0


@dataclass(frozen=True)
class Computation:
    """Everything the engine computed for one building, from which the result keys are taken.

    Attributes:
        gross_floor_area_in_m2: BGF.
        gross_volume_in_m3: V.
        set_temperature_in_c: θ_i,h.
        envelope: The conductances.
        inertia: C, τ, a and γ_lim.
        internal_gains: Q_i per month, the same under both climates.
        reference_climate: The reference climate.
        site_climate: The site climate.
        reference: The balance under the reference climate.
        site: The balance under the site climate.
    """

    gross_floor_area_in_m2: float
    gross_volume_in_m3: float
    set_temperature_in_c: float
    envelope: Envelope
    inertia: Dynamics
    internal_gains: Monthly
    reference_climate: Climate
    site_climate: Climate
    reference: Balance
    site: Balance


def heating_degree_days(set_temperature_in_c: float, outdoor_temperatures_in_c: Monthly) -> float:
    """HGT 22/14 by B 8110-5:2024-03 Eq. (4): Σ (θ_i,h − θ_e,j) · d_j over the months with θ_e,j ≤ 14 °C, in Kd."""
    return sum(
        (set_temperature_in_c - outdoor) * days
        for outdoor, days in zip(outdoor_temperatures_in_c, MONTH_DAYS)
        if outdoor <= HEATING_DEGREE_DAY_LIMIT_IN_C
    )


def fill(result: EpcResult, computation: Computation) -> None:
    """Adds every indicator, annual term and monthly series of the Austrian engine to a result.

    Args:
        result: The result of the building.
        computation: What the engine computed.
    """
    bgf = computation.gross_floor_area_in_m2
    envelope = computation.envelope
    result.add_indicator(
        "heating_demand_reference_climate", computation.reference.annual_heating_demand / bgf, "kWh/(m2 a)",
        cites="B 8110-6-1 Eq. (51), (52), (64); H 5050-1 Eq. (5), (9)", label="HWB_RK",
    )
    result.add_indicator(
        "heating_demand_site_climate", computation.site.annual_heating_demand / bgf, "kWh/(m2 a)",
        cites="B 8110-6-1 Eq. (51), (53), (64); H 5056-1 7.1", label="HWB_SK",
        note="stage 1: without recoverable hot-water losses (Q_TW,beh = 0)",
    )
    result.add_indicator("indicator_reference_area", bgf, "m2", cites="B 8110-6-1 Section 4, BF = 0.8 BGF", label="BGF")
    result.add_indicator(
        "heating_degree_days_22_14_site_climate",
        heating_degree_days(computation.set_temperature_in_c, computation.site_climate.outdoor_temperatures_in_c),
        "Kd", cites="B 8110-5 Eq. (4), with the days of each month", label="HGT 22/14",
    )
    result.add_indicator("mean_u_value", envelope.mean_u_value_in_w_per_m2_per_k, "W/(m2 K)",
                         cites="B 8110-6-1 Eq. (63a)", label="U_m")
    result.add_indicator("characteristic_length", envelope.characteristic_length_in_m, "m",
                         cites="B 8110-6-1 Eq. (1)", label="l_c")
    u_m, l_c = envelope.mean_u_value_in_w_per_m2_per_k, envelope.characteristic_length_in_m
    lek_t = LEK_FACTOR * u_m / (LEK_LENGTH_IN_M + l_c)
    result.add_indicator("lek_t", lek_t, "1", cites="B 8110-6-1 Eq. (63b)", label="LEK_T")
    for climate, balance_ in (("reference_climate", computation.reference), ("site_climate", computation.site)):
        result.add_indicator(f"heating_days_{climate}", sum(balance_.heating_days), "d",
                             cites="H 5050-1 Eq. (9); H 5056-1 Eq. (49)")
    _terms(result, computation)


def _terms(result: EpcResult, computation: Computation) -> None:
    """The annual terms and the monthly series behind the indicators."""
    envelope, inertia = computation.envelope, computation.inertia
    for key, value, unit, cites in (
        ("transmission_conductance", envelope.transmission_conductance_in_w_per_k, "W/K", "B 8110-6-1 Eq. (10a)"),
        ("element_conductance", envelope.element_conductance_in_w_per_k, "W/K", "B 8110-6-1 Eq. (10a), sum f A U"),
        ("thermal_bridge_conductance", envelope.thermal_bridge_conductance_in_w_per_k, "W/K", "B 8110-6-1 Eq. (11a)"),
        ("ventilation_conductance", envelope.ventilation_conductance_in_w_per_k, "W/K", "B 8110-6-1 Eq. (13)-(15)"),
        ("envelope_area", envelope.envelope_area_in_m2, "m2", "B 8110-6-1 3.9"),
        ("gross_volume", computation.gross_volume_in_m3, "m3", "B 8110-6-1 Section 4"),
        ("thermal_capacity", inertia.thermal_capacity_in_wh_per_k, "Wh/K", "B 8110-6-1 Eq. (50)"),
        ("time_constant", inertia.time_constant_in_h, "h", "B 8110-6-1 Eq. (49)"),
        ("utilisation_parameter", inertia.utilisation_parameter, "1", "B 8110-6-1 Eq. (59)"),
        ("internal_gains", sum(computation.internal_gains), "kWh/a", "B 8110-6-1 Eq. (37)"),
    ):
        result.add_term(key, value, unit, cites=cites)
    for climate, balance_ in (("reference_climate", computation.reference), ("site_climate", computation.site)):
        solar = tuple(gain - internal for gain, internal in zip(balance_.gains, computation.internal_gains))
        for key, values, cites in (
            ("transmission_losses", balance_.transmission_losses, "B 8110-6-1 Eq. (29a)"),
            ("ventilation_losses", balance_.ventilation_losses, "B 8110-6-1 Eq. (30)"),
            ("solar_gains", solar, "B 8110-6-1 Eq. (43)"),
            ("heating_demand", balance_.heating_demand, "B 8110-6-1 Eq. (51)"),
        ):
            result.add_term(f"{key}_{climate}", sum(values), "kWh/a", cites=cites)
        result.add_monthly(f"heating_demand_{climate}", balance_.heating_demand, "kWh")
        result.add_monthly(f"losses_{climate}", balance_.losses, "kWh")
        result.add_monthly(f"gains_{climate}", balance_.gains, "kWh")
        result.add_monthly(f"heating_share_{climate}", balance_.heating_share, "1")
    result.add_monthly("outdoor_temperature_reference_climate", computation.reference_climate.outdoor_temperatures_in_c,
                       "degC")
    result.add_monthly("outdoor_temperature_site_climate", computation.site_climate.outdoor_temperatures_in_c, "degC")
