"""The conductances of the Austrian engine: transmission over HiSim's elements, thermal bridges, ventilation.

Transmission follows the simplified approach of B 8110-6-1:2024-03 5.3: every element of the description
contributes f_i,h · A_i · U_i with the heating-case factor of the Tab. 3–5 row its situation takes
(:func:`~epc.engines.at.defaults.correction_factor_row`), and the thermal bridges add the default of
Eq. (11a) with its lower bound. An element against a conditioned neighbour lies outside the thermal
envelope (Section 4, p. 8): it is in neither the conductance nor the envelope area (I-10).

Ventilation is natural ventilation for a residential building (6.2.2): the air volume of Eq. (13), the
hygienic air change of the usage profile as the rate of Eq. (15a, b), and the conductance of Eq. (14).

The envelope area A, the mean U-value U_m = L_T / A (Eq. (63a)) and the characteristic length
ℓ_c = V / A (Eq. (1)) follow from the same elements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from epc.engines.at import defaults
from epc.engines.at.data_tables import AustrianTables
from epc.engines.at.usage import Usage
from epc.model.building import BuildingDescription
from epc.model.errors import EpcError, EpcErrorKind
from epc.model.result import Assumption

#: B 8110-6-1:2024-03 5.3.2 c), Eq. (11a): the factor, the U-value offset in W/(m² · K) and the share of the
#: lower bound of the thermal-bridge default.
THERMAL_BRIDGE_FACTOR = 0.2
THERMAL_BRIDGE_U_OFFSET = 0.75
THERMAL_BRIDGE_MINIMUM_SHARE = 0.1

#: B 8110-6-1:2024-03 6.1, Eq. (13): the fixed net room height in m.
NET_ROOM_HEIGHT = 2.6

#: B 8110-6-1:2024-03 6.2.1, Eq. (14): c_p,L · ρ_L of air in Wh/(m³ · K).
AIR_HEAT_CAPACITY = 0.34


@dataclass(frozen=True)
class ElementConductance:
    """What one element contributes: its row of Tab. 3–5 and f_i,h · A_i · U_i.

    Attributes:
        name: The element class.
        row: The key of its Tab. 3–5 row, ``None`` for a conditioned neighbour.
        factor: f_i,h of the row, ``None`` for a conditioned neighbour.
        conductance_in_w_per_k: f_i,h · A_i · U_i, 0 for a conditioned neighbour.
    """

    name: str
    row: Optional[str]
    factor: Optional[float]
    conductance_in_w_per_k: float


@dataclass(frozen=True)
class Envelope:
    """The conductances and the envelope of one building, shared by both climates (I-3).

    Attributes:
        elements: Each element's contribution, in the description's order.
        element_conductance_in_w_per_k: Σ f_i,h · A_i · U_i.
        envelope_area_in_m2: A, the elements' areas without those against a conditioned neighbour.
        thermal_bridge_conductance_in_w_per_k: L_ψ + L_χ by Eq. (11a), at least its lower bound.
        transmission_conductance_in_w_per_k: L_T,h by Eq. (10a).
        mean_u_value_in_w_per_m2_per_k: U_m = L_T,h / A, Eq. (63a).
        characteristic_length_in_m: ℓ_c = V / A, Eq. (1).
        air_volume_in_m3: V_V by Eq. (13).
        air_change_per_h: n_nL = n_L,hyg, Eq. (15b).
        ventilation_conductance_in_w_per_k: L_V = 0.34 · n_nL · V_V, Eq. (14), (15a).
        assumptions: The assumptions of the rows taken by default (D29).
    """

    elements: Tuple[ElementConductance, ...]
    element_conductance_in_w_per_k: float
    envelope_area_in_m2: float
    thermal_bridge_conductance_in_w_per_k: float
    transmission_conductance_in_w_per_k: float
    mean_u_value_in_w_per_m2_per_k: float
    characteristic_length_in_m: float
    air_volume_in_m3: float
    air_change_per_h: float
    ventilation_conductance_in_w_per_k: float
    assumptions: Tuple[Assumption, ...]


def envelope(
    tables: AustrianTables,
    building: BuildingDescription,
    usage: Usage,
    gross_floor_area_in_m2: float,
    gross_volume_in_m3: float,
) -> Envelope:
    """The conductances of a building in the heating case.

    Args:
        tables: The Austrian tables.
        building: The description.
        usage: The building's usage profile.
        gross_floor_area_in_m2: BGF.
        gross_volume_in_m3: V.

    Returns:
        Transmission, thermal bridges, ventilation, envelope area, U_m and ℓ_c.

    Raises:
        EpcError: ``E-18`` for a building whose envelope has no area.
    """
    contributions: List[ElementConductance] = []
    assumptions: List[Assumption] = []
    envelope_area = 0.0
    for element in building.elements:
        row, assumption = defaults.correction_factor_row(element)
        if assumption is not None:
            assumptions.append(assumption)
        if row is None:
            contributions.append(ElementConductance(element.name, None, None, 0.0))
            continue
        factor = tables.temperature_correction_factors[row].f_h
        conductance = factor * element.area_in_m2 * element.u_value_in_watt_per_m2_per_kelvin
        contributions.append(ElementConductance(element.name, row, factor, conductance))
        envelope_area += element.area_in_m2
    if envelope_area <= 0:
        raise EpcError(EpcErrorKind.VALUE_NOT_ADMISSIBLE, name=building.name, field="elements",
                       value="no area against outside air or an unconditioned space",
                       rule="a thermal envelope with an area")
    element_conductance = sum(contribution.conductance_in_w_per_k for contribution in contributions)
    bridges = thermal_bridge_conductance(element_conductance, envelope_area)
    transmission = element_conductance + bridges
    air_volume = defaults.REFERENCE_AREA_SHARE * gross_floor_area_in_m2 * NET_ROOM_HEIGHT
    air_change = usage.hygienic_air_change_per_h
    return Envelope(
        elements=tuple(contributions),
        element_conductance_in_w_per_k=element_conductance,
        envelope_area_in_m2=envelope_area,
        thermal_bridge_conductance_in_w_per_k=bridges,
        transmission_conductance_in_w_per_k=transmission,
        mean_u_value_in_w_per_m2_per_k=transmission / envelope_area,
        characteristic_length_in_m=gross_volume_in_m3 / envelope_area,
        air_volume_in_m3=air_volume,
        air_change_per_h=air_change,
        ventilation_conductance_in_w_per_k=AIR_HEAT_CAPACITY * air_change * air_volume,
        assumptions=tuple(assumptions),
    )


def thermal_bridge_conductance(element_conductance_in_w_per_k: float, envelope_area_in_m2: float) -> float:
    """L_ψ + L_χ by B 8110-6-1:2024-03 Eq. (11a), never below 0.1 · Σ f_i,h · A_i · U_i.

    Args:
        element_conductance_in_w_per_k: Σ f_i,h · A_i · U_i, which in the simplified approach is
            L_e + L_u + L_g.
        envelope_area_in_m2: Σ A_i of the envelope.

    Returns:
        The thermal-bridge conductance in W/K.
    """
    by_formula = (
        THERMAL_BRIDGE_FACTOR
        * (THERMAL_BRIDGE_U_OFFSET - element_conductance_in_w_per_k / envelope_area_in_m2)
        * element_conductance_in_w_per_k
    )
    return max(by_formula, THERMAL_BRIDGE_MINIMUM_SHARE * element_conductance_in_w_per_k)
