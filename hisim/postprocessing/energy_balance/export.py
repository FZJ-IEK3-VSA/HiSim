"""Checks a finished run's energy flows, fails it when they do not hold, and writes its report and Sankeys.

The check runs in every simulation (owner, 2026-10-02: always fail hard). :meth:`EnergyBalanceReport.conclude`
fails the run with :class:`EnergyBalanceError` when a declared port is NaN or infinite, when a component's
balance does not close, or when the two sides of a transfer disagree beyond the tolerance; the message names
every one of them. A run in which no component declares a port passes.

:class:`~hisim.postprocessingoptions.PostProcessingOptions` ``EXPORT_ENERGY_BALANCE`` only decides whether the
files are written, before the run is failed: ``balance_report.json`` lists, per declared component, the run's in /
out / loss / storage change in kWh (in total and per carrier), the residual in kWh and in percent of the
throughput, the worst step and the verdict, then every paired link and every undeclared component. Beside it,
``energy_sankeys/`` holds ``energy_sankey.json`` (every diagram's nodes and links) and one plotly HTML per
carrier plus ``overall.html``, which share one ``plotly.min.js``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from hisim import log
from hisim.component import ComponentOutput
from hisim.component_wrapper import ComponentWrapper
from hisim.postprocessing.energy_balance.check import (
    BalanceCheck,
    ComponentBalance,
    EnergyBalanceError,
    LinkBalance,
    NonFinitePort,
    Tolerance,
)
from hisim.postprocessing.energy_balance.ports import DeclaredPorts
from hisim.postprocessing.energy_balance.sankey import SankeyBuilder, SankeyDiagram

REPORT_FILE = "balance_report.json"
SANKEY_DIRECTORY = "energy_sankeys"
SANKEY_JSON_FILE = "energy_sankey.json"
REPORT_SCHEMA = "hisim.energy_balance/1"


class EnergyBalanceReport:
    """Checks one finished run, writes its report and Sankeys on request, and fails the run when it must."""

    def __init__(
        self,
        results: pd.DataFrame,
        all_outputs: Sequence[ComponentOutput],
        wrapped_components: Sequence[ComponentWrapper],
        seconds_per_timestep: float,
        tolerance: Tolerance = Tolerance(),
    ) -> None:
        """Collect the declared ports of the run and check every declared component and link."""
        self.seconds_per_timestep = seconds_per_timestep
        self.tolerance = tolerance
        self.declared = DeclaredPorts.collect(results, all_outputs, wrapped_components, seconds_per_timestep)
        check = BalanceCheck(tolerance)
        self.nonfinite: List[NonFinitePort] = check.nonfinite(self.declared)
        self.balances: List[ComponentBalance] = check.run(self.declared)
        self.links: List[LinkBalance] = check.links(self.declared)

    @property
    def failing(self) -> List[ComponentBalance]:
        """The components whose balance does not close."""
        return [balance for balance in self.balances if not balance.closes]

    @property
    def failing_links(self) -> List[LinkBalance]:
        """The transfers whose two sides disagree beyond the tolerance."""
        return [link for link in self.links if not link.closes]

    @property
    def verdict(self) -> str:
        """``nothing_declared``, ``closes`` or ``does_not_close``."""
        if not self.balances:
            return "nothing_declared"
        return "closes" if not self.failing and not self.failing_links else "does_not_close"

    def step_time(self, step: Optional[int]) -> Optional[str]:
        """The time stamp of a step, as the result table indexes it."""
        index = self.declared.index
        if step is None or index is None or step >= len(index):
            return None
        return str(index[step].isoformat()) if hasattr(index[step], "isoformat") else str(index[step])

    def component_entry(self, balance: ComponentBalance) -> Dict[str, Any]:
        """One component's entry of the report."""
        worst = balance.worst_step
        return {
            "component": balance.component,
            "class": balance.class_name,
            "verdict": "closes" if balance.closes else "does_not_close",
            **balance.totals.as_dict(),
            "throughput_kwh": balance.throughput,
            "residual_kwh": balance.residual,
            "residual_percent": balance.residual_percent,
            "failing_steps": int(balance.step_failures.sum()),
            "worst_step": None
            if worst is None
            else {
                "index": worst,
                "time": self.step_time(worst),
                "residual_kwh": float(balance.residual_per_step[worst]),
                "throughput_kwh": float(balance.throughput_per_step[worst]),
            },
            "carriers": {carrier: totals.as_dict() for carrier, totals in balance.carriers.items()},
            "ports": [series.as_dict() for series in balance.ports],
        }

    def report(self) -> Dict[str, Any]:
        """The whole report as a JSON-ready dictionary."""
        return {
            "schema": REPORT_SCHEMA,
            "seconds_per_timestep": self.seconds_per_timestep,
            "timesteps": 0 if self.declared.index is None else len(self.declared.index),
            "tolerance": self.tolerance.as_dict(),
            "verdict": self.verdict,
            "components": [self.component_entry(balance) for balance in self.balances],
            "links": [link.as_dict() for link in self.links],
            "undeclared": [
                {"component": name, "class": self.declared.class_names.get(name, "")}
                for name in self.declared.undeclared
            ],
        }

    def sankeys(self) -> Dict[str, Dict[str, Any]]:
        """Every diagram of the run: one per carrier and ``overall``."""
        links = SankeyBuilder(self.declared, self.balances).build()
        return SankeyDiagram.per_carrier(links, self.declared)

    def write(self, result_directory: str, with_html: bool = True) -> Path:
        """Write the report and the Sankeys into ``result_directory``; this never fails the run.

        Returns:
            The path of ``balance_report.json``.
        """
        directory = Path(result_directory)
        report_path = directory / REPORT_FILE
        with open(report_path, "w", encoding="utf-8") as stream:
            json.dump(self.report(), stream, indent=2)
        diagrams = self.sankeys()
        sankey_directory = directory / SANKEY_DIRECTORY
        sankey_directory.mkdir(exist_ok=True)
        with open(sankey_directory / SANKEY_JSON_FILE, "w", encoding="utf-8") as stream:
            json.dump(diagrams, stream, indent=2)
        if with_html:
            for name, diagram in diagrams.items():
                figure = SankeyDiagram.figure(diagram, f"Energy flows over the run: {name} (kWh)")
                figure.write_html(str(sankey_directory / f"{name}.html"), include_plotlyjs="directory")
        return report_path

    def conclude(self, result_directory: Optional[str] = None, with_html: bool = True) -> Optional[Path]:
        """Fail the run on a port that is not finite, write the files when asked, then fail it on what does not close.

        A port that is not finite fails the run before anything is written, since no balance can be read from it.

        Args:
            result_directory: Where to write the report and the Sankeys; None writes nothing.
            with_html: Whether the Sankeys are also written as HTML.

        Returns:
            The path of ``balance_report.json`` when it was written.

        Raises:
            EnergyBalanceError: When a declared port is not finite, a balance does not close, or a link's two
                sides disagree beyond the tolerance.
        """
        if self.nonfinite:
            raise EnergyBalanceError(self.nonfinite_message())
        report_path = None if result_directory is None else self.write(result_directory, with_html)
        log.information(
            f"Energy balance: {len(self.balances) - len(self.failing)} of {len(self.balances)} declared components "
            f"close, {len(self.links) - len(self.failing_links)} of {len(self.links)} paired links close, "
            f"{len(self.declared.undeclared)} components undeclared"
            + ("." if report_path is None else f"; report in {report_path}.")
        )
        if self.failing or self.failing_links:
            raise EnergyBalanceError(self.failure_message(report_path))
        return report_path

    def nonfinite_message(self) -> str:
        """The error of a run with a declared port that is NaN or infinite, naming every such port."""
        lines = [
            f"- energy port {port.name} ({port.series.port.role.value}, {port.series.port.carrier.value}) is NaN or "
            f"infinite at step {port.first_step} ({self.step_time(port.first_step)}), in {port.steps} steps in all"
            for port in self.nonfinite
        ]
        return "The declared energy flows of this run are not finite:\n" + "\n".join(lines)

    def failure_message(self, report_path: Optional[Path] = None) -> str:
        """The error of a run whose balances or links do not close, naming every one."""
        lines: List[str] = []
        for balance in self.failing:
            carriers = ", ".join(
                f"{carrier} {totals.residual:.6g} kWh" for carrier, totals in balance.carriers.items()
            )
            worst = balance.worst_step
            worst_text = (
                "no step"
                if worst is None
                else f"step {worst} ({self.step_time(worst)}): residual {balance.residual_per_step[worst]:.6g} kWh "
                f"of {balance.throughput_per_step[worst]:.6g} kWh throughput"
            )
            lines.append(
                f"- component {balance.component} ({balance.class_name}): residual {balance.residual:.6g} kWh "
                f"({balance.residual_percent:.4g} % of {balance.throughput:.6g} kWh throughput); per carrier "
                f"{carriers}; {int(balance.step_failures.sum())} steps beyond tolerance, worst {worst_text}"
            )
        for link in self.failing_links:
            lines.append(
                f"- link {link.sender} -> {link.receiver} ({link.carrier}): {link.sender} sends {link.sent:.6g} kWh, "
                f"{link.receiver} receives {link.received:.6g} kWh ({link.difference_percent:.4g} % of the larger)"
            )
        where = "" if report_path is None else f"\nSee {report_path}."
        return (
            f"The energy balance of this run does not close (tolerance: {self.tolerance.describe()}):\n"
            + "\n".join(lines)
            + where
        )
