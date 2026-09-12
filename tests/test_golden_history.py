"""Tests for the golden-reference history visualiser.

Two halves, as the rig itself has two: the pure functions of
``scripts/golden_history.py`` are pinned against synthetic in-memory histories, and the
translation table of ``scripts/golden_kpi_renames.py`` is checked back against reality --
every old name it declares must occur in some historical golden, every new name in the
golden files as they stand today.

The reality half needs the git history of ``golden_references/``, which a shallow CI
checkout does not have; those tests skip themselves when fewer than two commits touch the
directory. Nothing here writes outside ``tmp_path``.
"""

from __future__ import annotations

import csv
import html.parser
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from scripts.golden_history import (
    MODE_ABSOLUTE,
    MODE_EMPTY,
    MODE_PERCENT,
    Commit,
    apply_kpi_renames,
    build_pair_history,
    build_panel,
    collect_moves,
    golden_commits,
    golden_files_at,
    movement_score,
    parse_formats,
    parse_pr_number,
    relative_change,
    render_html,
    render_index,
    render_png,
    resolve_pairs,
    write_moves_csv,
)
from scripts.golden_kpi_renames import (
    FLEET_WIDE,
    KPI_RENAMES,
    PAIR_RENAMES,
    KpiRename,
    canonical_pair,
    kpi_renames_for,
)

pytestmark = pytest.mark.base

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "golden_references"


def _commit(index: int, subject: str = "a bless (#100)") -> Commit:
    """Build a throwaway commit for a synthetic history.

    Args:
        index: Distinguishes the commits; it becomes the sha and the day of month.
        subject: The commit subject.

    Returns:
        The commit.
    """
    return Commit(sha=f"{index:040d}", date=f"2026-01-{index + 1:02d}", subject=subject)


def _history(values_per_commit: List[Dict[str, Any]], stem: str = "pair__one_week_60s") -> Any:
    """Assemble a synthetic pair history from one mapping per commit.

    Args:
        values_per_commit: The golden file's contents at each commit, oldest first.
        stem: The pair stem to build under.

    Returns:
        The assembled :class:`~scripts.golden_history.PairHistory`.
    """
    snapshots: List[Tuple[Commit, Optional[Dict[str, Any]]]] = [
        (_commit(index), contents) for index, contents in enumerate(values_per_commit)
    ]
    return build_pair_history(stem, snapshots)


# --------------------------------------------------------------------------- #
# PR numbers and labels
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "subject, expected",
    [
        ("Maintenance is a yearly cost, not a depreciating one (#657)", 657),
        ("Golden gate full fleet: all 22 setups gated (merges last) (#642)", 642),
        ("bless golden references: intentional simulation-output change", None),
        ("fix hp golden ref test one week", None),
        ("reverts (#12) and lands (#34)", 34),
        ("mentions #99 without parentheses", None),
        ("trailing whitespace is fine (#1) ", 1),
    ],
)
def test_parse_pr_number(subject: str, expected: Optional[int]) -> None:
    """A PR number is the trailing ``(#NNN)`` of a squash-merge subject and nothing else."""
    assert parse_pr_number(subject) == expected


def test_commit_label_prefers_the_pr_over_the_sha() -> None:
    """A tick reads as date plus PR, and falls back to the short sha when there is none."""
    assert Commit("abcdef1234", "2026-09-09", "a change (#657)").label == "2026-09-09 #657"
    assert Commit("abcdef1234", "2026-07-10", "a bless").label == "2026-07-10 abcdef12"


# --------------------------------------------------------------------------- #
# Renaming
# --------------------------------------------------------------------------- #
def test_apply_kpi_renames_moves_the_value_to_the_new_key() -> None:
    """A declared rename carries the value over and leaves the rest alone."""
    out = apply_kpi_renames({"old": 1.0, "other": 2.0}, {"old": "new"})
    assert out == {"new": 1.0, "other": 2.0}


def test_apply_kpi_renames_leaves_a_snapshot_that_carries_both_names_alone() -> None:
    """Where both names are present they are two measurements, so neither is merged away."""
    out = apply_kpi_renames({"old": 1.0, "new": 2.0}, {"old": "new"})
    assert out == {"old": 1.0, "new": 2.0}


def test_apply_kpi_renames_is_a_no_op_when_the_old_key_is_absent() -> None:
    """A rename for a key this pair never had changes nothing."""
    assert apply_kpi_renames({"new": 3.0}, {"old": "new"}) == {"new": 3.0}


