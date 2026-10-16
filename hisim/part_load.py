"""Part load: the fraction of a coarse step a device runs, set by its L1 controller with a memoryless rule.

Above a step-length threshold (``SimulationParameters.part_load_above_seconds``, 600 s by default) a device that its
controller has switched on does not have to run the whole step: it runs only the fraction of the step, the part-load
ratio, that brings its store to the controller's target temperature by the end of the step (a circuit that draws
from a store, the largest fraction that keeps the store at or above a floor). At and below the threshold every device
runs whole steps, so a fine-step run is the reference without any adaptation.

The module holds five pieces, none of which knows a particular component. Which rule a controller uses depends on the
kind of store its device charges or draws from:

* a step-mean store, the hot-water cylinder (:class:`hisim.hydronics.MixedNode`), uses :class:`PartLoadRule` and
  :class:`PartLoadControl` (decision 11 of ``roadmap/hydronic_part_load_spec.md``): the boiler's hot-water charge and
  the solar collector's pump;
* a start-of-step store, the space-heating buffer (:class:`hisim.hydronics.StartOfStepNode`), uses
  :class:`StartOfStepPartLoadRule` and :class:`StartOfStepPartLoadControl` (decision 21): the boiler's and the heat
  pump's space-heating charge and the heat distribution system's circuit. The two rules read different inputs; a
  start-of-step store publishes its end temperature without each circuit, which makes its rules exact.

The pieces:

* :class:`PartLoadRule` is the pure rule for a step-mean store: from the ratio the device ran with and the store's
  temperatures at the start and at the end of the step it returns the ratio for the next pass. It remembers nothing;
  the simulator's passes iterate it like any other component's law.
* :class:`PartLoadControl` is the part of an L1 controller that reads the store's end-of-step temperature and the
  ratio its device reports, applies :class:`PartLoadRule` and publishes the ratio and the target it aims at.
* :class:`StartOfStepPartLoadRule` holds the exact rules for a start-of-step store: the ratio of a generator that
  ends the store at its target (``raising_ratio``) and the largest ratio of a distribution circuit that keeps the
  store at or above a floor (``lowering_ratio``). Each lands in one pass once the store's other circuits' ratios
  stand; the circuit ends of one store read each other's ratios of the pass before and settle together within a few
  passes.
* :class:`StartOfStepPartLoadControl` is the part of an L1 controller that reads a start-of-step store's end
  temperature, its end temperature without the device's circuit and the ratio the device reports, applies one of
  those rules and publishes the ratio (and, for a generator, the target).
* :class:`PartLoadCommand` is the part of a device that reads the ratio its controller commands and reports the
  ratio it ran with; devices on either kind of store use it.

Terms:

* **Part-load ratio**: the fraction of a step a device runs at full load, from 0 to 1. A device that runs a fraction
  of the step at full load publishes the averaged flow, the full-load flow times the ratio, at its full-load supply
  temperature, and books the ratio times its full-load heat.
* **Ratio run**: the part-load ratio the device actually ran with in a pass, which it publishes as an output. It is
  the commanded ratio while the device follows its controller, 1 when the device itself enforces a whole-step run
  against its controller (a minimum running time kept inside the device), and 0 while it does not run. A minimum
  running time kept by the controller, as the boiler's is, is not such a run: the controller keeps its device in the
  mode, and the rule's ratio decides how much of the step it fires, 0 for a store that starts at or above the target.
* **Target band**: the end-of-step temperatures the rule accepts, from the target to the target plus
  :attr:`PartLoadRule.TARGET_BAND_IN_KELVIN`. Landing at or just above the target makes the controller end the charge
  on the next step, as a fine-step run does.
"""

import math
from typing import ClassVar, Optional

from hisim import loadtypes as lt
from hisim.component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues


