"""T-5, T-6 for stage 1b: the recoverable hot-water losses, their defaults, their tables and the heating days.

DISCLAIMER - SYNTHETIC TEST DATA. Every table value in this module is invented to test the code,
and any building in it is a placeholder or a HiSim test record: no value comes from an OENORM
table, and no number computed here is the heating demand of any real building. Never use them for
a study or a real calculation. The standards themselves (OENORM B 8110-5, B 8110-6-1, B 8110-6-2,
H 5050-1, H 5056-1, edition 2024-03) can be purchased from Austrian Standards International:
https://www.austrian-standards.at

The tables come from :mod:`epc.tests.at.synthetic.at_tables`; every expectation is a hand computation of
H 5056-1:2024-03 Eq. (15), (22), (24), (26), (28) and (51) with those values. No value of a licensed table
appears here (R8.5).
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from epc.engines.at import balance, defaults, hot_water
from epc.engines.at.climate import Climate
from epc.engines.at.data_tables import AustrianTables, load_tables
from epc.engines.at.envelope import Envelope
from epc.engines.at.hot_water_tables import HOT_WATER_FILE, OPERATION_FILE
from epc.model.errors import EpcError
from epc.model.hot_water import HotWaterSystem, PipeRun
from epc.tests.at.synthetic.at_tables import OPERATING_HOURS, USAGE_DAYS, hot_water_value, synthetic_tables, write
from epc.tests.at.synthetic.test_at_envelope import _building

pytestmark = pytest.mark.base

#: BF and θ_i,h of the hand-written building (100 m², one dwelling unit: class "1-1").
AREA = 100.0
SET_TEMPERATURE = 31.1

#: The usage hours d_Nutz · t_h,d of each month.
HOURS = tuple(days * OPERATING_HOURS for days in USAGE_DAYS)


def _system(**changes: Any) -> HotWaterSystem:
    """A stated system without circulation: risers inside (1/3, fittings insulated), the rest outside."""
    system = HotWaterSystem(
        circulation=False,
        distribution=PipeRun(conditioned=False, insulation="0", fittings_insulated=False),
        risers=PipeRun(conditioned=True, insulation="1/3", fittings_insulated=True),
        stub_material="plastic",
        storage_kind="indirect",
        storage_conditioned=False,
    )
    return dataclasses.replace(system, **changes)


def _losses(tables: AustrianTables, system: HotWaterSystem) -> hot_water.RecoverableLosses:
    """Q_TW,beh of the hand-written building with a system."""
    return hot_water.recoverable_losses(tables.hot_water, _building(), system, tables.operation["1-1"],
                                        SET_TEMPERATURE, AREA)


def _pipe_temperature() -> float:
    """θ_TW,m without circulation by the formula of Tab. 8 with the synthetic coefficients."""
    span = 1 - math.exp(-AREA / hot_water_value("temperature_no_circulation_scale_bf"))
    base = hot_water_value("temperature_no_circulation_base")
    return base + hot_water_value("temperature_no_circulation_span") * span


@pytest.fixture(name="tables")
def fixture_tables(tmp_path: Path) -> AustrianTables:
    """The synthetic tables, loaded."""
    return load_tables(write(tmp_path, synthetic_tables()))


def test_a_building_without_a_system_takes_the_default_with_an_assumption() -> None:
    """D45: the OIB default system; a stated system is the description's own."""
    system, assumption = defaults.hot_water_system(_building())
    assert system == defaults.DEFAULT_HOT_WATER_SYSTEM
    assert assumption is not None and "OIB" in assumption.source
    stated = dataclasses.replace(_building(), hot_water=_system())
    assert defaults.hot_water_system(stated) == (_system(), None)


