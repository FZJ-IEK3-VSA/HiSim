"""The exact part-load rules of a start-of-step store: a generator's raising ratio and a distribution's lowering ratio.

:class:`hisim.part_load.StartOfStepPartLoadRule` reads the store's end temperature, its end temperature without the
circuit and the ratio the circuit ran. The tests check every row of both tables, then iterate both rules together
against a real :class:`hisim.hydronics.StartOfStepNode` with a generator and a distribution circuit, as the simulator's
passes would: each pass reads only the values the store published in the pass before.
"""

from typing import ClassVar, List, Tuple

import pytest

from hisim import hydronics
from hisim.part_load import PartLoadRule, StartOfStepPartLoadRule

pytestmark = pytest.mark.base


class Store:

    """A 170 kg buffer at a 900 s step, between a generator and a distribution circuit, a namespace of helpers."""

    #: The buffer's heat capacity, in J/K: 170 kg of water.
    HEAT_CAPACITY_IN_JOULE_PER_KELVIN: ClassVar[float] = 170.0 * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K

    #: The step length, in s.
    SECONDS_PER_TIMESTEP: ClassVar[float] = 900.0

    #: The generator's full-step flow, in kg/s, and its lift, in K.
    GENERATOR_FLOW_IN_KG_PER_SECOND: ClassVar[float] = 0.369
    GENERATOR_LIFT_IN_KELVIN: ClassVar[float] = 5.0

    #: The distribution circuit's full-step flow, in kg/s.
    DISTRIBUTION_FLOW_IN_KG_PER_SECOND: ClassVar[float] = 0.27

    @classmethod
    def step(
        cls, *, start_in_celsius: float, generator_ratio: float, distribution_ratio: float, drop_in_kelvin: float
    ) -> Tuple[float, float, float]:
        """Return the store's end temperature, and its end temperatures without the generator and the distribution."""
        node = hydronics.StartOfStepNode.step(
            t0_c=start_in_celsius,
            inflows=[
                hydronics.Inflow(
                    generator_ratio * cls.GENERATOR_FLOW_IN_KG_PER_SECOND,
                    start_in_celsius + cls.GENERATOR_LIFT_IN_KELVIN,
                ),
                hydronics.Inflow(
                    distribution_ratio * cls.DISTRIBUTION_FLOW_IN_KG_PER_SECOND, start_in_celsius - drop_in_kelvin
                ),
            ],
            ua_w_per_k=0.0,
            t_amb_c=20.0,
            dt_s=cls.SECONDS_PER_TIMESTEP,
            heat_capacity_j_per_k=cls.HEAT_CAPACITY_IN_JOULE_PER_KELVIN,
        )
        generator_heat_in_joule, distribution_heat_in_joule = node.heat_in_j_per_inflow
        return (
            node.t_end_c,
            node.t_end_c - generator_heat_in_joule / cls.HEAT_CAPACITY_IN_JOULE_PER_KELVIN,
            node.t_end_c - distribution_heat_in_joule / cls.HEAT_CAPACITY_IN_JOULE_PER_KELVIN,
        )


@pytest.mark.parametrize(
    ("actual_ratio", "end", "without", "expected"),
    [
        (1.0, 38.0, 32.0, 0.0),  # the store reaches the target without the circuit
        (1.0, 40.0, 32.0, 0.0),  # exactly at the target without it: still 0
        (0.4, 32.03, 26.0, 0.4),  # inside the band: kept
        (0.0, 20.0, 20.0, 1.0),  # ran nothing
        (0.5, 25.0, 26.0, 1.0),  # brought nothing (a rise of zero or less)
        (1.0, 38.0, 26.0, (32.025 - 26.0) / 12.0),  # the line
        (0.2, 27.0, 26.0, 1.0),  # the line clamped to 1
    ],
)
def test_the_raising_ratio_follows_its_table(actual_ratio: float, end: float, without: float, expected: float) -> None:
    """Every case of the generator's rule, with a 32 °C target."""
    assert StartOfStepPartLoadRule.raising_ratio(
        actual_ratio=actual_ratio,
        end_temperature_in_celsius=end,
        end_temperature_without_circuit_in_celsius=without,
        target_temperature_in_celsius=32.0,
    ) == pytest.approx(expected)


def test_a_store_starting_above_its_target_is_charged_when_its_draw_would_take_it_below() -> None:
    """The rule reads no start temperature: a store at 33 °C that its draw would leave at 26 °C still gets charged."""
    ratio = StartOfStepPartLoadRule.raising_ratio(
        actual_ratio=1.0,
        end_temperature_in_celsius=38.0,
        end_temperature_without_circuit_in_celsius=26.0,
        target_temperature_in_celsius=32.0,
    )
    assert 0.0 < ratio < 1.0


