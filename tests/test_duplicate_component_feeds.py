"""The rule that one source output may feed one dynamic component only once.

A dynamic component sums the inputs carrying a given tag, so a source wired into it twice is
counted twice, and nothing about the result looks wrong -- the simulation completes and every
series is plausible. ``household_gas_solar_thermal`` reported a grid import of 21.72 kWh against a
total consumption of 10.9 for exactly this reason, and sat broken for months filed as a bug in the
KPI layer, because the only thing that ever noticed was a derived percentage exceeding 100.

These tests pin the refusal and, as importantly, pin what it must *not* refuse: two different
outputs of one source, and one output going to two different components, are both ordinary.
"""

# clean

import dataclasses

import pytest

from hisim import loadtypes as lt
from hisim.component import ComponentInput
from hisim.config import ComponentID
from hisim.dynamic_component import DuplicateComponentFeedError
from hisim.components.electricity_meter import ElectricityMeter, ElectricityMeterConfig
from hisim.simulationparameters import SimulationParameters


def make_meter(name: str = "ElectricityMeter") -> ElectricityMeter:
    """Build an electricity meter to wire things into."""
    config = dataclasses.replace(
        ElectricityMeterConfig.get_electricity_meter_default_config(),
        component_id=ComponentID(name=name),
    )
    return ElectricityMeter(
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
        config=config,
    )


def feed(meter: ElectricityMeter, source: str, output: str) -> None:
    """Wire one source output into the meter as uncontrolled consumption."""
    meter.add_component_input_and_connect(
        source_object_name=source,
        source_component_output=output,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )


@pytest.mark.base
def test_the_same_output_cannot_feed_one_component_twice() -> None:
    """The second feed is refused, by name, at the point of wiring."""
    meter = make_meter()
    feed(meter, "UTSPConnector", "ElectricalPowerConsumption")

    with pytest.raises(DuplicateComponentFeedError) as refusal:
        feed(meter, "UTSPConnector", "ElectricalPowerConsumption")

    message = str(refusal.value)
    assert "UTSPConnector" in message and "ElectricalPowerConsumption" in message, (
        "the refusal must name the source and output, so the reader can find the second wiring"
    )
    assert "connect_automatically" in message, (
        "the usual cause is wiring by hand as well as by default connection; the message says so"
    )


@pytest.mark.base
def test_the_refusal_happens_before_anything_is_added() -> None:
    """A refused feed leaves the component exactly as it was.

    The check runs before the input object is built and appended, so a caller that catches the
    error is not left with a half-added input that connection resolution would later trip over.
    """
    meter = make_meter()
    feed(meter, "UTSPConnector", "ElectricalPowerConsumption")
    inputs_after_first = len(meter.inputs)

    with pytest.raises(DuplicateComponentFeedError):
        feed(meter, "UTSPConnector", "ElectricalPowerConsumption")

    assert len(meter.inputs) == inputs_after_first, "the refused feed must add no input"


@pytest.mark.base
def test_two_different_outputs_of_one_source_are_allowed() -> None:
    """Distinct outputs are distinct flows, however they are tagged."""
    meter = make_meter()
    feed(meter, "UTSPConnector", "ElectricalPowerConsumption")
    feed(meter, "UTSPConnector", "WaterConsumption")

    sources = {(one.src_object_name, one.src_field_name) for one in meter.inputs if one.src_object_name}
    assert ("UTSPConnector", "ElectricalPowerConsumption") in sources
    assert ("UTSPConnector", "WaterConsumption") in sources


@pytest.mark.base
def test_one_output_may_feed_two_different_components() -> None:
    """The rule is per component, not global: a meter and an energy manager may both read a source."""
    first = make_meter("FirstMeter")
    second = make_meter("SecondMeter")

    feed(first, "UTSPConnector", "ElectricalPowerConsumption")
    feed(second, "UTSPConnector", "ElectricalPowerConsumption")

    for meter in (first, second):
        assert any(
            isinstance(one, ComponentInput) and one.src_field_name == "ElectricalPowerConsumption"
            for one in meter.inputs
        ), f"{meter.component_name} should have been fed"
