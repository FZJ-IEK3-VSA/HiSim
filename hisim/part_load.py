"""Part load: the fraction of a coarse step a device runs, regulated by its L1 controller.

Above a step-length threshold (``SimulationParameters.part_load_above_seconds``, 600 s by default) a device that its
controller has switched on does not have to run the whole step: it runs only the fraction of the step, the part-load
ratio, that brings its store to the controller's target temperature by the end of the step. At and below the
threshold every device runs whole steps, so a fine-step run is the reference without any adaptation.

The module holds three pieces, none of which knows a particular component:

* :class:`PartLoadRatioRegulator` searches the ratio. A store's end-of-step temperature rises with the ratio of a
  device that heats it, so the ratio that ends the step in a narrow band above the target is the root of a monotone
  function on [0, 1]. The regulator searches it trial by trial across the simulator's passes of the step and holds
  its trial under ``force_convergence``.
* :class:`PartLoadControl` is the part of an L1 controller that reads the store's end-of-step temperature, runs the
  regulator and publishes the ratio and the target it aims at.
* :class:`PartLoadCommand` is the part of a device that reads the ratio its controller commands.

Terms:

* **Part-load ratio**: the fraction of a step a device runs at full load, from 0 to 1. A device that runs a fraction
  of the step at full load publishes the averaged flow, the full-load flow times the ratio, at its full-load supply
  temperature, and books the ratio times its full-load heat.
* **Target band**: the end-of-step temperatures the regulator accepts, from the target to the target plus
  :attr:`PartLoadRatioRegulator.TARGET_BAND_IN_KELVIN`. Landing at or just above the target makes the controller end
  the charge on the next step, as a fine-step run does.
"""

import dataclasses
import enum
from dataclasses import dataclass
from typing import ClassVar, Optional

from hisim import loadtypes as lt
from hisim.component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues


class BracketSide(str, enum.Enum):
    """The end of the regulator's bracket a trial replaced: the lower end leaves the store too cold, the upper too hot."""

    LOWER = "lower"
    UPPER = "upper"


@dataclass(frozen=True)
class BracketEnd:
    """One end of the regulator's bracket: a ratio that was tried and the store temperature it gave.

    Attributes:
        part_load_ratio: The ratio that was tried, from 0 to 1.
        end_temperature_in_celsius: The store's end-of-step temperature the simulator returned for it, in °C.
        weight: The Illinois weight of this end, 1 when it was set and halved each time the other end moved twice in
            a row; it scales this end's deviation in the interpolation.
    """

    part_load_ratio: float
    end_temperature_in_celsius: float
    weight: float = 1.0


@dataclass(frozen=True)
class PartLoadSearch:
    """What the regulator remembers of its search within one step: the current trial and the bracket around the root.

    A step starts from :meth:`PartLoadRatioRegulator.fresh_search`: the whole step (ratio 1) as the first trial and no
    bracket. The memory spans the simulator's passes of one step and is discarded at the next step's start.

    Attributes:
        trial_ratio: The ratio the controller publishes, from 0 to 1.
        passes_with_trial: In how many passes before this one the current trial has been published.
        passes_in_step: How many passes of the step came before this one, those in which the device did not run
            included.
        lower: A tried ratio that left the store below the target band, or ``None`` before one is known.
        upper: A tried ratio that left the store above the target band, or ``None`` before one is known.
        previous_upper: The upper end the current one replaced, or ``None``; with the upper end it shapes the
            estimate below them while no ratio is known to leave the store too cold.
        last_moved: The bracket end the last trial replaced, for the Illinois weighting, or ``None``.
        trials: How many trials the search has published in this step, the first included.
        estimates_below_hot_trials: How many trials were estimated below too-hot trials from the start temperature.
        is_settled: Whether the search has stopped: the bracket shrank to nothing or the trials ran out.
    """

    trial_ratio: float = 1.0
    passes_with_trial: int = 0
    passes_in_step: int = 0
    lower: Optional[BracketEnd] = None
    upper: Optional[BracketEnd] = None
    previous_upper: Optional[BracketEnd] = None
    last_moved: Optional[BracketSide] = None
    trials: int = 1
    estimates_below_hot_trials: int = 0
    is_settled: bool = False


