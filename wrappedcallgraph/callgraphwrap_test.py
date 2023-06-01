""" Module for visualizing selected methods as a flow chart. """
from functools import wraps
from typing import Any, Dict, List
import time
from dataclasses import dataclass
from threading import Lock
import pydot
import seaborn as sns


# https://refactoring.guru/design-patterns/singleton/python/example#example-1

"""Graph call path factory function."""


def register_method(func):
    """Register method for storing the function's information."""

    @wraps(func)
    def function_wrapper_for_node_storage(*args, **kwargs):
        """Collects function nodes, logs singleton, and executes functions."""

        name = func.__module__ + "." + func.__name__

        # create a methodcall object with the name of the wrapped function
        methodcall = MethodCall(name)

        # if the methodcall object exists already in list of all functions, increase callcounter of the methodcall
        if SingletonSimRepository().exist_entry(
            entry=methodcall, my_list=SingletonSimRepository().my_list_all_functions
        ):
            SingletonSimRepository().edit_callcounter(entry=methodcall)

        # else add the methodcall object to singleton list of all functions and to list of source functions
        else:
            SingletonSimRepository().create(
                entry=methodcall,
                timer=0,
                my_list=SingletonSimRepository().my_list_source_functions,
            )
            SingletonSimRepository().set_entry(
                entry=methodcall, my_list=SingletonSimRepository().my_list_all_functions
            )
            SingletonSimRepository().set_entry(
                entry=methodcall,
                my_list=SingletonSimRepository().my_list_source_functions,
            )

        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        total_time = end_time - start_time
        SingletonSimRepository().edit_timer(entry=methodcall, timer=total_time)

        # when the function execution is completed, the function will not call any other functions anymore and can be deleted from source function list
        if SingletonSimRepository().exist_entry(
            entry=methodcall, my_list=SingletonSimRepository().my_list_source_functions
        ):
            SingletonSimRepository().delete_entry(
                entry=methodcall,
                my_list=SingletonSimRepository().my_list_source_functions,
            )

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

    """Singelton class to store information of the functions/methodcall objects.

    Attributes
    ----------
    my_list_all_functions: List[MethodCall] = []
        list for storing all the called functions as methodcall objects
    my_list_source_functions: List[MethodCall] = []
        list containing only the current possible source/parent functions as methocall objects
    my_info: dict = {}
        dictionary containing information about the methodcall objects

    """

    def __init__(self) -> None:
        """Initializes the Singleton."""
        self.my_list_all_functions: List[MethodCall] = []
        self.my_list_source_functions: List[MethodCall] = []
        self.my_info: dict = {}

    def set_entry(self, entry: Any, my_list: List[Any]) -> None:
        """Sets an entry in the Singleton."""
        my_list.append(entry)

    def get_entry(self, entry: Any) -> Any:
        """Gets an entry from the Singleton."""
        return entry

    def exist_entry(self, entry: Any, my_list: List[Any]) -> bool:
        """Checks if an entry exists."""
        if entry in my_list:
            return True
        return False

    def delete_entry(self, entry: Any, my_list: List[Any]) -> None:
        """Deletes an existing entry."""
        my_list.remove(entry)

    def clear(self, my_list: List[Any]) -> None:
        """Clears all the list at the end of the simulation to enable garbage collection and reduce memory consumption."""
        my_list.clear()
        del my_list

    def edit_callcounter(self, entry: Any) -> None:
        """Increases the callcounter of the methodcall object by one."""
        self.my_info[entry.name]["callcounter"] += 1

    def edit_timer(self, entry: Any, timer: float) -> None:
        """Edits timer for the methodcall object."""
        self.my_info[entry.name]["timer"] += timer

    def create(self, entry: Any, timer: float, my_list: List[Any]) -> None:
        """Creates an entry in the info dictionary for the methodcall object and sets its source functions."""
        self.my_info[entry.name] = {
            "timer": timer,
            "callcounter": 1,
            "start": time.perf_counter(),
            "source_functions": [],
        }
        self.set_source_functions(entry, my_list=my_list)

    def set_source_functions(self, entry: Any, my_list: List[Any]) -> None:
        """Adds the source functions of the methodcall object to the info dictionary."""

        if len(my_list) > 0:
            if my_list[-1].name != entry.name:
                self.my_info[entry.name]["source_functions"].append(my_list[-1])


@dataclass
class MethodCall:

    """Methodcall class for depicting the call."""

    name: str
    node: pydot.Node = None


class MethodChart:

    """Class for generating charts that show the components."""

    def make_graphviz_chart(self, time_resolution: int, filename: str) -> None:
        """Visualizes the entire system with graphviz."""
        graph = pydot.Dot(graph_type="digraph", compound="true", strict="true")
        graph.set_node_defaults(
            color="black", style="filled", shape="box", fontname="Arial", fontsize="10"
        )

        """Set node color scheme."""
        max_value = max(
            [
                SingletonSimRepository().my_info[item.name]["callcounter"]
                for item in SingletonSimRepository().my_list_all_functions
            ]
        )
        palette = sns.color_palette("light:#5A9", max_value + 1).as_hex()

        """Generate callgraph."""
        for methodcall in SingletonSimRepository().my_list_all_functions:
            count_label = "Count: " + str(
                SingletonSimRepository().my_info[methodcall.name]["callcounter"]
            )
            time_label = "Time: " + str(
                round(
                    SingletonSimRepository().my_info[methodcall.name]["timer"],
                    time_resolution,
                )
            )

            methodcall.node = pydot.Node(
                methodcall.name,
                label=methodcall.name + "\\n" + count_label + "\\n" + time_label,
                fillcolor=palette[
                    SingletonSimRepository().my_info[methodcall.name]["callcounter"]
                ],
            )

            graph.add_node(methodcall.node)

            for src_node in SingletonSimRepository().my_info[methodcall.name][
                "source_functions"
            ]:

                node_a = src_node.node
                node_b = methodcall.node

                if None not in (node_a, node_b):
                    graph.add_edge(pydot.Edge(node_a, node_b))
                else:
                    raise ValueError(
                        f"Edge cannot be created because at least one of the nodes is None: node_a: {node_a}, node_b: {node_b}"
                    )

        graph.write_png(filename)
