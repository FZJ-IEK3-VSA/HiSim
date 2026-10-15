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

from hisim.energy_system.assemblies.twins import COMPOSED_TWINS
from scripts.golden_matrix import (
    DEFAULT_PAIR_SECONDS,
    HORIZON_FACTORIES,
    PAIRS_AT_ONCE,
    build_matrix,
    build_runs,
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

    The expected counts are hand-written truths about the shipped config — twenty-three entries (the
    twenty-two Python setups and the energy-system file ``gas_boiler_household``) run the week, and all
    of them but those in :data:`CANNOT_RUN_A_FULL_YEAR` (today none) the full year as well (owner
    decision 2026-10-11), and all of them but those in :data:`CANNOT_RUN_JULY` the July week
    (hisim-9g2.8) — rather than re-derived through the very predicate under test, which would
    reproduce any logic error on both sides of the assertion. A new setup joining the config moves
    these literals on purpose: the reviewer of that change should see the gate grow.
    """
    config = json.loads(REAL_CONFIG.read_text())

    assert len(config["setups"]) == 23
    assert len(build_matrix(config, horizon="week")["include"]) == 23
    assert len(build_matrix(config, horizon="july_week")["include"]) == 23 - len(CANNOT_RUN_JULY)
    assert len(build_matrix(config, horizon="year")["include"]) == 23
    assert len(build_matrix(config)["include"]) == 69 - len(CANNOT_RUN_JULY)


#: The setups whose July week would still read January in part, each with the bead whose fix lifts its
#: restriction to the January week and the year in ``golden_config.json``. A setup leaves this table in the
#: change that fixes it.
CANNOT_RUN_JULY: dict[str, str] = {
    # The CSV loader indexes its wind profile by the timestep number, so a July week reads January's wind.
    "electrolyzer_with_renewables": "hisim-9g2.14",
}


def test_every_setup_kept_from_the_july_week_is_restricted_for_its_bead() -> None:
    """Catches a setup leaving the July week without a recorded reason, or keeping its restriction after its fix.

    The July pairs bless the first true July references (hisim-9g2.8). A setup whose July is still in part
    January's stays out of them, and :data:`CANNOT_RUN_JULY` names the bead that lifts the restriction.
    """
    config = json.loads(REAL_CONFIG.read_text())
    july_setups = {pair["setup"] for pair in build_matrix(config, horizon="july_week")["include"]}
    kept_out = {setup["id"] for setup in config["setups"]} - july_setups

    assert kept_out == set(CANNOT_RUN_JULY)


#: The setups that cannot complete a full year yet, each with the bead whose fix lifts its week-only
#: restriction in ``golden_config.json``. A setup leaves this table in the change that fixes it.
CANNOT_RUN_A_FULL_YEAR: dict[str, str] = {
}


def test_every_shipped_setup_has_a_weighted_full_year_golden() -> None:
    """Every setup of the shipped config runs the full year, with a full-year weight at least its week's.

    Catches a setup slipping back to the week alone (owner decision 2026-10-11: every system setup
    has a full-year golden, because the first week of January never runs a solar collector's pump),
    a full-year pair weighing the 120 s default, which would pack several real one-to-twelve
    minute runs onto one lane of a shard, and a full-year weight below the week's, which no
    measurement gives. The only setups excused are those of :data:`CANNOT_RUN_A_FULL_YEAR`, and
    only while they are restricted to the week; they need no full-year weight.
    """
    config = json.loads(REAL_CONFIG.read_text())
    year_runners = [s for s in config["setups"] if s["id"] not in CANNOT_RUN_A_FULL_YEAR]

    year_setups = {pair["setup"] for pair in build_matrix(config, horizon="year")["include"]}
    unweighted = [s["id"] for s in year_runners if "full_year_60s" not in s.get("seconds", {})]
    lighter_than_their_week = [
        s["id"] for s in year_runners
        if s.get("seconds", {}).get("full_year_60s", 0) < s.get("seconds", {}).get("one_week_60s", 0)
    ]

    assert year_setups == {s["id"] for s in year_runners}
    assert not unweighted, f"setups without a full_year_60s weight: {unweighted}"
    assert not lighter_than_their_week, f"full-year weight below the week's: {lighter_than_their_week}"


def test_every_system_setup_is_in_the_golden_config() -> None:
    """Every ``system_setups/*.py`` that defines a ``setup_function`` is a setup of the shipped config.

    Catches a new energy system joining ``system_setups/`` without a golden: the gate would never
    notice a change to it. Drivers that call another setup's ``setup_function`` (the
    ``economic_example/`` scripts) define none of their own and are not energy systems of their own.
    An energy system written in YAML alone (``energy_systems/gas_boiler_household.energy_system.yaml``)
    is an entry of its own and has no ``system_setups/`` file, so it is not checked here.
    """
    config = json.loads(REAL_CONFIG.read_text())
    configured = {Path(s["path"]).as_posix() for s in config["setups"]}
    with_setup_function = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "system_setups").glob("*.py")
        if "def setup_function(" in path.read_text(encoding="utf-8")
    }

    assert with_setup_function - configured == set()


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

    ``three_months_only`` is the example: a factory of ``SimulationParameters`` that no horizon names.
    """
    config = _config()
    config["parameter_sets"].append({"id": "three_months_60s", "factory": "three_months_only"})

    with pytest.raises(ValueError, match="three_months_only"):
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
    """The ``horizons`` restriction keeps a week-only setup out of the full-year matrix.

    Catches: the restriction being read but not applied, which would run a setup in a horizon its
    config entry excludes. The shipped config restricts no setup, so the config here is synthetic.
    """
    config = _config()
    config["setups"][1]["horizons"] = ["week"]

    year_setups = {pair["setup"] for pair in build_matrix(config, horizon="year")["include"]}
    week_setups = {pair["setup"] for pair in build_matrix(config, horizon="week")["include"]}

    assert year_setups == {"s1"}
    assert week_setups == {"s1", "s2"}


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
    assert PAIRS_AT_ONCE["week"] == PAIRS_AT_ONCE["july_week"] == 4 and PAIRS_AT_ONCE["year"] == 2


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


def test_the_shipped_config_shards_into_the_workflows_jobs() -> None:
    """The workflows' own calls: 23 week pairs in four shards, 23 full-year pairs in five.

    golden-check cuts the week into four shards with the YAML twins and the composed files
    counted (``--with-yaml --with-composed``), the year into five shards with the composed files
    counted. Catches a shard holding no pair, which would start a runner for nothing.
    """
    config = json.loads(REAL_CONFIG.read_text())
    composed = composed_setups(config, REPO_ROOT)
    week = build_shards(config, 4, horizon="week", with_yaml=True, composed=composed)["include"]
    year = build_shards(config, 5, horizon="year", composed=composed)["include"]
    assert len(week) == 4 and sum(cell["count"] for cell in week) == 23
    assert len(year) == 5 and sum(cell["count"] for cell in year) == 23
    assert all(cell["count"] > 0 for cell in [*week, *year])


def test_the_shipped_config_gives_the_bless_a_job_per_year_pair_and_four_week_shards() -> None:
    """The bless's call (golden-update.yml, ``--shards 4 --one-pair-per-job year``): 23 + 4 jobs.

    Every Python pair of both horizons is blessed exactly once, each full-year pair in a job of its
    own running only it (``jobs`` 1), the week pairs in four shards. Catches a pair the bless would
    no longer regenerate, which would leave its golden stale without anyone noticing.
    """
    config = json.loads(REAL_CONFIG.read_text())
    cells = build_shards(config, 4, one_pair_per_job=frozenset({"year"}))["include"]
    year = [cell for cell in cells if cell["horizon"] == "year"]
    week = [cell for cell in cells if cell["horizon"] == "week"]
    assert len(year) == 23 and all(cell["count"] == 1 and cell["jobs"] == 1 for cell in year)
    assert len(week) == 4 and sum(cell["count"] for cell in week) == 23
    emitted = sorted(pair for cell in cells for pair in _pairs_of(cell))
    assert emitted == sorted((p["setup"], p["param"]) for p in build_matrix(config)["include"])
    assert len(emitted) == 46


def test_one_pair_per_job_gives_each_pair_of_the_horizon_a_cell_first_and_shards_the_rest() -> None:
    """A horizon named in ``one_pair_per_job`` gets a cell per pair, heaviest first, ahead of the shards.

    Catches a pair of that horizon dropped or doubled, a cell running more pairs at once than it has
    runs (``jobs``), names that collide (the bless names its artifacts after them, and a collision
    would make one upload overwrite another's file), and the other horizons' shards changing.
    """
    config = _weighted_config()
    cells = build_shards(config, 2, one_pair_per_job=frozenset({"year"}), composed=frozenset({"heavy"}))["include"]
    assert [(cell["horizon"], cell["pairs"], cell["jobs"], cell["seconds"]) for cell in cells[:2]] == [
        ("year", "heavy:full_year_60s", 2, 470),
        ("year", "big:full_year_60s", 1, DEFAULT_PAIR_SECONDS),
    ]
    assert all(cell["count"] == 1 for cell in cells[:2])
    week = build_shards(config, 2, horizon="week", composed=frozenset({"heavy"}))["include"]
    assert cells[2:] == week
    assert len({cell["name"] for cell in cells}) == len(cells)


def test_one_pair_per_job_refuses_an_unknown_horizon_and_a_call_without_shards() -> None:
    """A typo'd horizon would silently shard what was meant to run alone; without --shards it would do nothing."""
    with pytest.raises(ValueError, match="Unknown horizons"):
        build_shards(_weighted_config(), 2, one_pair_per_job=frozenset({"years"}))
    with pytest.raises(SystemExit):
        main(["--runs", "--one-pair-per-job", "year"])


def test_runs_give_every_run_a_cell_of_its_own_heaviest_first() -> None:
    """golden-year's matrix: one cell per (pair, mode), each pair's Python run and its composed run where it has one.

    Catches a run dropped or doubled (a gate covering less, or paying a runner twice) and an order that would queue
    the longest run last.
    """
    config = _weighted_config()
    cells = build_runs(config, horizon="year", composed=frozenset({"heavy"}))["include"]
    assert [(cell["setup"], cell["mode"], cell["seconds"]) for cell in cells] == [
        ("heavy", "python", 470),
        ("heavy", "composed", 470),
        ("big", "python", DEFAULT_PAIR_SECONDS),
    ]
    assert all(cell["pair"] == f"{cell['setup']}:{cell['param']}" for cell in cells)
    assert len({cell["name"] for cell in cells}) == len(cells)
    week = build_runs(config, horizon="week", with_yaml=True)["include"]
    week_pairs = build_matrix(config, horizon="week")["include"]
    expected = sorted((pair["setup"], mode) for pair in week_pairs for mode in ("python", "yaml"))
    assert sorted((cell["setup"], cell["mode"]) for cell in week) == expected
    assert [cell["seconds"] for cell in week] == sorted((cell["seconds"] for cell in week), reverse=True)


def test_the_shipped_config_gives_golden_year_one_job_per_run() -> None:
    """The year tier's call: the 23 full-year runs of the config's entries and one composed run per setup with a composed file."""
    config = json.loads(REAL_CONFIG.read_text())
    composed = composed_setups(config, REPO_ROOT)
    cells = build_runs(config, horizon="year", composed=composed)["include"]
    python_setups = sorted(cell["setup"] for cell in cells if cell["mode"] == "python")
    assert python_setups == sorted(setup["id"] for setup in config["setups"])
    assert {cell["setup"] for cell in cells if cell["mode"] == "composed"} == set(composed)
    assert len(cells) == 23 + len(composed)


def test_runs_and_shards_exclude_each_other() -> None:
    """A cell is either one run or one shard; asking for both is refused rather than one silently winning."""
    with pytest.raises(SystemExit):
        main(["--horizon", "year", "--runs", "--shards", "4"])


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
    """The shipped config's composed setups, read from ``energy_systems/``, are the setups of the library table.

    Catches the matrix (stdlib only, reading files) and the gate (reading the table) disagreeing on which pairs run a
    composed file.
    """
    config = json.loads(REAL_CONFIG.read_text())
    in_table = {setup["id"] for setup in config["setups"] if Path(setup["path"]).stem in COMPOSED_TWINS}
    assert "household_heatpump_building_sizer" in in_table
    assert composed_setups(config, REPO_ROOT) == frozenset(in_table)
