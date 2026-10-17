"""Property tests of the start-of-step node: every circuit booked on the start temperature, the balance closed per step.

``hypothesis`` is not a declared test dependency, so the properties are checked over seeded random sweeps with fixed
seeds, as in ``test_hydronics.py``; a reference sum uses :func:`math.fsum`.
"""

import math
import random
from typing import Any, ClassVar, Dict, List, Tuple

import pytest

from hisim import hydronics
from hisim.hydronics import Inflow, MixedNode, StartOfStepNode, Water

pytestmark = pytest.mark.base


class StartNodeSweep:

    """The settings of the seeded sweeps, a namespace."""

    #: The step lengths the sweeps draw cases for, in s.
    STEP_LENGTHS_IN_SECONDS: ClassVar[Tuple[float, ...]] = (60.0, 900.0, 3600.0)
    #: The cases drawn per step length.
    CASES_PER_STEP_LENGTH: ClassVar[int] = 2000
    #: The seed of every sweep.
    SEED: ClassVar[int] = 20261015

    @classmethod
    def cases(cls) -> List[Tuple[float, List[Inflow], float, float, float, float, float]]:
        """Return random node steps: start temperature, inflows, UA, ambient, step length, heat capacity, draw.

        Temperatures span 5 to 95 °C, flows 0 to 1 kg/s per stream (a third of them without flow), loss coefficients
        0 to 10 W/K, heat capacities those of 20 to 1000 kg of water, and draws 0 to 10 kW.
        """
        generator = random.Random(cls.SEED)
        drawn = []
        for dt_in_seconds in cls.STEP_LENGTHS_IN_SECONDS:
            for _ in range(cls.CASES_PER_STEP_LENGTH):
                inflows = [
                    Inflow(
                        0.0 if generator.random() < 1 / 3 else generator.uniform(0.0, 1.0), generator.uniform(5, 95)
                    )
                    for _ in range(generator.randint(0, 3))
                ]
                drawn.append(
                    (
                        generator.uniform(5, 95),
                        inflows,
                        generator.uniform(0.0, 10.0),
                        generator.uniform(10, 25),
                        dt_in_seconds,
                        generator.uniform(20, 1000) * Water.SPECIFIC_HEAT_J_PER_KG_K,
                        0.0 if generator.random() < 0.5 else generator.uniform(0.0, 10000.0),
                    )
                )
        return drawn


def test_the_balance_closes_on_every_step_whatever_the_step_length() -> None:
    """The inflow heats less loss and draw equal the change of stored heat, to a 1e-9 share of the largest term."""
    for t0, inflows, ua, t_amb, dt, capacity, drawn_w in StartNodeSweep.cases():
        step = StartOfStepNode.step(
            t0_c=t0, inflows=inflows, ua_w_per_k=ua, t_amb_c=t_amb, dt_s=dt, heat_capacity_j_per_k=capacity,
            heat_drawn_w=drawn_w,
        )
        terms = list(step.heat_in_j_per_inflow) + [-step.loss_j, -step.heat_drawn_j]
        stored = capacity * (step.t_end_c - t0)
        scale = max([abs(term) for term in terms] + [abs(stored), 1.0])
        assert math.fsum(terms) - stored == pytest.approx(0.0, abs=1e-9 * scale)


def test_every_circuit_is_booked_on_the_start_temperature() -> None:
    """Each inflow brings ``m c (T_i - T0) dt``, the loss is ``UA (T0 - T_amb) dt``: both ends book from one triple."""
    for t0, inflows, ua, t_amb, dt, capacity, drawn_w in StartNodeSweep.cases()[:500]:
        step = StartOfStepNode.step(
            t0_c=t0, inflows=inflows, ua_w_per_k=ua, t_amb_c=t_amb, dt_s=dt, heat_capacity_j_per_k=capacity,
            heat_drawn_w=drawn_w,
        )
        for inflow, heat_in_j in zip(inflows, step.heat_in_j_per_inflow):
            assert heat_in_j == hydronics.circuit_heat_j(
                mass_flow_kg_per_s=inflow.mass_flow_kg_per_s, t_supply_c=inflow.temperature_c, t_return_c=t0, dt_s=dt
            )
        assert step.loss_j == ua * (t0 - t_amb) * dt
        assert step.heat_drawn_j == drawn_w * dt


