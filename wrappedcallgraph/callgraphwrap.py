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


def graph_call_path_factory(node_container):
    """Graph call path factory function."""
    def register_method(func):
        @wraps(func)
        def function_wrapper_for_node_storage(*args, **kwargs):
            """Collects function nodes, logs stack trace, and executes functions."""
            curr_frame = inspect.stack(0)
            base_node = curr_frame[0][0].f_locals["func"].__qualname__

            if base_node not in node_container.rank["Root"]:
                node_container.rank["Root"].append(base_node)
                node_container.wrapped_method_counter[base_node] = 0
                node_container.wrapped_method_timer[base_node] = {
                    "start": time.perf_counter(),
                    "time": 0,
                }

            for frame in curr_frame[1:]:

                info = node_container.extract_source(frame=frame)

                if (
                    info in node_container.rank["Root"]
                    and info not in node_container.wrapped_method_src[base_node]
                ):
                    node_container.wrapped_method_src[base_node].append(info)

                if frame[3] == "main" and info not in node_container.rank["Root"]:
                    node_container.rank["Root"].insert(0, info)
                    node_container.wrapped_method_counter[info] = 1
                    node_container.wrapped_method_timer[info] = {"start": 0, "time": 0}

            del curr_frame

            if node_container.profiler:
                print("profiling enabled")
                node_container.profile.enable()
                start_time = time.perf_counter()
                result = func(*args, **kwargs)
                end_time = time.perf_counter()
                node_container.profile.disable()
                print("profiling disabled")
            else:
                start_time = time.perf_counter()
                result = func(*args, **kwargs)
                end_time = time.perf_counter()

            total_time = end_time - start_time
            node_container.wrapped_method_counter[base_node] += 1
            node_container.wrapped_method_timer[base_node]["time"] += total_time

            return result

        return function_wrapper_for_node_storage

    return register_method


class MethodChart:

    """Class for generating charts that show the components."""

    def __init__(self, profiler: bool = False) -> None:
        """Initizalizes the class."""
        self.wrapped_method_nodes: dict = {}
        self.wrapped_method_counter: dict = {}
        self.wrapped_method_timer: dict = {}
        self.wrapped_method_src: defaultdict = defaultdict(list)
        self.rank: defaultdict = defaultdict(list)
        self.color_palette: dict = {}
        self.profiler: bool = profiler
        if self.profiler:
            self.profile: cProfile.Profile = cProfile.Profile()

    def extract_source(self, frame: inspect.FrameInfo) -> Any:
        """Extracts the class and function name from a frame object."""
        function_name = frame[3]
        try:
            class_name = frame[0].f_locals["self"].__class__.__name__
            source = class_name + "." + function_name
        except KeyError:
            source = function_name
        return source

    def set_color_scheme(self) -> None:
        """Assigns a color scheme to each node based on number of times called."""
        max_value = max(self.wrapped_method_counter.values())
        palette = sns.color_palette("light:#5A9", max_value + 1).as_hex()
        self.color_palette = {
            method: palette[self.wrapped_method_counter[method]]
            for method in self.wrapped_method_counter
        }

    def set_order_rank(self) -> None:
        """Clusters nodes by common source."""
        for base_node in list(self.rank["Root"]):
            src_nodes = self.wrapped_method_src[base_node]
            if src_nodes:
                self.rank[tuple(src_nodes)].append(base_node)
                self.rank["Root"].remove(base_node)

    def sum_time(self) -> None:
        """Aggregate total time for main function."""
        if "main" in self.rank["Root"][0]:
            main_node = self.rank["Root"][0]
            summed_time = [
                sum(
                    self.wrapped_method_timer[node]["time"]
                    for node in self.wrapped_method_timer
                )
            ]
            self.wrapped_method_timer[main_node]["time"] = summed_time[0]

    def set_depth(self, rank: list) -> list:
        """Define the source nodes present at the max depth.

        If node is present in the root patway, the final node is selected.
        """
        max_depth = max((self.wrapped_method_src[node]) for node in rank)
        src_nodes = [
            node for node in rank if self.wrapped_method_src[node] == max_depth
        ]
        root_src_nodes = [node for node in self.rank["Root"] if node in src_nodes]
        for node in root_src_nodes[:-1]:
            src_nodes.remove(node)
        return src_nodes

    def make_graphviz_chart(self, time_resolution: int, filename: str) -> None:
        """Visualizes the entire system with graphviz."""
        graph = pydot.Dot(graph_type="digraph", compound="true")
        graph.set_node_defaults(
            color="black", style="filled", shape="box", fontname="Arial", fontsize="10"
        )
        self.set_color_scheme()
        self.sum_time()
        self.set_order_rank()

        for rank in self.rank:
            for base_node in self.rank[rank]:
                count_label = "Count: " + str(self.wrapped_method_counter[base_node])
                time_label = "Time: " + str(
                    round(self.wrapped_method_timer[base_node]["time"], time_resolution)
                )
                if rank == "Root":
                    my_node = pydot.Node(
                        base_node,
                        label=base_node + "\\n" + count_label + "\\n" + time_label,
                        fillcolor=self.color_palette[base_node],
                        style="filled, dashed",
                    )
                else:
                    my_node = pydot.Node(
                        base_node,
                        label=base_node + "\\n" + count_label + "\\n" + time_label,
                        fillcolor=self.color_palette[base_node],
                    )
                self.wrapped_method_nodes[base_node] = my_node
                graph.add_node(my_node)

        for rank in self.rank:
            if rank == "Root":
                for i in range(1, len(self.rank[rank])):
                    node_a = self.wrapped_method_nodes[self.rank[rank][i - 1]]
                    node_b = self.wrapped_method_nodes[self.rank[rank][i]]
                    graph.add_edge(pydot.Edge(node_a, node_b))
            else:
                src_nodes = self.set_depth(rank)
                for base_node in self.rank[rank]:
                    for src_node in src_nodes:
                        node_a = self.wrapped_method_nodes[src_node]
                        node_b = self.wrapped_method_nodes[base_node]
                        graph.add_edge(pydot.Edge(node_a, node_b))

        if self.profiler:
            self.profile.dump_stats("profile.pstats")

        graph.write_png(filename)

