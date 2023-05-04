""" Module for visualizing selected methods as a flow chart. """
from functools import wraps
from typing import Any, List
import time
from collections import defaultdict
import inspect
import cProfile
import pydot
import seaborn as sns
from dataclasses import dataclass, field
from typing import Any, Dict, List
from threading import Lock

# def graph_call_path_factory(node_container):
#     """Graph call path factory function."""
#     def register_method(func):
#         @wraps(func)
#         def function_wrapper_for_node_storage(*args, **kwargs):
#             """Collects function nodes, logs stack trace, and executes functions."""
#             curr_frame = inspect.stack(0)
#             base_node = curr_frame[0][0].f_locals["func"].__qualname__

#             if base_node not in node_container.rank["Root"]:
#                 node_container.rank["Root"].append(base_node)
#                 node_container.wrapped_method_counter[base_node] = 0
#                 node_container.wrapped_method_timer[base_node] = {
#                     "start": time.perf_counter(),
#                     "time": 0,
#                 }

#             for frame in curr_frame[1:]:

#                 info = node_container.extract_source(frame=frame)

#                 if (
#                     info in node_container.rank["Root"]
#                     and info not in node_container.wrapped_method_src[base_node]
#                 ):
#                     node_container.wrapped_method_src[base_node].append(info)

#                 if frame[3] == "main" and info not in node_container.rank["Root"]:
#                     node_container.rank["Root"].insert(0, info)
#                     node_container.wrapped_method_counter[info] = 1
#                     node_container.wrapped_method_timer[info] = {"start": 0, "time": 0}

#             del curr_frame

#             if node_container.profiler:
#                 print("profiling enabled")
#                 node_container.profile.enable()
#                 start_time = time.perf_counter()
#                 result = func(*args, **kwargs)
#                 end_time = time.perf_counter()
#                 node_container.profile.disable()
#                 print("profiling disabled")
#             else:
#                 start_time = time.perf_counter()
#                 result = func(*args, **kwargs)
#                 end_time = time.perf_counter()

#             total_time = end_time - start_time
#             node_container.wrapped_method_counter[base_node] += 1
#             node_container.wrapped_method_timer[base_node]["time"] += total_time

#             return result

#         return function_wrapper_for_node_storage

#     return register_method


# class MethodChart:

#     """Class for generating charts that show the components."""

#     def __init__(self, profiler: bool = False) -> None:
#         """Initizalizes the class."""
#         self.wrapped_method_nodes: dict = {}
#         self.wrapped_method_counter: dict = {}
#         self.wrapped_method_timer: dict = {}
#         self.wrapped_method_src: defaultdict = defaultdict(list)
#         self.rank: defaultdict = defaultdict(list)
#         self.color_palette: dict = {}
#         self.profiler: bool = profiler
#         if self.profiler:
#             self.profile: cProfile.Profile = cProfile.Profile()

#     def extract_source(self, frame: inspect.FrameInfo) -> Any:
#         """Extracts the class and function name from a frame object."""
#         function_name = frame[3]
#         try:
#             class_name = frame[0].f_locals["self"].__class__.__name__
#             source = class_name + "." + function_name
#         except KeyError:
#             source = function_name
#         return source

#     def set_color_scheme(self) -> None:
#         """Assigns a color scheme to each node based on number of times called."""
#         max_value = max(self.wrapped_method_counter.values())
#         palette = sns.color_palette("light:#5A9", max_value + 1).as_hex()
#         self.color_palette = {
#             method: palette[self.wrapped_method_counter[method]]
#             for method in self.wrapped_method_counter
#         }

#     def set_order_rank(self) -> None:
#         """Clusters nodes by common source."""
#         for base_node in list(self.rank["Root"]):
#             src_nodes = self.wrapped_method_src[base_node]
#             if src_nodes:
#                 self.rank[tuple(src_nodes)].append(base_node)
#                 self.rank["Root"].remove(base_node)

