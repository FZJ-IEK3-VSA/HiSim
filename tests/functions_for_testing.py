"""Helper functions for testing."""
# clean
from hisim.component import ComponentOutput


def get_number_of_outputs(list_of_components: list) -> int:
    """Calculates the number of outputs for a list of components or individual outputs.

    Iterates over the list and, for each entry, either counts it as a single
    output (if it is a ``ComponentOutput`` instance) or sums the length of the
    component's ``.outputs`` collection.

    Args:
        list_of_components (list): Mixed list of component instances (which
            expose an ``.outputs`` attribute) and individual ``ComponentOutput``
            objects.

    Returns:
        int: Total number of outputs across all entries in the list.
    """
    number_of_outputs = 0
    for component in list_of_components:
        if isinstance(component, ComponentOutput):
            number_of_outputs = number_of_outputs + 1
        else:
            number_of_outputs = number_of_outputs + len(component.outputs)
    return number_of_outputs


def add_global_index_of_real_components(
    list_of_components: list, number_of_fake_inputs: int
) -> None:
    """Sets the global index for real component outputs.

    Iterates over each component in the list and assigns a global_index to every
    ``ComponentOutput`` instance found among the component's instance attributes.
    The component's ``__dict__`` is scanned directly for attributes whose value
    is a ``ComponentOutput``; this does not rely on an ``.outputs`` collection.

    The indices start after the offset provided by number_of_fake_inputs,
    ensuring fake-component indices come first in the global ordering.

    Args:
        list_of_components (list): List of component instances. Each component is
            inspected via ``__dict__`` and every attribute whose value is a
            ``ComponentOutput`` is assigned the next global index.
        number_of_fake_inputs (int): Offset to apply so fake-component indices
            come first in the global index sequence.

    Returns:
        None: Mutates the global_index attribute on each ComponentOutput.

    Note:
        Unlike :func:`get_number_of_outputs`, which counts outputs through a
        component's ``.outputs`` attribute, this function discovers
        ``ComponentOutput`` objects by scanning ``__dict__``. A component that
        stores its outputs only inside an ``.outputs`` list (rather than as
        individual attributes) would therefore not be indexed here. Aligning the
        two approaches could be considered for consistency.
    """
    counter = 0 + number_of_fake_inputs
    for component in list_of_components:
        for attr_name, attr_value in component.__dict__.items():
            if isinstance(attr_value, ComponentOutput):
                getattr(component, attr_name).global_index = counter
                counter = counter + 1


def add_global_index_of_fake_components(list_of_components: list) -> int:
    """Sets global index for fake components (individual ComponentOutput objects).

    Assigns sequential global_index values starting from 0 to each ComponentOutput
    in the list. These "fake components" are individual ComponentOutput objects
    rather than full component instances.

    Args:
        list_of_components (list): List of ComponentOutput instances (referred to
            as "fake components" in this context).

    Returns:
        int: Count of fake components (used as offset by callers to ensure
            non-overlapping indices with real components).
    """
    number_of_fake_inputs = len(list_of_components)
    counter = 0
    for component in list_of_components:
        component.global_index = counter
        counter = counter + 1
    return number_of_fake_inputs


def add_global_index_of_components(list_of_components: list) -> None:
    """Adds the global index of components.

    Partitions the mixed list into real components (full component instances) and
    fake components (individual ComponentOutput objects). Assigns non-overlapping
    global indices starting with fake components (indices 0 to n-1), followed by
    real component outputs (indices n onwards).

    Args:
        list_of_components (list): Mixed list of component instances and
            ComponentOutput objects.

    Returns:
        None: Mutates the global_index attribute on each ComponentOutput.
    """
    list_of_real_components = []
    list_of_fake_components = []
    for component in list_of_components:
        if isinstance(component, ComponentOutput):
            list_of_fake_components.append(component)
        else:
            list_of_real_components.append(component)
    number_of_fake_inputs = add_global_index_of_fake_components(list_of_fake_components)
    add_global_index_of_real_components(
        list_of_components=list_of_real_components,
        number_of_fake_inputs=number_of_fake_inputs,
    )