# global METHOD_PATTERN
METHOD_PATTERN = MethodChart(False)

# # #######################################################################################################################
# # Test Noah's Ideas
# class MethodChartCallGraphFactory:

#     def __init__(self, singleton_node_container: Any) -> None:
#         self.graph_call_path_factory(singleton_node_container=singleton_node_container)
        

#     def graph_call_path_factory(singleton_node_container:Any):
#         """Graph call path factory decorator function."""
#         def register_method(func):
#             @wraps(func)
#             def function_wrapper_for_node_storage(*args, **kwargs):
#                 """Collects function nodes, logs stack trace, and executes functions."""
#                 curr_frame = inspect.stack(0)
#                 base_node = curr_frame[0][0].f_locals["func"].__qualname__

#                 if base_node not in singleton_node_container.rank["Root"]:
#                     singleton_node_container.rank["Root"].append(base_node)
#                     singleton_node_container.wrapped_method_counter[base_node] = 0
#                     singleton_node_container.wrapped_method_timer[base_node] = {
#                         "start": time.perf_counter(),
#                         "time": 0,
#                     }

#                 for frame in curr_frame[1:]:

#                     info = singleton_node_container.extract_source(frame=frame)

#                     if (
#                         info in singleton_node_container.rank["Root"]
#                         and info not in singleton_node_container.wrapped_method_src[base_node]
#                     ):
#                         singleton_node_container.wrapped_method_src[base_node].append(info)

#                     if frame[3] == "main" and info not in singleton_node_container.rank["Root"]:
#                         singleton_node_container.rank["Root"].insert(0, info)
#                         singleton_node_container.wrapped_method_counter[info] = 1
#                         singleton_node_container.wrapped_method_timer[info] = {"start": 0, "time": 0}

#                 del curr_frame

#                 if singleton_node_container.profiler:
#                     print("profiling enabled")
#                     singleton_node_container.profile.enable()
#                     start_time = time.perf_counter()
#                     result = func(*args, **kwargs)
#                     end_time = time.perf_counter()
#                     singleton_node_container.profile.disable()
#                     print("profiling disabled")
#                 else:
#                 start_time = time.perf_counter()
#                 result = func(*args, **kwargs)
#                 end_time = time.perf_counter()

#                 total_time = end_time - start_time
#                 singleton_node_container.wrapped_method_counter[base_node] += 1
#                 singleton_node_container.wrapped_method_timer[base_node]["time"] += total_time

#                 return result

#             return function_wrapper_for_node_storage

#         return register_method
    
# @dataclass
# class MethodCall:
#     """
#     This MethodCall class depicts the call.
#     """
#     name: str
#     classname: str
#     timer: Any
#     callcounter: int
#     functioncalls: dict

