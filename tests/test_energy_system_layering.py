"""The one architectural invariant that only an import graph can show: components stay below files.

An aggregating component declares which flows it accepts, and a dynamic component creates the
ports a resolved feed asks for. Both need types the energy-system format also uses, and the
tempting place to put those types is with the format — which would make importing *any* component
execute the format's package, and with it the model layer, its validation library and its YAML
reader. Nothing would break; every component import would simply become slower and every
dependency of the file format would become a dependency of the component tree.

So the shared types live in :mod:`hisim.config.channels`, below both, and the rule that keeps them
there is checked here rather than left to a review. The check runs in a fresh interpreter, because
the question is what a *first* import pulls in and a test process has long since imported
everything.
"""

# clean

import subprocess
import sys
from typing import ClassVar, Tuple

import pytest


class Layering:
    """The modules whose import graph is pinned, and what must stay out of it."""

    #: The component-side modules that use the channel declarations: the base class every
    #: aggregator derives from, and the two aggregators HiSim ships.
    IMPORTERS: ClassVar[Tuple[str, ...]] = (
        "hisim.dynamic_component",
        "hisim.components.electricity_meter",
        "hisim.components.controller_l2_energy_management_system",
    )

    #: What none of them may drag in. The first is the rule itself; the other two are what the
    #: rule exists to keep out, and naming them makes a failure say why it matters.
    FORBIDDEN: ClassVar[Tuple[str, ...]] = ("hisim.energy_system", "pydantic", "yaml")

    @classmethod
    def loaded_modules(cls, module: str) -> Tuple[str, ...]:
        """Imports one module in a fresh interpreter and reports what that pulled in.

        Args:
            module: The dotted module path to import.

        Returns:
            Every module name in the child interpreter's module table afterwards.

        Raises:
            AssertionError: If the child interpreter could not import the module at all.
        """
        program = f"import {module}, sys; print('\\n'.join(sorted(sys.modules)))"
        finished = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, check=False
        )
        assert finished.returncode == 0, f"importing {module} failed: {finished.stderr}"
        return tuple(finished.stdout.split())


@pytest.mark.base
@pytest.mark.parametrize("module", Layering.IMPORTERS)
def test_importing_a_component_does_not_import_the_energy_system_format(module: str) -> None:
    """Catches a channel type drifting back up into the file format's package.

    The failure to expect is an import added for convenience — an error class, a helper, a type
    alias — that reaches from a component module into ``hisim.energy_system`` and thereby makes
    the whole file-format stack a dependency of every simulation HiSim runs.
    """
    loaded = Layering.loaded_modules(module)

    offenders = [
        name
        for name in loaded
        for forbidden in Layering.FORBIDDEN
        if name == forbidden or name.startswith(f"{forbidden}.")
    ]

    assert offenders == [], f"importing {module} pulled in {sorted(set(offenders))}"


@pytest.mark.base
def test_the_channel_types_are_importable_from_the_format_as_well() -> None:
    """Catches the move breaking the spelling the matcher and its tests already use.

    The declarations moved; where they are imported from did not have to. Both spellings resolve
    to the same objects, which is what makes the move invisible to everything but the import graph.
    """
    from hisim.config import channels as leaf  # noqa: PLC0415  (the point of the test)
    from hisim.energy_system import channels as matcher  # noqa: PLC0415
    from hisim.energy_system import resolution  # noqa: PLC0415

    assert matcher.DynamicConnectionChannel is leaf.DynamicConnectionChannel
    assert matcher.DispatchRule is leaf.DispatchRule
    assert resolution.ResolvedDynamicConnection is leaf.ResolvedDynamicConnection
    assert resolution.ResolvedDynamicWire is leaf.ResolvedDynamicWire


@pytest.mark.base
def test_a_channel_that_cannot_mean_anything_is_refused_at_declaration() -> None:
    """Catches the declaration checks being lost in the move to the leaf.

    They are what turns a mistyped aggregator declaration into a failure in that component's own
    test rather than into a silently unmatched participant in somebody's simulation months later.
    """
    from hisim import loadtypes as lt  # noqa: PLC0415  (one test only)
    from hisim.config.channels import (  # noqa: PLC0415
        ChannelDeclarationError,
        DispatchRule,
        DynamicConnectionChannel,
    )

    with pytest.raises(ChannelDeclarationError, match="declares no tags"):
        DynamicConnectionChannel(
            key="anything",
            tags=frozenset(),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        )

    with pytest.raises(ChannelDeclarationError, match="forbids dispatch"):
        DynamicConnectionChannel(
            key="contradiction",
            tags=frozenset({lt.ComponentType.BATTERY}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
            dispatch_tags=frozenset({lt.InandOutputType.ELECTRICITY_TARGET}),
        )
