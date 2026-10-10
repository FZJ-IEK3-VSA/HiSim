"""Part load: the fraction of a coarse step a device runs, set by its L1 controller with a memoryless rule.

Above a step-length threshold (``SimulationParameters.part_load_above_seconds``, 600 s by default) a device that its
controller has switched on does not have to run the whole step: it runs only the fraction of the step, the part-load
ratio, that brings its store to the controller's target temperature by the end of the step. At and below the
threshold every device runs whole steps, so a fine-step run is the reference without any adaptation.

The module holds three pieces, none of which knows a particular component:

* :class:`PartLoadRule` is the pure rule: from the ratio the device ran with and the store's temperatures at the
  start and at the end of the step it returns the ratio for the next pass. It remembers nothing; the simulator's
  passes iterate it like any other component's law.
* :class:`PartLoadControl` is the part of an L1 controller that reads the store's end-of-step temperature and the
  ratio its device reports, applies the rule and publishes the ratio and the target it aims at.
* :class:`PartLoadCommand` is the part of a device that reads the ratio its controller commands and reports the
  ratio it ran with.

Terms:

* **Part-load ratio**: the fraction of a step a device runs at full load, from 0 to 1. A device that runs a fraction
  of the step at full load publishes the averaged flow, the full-load flow times the ratio, at its full-load supply
  temperature, and books the ratio times its full-load heat.
* **Ratio run**: the part-load ratio the device actually ran with in a pass, which it publishes as an output. It is
  the commanded ratio while the device follows its controller, 1 when the device runs a whole step on its own (a
  minimum running time), and 0 while it does not run.
* **Target band**: the end-of-step temperatures the rule accepts, from the target to the target plus
  :attr:`PartLoadRule.TARGET_BAND_IN_KELVIN`. Landing at or just above the target makes the controller end the charge
  on the next step, as a fine-step run does.
"""

from typing import ClassVar

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
        if not 0.0 <= ratio_run <= 1.0:
            raise ValueError(f"The part-load rule needs a ratio run from 0 to 1, not {ratio_run}.")
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
        if not abs(value) < float("inf"):
            raise ValueError(f"The part-load rule needs a finite {name}, not {value}.")


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
                "threshold the part-load ratio is set so that the store ends the step at it."
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
        ratio_run = float(stsv.get_input_value(self.ratio_run_channel))
        if not 0.0 <= ratio_run <= 1.0:
            raise ValueError(
                f"{self.ratio_run_channel.fullname}: a part-load ratio run is a fraction from 0 to 1, not {ratio_run}."
            )
        return ratio_run


class PartLoadCommand:
    """The part of a device that reads the part-load ratio its L1 controller commands and reports the ratio it ran.

    A device whose controller runs it reads the ratio and, below 1, runs only that fraction of the step at full load:
    it publishes the averaged flow and books the ratio times its full-load heat. It reports the ratio it actually ran
    with as an output, a port quantity its controller reads: the commanded ratio while it follows its controller, 1 for
    a run it enforces itself against its controller (a minimum running time, which runs the whole step), and 0 while it
    does not run.
    """

    def __init__(self, device: Component, *, input_name: str, ratio_run_output_name: str, device_description: str) -> None:
        """Add the part-load ratio input and the ratio-run output to ``device``.

        Args:
            device: The device that reads the ratio.
            input_name: The field name of its part-load ratio input.
            ratio_run_output_name: The field name of its ratio-run output.
            device_description: How the output description names the device's run, for example "the boiler's
                hot-water charge".
        """
        self.ratio_channel: ComponentInput = device.add_input(
            device.component_name, input_name, lt.LoadTypes.ANY, lt.Units.FRACTION, True
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

    def publish_ratio_run(self, stsv: SingleTimeStepValues, ratio_run: float) -> None:
        """Publish the part-load ratio the device ran with in this pass.

        Args:
            stsv: The step's values.
            ratio_run: The ratio the device ran with, from 0 to 1.

        Raises:
            ValueError: If the ratio lies outside [0, 1].
        """
        if not 0.0 <= ratio_run <= 1.0:
            raise ValueError(
                f"{self.ratio_run_channel.full_name}: a part-load ratio run is a fraction from 0 to 1, not {ratio_run}."
            )
        stsv.set_output_value(self.ratio_run_channel, ratio_run)
