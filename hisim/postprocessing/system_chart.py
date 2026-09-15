"""Module for visualizing the entire system as a flow chart."""

# clean

from collections import defaultdict
from pathlib import Path
from typing import List
from typing import Optional
from typing import TypedDict

import pydot

from hisim import log
from hisim.component import ComponentInput, ComponentOutput
from hisim.loadtypes import UNITS_USING_MEAN_AGGREGATION, Units
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.report_image_entries import SystemChartEntry


def _cumulative_unit_for_sum(unit: Units) -> str:
    """Derive the unit label for a cumulative *sum* from the source output's unit.

    A sum over timesteps of a "per timestep" quantity yields a total in the
    base unit (e.g. ``"kWh per timestep"`` -> ``"kWh"``).  Extensive
    quantities (energy, volume, mass, cost, time) keep their unit when
    summed, so their unit string is returned unchanged.

    Args:
        unit: The :class:`Units` member of the source output whose values
            were summed.

    Returns:
        The unit string to display next to the cumulative sum value.
    """
    text = unit.value
    suffix = " per timestep"
    if text.endswith(suffix):
        return text[: -len(suffix)]
    return text


class _ChartSpecRequired(TypedDict):
    """Required keyword arguments for one :meth:`SystemChart.make_graphviz_chart` variant."""

    with_labels: bool
    with_class_names: bool
    filename: str
    caption: str


class _ChartSpec(_ChartSpecRequired, total=False):
    """A chart variant spec, adding the optional ``with_results`` flag."""

    with_results: bool


