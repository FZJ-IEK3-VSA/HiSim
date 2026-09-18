"""The one shared ``get_main_classname`` resolves ``MAIN_CLASS`` and refuses what it cannot resolve.

Every converted configuration class names its component in one line, ``MAIN_CLASS``, and the
base class turns that line into the component's fully qualified name. Three things have to hold
for the name serialized scenarios and postprocessing spell a component by to stay right: the
returned string is the component's own ``get_full_classname()`` (so a package that pins a shorter
``__module__`` and one that does not both come out under the path HiSim uses), a class that
declares nothing is refused by name, and a misspelt path is refused as a ``ValueError`` naming the
class and the path rather than as a bare import error.

The first of those refusals happens twice over, and both are pinned here: a class body that
declares neither ``MAIN_CLASS`` nor its own ``get_main_classname`` never becomes a class at all,
and a class that reaches the resolver unset anyway — by overriding the method and delegating back
up — still gets the ``NotImplementedError`` that names it.
"""

from dataclasses import dataclass
from typing import ClassVar

import pytest

from hisim.components.building import Building
from hisim.components.building.config import BuildingConfig
from hisim.components.generic_pv_system import PVSystem, PVSystemConfig
from hisim.config import ComponentID, ConfigBase


class Cases:
    """The paths the tests compare against, spelled once."""

    #: A component its package re-exports under the short path and pins ``__module__`` to.
    PINNED_SHORT_PATH: ClassVar[str] = "hisim.components.generic_pv_system.PVSystem"
    #: A component whose package re-exports it but leaves ``__module__`` on the defining module.
    DEFINING_MODULE_PATH: ClassVar[str] = "hisim.components.building.building.Building"


@pytest.mark.base
def test_a_pinned_reexport_resolves_to_the_short_path() -> None:
    """Catches the resolver echoing a path other than the one the component itself spells."""
    assert PVSystemConfig.MAIN_CLASS == Cases.PINNED_SHORT_PATH
    assert PVSystemConfig.get_main_classname() == Cases.PINNED_SHORT_PATH
    assert PVSystemConfig.get_main_classname() == PVSystem.get_full_classname()


@pytest.mark.base
def test_an_unpinned_reexport_resolves_to_the_defining_module() -> None:
    """Catches the building's declared path and its resolved path drifting apart again.

    The building package does not pin ``Building.__module__``, so the component spells itself by
    its defining module and 31 recorded twins spell it the same way; ``MAIN_CLASS`` states that
    path so the declaration reads as what the method returns.
    """
    assert BuildingConfig.MAIN_CLASS == Cases.DEFINING_MODULE_PATH
    assert BuildingConfig.get_main_classname() == Cases.DEFINING_MODULE_PATH
    assert BuildingConfig.get_main_classname() == Building.get_full_classname()


@pytest.mark.base
def test_a_class_that_declares_nothing_is_refused_at_its_definition() -> None:
    """Catches a config without an identity being accepted until something serializes it.

    The class body below is the whole test: ``ConfigBase.__init_subclass__`` runs while it is
    turned into a class, finds neither ``MAIN_CLASS`` nor an own ``get_main_classname``, and
    refuses by name. Before the check the same class was built happily and only failed at the
    first dump, a twin recording or a cost lookup away from the line that forgot to declare.
    """
    with pytest.raises(TypeError, match="NamelessConfig"):

        @dataclass
        class NamelessConfig(ConfigBase):  # pylint: disable=unused-variable
            """A configuration that neither declares MAIN_CLASS nor overrides the method."""


@pytest.mark.base
def test_a_class_that_reaches_the_resolver_unset_is_refused_by_name() -> None:
    """Catches the resolver's own refusal losing the class name it reports.

    A class definition cannot reach the resolver unset any more, so the backstop is exercised
    the one way left: a class that declares its own ``get_main_classname``, which satisfies the
    definition-time check, and delegates straight back to the base implementation with
    ``MAIN_CLASS`` still empty.
    """

    @dataclass
    class DelegatingConfig(ConfigBase):
        """A configuration whose override hands the question back to the base class."""

        @classmethod
        def get_main_classname(cls) -> str:
            """Delegates to the base resolver, which has nothing to resolve."""
            return super().get_main_classname()

    with pytest.raises(NotImplementedError, match="DelegatingConfig"):
        DelegatingConfig.get_main_classname()


@pytest.mark.base
@pytest.mark.parametrize(
    "main_class",
    [
        "NoDotsHere",
        "hisim.components.no_such_module.Thing",
        "hisim.components.generic_pv_system.NoSuchClass",
    ],
    ids=["no-module", "missing-module", "missing-attribute"],
)
def test_a_path_that_cannot_be_resolved_is_a_value_error_naming_the_path(main_class: str) -> None:
    """Catches a misspelt MAIN_CLASS surfacing as a bare ImportError or AttributeError."""

    @dataclass
    class MisspeltConfig(ConfigBase):
        """A configuration whose one line points nowhere."""

        MAIN_CLASS: ClassVar[str] = main_class

    with pytest.raises(ValueError, match="MisspeltConfig") as refusal:
        MisspeltConfig.get_main_classname()
    assert main_class in str(refusal.value)


@pytest.mark.base
def test_a_resolved_config_still_builds() -> None:
    """Catches the base-class change breaking construction of an ordinary converted config."""
    config = PVSystemConfig.preset_rooftop("PV")
    assert config.component_id == ComponentID(name="PV")
    assert config.get_main_classname() == Cases.PINNED_SHORT_PATH
