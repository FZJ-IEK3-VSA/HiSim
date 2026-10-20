"""Checks a finished run's energy flows, fails it when they do not hold, and writes its report and Sankeys.

The check runs in every simulation (owner, 2026-10-02: always fail hard). :meth:`EnergyBalanceReport.conclude` fails
the run with :class:`EnergyBalanceError` when a declared port is NaN or infinite, when a component's balance does
not close, when the two sides of a transfer disagree beyond the tolerance, when only one of two declared
components books a transfer between them, or, in a run without undeclared components, when a meter publishes other
than it reads or a metered port passes no meter (:mod:`.meters`); the message names every one of them. A run in
which no component declares a port passes.

:class:`~hisim.postprocessingoptions.PostProcessingOptions` ``EXPORT_ENERGY_BALANCE`` only decides whether the
files are written, before the run is failed. ``balance_report.json`` (schema ``hisim.energy_balance/2``) lists, per
declared component, the run's in / out / loss / storage change in kWh (in total and per carrier), the residual in kWh
and in percent of the throughput, the worst step and the verdict; then every link between declared components (a
side that books nothing is null, and a failing link states its reason); every meter with what it publishes, the
ports it reads and whether the two agree; every unmetered port; every unlinked port; every undeclared component; and
every exempt component with its reason. Beside it, ``energy_sankeys/`` holds
``energy_sankey.json`` (every view's nodes and links: ``overall``, the connected graph, and one filter per carrier)
and one plotly HTML per view, which share one ``plotly.min.js``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Sequence

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
from hisim.postprocessing.energy_balance.meters import MeterCheck
from hisim.postprocessing.energy_balance.ports import DeclaredPorts
from hisim.postprocessing.energy_balance.sankey import SankeyBuilder, SankeyDiagram


class EnergyBalanceReport:
    """Checks one finished run, writes its report and Sankeys on request, and fails the run when it must."""

    #: The report's file name in the result directory.
    REPORT_FILE: ClassVar[str] = "balance_report.json"
    #: The directory of the Sankeys in the result directory.
    SANKEY_DIRECTORY: ClassVar[str] = "energy_sankeys"
    #: The file of every view's nodes and links in the Sankey directory.
    SANKEY_JSON_FILE: ClassVar[str] = "energy_sankey.json"
    #: The report's schema: 2 adds the meters, the unmetered and unlinked ports and the exempt components.
    REPORT_SCHEMA: ClassVar[str] = "hisim.energy_balance/2"

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
        self.meter_check = MeterCheck(self.declared, tolerance)

    @property
    def failing(self) -> List[ComponentBalance]:
        """The components whose balance does not close."""
        return [balance for balance in self.balances if not balance.closes]

    @property
    def failing_links(self) -> List[LinkBalance]:
        """The transfers whose two sides disagree beyond the tolerance, or that only one side books."""
        return [link for link in self.links if not link.closes]

    @property
    def verdict(self) -> str:
        """``nothing_declared``, ``closes`` or ``does_not_close``."""
        if not self.balances:
            return "nothing_declared"
        return "does_not_close" if self.fails else "closes"

    @property
    def fails(self) -> bool:
        """Whether a balance, a link, an enforced meter balance or the enforced rule that flows are metered fails."""
        return bool(
            self.failing or self.failing_links or self.meter_check.failing or self.meter_check.failing_unmetered
        )

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
            "schema": self.REPORT_SCHEMA,
            "seconds_per_timestep": self.seconds_per_timestep,
            "timesteps": 0 if self.declared.index is None else len(self.declared.index),
            "tolerance": self.tolerance.as_dict(),
            "verdict": self.verdict,
            "components": [self.component_entry(balance) for balance in self.balances],
            "links": [link.as_dict() for link in self.links],
            "meters": [meter.as_dict(self.step_time) for meter in self.meter_check.balances],
            "unmetered": self.meter_check.unmetered_entry(),
            "unlinked": [
                {
                    "component": series.component,
                    "output": series.output.field_name,
                    "role": series.port.role.value,
                    "carrier": series.port.carrier.value,
                    "total_kwh": series.annual_kilowatt_hours,
                }
                for series in self.declared.unlinked()
            ],
            "undeclared": [
                {"component": name, "class": self.declared.class_names.get(name, "")}
                for name in self.declared.undeclared
            ],
            "exempt": [
                {"component": name, "class": self.declared.class_names.get(name, ""), "reason": reason}
                for name, reason in self.declared.exempt.items()
            ],
        }

    def sankeys(self) -> Dict[str, Dict[str, Any]]:
        """Every view of the run's Sankey graph: ``overall``, the connected graph, then one filter per carrier."""
        builder = SankeyBuilder(self.declared, self.balances, self.meter_check.balances)
        links = builder.build()
        return SankeyDiagram.views(links, builder.kinds)

    def write(self, result_directory: str, with_html: bool = True) -> Path:
        """Write the report and the Sankeys into ``result_directory``; this never fails the run.

        Returns:
            The path of ``balance_report.json``.
        """
        directory = Path(result_directory)
        report_path = directory / self.REPORT_FILE
        with open(report_path, "w", encoding="utf-8") as stream:
            json.dump(self.report(), stream, indent=2)
        diagrams = self.sankeys()
        sankey_directory = directory / self.SANKEY_DIRECTORY
        sankey_directory.mkdir(exist_ok=True)
        with open(sankey_directory / self.SANKEY_JSON_FILE, "w", encoding="utf-8") as stream:
            json.dump(diagrams, stream, indent=2)
        if with_html:
            for name, diagram in diagrams.items():
                figure = SankeyDiagram.figure(diagram, SankeyDiagram.title(name))
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
            EnergyBalanceError: When a declared port is not finite, a balance does not close, a link's two
                sides disagree beyond the tolerance, only one of two declared components books a transfer, or, in a
                run without undeclared components, a meter publishes other than it reads or a metered port passes
                no meter.
        """
        if self.nonfinite:
            raise EnergyBalanceError(self.nonfinite_message())
        report_path = None if result_directory is None else self.write(result_directory, with_html)
        meters = self.meter_check.balances
        log.information(
            f"Energy balance: {len(self.balances) - len(self.failing)} of {len(self.balances)} declared components "
            f"close, {len(self.links) - len(self.failing_links)} of {len(self.links)} links between them close, "
            f"{sum(meter.agrees for meter in meters)} of {len(meters)} meters publish what they read, "
            f"{len(self.meter_check.unmetered)} ports unmetered, "
            f"{len(self.declared.unlinked())} ports unlinked, {len(self.declared.undeclared)} components undeclared, "
            f"{len(self.declared.exempt)} exempt"
            + ("." if report_path is None else f"; report in {report_path}.")
        )
        if self.fails:
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
        """The error of a run whose balances, links or meters do not hold, naming each with its reason."""
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
            lines.append(f"- link {link.sender} -> {link.receiver} ({link.carrier}): {link.reason}")
        for meter in self.meter_check.failing:
            lines.append(
                f"- {meter.describe()} ({self.step_time(meter.worst_step)}): the meter reads a flow no port declares, "
                f"or a port linked to it is not what the meter reads"
            )
        for series in self.meter_check.failing_unmetered:
            lines.append(
                f"- port {series.name} ({series.port.role.value}, {series.port.carrier.value}, "
                f"{series.annual_kilowatt_hours:.6g} kWh) passes no meter: its peer is {series.peer.name}"
            )
        where = "" if report_path is None else f"\nSee {report_path}."
        return (
            f"The energy balance of this run does not close (tolerance: {self.tolerance.describe()}):\n"
            + "\n".join(lines)
            + where
        )
