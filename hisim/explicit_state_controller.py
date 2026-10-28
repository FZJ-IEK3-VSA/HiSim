"""The opt-in base class of a controller that declares its whole memory as one immutable state.

:class:`ExplicitStateController` owns a controller's lifecycle methods (``i_simulate``, ``i_save_state``,
``i_restore_state``): the controller names its memory as a ``NamedTuple`` and implements ``initial_state``, ``decide``
and ``publish``, and the rule for forced passes is code in this one place. It lives in its own module so that
:mod:`hisim.component`, which every component imports, stays the home of the kind base classes only.
"""

from __future__ import annotations

import typing
from typing import Any, ClassVar, Tuple

from hisim import config as cfg
from hisim.component import Component, SingleTimeStepValues
from hisim.simulationparameters import SimulationParameters


#: The type of a controller's state in :class:`ExplicitStateController`: a NamedTuple, so immutable and cheap to build.
ControllerStateT = typing.TypeVar("ControllerStateT", bound=tuple)


class ExplicitStateController(Component, typing.Generic[ControllerStateT]):
    """A controller that declares its whole memory as one immutable state and leaves the lifecycle to this class.

    The subclass names its memory, a ``NamedTuple``, and implements three methods: :meth:`initial_state`,
    :meth:`decide`, which reads the inputs of a pass and returns the next state without assigning anything on
    ``self``, and :meth:`publish`, which writes the outputs that belong to a state. This class owns the three lifecycle
    methods the simulator calls (:meth:`i_simulate`, :meth:`i_save_state`, :meth:`i_restore_state`) and keeps two
    references:

    * :attr:`state_at_step_start`, the state every unforced pass decides from;
    * :attr:`state_of_last_free_pass`, the state the last unforced pass decided, which a forced pass republishes and
      the next step starts from.

    A forced pass (``force_convergence``) does not decide: it publishes :attr:`state_of_last_free_pass` again, so the
    state a step commits is always the state whose outputs the controller published. Save and restore are reference
    assignments, so the convergence loop does no extra work.

    Use it beside the kind base, which it is not: ``class GenericBoilerController(
    ExplicitStateController[GenericBoilerControllerState], L1ControllerComponent)``. Why: with hand-written save,
    restore and forcing, every controller had to follow an unwritten rule, and a forced step could commit a state
    that did not belong to the outputs it held. Here the rule is code in one place, and a test can check every
    subclass for memory outside the state (``tests/test_explicit_state_controllers.py``).
    """

    #: The lifecycle methods this class owns; a subclass that defines one of them is refused (:meth:`__init_subclass__`).
    OWNED_LIFECYCLE_METHODS: ClassVar[Tuple[str, ...]] = ("i_simulate", "i_save_state", "i_restore_state")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Refuse a subclass that defines one of the lifecycle methods this class owns.

        For example, a subclass with its own ``i_restore_state`` raises when the class is defined, at import, not at
        the first forced step. ``typing.final`` makes mypy report the same override; this check also catches code
        mypy does not see.

        Args:
            kwargs: Passed on to the next ``__init_subclass__``.

        Raises:
            TypeError: If the subclass defines a method of :attr:`OWNED_LIFECYCLE_METHODS`.
        """
        super().__init_subclass__(**kwargs)
        overridden = [name for name in ExplicitStateController.OWNED_LIFECYCLE_METHODS if name in vars(cls)]
        if overridden:
            raise TypeError(
                f"{cls.__module__}.{cls.__name__} defines {overridden}, which ExplicitStateController owns: implement "
                "decide and publish instead, and keep all memory in the state."
            )

    def __init__(
        self,
        name: str,
        my_simulation_parameters: SimulationParameters,
        my_config: cfg.ConfigBase,
        my_display_config: cfg.DisplayConfig,
    ) -> None:
        """Initialize the component and set both state references to :meth:`initial_state`.

        :meth:`initial_state` runs at the end of this method, so it may read ``self.config`` and
        ``self.my_simulation_parameters``, but nothing the subclass sets after calling ``super().__init__``.

        Args:
            name: The component's runtime name (:meth:`Component.__init__`).
            my_simulation_parameters: The simulation-wide parameters.
            my_config: The component's configuration.
            my_display_config: How the component is presented in postprocessing.
        """
        super().__init__(
            name=name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=my_config,
            my_display_config=my_display_config,
        )
        initial_state = self.initial_state()
        #: The state every unforced pass of the current step decides from: the committed state of the step before.
        self.state_at_step_start: ControllerStateT = initial_state
        #: The state the last unforced pass decided; forced passes publish it, and the next step starts from it.
        self.state_of_last_free_pass: ControllerStateT = initial_state

    def initial_state(self) -> ControllerStateT:
        """Return the state of the controller before the first step.

        Returns:
            The initial state.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement initial_state.")

    def decide(self, *, timestep: int, stsv: SingleTimeStepValues, state: ControllerStateT) -> ControllerStateT:
        """Read the inputs of a pass and return the next state, which includes the decision.

        It must not assign anything on ``self``: every value that lives beyond the pass belongs in the returned state.

        Args:
            timestep: The step.
            stsv: The step's values, to read the inputs from. It must not write outputs; :meth:`publish` does.
            state: The state at the start of the step.

        Returns:
            The new state.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement decide.")

    def publish(self, *, stsv: SingleTimeStepValues, state: ControllerStateT) -> None:
        """Write the outputs that belong to ``state``.

        Args:
            stsv: The step's values, to write the outputs to.
            state: The state whose outputs are written.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement publish.")

    def publish_forced(self, *, stsv: SingleTimeStepValues, state: ControllerStateT) -> None:
        """Write the outputs of a forced pass; by default exactly the outputs of :meth:`publish`.

        A subclass overrides it only where a forced pass publishes something other than its held decision, for
        example a part-load ratio that becomes the ratio the device reports it ran, so the iteration stops moving. It
        writes outputs only; the state stays ``state``.

        Args:
            stsv: The step's values.
            state: The state of the last unforced pass.
        """
        self.publish(stsv=stsv, state=state)

    @typing.final
    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Decide from the state at the start of the step and publish, or, in a forced pass, republish the last decision.

        Args:
            timestep: The step.
            stsv: The step's values.
            force_convergence: Whether the simulator forces the step; the controller then does not decide.
        """
        if force_convergence:
            self.publish_forced(stsv=stsv, state=self.state_of_last_free_pass)
        else:
            self.state_of_last_free_pass = self.decide(timestep=timestep, stsv=stsv, state=self.state_at_step_start)
            self.publish(stsv=stsv, state=self.state_of_last_free_pass)

    @typing.final
    def i_save_state(self) -> None:
        """Commit the state of the last unforced pass as the state the new step starts from."""
        self.state_at_step_start = self.state_of_last_free_pass

    @typing.final
    def i_restore_state(self) -> None:
        """Do nothing: every unforced pass decides from :attr:`state_at_step_start`, which no pass changes."""
