"""The values written on the Sankey figure: flow values, node metrics and the caption's key results.

Every value is computed from the drawn flows, the KPIs and the realized configuration; a metric whose input the
run lacks is left out, never estimated. Values over the run are scaled to a year by 8,760 h over the run's hours
where the metric is per year (kWh/kWp, kWh/(m²a)).
"""

from __future__ import annotations

from typing import ClassVar, List, Optional, Tuple

from hisim import loadtypes as lt
from hisim.postprocessing.energy_balance.figure.graph import DrawnGraph, DrawnNode, FlowKind, GraphBuilder, Role
from hisim.postprocessing.energy_balance.figure.inputs import SankeyInputs


class Metrics:
    """Formats energies and computes the metric lines of the drawn nodes."""

    #: The hours of a year, for scaling a run's values to a year.
    HOURS_PER_YEAR: ClassVar[float] = 8760.0
    #: Energies from this many kWh up are written in MWh.
    MWH_FROM_KWH: ClassVar[float] = 10_000.0
    #: An electric heater's efficiency is 100 % by construction; from this many percent up it is not written.
    TRIVIAL_EFFICIENCY_PERCENT: ClassVar[float] = 99.95
    #: The KPI name prefix of the conditioned floor area, in m².
    FLOOR_AREA_KPI: ClassVar[str] = "Conditioned floor area"

    @staticmethod
    def format_kwh(value_kwh: float) -> str:
        """Return an energy for a label: MWh with one decimal from 10 MWh, whole kWh from 10 kWh, else one decimal.

        Thousands get separators and the sign is dropped. Example: 26,315.1 -> "26.3 MWh", 1,805.1 -> "1,805 kWh",
        -1.84 -> "1.8 kWh".
        """
        magnitude = abs(value_kwh)
        if magnitude >= Metrics.MWH_FROM_KWH:
            return f"{magnitude / 1000:,.1f} MWh"
        if magnitude >= 10:
            return f"{magnitude:,.0f} kWh"
        return f"{magnitude:,.1f} kWh"

    @staticmethod
    def ratio(numerator: float, denominator: float) -> Optional[float]:
        """Return numerator / denominator, or None when the denominator is not positive (the metric is left out)."""
        if denominator <= 0:
            return None
        return numerator / denominator

    @staticmethod
    def per_year(value: float, run_hours: float) -> float:
        """Return a run's value scaled to a year: value x 8,760 h / run hours (a week's 100 kWh -> 5,214 kWh/a).

        Raises:
            ValueError: If the run hours are not positive.
        """
        if run_hours <= 0:
            raise ValueError(f"a run must last a positive number of hours, not {run_hours}")
        return value * Metrics.HOURS_PER_YEAR / run_hours

    @staticmethod
    def sums(node: DrawnNode) -> Tuple[float, float, float, float, float]:
        """Return a node's heat out to drawn nodes, electricity in, ambient heat in, solar in and other non-heat input, in kWh."""
        carrier = lt.EnergyBalanceCarrier
        heat_out = -sum(f.value_kwh for f in node.flows_where(is_input=False, kind=FlowKind.LINK) if f.carrier in GraphBuilder.HEAT_CARRIERS)
        inputs = node.flows_where(is_input=True)
        electricity = sum(f.value_kwh for f in inputs if f.carrier == carrier.ELECTRICITY.value)
        ambient = sum(f.value_kwh for f in inputs if f.carrier == carrier.AMBIENT_HEAT.value)
        solar = sum(f.value_kwh for f in inputs if f.carrier == carrier.SOLAR.value)
        other = sum(
            f.value_kwh
            for f in inputs
            if f.carrier not in GraphBuilder.HEAT_CARRIERS
            and f.carrier not in (carrier.ELECTRICITY.value, carrier.AMBIENT_HEAT.value, carrier.SOLAR.value)
            and f.kind is not FlowKind.STORAGE
        )
        return heat_out, electricity, ambient, solar, other

    @staticmethod
    def node_lines(graph: DrawnGraph, node: DrawnNode, inputs: SankeyInputs) -> List[str]:
        """Return the metric lines under a node's title, by its role; empty when the role has none or inputs lack.

        Example: a heat pump with 14,644 kWh of heat out from 3,780 kWh of electricity and 11,864 kWh of ambient heat
        gives ["SPF 3.87 · ambient 81 %"].
        """
        role = graph.roles[node.name]
        if role is Role.PV:
            return Metrics.pv_lines(node, inputs)
        if role is Role.BUILDING:
            return Metrics.building_lines(node, inputs)
        line = {
            Role.GENERATOR: Metrics.generator_line,
            Role.SOLAR_COLLECTOR: Metrics.collector_line,
            Role.ELECTRIC_STORAGE: Metrics.storage_line,
            Role.COOLER: Metrics.cooler_line,
        }.get(role, lambda _: None)(node)
        return [line] if line else []

    @staticmethod
    def pv_lines(node: DrawnNode, inputs: SankeyInputs) -> List[str]:
        """Return a PV's peak power and its specific yield per year, or nothing without a realized peak power."""
        peak_in_watt = inputs.peak_power_in_watt.get(node.name)
        if not peak_in_watt:
            return []
        produced = -sum(f.value_kwh for f in node.flows_where(is_input=False, kind=FlowKind.LINK))
        specific = Metrics.per_year(produced, inputs.run_hours) / (peak_in_watt / 1000)
        return [f"{peak_in_watt / 1000:.1f} kWp", f"{specific:,.0f} kWh/kWp·a"]

    @staticmethod
    def building_lines(node: DrawnNode, inputs: SankeyInputs) -> List[str]:
        """Return a building's space heating per conditioned floor area and year, or nothing without the floor area."""
        area = inputs.kpi(Metrics.FLOOR_AREA_KPI)
        heat = sum(f.value_kwh for f in node.flows_where(is_input=True, kind=FlowKind.LINK, carrier=lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT.value))
        if not area or heat <= 0:
            return []
        return [f"heat to the rooms {Metrics.per_year(heat, inputs.run_hours) / area:.0f} kWh/(m²·a)"]

    @staticmethod
    def generator_line(node: DrawnNode) -> Optional[str]:
        """Return a heat pump's SPF and ambient share, or another generator's efficiency (none when trivially 100 %)."""
        heat_out, electricity, ambient, _, other = Metrics.sums(node)
        if ambient > 0 and electricity > 0:
            spf, share = Metrics.ratio(heat_out, electricity), Metrics.ratio(ambient, heat_out)
            return f"SPF {spf:.2f} · ambient {share * 100:.0f} %" if spf is not None and share is not None else None
        efficiency = Metrics.ratio(heat_out, electricity + other)
        if efficiency is None or (other == 0 and efficiency * 100 >= Metrics.TRIVIAL_EFFICIENCY_PERCENT):
            return None
        return f"efficiency {efficiency * 100:.1f} %"

    @staticmethod
    def collector_line(node: DrawnNode) -> Optional[str]:
        """Return a solar collector's efficiency: heat out over sunlight in."""
        heat_out, _, _, solar, _ = Metrics.sums(node)
        efficiency = Metrics.ratio(heat_out, solar)
        return f"collector efficiency {efficiency * 100:.0f} %" if efficiency is not None else None

    @staticmethod
    def storage_line(node: DrawnNode) -> Optional[str]:
        """Return a battery's AC round trip: discharge over charge plus the storage it drew down (less what it stored).

        Over a week a battery may give back more than it took; without the storage change the round trip could read
        194 %. Example: 16 kWh discharged from 8.3 kWh charged and 11 kWh drawn from storage is 83 %.
        """
        charge = sum(f.value_kwh for f in node.flows_where(is_input=True, kind=FlowKind.LINK))
        discharge = -sum(f.value_kwh for f in node.flows_where(is_input=False, kind=FlowKind.LINK))
        stored = sum(f.value_kwh for f in node.flows if f.kind is FlowKind.STORAGE)
        round_trip = Metrics.ratio(discharge, charge + stored)
        return f"round trip {round_trip * 100:.0f} %" if round_trip is not None else None

    @staticmethod
    def cooler_line(node: DrawnNode) -> Optional[str]:
        """Return a cooler's energy efficiency ratio: heat removed from the building over electricity in."""
        _, electricity, _, _, _ = Metrics.sums(node)
        removed = sum(f.value_kwh for f in node.flows_where(is_input=True, kind=FlowKind.LINK, carrier=lt.EnergyBalanceCarrier.COOLING.value))
        eer = Metrics.ratio(removed, electricity)
        return f"EER {eer:.1f}" if eer is not None else None

    @staticmethod
    def results_line(graph: DrawnGraph, inputs: SankeyInputs) -> str:
        """Return the caption's line of key results: grid exchange, self-consumption and self-sufficiency, heat delivered.

        A KPI the run lacks is left out; when the result directory holds no ``all_kpis.json`` at all, the line says so
        instead of silently showing only the heat (a run with ``PLOT_SANKEY`` always writes the file; a directory
        redrawn from an older run may lack it). The heat to the rooms is the space-heating heat the buildings receive,
        the heat removed the cooling the buildings give off, both from the drawn flows.
        """
        parts = [] if inputs.kpis else ["grid exchange and self-consumption unavailable: the result directory has no all_kpis.json"]
        for prefix, label in (("Total energy from grid", "grid import"), ("Total energy to grid", "grid export")):
            value = inputs.kpi(prefix)
            if value is not None:
                parts.append(f"{label} {Metrics.format_kwh(value)}")
        for prefix, label in (("Self-consumption rate of electricity", "self-consumption"), ("Self-sufficiency rate of electricity", "self-sufficiency")):
            value = inputs.kpi(prefix)
            if value is not None:
                parts.append(f"{label} {value:.1f} %")
        heat = cooling = 0.0
        for node in graph.nodes:
            if graph.roles[node.name] is Role.BUILDING:
                heat += sum(f.value_kwh for f in node.flows_where(is_input=True, kind=FlowKind.LINK, carrier=lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT.value))
                cooling -= sum(f.value_kwh for f in node.flows_where(is_input=False, kind=FlowKind.LINK, carrier=lt.EnergyBalanceCarrier.COOLING.value))
        if heat > 0:
            parts.append(f"heat delivered to the rooms {Metrics.format_kwh(heat)}")
        if cooling > 0:
            parts.append(f"heat removed by cooling {Metrics.format_kwh(cooling)}")
        return "Run results: " + " · ".join(parts) + "." if parts else ""