class PartLoadRule:
    """The rule an L1 controller sets its device's part-load ratio by, from the store's temperatures and the ratio run.

    The store's temperature at the start of the step stands in for its end temperature at ratio 0, so the line from
    ``(0, T_start)`` through ``(r_run, T_end)`` gives the next ratio where it meets the aim, the middle of the target
    band:

    ``r_next = r_run * (T_aim - T_start) / (T_end - T_start)``, clamped to [0, 1].

    Its fixed point is ``T_end = T_aim``, wherever the stand-in lies: ``T_start`` changes how fast the iteration gets
    there, not where it ends. Every end temperature inside the band is accepted as it is, so the ratio run is kept.
    Example: a tank that starts at 58.4 °C and ends at 63.5 °C with the whole step gets
    ``(60.025 - 58.4) / (63.5 - 58.4) = 0.319``.

    The cases the line cannot answer are decided explicitly (:meth:`next_part_load_ratio`): a store that starts at or
    above the target needs no heat (0); an end temperature at or below the start temperature, where the draw takes more
    than the device brings, gets the whole step (1); a device that ran nothing although the store ends below the target
    gets the whole step (1), so that the rule cannot stay at 0.

    The rule is a pure function of its arguments and keeps nothing between passes. Why: a component must be safe to
    iterate, its ``i_simulate`` a function of its inputs and its saved state only, so the simulator's plain iteration
    finds the ratio as it finds every other value of the step.
    """

    #: How far above its target a store may end the step for the rule to keep the ratio, in K. A one-minute run
    #: overshoots its target by about 0.2 K in the minute in which its controller sees it; the band stays below that,
    #: and wide against the float noise of the store's own iteration, so an accepted ratio stays accepted while the
    #: rest of the step converges.
    TARGET_BAND_IN_KELVIN: ClassVar[float] = 0.05

    @classmethod
    def aim_temperature_in_celsius(cls, target_temperature_in_celsius: float) -> float:
        """Return the end-of-step temperature the rule aims at, the middle of the target band, in °C.

        For example, a 60 °C target gives an aim of 60.025 °C. Aiming at the middle keeps a ratio that lands near the
        aim inside the band while the rest of the step converges.

        Args:
            target_temperature_in_celsius: The controller's target, in °C.

        Returns:
            The target plus half the band, in °C.
        """
        return target_temperature_in_celsius + cls.TARGET_BAND_IN_KELVIN / 2.0

    @classmethod
    def next_part_load_ratio(
        cls,
        *,
        ratio_run: float,
        start_temperature_in_celsius: float,
        end_temperature_in_celsius: float,
        target_temperature_in_celsius: float,
    ) -> float:
        """Return the part-load ratio for the next pass, from the ratio run and the store's start and end temperatures.

        The cases, in this order, the first that applies deciding:

        | case | next ratio |
        |---|---|
        | the store starts at or above the target | 0 |
        | the store ends inside the target band | the ratio run, unchanged |
        | the store ends at or below its start temperature (the draw dominates) | 1 |
        | the device ran nothing and the store ends below the target | 1 |
        | otherwise | ``r_run (T_aim - T_start) / (T_end - T_start)``, clamped to [0, 1] |

        Example: with a 60 °C target, a tank that starts at 58.4 °C and ends at 63.5 °C after a whole step gives
        0.319; one that ends at 57.0 °C gives 1; one that ends at 60.03 °C keeps the ratio it ran.

        Args:
            ratio_run: The part-load ratio the device reports it ran with, from 0 to 1.
            start_temperature_in_celsius: The store's temperature at the start of the step, in °C.
            end_temperature_in_celsius: The store's temperature at the end of the step, as the controller reads it in
                this pass, in °C.
            target_temperature_in_celsius: The store temperature at which the controller ends its device's charge,
                in °C.

        Returns:
            The part-load ratio to publish, from 0 to 1.

        Raises:
            ValueError: If a temperature is not finite or the ratio run lies outside [0, 1].
        """
        cls.check_finite("start_temperature_in_celsius", start_temperature_in_celsius)
        cls.check_finite("end_temperature_in_celsius", end_temperature_in_celsius)
        cls.check_finite("target_temperature_in_celsius", target_temperature_in_celsius)
        cls.validated_ratio(ratio_run, "The part-load rule's ratio run")
        if start_temperature_in_celsius >= target_temperature_in_celsius:
            return 0.0
        deviation_in_kelvin = end_temperature_in_celsius - target_temperature_in_celsius
        if 0.0 <= deviation_in_kelvin <= cls.TARGET_BAND_IN_KELVIN:
            return ratio_run
        rise_in_kelvin = end_temperature_in_celsius - start_temperature_in_celsius
        if rise_in_kelvin <= 0.0:
            return 1.0
        if ratio_run == 0.0 and deviation_in_kelvin < 0.0:
            return 1.0
        needed_in_kelvin = cls.aim_temperature_in_celsius(target_temperature_in_celsius) - start_temperature_in_celsius
        return min(1.0, max(0.0, ratio_run * needed_in_kelvin / rise_in_kelvin))

    @staticmethod
    def check_finite(name: str, value: float) -> None:
        """Refuse a temperature that is NaN or infinite, naming it.

        A NaN would pass every comparison of the rule as false and give a ratio nobody chose, so it is refused at the
        rule's boundary.

        Args:
            name: The argument's name, for the message.
            value: Its value.

        Raises:
            ValueError: If the value is not finite.
        """
        if not math.isfinite(value):
            raise ValueError(f"The part-load rule needs a finite {name}, not {value}.")

    @staticmethod
    def validated_ratio(value: float, name: str) -> float:
        """Return a part-load ratio as a float after checking that it lies from 0 to 1, naming it otherwise.

        Every place that reads or publishes a ratio (the rule's ratio run, the ratio a device reports, the ratio a
        controller commands) checks it the same way, so a device or wiring error stops the step with one message.
        For example, ``validated_ratio(0.4, "x")`` returns 0.4 and ``validated_ratio(1.2, "x")`` raises.

        Args:
            value: The ratio to check.
            name: What the ratio is, for the message, for example the full name of the port it was read from.

        Returns:
            The ratio as a float, from 0 to 1.

        Raises:
            ValueError: If the ratio is NaN or lies outside [0, 1].
        """
        ratio = float(value)
        if not 0.0 <= ratio <= 1.0:
            raise ValueError(f"{name}: a part-load ratio is a fraction from 0 to 1, not {ratio}.")
        return ratio