class PartLoadRatioRegulator:
    """Iterate the part-load ratio that ends a store's step in a narrow band above its target temperature.

    The store's end-of-step temperature rises with the ratio of a device that heats it, so the ratio that ends the
    step in the target band is the root of a rising function on [0, 1]. The regulator searches it trial by trial:

    1. The first trial is the whole step. A store that ends at or below the target band with it keeps the whole step.
    2. While every trial so far has ended the store too hot, the next trial is estimated below them from the store's
       start temperature, which stands in for its end temperature without the device (the store does not publish
       that): on the straight line from the start temperature at ratio 0 through the one too-hot trial, or on the
       parabola through the start temperature and the two latest too-hot trials, which follows the store's flattening
       rise with the ratio. After :attr:`MAXIMUM_ESTIMATES_BELOW_HOT_TRIALS` such estimates the trial is 0.
    3. Once one trial ended the store too cold and one too hot, every trial interpolates between the closest of each
       (regula falsi, with the Illinois weighting that halves the weight of an end that stays twice in a row, so the
       search converges superlinearly).

    The search aims at the middle of the band and accepts any end temperature inside it.

    Example: a hot-water tank that starts the step at 58.4 °C and ends it at 63.5 °C with the whole step gets the
    second trial ``(60.025 - 58.4) / (63.5 - 58.4) = 0.319``.

    A trial is read only once the store has answered it. In one pass the simulator evaluates every component once, in
    the order they were added, so the end temperature the controller reads in a pass was computed from its device's
    flow of the previous pass or of the one before, depending on that order. A trial is therefore published in
    :attr:`PASSES_PER_TRIAL` passes before its answer is read, and no answer is read before the step has run
    :attr:`PASSES_BEFORE_FIRST_READING` passes. The search thus never pairs a trial with the answer to another one, and
    where it lands does not depend on the evaluation order.

    The memory lives for the passes of one step: the controller calls :meth:`reset` at the start of every step
    (``i_save_state``), so a step never inherits a ratio from the step before, and :meth:`restart` in a pass in which
    its device does not run. This memory across the passes of a step is what lets the controller regulate at all; the
    ratio the search settles on depends only on the store's answer.
    """

    #: How far above its target a store may end the step for the search to accept the ratio, in K. A one-minute run
    #: overshoots its target by about 0.2 K in the minute in which its controller sees it; the band stays below that,
    #: and wide against the float noise of the store's own iteration, so an accepted ratio stays accepted while the
    #: rest of the step converges.
    TARGET_BAND_IN_KELVIN: ClassVar[float] = 0.05

    #: The passes a trial is published in before the store's answer to it is read. In two passes the answer reaches
    #: the controller whatever order the controller, its device and the store are evaluated in.
    PASSES_PER_TRIAL: ClassVar[int] = 2

    #: The passes of a step before the first one whose reading is an answer. In the first pass a controller evaluated
    #: before the store reads the store's values of the step before, so the device and the store answer the step's own
    #: start one pass later, and the controller reads that answer one or two passes after that.
    PASSES_BEFORE_FIRST_READING: ClassVar[int] = 3

    #: How many trials in a row may be estimated below too-hot trials before the search tries 0. A store whose end
    #: temperature without the device is far below its start temperature (a large draw) can need two or three.
    MAXIMUM_ESTIMATES_BELOW_HOT_TRIALS: ClassVar[int] = 4

    #: The most trials one step may take; the search then keeps its last trial. The simulator forces convergence on
    #: the passes after the twelfth, in which the controller keeps its trial, so more trials could not be read anyway.
    MAXIMUM_TRIALS: ClassVar[int] = 8

    #: The width below which the bracket counts as closed, as a ratio. A store that ends below the band at one end and
    #: above it at the other of so narrow a bracket has a jump there; the search then keeps the upper end.
    CLOSED_BRACKET_WIDTH: ClassVar[float] = 1e-9

    def __init__(self) -> None:
        """Create a regulator at the start of a step, with the whole step as its first trial."""
        self.search: PartLoadSearch = self.fresh_search()

    @staticmethod
    def fresh_search(passes_in_step: int = 0) -> PartLoadSearch:
        """Return the memory of a search that has not started: the whole step as the trial and no bracket.

        Args:
            passes_in_step: How many passes of the step have run already.

        Returns:
            The search, starting in the given pass of the step.
        """
        return PartLoadSearch(passes_in_step=passes_in_step)

    def reset(self) -> None:
        """Start a new search for a new step."""
        self.search = self.fresh_search()

    def restart(self) -> None:
        """Discard the search after a pass in which the device did not run, and count that pass of the step."""
        self.search = self.fresh_search(self.search.passes_in_step + 1)

    def held_ratio(self) -> float:
        """Return the current trial unchanged, for a pass under ``force_convergence``.

        Returns:
            The part-load ratio of the current trial, from 0 to 1.
        """
        return self.search.trial_ratio

    def next_ratio(
        self,
        *,
        start_temperature_in_celsius: float,
        end_temperature_in_celsius: float,
        target_temperature_in_celsius: float,
    ) -> float:
        """Advance the search by one simulator pass and return the ratio to publish in it.

        Args:
            start_temperature_in_celsius: The store's temperature at the start of the step, °C.
            end_temperature_in_celsius: The store's end-of-step temperature the controller reads in this pass, °C.
            target_temperature_in_celsius: The temperature the controller ends its device's charge at, °C.

        Returns:
            The part-load ratio to publish in this pass, from 0 to 1.
        """
        self.search = self.advanced_search(
            self.search,
            start_temperature_in_celsius=start_temperature_in_celsius,
            end_temperature_in_celsius=end_temperature_in_celsius,
            target_temperature_in_celsius=target_temperature_in_celsius,
        )
        return self.search.trial_ratio

    @classmethod
    def advanced_search(
        cls,
        search: PartLoadSearch,
        *,
        start_temperature_in_celsius: float,
        end_temperature_in_celsius: float,
        target_temperature_in_celsius: float,
    ) -> PartLoadSearch:
        """Return the search after one more simulator pass, given the store's temperatures read in that pass.

        A trial the store has not answered yet (:meth:`is_answered`) is kept, and so is a trial whose answer lies in
        the target band. An answer below or above the band replaces the bracket's lower or upper end
        (:meth:`bracket_with_reading`), and :meth:`with_next_trial` chooses the next trial.

        Example: after three passes at ratio 1, a reading of 63.47 °C against a 60 °C target and a start temperature of
        58.41 °C give the next trial ``(60.025 - 58.41) / (63.47 - 58.41) = 0.3192``.

        Args:
            search: The search before this pass.
            start_temperature_in_celsius: The store's temperature at the start of the step, °C.
            end_temperature_in_celsius: The store's end-of-step temperature read in this pass, °C.
            target_temperature_in_celsius: The temperature the controller ends its device's charge at, °C.

        Returns:
            The search after this pass; its ``trial_ratio`` is the ratio to publish.

        Raises:
            ValueError: If a temperature is not finite.
        """
        cls.check_finite("start_temperature_in_celsius", start_temperature_in_celsius)
        cls.check_finite("end_temperature_in_celsius", end_temperature_in_celsius)
        cls.check_finite("target_temperature_in_celsius", target_temperature_in_celsius)
        kept = dataclasses.replace(
            search, passes_with_trial=search.passes_with_trial + 1, passes_in_step=search.passes_in_step + 1
        )
        if search.is_settled or not cls.is_answered(search):
            return kept
        deviation_in_kelvin = end_temperature_in_celsius - target_temperature_in_celsius
        if 0.0 <= deviation_in_kelvin <= cls.TARGET_BAND_IN_KELVIN:
            return kept
        aim_temperature_in_celsius = cls.aim_temperature_in_celsius(target_temperature_in_celsius)
        bracketed = cls.bracket_with_reading(
            kept,
            end_temperature_in_celsius=end_temperature_in_celsius,
            is_too_cold=deviation_in_kelvin < 0.0,
            aim_temperature_in_celsius=aim_temperature_in_celsius,
        )
        return cls.with_next_trial(
            bracketed,
            aim_temperature_in_celsius=aim_temperature_in_celsius,
            start_temperature_in_celsius=start_temperature_in_celsius,
        )

    @classmethod
    def is_answered(cls, search: PartLoadSearch) -> bool:
        """Return whether the store's end temperature read in this pass is its answer to the current trial.

        It is once the trial has been published in :attr:`PASSES_PER_TRIAL` passes before this one and the step has run
        :attr:`PASSES_BEFORE_FIRST_READING` passes before this one. Example: a device that runs from the step's first
        pass has its first trial read in the fourth pass; one that runs from the second pass, too.

        Args:
            search: The search before this pass.

        Returns:
            Whether the reading may be paired with the current trial.
        """
        return (
            search.passes_with_trial >= cls.PASSES_PER_TRIAL
            and search.passes_in_step >= cls.PASSES_BEFORE_FIRST_READING
        )

    @classmethod
    def aim_temperature_in_celsius(cls, target_temperature_in_celsius: float) -> float:
        """Return the end temperature the search aims at, the middle of the target band, in °C.

        Args:
            target_temperature_in_celsius: The controller's target, °C.

        Returns:
            The target plus half the band, °C.
        """
        return target_temperature_in_celsius + cls.TARGET_BAND_IN_KELVIN / 2.0

    @staticmethod
    def bracket_with_reading(
        search: PartLoadSearch, *, end_temperature_in_celsius: float, is_too_cold: bool, aim_temperature_in_celsius: float
    ) -> PartLoadSearch:
        """Return the search with the current trial as the new lower (too cold) or upper (too hot) bracket end.

        The other end's Illinois weight is halved when the same end moves twice in a row. An end that no longer lies
        on its side of the aim (the target moved, or another device on the same store changed its ratio) is dropped,
        and so is the older end when the store no longer rises with the ratio between the two, so the bracket always
        encloses the aim.

        Args:
            search: The search before the reading.
            end_temperature_in_celsius: The store's end temperature for the current trial, °C.
            is_too_cold: Whether that temperature lies below the target band.
            aim_temperature_in_celsius: The middle of the target band, °C.

        Returns:
            The search with its bracket updated; the trial is not yet replaced.
        """
        new_end = BracketEnd(search.trial_ratio, end_temperature_in_celsius)
        side = BracketSide.LOWER if is_too_cold else BracketSide.UPPER
        lower, upper, previous_upper = search.lower, search.upper, search.previous_upper
        if side == BracketSide.LOWER:
            lower = new_end
            if search.last_moved == BracketSide.LOWER and upper is not None:
                upper = dataclasses.replace(upper, weight=upper.weight / 2.0)
        else:
            previous_upper, upper = upper, new_end
            if search.last_moved == BracketSide.UPPER and lower is not None:
                lower = dataclasses.replace(lower, weight=lower.weight / 2.0)
        if lower is not None and lower.end_temperature_in_celsius >= aim_temperature_in_celsius:
            lower = None
        if upper is not None and upper.end_temperature_in_celsius <= aim_temperature_in_celsius:
            upper = None
        if lower is not None and upper is not None and lower.part_load_ratio >= upper.part_load_ratio:
            lower, upper = (lower, None) if side == BracketSide.LOWER else (None, upper)
        return dataclasses.replace(search, lower=lower, upper=upper, previous_upper=previous_upper, last_moved=side)

    @classmethod
    def with_next_trial(
        cls, search: PartLoadSearch, *, aim_temperature_in_celsius: float, start_temperature_in_celsius: float
    ) -> PartLoadSearch:
        """Return the search with its next trial, or with its trial kept when no other ratio does better.

        * Without a too-hot end: the whole step, kept when it was the trial already.
        * Without a too-cold end: :meth:`ratio_below_hot_trials`, up to :attr:`MAXIMUM_ESTIMATES_BELOW_HOT_TRIALS`
          times; after that 0, kept when it was the trial already.
        * With both ends: the Illinois interpolation :meth:`interpolated_ratio`.

        The search settles, keeping its trial, when the bracket has closed (on the upper end) or the trials ran out.

        Args:
            search: The search with its bracket updated by the latest reading.
            aim_temperature_in_celsius: The middle of the target band, °C.
            start_temperature_in_celsius: The store's temperature at the start of the step, °C.

        Returns:
            The search with the trial to publish next.
        """
        lower, upper = search.lower, search.upper
        if upper is None:
            return cls.with_trial(search, 1.0)
        if lower is None:
            if search.estimates_below_hot_trials >= cls.MAXIMUM_ESTIMATES_BELOW_HOT_TRIALS:
                return cls.with_trial(search, 0.0)
            ratio = cls.ratio_below_hot_trials(
                upper,
                search.previous_upper,
                aim_temperature_in_celsius=aim_temperature_in_celsius,
                start_temperature_in_celsius=start_temperature_in_celsius,
            )
            return dataclasses.replace(
                cls.with_trial(search, ratio), estimates_below_hot_trials=search.estimates_below_hot_trials + 1
            )
        if upper.part_load_ratio - lower.part_load_ratio <= cls.CLOSED_BRACKET_WIDTH:
            return dataclasses.replace(cls.with_trial(search, upper.part_load_ratio), is_settled=True)
        next_trial = cls.with_trial(
            search, cls.interpolated_ratio(lower, upper, aim_temperature_in_celsius=aim_temperature_in_celsius)
        )
        if next_trial.trials >= cls.MAXIMUM_TRIALS:
            return dataclasses.replace(next_trial, is_settled=True)
        return next_trial

    @staticmethod
    def with_trial(search: PartLoadSearch, part_load_ratio: float) -> PartLoadSearch:
        """Return the search publishing ``part_load_ratio``: a new trial from this pass on, or the same one kept.

        Args:
            search: The search before the choice.
            part_load_ratio: The ratio to publish, from 0 to 1.

        Returns:
            The search with that trial; a new trial counts one pass and one more trial.
        """
        if part_load_ratio == search.trial_ratio:
            return search
        return dataclasses.replace(search, trial_ratio=part_load_ratio, passes_with_trial=1, trials=search.trials + 1)

    @classmethod
    def ratio_below_hot_trials(
        cls,
        upper: BracketEnd,
        previous_upper: Optional[BracketEnd],
        *,
        aim_temperature_in_celsius: float,
        start_temperature_in_celsius: float,
    ) -> float:
        """Return a trial below the too-hot trials, estimated with the store's start temperature at ratio 0.

        With two too-hot trials the estimate is the parabola through the start temperature at 0 and both trials
        (:meth:`parabola_ratio`); with one, or where the parabola gives no ratio below the latest trial, it is the
        straight line from the start temperature through the latest trial (:meth:`line_ratio`).

        Args:
            upper: The latest too-hot trial.
            previous_upper: The too-hot trial before it, or ``None``.
            aim_temperature_in_celsius: The middle of the target band, °C.
            start_temperature_in_celsius: The store's temperature at the start of the step, °C.

        Returns:
            A ratio from 0 to below the latest too-hot trial's ratio.
        """
        line = cls.line_ratio(
            upper, aim_temperature_in_celsius=aim_temperature_in_celsius, start_temperature_in_celsius=start_temperature_in_celsius
        )
        if previous_upper is None:
            return line
        parabola = cls.parabola_ratio(
            upper,
            previous_upper,
            aim_temperature_in_celsius=aim_temperature_in_celsius,
            start_temperature_in_celsius=start_temperature_in_celsius,
        )
        return line if parabola is None else parabola

    @staticmethod
    def line_ratio(upper: BracketEnd, *, aim_temperature_in_celsius: float, start_temperature_in_celsius: float) -> float:
        """Return the ratio where the line from the start temperature at ratio 0 through a too-hot trial meets the aim.

        A store that starts at or above the aim gives 0.

        Example: the start at 58.41 °C, the whole step ending at 63.47 °C and the aim at 60.025 °C give 0.3192.

        Args:
            upper: The too-hot trial.
            aim_temperature_in_celsius: The middle of the target band, °C.
            start_temperature_in_celsius: The store's temperature at the start of the step, °C.

        Returns:
            A ratio from 0 to below the trial's ratio.
        """
        rise_in_kelvin = upper.end_temperature_in_celsius - start_temperature_in_celsius
        needed_in_kelvin = aim_temperature_in_celsius - start_temperature_in_celsius
        if needed_in_kelvin <= 0.0 or rise_in_kelvin <= needed_in_kelvin:
            return 0.0
        return upper.part_load_ratio * needed_in_kelvin / rise_in_kelvin

    @staticmethod
    def parabola_ratio(
        upper: BracketEnd,
        previous_upper: BracketEnd,
        *,
        aim_temperature_in_celsius: float,
        start_temperature_in_celsius: float,
    ) -> Optional[float]:
        """Return where the parabola through the start temperature at 0 and two trials meets the aim, if below both.

        The parabola is ``T(r) = T_start + b r + c r**2``; its root at the aim is taken on its rising branch. ``None``
        when the two trials have the same ratio, when the parabola does not reach the aim, or when its root does not
        lie strictly between 0 and the latest trial's ratio.

        Example: the start at 48.27 °C, the whole step ending at 63.64 °C and the ratio 0.7635 ending at 61.50 °C give,
        for the aim at 60.025 °C, about 0.641.

        Args:
            upper: The latest too-hot trial.
            previous_upper: The too-hot trial before it.
            aim_temperature_in_celsius: The middle of the target band, °C.
            start_temperature_in_celsius: The store's temperature at the start of the step, °C.

        Returns:
            The ratio, or ``None``.
        """
        ratio_1, rise_1_in_kelvin = upper.part_load_ratio, upper.end_temperature_in_celsius - start_temperature_in_celsius
        ratio_2 = previous_upper.part_load_ratio
        rise_2_in_kelvin = previous_upper.end_temperature_in_celsius - start_temperature_in_celsius
        if ratio_1 <= 0.0 or ratio_2 <= 0.0 or ratio_1 == ratio_2:
            return None
        curvature = (rise_2_in_kelvin / ratio_2 - rise_1_in_kelvin / ratio_1) / (ratio_2 - ratio_1)
        slope = rise_1_in_kelvin / ratio_1 - curvature * ratio_1
        needed_in_kelvin = aim_temperature_in_celsius - start_temperature_in_celsius
        if curvature == 0.0:
            ratio = needed_in_kelvin / slope if slope > 0.0 else -1.0
        else:
            discriminant = slope * slope + 4.0 * curvature * needed_in_kelvin
            if discriminant < 0.0:
                return None
            ratio = (-slope + discriminant**0.5) / (2.0 * curvature)
        if not 0.0 < ratio < ratio_1:
            return None
        return ratio

    @staticmethod
    def interpolated_ratio(lower: BracketEnd, upper: BracketEnd, *, aim_temperature_in_celsius: float) -> float:
        """Return the ratio where the line through the weighted bracket ends meets the aim temperature.

        Each end's deviation from the aim is scaled by its Illinois weight, and the root of the line through the two
        weighted deviations is taken. A root on or outside the bracket (rounding at a nearly closed bracket) gives
        the bracket's midpoint instead, so the next trial always lies strictly inside it.

        Example: the lower end 0 at 54.9 °C and the upper end 1 at 62.0 °C, both at weight 1, with the aim at
        60.025 °C give ``5.125 / 7.1 = 0.7218``.

        Args:
            lower: The bracket end that left the store below the aim.
            upper: The bracket end that left the store above the aim.
            aim_temperature_in_celsius: The middle of the target band, °C.

        Returns:
            A ratio strictly between the two ends' ratios.
        """
        lower_deviation_in_kelvin = lower.weight * (lower.end_temperature_in_celsius - aim_temperature_in_celsius)
        upper_deviation_in_kelvin = upper.weight * (upper.end_temperature_in_celsius - aim_temperature_in_celsius)
        width = upper.part_load_ratio - lower.part_load_ratio
        ratio = lower.part_load_ratio - lower_deviation_in_kelvin * width / (
            upper_deviation_in_kelvin - lower_deviation_in_kelvin
        )
        if not lower.part_load_ratio < ratio < upper.part_load_ratio:
            return lower.part_load_ratio + width / 2.0
        return ratio

    @staticmethod
    def check_finite(name: str, value: float) -> None:
        """Refuse a temperature that is NaN or infinite, naming it.

        Args:
            name: The argument's name, for the message.
            value: Its value.

        Raises:
            ValueError: If the value is not finite.
        """
        if not abs(value) < float("inf"):
            raise ValueError(f"The part-load search needs a finite {name}, not {value}.")


