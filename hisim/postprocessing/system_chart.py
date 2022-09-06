""" Module for visualizing the entire system as a flow chart. """
# importing networkx
# import networkx as nx
# importing matplotlib.pyplot
import os
# from typing import List
# from dataclasses import dataclass, field
# import matplotlib.pyplot as plt
# from netgraph import Graph, InteractiveGraph, EditableGraph
import pydot


from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
# from hisim.component import ComponentInput
# from hisim import log


class SystemChart:

    """ Class for generating charts that show all the components. """

    def __init__(self, ppdt: PostProcessingDataTransfer) -> None:
        """ Initizalizes the class. """
        self.ppdt: PostProcessingDataTransfer = ppdt

    def make_chart(self) -> None:
        """ Makes different charts. Entry point for the class. """
        self.make_graphviz_chart(False, "System_no_Edge_labels.png")
        self.make_graphviz_chart(True, "System_with_Edge_labels.png")

    def make_graphviz_chart(self, with_labels: bool, filename: str) -> None:
        """ Visualizes the entire system with graphviz. """
        graph = pydot.Dot(graph_type='digraph')
        graph.set_node_defaults(color='lightgray', style='filled', shape='box', fontname='Arial', fontsize='10')
        node_dict = {}
        for component in self.ppdt.wrapped_components:
            my_node = pydot.Node(component.my_component.component_name)
            # my_node.set("label", "<B>" + component.my_component.component_name + "</B>")
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
                this_edge_label = str(component_input.src_field_name) + " -> " + component_input.field_name + " in " + component_input.unit
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
        fullpath = os.path.join(self.ppdt.simulation_parameters.result_directory, filename)
        graph.write_png(fullpath)


#     def make_chart_netgraph(self):
#         log.information("Making a chart.")
#
#         if len(self.ppdt.wrapped_components) == 0:
#             raise ValueError("No components could be detected.")
#
#         # set up component numbering dicts
#         node_list: List[NodeEntry] = []
#
#         for component in self.ppdt.wrapped_components:
#             ne = NodeEntry(component.my_component.component_name)
#             node_list.append(ne)
#             for input in component.component_inputs:
#                 if input.src_object_name is not None:
#                     ne.ConnectionsTo.append(input.src_object_name)
#         newlist: List[NodeEntry] = sorted(node_list, key=lambda x: len(x.ConnectionsTo), reverse=True)
#         # newlist: List[NodeEntry] = sorted(node_list, key=lambda x: x.Name, reverse=True)
#         print(newlist)
#         #return
#             #if not component.my_component.component_name in component_nodenames:
# #                component_nodenames[component.my_component.component_name] = idx
# #                node_labels[idx] = component.my_component.component_name
# #                idx = idx + 1
#
#         component_nodenames = {}
#         node_labels = {}
#         node: NodeEntry
#         idx = 0
#         for node in newlist:
#             component_nodenames[node.Name] = idx
#             node_labels[idx] = node.Name
#             idx = idx + 1
#         # set up edge dicts
#         all_edges_weight = {}
#         edge_labels = {}
#         for component in self.ppdt.wrapped_components:
#             input: ComponentInput
#             for input in component.component_inputs:
#                 # skip disconnected inputs
#                 if input.src_object_name is None:
#                     continue
#
#                 # clumsy due to mypy check
#                 source_field_name = ""
#                 if not input.source_output is None:
#                     source_field_name = input.source_output.field_name
#
#                 idx1 = component_nodenames[input.src_object_name]
#                 idx2 = component_nodenames[input.component_name]
#                 key = (idx1,idx2)
#                 if not key in all_edges_weight:
#                     all_edges_weight[key] = 0.5
#                 else:
#                     all_edges_weight[key] = all_edges_weight[key] + 0.5
#                 this_edge_label =   source_field_name + " -> " + input.field_name + " in " + input.unit
#                 if not key in edge_labels:
#                     edge_labels[key] = this_edge_label
#                 else:
#                     edge_labels[key] = edge_labels[key] + "\n" + this_edge_label
#         all_edges = []
#         for dictkey, val in all_edges_weight.items():
#             newkey= (dictkey[0], dictkey[1], val)
#             all_edges.append(newkey)
#         fig, ax = plt.subplots(figsize=(15, 15))
#         cmap = 'Spectral'
#         node_label_font_dict =dict(size=12, fontfamily='Arial', alpha=0.5)
#         edge_label_dict = dict(size=8, fontfamily='Arial', alpha=0.5,
#                                bbox=dict(alpha=0.5))
#         # bbox=dict(boxstyle='round',
#         ec=(1.0, 0.0, 0.0),                                                         fc=(0.5, 0.5, 1.0)))
#         graph = Graph(all_edges, ax=ax, node_layout='circular',
#                       arrows=True,
#                       edge_layout='curved', node_labels=node_labels,
#                       node_label_fontdict=node_label_font_dict,
#                        edge_labels=edge_labels,
#                       edge_cmap=cmap,
#                       edge_width=2.,
#                       edge_label_fontdict=edge_label_dict
#
#                     )
#         fig.canvas.draw()
#         fullpath = os.path.join(self.ppdt.simulation_parameters.result_directory, "system_chart.png")
#         fig.savefig(fullpath, bbox_inches='tight', pad_inches=0, dpi=300)
#
#     def make_chart_networkx(self):
#         log.information("Making a chart.")
#         component_nodenames = []
#         graph = nx.MultiDiGraph()
#         if len(self.ppdt.wrapped_components) == 0:
#             raise ValueError("No components could be detected.")
#         for component in self.ppdt.wrapped_components:
#             if not component.my_component.component_name in component_nodenames:
#                 graph.add_node(component.my_component.component_name)
#                 component_nodenames.append(component.my_component.component_name)
#         edge_label_dict = {}
#         for component in self.ppdt.wrapped_components:
#             input: ComponentInput
#             for input in component.component_inputs:
#                 #if input.component_name is None:
#                 #    continue
#                 if input.src_object_name is None:
#                     continue
#
#                 # clumsy due to mypy check
#                 source_field_name = ""
#                 if not input.source_output is None:
#                     source_field_name = input.source_output.field_name
#
#                 graph.add_edge(input.src_object_name, input.component_name)
#                 #graph.add_edges_from([],label=input.unit, weight=10)
#                 key = (input.component_name, input.src_object_name)
#                 if not key in edge_label_dict:
#                     edge_label_dict[key] = source_field_name + " -> " + input.field_name + " in " + input.unit
#                 else:
#                     old_entry: str = edge_label_dict[key]
#                     edge_label_dict[key] = old_entry + "\n" + source_field_name + " -> " + input.field_name + " in " + input.unit
#
#         pos = nx.spring_layout(graph, iterations=5)
#         # plt.axis('off')
#         fig, ax = plt.subplots(figsize=(40, 40), dpi=300)
#         nx.draw(graph, pos, with_labels=True, connectionstyle='arc3, rad = 0.25')
#         nx.draw_networkx_nodes(graph, pos, ax=ax)
#         nx.draw_networkx_labels(graph, pos, ax=ax)
#         nx.draw_networkx_edge_labels(
#             graph, pos,
#             edge_labels=edge_label_dict,
#             font_color='red',
#             alpha=0.5,
#             font_size=8
#         )
#
#         fig.savefig("network_t1.png", bbox_inches='tight', pad_inches=0, dpi=300)
