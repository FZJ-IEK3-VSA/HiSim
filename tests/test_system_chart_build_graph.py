"""Unit tests for the pure graph-building core ``SystemChart._build_graph``.

``SystemChart.make_graphviz_chart`` interleaves the testable graph-assembly
logic with ``graph.write_png`` filesystem I/O and a live Graphviz install, so
it cannot be exercised under ``pytest -m base``. ``_build_graph`` was extracted
as a pure method that only reads ``self.ppdt`` and returns the assembled
:class:`pydot.Dot`, which these tests drive with a small stub
``PostProcessingDataTransfer`` and the real :class:`ComponentInput` /
:class:`ComponentOutput` types.
"""

# clean

from types import SimpleNamespace
from typing import Optional, cast

import pandas as pd
import pydot
import pytest

from hisim.component import ComponentInput, ComponentOutput
from hisim.loadtypes import LoadTypes, Units
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.system_chart import SystemChart


class _FakeComponent:
    """Minimal stand-in for a :class:`Component` (only needs a name and class)."""

    def __init__(self, component_name: str) -> None:
        self.component_name = component_name


class _FakeWrapper:
    """Minimal stand-in for a :class:`ComponentWrapper`."""

    def __init__(self, component: _FakeComponent, inputs) -> None:
        self.my_component = component
        self.component_inputs = list(inputs)


def _make_ppdt(wrapped_components, results_cumulative=None) -> PostProcessingDataTransfer:
    """Build a stub ``PostProcessingDataTransfer`` exposing only what ``_build_graph`` reads."""
    return cast(
        PostProcessingDataTransfer,
        SimpleNamespace(
            wrapped_components=wrapped_components,
            results_cumulative=results_cumulative,
        ),
    )


def _make_input(
    target_name: str,
    field_name: str,
    unit: Units,
    src_object_name: str,
    src_field_name: str,
    source_output: Optional[ComponentOutput] = None,
) -> ComponentInput:
    """Create a fully-connected :class:`ComponentInput` for the tests."""
    cinput = ComponentInput(
        object_name=target_name,
        field_name=field_name,
        load_type=LoadTypes.ANY,
        unit=unit,
        mandatory=False,
    )
    cinput.src_object_name = src_object_name
    cinput.src_field_name = src_field_name
    cinput.source_output = source_output
    return cinput


def _real_nodes(graph: pydot.Dot) -> list:
    """Return the graph nodes excluding pydot's implicit ``node`` default."""
    return [n for n in graph.get_nodes() if n.get_name() != "node"]


def _two_component_setup() -> PostProcessingDataTransfer:
    """A Source -> Controller wiring used by several tests."""
    source = _FakeComponent("Source")
    controller = _FakeComponent("Controller Thing")
    output = ComponentOutput("Source", "Out", LoadTypes.ANY, Units.WATT)
    controller_input = _make_input(
        target_name="Controller Thing",
        field_name="In",
        unit=Units.WATT,
        src_object_name="Source",
        src_field_name="Out",
        source_output=output,
    )
    return _make_ppdt(
        wrapped_components=[
            _FakeWrapper(source, []),
            _FakeWrapper(controller, [controller_input]),
        ]
    )


@pytest.mark.base
def test_build_graph_returns_pydot_digraph() -> None:
    """``_build_graph`` returns a ``pydot.Dot`` of graph_type digraph."""
    chart = SystemChart(_two_component_setup())
    graph = chart._build_graph(
        with_labels=False, with_class_names=False, with_results=False
    )
    assert isinstance(graph, pydot.Dot)
    assert graph.get_graph_type() == "digraph"


@pytest.mark.base
def test_build_graph_creates_one_node_per_component() -> None:
    """Each wrapped component becomes exactly one node, named by component_name."""
    chart = SystemChart(_two_component_setup())
    graph = chart._build_graph(
        with_labels=False, with_class_names=False, with_results=False
    )
    names = sorted(n.get_name() for n in _real_nodes(graph))
    assert names == ["Controller Thing", "Source"]


@pytest.mark.base
def test_build_graph_controller_fillcolor_selection() -> None:
    """Nodes whose name contains 'Controller' are lightgray, others darkgray."""
    chart = SystemChart(_two_component_setup())
    graph = chart._build_graph(
        with_labels=False, with_class_names=False, with_results=False
    )
    fillcolors = {n.get_name(): n.get("fillcolor") for n in _real_nodes(graph)}
    assert fillcolors["Source"] == "darkgray"
    assert fillcolors["Controller Thing"] == "lightgray"


@pytest.mark.base
def test_build_graph_with_class_names_appends_class() -> None:
    """``with_class_names`` appends the Python class name to each node name."""
    chart = SystemChart(_two_component_setup())
    graph = chart._build_graph(
        with_labels=False, with_class_names=True, with_results=False
    )
    names = {n.get_name() for n in _real_nodes(graph)}
    # The class name of the fake component is appended after a newline.
    assert "Source\n_FakeComponent" in names
    assert "Controller Thing\n_FakeComponent" in names