@pytest.mark.parametrize(
    ("actual_ratio", "end", "without", "expected"),
    [
        (1.0, 25.0, 20.0, 0.0),  # without the circuit the store already ends at the floor
        (1.0, 25.0, 19.0, 0.0),  # or below it
        (0.0, 30.0, 30.0, 1.0),  # ran nothing
        (1.0, 30.0, 30.0, 1.0),  # took nothing
        (1.0, 25.0, 30.0, 1.0),  # the whole step keeps the store above the floor
        (1.0, 12.0, 30.0, 10.0 / 18.0),  # the line
        (0.5, 21.0, 30.0, 0.5 * 10.0 / 9.0),  # the line from a part ratio
    ],
)
def test_the_lowering_ratio_follows_its_table(actual_ratio: float, end: float, without: float, expected: float) -> None:
    """Every case of the distribution's rule, with a 20 °C floor."""
    assert StartOfStepPartLoadRule.lowering_ratio(
        actual_ratio=actual_ratio,
        end_temperature_in_celsius=end,
        end_temperature_without_circuit_in_celsius=without,
        floor_temperature_in_celsius=20.0,
    ) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("rule", "name"),
    [(StartOfStepPartLoadRule.raising_ratio, "target"), (StartOfStepPartLoadRule.lowering_ratio, "floor")],
)
def test_an_actual_ratio_outside_zero_to_one_is_refused(rule, name: str) -> None:
    """An actual ratio outside [0, 1] is refused by both rules.

    A non-finite temperature is not checked here: the rules run on every pass, and a NaN or infinite value is caught
    once after the run by the check of the result columns (hisim/postprocessing/finite_results.py).
    """
    limit = {f"{name}_temperature_in_celsius": 20.0}
    with pytest.raises(ValueError):
        rule(actual_ratio=1.5, end_temperature_in_celsius=30.0, end_temperature_without_circuit_in_celsius=25.0, **limit)


@pytest.mark.parametrize(
    ("start_in_celsius", "drop_in_kelvin", "target_in_celsius", "floor_in_celsius", "binding"),
    [
        (31.0, 5.0, 32.0, 20.0, "aim"),  # a charge near the target against a draw
        (33.0, 6.0, 32.0, 20.0, "aim"),  # a store above its target that the draw would empty: charged as well
        (23.0, 14.0, 32.0, 20.0, "floor"),  # a drained store: the generator at full ratio, the draw cut at the floor
        (40.0, 2.0, 32.0, 20.0, "none"),  # a full store with a small draw: no charge, the whole draw
    ],
)
def test_both_rules_together_settle_within_a_few_passes_inside_the_store_s_range(
    start_in_celsius: float, drop_in_kelvin: float, target_in_celsius: float, floor_in_celsius: float, binding: str
) -> None:
    """Iterated as the simulator's passes would, both ratios settle and the store ends between the floor and the aim.

    The store's end temperature is linear in each ratio, so each rule is exact given the other's ratio of the pass
    before; together they settle within a handful of passes. ``binding`` names the limit the case settles on:

    * ``"aim"``: the generator runs a fraction (0.837 for 31 °C and a 5 K drop) and the store ends at the aim,
      32.025 °C; the draw runs whole.
    * ``"floor"``: the generator runs whole and the draw is cut (0.638 for 23 °C and a 14 K drop) so that the store
      ends at the 20 °C floor.
    * ``"none"``: the generator does not run and the draw runs whole.
    """
    generator_ratio, distribution_ratio = 1.0, 1.0
    history: List[Tuple[float, float]] = []
    for _ in range(12):
        end, without_generator, without_distribution = Store.step(
            start_in_celsius=start_in_celsius,
            generator_ratio=generator_ratio,
            distribution_ratio=distribution_ratio,
            drop_in_kelvin=drop_in_kelvin,
        )
        generator_ratio, distribution_ratio = (
            StartOfStepPartLoadRule.raising_ratio(
                actual_ratio=generator_ratio,
                end_temperature_in_celsius=end,
                end_temperature_without_circuit_in_celsius=without_generator,
                target_temperature_in_celsius=target_in_celsius,
            ),
            StartOfStepPartLoadRule.lowering_ratio(
                actual_ratio=distribution_ratio,
                end_temperature_in_celsius=end,
                end_temperature_without_circuit_in_celsius=without_distribution,
                floor_temperature_in_celsius=floor_in_celsius,
            ),
        )
        history.append((generator_ratio, distribution_ratio))
    assert history[-1] == pytest.approx(history[-2], abs=1e-9)
    end, _, _ = Store.step(
        start_in_celsius=start_in_celsius,
        generator_ratio=generator_ratio,
        distribution_ratio=distribution_ratio,
        drop_in_kelvin=drop_in_kelvin,
    )
    assert end >= floor_in_celsius - 1e-9
    assert end <= max(start_in_celsius, PartLoadRule.aim_temperature_in_celsius(target_in_celsius)) + 1e-9
    if binding == "aim":
        assert 0.0 < generator_ratio < 1.0
        assert distribution_ratio == 1.0
        assert end == pytest.approx(PartLoadRule.aim_temperature_in_celsius(target_in_celsius), abs=1e-6)
    elif binding == "floor":
        assert generator_ratio == 1.0
        assert 0.0 < distribution_ratio < 1.0
        assert end == pytest.approx(floor_in_celsius, abs=1e-6)
    else:
        assert generator_ratio == 0.0
        assert distribution_ratio == 1.0