# # https://refactoring.guru/design-patterns/singleton/python/example#example-0
# class SingletonMetaClass(type):
#     """
#     The Singleton class can be implemented in different ways in Python. Some
#     possible methods include: base class, decorator, metaclass. We will use the
#     metaclass because it is best suited for this purpose.
#     """
#     _instances = {}

#     def __call__(cls, *args, **kwargs):
#         """
#         Possible changes to the value of the `__init__` argument do not affect
#         the returned instance.
#         """
#         if cls not in cls._instances:
#             cls._instances[cls] = super(SingletonMetaClass, cls).__call__(*args, **kwargs)
#         return cls._instances[cls]
    

# @dataclass
# class SingletonDataClassNode(metaclass=SingletonMetaClass):

#     """Singleton class that contains the data (MethodCall dataclasses) in call stack."""

#     name: str
#     call_stack: List[MethodCall] = field(default_factory=lambda: [])

#     # Creating an empty stack
#     def check_empty(self):
#         return len(self.call_stack) == 0


#     # Adding items into the stack
#     def add_child(self, childnode):
#         self.call_stack.append(childnode)
#         return childnode


#     # Removing an element from the stack
#     def pop(self):
#         if (self.check_empty(self.call_stack)):
#             return "stack is empty"

#         return self.call_stack.pop()
    
#     # https://qwilka.github.io/PyCon_Limerick_2020/#38
#     def show_tree_structure(self, indent=2, _pre=""):
#         print(_pre + self.name)
#         for _c in self.call_stack:
#             _c.show_tree_structure(indent, _pre=_pre + " "*indent)

#     # def __init__(self) -> None:
#     #     """Initizalizes the class."""
#     #     self.wrapped_method_nodes: dict = {}
#     #     self.wrapped_method_counter: dict = {}
#     #     self.wrapped_method_timer: dict = {}
#     #     self.wrapped_method_src: defaultdict = defaultdict(list)
#     #     self.rank: defaultdict = defaultdict(list)
#     #     # self.profiler: bool = profiler
#     #     # if self.profiler:
#     #     #     self.profile: cProfile.Profile = cProfile.Profile()

#     # def extract_source(self, frame: inspect.FrameInfo) -> Any:
#     #     """Extracts the class and function name from a frame object."""
#     #     function_name = frame[3]
#     #     try:
#     #         class_name = frame[0].f_locals["self"].__class__.__name__
#     #         source = class_name + "." + function_name
#     #     except KeyError:
#     #         source = function_name
#     #     return source


#     # def set_order_rank(self) -> None:
#     #     """Clusters nodes by common source."""
#     #     for base_node in list(self.rank["Root"]):
#     #         src_nodes = self.wrapped_method_src[base_node]
#     #         if src_nodes:
#     #             self.rank[tuple(src_nodes)].append(base_node)
#     #             self.rank["Root"].remove(base_node)

#     # def sum_time(self) -> None:
#     #     """Aggregate total time for main function."""
#     #     if "main" in self.rank["Root"][0]:
#     #         main_node = self.rank["Root"][0]
#     #         summed_time = [
#     #             sum(
#     #                 self.wrapped_method_timer[node]["time"]
#     #                 for node in self.wrapped_method_timer
#     #             )
#     #         ]
#     #         self.wrapped_method_timer[main_node]["time"] = summed_time[0]

#     # def set_depth(self, rank: list) -> list:
#     #     """Define the source nodes present at the max depth.

#     #     If node is present in the root patway, the final node is selected.
#     #     """
#     #     max_depth = max((self.wrapped_method_src[node]) for node in rank)
#     #     src_nodes = [
#     #         node for node in rank if self.wrapped_method_src[node] == max_depth
#     #     ]
#     #     root_src_nodes = [node for node in self.rank["Root"] if node in src_nodes]
#     #     for node in root_src_nodes[:-1]:
#     #         src_nodes.remove(node)
#     #     return src_nodes

# class MethodChartGraph():

#     def __init__(self, singleton_node_container: Any=None, time_resolution: int=10, filename: str="wrappedcallgraph/HISIM_Method_Pattern.png") -> None:
#         self.color_palette: dict = {}
#         self.set_color_scheme(singleton_node_container=singleton_node_container)
#         self.make_graphviz_chart(time_resolution=time_resolution, filename=filename, singleton_node_container=singleton_node_container)
        
#     def set_color_scheme(self, singleton_node_container: Any) -> None:
#         """Assigns a color scheme to each node based on number of times called."""
#         max_value = max(singleton_node_container.wrapped_method_counter.values())
#         palette = sns.color_palette("light:#5A9", max_value + 1).as_hex()
#         self.color_palette = {
#             method: palette[singleton_node_container.wrapped_method_counter[method]]
#             for method in singleton_node_container.wrapped_method_counter
#         }

