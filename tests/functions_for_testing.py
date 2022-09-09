from hisim.component import ComponentOutput


def get_number_of_outputs(list_of_components: list) -> int:
    number_of_outputs = 0
    for component in list_of_components:
        if isinstance(component, ComponentOutput):
            number_of_outputs = number_of_outputs + 1
        else:
            number_of_outputs = number_of_outputs + component.outputs.__len__()
    return number_of_outputs


def add_global_index_of_real_components(list_of_components: list, number_of_fake_inputs: int) -> None:
    counter = 0 + number_of_fake_inputs
    for component in list_of_components:
        o = component.__dict__
        for objects in component.__dict__:
            if isinstance(o[objects], ComponentOutput):
                getattr(component, objects).global_index = counter
                counter = counter + 1


def add_global_index_of_fake_components(list_of_components: list) -> int:
    number_of_fake_inputs = len(list_of_components)
    counter = 0
    for component in list_of_components:
        component.global_index = counter
        counter = counter + 1
    return number_of_fake_inputs


def add_global_index_of_components(list_of_components: list) -> None:
    list_of_real_components = []
    list_of_fake_components = []
    for component in list_of_components:
        if isinstance(component, ComponentOutput):
            list_of_fake_components.append(component)
        else:
            list_of_real_components.append(component)
    number_of_fake_inputs = add_global_index_of_fake_components(list_of_fake_components)
    add_global_index_of_real_components(list_of_components=list_of_real_components, number_of_fake_inputs=number_of_fake_inputs)