class PartLoadControl:
    """The part of an L1 controller that commands its device's part-load ratio and publishes its target.

    The controller decides as before whether its device runs; this part decides how much of the step it runs. It adds
    four channels to the controller: inputs for the store's end-of-step temperature and for the ratio the device
    reports it ran with, and outputs for the part-load ratio and for the target temperature the ratio aims at, the
    temperature at which the controller ends the charge. In every pass the controller publishes:

    * 0 while it does not run its device;
    * exactly 1 at and below the part-load threshold while it runs its device;
    * above the threshold, the ratio :class:`PartLoadRule` gives for the ratio run and the store's temperatures
      (:meth:`publish`); under ``force_convergence`` the ratio run itself (:meth:`publish_held`), so the device keeps
      what it ran and the iteration stops moving.

    The controller calls :meth:`publish` in every unforced pass and :meth:`publish_held` in every forced one.

    The part holds no state: everything it publishes follows from the inputs of the pass.
    """

    def __init__(
        self,
        controller: Component,
        *,
        end_temperature_input_name: str,
        ratio_run_input_name: str,
        ratio_output_name: str,
        target_output_name: str,
        device_description: str,
        controls_a_store: bool = True,
    ) -> None:
        """Add the end-temperature and ratio-run inputs and the ratio and target outputs to ``controller``.

        Args:
            controller: The L1 controller this part belongs to; its simulation parameters decide the threshold.
            end_temperature_input_name: The field name of the input that reads the store's end-of-step temperature.
            ratio_run_input_name: The field name of the input that reads the ratio the device ran with.
            ratio_output_name: The field name of the part-load ratio output.
            target_output_name: The field name of the target temperature output.
            device_description: How the output descriptions name the device and its store, for example "the boiler's
                hot-water charge".
            controls_a_store: Whether the controller is configured with the store, so the two inputs must be wired.
                A controller configured without it still publishes the ratio, always 0, for its device.
        """
        self.runs_part_load: bool = controller.my_simulation_parameters.runs_part_load()
        self.end_temperature_channel: ComponentInput = controller.add_input(
            controller.component_name,
            end_temperature_input_name,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            controls_a_store,
        )
        self.ratio_run_channel: ComponentInput = controller.add_input(
            controller.component_name,
            ratio_run_input_name,
            lt.LoadTypes.ANY,
            lt.Units.FRACTION,
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
                "threshold the part-load ratio is set so that the store ends the step at it or just above it (within "
                "the rule's target band), except on a step the simulator forces to converge."
            ),
        )

    def publish(
        self,
        stsv: SingleTimeStepValues,
        *,
        device_runs: bool,
        start_temperature_in_celsius: float,
        target_temperature_in_celsius: float,
    ) -> float:
        """Publish the part-load ratio and the target temperature of an unforced pass, and return the ratio.

        The ratio is 0 while the device does not run, 1 at and below the threshold while it runs, and above the
        threshold :meth:`PartLoadRule.next_part_load_ratio` of the ratio run and the store's end temperature read in
        this pass. For example, a running boiler at 900 s whose tank starts at 58.4 °C and is read at 63.5 °C after a
        whole step gets 0.319.

        Args:
            stsv: The step's values, to read the inputs from and to write the outputs to.
            device_runs: Whether the controller has its device running in this pass.
            start_temperature_in_celsius: The store's temperature at the start of the step, which the controller
                reads to decide, in °C.
            target_temperature_in_celsius: The store temperature at which the controller ends the charge, in °C.

        Returns:
            The published part-load ratio, from 0 to 1.
        """
        stsv.set_output_value(self.target_channel, target_temperature_in_celsius)
        if not device_runs:
            ratio = 0.0
        elif not self.runs_part_load:
            ratio = 1.0
        else:
            ratio = PartLoadRule.next_part_load_ratio(
                ratio_run=self.ratio_run(stsv),
                start_temperature_in_celsius=start_temperature_in_celsius,
                end_temperature_in_celsius=stsv.get_input_value(self.end_temperature_channel),
                target_temperature_in_celsius=target_temperature_in_celsius,
            )
        stsv.set_output_value(self.ratio_channel, ratio)
        return ratio

    def publish_held(self, stsv: SingleTimeStepValues) -> None:
        """Publish the part-load ratio of a pass under ``force_convergence``.

        Above the threshold the ratio becomes the ratio the device reports it ran with, so the device keeps running
        what it ran and the iteration stops moving; a device that did not run reports 0. At and below the threshold the
        ratio output keeps the value of the last unforced pass, as the controller's other outputs do, so a fine-step
        run computes exactly what it did without part load. The target output keeps its value either way.

        Args:
            stsv: The step's values, to read the ratio run from and to write the ratio to.
        """
        if not self.runs_part_load:
            return
        stsv.set_output_value(self.ratio_channel, self.ratio_run(stsv))

    def ratio_run(self, stsv: SingleTimeStepValues) -> float:
        """Return the part-load ratio the device reports it ran with, as read in this pass.

        The device publishes it as an ordinary output, so the controller reads it like any other port quantity; a
        value outside [0, 1] means a device or a wiring error and stops the step.

        Args:
            stsv: The step's values.

        Returns:
            The ratio run, from 0 to 1.

        Raises:
            ValueError: If the device reported a ratio outside [0, 1].
        """
        return PartLoadRule.validated_ratio(
            stsv.get_input_value(self.ratio_run_channel), f"{self.ratio_run_channel.fullname} (ratio run)"
        )


