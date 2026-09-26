"""The tier-1 report on disk: ``report.json``, the matrix page and one page per probe.

Everything is written from :meth:`VerificationReport.to_json`, so the pages and the JSON cannot
say different things. The pages are static HTML with inline CSS and a few lines of inline script
for the two filters; no external asset, so the directory opens from a CI artifact as it is::

    <out>/report.json          the whole run, machine-readable
    <out>/index.html           the matrix: one row per probe, columns req | map | sys
    <out>/probes/<page>.html   one probe, its path drawn top to bottom

Nothing in any of them reads a clock, so two runs of one commit write the same bytes.
"""

import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Sequence

from hisim.renovisor.verify.runner import CellState, Stage, VerificationReport


class ReportWriter:
    """Writes one :class:`VerificationReport` into a directory."""

    #: The machine-readable file.
    JSON_FILE: ClassVar[str] = "report.json"

    #: The matrix page.
    INDEX_FILE: ClassVar[str] = "index.html"

    #: The directory of the probe pages.
    PROBE_DIRECTORY: ClassVar[str] = "probes"

    @classmethod
    def write(cls, report: VerificationReport, directory: Path) -> Dict[str, Any]:
        """Write the three kinds of file and return the JSON document that was written.

        Args:
            report: The run.
            directory: Where to write; created when missing.

        Returns:
            The content of ``report.json``, each probe with the ``page`` it links to.
        """
        document = report.to_json()
        for probe in document["probes"]:
            probe["page"] = f"{cls.PROBE_DIRECTORY}/{cls.page_name(probe['name'])}"
        pages = directory / cls.PROBE_DIRECTORY
        pages.mkdir(parents=True, exist_ok=True)
        (directory / cls.JSON_FILE).write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (directory / cls.INDEX_FILE).write_text(MatrixPage.render(document), encoding="utf-8")
        names = {probe["name"]: probe["page"] for probe in document["probes"]}
        for probe in document["probes"]:
            (directory / probe["page"]).write_text(ProbePage.render(probe, document, names), encoding="utf-8")
        return document

    @staticmethod
    def page_name(name: str) -> str:
        """Return the file name of one probe's page: readable, short, unique, and safe for an artifact.

        A probe name can carry a whole material object and characters an artifact upload refuses
        (``:``, ``"``, ``*``), so the readable part is cut and cleaned and a hash of the whole
        name makes it unique.
        """
        readable = re.sub(r"[^A-Za-z0-9._=-]+", "_", name)[:72].strip("_")
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        return f"{readable}-{digest}.html"


#: The page style, shared by both kinds of page. Light and dark from the reader's system setting.
STYLE = """
:root { --bg:#fff; --fg:#1d232b; --muted:#5b6673; --line:#d9dee4; --head:#f3f5f7;
        --ok:#1f7a35; --listed:#9a6b00; --none:#8a3ab9; --fail:#b3261e; --code:#f6f8fa; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171b; --fg:#e4e8ec; --muted:#9aa5b1; --line:#2c333b; --head:#1c2127;
          --ok:#5cc47a; --listed:#e0b04a; --none:#c58af0; --fail:#ff7a70; --code:#1c2127; }
}
body { background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:0 16px 48px; }
h1 { font-size:20px; margin:20px 0 4px; } h2 { font-size:16px; margin:24px 0 8px; }
.muted { color:var(--muted); } a { color:inherit; }
table { border-collapse:collapse; width:100%; }
th, td { border-bottom:1px solid var(--line); padding:4px 8px; text-align:left; vertical-align:top; }
th { background:var(--head); position:sticky; top:0; }
td.cell { text-align:center; font-size:16px; width:3em; }
.as_expected { color:var(--ok); } .as_listed { color:var(--listed); } .no_effect { color:var(--none); }
.failed { color:var(--fail); } .not_run { color:var(--muted); }
code, .value { font-family:ui-monospace, monospace; font-size:12px; background:var(--code);
               padding:0 3px; word-break:break-word; }
.filters { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }
.stage { border-left:3px solid var(--line); padding:4px 0 4px 12px; margin:12px 0; }
.wrap { overflow-x:auto; }
"""


