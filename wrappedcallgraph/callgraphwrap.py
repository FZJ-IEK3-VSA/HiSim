""" Module for visualizing selected methods as a flow chart. """
from functools import wraps
from typing import Any, List
import time
import pydot
import seaborn as sns
from dataclasses import dataclass
from typing import Any, Dict, List
from threading import Lock

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
        
        name = func.__module__ + '.' + func.__name__
        methodcall = MethodCall(name)

        if SingletonSimRepository().exist_entry(entry=methodcall):
            SingletonSimRepository().edit(entry=methodcall, time=total_time)
            SingletonSimRepository().delete_entry(entry=methodcall)
            SingletonSimRepository().set_entry(entry=methodcall)
                
        else:
            
            SingletonSimRepository().create(entry=methodcall, time=total_time)
            SingletonSimRepository().set_entry(entry=methodcall)

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
        self.my_info: dict = {}

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

    def edit(self, entry: Any, time: float) -> None:
        self.my_info[entry.name]['timer'] += time
        self.my_info[entry.name]['callcounter'] += 1
        self.set_functioncalls(entry)
    
    def create(self, entry: Any, time: float) -> None:
        self.my_info[entry.name] = {'timer': time,
                                    'callcounter': 1,
                                    'functioncalls': []}
        self.set_functioncalls(entry)

    def set_functioncalls(self, entry: Any) -> None:
        if len(self.my_list) > 0:
            if self.my_list[-1].name != entry.name:
                self.my_info[entry.name]['functioncalls'].append(self.my_list[-1])

@dataclass
class MethodCall:
    """
    This MethodCall class depicts the call.
    """
    name: str
    node: pydot.Node = None

class MethodChart:

    """Class for generating charts that show the components."""

    def make_graphviz_chart(time_resolution: int, filename: str) -> None:
        """Visualizes the entire system with graphviz."""
        graph = pydot.Dot(graph_type="digraph", compound="true", strict="true")
        graph.set_node_defaults(
            color="black", style="filled", shape="box", fontname="Arial", fontsize="10"
        )

        """Set node color scheme."""
        max_value = max([SingletonSimRepository().my_info[item.name]['callcounter'] for item in SingletonSimRepository().my_list])
        palette = sns.color_palette("light:#5A9", max_value + 1).as_hex()

        """Generate callgraph."""
        for methodcall in SingletonSimRepository().my_list:
            count_label = "Count: " + str(SingletonSimRepository().my_info[methodcall.name]['callcounter'])
            time_label = "Time: " + str(round(SingletonSimRepository().my_info[methodcall.name]['timer'], time_resolution))

            methodcall.node = pydot.Node(
                methodcall.name,
                label=methodcall.name + "\\n" + count_label + "\\n" + time_label,
                fillcolor=palette[SingletonSimRepository().my_info[methodcall.name]['callcounter']]
                )

            graph.add_node(methodcall.node)
            
            for src_node in SingletonSimRepository().my_info[methodcall.name]['functioncalls']:
                node_a = src_node.node
                node_b = methodcall.node
                graph.add_edge(pydot.Edge(node_a, node_b))

        graph.write_png(filename)