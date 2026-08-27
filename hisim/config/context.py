"""The sizing context: the facts about the surrounding system that sizing laws read.

This module holds the two halves of the sizing-fact vocabulary, deliberately side by
side in one file:
:class:`SizingContext`, the frozen snapshot whose fields *are* the facts, and
:class:`Size`, the expression terms over exactly those fields. Keeping them together is
what makes the single-registry invariant — one term per fact, no drift — reviewable at
a glance; ``tests/test_sizing.py`` asserts it mechanically.

Per the ``hisim.config`` layering rule the module imports nothing from the rest of
HiSim at module level. This module carries one of the package's two sanctioned
exceptions (the other being ``hisim.log``, see the package ``__init__``):
:meth:`SizingContext.for_building` imports the building package inside the method
body — the building physics is what turns a ``BuildingConfig`` into sizing facts, and
the building package necessarily imports ``ConfigBase`` from this package, so a
module-level import here would close the cycle.
"""

# clean

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Optional

from hisim.config.laws import _FactTerm

if TYPE_CHECKING:
    from hisim.components.building.config import BuildingConfig
    from hisim.components.heat_distribution_system import HeatDistributionSystemType


@dataclass(frozen=True)
class SizingContext:
    """The facts about the surrounding system that sizing laws may read.

    A small frozen snapshot, scope-resolved: today per building via
    :meth:`for_building`, later per unit/apartment via a ``for_unit`` constructor —
    components never slice building structure themselves. All fields are optional; a law
    reading an absent fact fails precisely, naming field and fact. Facts that derive
    from *sibling components* rather than the building (a boiler controller's power
    band) are added by the setup via :meth:`with_facts`.
    """

    heating_load_in_watt: Optional[float] = None
    heating_reference_temperature_in_celsius: Optional[float] = None
    number_of_apartments: Optional[float] = None
    conditioned_floor_area_in_m2: Optional[float] = None
    water_mass_flow_rate_in_kg_per_second: Optional[float] = None
    heat_distribution_system_type: Optional["HeatDistributionSystemType"] = None
    number_of_residents: Optional[float] = None
    maximal_thermal_power_in_watt: Optional[float] = None
    minimal_thermal_power_in_watt: Optional[float] = None
    set_heating_temperature_in_celsius: Optional[float] = None
    set_cooling_temperature_in_celsius: Optional[float] = None

    def with_facts(self, **facts: Any) -> "SizingContext":
        """Returns a copy of this context with the given facts added or replaced.

        This is how a setup enriches the building-derived context with facts only it
        knows — typically values derived from sibling components, like the power band of
        the boiler a controller belongs to.
        """
        return dataclasses.replace(self, **facts)

    @classmethod
    def for_building(cls, building_config: "BuildingConfig") -> "SizingContext":
        """Derives the building-scope facts from a building configuration.

        Runs the TABULA/EPISCOPE lookup (``BuildingInformation``) exactly once and
        snapshots the derived quantities, so no sizing law ever triggers hidden file I/O
        and no setup recomputes the heating load per component. The import is local
        because this module must not import components at module level (see the module
        docstring); it is the one sanctioned exception to the package's layering rule.

        Args:
            building_config: The building whose derived facts the returned context carries.

        Returns:
            A context with the building-derived facts filled in.
        """
        from hisim.components.building.information import (  # pylint: disable=import-outside-toplevel
            BuildingInformation,
        )

        information = BuildingInformation(config=building_config)
        return cls(
            heating_load_in_watt=information.max_thermal_building_demand_in_watt,
            heating_reference_temperature_in_celsius=building_config.heating_reference_temperature_in_celsius,
            number_of_apartments=information.number_of_apartments,
            conditioned_floor_area_in_m2=information.scaled_conditioned_floor_area_in_m2,
            set_heating_temperature_in_celsius=building_config.set_heating_temperature_in_celsius,
            set_cooling_temperature_in_celsius=building_config.set_cooling_temperature_in_celsius,
        )


class Size:
    """Expression terms over the :class:`SizingContext` facts, exactly one per field.

    Terms keep the full field name including its unit — ``Size.HEATING_LOAD_IN_WATT`` —
    matching the repository's unit-explicit naming convention. The one-term-per-field
    invariant (one shared fact vocabulary, so terms and facts cannot drift apart) is
    enforced by ``tests/test_sizing.py``
    rather than by dynamic class construction, so type checkers see every term.
    """

    HEATING_LOAD_IN_WATT: ClassVar[_FactTerm] = _FactTerm("heating_load_in_watt")
    HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS: ClassVar[_FactTerm] = _FactTerm(
        "heating_reference_temperature_in_celsius")
    NUMBER_OF_APARTMENTS: ClassVar[_FactTerm] = _FactTerm("number_of_apartments")
    CONDITIONED_FLOOR_AREA_IN_M2: ClassVar[_FactTerm] = _FactTerm("conditioned_floor_area_in_m2")
    WATER_MASS_FLOW_RATE_IN_KG_PER_SECOND: ClassVar[_FactTerm] = _FactTerm(
        "water_mass_flow_rate_in_kg_per_second")
    HEAT_DISTRIBUTION_SYSTEM_TYPE: ClassVar[_FactTerm] = _FactTerm("heat_distribution_system_type")
    NUMBER_OF_RESIDENTS: ClassVar[_FactTerm] = _FactTerm("number_of_residents")
    MAXIMAL_THERMAL_POWER_IN_WATT: ClassVar[_FactTerm] = _FactTerm("maximal_thermal_power_in_watt")
    MINIMAL_THERMAL_POWER_IN_WATT: ClassVar[_FactTerm] = _FactTerm("minimal_thermal_power_in_watt")
    SET_HEATING_TEMPERATURE_IN_CELSIUS: ClassVar[_FactTerm] = _FactTerm("set_heating_temperature_in_celsius")
    SET_COOLING_TEMPERATURE_IN_CELSIUS: ClassVar[_FactTerm] = _FactTerm("set_cooling_temperature_in_celsius")