def test_a_renamed_kpi_becomes_one_series(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stitching turns an ending and a beginning into one panel spanning every commit."""
    monkeypatch.setattr("scripts.golden_history.kpi_renames_for", lambda stem: {"a.old": "a.new"})
    history = _history([{"a.old": 10.0}, {"a.old": 12.0}, {"a.new": 15.0}])
    assert [panel.kpi for panel in history.panels] == ["a.new"]
    assert history.panels[0].values == (10.0, 12.0, 15.0)


def test_without_the_table_a_rename_would_be_two_half_length_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stitching is what the table buys: without it the same history is two panels."""
    monkeypatch.setattr("scripts.golden_history.kpi_renames_for", lambda stem: {})
    history = _history([{"a.old": 10.0}, {"a.old": 12.0}, {"a.new": 15.0}])
    assert sorted(panel.kpi for panel in history.panels) == ["a.new", "a.old"]


# --------------------------------------------------------------------------- #
# Panels: relative change, the zero baseline, non-numeric values
# --------------------------------------------------------------------------- #
def test_relative_change_is_percent_against_the_previous_value() -> None:
    """A half is minus fifty percent, and a doubling is a hundred."""
    assert relative_change(2.0, 1.0) == pytest.approx(-50.0)
    assert relative_change(2.0, 4.0) == pytest.approx(100.0)
    assert relative_change(-2.0, -3.0) == pytest.approx(-50.0)
    assert relative_change(0.0, 5.0) is None


def test_panel_plots_percent_against_the_first_recorded_value() -> None:
    """The y axis is the change against the series' first value, in percent."""
    panel = build_panel("kpi", [50.0, 75.0, 25.0])
    assert panel.mode == MODE_PERCENT
    assert panel.baseline == 50.0
    assert panel.plotted == pytest.approx((0.0, 50.0, -50.0))
    assert panel.moved is True


def test_a_first_value_of_zero_switches_the_panel_to_absolute_change() -> None:
    """A percentage against zero is undefined, so the panel shows the absolute change."""
    panel = build_panel("kpi", [0.0, 4.0, 6.0])
    assert panel.mode == MODE_ABSOLUTE
    assert panel.plotted == pytest.approx((0.0, 4.0, 6.0))


def test_non_numeric_values_are_skipped_and_leave_a_gap() -> None:
    """``null`` and strings are not plotted; the surrounding series is unaffected."""
    history = _history([{"k": 4.0}, {"k": None}, {"k": "Home"}, {"k": 8.0}])
    panel = history.panels[0]
    assert panel.values == (4.0, None, None, 8.0)
    assert panel.plotted[1] is None
    assert panel.plotted[3] == pytest.approx(100.0)


def test_a_kpi_that_is_never_numeric_still_gets_a_panel() -> None:
    """One panel per KPI stays literally true; the panel simply says there is nothing."""
    panel = build_panel("kpi", [None, None])
    assert panel.mode == MODE_EMPTY
    assert panel.baseline is None
    assert panel.moved is False
    assert panel.score == 0.0


def test_a_flat_series_never_moved() -> None:
    """A KPI reproduced to the last bit across every bless is quiet."""
    panel = build_panel("kpi", [3.5, 3.5, 3.5])
    assert panel.moved is False
    assert panel.score == 0.0


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #
def test_movement_score_is_the_path_length_not_the_extreme() -> None:
    """A KPI that drifted at every bless outranks one that jumped once and came back."""
    assert movement_score([10.0, 11.0, 12.0, 13.0]) == pytest.approx(0.3)
    assert movement_score([10.0, 20.0, 10.0]) == pytest.approx(2.0)
    assert movement_score([10.0]) == 0.0
    assert movement_score([None, None]) == 0.0


def test_movement_score_of_a_series_that_starts_at_zero_uses_its_first_non_zero_value() -> None:
    """A zero baseline would make every relative score infinite, so the scale moves on."""
    assert movement_score([0.0, 4.0, 4.0]) == pytest.approx(1.0)
    assert movement_score([0.0, 0.0]) == 0.0


def test_panels_are_sorted_with_the_largest_mover_first() -> None:
    """The eye should land on the movers, so they come first and the quiet ones last."""
    history = _history(
        [
            {"quiet": 5.0, "small": 100.0, "large": 10.0},
            {"quiet": 5.0, "small": 101.0, "large": 20.0},
        ]
    )
    assert [panel.kpi for panel in history.panels] == ["large", "small", "quiet"]
    assert history.panels[-1].moved is False


# --------------------------------------------------------------------------- #
# The commits a pair is drawn against
# --------------------------------------------------------------------------- #
def test_commits_before_the_file_existed_are_not_drawn() -> None:
    """A pair added late starts at the commit that added it, which is not an error."""
    history = _history([None, None, {"k": 1.0}, {"k": 2.0}])  # type: ignore[list-item]
    assert len(history.commits) == 2
    assert history.commits[0].date == "2026-01-03"


def test_a_commit_that_did_not_change_this_pair_is_not_drawn() -> None:
    """The x axis is the commits that touched *this* file, not the whole directory."""
    history = _history([{"k": 1.0}, {"k": 1.0}, {"k": 2.0}])
    assert [commit.date for commit in history.commits] == ["2026-01-01", "2026-01-03"]


# --------------------------------------------------------------------------- #
# moves.csv
# --------------------------------------------------------------------------- #
def test_a_first_appearance_is_not_a_move() -> None:
    """There is nothing a KPI's first recorded value moved from."""
    history = _history([{"k": 1.0}, {"k": 1.0, "fresh": 9.0}])
    assert [move.kpi for move in collect_moves(history)] == []


def test_a_move_is_reported_against_the_last_value_actually_recorded() -> None:
    """A gap is a gap: the comparison reaches back past it rather than restarting."""
    history = _history([{"k": 4.0}, {"k": None}, {"k": 5.0}])
    moves = collect_moves(history)
    assert len(moves) == 1
    assert (moves[0].previous, moves[0].value) == (4.0, 5.0)
    assert moves[0].relative_change == pytest.approx(25.0)


def test_a_change_within_the_gate_tolerance_is_not_a_move() -> None:
    """What the golden gate would have accepted does not count as movement."""
    history = _history([{"k": 1.0}, {"k": 1.0 + 1e-15}])
    assert collect_moves(history) == []


def test_a_move_out_of_zero_has_no_relative_change() -> None:
    """Percent against zero is undefined and the column stays empty rather than lying."""
    history = _history([{"k": 0.0}, {"k": 3.0}])
    moves = collect_moves(history)
    assert len(moves) == 1
    assert moves[0].relative_change is None


def test_write_moves_csv_writes_one_row_per_move(tmp_path: Path) -> None:
    """The CSV is the greppable answer to 'what moved when'."""
    history = _history([{"a": 1.0, "b": 0.0}, {"a": 2.0, "b": 4.0}], stem="demo__one_week_60s")
    path = tmp_path / "moves.csv"
    assert write_moves_csv(collect_moves(history), path) == 2

    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    assert [row["kpi"] for row in rows] == ["a", "b"]
    assert rows[0] == {
        "commit": "0" * 8,
        "date": "2026-01-02",
        "pr": "100",
        "pair": "demo__one_week_60s",
        "kpi": "a",
        "previous": "1.0",
        "value": "2.0",
        "relative_change": "100",
    }
    assert rows[1]["relative_change"] == ""


def test_moves_csv_leaves_the_pr_column_empty_when_the_subject_has_none(tmp_path: Path) -> None:
    """Not every bless came through a pull request; the column says so by being empty."""
    snapshots = [(_commit(0, "a bless"), {"k": 1.0}), (_commit(1, "another bless"), {"k": 2.0})]
    path = tmp_path / "moves.csv"
    write_moves_csv(collect_moves(build_pair_history("demo__one_week_60s", snapshots)), path)
    assert list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))[0]["pr"] == ""