class PartLoadControl:
    """The part of an L1 controller that commands its device's part-load ratio and publishes its target.

    The controller decides as before whether its device runs; this part decides how much of the step it runs. It adds
    three channels to the controller: an input for the store's end-of-step temperature, an output for the part-load
    ratio and an output for the target temperature the ratio aims at, the temperature at which the controller ends
    the charge. In every pass the controller calls :meth:`publish`:

    * a device that does not run gets the ratio 0, and the search restarts when it runs again;
    * at and below the part-load threshold a running device gets exactly 1, the whole step;
    * above it the ratio comes from a :class:`PartLoadRatioRegulator`, which keeps its trial under
      ``force_convergence``.

    The controller calls :meth:`start_step` in ``i_save_state``, so every step starts a fresh search.
    """

    def __init__(
        self,
        controller: Component,
        *,
        end_temperature_input_name: str,
        ratio_output_name: str,
        target_output_name: str,
        device_description: str,
        controls_a_store: bool = True,
    ) -> None:
        """Add the end-temperature input and the ratio and target outputs to ``controller``.

        Args:
            controller: The L1 controller this part belongs to; its simulation parameters decide the threshold.
            end_temperature_input_name: The field name of the input that reads the store's end-of-step temperature.
            ratio_output_name: The field name of the part-load ratio output.
            target_output_name: The field name of the target temperature output.
            device_description: How the output descriptions name the device and its store, for example "the boiler's
                hot-water charge".
            controls_a_store: Whether the controller is configured with the store, so the end-temperature input must
                be wired. A controller configured without it still publishes the ratio, always 0, for its device.
        """
        self.runs_part_load: bool = controller.my_simulation_parameters.runs_part_load()
        self.regulator = PartLoadRatioRegulator()
        self.end_temperature_channel: ComponentInput = controller.add_input(
            controller.component_name,
            end_temperature_input_name,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            controls_a_store,
        )
        self.ratio_channel: ComponentOutput = controller.add_output(
            controller.component_name,
            ratio_output_name,
            lt.LoadTypes.ANY,
            lt.Units.FRACTION,
            output_description=(
                f"The part-load ratio of {device_description}: the fraction of the step it runs at full load. 0 while "
                "it does not run, 1 for the whole step (always at and below the part-load threshold); above the "
                "threshold the ratio that ends the store's step at the target."
            ),
        )
        self.target_channel: ComponentOutput = controller.add_output(
            controller.component_name,
            target_output_name,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=(
                f"The store temperature at which the controller ends {device_description}. Above the part-load "
                "threshold the part-load ratio is iterated so that the store ends the step at it."
            ),
        )

    def start_step(self) -> None:
        """Discard the last step's search, so the new step starts from the whole step."""
        self.regulator.reset()

    def publish(
        self,
        stsv: SingleTimeStepValues,
        *,
        device_runs: bool,
        start_temperature_in_celsius: float,
        target_temperature_in_celsius: float,
        force_convergence: bool,
    ) -> float:
        """Publish this pass's part-load ratio and target temperature, and return the ratio.

        Args:
            stsv: The step's values, to read the store's end temperature from and to write the outputs to.
            device_runs: Whether the controller has its device running in this pass.
            start_temperature_in_celsius: The store's temperature at the start of the step, which the controller
                reads to decide, °C.
            target_temperature_in_celsius: The store temperature at which the controller ends the charge, °C.
            force_convergence: Whether the simulator forces this pass to converge; the search then keeps its trial.

        Returns:
            The published part-load ratio, from 0 to 1.
        """
        stsv.set_output_value(self.target_channel, target_temperature_in_celsius)
        ratio = self.part_load_ratio(
            stsv,
            device_runs=device_runs,
            start_temperature_in_celsius=start_temperature_in_celsius,
            target_temperature_in_celsius=target_temperature_in_celsius,
            force_convergence=force_convergence,
        )
        stsv.set_output_value(self.ratio_channel, ratio)
        return ratio

    def part_load_ratio(
        self,
        stsv: SingleTimeStepValues,
        *,
        device_runs: bool,
        start_temperature_in_celsius: float,
        target_temperature_in_celsius: float,
        force_convergence: bool,
    ) -> float:
        """Return the part-load ratio of this pass: 0 when off, 1 at and below the threshold, else the search's trial.

        Args:
            stsv: The step's values, to read the store's end temperature from.
            device_runs: Whether the controller has its device running in this pass.
            start_temperature_in_celsius: The store's temperature at the start of the step, °C.
            target_temperature_in_celsius: The store temperature at which the controller ends the charge, °C.
            force_convergence: Whether the simulator forces this pass to converge.

        Returns:
            The part-load ratio, from 0 to 1.
        """
        if not device_runs:
            self.regulator.restart()
            return 0.0
        if not self.runs_part_load:
            return 1.0
        if force_convergence:
            return self.regulator.held_ratio()
        return self.regulator.next_ratio(
            start_temperature_in_celsius=start_temperature_in_celsius,
            end_temperature_in_celsius=stsv.get_input_value(self.end_temperature_channel),
            target_temperature_in_celsius=target_temperature_in_celsius,
        )