#     def make_graphviz_chart(self, time_resolution: int, filename: str, singleton_node_container: Any) -> None:
#         """Visualizes the entire system with graphviz."""
#         graph = pydot.Dot(graph_type="digraph", compound="true")
#         graph.set_node_defaults(
#             color="black", style="filled", shape="box", fontname="Arial", fontsize="10"
#         )
#         singleton_node_container.sum_time()
#         singleton_node_container.set_order_rank()

#         for rank in singleton_node_container.rank:
#             for base_node in singleton_node_container.rank[rank]:
#                 count_label = "Count: " + str(singleton_node_container.wrapped_method_counter[base_node])
#                 time_label = "Time: " + str(
#                     round(singleton_node_container.wrapped_method_timer[base_node]["time"], time_resolution)
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
#                 singleton_node_container.wrapped_method_nodes[base_node] = my_node
#                 graph.add_node(my_node)

#         for rank in singleton_node_container.rank:
#             if rank == "Root":
#                 for i in range(1, len(singleton_node_container.rank[rank])):
#                     node_a = singleton_node_container.wrapped_method_nodes[singleton_node_container.rank[rank][i - 1]]
#                     node_b = singleton_node_container.wrapped_method_nodes[singleton_node_container.rank[rank][i]]
#                     graph.add_edge(pydot.Edge(node_a, node_b))
#             else:
#                 src_nodes = singleton_node_container.set_depth(rank)
#                 for base_node in singleton_node_container.rank[rank]:
#                     for src_node in src_nodes:
#                         node_a = singleton_node_container.wrapped_method_nodes[src_node]
#                         node_b = singleton_node_container.wrapped_method_nodes[base_node]
#                         graph.add_edge(pydot.Edge(node_a, node_b))

#         # if self.profiler:
#         #     self.profile.dump_stats("profile.pstats")

#         graph.write_png(filename)

# METHOD_CHART_NODE_CONTAINER = SingletonDataClassNode()
# # METHOD_PATTERN = MethodChartGraph(singleton_node_container=METHOD_CONTAINER)


""" Class for the simulation repository. """
# clean
from typing import Any, Dict
from threading import Lock
import enum
from hisim import loadtypes as lt


# https://refactoring.guru/design-patterns/singleton/python/example#example-1


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
        """Initializes the SimRepository."""
        self.my_dict: Dict[Any, Any] = {}
        self.my_dynamic_dict: Dict[lt.ComponentType, Dict[int, Any]] = {
            elem: {} for elem in lt.ComponentType
        }

    def set_entry(self, key: Any, entry: Any) -> None:
        """Sets an entry in the SimRepository."""
        self.my_dict[key] = entry

    def get_entry(self, key: Any) -> Any:
        """Gets an entry from the SimRepository."""
        return self.my_dict[key]

    def exist_entry(self, key: Any) -> bool:
        """Checks if an entry exists."""
        if key in self.my_dict:
            return True
        return False

    def delete_entry(self, key: Any) -> None:
        """Deletes an existing entry."""
        self.my_dict.pop(key)

    def set_dynamic_entry(
        self, component_type: lt.ComponentType, source_weight: int, entry: Any
    ) -> None:
        """Sets a dynamic entry."""
        self.my_dynamic_dict[component_type][source_weight] = entry

    def get_dynamic_entry(
        self, component_type: lt.ComponentType, source_weight: int
    ) -> Any:
        """Gets a dynmaic entry."""
        component = self.my_dynamic_dict.get(component_type, None)
        if component is None:
            return None
        value = component.get(source_weight, None)
        return value

    def get_dynamic_component_weights(self, component_type: lt.ComponentType) -> list:
        """Gets weights for dynamic components."""
        return list(self.my_dynamic_dict[component_type].keys())

    def delete_dynamic_entry(
        self, component_type: lt.ComponentType, source_weight: int
    ) -> Any:
        """Deletes a dynamic component entry."""
        self.my_dynamic_dict[component_type].pop(source_weight)

    def clear(self):
        """Clears all dictionaries at the end of the simulation to enable garbage collection and reduce memory consumption."""
        self.my_dict.clear()
        del self.my_dict
        self.my_dynamic_dict.clear()
        del self.my_dynamic_dict


class SingletonDictKeyEnum(enum.Enum):

    """Class for setting dictionary keys in the singleton sim repository."""

    NUMBEROFAPARTMENTS = 1