# --------------------------------------------------------------------------- #
# CLI argument handling
# --------------------------------------------------------------------------- #
def test_resolve_pairs_expands_a_setup_id_into_its_parameter_sets() -> None:
    """``--pairs household_oil_building_sizer`` means both of its horizons."""
    available = ["a__one_week_60s", "a__full_year_60s", "b__one_week_60s"]
    assert resolve_pairs(["a"], available) == ["a__one_week_60s", "a__full_year_60s"]
    assert resolve_pairs(["b__one_week_60s"], available) == ["b__one_week_60s"]
    assert resolve_pairs([], available) == available


def test_resolve_pairs_refuses_a_name_nothing_matches() -> None:
    """A typo is an error, not an empty run."""
    with pytest.raises(ValueError, match="No golden reference matches"):
        resolve_pairs(["nope"], ["a__one_week_60s"])


def test_parse_formats() -> None:
    """Formats are png, html or both, in a stable order."""
    assert parse_formats("html,png") == ["png", "html"]
    assert parse_formats("PNG") == ["png"]
    for bad in ("", "pdf", "png,svg"):
        with pytest.raises(ValueError):
            parse_formats(bad)


# --------------------------------------------------------------------------- #
# Rendering (into tmp_path only)
# --------------------------------------------------------------------------- #
def test_rendering_writes_a_png_an_html_page_and_an_index(tmp_path: Path) -> None:
    """The three outputs are produced without a display and the page parses as HTML."""
    history = _history([{"a": 1.0, "b": 0.0, "c": None}, {"a": 2.0, "b": 0.0, "c": None}])
    png, page, index = tmp_path / "p.png", tmp_path / "p.html", tmp_path / "index.html"
    render_png(history, png)
    render_html(history, page, "plotly.min.js")
    render_index([history], ["png", "html"], {history.stem: 1}, index)

    assert png.stat().st_size > 1000
    for path in (page, index):
        text = path.read_text(encoding="utf-8")
        assert text.lstrip().lower().startswith("<!doctype html>")
        html.parser.HTMLParser().feed(text)
    assert 'src="plotly.min.js"' in page.read_text(encoding="utf-8")
    assert "cdn" not in page.read_text(encoding="utf-8").split("<body")[0]


