#!/usr/bin/env python3
"""Draw how every golden KPI moved across the blesses.

``golden_references/*.json`` records what the gated fleet produced at the moment of the
last bless: one flat ``{"BUI1.<component>.<kpi>": number}`` file per ``(setup,
parameter_set)`` pair. The files are re-blessed whenever an intended change moves a number,
and since #682 a bless is sticky -- a value the gate would have accepted keeps its stored
number -- so the git history of that directory is a fairly clean record of which KPI moved
in which pull request.

This script reads that record and draws it. It walks the commits that touched
``golden_references/`` on the current branch, loads every golden file at every commit where
it existed, stitches renamed keys back into one series through
:mod:`scripts.golden_kpi_renames`, and writes, per pair, a grid of small multiples -- one
panel per KPI, one marker per commit that touched the pair's file, the y axis being the
KPI's change against its first recorded value. It also writes one ``moves.csv`` covering
every pair, which is the greppable answer to "what moved when".

Conventions
-----------
* **y axis.** Percent change against the KPI's first recorded value in the plotted window.
  If that first value is exactly ``0`` the percentage is undefined, so the panel plots the
  **absolute** change instead (which equals the value) and its title is marked ``[abs]``.
  ``moves.csv`` leaves ``relative_change`` empty for a move out of a zero.
* **Non-numeric values** (``null``, strings) are skipped: the commit gets no marker in that
  panel. A KPI that is non-numeric throughout still gets a panel, labelled "no numeric
  values", so that "one panel per KPI" stays literally true.
* **Panel order.** Largest total movement first. Total movement is the path length of the
  series -- the sum of ``|v(i) - v(i-1)|`` over consecutive recorded values -- divided by the
  first non-zero value, so it is defined for a series that starts at zero as well, and it
  counts a KPI that moved five times above one that moved once as far. The three largest
  movers are annotated with their rank. Panels that never moved are drawn in grey, so the
  eye lands on the movers.
* **x axis.** The commits that changed this pair's file, oldest first, labelled with the
  date and the PR number parsed from the commit subject (``... (#NNN)``), or the short sha
  when the subject carries no PR.

Output lands in ``results/golden_history/`` by default (gitignored): ``<pair>.png``,
``<pair>.html``, one shared ``plotly.min.js`` the pages reference relatively, an
``index.html`` linking them, and ``moves.csv``.

Usage::

    python scripts/golden_history.py
    python scripts/golden_history.py --pairs household_oil_building_sizer --since 2026-09-01
    python scripts/golden_history.py --formats png --out /tmp/gh --max-commits 5
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")  # no display anywhere this runs -- must precede the pyplot import

# pylint: disable=wrong-import-position
from matplotlib import pyplot as plt  # noqa: E402
import plotly.graph_objects as go  # type: ignore[import-untyped]  # noqa: E402
from plotly.offline import get_plotlyjs  # type: ignore[import-untyped]  # noqa: E402
from plotly.subplots import make_subplots  # type: ignore[import-untyped]  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # run as a script from scripts/ ...
    from golden_check import golden_filename  # type: ignore[import-not-found]
    from golden_kpi_renames import canonical_pair, kpi_renames_for  # type: ignore[import-not-found]
    from golden_kpis import ABS_TOL, REL_TOL  # type: ignore[import-not-found]
    from runner import load_config, select_pairs  # type: ignore[import-not-found]
except ModuleNotFoundError:  # ... or imported as scripts.golden_history (tests)
    from scripts.golden_check import golden_filename
    from scripts.golden_kpi_renames import canonical_pair, kpi_renames_for
    from scripts.golden_kpis import ABS_TOL, REL_TOL
    from scripts.runner import load_config, select_pairs

GOLDEN_DIR_NAME = "golden_references"
#: Files in the golden directory that are not a pair's references.
NON_PAIR_FILES = frozenset({"manifest.json", "README.md"})
DEFAULT_CONFIG_PATH = Path(__file__).parent / "golden_config.json"
DEFAULT_OUT_DIR = _REPO_ROOT / "results" / "golden_history"
#: Trailing ``(#1234)`` of a squash-merge subject.
PR_IN_SUBJECT = re.compile(r"\(#(\d+)\)\s*$")
#: How many of the largest movers get their rank written into the panel title.
ANNOTATED_MOVERS = 3
PANEL_COLUMNS = 5

MOVER_COLOUR = "#1f4e79"
TOP_MOVER_COLOUR = "#c2410c"
QUIET_COLOUR = "#b0b0b0"

#: Panel modes: percent change, absolute change (first value was zero), nothing to plot.
MODE_PERCENT = "percent"
MODE_ABSOLUTE = "absolute"
MODE_EMPTY = "empty"


# ---------------------------------------------------------------------------
# Pure model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Commit:
    """One commit that touched ``golden_references/``.

    Attributes:
        sha: The full commit sha.
        date: The author date as ``YYYY-MM-DD``.
        subject: The commit subject line.
    """

    sha: str
    date: str
    subject: str

    @property
    def short(self) -> str:
        """The eight-character sha, as the labels and ``moves.csv`` spell it."""
        return self.sha[:8]

    @property
    def pr(self) -> Optional[int]:
        """The pull request number in the subject, or ``None`` if it carries none."""
        return parse_pr_number(self.subject)

    @property
    def label(self) -> str:
        """The x-axis tick: the date plus the PR number, or the short sha."""
        return f"{self.date} #{self.pr}" if self.pr is not None else f"{self.date} {self.short}"


@dataclass(frozen=True)
class Panel:
    """One KPI's history within one pair, ready to draw.

    Attributes:
        kpi: The KPI key, in its current spelling.
        values: The recorded value per plotted commit; ``None`` where the pair's file did
            not carry the KPI, or carried a non-numeric value.
        plotted: What the panel's y axis shows, aligned with ``values``.
        mode: ``"percent"``, ``"absolute"`` (the first value was zero) or ``"empty"``.
        baseline: The first recorded numeric value, or ``None`` if there is none.
        score: Total movement -- the series' path length relative to its first non-zero
            value. Zero for a KPI that never moved.
        moved: Whether any consecutive recorded pair differs beyond the gate's tolerance.
    """

    kpi: str
    values: Tuple[Optional[float], ...]
    plotted: Tuple[Optional[float], ...]
    mode: str
    baseline: Optional[float]
    score: float
    moved: bool


@dataclass(frozen=True)
class PairHistory:
    """Everything one pair contributes: its commits and one panel per KPI.

    Attributes:
        stem: The golden filename stem, e.g. ``household_oil_building_sizer__one_week_60s``.
        commits: The commits that changed this pair's file, oldest first.
        panels: One panel per KPI, largest total movement first.
    """

    stem: str
    commits: Tuple[Commit, ...]
    panels: Tuple[Panel, ...]


@dataclass(frozen=True)
class Move:
    """One KPI value that changed at one commit.

    Attributes:
        commit: The commit at which the new value was recorded.
        pair: The golden filename stem.
        kpi: The KPI key, in its current spelling.
        previous: The last value recorded before this commit.
        value: The value recorded at this commit.
        relative_change: The change against ``previous`` in percent, or ``None`` when
            ``previous`` is exactly zero.
    """

    commit: Commit
    pair: str
    kpi: str
    previous: float
    value: float
    relative_change: Optional[float]


def parse_pr_number(subject: str) -> Optional[int]:
    """Return the pull request number a commit subject ends with.

    Args:
        subject: A commit subject line, e.g. ``"Maintenance is a yearly cost (#657)"``.

    Returns:
        The number, or ``None`` when the subject carries no trailing ``(#NNN)``.
    """
    match = PR_IN_SUBJECT.search(subject)
    return int(match.group(1)) if match else None


def is_number(value: Any) -> bool:
    """True for a real number; ``bool`` is not one for our purposes.

    Args:
        value: Any value read out of a golden file.

    Returns:
        Whether it can be plotted as a number.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def apply_kpi_renames(mapping: Mapping[str, Any], renames: Mapping[str, str]) -> Dict[str, Any]:
    """Rewrite a snapshot's keys through the declared renames.

    A rename applies only where the old key is present and the new key is not: a snapshot
    carrying both is one where the two names are two measurements, not two spellings of one,
    and rewriting it would silently merge them.

    Args:
        mapping: One golden file's contents.
        renames: ``old key -> new key``, from :func:`~scripts.golden_kpi_renames.kpi_renames_for`.

    Returns:
        A new mapping with the renames applied.
    """
    out = dict(mapping)
    for old, new in renames.items():
        if old in out and new not in out:
            out[new] = out.pop(old)
    return out


def moved_beyond_tolerance(previous: float, value: float) -> bool:
    """Whether two values differ by more than the golden gate would have accepted.

    Args:
        previous: The earlier value.
        value: The later value.

    Returns:
        True when the gate would have reported the difference.
    """
    return not math.isclose(value, previous, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def relative_change(previous: float, value: float) -> Optional[float]:
    """The change from ``previous`` to ``value`` in percent.

    Args:
        previous: The reference value.
        value: The new value.

    Returns:
        ``(value - previous) / |previous| * 100``, or ``None`` when ``previous`` is zero.
    """
    if previous == 0:
        return None
    return (value - previous) / abs(previous) * 100.0


def movement_score(values: Sequence[Optional[float]]) -> float:
    """Total movement of a series: its path length relative to its first non-zero value.

    Summing every step rather than taking the extreme means a KPI that drifted at five
    blesses outranks one that jumped once and stayed. Dividing by the first non-zero value
    keeps the number comparable between a KPI measured in euros and one in kilowatt-hours,
    and leaves a series that starts at zero rankable, which a percentage against the first
    value would not.

    Args:
        values: The recorded values, ``None`` where nothing was recorded.

    Returns:
        The score; ``0.0`` for a series that never moved or has fewer than two values.
    """
    recorded = [value for value in values if value is not None]
    if len(recorded) < 2:
        return 0.0
    scale = next((abs(value) for value in recorded if value != 0), 0.0) or 1.0
    return sum(abs(b - a) for a, b in zip(recorded, recorded[1:])) / scale


def build_panel(kpi: str, values: Sequence[Optional[float]]) -> Panel:
    """Turn one KPI's recorded values into a drawable panel.

    Args:
        kpi: The KPI key.
        values: One entry per plotted commit, ``None`` where nothing numeric was recorded.

    Returns:
        The panel, carrying what to plot and how far the KPI moved.
    """
    recorded = [value for value in values if value is not None]
    if not recorded:
        return Panel(kpi, tuple(values), tuple(values), MODE_EMPTY, None, 0.0, False)

    baseline = recorded[0]
    if baseline == 0:
        mode = MODE_ABSOLUTE
        plotted: List[Optional[float]] = [None if v is None else v - baseline for v in values]
    else:
        mode = MODE_PERCENT
        plotted = [None if v is None else (v - baseline) / abs(baseline) * 100.0 for v in values]

    moved = any(moved_beyond_tolerance(a, b) for a, b in zip(recorded, recorded[1:]))
    return Panel(kpi, tuple(values), tuple(plotted), mode, baseline, movement_score(values), moved)


def build_pair_history(
    stem: str, snapshots: Sequence[Tuple[Commit, Optional[Mapping[str, Any]]]]
) -> PairHistory:
    """Assemble one pair's history from its snapshot at every commit.

    Commits before the file first appeared are dropped, as are commits at which the file is
    absent (it has never been deleted, but a deletion would be a gap, not a zero) and
    commits at which the renamed contents are identical to the previous kept snapshot --
    those did not touch this pair even though they touched the directory.

    Args:
        stem: The golden filename stem, in its current spelling.
        snapshots: ``(commit, contents or None)`` for every walked commit, oldest first.

    Returns:
        The pair's history, panels sorted by total movement, largest first.
    """
    renames = kpi_renames_for(stem)
    kept: List[Tuple[Commit, Dict[str, Any]]] = []
    for commit, contents in snapshots:
        if contents is None:
            continue
        renamed = apply_kpi_renames(contents, renames)
        if kept and renamed == kept[-1][1]:
            continue
        kept.append((commit, renamed))

    keys: List[str] = []
    seen = set()
    for _, contents_at in kept:
        for key in contents_at:
            if key not in seen:
                seen.add(key)
                keys.append(key)

    panels = [
        build_panel(
            key,
            [
                contents_at[key] if is_number(contents_at.get(key)) else None
                for _, contents_at in kept
            ],
        )
        for key in keys
    ]
    panels.sort(key=lambda panel: (-panel.score, panel.kpi))
    return PairHistory(stem, tuple(commit for commit, _ in kept), tuple(panels))


def collect_moves(history: PairHistory) -> List[Move]:
    """Every value in one pair that changed beyond tolerance, in commit order.

    The first value a KPI ever records is not a move -- there is nothing it moved from -- and
    a KPI that reappears after a gap is compared against the last value actually recorded.

    Args:
        history: The pair's assembled history.

    Returns:
        The moves, ordered by commit and then by KPI name.
    """
    moves: List[Move] = []
    by_name = sorted(history.panels, key=lambda panel: panel.kpi)
    for index, commit in enumerate(history.commits):
        for panel in by_name:
            value = panel.values[index]
            if value is None:
                continue
            previous = next(
                (v for v in reversed(panel.values[:index]) if v is not None), None
            )
            if previous is None or not moved_beyond_tolerance(previous, value):
                continue
            moves.append(
                Move(commit, history.stem, panel.kpi, previous, value, relative_change(previous, value))
            )
    return moves


# ---------------------------------------------------------------------------
# Git layer
# ---------------------------------------------------------------------------
def run_git(repo: Path, *args: str) -> str:
    """Run a git command in ``repo`` and return its stdout.

    Args:
        repo: The repository (or worktree) root.
        *args: The git arguments.

    Returns:
        Standard output, decoded as UTF-8.

    Raises:
        subprocess.CalledProcessError: If git exits non-zero.
    """
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def golden_commits(repo: Path, since: Optional[str] = None, max_commits: Optional[int] = None) -> List[Commit]:
    """The commits that touched ``golden_references/``, oldest first.

    Args:
        repo: The repository root.
        since: A commit-ish that itself touched the directory (the walk starts there,
            inclusive), or anything git understands as a date (the walk keeps commits at or
            after it). ``None`` walks everything.
        max_commits: Keep at most this many of the most recent commits.

    Returns:
        The commits, oldest first.

    Raises:
        ValueError: If ``since`` resolves to a commit that does not touch the directory --
            there is no snapshot to start from, and silently walking everything instead
            would answer a question nobody asked.
    """
    args = ["log", "--reverse", "--format=%H%x1f%ad%x1f%s", "--date=short"]
    start_sha: Optional[str] = None
    if since is not None:
        try:
            start_sha = run_git(repo, "rev-parse", "--verify", "--quiet", f"{since}^{{commit}}").strip()
        except subprocess.CalledProcessError:
            args.append(f"--since={since}")
    args += ["--", f"{GOLDEN_DIR_NAME}/"]

    commits: List[Commit] = []
    for line in run_git(repo, *args).splitlines():
        sha, date, subject = line.split("\x1f", 2)
        commits.append(Commit(sha=sha, date=date, subject=subject))

    if start_sha:
        shas = [commit.sha for commit in commits]
        if start_sha not in shas:
            raise ValueError(
                f"--since {since!r} resolves to {start_sha[:8]}, which does not touch "
                f"{GOLDEN_DIR_NAME}/. Pass a date, or one of the commits that do."
            )
        commits = commits[shas.index(start_sha):]
    if max_commits is not None:
        commits = commits[-max_commits:]
    return commits


def golden_files_at(repo: Path, commit: Commit) -> Dict[str, Dict[str, Any]]:
    """Read every golden reference file as it stood at one commit.

    Two git calls: one tree listing, one batched blob read. Keys are the filename stems as
    *that commit* spelled them, before any renaming is applied.

    Args:
        repo: The repository root.
        commit: The commit to read.

    Returns:
        ``stem -> contents`` for every pair file the commit's tree carries.
    """
    oids: Dict[str, str] = {}
    listing = run_git(repo, "ls-tree", "-r", commit.sha, "--", f"{GOLDEN_DIR_NAME}/")
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        name = path.rsplit("/", 1)[-1]
        if name in NON_PAIR_FILES or not name.endswith(".json"):
            continue
        oids[name[: -len(".json")]] = meta.split()[2]
    if not oids:
        return {}

    raw = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input="\n".join(oids.values()).encode(),
        capture_output=True,
        check=True,
    ).stdout
    contents: Dict[str, Dict[str, Any]] = {}
    position = 0
    for stem in oids:
        header_end = raw.index(b"\n", position)
        size = int(raw[position:header_end].split()[2])
        body = raw[header_end + 1: header_end + 1 + size]
        position = header_end + 1 + size + 1  # the blob is followed by a newline
        parsed = json.loads(body.decode("utf-8"))
        if isinstance(parsed, dict):
            contents[stem] = parsed
    return contents


def snapshot_at(repo: Path, commit: Commit, stems: Sequence[str]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Read the wanted pairs as they stood at one commit, under their current names.

    A stem the commit's tree does not carry maps to ``None`` -- the pair simply had no
    reference yet, which is not an error.

    Args:
        repo: The repository root.
        commit: The commit to read.
        stems: The golden filename stems wanted, in their current spelling.

    Returns:
        ``stem -> contents`` (or ``None``), one entry per requested stem.
    """
    by_current_name = {
        canonical_pair(stem): contents for stem, contents in golden_files_at(repo, commit).items()
    }
    return {stem: by_current_name.get(stem) for stem in stems}


def walk_history(repo: Path, stems: Sequence[str], commits: Sequence[Commit]) -> List[PairHistory]:
    """Load every pair at every commit and assemble the histories.

    Args:
        repo: The repository root.
        stems: The golden filename stems to cover.
        commits: The commits to read, oldest first.

    Returns:
        One :class:`PairHistory` per stem, in the order the stems were given; a stem with no
        snapshot anywhere in the window yields a history with no commits.
    """
    per_stem: Dict[str, List[Tuple[Commit, Optional[Dict[str, Any]]]]] = {stem: [] for stem in stems}
    for commit in commits:
        snapshot = snapshot_at(repo, commit, stems)
        for stem in stems:
            per_stem[stem].append((commit, snapshot[stem]))
    return [build_pair_history(stem, per_stem[stem]) for stem in stems]


def configured_pairs(config_path: Path, golden_dir: Path) -> List[str]:
    """The golden filename stems the gate configures and a reference exists for today.

    Args:
        config_path: Path to ``golden_config.json``.
        golden_dir: Path to ``golden_references/``.

    Returns:
        The stems, in config order.
    """
    config = load_config(config_path)
    stems = [golden_filename(setup.id, param.id)[: -len(".json")] for setup, param in select_pairs(config)]
    return [stem for stem in stems if (golden_dir / f"{stem}.json").exists()]


def resolve_pairs(requested: Sequence[str], available: Sequence[str]) -> List[str]:
    """Expand what ``--pairs`` asked for into golden filename stems.

    An argument may be a full stem (``household_oil_building_sizer__one_week_60s``) or a
    setup id (``household_oil_building_sizer``), which selects every parameter set of it.

    Args:
        requested: The ``--pairs`` arguments; empty means all of ``available``.
        available: Every stem that has a golden file today.

    Returns:
        The selected stems, in the order ``available`` lists them.

    Raises:
        ValueError: If an argument matches no stem.
    """
    if not requested:
        return list(available)
    selected: List[str] = []
    for name in requested:
        matched = [stem for stem in available if stem == name or stem.startswith(f"{name}__")]
        if not matched:
            raise ValueError(f"No golden reference matches {name!r}. Known pairs: {', '.join(available)}")
        selected.extend(stem for stem in matched if stem not in selected)
    return [stem for stem in available if stem in selected]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def write_moves_csv(moves: Sequence[Move], path: Path) -> int:
    """Write one row per KPI value that changed, across every pair.

    Args:
        moves: The moves to write, in the order they should appear.
        path: The file to write.

    Returns:
        The number of rows written.
    """
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["commit", "date", "pr", "pair", "kpi", "previous", "value", "relative_change"])
        for move in moves:
            writer.writerow(
                [
                    move.commit.short,
                    move.commit.date,
                    "" if move.commit.pr is None else move.commit.pr,
                    move.pair,
                    move.kpi,
                    repr(move.previous),
                    repr(move.value),
                    "" if move.relative_change is None else f"{move.relative_change:.6g}",
                ]
            )
    return len(moves)


def panel_title(panel: Panel, rank: int) -> str:
    """The short label above one panel.

    Args:
        panel: The panel.
        rank: Its position in the movement order, counting from one.

    Returns:
        The KPI without its building prefix, marked ``[abs]`` when the panel shows an
        absolute change, and carrying its rank when it is among the largest movers.
    """
    name = panel.kpi[len("BUI1."):] if panel.kpi.startswith("BUI1.") else panel.kpi
    if panel.mode == MODE_ABSOLUTE:
        name = f"[abs] {name}"
    if panel.moved and rank <= ANNOTATED_MOVERS:
        name = f"#{rank} {name}"
    return name


def _wrap(text: str, width: int, lines: int) -> str:
    """Break a label into at most ``lines`` lines of about ``width`` characters.

    Args:
        text: The label.
        width: The target line width.
        lines: The maximum number of lines; the last one is ellipsised if more are needed.

    Returns:
        The wrapped label.
    """
    words, out, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    out.append(current)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][: max(0, width - 1)] + "…"
    return "\n".join(out)


def _panel_colour(panel: Panel, rank: int) -> str:
    """The colour a panel is drawn in: loud for a mover, grey for a KPI that never moved.

    Args:
        panel: The panel.
        rank: Its position in the movement order, counting from one.

    Returns:
        A matplotlib colour string.
    """
    if not panel.moved:
        return QUIET_COLOUR
    return TOP_MOVER_COLOUR if rank <= ANNOTATED_MOVERS else MOVER_COLOUR


def _grid_shape(count: int) -> Tuple[int, int]:
    """Rows and columns for ``count`` panels.

    Args:
        count: The number of panels.

    Returns:
        ``(rows, columns)``, never smaller than one by one -- an empty golden file would
        otherwise ask for a figure with no axes at all.
    """
    columns = min(PANEL_COLUMNS, max(1, count))
    return (max(1, math.ceil(count / columns)), columns)


def _y_label(panel: Panel) -> str:
    """The y-axis meaning of one panel.

    Args:
        panel: The panel.

    Returns:
        A short description of the plotted quantity.
    """
    if panel.mode == MODE_ABSOLUTE:
        return "abs. change"
    return "% vs first"


def render_png(history: PairHistory, path: Path) -> None:
    """Draw one pair's small multiples into a PNG.

    Args:
        history: The pair's assembled history.
        path: The file to write.
    """
    rows, columns = _grid_shape(len(history.panels))
    figure, axes = plt.subplots(
        rows, columns, figsize=(columns * 3.0, rows * 1.75), squeeze=False, constrained_layout=True
    )
    x_values = list(range(len(history.commits)))
    labels = [commit.label for commit in history.commits]
    movers = sum(1 for panel in history.panels if panel.moved)
    figure.suptitle(
        f"{history.stem}  —  {len(history.panels)} KPIs, {movers} of them moved "
        f"across {len(history.commits)} blesses ({labels[0]} … {labels[-1]})",
        fontsize=11,
    )

    for index in range(rows * columns):
        axis = axes[index // columns][index % columns]
        if index >= len(history.panels):
            axis.axis("off")
            continue
        panel = history.panels[index]
        colour = _panel_colour(panel, index + 1)
        axis.set_title(_wrap(panel_title(panel, index + 1), 34, 2), fontsize=6, color=colour)
        axis.tick_params(labelsize=5)
        axis.set_ylabel(_y_label(panel), fontsize=5)
        if panel.mode == MODE_EMPTY:
            axis.text(0.5, 0.5, "no numeric values", fontsize=6, color=QUIET_COLOUR,
                      ha="center", va="center", transform=axis.transAxes)
            axis.set_yticks([])
        else:
            y_values = [math.nan if value is None else value for value in panel.plotted]
            axis.plot(x_values, y_values, marker="o", markersize=3, linewidth=1.0, color=colour)
            axis.axhline(0.0, color="#dddddd", linewidth=0.6, zorder=0)
        axis.set_xlim(-0.5, len(history.commits) - 0.5)
        axis.set_xticks(x_values)
        bottom_of_column = index + columns >= len(history.panels)
        axis.set_xticklabels(
            labels if bottom_of_column else [""] * len(labels), rotation=90, fontsize=4.5
        )

    figure.savefig(path, dpi=110)
    plt.close(figure)


def render_html(history: PairHistory, path: Path, plotly_src: str) -> None:
    """Draw one pair's small multiples into an interactive page.

    Args:
        history: The pair's assembled history.
        path: The file to write.
        plotly_src: The relative path the page's script tag references.
    """
    rows, columns = _grid_shape(len(history.panels))
    figure = make_subplots(
        rows=rows,
        cols=columns,
        subplot_titles=[panel_title(panel, i + 1) for i, panel in enumerate(history.panels)],
        vertical_spacing=min(0.06, 0.9 / max(rows, 1)),
        horizontal_spacing=0.04,
    )
    labels = [commit.label for commit in history.commits]
    subjects = [commit.subject for commit in history.commits]

    for index, panel in enumerate(history.panels):
        if panel.mode == MODE_EMPTY:
            continue
        figure.add_trace(
            go.Scatter(
                x=labels,
                y=[None if value is None else value for value in panel.plotted],
                customdata=[
                    [subject, "n/a" if raw is None else repr(raw)]
                    for subject, raw in zip(subjects, panel.values)
                ],
                mode="lines+markers",
                name=panel.kpi,
                line={"color": _panel_colour(panel, index + 1), "width": 1.4},
                marker={"size": 5},
                hovertemplate=(
                    f"<b>{html.escape(panel.kpi)}</b><br>%{{x}}<br>"
                    f"{_y_label(panel)}: %{{y:.6g}}<br>value: %{{customdata[1]}}"
                    "<br>%{customdata[0]}<extra></extra>"
                ),
                showlegend=False,
            ),
            row=index // columns + 1,
            col=index % columns + 1,
        )

    movers = sum(1 for panel in history.panels if panel.moved)
    figure.update_layout(
        title=(
            f"{history.stem} — {len(history.panels)} KPIs, {movers} moved, "
            f"{len(history.commits)} blesses"
        ),
        height=max(400, rows * 210 + 120),
        margin={"l": 50, "r": 20, "t": 90, "b": 40},
        template="plotly_white",
        font={"size": 10},
    )
    figure.update_annotations(font_size=8)
    figure.update_xaxes(tickangle=-90, tickfont={"size": 7}, showticklabels=True)
    figure.update_yaxes(tickfont={"size": 7})
    figure.write_html(str(path), include_plotlyjs=plotly_src, full_html=True, auto_open=False)


def render_index(histories: Sequence[PairHistory], formats: Sequence[str], move_counts: Mapping[str, int],
                 path: Path) -> None:
    """Write the page that links every pair's figures.

    Args:
        histories: The assembled histories, in output order.
        formats: The formats that were written (``"png"``, ``"html"``).
        move_counts: How many moves each pair contributed to ``moves.csv``.
        path: The file to write.
    """
    rows = []
    for history in histories:
        links = " ".join(
            f'<a href="{html.escape(history.stem)}.{fmt}">{fmt}</a>' for fmt in formats
        )
        top = ", ".join(
            html.escape(panel.kpi[len("BUI1."):]) for panel in history.panels[:ANNOTATED_MOVERS] if panel.moved
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(history.stem)}</td>"
            f"<td class=n>{len(history.commits)}</td>"
            f"<td class=n>{len(history.panels)}</td>"
            f"<td class=n>{sum(1 for panel in history.panels if panel.moved)}</td>"
            f"<td class=n>{move_counts.get(history.stem, 0)}</td>"
            f"<td>{top or '<span class=q>nothing moved</span>'}</td>"
            f"<td>{links}</td></tr>"
        )
    path.write_text(
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>Golden reference history</title><style>"
        "body{font:14px system-ui,sans-serif;margin:2rem;max-width:70rem}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border-bottom:1px solid #ddd;padding:.35rem .5rem;text-align:left;vertical-align:top}"
        "td.n{text-align:right}.q{color:#999}a{margin-right:.5rem}"
        "</style></head><body><h1>Golden reference history</h1>"
        "<p>How every golden KPI moved across the blesses. Percent against each KPI's first "
        "recorded value; panels marked <code>[abs]</code> start at zero and show the absolute "
        "change instead. Every move is also a row in <a href=\"moves.csv\">moves.csv</a>.</p>"
        "<table><thead><tr><th>pair</th><th>blesses</th><th>KPIs</th><th>movers</th><th>moves</th>"
        "<th>largest movers</th><th>figures</th></tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table></body></html>\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="golden_history.py",
        description="Draw how every golden KPI moved across the blesses.",
    )
    parser.add_argument(
        "--pairs", nargs="+", default=[], metavar="STEM",
        help="Golden filename stems or setup ids to draw (default: every configured pair "
             "that has a golden file today).",
    )
    parser.add_argument(
        "--since", default=None, metavar="DATE_OR_SHA",
        help="Start the walk at this commit (inclusive), or keep commits at or after this date.",
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT_DIR, metavar="DIR",
        help=f"Output directory (default: {DEFAULT_OUT_DIR}).",
    )
    parser.add_argument(
        "--formats", default="png,html",
        help="Comma-separated output formats: png, html (default: both).",
    )
    parser.add_argument(
        "--max-commits", type=int, default=None, metavar="N",
        help="Keep only the N most recent commits of the walk.",
    )
    parser.add_argument(
        "--repo", type=Path, default=_REPO_ROOT, metavar="DIR",
        help="Repository to read the history from (default: this checkout).",
    )
    return parser


def parse_formats(raw: str) -> List[str]:
    """Validate the ``--formats`` argument.

    Args:
        raw: The comma-separated argument.

    Returns:
        The formats, in a stable order.

    Raises:
        ValueError: On an unknown or empty format list.
    """
    wanted = [part.strip().lower() for part in raw.split(",") if part.strip()]
    unknown = [fmt for fmt in wanted if fmt not in ("png", "html")]
    if unknown or not wanted:
        raise ValueError(f"--formats must name png and/or html, got {raw!r}")
    return [fmt for fmt in ("png", "html") if fmt in wanted]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Walk the history, draw the figures and write ``moves.csv``.

    Args:
        argv: Command-line arguments; ``None`` reads ``sys.argv``.

    Returns:
        ``0`` on success, ``2`` on a bad argument.
    """
    args = build_parser().parse_args(argv)
    try:
        formats = parse_formats(args.formats)
        available = configured_pairs(DEFAULT_CONFIG_PATH, args.repo / GOLDEN_DIR_NAME)
        stems = resolve_pairs(args.pairs, available)
        commits = golden_commits(args.repo, args.since, args.max_commits)
    except ValueError as error:
        print(f"golden_history: {error}", file=sys.stderr)
        return 2

    if not commits:
        print("golden_history: no commit touches golden_references/ in that window.", file=sys.stderr)
        return 2

    histories = [history for history in walk_history(args.repo, stems, commits) if history.commits]
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    moves_by_pair = {history.stem: collect_moves(history) for history in histories}
    rows = write_moves_csv([move for moves in moves_by_pair.values() for move in moves], out_dir / "moves.csv")
    move_counts = {stem: len(moves) for stem, moves in moves_by_pair.items()}

    if "html" in formats:
        # One shared bundle rather than 4.3 MB embedded in each page: the pages reference it
        # relatively, so the directory as a whole opens without a network.
        (out_dir / "plotly.min.js").write_text(get_plotlyjs(), encoding="utf-8")
    for history in histories:
        if "png" in formats:
            render_png(history, out_dir / f"{history.stem}.png")
        if "html" in formats:
            render_html(history, out_dir / f"{history.stem}.html", "plotly.min.js")
    render_index(histories, formats, move_counts, out_dir / "index.html")

    print(
        f"golden_history: {len(commits)} commits walked, {len(histories)} pairs drawn, "
        f"{rows} moves -> {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