def test_the_worked_example_of_the_docstring() -> None:
    """170 kg at 40 °C charged with 0.3 kg/s at 45 °C for 900 s, losing 3.6 W/K to 20 °C, ends at 47.85 °C."""
    step = StartOfStepNode.step(
        t0_c=40.0, inflows=[Inflow(0.3, 45.0)], ua_w_per_k=3.6, t_amb_c=20.0, dt_s=900.0,
        heat_capacity_j_per_k=170.0 * Water.SPECIFIC_HEAT_J_PER_KG_K,
    )
    assert len(step.heat_in_j_per_inflow) == 1
    assert step.heat_in_j_per_inflow[0] == pytest.approx(5.643e6)
    assert step.loss_j == pytest.approx(64800.0)
    assert step.t_end_c == pytest.approx(47.849986, abs=1e-6)


def test_a_short_step_agrees_with_the_mixed_node() -> None:
    """For a step short against the node's turnover both nodes end at the same temperature to second order.

    A 300 kg node at 40 °C with 0.1 kg/s at 50 °C and a 2 W/K loss over 10 s: the turnover ratio is 0.003, and the
    two end temperatures agree within 1e-4 K, while over 3600 s they differ by more than 1 K.
    """
    arguments: Dict[str, Any] = {
        "t0_c": 40.0,
        "inflows": [Inflow(0.1, 50.0)],
        "ua_w_per_k": 2.0,
        "t_amb_c": 20.0,
        "heat_capacity_j_per_k": 300.0 * Water.SPECIFIC_HEAT_J_PER_KG_K,
    }
    short_start = StartOfStepNode.step(dt_s=10.0, **arguments)
    short_mixed = MixedNode.step(dt_s=10.0, **arguments)
    assert short_start.t_end_c == pytest.approx(short_mixed.t_end_c, abs=1e-4)
    long_start = StartOfStepNode.step(dt_s=3600.0, **arguments)
    long_mixed = MixedNode.step(dt_s=3600.0, **arguments)
    assert abs(long_start.t_end_c - long_mixed.t_end_c) > 1.0


def test_a_negative_draw_puts_heat_into_the_node() -> None:
    """A draw of -1 kW for 900 s warms a node of 1 MJ/K without flow or loss by 0.9 K, as a positive one cools it.

    The draw is signed like a circuit's heat, so the emitters of a cooling distribution system can hand the rooms' heat
    to the pipe water through it.
    """
    arguments: Dict[str, Any] = {
        "t0_c": 18.0,
        "inflows": [],
        "ua_w_per_k": 0.0,
        "t_amb_c": 20.0,
        "dt_s": 900.0,
        "heat_capacity_j_per_k": 1e6,
    }
    warmed = StartOfStepNode.step(heat_drawn_w=-1000.0, **arguments)
    cooled = StartOfStepNode.step(heat_drawn_w=1000.0, **arguments)
    assert warmed.heat_drawn_j == -9e5
    assert warmed.t_end_c == pytest.approx(18.9)
    assert cooled.t_end_c == pytest.approx(17.1)


def test_a_node_without_flow_loss_or_draw_keeps_its_temperature() -> None:
    """No flow, no loss and no draw leave the node where it started, with zero heats."""
    step = StartOfStepNode.step(
        t0_c=55.0, inflows=[Inflow(0.0, 80.0)], ua_w_per_k=0.0, t_amb_c=20.0, dt_s=900.0, heat_capacity_j_per_k=1e6
    )
    assert step.t_end_c == 55.0
    assert step.heat_in_j_per_inflow == (0.0,)
    assert step.loss_j == 0.0


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"ua_w_per_k": -1.0}, hydronics.NegativeHeatLossCoefficientError),
        ({"dt_s": 0.0}, hydronics.NonPositiveTimestepError),
        ({"heat_capacity_j_per_k": 0.0}, hydronics.NonPositiveHeatCapacityError),
        ({"t0_c": float("nan")}, hydronics.NonFiniteValueError),
        ({"heat_drawn_w": float("inf")}, hydronics.NonFiniteValueError),
        ({"inflows": [(0.1, 50.0)]}, hydronics.HydronicsError),
    ],
)
def test_invalid_arguments_are_refused(overrides: Dict[str, Any], error: type) -> None:
    """A negative loss coefficient, a non-positive step or capacity, a non-finite value or a bare tuple is refused."""
    arguments: Dict[str, Any] = {
        "t0_c": 40.0,
        "inflows": [Inflow(0.1, 50.0)],
        "ua_w_per_k": 2.0,
        "t_amb_c": 20.0,
        "dt_s": 900.0,
        "heat_capacity_j_per_k": 1e6,
    }
    arguments.update(overrides)
    with pytest.raises(error):
        StartOfStepNode.step(**arguments)
