# """ Module for visualizing selected methods as a flow chart. """

# from functools import wraps
# import pydot
# import time
# from collections import defaultdict
# import inspect
# import seaborn as sns
# import cProfile
# from hisim import log
# import gprof2dot
# import pstats
# import os

# # pr = cProfile.Profile()
# # pr.enable()
# def graph_call_path_factory(node_container):
    
#     def register_method(func):
#         @wraps(func)
#         def function_wrapper_for_node_storage(*args, **kwargs):

#             curr_frame = inspect.stack(0)
#             node_name = curr_frame[0][0].f_locals['func'].__qualname__
#             src_function = curr_frame[1][3]
#             try:
#                 src_class = type(curr_frame[1][0].f_locals['self']).__qualname__
#                 src_node = src_class + '.' + src_function
#             except KeyError:
#                 src_node = src_function
#             if node_name not in node_container.wrapped_method_nodes:
#                 node_container.wrapped_method_nodes[node_name] = node_name
#                 node_container.wrapped_method_counter[node_name] = 0
#                 node_container.wrapped_method_timer[node_name] = 0

#             node_container.wrapped_method_src[node_name].add(src_node)
#             start_time = time.perf_counter()
#             if node_container.profiler:
#                 node_container.pr.enable()
#                 # log.information("profiling enabled")
#                 result = func(*args, **kwargs)
#                 node_container.pr.disable()
#                 stats = pstats.Stats(node_container.pr, stream=None).sort_stats('cumulative')
#                 # log.information("profiling disabled")
#                 stats.dump_stats("wrappedcallgraph/profiler.pstats") #node_container.pr

#             else:
#                 result = func(*args, **kwargs)
#             end_time = time.perf_counter()
#             total_time = end_time - start_time
#             node_container.wrapped_method_counter[node_name] += 1
#             node_container.wrapped_method_timer[node_name] += total_time
#             del curr_frame
#             return result
        
#         return function_wrapper_for_node_storage
    
#     return register_method
            
# class MethodChart:
#     """ Class for generating charts that show the components. """

#     def __init__(self, profiler: bool = False):
#         """ Initizalizes the class. """
#         self.wrapped_method_nodes = defaultdict(list)
#         self.wrapped_method_counter = defaultdict(int)
#         self.wrapped_method_timer = defaultdict(float)
#         self.wrapped_method_src = defaultdict(set)
#         self.profiler = profiler
#         if self.profiler:
#             self.pr = cProfile.Profile()

#     def set_color_scheme(self):
#         max_value = max(self.wrapped_method_counter.values())
#         palette = sns.color_palette("light:#5A9", max_value + 1).as_hex()
#         self.color_palette = {method: palette[self.wrapped_method_counter[method]] for method in self.wrapped_method_counter}

#     def make_graphviz_chart(self, with_labels: bool, time_resolution: int, filename: str) -> None:
#         """ Visualizes the entire system with graphviz. """
#         graph = pydot.Dot(graph_type='digraph')
#         graph.set_node_defaults(color='lightgrey', style='filled', shape='box', fontname='Arial', fontsize='10')
#         self.set_color_scheme()
#         node_dict = {}
#         for method in self.wrapped_method_nodes:
#             node = self.wrapped_method_nodes[method]
#             count_label = 'Count: ' + str(self.wrapped_method_counter[method])
#             time_label =  'Time: ' + str(round(self.wrapped_method_timer[method], time_resolution))
#             my_node = pydot.Node(node, label=node+"\\n"+count_label+"\\n"+time_label, color=self.color_palette[method])
#             node_dict[method] = my_node
#             graph.add_node(my_node)
#         edge_labels = {}
#         for base_node in self.wrapped_method_nodes:
#             src_nodes = [node for node in self.wrapped_method_src[base_node] if node in self.wrapped_method_nodes]
#             for src_node in src_nodes:
#                 node_a = node_dict[src_node]
#                 node_b = node_dict[base_node]
#                 key = (node_a, node_b)
#                 this_edge_label = str(src_node) + " -> " + base_node
#                 this_edge_label = this_edge_label.replace("°C", "&#8451;")
#                 if key not in edge_labels:
#                     edge_labels[key] = this_edge_label
#                 else:
#                     edge_labels[key] = edge_labels[key] + "\\n" + this_edge_label
#         for node_key, label in edge_labels.items():
#             if with_labels:
#                 graph.add_edge(pydot.Edge(node_key[0], node_key[1], label=label))
#             else:
#                 graph.add_edge(pydot.Edge(node_key[0], node_key[1]))
#         graph.write_png(filename)

# global method_pattern
# method_pattern = MethodChart()
""" Module for visualizing selected methods as a flow chart. """

from functools import wraps
import pydot
import time
from collections import defaultdict
import inspect
import seaborn as sns
import cProfile
from hisim import log