class PartLoadCommand:
    """The part of a device that reads the part-load ratio its L1 controller commands and reports the ratio it ran.

    A device whose controller runs it reads the ratio and, below 1, runs only that fraction of the step at full load:
    it publishes the averaged flow and books the ratio times its full-load heat. It reports the ratio it actually ran
    with as an output, a port quantity its controller reads: the commanded ratio while it follows its controller, 1 for
    a run it enforces itself against its controller (a minimum running time kept inside the device, which runs the
    whole step), and 0 while it does not run. A minimum running time its controller keeps is not such a run: the
    controller commands the rule's ratio for it like for any other step.

    A device may declare the ratio input optional, for a device that also runs without the controller that sends it;
    an unwired input commands the whole step (:meth:`ratio`).
    """

    def __init__(
        self,
        device: Component,
        *,
        input_name: str,
        ratio_run_output_name: str,
        device_description: str,
        mandatory: bool = True,
    ) -> None:
        """Add the part-load ratio input and the ratio-run output to ``device``.

        Args:
            device: The device that reads the ratio.
            input_name: The field name of its part-load ratio input.
            ratio_run_output_name: The field name of its ratio-run output.
            device_description: How the output description names the device's run, for example "the boiler's
                hot-water charge".
            mandatory: Whether the ratio input must be wired; an optional input that is not wired commands 1.
        """
        self.ratio_channel: ComponentInput = device.add_input(
            device.component_name, input_name, lt.LoadTypes.ANY, lt.Units.FRACTION, mandatory
        )
        self.ratio_run_channel: ComponentOutput = device.add_output(
            device.component_name,
            ratio_run_output_name,
            lt.LoadTypes.ANY,
            lt.Units.FRACTION,
            output_description=(
                f"The part-load ratio {device_description} ran with in this step: the fraction of the step it ran at "
                "full load. 0 while it does not run, 1 for the whole step."
            ),
        )

    def ratio(self, stsv: SingleTimeStepValues) -> float:
        """Return the commanded part-load ratio of this pass; 1, the whole step, while an optional input is unwired.

        Args:
            stsv: The step's values.

        Returns:
            The ratio, from 0 to 1.

        Raises:
            ValueError: If the controller sent a ratio outside [0, 1].
        """
        if self.ratio_channel.source_output is None:
            return 1.0
        return PartLoadRule.validated_ratio(stsv.get_input_value(self.ratio_channel), self.ratio_channel.fullname)

    def publish_ratio_run(self, stsv: SingleTimeStepValues, ratio_run: float) -> None:
        """Publish the part-load ratio the device ran with in this pass.

        Args:
            stsv: The step's values.
            ratio_run: The ratio the device ran with, from 0 to 1.

        Raises:
            ValueError: If the ratio lies outside [0, 1].
        """
        stsv.set_output_value(
            self.ratio_run_channel,
            PartLoadRule.validated_ratio(ratio_run, f"{self.ratio_run_channel.full_name} (ratio run)"),
        )


