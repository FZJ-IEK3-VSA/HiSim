"""Computes a finished run's energy flows, and writes its report and Sankeys.

The balance is computed in every simulation (owner, 2026-10-02: always fail hard), and the setup rules after the run
judge it before anything else of the post-processing runs (``hisim/setup_rules/results.py`` and
``hisim/setup_rules/meters.py``): they fail the run with :class:`~hisim.setup_rules.results.EnergyBalanceError` when a
declared port is NaN or infinite, when a component's balance does not close, when the two sides of a transfer
disagree beyond the tolerance, when only one of two declared components books a transfer between them, when a fluid
loop breaks the rules of :mod:`hisim.energy_port` (two components declaring an end on one flow, an end without its
match, a loop pumping water the balance cannot see; :attr:`~.ports.DeclaredPorts.loop_breaches`), when the supplier
of a loop that may not cool removes heat from its water beyond the noise
(:attr:`~.ports.DeclaredPorts.cooling_on_heating_only_loops`), or, in a run
without undeclared components, when a meter publishes other than it reads or a metered port passes no meter
(:mod:`.meters`); the violation names every one of them. A run in which no component declares a port passes.

:class:`~hisim.postprocessingoptions.PostProcessingOptions` ``EXPORT_ENERGY_BALANCE`` only decides whether the
files are written: at the end of the post-processing, or, for a run whose balance the rules refuse, before the
refusal propagates (:meth:`~hisim.postprocessing.postprocessing_main.PostProcessor.judge_the_results`).
``balance_report.json`` (schema ``hisim.energy_balance/5``) lists, per
declared component, the run's consumption / production / loss / storage change in kWh (in total and per carrier), the residual in kWh
and in percent of the throughput, the worst step and the verdict; then every link between declared components (a
side that books nothing is null, and a failing link states its reason; a fluid loop is a link from its supply
owner to its receiver, and each end's port lists the loop's three outputs; an input's port names the output it
reads); every breach of the loop rules;
every meter with what it publishes, the ports it reads and whether the two agree; every unmetered port; every
unlinked port; every undeclared component; and every exempt component with its reason. Beside it,
``energy_sankeys/`` holds ``energy_sankey.json`` (every view's nodes and links: ``overall``, the connected graph, and
one filter per carrier) and one plotly HTML per view, which share one ``plotly.min.js``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Sequence

import pandas as pd

from hisim import log
from hisim.component import ComponentOutput
from hisim.postprocessing.energy_balance.check import (
    BalanceCheck,
    ComponentBalance,
    LinkBalance,
    NonFinitePort,
    Tolerance,
)
from hisim.postprocessing.energy_balance.meters import MeterCheck
from hisim.postprocessing.energy_balance.ports import DeclaredPorts
from hisim.postprocessing.energy_balance.sankey import SankeyBuilder, SankeyDiagram


class EnergyBalanceReport:
    """Computes the energy balance of one finished run and writes its report and Sankeys on request.

    The setup rules after the run read it and judge it (``hisim/setup_rules/results.py``,
    ``hisim/setup_rules/meters.py``); :attr:`fails` and :attr:`verdict` say in the report what they refuse.
    """

    #: The report's file name in the result directory.
    REPORT_FILE: ClassVar[str] = "balance_report.json"
    #: The directory of the Sankeys in the result directory.
    SANKEY_DIRECTORY: ClassVar[str] = "energy_sankeys"
    #: The file of every view's nodes and links in the Sankey directory.
    SANKEY_JSON_FILE: ClassVar[str] = "energy_sankey.json"
    #: The report's schema: 2 adds the meters, the unmetered and unlinked ports and the exempt components; 3 renames
    #: the roles ``in`` and ``out`` to ``consumption`` and ``production``, and the totals ``in_kwh`` and ``out_kwh`` to
    #: ``consumption_kwh`` and ``production_kwh``; 4 drops each port's ``peer_input`` and ``peer_output`` and adds
    #: ``reads``, the output an input port reads (decision D9 of ``roadmap/fluid_loops_spec.md``).
    REPORT_SCHEMA: ClassVar[str] = "hisim.energy_balance/5"

    def __init__(
        self,
        results: pd.DataFrame,
        all_outputs: Sequence[ComponentOutput],
        components: Sequence[Any],
        seconds_per_timestep: float,
        tolerance: Tolerance = Tolerance(),
    ) -> None:
        """Collect the declared ports of the run and compute every declared component's balance, every link and every meter.

        Args:
            results: The result table, one column per output and one row per step.
            all_outputs: The run's outputs, in the order of the table's columns.
            components: The components of the run, in the order they were added, with their inputs connected.
            seconds_per_timestep: The length of one step.
            tolerance: When a balance, a link or a meter counts as closed.

        Raises:
            ValueError: As :meth:`~.ports.DeclaredPorts.collect` raises it.
        """
        self.seconds_per_timestep = seconds_per_timestep
        self.tolerance = tolerance
        self.declared = DeclaredPorts.collect(results, all_outputs, components, seconds_per_timestep)
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
        """``does_not_close`` whenever the run fails, else ``nothing_declared`` or ``closes``.

        A run in which no component declares a port can still fail, by an enforced meter or an unmetered port, and
        the report then says so rather than "nothing_declared".
        """
        if self.fails:
            return "does_not_close"
        return "nothing_declared" if not self.balances else "closes"

    @property
    def fails(self) -> bool:
        """Whether a balance, a link, a loop rule, an enforced meter balance or the enforced metering rule fails, as the report states it."""
        return bool(
            self.failing
            or self.failing_links
            or self.declared.loop_faults
            or self.meter_check.failing
            or self.meter_check.failing_unmetered
        )

    def step_time(self, step: Optional[int]) -> Optional[str]:
        """The time stamp of a step, as the result table indexes it (:meth:`~.ports.DeclaredPorts.step_time`)."""
        return None if step is None else self.declared.step_time(step)

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
            "loop_faults": list(self.declared.loop_faults),
            "meters": [meter.as_dict(self.step_time) for meter in self.meter_check.balances],
            "unmetered": self.meter_check.unmetered_entry(),
            "unlinked": [
                {
                    "component": series.component,
                    "output": series.port_name,
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
        builder = SankeyBuilder(self.declared, self.balances, self.meter_check.balances, self.links)
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
        """Write the report and the Sankeys when asked, and log what the balance found; this never fails the run.

        The setup rules after the run have judged the balance before (``hisim/setup_rules/results.py``,
        ``hisim/setup_rules/meters.py``); a run that reaches this passed them.

        Args:
            result_directory: Where to write the report and the Sankeys; None writes nothing.
            with_html: Whether the Sankeys are also written as HTML.

        Returns:
            The path of ``balance_report.json`` when it was written.
        """
        report_path = None if result_directory is None else self.write(result_directory, with_html)
        meters = self.meter_check.balances
        log.information(
            f"Energy balance: {len(self.balances) - len(self.failing)} of {len(self.balances)} declared components "
            f"close, {len(self.links) - len(self.failing_links)} of {len(self.links)} links between them close, "
            f"{len(self.declared.loop_breaches)} loop faults, "
            f"{sum(meter.agrees for meter in meters)} of {len(meters)} meters publish what they read, "
            f"{len(self.meter_check.unmetered)} ports unmetered, "
            f"{len(self.declared.unlinked())} ports unlinked, {len(self.declared.undeclared)} components undeclared, "
            f"{len(self.declared.exempt)} exempt"
            + ("." if report_path is None else f"; report in {report_path}.")
        )
        return report_path