class PartLoadCommand:
    """The part of a device that reads the part-load ratio its L1 controller commands.

    A device whose controller runs it reads the ratio and, below 1, runs only that fraction of the step at full load:
    it publishes the averaged flow and books the ratio times its full-load heat. A run the device enforces itself
    against its controller (a minimum running time) runs the whole step, since the controller commanded no fraction
    for it.
    """

    def __init__(self, device: Component, *, input_name: str) -> None:
        """Add the part-load ratio input to ``device``.

        Args:
            device: The device that reads the ratio.
            input_name: The field name of its part-load ratio input.
        """
        self.ratio_channel: ComponentInput = device.add_input(
            device.component_name, input_name, lt.LoadTypes.ANY, lt.Units.FRACTION, True
        )

    def ratio(self, stsv: SingleTimeStepValues) -> float:
        """Return the commanded part-load ratio of this pass.

        Args:
            stsv: The step's values.

        Returns:
            The ratio, from 0 to 1.

        Raises:
            ValueError: If the controller sent a ratio outside [0, 1].
        """
        ratio = float(stsv.get_input_value(self.ratio_channel))
        if not 0.0 <= ratio <= 1.0:
            raise ValueError(
                f"{self.ratio_channel.fullname}: a part-load ratio is a fraction from 0 to 1, not {ratio}."
            )
        return ratio