class StartOfStepPartLoadRule:
    """The exact part-load rules for a circuit end of a start-of-step store: one that raises it, one that lowers it.

    A start-of-step store (:class:`hisim.hydronics.StartOfStepNode`) books every circuit on its start temperature, so a
    circuit that runs a fraction ``r`` of the step changes the store's end temperature by exactly ``r`` times what the
    whole step would. The store publishes its end temperature ``T_end`` and, per circuit, its end temperature without
    that circuit ``T_wo``; with the ratio the circuit ran, ``r_run``, the end temperature at any ratio follows,
    ``T_wo + r (T_end − T_wo) / r_run``. The rules solve this line for the ratio, so a ratio lands in one pass where
    :class:`PartLoadRule`, which has to take the start temperature as the end temperature at ratio 0, alternates while
    another circuit draws from the store.

    * :meth:`raising_ratio`, for a generator: the ratio that ends the store at the aim, the middle of the target band.
      A store that starts at or above the target is charged too, when its draws would take it below the target by the
      end of the step: the store does not see its own fall within the step, so waiting for the next step lets it run
      dry first.
    * :meth:`lowering_ratio`, for a distribution circuit: the largest ratio that keeps the store's end temperature at
      or above a floor, the room temperature a store below which could not heat.

    Example: a buffer that would end at 38 °C with a boiler's whole step and at 26 °C without it, with a 32 °C target,
    gets ``(32.025 − 26) / (38 − 26) = 0.502``. A buffer that would end at 12 °C with the distribution system's whole
    step and at 30 °C without it, with a 20 °C floor, gets ``(30 − 20) / (30 − 12) = 0.556`` for the distribution.

    Both rules are pure functions of their arguments; nothing is kept between passes.
    """

    @classmethod
    def raising_ratio(
        cls,
        *,
        ratio_run: float,
        end_temperature_in_celsius: float,
        end_temperature_without_circuit_in_celsius: float,
        target_temperature_in_celsius: float,
    ) -> float:
        """Return the generator's ratio for the next pass: the one that ends the store at the aim.

        The cases, the first that applies deciding:

        | case | next ratio |
        |---|---|
        | the store ends at or above the target without the circuit | 0 |
        | the store ends inside the target band | the ratio run, unchanged |
        | the circuit ran nothing, or brought nothing | 1 |
        | otherwise | ``r_run (T_aim − T_wo) / (T_end − T_wo)``, clamped to [0, 1] |

        Args:
            ratio_run: The ratio the generator reports it ran with, from 0 to 1.
            end_temperature_in_celsius: The store's end temperature as read in this pass, in °C.
            end_temperature_without_circuit_in_celsius: The store's end temperature without this circuit, in °C.
            target_temperature_in_celsius: The store temperature at which the controller ends the charge, in °C.

        Returns:
            The ratio to publish, from 0 to 1.

        Raises:
            ValueError: If a temperature is not finite or the ratio run lies outside [0, 1].
        """
        cls.check(ratio_run, end_temperature_in_celsius, end_temperature_without_circuit_in_celsius)
        PartLoadRule.check_finite("target_temperature_in_celsius", target_temperature_in_celsius)
        if end_temperature_without_circuit_in_celsius >= target_temperature_in_celsius:
            return 0.0
        deviation_in_kelvin = end_temperature_in_celsius - target_temperature_in_celsius
        if 0.0 <= deviation_in_kelvin <= PartLoadRule.TARGET_BAND_IN_KELVIN:
            return ratio_run
        rise_in_kelvin = end_temperature_in_celsius - end_temperature_without_circuit_in_celsius
        if ratio_run == 0.0 or rise_in_kelvin <= 0.0:
            return 1.0
        needed_in_kelvin = (
            PartLoadRule.aim_temperature_in_celsius(target_temperature_in_celsius)
            - end_temperature_without_circuit_in_celsius
        )
        return min(1.0, max(0.0, ratio_run * needed_in_kelvin / rise_in_kelvin))

    @classmethod
    def lowering_ratio(
        cls,
        *,
        ratio_run: float,
        end_temperature_in_celsius: float,
        end_temperature_without_circuit_in_celsius: float,
        floor_temperature_in_celsius: float,
    ) -> float:
        """Return the distribution circuit's ratio for the next pass: the largest that keeps the store above the floor.

        The cases, the first that applies deciding:

        | case | next ratio |
        |---|---|
        | the store ends at or below the floor without the circuit | 0 |
        | the circuit ran nothing, or took nothing | 1 |
        | otherwise | ``r_run (T_wo − T_floor) / (T_wo − T_end)``, clamped to [0, 1] |

        A circuit whose whole step keeps the store above the floor gets 1.

        Args:
            ratio_run: The ratio the circuit reports it ran with, from 0 to 1.
            end_temperature_in_celsius: The store's end temperature as read in this pass, in °C.
            end_temperature_without_circuit_in_celsius: The store's end temperature without this circuit, in °C.
            floor_temperature_in_celsius: The lowest end temperature the circuit may leave the store at, in °C.

        Returns:
            The ratio to publish, from 0 to 1.

        Raises:
            ValueError: If a temperature is not finite or the ratio run lies outside [0, 1].
        """
        cls.check(ratio_run, end_temperature_in_celsius, end_temperature_without_circuit_in_celsius)
        PartLoadRule.check_finite("floor_temperature_in_celsius", floor_temperature_in_celsius)
        if end_temperature_without_circuit_in_celsius <= floor_temperature_in_celsius:
            return 0.0
        fall_in_kelvin = end_temperature_without_circuit_in_celsius - end_temperature_in_celsius
        if ratio_run == 0.0 or fall_in_kelvin <= 0.0:
            return 1.0
        allowed_in_kelvin = end_temperature_without_circuit_in_celsius - floor_temperature_in_celsius
        return min(1.0, max(0.0, ratio_run * allowed_in_kelvin / fall_in_kelvin))

    @staticmethod
    def check(
        ratio_run: float, end_temperature_in_celsius: float, end_temperature_without_circuit_in_celsius: float
    ) -> None:
        """Refuse a ratio run outside [0, 1] and a non-finite end temperature, naming the value.

        Args:
            ratio_run: The ratio the circuit reports it ran with.
            end_temperature_in_celsius: The store's end temperature, in °C.
            end_temperature_without_circuit_in_celsius: The store's end temperature without the circuit, in °C.

        Raises:
            ValueError: If one of them is invalid.
        """
        PartLoadRule.validated_ratio(ratio_run, "The start-of-step part-load rule's ratio run")
        PartLoadRule.check_finite("end_temperature_in_celsius", end_temperature_in_celsius)
        PartLoadRule.check_finite(
            "end_temperature_without_circuit_in_celsius", end_temperature_without_circuit_in_celsius
        )


