""" Helper functions for testing. """
# clean
from hisim.component import ComponentOutput


def get_number_of_outputs(list_of_components: list) -> int:
    """Calculates the number of outputs for a list of components or individual outputs."""
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
    """Sets the global index for components."""
    counter = 0 + number_of_fake_inputs
    for component in list_of_components:
        object_dict = component.__dict__
        for objects in component.__dict__:
            if isinstance(object_dict[objects], ComponentOutput):
                getattr(component, objects).global_index = counter
                counter = counter + 1


def add_global_index_of_fake_components(list_of_components: list) -> int:
    """Sets global index for fake components."""
    number_of_fake_inputs = len(list_of_components)
    counter = 0
    for component in list_of_components:
        component.global_index = counter
        counter = counter + 1
    return number_of_fake_inputs


def add_global_index_of_components(list_of_components: list) -> None:
    """Adds the global index of components."""
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
