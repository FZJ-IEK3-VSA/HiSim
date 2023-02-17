""" Module for visualizing the entire system as a flow chart. """
# clean

import os
import pydot

from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer


class SystemChart:

    """Class for generating charts that show all the components."""

    def __init__(self, ppdt: PostProcessingDataTransfer) -> None:
        """Initizalizes the class."""
        self.ppdt: PostProcessingDataTransfer = ppdt

    def make_chart(self) -> None:
        """Makes different charts. Entry point for the class."""
        self.make_graphviz_chart(
            with_labels=False,
            with_class_names=True,
            filename="System_no_Edge_with_class_labels.png",
        )
        self.make_graphviz_chart(
            with_labels=False,
            with_class_names=False,
            filename="System_no_Edge_labels.png",
        )
        self.make_graphviz_chart(
            with_labels=True,
            with_class_names=False,
            filename="System_with_Edge_labels.png",
        )

    def make_graphviz_chart(
        self, with_labels: bool, with_class_names: bool, filename: str
    ) -> None:
        """Visualizes the entire system with graphviz."""
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
            if with_class_names:
                node_name = node_name + "\n" + component.my_component.__class__.__name__
            my_node = pydot.Node(node_name)
            node_dict[component.my_component.component_name] = my_node
            graph.add_node(my_node)
        edge_labels = {}
        for component in self.ppdt.wrapped_components:
            for component_input in component.component_inputs:
                if component_input.src_object_name is None:
                    continue
                node_a = node_dict[component_input.src_object_name]
                node_b = node_dict[component.my_component.component_name]
                key = (node_a, node_b)
                this_edge_label = (
                    str(component_input.src_field_name)
                    + " -> "
                    + component_input.field_name
                    + " in "
                    + component_input.unit
                )
                this_edge_label = this_edge_label.replace("°C", "&#8451;")
                if key not in edge_labels:
                    edge_labels[key] = this_edge_label
                else:
                    edge_labels[key] = edge_labels[key] + "\\n" + this_edge_label

        for node_key, label in edge_labels.items():
            if with_labels:
                graph.add_edge(pydot.Edge(node_key[0], node_key[1], label=label))
            else:
                graph.add_edge(pydot.Edge(node_key[0], node_key[1]))
        fullpath = os.path.join(
            self.ppdt.simulation_parameters.result_directory, filename
        )
        graph.write_png(fullpath)  # noqa: no-member