def graph_call_path_factory(node_container):
    
    def register_method(func):
        @wraps(func)
        def function_wrapper_for_node_storage(*args, **kwargs):

            curr_frame = inspect.stack(0)
            node_name = curr_frame[0][0].f_locals['func'].__qualname__
            src_function = curr_frame[1][3]
            try:
                src_class = type(curr_frame[1][0].f_locals['self']).__qualname__
                src_node = src_class + '.' + src_function
            except KeyError:
                src_node = src_function
            
            if node_name not in node_container.wrapped_method_nodes:
                node_container.wrapped_method_nodes[node_name] = node_name
                node_container.wrapped_method_counter[node_name] = 0
                node_container.wrapped_method_timer[node_name] = {'start':time.perf_counter(), 'time':0}

            node_container.wrapped_method_src[node_name].add(src_node)
            start_time = time.perf_counter()
            if node_container.profiler:
                node_container.pr.enable()
                # log.information("profiling enabled")
                result = func(*args, **kwargs)
                node_container.pr.disable()
                # log.information("profiling disabled")
                node_container.pr.dump_stats("wrappedcallgraph/profile.pstats")
            else:
                result = func(*args, **kwargs)
            end_time = time.perf_counter()
            total_time = end_time - start_time
            node_container.wrapped_method_counter[node_name] += 1
            node_container.wrapped_method_timer[node_name]['time'] += total_time
            del curr_frame
            return result
        
        return function_wrapper_for_node_storage
    
    return register_method

class MethodChart:
    """ Class for generating charts that show the components. """

    def __init__(self, profiler: bool = False):
        """ Initizalizes the class. """
        self.wrapped_method_nodes = defaultdict(list)
        self.wrapped_method_counter = defaultdict(int)
        self.wrapped_method_timer = defaultdict(float)
        self.wrapped_method_src = defaultdict(set)
        self.profiler = profiler
        if self.profiler:
            self.pr = cProfile.Profile()

    def set_color_scheme(self):
        max_value = max(self.wrapped_method_counter.values())
        palette = sns.color_palette("light:#5A9", max_value + 1).as_hex()
        self.color_palette = {method: palette[self.wrapped_method_counter[method]] for method in self.wrapped_method_counter}

    def set_order_rank(self):
        self.rank: dict = {}
        for base_node in self.wrapped_method_nodes:
            src_nodes = [node for node in self.wrapped_method_src[base_node] if node in self.wrapped_method_nodes]
            self.wrapped_method_src[base_node] = src_nodes
        for node, src in self.wrapped_method_src.items():
            if not src:
                src = self.wrapped_method_timer[node]['start']
            else:
                src = tuple(src)
            if src not in self.rank.keys():
                self.rank[src] = [node]
            else:
                self.rank[src].append(node)

    def make_graphviz_chart(self, with_labels: bool, time_resolution: int, filename: str) -> None:
        """ Visualizes the entire system with graphviz. """
        graph = pydot.Dot(graph_type='digraph', compound='true')
        graph.set_node_defaults(color='lightgrey', style='filled', shape='box', fontname='Arial', fontsize='10')
        self.set_color_scheme()
        self.set_order_rank()

        node_dict = {}
        previous_cluster = []
        for i,node_set in enumerate(self.rank.values()):
            cluster=pydot.Cluster(str(i), label=str(i))
            graph.add_subgraph(cluster)
            for node in node_set:
                count_label = 'Count: ' + str(self.wrapped_method_counter[node])
                time_label =  'Time: ' + str(round(self.wrapped_method_timer[node]['time'], time_resolution))
                my_node = pydot.Node(node, label=node+"\\n"+count_label+"\\n"+time_label, color=self.color_palette[node])
                cluster.add_node(my_node)
                node_dict[node] = my_node
            if i > 0:
                for prev_node in previous_cluster:
                    graph.add_edge(pydot.Edge(prev_node, node))
                previous_cluster.clear()
                previous_cluster.append(node)

        # groupings = list(cluster_rank)
        # for i,node_set in enumerate(cluster_rank.values()):
        #     if i == 0:
        #         src_nodes = []
        #     else:
        #         src_nodes = cluster_rank[groupings[i-1]]
        #     for base_node in node_set:
        #         node_base = node_dict[base_node]
        #         for src_node in src_nodes:
        #             node_source = node_dict[src_node]
        #             graph.add_edge(pydot.Edge(node_source, node_base))
    
        # edge_labels = {}
        # for base_node in self.wrapped_method_nodes:
        #     for src_node in self.wrapped_method_src[base_node]:
        #         node_a = node_dict[src_node]
        #         node_b = node_dict[base_node]
        #         key = (node_a, node_b)
        #         this_edge_label = str(src_node) + " -> " + base_node
        #         this_edge_label = this_edge_label.replace("°C", "&#8451;")
        #         if key not in edge_labels:
        #             edge_labels[key] = this_edge_label
        #         else:
        #             edge_labels[key] = edge_labels[key] + "\\n" + this_edge_label
        # for node_key, label in edge_labels.items():
        #     if with_labels:
        #         graph.add_edge(pydot.Edge(node_key[0], node_key[1], label=label))
        #     else:
        #         graph.add_edge(pydot.Edge(node_key[0], node_key[1]))

        graph.write_png(filename)

global method_pattern
method_pattern = MethodChart()