@pytest.mark.base
def test_build_graph_edge_without_label() -> None:
    """With ``with_labels=False`` an edge is added but carries no label."""
    chart = SystemChart(_two_component_setup())
    graph = chart._build_graph(
        with_labels=False, with_class_names=False, with_results=False
    )
    edges = graph.get_edges()
    assert len(edges) == 1
    assert edges[0].get_source() == "Source"
    assert edges[0].get_destination() == "Controller Thing"
    assert edges[0].get("label") in (None, "")


@pytest.mark.base
def test_build_graph_edge_label_format_and_celsius_replacement() -> None:
    """Edge labels combine src-field, target-field and unit, and replace °C with &#8451;."""
    controller = _FakeComponent("Controller Thing")
    boiler = _FakeComponent("Boiler")
    celsius_input = _make_input(
        target_name="Boiler",
        field_name="TemperatureIn",
        unit=Units.CELSIUS,
        src_object_name="Controller Thing",
        src_field_name="TemperatureOut",
    )
    ppdt = _make_ppdt(
        wrapped_components=[
            _FakeWrapper(controller, []),
            _FakeWrapper(boiler, [celsius_input]),
        ]
    )
    chart = SystemChart(ppdt)
    graph = chart._build_graph(
        with_labels=True, with_class_names=False, with_results=False
    )
    edges = graph.get_edges()
    assert len(edges) == 1
    label = edges[0].get("label")
    assert label is not None
    assert "TemperatureOut -> TemperatureIn in" in label
    # The °C unit must have been replaced by its HTML entity, not left as a literal.
    assert "°C" not in label
    assert "&#8451;" in label


@pytest.mark.base
def test_build_graph_input_without_source_is_skipped() -> None:
    """An input with ``src_object_name is None`` produces no edge."""
    controller = _FakeComponent("Controller")
    dangling_input = ComponentInput(
        object_name="Controller",
        field_name="In",
        load_type=LoadTypes.ANY,
        unit=Units.WATT,
        mandatory=False,
    )
    # src_object_name defaults to None on ComponentInput
    assert dangling_input.src_object_name is None
    ppdt = _make_ppdt(
        wrapped_components=[_FakeWrapper(controller, [dangling_input])]
    )
    chart = SystemChart(ppdt)
    graph = chart._build_graph(
        with_labels=True, with_class_names=False, with_results=False
    )
    assert graph.get_edges() == []
    # but the node itself is still present
    assert [n.get_name() for n in _real_nodes(graph)] == ["Controller"]


@pytest.mark.base
def test_build_graph_with_results_appends_cumulative_value() -> None:
    """``with_results`` appends the cumulative result from ``results_cumulative``."""
    ppdt = _two_component_setup()
    output = ppdt.wrapped_components[1].component_inputs[0].source_output
    assert output is not None
    pretty_name = output.get_pretty_name()
    ppdt.results_cumulative = pd.DataFrame([{pretty_name: 1234.5678}])

    chart = SystemChart(ppdt)
    graph = chart._build_graph(
        with_labels=True, with_class_names=False, with_results=True
    )
    label = graph.get_edges()[0].get("label")
    assert label is not None
    # value is rounded to 3 decimals
    assert ": 1234.568" in label


@pytest.mark.base
def test_build_graph_with_results_without_source_output_omits_value() -> None:
    """An input lacking a ComponentOutput source gets no cumulative value appended."""
    controller = _FakeComponent("Controller Thing")
    boiler = _FakeComponent("Boiler")
    input_no_output = _make_input(
        target_name="Boiler",
        field_name="In",
        unit=Units.WATT,
        src_object_name="Controller Thing",
        src_field_name="Out",
        source_output=None,
    )
    ppdt = _make_ppdt(
        wrapped_components=[
            _FakeWrapper(controller, []),
            _FakeWrapper(boiler, [input_no_output]),
        ],
        results_cumulative=pd.DataFrame(),
    )
    chart = SystemChart(ppdt)
    graph = chart._build_graph(
        with_labels=True, with_class_names=False, with_results=True
    )
    label = graph.get_edges()[0].get("label")
    assert label is not None
    # No ": <number>" cumulative suffix when there is no ComponentOutput.
    assert ": " not in label


@pytest.mark.base
def test_build_graph_merges_parallel_edges_with_newline() -> None:
    """Two inputs sharing the same source/target node pair share one edge label joined by \\n."""
    controller = _FakeComponent("Controller")
    boiler = _FakeComponent("Boiler")
    first = _make_input(
        target_name="Boiler",
        field_name="In1",
        unit=Units.WATT,
        src_object_name="Controller",
        src_field_name="Out1",
    )
    second = _make_input(
        target_name="Boiler",
        field_name="In2",
        unit=Units.WATT,
        src_object_name="Controller",
        src_field_name="Out2",
    )
    ppdt = _make_ppdt(
        wrapped_components=[
            _FakeWrapper(controller, []),
            _FakeWrapper(boiler, [first, second]),
        ]
    )
    chart = SystemChart(ppdt)
    graph = chart._build_graph(
        with_labels=True, with_class_names=False, with_results=False
    )
    edges = graph.get_edges()
    # A single edge for the shared node pair, with both labels joined by a literal \n.
    assert len(edges) == 1
    label = edges[0].get("label")
    assert label is not None
    assert "Out1 -> In1 in W" in label
    assert "Out2 -> In2 in W" in label
    assert "\\n" in label