def test_the_default_system_loses_only_by_the_fittings_and_the_stub_lines(tables: AustrianTables) -> None:
    """Eq. (22) and (28): the default's runs and storage lie outside, so Q_TW,beh is emission plus stub lines."""
    losses = _losses(tables, defaults.DEFAULT_HOT_WATER_SYSTEM)
    emission = hot_water_value("armatures_two_handle") + hot_water_value("metering_individual")
    stub = hot_water_value("length_stub_residential_per_bf") * AREA * hot_water_value("stub_loss_steel_or_unknown")
    assert losses.emission == tuple(emission * AREA * hours / 1000 for hours in HOURS)
    assert losses.stub_lines == tuple(stub * hours / 1000 for hours in HOURS)
    assert losses.pipes == (0.0,) * 12 and losses.pipe_heat_flow_in_w == 0.0
    assert losses.total == tuple(a + b for a, b in zip(losses.emission, losses.stub_lines))


def test_the_pipes_inside_follow_eq_24_and_26(tables: AustrianTables) -> None:
    """Eq. (26) for the risers inside; the distribution inside adds its f_ero,1 term."""
    riser = hot_water_value("length_riser_per_bf") * AREA
    riser_term = riser * hot_water_value("pipe_loss_insulation_1/3") * hot_water_value("fero_2_1/3_insulated")
    difference = _pipe_temperature() - SET_TEMPERATURE
    losses = _losses(tables, _system())
    assert losses.pipe_heat_flow_in_w == difference * riser_term
    assert losses.pipes == tuple(difference * riser_term * hours / 1000 for hours in HOURS)
    inside = _system(distribution=PipeRun(conditioned=True, insulation="2/3", fittings_insulated=False))
    distribution = (hot_water_value("length_distribution_constant")
                    + hot_water_value("length_distribution_per_bf") * AREA)
    distribution_term = (distribution * hot_water_value("pipe_loss_insulation_2/3")
                         * hot_water_value("fero_1_2/3_uninsulated"))
    assert _losses(tables, inside).pipe_heat_flow_in_w == difference * (distribution_term + riser_term)


def test_stated_lengths_replace_the_defaults_of_tab_6(tables: AustrianTables) -> None:
    """A stated riser and stub length are taken as stated."""
    system = _system(risers=PipeRun(conditioned=True, insulation="1/3", fittings_insulated=True, length_in_m=7.0),
                     stub_length_in_m=3.0)
    losses = _losses(tables, system)
    riser_term = 7.0 * hot_water_value("pipe_loss_insulation_1/3") * hot_water_value("fero_2_1/3_insulated")
    assert losses.pipe_heat_flow_in_w == (_pipe_temperature() - SET_TEMPERATURE) * riser_term
    assert losses.stub_lines == tuple(3.0 * hot_water_value("stub_loss_plastic") * hours / 1000 for hours in HOURS)


@pytest.mark.parametrize(
    "changes, what",
    [
        ({"storage_conditioned": True}, "storage in the conditioned area"),
        ({"risers": PipeRun(conditioned=True, insulation="0", fittings_insulated=False)}, "uninsulated"),
        ({"circulation": True}, "circulation in the conditioned area"),
    ],
    ids=["storage inside", "uninsulated pipe inside", "circulation inside"],
)
def test_what_stage_1b_does_not_compute_is_refused(tables: AustrianTables, changes: dict, what: str) -> None:
    """E-20 instead of a guess: a storage, an uninsulated pipe or circulation in the conditioned area."""
    with pytest.raises(EpcError) as refusal:
        _losses(tables, _system(**changes))
    assert refusal.value.error_id == "E-20"
    assert what in refusal.value.message