def _escape(value: Any) -> str:
    """Return a value as escaped HTML text."""
    return html.escape(str(value), quote=True)


def _value(row: Mapping[str, Any], key: str) -> str:
    """Return one side of a change as HTML: the JSON of its value, or *(absent)*."""
    if key not in row:
        return '<span class="muted">(absent)</span>'
    return f'<span class="value">{_escape(json.dumps(row[key], ensure_ascii=False))}</span>'


def _cell(state: str) -> str:
    """Return one matrix cell."""
    glyph = CellState(state).glyph
    return f'<td class="cell {state}" title="{_escape(CellState(state).legend)}">{glyph}</td>'


def _legend(document: Mapping[str, Any]) -> str:
    """Return the legend line."""
    items = [
        f'<span class="{state}">{entry["glyph"]}</span> {_escape(entry["meaning"])}'
        for state, entry in document["legend"].items()
    ]
    return '<p class="muted">' + " &nbsp; ".join(items) + "</p>"


def _page(title: str, body: str, script: str = "") -> str:
    """Return a whole HTML page."""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_escape(title)}</title><style>{STYLE}</style></head><body>\n{body}\n"
        f"{('<script>' + script + '</script>') if script else ''}</body></html>\n"
    )


class MatrixPage:
    """``index.html``: the failures, the findings and one row per probe."""

    #: Filters rows by category and by cell state, client-side, with no library.
    SCRIPT: ClassVar[str] = """
const category = document.getElementById('category'), state = document.getElementById('state'),
      text = document.getElementById('text');
function apply() {
  for (const row of document.querySelectorAll('#matrix tbody tr')) {
    const byCategory = !category.value || row.dataset.category === category.value;
    const byState = !state.value || row.dataset.states.split(' ').includes(state.value);
    const byText = !text.value || row.dataset.name.toLowerCase().includes(text.value.toLowerCase());
    row.hidden = !(byCategory && byState && byText);
  }
}
for (const control of [category, state, text]) control.addEventListener('input', apply);
"""

    @classmethod
    def render(cls, document: Mapping[str, Any]) -> str:
        """Return the page."""
        summary = document["summary"]
        translator = document["translator"]
        parts: List[str] = [
            "<h1>Path verification, tier 1</h1>",
            f'<p class="muted">translator {_escape(translator["version"])} at HiSim '
            f'{_escape(translator["hisim_commit"])} &middot; {summary["probes"]} probes, '
            f'{summary["translations"]} translations &middot; stages 1&ndash;3 (request, mapping report, '
            "energy system); stages 4&ndash;5 run in tier 2</p>",
            _legend(document),
            cls._tally(summary),
            cls._issues("Failures", document["failures"], document),
            cls._issues("Findings", document["findings"], document),
            cls._filters(document["probes"]),
            cls._table(document["probes"]),
        ]
        return _page("Path verification", "\n".join(parts), cls.SCRIPT)

    @staticmethod
    def _tally(summary: Mapping[str, Any]) -> str:
        """Return the table of how many cells carry each state."""
        states = [state.value for state in CellState]
        head = "".join(f'<th class="{state}">{CellState(state).glyph}</th>' for state in states)
        rows = "".join(
            f"<tr><td>{stage}</td>" + "".join(f"<td>{counts[state]}</td>" for state in states) + "</tr>"
            for stage, counts in summary["cells"].items()
        )
        return (
            f'<p><strong>{summary["failures"]}</strong> failure(s), <strong>{summary["findings"]}</strong> '
            "finding(s).</p>"
            f'<div class="wrap"><table style="width:auto"><thead><tr><th>stage</th>{head}</tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
        )

    @staticmethod
    def _issues(title: str, issues: Sequence[Mapping[str, Any]], document: Mapping[str, Any]) -> str:
        """Return one titled list of issues, each linked to its probe."""
        if not issues:
            return f"<h2>{title}</h2><p class=\"muted\">none</p>"
        pages = {probe["name"]: probe["page"] for probe in document["probes"]}
        items = []
        for issue in issues:
            where = ""
            if issue.get("probe"):
                where = f'<a href="{_escape(pages[issue["probe"]])}">{_escape(issue["probe"])}</a>: '
            items.append(f"<li><code>{_escape(issue['code'])}</code> {where}{_escape(issue['message'])}</li>")
        return f"<details open><summary><h2 style=\"display:inline\">{title} ({len(issues)})</h2></summary>" \
               f"<ul>{''.join(items)}</ul></details>"

    @staticmethod
    def _filters(probes: Sequence[Mapping[str, Any]]) -> str:
        """Return the category and state filters and the name search."""
        categories = sorted({probe["category"] for probe in probes})
        options = "".join(
            f'<option value="{_escape(category)}">{_escape(category)}</option>' for category in categories
        )
        states = "".join(
            f'<option value="{state.value}">{state.glyph} {_escape(state.legend)}</option>' for state in CellState
        )
        return (
            '<div class="filters">'
            f'<label>category <select id="category"><option value="">all</option>{options}</select></label>'
            f'<label>state <select id="state"><option value="">any</option>{states}</select></label>'
            '<label>name <input id="text" type="search"></label></div>'
        )

    @staticmethod
    def _table(probes: Sequence[Mapping[str, Any]]) -> str:
        """Return the matrix."""
        rows: List[str] = []
        for probe in probes:
            cells = probe["cells"]
            remark = next(
                (probe["remarks"].get(stage.value, "") for stage in Stage
                 if cells[stage.value] not in (CellState.AS_EXPECTED.value, CellState.NOT_RUN.value)),
                "",
            )
            rows.append(
                f'<tr data-category="{_escape(probe["category"])}" data-name="{_escape(probe["name"])}" '
                f'data-states="{" ".join(sorted(set(cells.values())))}">'
                f'<td><a href="{_escape(probe["page"])}">{_escape(probe["name"])}</a></td>'
                f'<td class="muted">{_escape(probe["category"])}</td>'
                + "".join(_cell(cells[stage.value]) for stage in Stage)
                + f'<td class="muted">{_escape(remark)}</td></tr>'
            )
        head = "".join(f'<th class="cell">{stage.value}</th>' for stage in Stage)
        return (
            '<div class="wrap"><table id="matrix"><thead><tr><th>probe</th><th>category</th>'
            f"{head}<th>remark</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )


class ProbePage:
    """One probe's page: the path from the request to the energy system, values on both sides."""

    @classmethod
    def render(cls, probe: Mapping[str, Any], document: Mapping[str, Any], pages: Mapping[str, str]) -> str:
        """Return the page.

        Args:
            probe: The probe's entry of ``report.json``.
            document: The whole of ``report.json``, for the legend.
            pages: Probe name -> its page, to link the base.
        """
        base = probe["base"]
        base_name = base.get("name")
        if base_name in pages:
            base_link = f'<a href="../{_escape(pages[base_name])}">{_escape(base_name)}</a>'
        else:
            base_link = _escape(base_name or "none")
        cells = probe["cells"]
        remarks = probe.get("remarks", {})
        parts = [
            '<p><a href="../index.html">&larr; matrix</a></p>',
            f"<h1>{_escape(probe['name'])}</h1>",
            f'<p class="muted">{_escape(probe["kind"])} &middot; {_escape(probe["category"])} &middot; request '
            f'<code>{_escape(probe["request_hash"])}</code></p>',
            f"<p>base: {base_link} <span class=\"muted\">({_escape(base.get('reason', ''))})</span></p>",
            _legend(document),
            cls._stage("UI", '<p class="muted">not recorded (stage 0 is recorded from the e2e suite, spec §4)</p>'),
            cls._stage(
                f"request {cls._badge(cells['req'])}",
                cls._remark(remarks.get("req")) + cls._changes(probe["stage1"]["changes"], with_source=False),
            ),
            cls._stage(f"translator {cls._badge(cells['map'])}", cls._remark(remarks.get("map")) + cls._lines(probe)),
            cls._stage(f"system {cls._badge(cells['sys'])}", cls._remark(remarks.get("sys")) + cls._system(probe)),
            cls._stage("realized", '<p class="muted">not run (tier 2)</p>'),
            cls._stage("KPI", '<p class="muted">not run (tier 2)</p>'),
        ]
        return _page(probe["name"], "\n".join(parts))

    @staticmethod
    def _badge(state: str) -> str:
        """Return a cell state as a coloured glyph."""
        return f'<span class="{state}" title="{_escape(CellState(state).legend)}">{CellState(state).glyph}</span>'

    @staticmethod
    def _remark(remark: Any) -> str:
        """Return a column's remark as a paragraph, or nothing."""
        return f'<p class="muted">{_escape(remark)}</p>' if remark else ""

    @staticmethod
    def _stage(title: str, body: str) -> str:
        """Return one stage's block."""
        return f'<div class="stage"><h2>{title}</h2>{body}</div>'

    @staticmethod
    def _changes(changes: Sequence[Mapping[str, Any]], with_source: bool) -> str:
        """Return a table of ``path: before -> after`` rows."""
        if not changes:
            return '<p class="muted">no leaf differs</p>'
        head = "<th>path</th><th>before</th><th>after</th>" + ("<th>asked for by</th>" if with_source else "")
        rows = "".join(
            f"<tr><td><code>{_escape(row['path'])}</code></td><td>{_value(row, 'before')}</td>"
            f"<td>{_value(row, 'after')}</td>"
            + (f"<td class=\"muted\">{_escape(row.get('source', ''))}</td>" if with_source else "")
            + "</tr>"
            for row in changes
        )
        return f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'

    @staticmethod
    def _lines(probe: Mapping[str, Any]) -> str:
        """Return the mapping report's lines for the changed leaves."""
        refused = probe.get("refused")
        if refused:
            items = "".join(
                f"<li><code>{_escape(problem['code'])}</code> at <code>{_escape(problem['path'])}</code>: "
                f"{_escape(problem['message'])}</li>"
                for problem in refused["problems"]
            )
            who = "the request schema" if refused["by"] == "schema" else "a semantic check"
            return f"<p>refused by {who}:</p><ul>{items}</ul>"
        if probe.get("error"):
            return (
                f"<p class=\"failed\">{_escape(probe['error'])}</p>"
                f"<div class=\"wrap\"><pre><code>{_escape(probe.get('traceback') or '')}</code></pre></div>"
            )
        lines = probe["stage2"]
        if not lines:
            return '<p class="muted">no changed leaf</p>'
        rows = "".join(
            f"<tr><td><code>{_escape(line['line'])}</code>{' (removed)' if line.get('removed') else ''}</td>"
            f"<td>{_escape(line['status'])}</td><td class=\"muted\">{_escape(line.get('announced', ''))}</td>"
            f"<td><code>{_escape(line.get('target', ''))}</code></td>"
            f"<td>{_value(line, 'value') if 'value' in line else ''}</td>"
            f"<td class=\"muted\">{_escape(line.get('note', ''))}</td></tr>"
            for line in lines
        )
        return (
            '<div class="wrap"><table><thead><tr><th>report line</th><th>status</th><th>announced</th>'
            f"<th>target</th><th>value</th><th>note</th></tr></thead><tbody>{rows}</tbody></table></div>"
        )

    @classmethod
    def _system(cls, probe: Mapping[str, Any]) -> str:
        """Return the energy-system diff with the base file on both sides."""
        stage = probe["stage3"]
        base_file = stage["base_file"]
        head = ""
        if base_file["before"] != base_file["after"]:
            head = (
                f"<p>base file <code>{_escape(base_file['before'])}</code> &rarr; "
                f"<code>{_escape(base_file['after'])}</code></p>"
            )
        elif base_file["after"]:
            head = f"<p class=\"muted\">base file <code>{_escape(base_file['after'])}</code> on both sides</p>"
        return head + cls._changes(stage["changes"], with_source=True)
