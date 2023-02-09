""" Module for visualizing selected methods as a flow chart. """

from functools import wraps
import pydot
import time
from collections import defaultdict
import inspect
import seaborn as sns
from hisim import log

def graph_call_path_factory(node_container):
    def register_method(func):
        @wraps(func)
        def function_wrapper_for_node_storage(*args, **kwargs):
            curr_frame = inspect.currentframe()
            node_name = curr_frame.f_locals['func'].__qualname__
            if node_name not in node_container.wrapped_method_nodes:
                node_class = curr_frame.f_locals['func'].__qualname__.replace('.' + node_name,'')
                node_container.wrapped_method_nodes[node_name] = node_name
                node_container.node_class_map[node_name] = node_class
                node_container.wrapped_method_counter[node_name] = 0
                node_container.wrapped_method_timer[node_name] = 0
                src_function = inspect.stack()[1][3]
                frame = inspect.stack()[1][0].f_locals         
                try:
                    try:
                        src_class = type(frame['self']).__qualname__
                    except KeyError:
                        src_class = None
                finally:
                    del frame
                node_container.wrapped_method_src[node_name] = src_function
                node_container.src_class_map[node_name] = src_class
            start_time = time.perf_counter()
            result = func(*args, **kwargs)
            end_time = time.perf_counter()
            total_time = end_time - start_time
            node_container.wrapped_method_counter[node_name] += 1
            node_container.wrapped_method_timer[node_name] += total_time
            del curr_frame
            return result
        return function_wrapper_for_node_storage
    return register_method

class MethodChart:
    """ Class for generating charts that show the components. """

    def __init__(self):
        """ Initizalizes the class. """
        self.wrapped_method_nodes = defaultdict(list)
        self.wrapped_method_counter = defaultdict(int)
        self.wrapped_method_timer = defaultdict(float)
        self.wrapped_method_src = defaultdict(list)
        self.node_class_map = defaultdict(list)
        self.src_class_map = defaultdict(list)

    def set_color_scheme(self):
        max_value = max(self.wrapped_method_counter.values())
        palette = sns.color_palette("light:#5A9", max_value + 1).as_hex()
        self.color_palette = {method: palette[self.wrapped_method_counter[method]] for method in self.wrapped_method_counter}

    def make_graphviz_chart(self, with_labels: bool, time_resolution: int, filename: str) -> None:
        """ Visualizes the entire system with graphviz. """
        graph = pydot.Dot(graph_type='digraph')
        graph.set_node_defaults(color='lightgrey', style='filled', shape='box', fontname='Arial', fontsize='10')
        self.set_color_scheme()
        node_dict = {}
        for method in self.wrapped_method_nodes:
            node = self.wrapped_method_nodes[method]
            count_label = 'Count: ' + str(self.wrapped_method_counter[method])
            time_label =  'Time: ' + str(round(self.wrapped_method_timer[method], time_resolution))
            my_node = pydot.Node(node, label=node+"\\n"+count_label+"\\n"+time_label, color=self.color_palette[method])
            node_dict[method] = my_node
            graph.add_node(my_node)
            log.information("callgraph " + str(graph.to_string()))
        edge_labels = {}
        for node_method in self.wrapped_method_nodes:
            base_node_src_node = self.wrapped_method_src[node_method]
            base_node_src_class = self.src_class_map[node_method]
            if base_node_src_class:
                base_node_src_name = base_node_src_class + '.' + base_node_src_node
            else:
                base_node_src_name = base_node_src_node
            if base_node_src_name in self.wrapped_method_nodes:
                node_a = node_dict[base_node_src_name]
                node_b = node_dict[node_method]
                key = (node_a, node_b)
                this_edge_label = str(base_node_src_name) + " -> " + node_method
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
        graph.write_png(filename)

global method_pattern
method_pattern = MethodChart()