""" Module for visualizing selected methods as a flow chart. """
from functools import wraps
import pydot
import time
from collections import defaultdict
import inspect
import seaborn as sns
import cProfile

def graph_call_path_factory(node_container):

    def register_method(func):
        @wraps(func)
        def function_wrapper_for_node_storage(*args, **kwargs):
            """ Collects function nodes, logs stack trace, and executes functions. """
            curr_frame = inspect.stack(0)
            base_node = curr_frame[0][0].f_locals['func'].__qualname__

            if base_node not in node_container.wrapped_method_nodes:
                node_container.wrapped_method_nodes[base_node] = base_node
                node_container.wrapped_method_counter[base_node] = 0
                node_container.wrapped_method_timer[base_node] = {'start':time.perf_counter(), 'time':0}

            for frame in curr_frame[1:]:
                info = MethodChart.extract_source(frame)
                if info in node_container.wrapped_method_nodes:
                    node_container.wrapped_method_src[base_node].add(info)
            del curr_frame

            if node_container.profiler:
                print("profiling enabled")
                node_container.pr.enable()
                start_time = time.perf_counter()
                result = func(*args, **kwargs)
                end_time = time.perf_counter()
                node_container.pr.disable()
                print("profiling disabled")
            else:
                start_time = time.perf_counter()
                result = func(*args, **kwargs)
                end_time = time.perf_counter()

            total_time = end_time - start_time
            node_container.wrapped_method_counter[base_node] += 1
            node_container.wrapped_method_timer[base_node]['time'] += total_time

            return result

        return function_wrapper_for_node_storage
    
    return register_method


class MethodChart:
    """ Class for generating charts that show the components. """

    def __init__(self,
                 profiler: bool = False) -> None:
        """ Initizalizes the class. """
        self.wrapped_method_nodes:dict = {}
        self.wrapped_method_counter:dict = {}
        self.wrapped_method_timer:dict = {}
        self.wrapped_method_src:defaultdict = defaultdict(set)
        self.rank:defaultdict = defaultdict(list)
        self.profiler = profiler
        if self.profiler:
            self.pr:cProfile = cProfile.Profile()

    def set_color_scheme(self) -> None:
        """ Assigns a color scheme to each node based on number of times called. """
        max_value = max(self.wrapped_method_counter.values())
        palette = sns.color_palette("light:#5A9", max_value + 1).as_hex()
        self.color_palette = {method: palette[self.wrapped_method_counter[method]] for method in self.wrapped_method_counter}

    def extract_source(frame: inspect.FrameInfo) -> str:
        """ Extracts the class and function name from a frame object. """
        function_name = frame[3]
        try:
            class_name = frame[0].f_locals['self'].__class__.__name__
            source = class_name + '.' + function_name
        except KeyError:
            source = function_name
        return source

    def set_order_rank(self) -> None:
        """ Clusters nodes by common source. """
        for base_node in self.wrapped_method_nodes:
            src_nodes = self.wrapped_method_src[base_node]
            if src_nodes:
                self.rank[tuple(src_nodes)].append(base_node)
            else:
                self.rank['Root'].append(base_node)

    def make_graphviz_chart(self,
                            time_resolution: int,
                            filename: str) -> None:
        """ Visualizes the entire system with graphviz. """
        graph = pydot.Dot(graph_type='digraph', compound='true')
        graph.set_node_defaults(color='lightgrey', style='filled', shape='box', fontname='Arial', fontsize='10')
        self.set_color_scheme()
        self.set_order_rank()

        for base_node in self.wrapped_method_nodes:
            count_label = 'Count: ' + str(self.wrapped_method_counter[base_node])
            time_label =  'Time: ' + str(round(self.wrapped_method_timer[base_node]['time'], time_resolution))
            my_node = pydot.Node(base_node, label=base_node+"\\n"+count_label+"\\n"+time_label, color=self.color_palette[base_node])
            self.wrapped_method_nodes[base_node] = my_node
            graph.add_node(my_node)

        for rank in self.rank:
            if rank == 'Root':
                for i in range(1, len(self.rank[rank])):
                    node_a = self.wrapped_method_nodes[self.rank[rank][i-1]]
                    node_b = self.wrapped_method_nodes[self.rank[rank][i]]
                    graph.add_edge(pydot.Edge(node_a, node_b))
            else:
                for base_node in self.rank[rank]:
                    if len(rank) == 1:
                        node_a = self.wrapped_method_nodes[rank[0]]
                        node_b = self.wrapped_method_nodes[base_node]
                        graph.add_edge(pydot.Edge(node_a, node_b))
                    else:
                        for src_node in rank:
                            if src_node != self.rank['Root'][0]:
                                node_a = self.wrapped_method_nodes[src_node]
                                node_b = self.wrapped_method_nodes[base_node]
                                graph.add_edge(pydot.Edge(node_a, node_b))      

        if self.profiler:
            self.pr.dump_stats("profile.pstats")

        graph.write_png(filename)

global method_pattern
method_pattern = MethodChart()