class StartOfStepPartLoadControl:
    """The part of an L1 controller that sets the fraction of a coarse step its device runs on a start-of-step store.

    The controller decides as before whether its device runs; this part decides how much of the step, by
    :class:`StartOfStepPartLoadRule`. It adds three inputs to the controller (the store's end temperature, its end
    temperature without the device's circuit, and the ratio the device reports it ran) and a ratio output, plus, for a
    device that raises the store, a target output. In every pass it publishes:

    * 0 while the device does not run;
    * exactly 1 at and below the part-load threshold while it runs, and 1 when no store is wired to the inputs (a
      device feeding a consumer without a store in between runs whole steps);
    * above the threshold, the ratio of :meth:`StartOfStepPartLoadRule.raising_ratio` or
      :meth:`StartOfStepPartLoadRule.lowering_ratio`, at most the budget the controller allows;
    * under ``force_convergence``, above the threshold, the ratio the device reports it ran (:meth:`publish_held`).

    The inputs are optional, since a controller may run its device without a store. The part keeps no state.
    """

    def __init__(
        self,
        controller: Component,
        *,
        end_temperature_input_name: str,
        end_temperature_without_circuit_input_name: str,
        ratio_run_input_name: str,
        ratio_output_name: str,
        device_description: str,
        target_output_name: Optional[str] = None,
    ) -> None:
        """Add the three inputs, the ratio output and, if named, the target output to ``controller``.

        Args:
            controller: The L1 controller this part belongs to; its simulation parameters decide the threshold.
            end_temperature_input_name: The input that reads the store's end temperature.
            end_temperature_without_circuit_input_name: The input that reads the store's end temperature without
                the device's circuit.
            ratio_run_input_name: The input that reads the ratio the device ran with.
            ratio_output_name: The ratio output.
            device_description: How the output descriptions name the device's run, for example "the boiler's
                space-heating charge".
            target_output_name: The target output of a device that raises the store; None for one that lowers it.
        """
        self.runs_part_load: bool = controller.my_simulation_parameters.runs_part_load()
        self.end_temperature_channel: ComponentInput = controller.add_input(
            controller.component_name, end_temperature_input_name, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, False
        )
        self.end_temperature_without_circuit_channel: ComponentInput = controller.add_input(
            controller.component_name,
            end_temperature_without_circuit_input_name,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            False,
        )
        self.ratio_run_channel: ComponentInput = controller.add_input(
            controller.component_name, ratio_run_input_name, lt.LoadTypes.ANY, lt.Units.FRACTION, False
        )
        self.ratio_channel: ComponentOutput = controller.add_output(
            controller.component_name,
            ratio_output_name,
            lt.LoadTypes.ANY,
            lt.Units.FRACTION,
            output_description=(
                f"The part-load ratio of {device_description}: the fraction of the step it runs. 0 while it does not "
                "run, 1 for the whole step (always at and below the part-load threshold); above the threshold the "
                "ratio that keeps the store within its range by the end of the step."
            ),
        )
        self.target_channel: Optional[ComponentOutput] = None
        if target_output_name is not None:
            self.target_channel = controller.add_output(
                controller.component_name,
                target_output_name,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
                output_description=(
                    f"The store temperature {device_description} aims at: above the part-load threshold its ratio "
                    "ends the store at it or just above it, except on a step the simulator forces to converge."
                ),
            )

    def store_is_wired(self) -> bool:
        """Return whether a store feeds the end-temperature inputs, so the device can run part of a step."""
        return (
            self.end_temperature_channel.source_output is not None
            and self.end_temperature_without_circuit_channel.source_output is not None
        )

    def runs_fractions(self) -> bool:
        """Return whether this part sets fractions below 1: above the threshold, with a store wired."""
        return self.runs_part_load and self.store_is_wired()

    def publish_raising(
        self,
        stsv: SingleTimeStepValues,
        *,
        device_runs: bool,
        target_temperature_in_celsius: float,
        budget: float = 1.0,
    ) -> float:
        """Publish the ratio of a device that raises the store, and its target; return the ratio.

        Args:
            stsv: The step's values.
            device_runs: Whether the controller has its device running in this pass.
            target_temperature_in_celsius: The store temperature at which the controller ends the charge, in °C.
            budget: The largest fraction of the step left to this device, from 0 to 1.

        Returns:
            The published ratio, from 0 to 1.
        """
        if self.target_channel is not None:
            stsv.set_output_value(self.target_channel, target_temperature_in_celsius)
        ratio = 0.0
        if device_runs:
            ratio = 1.0
            if self.runs_fractions():
                ratio = StartOfStepPartLoadRule.raising_ratio(
                    ratio_run=self.ratio_run(stsv),
                    end_temperature_in_celsius=stsv.get_input_value(self.end_temperature_channel),
                    end_temperature_without_circuit_in_celsius=stsv.get_input_value(
                        self.end_temperature_without_circuit_channel
                    ),
                    target_temperature_in_celsius=target_temperature_in_celsius,
                )
            ratio = min(ratio, PartLoadRule.validated_ratio(budget, "The part-load budget"))
        stsv.set_output_value(self.ratio_channel, ratio)
        return ratio

    def publish_lowering(
        self, stsv: SingleTimeStepValues, *, device_runs: bool, floor_temperature_in_celsius: float
    ) -> float:
        """Publish the ratio of a device that lowers the store; return the ratio.

        Args:
            stsv: The step's values.
            device_runs: Whether the controller has its device running in this pass.
            floor_temperature_in_celsius: The lowest end temperature the device may leave the store at, in °C.

        Returns:
            The published ratio, from 0 to 1.
        """
        ratio = 0.0
        if device_runs:
            ratio = 1.0
            if self.runs_fractions():
                ratio = StartOfStepPartLoadRule.lowering_ratio(
                    ratio_run=self.ratio_run(stsv),
                    end_temperature_in_celsius=stsv.get_input_value(self.end_temperature_channel),
                    end_temperature_without_circuit_in_celsius=stsv.get_input_value(
                        self.end_temperature_without_circuit_channel
                    ),
                    floor_temperature_in_celsius=floor_temperature_in_celsius,
                )
        stsv.set_output_value(self.ratio_channel, ratio)
        return ratio

    def publish_held(self, stsv: SingleTimeStepValues) -> None:
        """Publish the ratio of a pass under ``force_convergence``: above the threshold, the ratio the device ran.

        At and below the threshold, or without a store, the ratio keeps the value of the last unforced pass, as the
        controller's other outputs do.

        Args:
            stsv: The step's values.
        """
        if self.runs_fractions():
            stsv.set_output_value(self.ratio_channel, self.ratio_run(stsv))

    def ratio_run(self, stsv: SingleTimeStepValues) -> float:
        """Return the ratio the device reports it ran with, as read in this pass.

        Args:
            stsv: The step's values.

        Returns:
            The ratio run, from 0 to 1.

        Raises:
            ValueError: If the device reported a ratio outside [0, 1].
        """
        return PartLoadRule.validated_ratio(
            stsv.get_input_value(self.ratio_run_channel), f"{self.ratio_run_channel.fullname} (ratio run)"
        )
