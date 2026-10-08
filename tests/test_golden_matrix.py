"""Unit tests for ``scripts/golden_matrix.py`` — the CI matrix emitter.

``build_matrix`` is stdlib-only and reads a plain config dict, so most tests
need neither HiSim nor the committed config on disk. Two tests load the real
``golden_config.json`` to confirm the emitter agrees with the shipped config.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.golden_matrix import (
    DEFAULT_PAIR_SECONDS,
    HORIZON_FACTORIES,
    PAIRS_AT_ONCE,
    build_matrix,
    build_shards,
    composed_setups,
    main,
    pair_seconds,
)

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

    ``one_week_july`` is the example precisely because it is a factory the golden gate must not
    gain today: a mid-year start date changes no profile in HiSim, so a July parameter set would
    bless January's numbers under a summer name (R11.5 as amended, and the fence in
    ``scripts/p3_parity_matrix.py``). The refusal here is about the vocabulary rather than about
    that defect, and the two do not contradict each other — both refuse the factory.
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


# --------------------------------------------------------------------------- #
# --shards: the cells the golden workflows fan out over
# --------------------------------------------------------------------------- #
def _weighted_config() -> dict:
    """Six week setups with measured seconds (one unmeasured), two of them also run the year."""
    weights = {"heavy": 250, "big": 240, "mid1": 120, "mid2": 110, "small": 20}
    setups: list[dict[str, Any]] = [
        {"id": sid, "path": "x", "seconds": {"one_week_60s": s}, "horizons": ["week"]} for sid, s in weights.items()
    ]
    setups.append({"id": "unmeasured", "path": "x", "horizons": ["week"]})
    setups[0]["horizons"] = ["week", "year"]
    setups[0]["seconds"] = {"one_week_60s": 250, "full_year_60s": 470}
    setups[1]["horizons"] = ["week", "year"]
    return {
        "setups": setups,
        "parameter_sets": [
            {"id": "one_week_60s", "factory": "one_week_only"},
            {"id": "full_year_60s", "factory": "full_year"},
        ],
    }


def _pairs_of(cell: dict) -> list[tuple[str, str]]:
    return [tuple(token.split(":")) for token in cell["pairs"].split()]  # type: ignore[misc]


def test_shards_cover_every_pair_exactly_once_and_never_mix_horizons() -> None:
    """Catches a shard dropping or duplicating a pair, which would shrink or double the gate silently."""
    config = _weighted_config()
    cells = build_shards(config, 2)["include"]

    emitted = [pair for cell in cells for pair in _pairs_of(cell)]
    expected = [(p["setup"], p["param"]) for p in build_matrix(config)["include"]]
    assert sorted(emitted) == sorted(expected)
    assert len(emitted) == len(set(emitted))
    for cell in cells:
        params = {param for _, param in _pairs_of(cell)}
        assert len(params) == 1
        assert cell["name"].startswith(cell["horizon"] + "-")
        assert cell["count"] == len(_pairs_of(cell))


def test_shards_run_as_many_pairs_at_once_as_the_horizon_memory_allows() -> None:
    """A week shard runs four pairs at once, a full-year shard two (about 6 GiB each on a 16 GB runner)."""
    cells = build_shards(_weighted_config(), 3)["include"]
    assert {cell["horizon"]: cell["jobs"] for cell in cells} == {"week": 4, "year": 2}
    assert PAIRS_AT_ONCE["week"] == 4 and PAIRS_AT_ONCE["year"] == 2


def test_shards_separate_the_heaviest_pairs_and_list_them_first() -> None:
    """The two longest week pairs land in different shards, each shard starting with its heaviest.

    Catches the balance ignoring the weights (both long pairs in one shard would double its wall
    time) and a shard listing a short pair first, which would start the long one last.
    """
    week = [cell for cell in build_shards(_weighted_config(), 2)["include"] if cell["horizon"] == "week"]
    firsts = {_pairs_of(cell)[0][0] for cell in week}
    assert firsts == {"heavy", "big"}
    assert {cell["seconds"] for cell in week} == {250, 240}


def test_a_pair_without_measured_seconds_weighs_the_default() -> None:
    """A new setup needs no measurement to join a shard; a measured one weighs its seconds."""
    assert pair_seconds({"id": "s"}, "one_week_60s") == DEFAULT_PAIR_SECONDS
    assert pair_seconds({"id": "s", "seconds": {"one_week_60s": 7}}, "one_week_60s") == 7


def test_more_shards_than_pairs_gives_no_empty_shard() -> None:
    """An empty shard would still pay a container start for nothing, and pass vacuously."""
    cells = build_shards(_weighted_config(), 10)["include"]
    assert all(cell["count"] >= 1 for cell in cells)
    assert len([cell for cell in cells if cell["horizon"] == "year"]) == 2


@pytest.mark.parametrize(
    "seconds, message",
    [
        ({"one_week_60s": 0}, "positive"),
        ([60], "positive"),
        ({"one_week_60s": True}, "positive"),
        ({"typo_60s": 60}, "typo_60s"),
    ],
)
def test_a_malformed_weight_is_refused(seconds: object, message: str) -> None:
    """A typo'd weight fails the discover job instead of silently weighing the default."""
    config = _weighted_config()
    config["setups"][2]["seconds"] = seconds
    with pytest.raises(ValueError, match=message):
        build_shards(config, 2)