#     def sum_time(self) -> None:
#         """Aggregate total time for main function."""
#         if "main" in self.rank["Root"][0]:
#             main_node = self.rank["Root"][0]
#             summed_time = [
#                 sum(
#                     self.wrapped_method_timer[node]["time"]
#                     for node in self.wrapped_method_timer
#                 )
#             ]
#             self.wrapped_method_timer[main_node]["time"] = summed_time[0]

#     def set_depth(self, rank: list) -> list:
#         """Define the source nodes present at the max depth.

#         If node is present in the root patway, the final node is selected.
#         """
#         max_depth = max((self.wrapped_method_src[node]) for node in rank)
#         src_nodes = [
#             node for node in rank if self.wrapped_method_src[node] == max_depth
#         ]
#         root_src_nodes = [node for node in self.rank["Root"] if node in src_nodes]
#         for node in root_src_nodes[:-1]:
#             src_nodes.remove(node)
#         return src_nodes

#     def make_graphviz_chart(self, time_resolution: int, filename: str) -> None:
#         """Visualizes the entire system with graphviz."""
#         graph = pydot.Dot(graph_type="digraph", compound="true")
#         graph.set_node_defaults(
#             color="black", style="filled", shape="box", fontname="Arial", fontsize="10"
#         )
#         self.set_color_scheme()
#         self.sum_time()
#         self.set_order_rank()

#         for rank in self.rank:
#             for base_node in self.rank[rank]:
#                 count_label = "Count: " + str(self.wrapped_method_counter[base_node])
#                 time_label = "Time: " + str(
#                     round(self.wrapped_method_timer[base_node]["time"], time_resolution)
#                 )
#                 if rank == "Root":
#                     my_node = pydot.Node(
#                         base_node,
#                         label=base_node + "\\n" + count_label + "\\n" + time_label,
#                         fillcolor=self.color_palette[base_node],
#                         style="filled, dashed",
#                     )
#                 else:
#                     my_node = pydot.Node(
#                         base_node,
#                         label=base_node + "\\n" + count_label + "\\n" + time_label,
#                         fillcolor=self.color_palette[base_node],
#                     )
#                 self.wrapped_method_nodes[base_node] = my_node
#                 graph.add_node(my_node)

#         for rank in self.rank:
#             if rank == "Root":
#                 for i in range(1, len(self.rank[rank])):
#                     node_a = self.wrapped_method_nodes[self.rank[rank][i - 1]]
#                     node_b = self.wrapped_method_nodes[self.rank[rank][i]]
#                     graph.add_edge(pydot.Edge(node_a, node_b))
#             else:
#                 src_nodes = self.set_depth(rank)
#                 for base_node in self.rank[rank]:
#                     for src_node in src_nodes:
#                         node_a = self.wrapped_method_nodes[src_node]
#                         node_b = self.wrapped_method_nodes[base_node]
#                         graph.add_edge(pydot.Edge(node_a, node_b))

#         if self.profiler:
#             self.profile.dump_stats("profile.pstats")

#         graph.write_png(filename)

# # global METHOD_PATTERN
# METHOD_PATTERN = MethodChart(False)



# https://refactoring.guru/design-patterns/singleton/python/example#example-1

"""Graph call path factory function."""
def register_method(func):
    @wraps(func)
    def function_wrapper_for_node_storage(*args, **kwargs):
        """Collects function nodes, logs singleton, and executes functions."""
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        total_time = end_time - start_time

        if SingletonSimRepository().exist_entry(entry=func) is True:
            SingletonSimRepository().edit(entry=func)
            SingletonSimRepository().delete_entry(entry=func)
        
            SingletonSimRepository().set_timer(entry=methodcall, time=total_time)
            SingletonSimRepository().set_counter(entry=methodcall)
            SingletonSimRepository().set_functioncalls(entry=methodcall)
            SingletonSimRepository().set_entry(entry=func)
                
        else:
            SingletonSimRepository().create(entry=func)
            SingletonSimRepository().set_entry(entry=func)

        return result

    return function_wrapper_for_node_storage
       

