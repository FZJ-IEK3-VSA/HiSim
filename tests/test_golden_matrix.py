"""Unit tests for ``scripts/golden_matrix.py`` — the CI matrix emitter.

``build_matrix`` is stdlib-only and reads a plain config dict, so these tests need
neither HiSim nor the committed config on disk. One test does load the real
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


def test_build_matrix_real_config_has_20_pairs() -> None:
    """The shipped config expands to the expected 20 setup/param pairs."""
    config = json.loads(REAL_CONFIG.read_text())
    matrix = build_matrix(config)
    assert len(matrix["include"]) == len(config["setups"]) * len(config["parameter_sets"])
    assert len(matrix["include"]) == 20