def test_zero_shards_is_refused() -> None:
    """Zero shards would emit an empty matrix: a gate that runs nothing and stays green."""
    with pytest.raises(ValueError, match="shards"):
        build_shards(_weighted_config(), 0)


def test_the_shipped_config_shards_into_four_week_and_four_year_jobs() -> None:
    """The workflows' own call: 22 week pairs and 8 full-year pairs, four shards each."""
    config = json.loads(REAL_CONFIG.read_text())
    week = build_shards(config, 4, horizon="week")["include"]
    year = build_shards(config, 4, horizon="year")["include"]
    assert len(week) == 4 and sum(cell["count"] for cell in week) == 22
    assert len(year) == 4 and sum(cell["count"] for cell in year) == 8


def test_the_cli_prints_shards_as_one_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    """The discover jobs write the output into ``$GITHUB_OUTPUT``, which takes one line."""
    assert main(["--horizon", "year", "--shards", "4"]) == 0
    printed = capsys.readouterr().out
    assert printed.count("\n") == 1
    assert json.loads(printed)["include"][0]["name"] == "year-1"


def test_with_yaml_counts_each_pair_twice_on_two_lanes() -> None:
    """golden-check runs each pair's YAML twin beside it: the shard's lanes carry both runs.

    Catches the balance ignoring the twins, which would pack a shard as if it had half the work.
    With two shards of four lanes, the 250 s pair takes two lanes of one shard and the 240 s pair
    two of the other; the rest fill in without lengthening either.
    """
    config = _weighted_config()
    single = [c for c in build_shards(config, 2)["include"] if c["horizon"] == "week"]
    double = [c for c in build_shards(config, 2, with_yaml=True)["include"] if c["horizon"] == "week"]

    assert sorted(c["seconds"] for c in double) == [240, 250]
    assert sum(c["count"] for c in double) == sum(c["count"] for c in single) == 6
    assert {_pairs_of(c)[0][0] for c in double} == {"heavy", "big"}
    one_lane_year = build_shards(config, 1, horizon="year", with_yaml=True)["include"]
    # Two year pairs (470 s, and 120 s unmeasured) with their twins on the year's two lanes: 590 each.
    assert one_lane_year[0]["seconds"] == 470 + DEFAULT_PAIR_SECONDS


def test_with_composed_counts_a_third_run_for_a_setup_with_a_composed_file() -> None:
    """golden-check runs a pair's composed file beside its Python run and its twin: one more lane of its weight.

    Catches the balance ignoring the composed runs. With one shard of four lanes, the 250 s pair takes three lanes
    and the 240 s pair, two runs, cannot share a lane with it without ending at 490 s.
    """
    config = _weighted_config()
    one_shard = build_shards(config, 1, horizon="week", with_yaml=True, composed=frozenset({"heavy"}))["include"]
    assert one_shard[0]["seconds"] > build_shards(config, 1, horizon="week", with_yaml=True)["include"][0]["seconds"]
    year = build_shards(config, 1, horizon="year", composed=frozenset({"heavy"}))["include"]
    # The 470 s year pair and its composed file on the year's two lanes, the unmeasured pair after the shorter one.
    assert year[0]["seconds"] == 470 + DEFAULT_PAIR_SECONDS


def test_the_composed_setups_are_the_ones_with_a_composed_file_beside_the_config() -> None:
    """The shipped config's composed setups are read from ``energy_systems/``: today the heat-pump sizer."""
    config = json.loads(REAL_CONFIG.read_text())
    assert composed_setups(config, REPO_ROOT) == frozenset({"household_heatpump_building_sizer"})