class SystemChart:
    """Class for generating charts that show all the components."""

    def __init__(self, ppdt: PostProcessingDataTransfer) -> None:
        """Initializes the SystemChart.

        Args:
            ppdt: PostProcessingDataTransfer object containing simulation
                results, wrapped components, and parameters needed to build
                the system flow chart.
        """
        self.ppdt: PostProcessingDataTransfer = ppdt

    def make_chart(self) -> List[SystemChartEntry]:  # type: ignore
        """Generate four variants of the system flow chart and return their entries.

        Produces four Graphviz-rendered PNG charts of the simulated system:
        without edge labels (with and without class names), with edge labels,
        and with edge labels plus cumulative result values. Each successfully
        generated chart is appended to the returned list; failed charts are
        skipped.

        Returns:
            A list of :class:`SystemChartEntry` objects, one per successfully
            generated chart variant.
        """
        fig_format = self.ppdt.simulation_parameters.figure_format
        chart_specs: list[_ChartSpec] = [
            {
                "with_labels": False,
                "with_class_names": True,
                "filename": f"System_no_Edge_with_class_labels{fig_format}",
                "caption": "System Chart of all components",
            },
            {
                "with_labels": False,
                "with_class_names": False,
                "filename": f"System_no_Edge_labels{fig_format}",
                "caption": "System Chart of all components and all outputs.",
            },
            {
                "with_labels": True,
                "with_class_names": False,
                "filename": f"System_with_Edge_labels{fig_format}",
                "caption": "System Chart with labels on all edges.",
            },
            {
                "with_labels": True,
                "with_class_names": False,
                "filename": f"System_with_Edge_labels_and_results{fig_format}",
                "caption": "System Chart with labels and cumulative result values on all edges.",
                "with_results": True,
            },
        ]
        files: List[SystemChartEntry] = []
        for spec in chart_specs:
            entry = self.make_graphviz_chart(**spec)
            if entry is not None:
                files.append(entry)
        return files

    def make_graphviz_chart(
        self,
        with_labels: bool,
        with_class_names: bool,
        filename: str,
        caption: str,
        with_results: bool = False,
    ) -> Optional[SystemChartEntry]:
        """Render a single system flow chart to a PNG file using Graphviz.

        Builds a directed graph (via :meth:`_build_graph`) where each wrapped
        component is a node and each input connection is an edge, optionally
        annotating edges with labels and cumulative result values, then writes
        the rendered PNG to the simulation result directory.

        Args:
            with_labels: If True, edges are annotated with input/field/unit
                (and optionally result) text.
            with_class_names: If True, appends the component's Python class
                name to each node label.
            filename: Base filename (including extension) for the output PNG,
                written under the simulation result directory.
            caption: Human-readable caption stored in the returned entry.
            with_results: If True, appends the cumulative result value to each
                edge label (requires ``with_labels`` for visible effect).

        Returns:
            A :class:`SystemChartEntry` for the generated chart, or ``None`` if
            chart generation failed (e.g., Graphviz is not installed on the
            system). Failures are logged and swallowed rather than raised.
        """

        system_chart_entry: Optional[SystemChartEntry] = SystemChartEntry(filename, caption)

        try:
            graph = self._build_graph(
                with_labels=with_labels,
                with_class_names=with_class_names,
                with_results=with_results,
            )
            fullpath = Path(self.ppdt.simulation_parameters.result_directory) / filename
            # pydot generates the write_<format> methods at runtime, so neither pylint nor mypy sees them.
            graph.write_png(fullpath)  # type: ignore[attr-defined]  # noqa: no-member
        except Exception as exc:  # noqa
            log.error(
                f"Failed to generate network charts. Probably Graphviz is missing on your system. The python error was: {exc}"
            )
            system_chart_entry = None
        return system_chart_entry

    def _build_graph(
        self,
        with_labels: bool,
        with_class_names: bool,
        with_results: bool,
    ) -> pydot.Dot:
        """Assemble the system flow-chart digraph from ``self.ppdt`` without writing to disk.

        This is the pure, testable core of :meth:`make_graphviz_chart`: it only
        reads ``self.ppdt`` (the wrapped components and the cumulative results)
        and returns the assembled :class:`pydot.Dot` graph. No filesystem I/O
        is performed and no Graphviz binary is invoked, so the node/edge
        construction logic can be exercised with a stub
        :class:`PostProcessingDataTransfer`.

        Each wrapped component becomes a node (controllers, detected by
        ``"Controller" in node_name``, get a ``lightgray`` fill, other
        components ``darkgray``). Each input connection whose source object is
        set becomes an edge; its label combines the source field, target field
        and unit, with the ``°C`` unit replaced by its HTML entity
        (``&#8451;``). When ``with_results`` is set, the cumulative result
        value for the connected output is appended to the label, annotated
        with its own derived unit and the aggregation kind (``mean`` or
        ``sum``) so that the number is not mistaken for the input field's
        instantaneous value: a mean keeps the source output's unit, while a
        sum is a cumulative total (e.g. ``"kWh per timestep"`` sums to
        ``"kWh"``).

        Args:
            with_labels: If True, edges carry the constructed label text;
                otherwise edges are added unlabelled.
            with_class_names: If True, appends the component's Python class
                name to each node label.
            with_results: If True, appends the cumulative result value (looked
                up via ``self.ppdt.results_cumulative.at[0, ...]``) to each
                edge label for inputs backed by a :class:`ComponentOutput`.

        Returns:
            The assembled :class:`pydot.Dot` digraph.
        """
        graph = pydot.Dot(graph_type="digraph")
        graph.set_node_defaults(
            color="lightgray",
            style="filled",
            shape="box",
            fontname="Arial",
            fontsize="10",
        )
        node_dict = {}
        for component in self.ppdt.wrapped_components:
            node_name = component.my_component.component_name

            # for non-physical components (controller) use darker color
            if "Controller" in node_name:
                fillcolor = "lightgray"
            else:
                fillcolor = "darkgray"

            if with_class_names:
                node_name = f"{node_name}\n{component.my_component.__class__.__name__}"
            my_node = pydot.Node(node_name, fillcolor=fillcolor)
            node_dict[component.my_component.component_name] = my_node
            graph.add_node(my_node)
        edge_labels: dict[tuple[pydot.Node, pydot.Node], list[str]] = defaultdict(list)
        for component in self.ppdt.wrapped_components:
            for component_input in component.component_inputs:
                if component_input.src_object_name is None:
                    continue
                node_a = node_dict[component_input.src_object_name]
                node_b = node_dict[component.my_component.component_name]
                key = (node_a, node_b)
                this_edge_label = self._format_edge_label(component_input, with_results)
                edge_labels[key].append(this_edge_label)

        for (node_a, node_b), labels in edge_labels.items():
            label = "\\n".join(labels)
            if with_labels:
                graph.add_edge(pydot.Edge(node_a, node_b, label=label))
            else:
                graph.add_edge(pydot.Edge(node_a, node_b))
        return graph

    def _format_edge_label(
        self,
        component_input: ComponentInput,
        with_results: bool,
    ) -> str:
        """Build the full edge-label text for one input connection.

        Assembles the ``"<src_field> -> <field> in <unit>"`` base label from the
        input connection, optionally appends the cumulative result annotation
        (when ``with_results`` is set and the input is backed by a
        :class:`ComponentOutput`), and finally replaces the literal ``°C``
        unit with its HTML entity (``&#8451;``) so Graphviz renders it correctly.

        Args:
            component_input: The input connection whose label is being built.
            with_results: If True, append the cumulative result annotation for
                inputs backed by a :class:`ComponentOutput`.

        Returns:
            The complete edge-label string.
        """
        label = (
            f"{component_input.src_field_name} -> {component_input.field_name}"
            f" in {component_input.unit.value}"
        )
        if with_results:
            annotation = self._compute_result_annotation(component_input.source_output)
            if annotation is not None:
                label += annotation
        return label.replace("°C", "&#8451;")

    def _compute_result_annotation(
        self,
        source_output: Optional[ComponentOutput],
    ) -> Optional[str]:
        """Build the cumulative result ``": value unit (kind)"`` suffix for an edge.

        Looks up the cumulative result value for ``source_output`` in
        ``self.ppdt.results_cumulative``, determines whether the value is a
        *mean* (for rate-like units such as W, °C) or a *sum* (for extensive
        units such as Wh, L, kg, Euro) of the source output over all timesteps,
        derives the unit to display next to the aggregated value, and returns
        the annotation suffix.

        A mean keeps the source output's unit, but a sum is a cumulative total
        whose unit may differ from the input field's unit (e.g.
        ``"kWh per timestep"`` sums to ``"kWh"``). The ``"in <unit>"`` clause of
        the edge label describes the input field, not this aggregated value, so
        the value is annotated with its own derived unit and the aggregation
        kind (mean/sum) to avoid a unit-label mismatch.

        Args:
            source_output: The :class:`ComponentOutput` whose cumulative result
                is annotated. ``None`` (or any non-:class:`ComponentOutput`)
                yields no annotation.

        Returns:
            The ``": <value> <unit> (<kind>)"`` suffix, or ``None`` when
            ``source_output`` is not a :class:`ComponentOutput` and there is no
            cumulative value to annotate.
        """
        if not isinstance(source_output, ComponentOutput):
            return None
        assert self.ppdt.results_cumulative is not None
        # The unit is runtime-determined from source_output.unit and tracked
        # separately in cumulative_unit; shown alongside the value in the label.
        output_cumulative_result_value = self.ppdt.results_cumulative.at[
            0, source_output.get_pretty_name()
        ]
        output_unit = source_output.unit
        if output_unit in UNITS_USING_MEAN_AGGREGATION:
            cumulative_unit = output_unit.value
            aggregation_kind = "mean"
        else:
            cumulative_unit = _cumulative_unit_for_sum(output_unit)
            aggregation_kind = "sum"
        return (
            f": {round(output_cumulative_result_value, 3)} {cumulative_unit}"
            f" ({aggregation_kind})"
        )