# --------------------------------------------------------------------------- #
# The rename table against the real history
# --------------------------------------------------------------------------- #
def _golden_commit_count() -> int:
    """How many commits of this checkout touch ``golden_references/``.

    Returns:
        The count; ``0`` when git cannot answer at all.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-list", "--count", "HEAD", "--", "golden_references/"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError):  # pragma: no cover - no git, no history
        return 0
    return int(out.strip() or 0)


requires_history = pytest.mark.skipif(
    _golden_commit_count() < 2,
    reason="needs the git history of golden_references/, which a shallow CI checkout lacks",
)


@pytest.fixture(scope="module")
def historical_keys() -> Dict[str, set]:
    """Every KPI key each golden file ever carried, keyed by the stem of that moment.

    Returns:
        ``stem -> set of keys``, over every commit that touched the directory.
    """
    keys: Dict[str, set] = {}
    for commit in golden_commits(REPO_ROOT):
        for stem, contents in golden_files_at(REPO_ROOT, commit).items():
            keys.setdefault(stem, set()).update(contents)
    return keys


@pytest.fixture(scope="module")
def current_keys() -> Dict[str, set]:
    """Every KPI key the golden files carry today.

    Returns:
        ``stem -> set of keys``.
    """
    return {
        path.stem: set(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(GOLDEN_DIR.glob("*.json"))
        if path.name != "manifest.json"
    }


@requires_history
def test_every_declared_old_kpi_name_really_existed(historical_keys: Dict[str, set]) -> None:
    """A rename claims a name the goldens once carried; a typo in it would go unnoticed."""
    for stem, entries in KPI_RENAMES.items():
        for entry in entries:
            if stem == FLEET_WIDE:
                assert any(entry.old in keys for keys in historical_keys.values()), (
                    f"no golden ever carried {entry.old!r}"
                )
            else:
                assert stem in historical_keys, f"no golden file was ever named {stem!r}"
                assert entry.old in historical_keys[stem], (
                    f"{stem!r} never carried {entry.old!r}"
                )


def test_every_declared_new_kpi_name_is_carried_today(current_keys: Dict[str, set]) -> None:
    """The other end of the claim: the new name must be in the files as they stand."""
    for stem, entries in KPI_RENAMES.items():
        for entry in entries:
            if stem == FLEET_WIDE:
                assert any(entry.new in keys for keys in current_keys.values()), (
                    f"no golden carries {entry.new!r} today"
                )
            else:
                assert stem in current_keys, f"there is no golden file {stem!r} today"
                assert entry.new in current_keys[stem], f"{stem!r} does not carry {entry.new!r}"


def test_no_declared_rename_is_still_in_effect_under_its_old_name(current_keys: Dict[str, set]) -> None:
    """A pair that still carries the old name today was not renamed and must not be listed."""
    for stem, entries in KPI_RENAMES.items():
        if stem == FLEET_WIDE:
            continue
        for entry in entries:
            assert entry.old not in current_keys.get(stem, set()), (
                f"{stem!r} still carries {entry.old!r}; that is not a rename"
            )


@requires_history
def test_every_rename_names_a_commit_that_touched_the_goldens() -> None:
    """The commit column is the evidence; it has to be a golden commit of this branch."""
    shas = {commit.sha for commit in golden_commits(REPO_ROOT)}
    claims: List[KpiRename] = [entry for entries in KPI_RENAMES.values() for entry in entries]
    for entry in claims:
        assert entry.commit in shas, f"{entry.commit} does not touch golden_references/"
    for rename in PAIR_RENAMES.values():
        assert rename.commit in shas, f"{rename.commit} does not touch golden_references/"


@requires_history
def test_pair_renames_name_a_file_that_existed_and_one_that_exists(
    historical_keys: Dict[str, set], current_keys: Dict[str, set]
) -> None:
    """Both ends of a file rename must be real; today the table is empty and that is fine."""
    for old, rename in PAIR_RENAMES.items():
        assert old == rename.old, f"PAIR_RENAMES is keyed by the old stem; {old!r} is not {rename.old!r}"
        assert old in historical_keys, f"no golden file was ever named {old!r}"
        assert canonical_pair(old) in current_keys, f"{old!r} resolves to a stem nothing carries"


def test_the_table_is_keyed_by_pairs_that_exist_today(current_keys: Dict[str, set]) -> None:
    """A KPI table keyed by a pair nobody gates any more would never be applied."""
    for stem in KPI_RENAMES:
        if stem != FLEET_WIDE:
            assert stem in current_keys, f"KPI_RENAMES names {stem!r}, which has no golden file"


def test_kpi_renames_for_merges_the_fleet_wide_claims_with_the_pairs_own() -> None:
    """A pair's own claim wins over a fleet-wide one for the same key."""
    stem = "household_oil_building_sizer__one_week_60s"
    merged = kpi_renames_for(stem)
    assert merged["BUI1.Fuel Meter.OPEX - CO2 Footprint"] == (
        "BUI1.Fuel Meter.OPEX - CO2 Footprint (FuelMeter)"
    )
    assert kpi_renames_for("a pair nobody has") == dict(
        (entry.old, entry.new) for entry in KPI_RENAMES.get(FLEET_WIDE, ())
    )


