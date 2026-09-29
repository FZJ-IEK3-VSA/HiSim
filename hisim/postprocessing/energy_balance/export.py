"""Writes a run's energy balance report and its Sankeys into the result directory (hisim-9uoo.2, .9).

``balance_report.json`` lists, per declared component, the run's in / out / loss / storage change in kWh (in
total and per carrier), the residual in kWh and in percent of the throughput, the worst step and the verdict,
and then every undeclared component. Beside it, ``energy_sankeys/`` holds ``energy_sankey.json`` (every diagram's
nodes and links) and one plotly HTML per carrier plus ``overall.html``, which share one ``plotly.min.js``.

Each component whose balance does not close is logged as one WARNING line; in strict mode
(``HISIM_ENERGY_BALANCE=strict``) the run then fails with :class:`EnergyBalanceError`.
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
    BalanceMode,
    ComponentBalance,
    EnergyBalanceError,
    Tolerance,
)
from hisim.postprocessing.energy_balance.ports import DeclaredPorts
from hisim.postprocessing.energy_balance.sankey import SankeyBuilder, SankeyDiagram

REPORT_FILE = "balance_report.json"
SANKEY_DIRECTORY = "energy_sankeys"
SANKEY_JSON_FILE = "energy_sankey.json"
REPORT_SCHEMA = "hisim.energy_balance/1"


class EnergyBalanceReport:
    """Checks one finished run, writes its report and Sankeys, and applies the mode."""

    def __init__(
        self,
        results: pd.DataFrame,
        all_outputs: Sequence[ComponentOutput],
        wrapped_components: Sequence[ComponentWrapper],
        seconds_per_timestep: float,
        tolerance: Tolerance = Tolerance(),
    ) -> None:
        """Collect the declared ports of the run and check every declared component."""
        self.seconds_per_timestep = seconds_per_timestep
        self.tolerance = tolerance
        self.declared = DeclaredPorts.collect(results, all_outputs, wrapped_components, seconds_per_timestep)
        self.balances: List[ComponentBalance] = BalanceCheck(tolerance).run(self.declared)

    @property
    def failing(self) -> List[ComponentBalance]:
        """The components whose balance does not close."""
        return [balance for balance in self.balances if not balance.closes]

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
            "worst_step": {
                "index": worst,
                "time": self.step_time(worst),
                "residual_kwh": None if worst is None else float(balance.residual_per_step[worst]),
                "throughput_kwh": None if worst is None else float(balance.throughput_per_step[worst]),
            },
            "carriers": {carrier: totals.as_dict() for carrier, totals in balance.carriers.items()},
            "ports": [series.as_dict() for series in balance.ports],
        }

    def report(self, mode: BalanceMode) -> Dict[str, Any]:
        """The whole report as a JSON-ready dictionary."""
        return {
            "schema": REPORT_SCHEMA,
            "mode": mode.value,
            "seconds_per_timestep": self.seconds_per_timestep,
            "timesteps": 0 if self.declared.index is None else len(self.declared.index),
            "tolerance": self.tolerance.as_dict(),
            "verdict": "closes" if not self.failing else "does_not_close",
            "components": [self.component_entry(balance) for balance in self.balances],
            "undeclared": [
                {"component": name, "class": self.declared.class_names.get(name, "")}
                for name in self.declared.undeclared
            ],
        }

    def sankeys(self) -> Dict[str, Dict[str, Any]]:
        """Every diagram of the run: one per carrier and ``overall``."""
        links = SankeyBuilder(self.declared, self.balances).build()
        return SankeyDiagram.per_carrier(links, self.declared)

    def write(self, result_directory: str, mode: Optional[BalanceMode] = None, with_html: bool = True) -> Path:
        """Write the report and the Sankeys, log the failures, and fail the run in strict mode.

        Returns:
            The path of ``balance_report.json``.

        Raises:
            EnergyBalanceError: In strict mode, when a component's balance does not close.
        """
        mode = BalanceMode.from_environment() if mode is None else mode
        directory = Path(result_directory)
        report_path = directory / REPORT_FILE
        with open(report_path, "w", encoding="utf-8") as stream:
            json.dump(self.report(mode), stream, indent=2)
        diagrams = self.sankeys()
        sankey_directory = directory / SANKEY_DIRECTORY
        sankey_directory.mkdir(exist_ok=True)
        with open(sankey_directory / SANKEY_JSON_FILE, "w", encoding="utf-8") as stream:
            json.dump(diagrams, stream, indent=2)
        if with_html:
            for name, diagram in diagrams.items():
                figure = SankeyDiagram.figure(diagram, f"Energy flows over the run: {name} (kWh)")
                figure.write_html(str(sankey_directory / f"{name}.html"), include_plotlyjs="directory")
        for balance in self.failing:
            log.warning(
                f"Energy balance of {balance.component} ({balance.class_name}) does not close: residual "
                f"{balance.residual:.6g} kWh ({balance.residual_percent:.4g} % of {balance.throughput:.6g} kWh), "
                f"{int(balance.step_failures.sum())} steps beyond tolerance, worst step {balance.worst_step} "
                f"({self.step_time(balance.worst_step)})."
            )
        log.information(
            f"Energy balance: {len(self.balances) - len(self.failing)} of {len(self.balances)} declared components "
            f"close, {len(self.declared.undeclared)} undeclared; report in {report_path}."
        )
        if mode == BalanceMode.STRICT and self.failing:
            names = ", ".join(balance.component for balance in self.failing)
            raise EnergyBalanceError(f"The energy balance of {names} does not close; see {report_path}.")
        return report_path
