# importing networkx
import networkx as nx
# importing matplotlib.pyplot
import matplotlib.pyplot as plt

from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.component import ComponentInput
from hisim import log

class SystemChart:
    def __init__(self, ppdt: PostProcessingDataTransfer):
        self.ppdt = ppdt

    def make_chart(self):
        log.information("Making a chart.")
        component_nodenames = []
        graph = nx.MultiDiGraph()
        if len(self.ppdt.wrapped_components) == 0:
            raise ValueError("No components could be detected.")
        for component in self.ppdt.wrapped_components:
            if not component.my_component.component_name in component_nodenames:
                graph.add_node(component.my_component.component_name)
                component_nodenames.append(component.my_component.component_name)
        edge_label_dict = {}
        for component in self.ppdt.wrapped_components:
            input: ComponentInput
            for input in component.component_inputs:
                #if input.component_name is None:
                #    continue
                if input.src_object_name is None:
                    continue
                graph.add_edge(input.src_object_name, input.component_name)
                #graph.add_edges_from([],label=input.unit, weight=10)
                key = (input.component_name, input.src_object_name)
                if not key in edge_label_dict:
                    edge_label_dict[key] = input.source_output.field_name + " -> " + input.field_name + " in " + input.unit
                else:
                    old_entry: str = edge_label_dict[key]
                    edge_label_dict[key] = old_entry + "\n" + input.source_output.field_name + " -> " + input.field_name + " in " + input.unit

        pos = nx.spring_layout(graph, iterations=5)
        # plt.axis('off')
        fig, ax = plt.subplots(figsize=(15, 15))
        nx.draw(graph, pos, with_labels=True, connectionstyle='arc3, rad = 0.25')
        nx.draw_networkx_nodes(graph, pos, ax=ax)
        nx.draw_networkx_labels(graph, pos, ax=ax)
        nx.draw_networkx_edge_labels(
            graph, pos,
            edge_labels=edge_label_dict,
            font_color='red',
            alpha=0.5,
            font_size=8
        )

        fig.savefig("network_t1.png", bbox_inches='tight', pad_inches=0)