def test_circulation_outside_the_conditioned_area_needs_no_temperature(tables: AustrianTables) -> None:
    """With every run outside, circulation adds nothing to the pipes inside (the default system)."""
    outside = _system(circulation=True, risers=PipeRun(conditioned=False, insulation="1/3", fittings_insulated=True))
    assert _losses(tables, outside).pipe_heat_flow_in_w == 0.0


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"risers": PipeRun(True, "1/4", True)}, "hot_water.risers.insulation"),
        ({"risers": PipeRun(True, "1/3", True, -1.0)}, "hot_water.risers.length_in_m"),
        ({"stub_material": "lead"}, "hot_water.stub_material"),
        ({"storage_kind": "none", "storage_conditioned": True}, "hot_water.storage_conditioned"),
        ({"armatures": "golden"}, "hot_water.armatures"),
        ({"stub_length_in_m": float("nan")}, "hot_water.stub_length_in_m"),
    ],
)
def test_a_stated_system_is_checked_by_the_description(changes: dict, field: str) -> None:
    """E-18 for a value no hot-water system can have."""
    with pytest.raises(EpcError) as refusal:
        dataclasses.replace(_building(), hot_water=_system(**changes))
    assert refusal.value.error_id == "E-18"
    assert f"'{field}'" in refusal.value.message


def test_the_site_heating_share_adds_the_recoverable_losses_to_the_gains() -> None:
    """Eq. (51) with Q_TW,beh for the heating days; η keeps the γ of B 8110-6-1 Eq. (58)."""
    envelope = Envelope((), 150.0, 100.0, 0.0, 150.0, 1.0, 1.0, 100.0, 0.5, 50.0, ())
    inertia = balance.Dynamics(1.0, 12.0, 2.0, 1.5)
    temperatures = [-2.0, 0.0, 4.0, 9.0, 14.0, 17.0, 19.0, 18.5, 15.0, 9.5, 4.0, 0.0]
    climate = Climate(tuple(temperatures), MappingProxyType({}))
    gains = tuple(200.0 + 50.0 * month for month in range(12))
    recoverable = tuple(30.0 for _ in range(12))
    with_losses = balance.balance(envelope, inertia, 20.5, climate, gains, reference=False, recoverable=recoverable)
    without = balance.balance(envelope, inertia, 20.5, climate, gains, reference=False)
    ratios = [(gain + 30.0) / loss for gain, loss in zip(gains, with_losses.losses)]
    assert with_losses.heating_share == pytest.approx(balance.heating_shares(ratios, inertia.gain_loss_ratio_limit))
    assert with_losses.utilisation == without.utilisation
    assert sum(with_losses.heating_days) < sum(without.heating_days)
    reference = balance.balance(envelope, inertia, 20.5, climate, gains, reference=True)
    assert reference.heating_share == balance.heating_shares(
        [gain / loss for gain, loss in zip(gains, reference.losses)], inertia.gain_loss_ratio_limit)


@pytest.mark.parametrize(
    "file, old, new, problem",
    [
        (HOT_WATER_FILE, "0,armatures_two_handle,", "0,armatures_golden,", "not one of the keys"),
        (HOT_WATER_FILE, "0,metering_individual,", "0,metering_individual,-", "must not be negative"),
        (OPERATION_FILE, "1-1,1,", "7-7,1,", "not a class of the usage profiles"),
        (OPERATION_FILE, "2-5,3,13.0,20.0", "2-5,3,13.0,21.0", "two values of t_h,d"),
    ],
    ids=["unknown key", "negative value", "unknown class", "two operating hours"],
)
def test_the_hot_water_tables_refuse_what_is_malformed(tmp_path: Path, file: str, old: str, new: str,
                                                       problem: str) -> None:
    """E-19 naming the file and the line."""
    tables = synthetic_tables()
    lines = tables[file]
    index = next(number for number, line in enumerate(lines) if line.startswith(old))
    lines[index] = new + lines[index][len(old):]
    with pytest.raises(EpcError) as refusal:
        load_tables(write(tmp_path, tables))
    assert refusal.value.error_id == "E-19"
    assert refusal.value.message.startswith(f"{file}, line ") and problem in refusal.value.message


def test_a_missing_key_is_an_incomplete_table(tmp_path: Path) -> None:
    """E-12 for a hot-water table without one of its keys."""
    tables = synthetic_tables()
    tables[HOT_WATER_FILE] = [line for line in tables[HOT_WATER_FILE] if ",stub_loss_copper," not in line]
    with pytest.raises(EpcError) as refusal:
        load_tables(write(tmp_path, tables))
    assert refusal.value.error_id == "E-12"