class SingletonMeta(type):

    """A class for a thread-safe implementation of Singleton."""

    _instances: Dict[Any, Any] = {}

    _lock: Lock = Lock()
    # We now have a lock object that will be used to synchronize threads during first access to the Singleton.

    def __call__(cls, *args, **kwargs):
        """Possible changes to the value of the `__init__` argument do not affect the returned instance."""
        # Now, imagine that the program has just been launched. Since there's no
        # Singleton instance yet, multiple threads can simultaneously pass the
        # previous conditional and reach this point almost at the same time. The
        # first of them will acquire lock and will proceed further, while the
        # rest will wait here.
        with cls._lock:
            # The first thread to acquire the lock, reaches this conditional,
            # goes inside and creates the Singleton instance. Once it leaves the
            # lock block, a thread that might have been waiting for the lock
            # release may then enter this section. But since the Singleton field
            # is already initialized, the thread won't create a new object.
            if cls not in cls._instances:
                instance = super().__call__(*args, **kwargs)
                cls._instances[cls] = instance
        return cls._instances[cls]


class SingletonSimRepository(metaclass=SingletonMeta):

    """Class for exchanging information across all components."""

    def __init__(self) -> None:
        """Initializes the Singleton."""
        self.my_list: List[MethodCall] = []

    def set_entry(self, entry: Any) -> None:
        """Sets an entry in the Singleton."""
        self.my_list.append(entry)

    def get_entry(self, entry: Any) -> Any:
        """Gets an entry from the Singleton."""
        return entry

    def exist_entry(self, entry: Any) -> bool:
        """Checks if an entry exists."""
        if entry in self.my_list:
            return True
        return False

    def delete_entry(self, entry: Any) -> None:
        """Deletes an existing entry."""
        self.my_list.remove(entry)

    def clear(self):
        """Clears all lists at the end of the simulation to enable garbage collection and reduce memory consumption."""
        self.my_list.clear()
        del self.my_list

    def set_name(self, entry: Any, name: str) -> None:
        entry.name = name

    def set_class(self, entry: Any, cls_class: str) -> None:
        entry.classname = cls_class

    def set_timer(self, entry: Any, time: float) -> None:
        entry.timer += time
    
    def set_counter(self, entry: Any) -> None:
        entry.callcounter += 1

    def set_functioncalls(self, entry: Any) -> None:
        if len(self.my_list) > 0:
            src_name = self.my_list[-1].name
            base_name = entry.name
            if self.my_list[-1].name != entry.name:
                entry.functioncalls.append(self.my_list[-1])

@dataclass
class MethodCall(fname):
    """
    This MethodCall class depicts the call.
    """
    name: str = fname
    node: pydot.Node = None
    timer: Any = 0.0
    callcounter: int = 0
    functioncalls: List[Any] = field(default_factory=list) 

class MethodChart:

    """Class for generating charts that show the components."""

    def make_graphviz_chart(time_resolution: int, filename: str) -> None:
        """Visualizes the entire system with graphviz."""
        graph = pydot.Dot(graph_type="digraph", compound="true", strict="true")
        graph.set_node_defaults(
            color="black", style="filled", shape="box", fontname="Arial", fontsize="10"
        )

        """Set node color scheme."""
        max_value = max([methodcall.callcounter for methodcall in SingletonSimRepository().my_list])
        palette = sns.color_palette("light:#5A9", max_value + 1).as_hex()

        """Generate callgraph."""
        for methodcall in SingletonSimRepository().my_list:
            count_label = "Count: " + str(methodcall.callcounter)
            time_label = "Time: " + str(round(methodcall.timer, time_resolution))
            name_label = methodcall.classname + '.' + methodcall.name

            methodcall.node = pydot.Node(
                methodcall.name,
                label=name_label + "\\n" + count_label + "\\n" + time_label,
                fillcolor=palette[methodcall.callcounter]
                )

            graph.add_node(methodcall.node)
            
            for src_node in methodcall.functioncalls:
                node_a = src_node.node
                node_b = methodcall.node
                graph.add_edge(pydot.Edge(node_a, node_b))

        graph.write_png(filename)