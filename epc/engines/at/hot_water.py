"""The recoverable hot-water losses Q_TW,beh of OENORM H 5056-1:2024-03 Eq. (15) (stage 1b, D44).

Q_TW,beh is the part of the hot-water losses that stays in heated rooms, per month:

- the emission losses of Eq. (22): (q_TW,WA,1 + q_TW,WA,2) · BF · d_Nutz · t_h,d / 1000, with the fittings
  of Tab. 3 and the metering of Tab. 4;
- the pipes in the conditioned area, Eq. (24) and (26): q*_TW,Ro,beh = Δθ_TW,m,beh · ((ℓ_Verteil · q_Ro +
  ℓ_Zirkl-V · q_Ro) · f_ero,1 + (ℓ_Steigl · q_Ro + ℓ_Zirkl-S · q_Ro) · f_ero,2), with the lengths of the runs
  in the conditioned area (stated, else Tab. 6), Δθ = θ_TW,m − θ_i,h (Tab. 8), q_Ro of Tab. 9 and f_ero of
  Tab. 10; the circulation lines lie and are insulated like the run beside them; times d_Nutz · t_h,d / 1000;
- the temperature-change losses of the stub lines, Eq. (28): ℓ_Stichl · q_Ro,A · d_Nutz · t_h,d / 1000, with
  the material of Tab. 7;
- the storage, when it stands in the conditioned area (Eq. (29), (30)).

d_Nutz and t_h,d are those of B 8110-5:2024-03 Tab. 4, 5 and 8 for the building's dwelling-unit class.
Three cases are refused with ``E-20`` rather than computed: a storage in the conditioned area (Eq. (29),
(30) are not part of stage 1b), an uninsulated pipe in the conditioned area (Tab. 9 states its loss per
pipe diameter with a unit still to be decided), and circulation in the conditioned area (Tab. 8
distinguishes single-family houses, which the norm does not define; still to be decided).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from epc.engines.at.hot_water_tables import Operation
from epc.engines.at.table_reading import Monthly
from epc.model.building import BuildingDescription
from epc.model.errors import EpcError, EpcErrorKind
from epc.model.hot_water import HotWaterSystem, PipeRun

#: The insulation ratio of Tab. 10 each ratio of Tab. 9 falls under.
_FERO_RATIO: Mapping[str, str] = {"2": "3/3", "1.5": "3/3", "1": "3/3", "2/3": "2/3", "1/3": "1/3", "0": "0"}


@dataclass(frozen=True)
class RecoverableLosses:
    """Q_TW,beh and its parts per month, January to December, in kWh.

    Attributes:
        emission: Q_TW,WA, Eq. (22).
        pipes: The pipes in the conditioned area, q*_TW,Ro,beh · d_Nutz · t_h,d / 1000 of Eq. (15).
        stub_lines: Q_TW,WV,A, Eq. (28).
        total: Q_TW,beh, Eq. (15).
        pipe_heat_flow_in_w: q*_TW,Ro,beh, Eq. (26).
    """

    emission: Monthly
    pipes: Monthly
    stub_lines: Monthly
    total: Monthly
    pipe_heat_flow_in_w: float


def recoverable_losses(
    values: Mapping[str, float],
    building: BuildingDescription,
    system: HotWaterSystem,
    operation: Operation,
    set_temperature_in_c: float,
    reference_area_in_m2: float,
) -> RecoverableLosses:
    """Q_TW,beh of every month by H 5056-1:2024-03 Eq. (15).

    Args:
        values: The values of H 5056-1 Tab. 3–10 and 15 by key.
        building: The description, for the messages.
        system: The hot-water system, stated or defaulted.
        operation: d_Nutz and t_h,d of the building's dwelling-unit class.
        set_temperature_in_c: θ_i,h.
        reference_area_in_m2: BF.

    Returns:
        Q_TW,beh and its parts.

    Raises:
        EpcError: ``E-20`` for a storage, an uninsulated pipe or circulation in the conditioned area.
    """
    if system.storage_conditioned:
        raise _not_yet(building, "a hot-water storage in the conditioned area",
                       "H 5056-1 Eq. (29), (30) are not part of stage 1b")
    area = reference_area_in_m2
    hours = tuple(days * operation.operating_hours_per_day for days in operation.usage_days)
    flow = _pipe_heat_flow(values, building, system, set_temperature_in_c, area)
    stub_length = (system.stub_length_in_m if system.stub_length_in_m is not None
                   else values["length_stub_residential_per_bf"] * area)
    emission_rate = values[f"armatures_{system.armatures}"] + values[f"metering_{system.metering}"]
    emission = tuple(emission_rate * area * each / 1000 for each in hours)
    pipes = tuple(flow * each / 1000 for each in hours)
    stub_lines = tuple(stub_length * values[f"stub_loss_{system.stub_material}"] * each / 1000 for each in hours)
    total = tuple(sum(parts) for parts in zip(emission, pipes, stub_lines))
    return RecoverableLosses(emission, pipes, stub_lines, total, flow)


def _pipe_heat_flow(values: Mapping[str, float], building: BuildingDescription, system: HotWaterSystem,
                    set_temperature_in_c: float, area: float) -> float:
    """q*_TW,Ro,beh of Eq. (26): the runs in the conditioned area with their circulation lines, in W."""
    circulation = 1.0 if system.circulation else 0.0
    runs = (
        (system.distribution, 1, values["length_distribution_constant"] + values["length_distribution_per_bf"] * area,
         circulation * (values["length_circulation_distribution_constant"]
                        + values["length_circulation_distribution_per_bf"] * area)),
        (system.risers, 2, values["length_riser_per_bf"] * area,
         circulation * values["length_circulation_riser_per_bf"] * area),
    )
    conductance = 0.0
    for run, group, default_length, circulation_length in runs:
        length = (run.length_in_m if run.length_in_m is not None else default_length) + circulation_length
        if not run.conditioned or length == 0:
            continue
        conductance += length * _pipe_loss(values, building, run) * _fero(values, run, group)
    if conductance == 0:
        return 0.0
    return (_pipe_temperature(values, building, system, area) - set_temperature_in_c) * conductance


def _pipe_loss(values: Mapping[str, float], building: BuildingDescription, run: PipeRun) -> float:
    """q_Ro of Tab. 9 for the run's insulation, in W/(m · K)."""
    if run.insulation == "0":
        raise _not_yet(building, "an uninsulated hot-water pipe in the conditioned area",
                       "H 5056-1 Tab. 9 states its loss per pipe diameter; the unit of d_ro is to be decided")
    return values[f"pipe_loss_insulation_{run.insulation}"]


def _fero(values: Mapping[str, float], run: PipeRun, group: int) -> float:
    """f_ero,1 (distribution) or f_ero,2 (risers) of Tab. 10 for the run's insulation and fittings."""
    fittings = "insulated" if run.fittings_insulated else "uninsulated"
    return values[f"fero_{group}_{_FERO_RATIO[run.insulation]}_{fittings}"]


def _pipe_temperature(values: Mapping[str, float], building: BuildingDescription, system: HotWaterSystem,
                      area: float) -> float:
    """θ_TW,m of the distribution and risers by Tab. 8, in °C."""
    if system.circulation:
        raise _not_yet(building, "circulation in the conditioned area",
                       "H 5056-1 Tab. 8 distinguishes single-family houses, which the norm does not define; "
                       "to be decided")
    span = 1 - math.exp(-area / values["temperature_no_circulation_scale_bf"])
    return values["temperature_no_circulation_base"] + values["temperature_no_circulation_span"] * span


def _not_yet(building: BuildingDescription, what: str, reason: str) -> EpcError:
    """The ``E-20`` refusal of a case stage 1b does not compute."""
    return EpcError(EpcErrorKind.NOT_COMPUTED_YET, name=building.name, what=what, cc="AT", reason=reason)