def test_canonical_pair_is_the_identity_while_no_file_has_been_renamed() -> None:
    """With an empty PAIR_RENAMES a stem is its own current name."""
    assert canonical_pair("household_oil_building_sizer__one_week_60s") == (
        "household_oil_building_sizer__one_week_60s"
    )


@requires_history
def test_since_a_golden_commit_starts_the_walk_there() -> None:
    """``--since <sha>`` means "from this bless onwards", inclusive."""
    commits = golden_commits(REPO_ROOT)
    truncated = golden_commits(REPO_ROOT, since=commits[-2].sha)
    assert [commit.sha for commit in truncated] == [commits[-2].sha, commits[-1].sha]


@requires_history
def test_since_a_date_keeps_the_commits_at_or_after_it() -> None:
    """A date that git understands is handed to ``git log --since``."""
    assert golden_commits(REPO_ROOT, since="2099-01-01") == []
    assert golden_commits(REPO_ROOT, since="1999-01-01") == golden_commits(REPO_ROOT)


@requires_history
def test_since_a_commit_that_never_touched_the_goldens_is_refused() -> None:
    """There is no snapshot to start from, and walking everything would answer another question."""
    golden = {commit.sha for commit in golden_commits(REPO_ROOT)}
    everything = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-list", "-n", "80", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    outsider = next((sha for sha in everything if sha not in golden), None)
    if outsider is None:  # pragma: no cover - every recent commit blessed a golden
        pytest.skip("every commit in reach touches golden_references/")
    with pytest.raises(ValueError, match="does not touch"):
        golden_commits(REPO_ROOT, since=outsider)


@requires_history
def test_max_commits_keeps_the_most_recent_ones() -> None:
    """``--max-commits`` trims the old end, because the recent blesses are the interesting ones."""
    commits = golden_commits(REPO_ROOT)
    assert golden_commits(REPO_ROOT, max_commits=3) == commits[-3:]


def test_an_empty_golden_file_still_renders(tmp_path: Path) -> None:
    """A pair whose file carries no KPI at all must not crash the figure machinery."""
    history = _history([{}, {"k": 1.0}])
    render_png(history, tmp_path / "p.png")
    render_html(history, tmp_path / "p.html", "plotly.min.js")
    assert (tmp_path / "p.png").exists()
