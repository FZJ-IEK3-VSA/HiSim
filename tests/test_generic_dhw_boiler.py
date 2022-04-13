from hisim.components import generic_dhw_boiler as sbb

def test_simple_bucket_boiler_state():
    state = sbb.BoilerState(100,300)
    energy = state.energy_from_temperature()
    cloned_state = state.clone()
    assert (state.temperature_in_K == cloned_state.temperature_in_K)
    cloned_state.set_temperature_from_energy(energy)
    assert(state.temperature_in_K == cloned_state.temperature_in_K)