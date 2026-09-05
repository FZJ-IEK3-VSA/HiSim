"""Unit tests for ``scripts/golden_matrix.py`` — the CI matrix emitter.

``build_matrix`` is stdlib-only and reads a plain config dict, so most tests
need neither HiSim nor the committed config on disk. Two tests load the real
``golden_config.json`` to confirm the emitter agrees with the shipped config.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.golden_matrix import HORIZON_FACTORIES, build_matrix

pytestmark = pytest.mark.base

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = REPO_ROOT / "scripts" / "golden_config.json"


def _config() -> dict:
    return {
        "setups": [{"id": "s1", "path": "x"}, {"id": "s2", "path": "y"}],
        "parameter_sets": [
            {"id": "one_week_60s", "factory": "one_week_only"},
            {"id": "full_year_60s", "factory": "full_year"},
        ],
    }


def test_build_matrix_all_pairs() -> None:
    """With no horizon filter the matrix is the full setup x param cartesian product."""
    matrix = build_matrix(_config())
    assert matrix == {
        "include": [
            {"setup": "s1", "param": "one_week_60s"},
            {"setup": "s1", "param": "full_year_60s"},
            {"setup": "s2", "param": "one_week_60s"},
            {"setup": "s2", "param": "full_year_60s"},
        ]
    }


def test_build_matrix_horizon_week() -> None:
    """The ``week`` horizon keeps only week-factory parameter sets."""
    matrix = build_matrix(_config(), horizon="week")
    assert matrix == {
        "include": [
            {"setup": "s1", "param": "one_week_60s"},
            {"setup": "s2", "param": "one_week_60s"},
        ]
    }


def test_build_matrix_horizon_year() -> None:
    """The ``year`` horizon keeps only full-year parameter sets."""
    matrix = build_matrix(_config(), horizon="year")
    assert [e["param"] for e in matrix["include"]] == ["full_year_60s", "full_year_60s"]


def test_build_matrix_unknown_horizon_raises() -> None:
    """An unrecognised horizon name raises ``ValueError``."""
    with pytest.raises(ValueError, match="horizon"):
        build_matrix(_config(), horizon="fortnight")


def test_horizon_factories_cover_config_factories() -> None:
    """Every factory used by the real config maps to a known horizon."""
    config = json.loads(REAL_CONFIG.read_text())
    used = {p["factory"] for p in config["parameter_sets"]}
    assert used <= set(HORIZON_FACTORIES.values())


def test_build_matrix_real_config_expands_to_the_pairs_the_horizons_allow() -> None:
    """The shipped config expands to one pair per (setup, parameter_set) the setup's horizons permit.

    The expected counts are hand-written truths about the shipped config — twenty-two setups run the
    week, only the original eight carry the full year — rather than re-derived through the very
    predicate under test, which would reproduce any logic error on both sides of the assertion.
    A new setup joining the config moves these literals on purpose: the reviewer of that change
    should see the gate grow.
    """
    config = json.loads(REAL_CONFIG.read_text())

    assert len(config["setups"]) == 22
    assert len(build_matrix(config, horizon="week")["include"]) == 22
    assert len(build_matrix(config, horizon="year")["include"]) == 8
    assert len(build_matrix(config)["include"]) == 30
    assert 30 < len(config["setups"]) * len(config["parameter_sets"]), (
        "the shipped config restricts at least one setup, so the full product would overcount"
    )


def test_a_malformed_horizons_value_fails_the_matrix_loudly() -> None:
    """Catches a config mistake silently shrinking the gate instead of failing the discover job.

    An empty list would make a setup participate in nothing — it vanishes from every matrix with
    nothing red anywhere — and a bare string would iterate per character, reporting unknown
    horizons ['w', 'e', 'e', 'k']. Both are refused with the setup named.
    """
    malformed: tuple = ([], "week", 3)
    for wrong in malformed:
        config = _config()
        config["setups"][0]["horizons"] = wrong
        with pytest.raises(ValueError, match="horizons|s1"):
            build_matrix(config)


def test_a_parameter_set_factory_outside_the_vocabulary_is_refused() -> None:
    """Catches an unmapped factory silently splitting restricted from unrestricted setups.

    A factory without a horizon resolves to None, and ``None in horizons`` is False — restricted
    setups would silently skip the parameter set while unrestricted ones run it. A new factory
    has to enter the vocabulary consciously instead.
    """
    config = _config()
    config["parameter_sets"].append({"id": "july_60s", "factory": "one_week_july"})

    with pytest.raises(ValueError, match="one_week_july"):
        build_matrix(config)


def test_the_matrix_and_the_runner_select_the_same_pairs() -> None:
    """Catches the CI matrix emitter and the runner gating two different fleets.

    The participation rule lives once in the shared vocabulary, and this is the proof it stays
    that way: the pairs CI fans out over the shipped config and the pairs a local run selects
    must be the identical set, or a one-sided edit has split the gate.
    """
    from scripts.runner import load_config, select_pairs

    emitted = {(pair["setup"], pair["param"]) for pair in build_matrix(json.loads(REAL_CONFIG.read_text()))["include"]}
    selected = {(setup.id, param.id) for setup, param in select_pairs(load_config(REAL_CONFIG))}

    assert emitted == selected


def test_a_setup_restricted_to_the_week_never_enters_the_year_matrix() -> None:
    """The ``horizons`` restriction keeps a week-only setup out of the expensive full-year matrix.

    Catches: the restriction being read but not applied, which would silently multiply the
    full-year CI cost by the whole fleet.
    """
    config = json.loads(REAL_CONFIG.read_text())
    week_only = [s["id"] for s in config["setups"] if s.get("horizons") == ["week"]]
    assert week_only, "the shipped config carries week-only setups"

    year_setups = {pair["setup"] for pair in build_matrix(config, horizon="year")["include"]}
    week_setups = {pair["setup"] for pair in build_matrix(config, horizon="week")["include"]}

    for setup_id in week_only:
        assert setup_id not in year_setups
        assert setup_id in week_setups
