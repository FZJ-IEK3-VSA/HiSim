"""The PV configuration: which array, on which roof, with which module and inverter.

Part of the ``hisim.components.generic_pv_system`` package split (see the package ``__init__`` for the
layout). Holds :class:`PVSystemConfig`, its three factories and the sizing fact
``pv_peak_power_in_watt`` that the battery and the charging station are read off.

:class:`PVLibModuleAndInverterEnum` is not here but in
:mod:`hisim.components.generic_pv_system.calculation`, and this module imports it from there. It names
the database the module and inverter parameters come from, so it is key material for the cached series,
and the producer must be able to import it without importing this module: ``hisim.config``, which every
configuration needs, reaches ``hisim.component``, the post-processing and the repository through its
deliberate lazy imports, and the producer layering rule of ``roadmap/cache_service_spec.md`` §12 bans
all three from a producer's import closure.
"""

# clean

# pylint: disable=cyclic-import
# (the only backward edge is a runtime-local import of PVSystem inside get_main_classname;
# module import order is acyclic)

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional

from dataclasses_json import dataclass_json

from hisim.components.generic_pv_system.calculation import PVLibModuleAndInverterEnum
from hisim.config import (
    ComponentID,
    ConfigBase,
    FactContribution,
    Sizable,
    Size,
    SizingContext,
    concrete,
    sized_field,
)

__authors__ = "Vitor Hugo Bellotto Zago, Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt", "Kristina Dabrock"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class PVSystemConfig(ConfigBase):
    """PVSystemConfig class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        from hisim.components.generic_pv_system.pv_system import (  # pylint: disable=import-outside-toplevel
            PVSystem,  # avoids the config -> component import cycle
        )

        return PVSystem.get_full_classname()

    component_id: ComponentID
    time: int
    location: str
    module_name: str
    integrate_inverter: bool
    inverter_name: str
    module_database: PVLibModuleAndInverterEnum
    inverter_database: PVLibModuleAndInverterEnum
    # marked as the capacity field for the cost-facts contract test (cost_spec.md §9.4)
    power_in_watt: float = field(metadata={"capacity": True})
    azimuth: float
    tilt: float
    # [0..1], how much pv potential is used
    share_of_maximum_pv_potential: float
    load_module_data: bool
    source_weight: int
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: Optional[float]
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float]
    #: lifetime in years
    lifetime_in_years: Optional[float]
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float]
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float]
    predictive_control: bool
    prediction_horizon: Optional[int]
    #: The weather this system is computed with, as ``WeatherConfig.identity()`` spells it. Sized from
    #: the weather by the sizing engine. It no longer carries the weather into the cache key: the
    #: series are keyed by the weather producer's own artifact key, which the DTO of
    #: ``generic_pv_system.calculation`` takes as key material and which covers the weather's code and data
    #: as well as its configuration (``roadmap/cache_service_spec.md`` §3.1). What the field is still
    #: for is the wire format -- it is a sized field of the declarative schema and a record of which
    #: weather a scenario was written against -- so it stays until that is changed deliberately.
    #: See ``roadmap/pylpg_flakiness.md`` F7.
    weather_identity: Sizable[str] = sized_field(rule=Size.WEATHER_IDENTITY, value_type=str)

    def __post_init__(self) -> None:
        """Refuses a share of the rooftop maximum that is not a share.

        The field is a fraction in ``[0, 1]``: both factories multiply the array's maximum by it,
        so a percentage typed as ``50`` sizes a fifty-fold rooftop and a negative share sizes a
        generator that consumes -- each producing a run that finishes and reports plausible
        numbers for a question nobody asked. It is also the provenance a record carries beside the
        power it explains, so a value outside the range is a record that cannot be read back as
        one. Only the share is checked here: ``power_in_watt`` is a sizable field and may still
        hold ``AUTO`` or a law at construction time.

        Raises:
            ValueError: If ``share_of_maximum_pv_potential`` lies outside ``[0, 1]``.
        """
        if not 0.0 <= self.share_of_maximum_pv_potential <= 1.0:
            raise ValueError(
                "The share of the maximum PV potential is a fraction between 0 and 1, not "
                f"{self.share_of_maximum_pv_potential}. It is multiplied onto the array's maximum "
                "power, so a percentage or a negative value would size a different array in silence."
            )

    @classmethod
    def get_default_pv_system(
        cls,
        name: str = "PVSystem",
        maximum_power_in_watt: float = 10e3,
        source_weight: int = 0,
        share_of_maximum_pv_potential: float = 1.0,
        location: str = "Aachen",
        component_id: Optional[ComponentID] = None,
        module_name: str = "Trina Solar TSM-435NE09RC.05",
        module_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE,  # noqa: E501
        inverter_name: str = "Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]",
        inverter_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE,  # noqa: E501
    ) -> "PVSystemConfig":
        """Gets a default PV system.

        ``maximum_power_in_watt`` is the array's maximum; the share is applied to it here, exactly
        once, and the ``power_in_watt`` field of the returned config is the result. A *record*
        therefore carries a result beside its provenance, so it is rebuilt from its fields and
        never replayed through this factory, which would apply the share a second time.
        """
        if component_id is None:
            component_id = ComponentID(name=name)
        power_in_watt = maximum_power_in_watt * share_of_maximum_pv_potential
        return PVSystemConfig(
            time=2019,
            power_in_watt=power_in_watt,
            load_module_data=False,
            integrate_inverter=True,
            module_database=module_database,
            inverter_database=inverter_database,
            module_name=module_name,
            inverter_name=inverter_name,
            component_id=component_id,
            azimuth=180,
            tilt=30,
            share_of_maximum_pv_potential=share_of_maximum_pv_potential,
            source_weight=source_weight,
            location=location,
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
            predictive_control=False,
            prediction_horizon=None,
        )

    @classmethod
    def get_scaled_pv_system(
        cls,
        rooftop_area_in_m2: float,
        name: str = "PVSystem",
        share_of_maximum_pv_potential: float = 1.0,
        module_name: str = "Trina Solar TSM-435NE09RC.05",
        module_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE,  # noqa: E501
        inverter_name: str = "Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]",
        inverter_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE,
        location: str = "Aachen",
        component_id: Optional[ComponentID] = None,
        load_module_data: bool = False,
    ) -> "PVSystemConfig":
        """Gets a default PV system with scaling according to rooftop area.

        The share of the maximum potential is applied exactly once, by ``size_pv_system``, which is why it
        is not passed on to ``get_default_pv_system`` (that would multiply the power by it a second time).
        It is instead stamped onto the finished config afterwards, so the returned configuration records
        the share that was really applied rather than the 1.0 default of ``get_default_pv_system``.
        """
        if component_id is None:
            component_id = ComponentID(name=name)
        total_pv_power_in_watt = cls.size_pv_system(
            rooftop_area_in_m2=rooftop_area_in_m2,
            share_of_maximum_pv_potential=share_of_maximum_pv_potential,
            module_name=module_name,
            module_database=module_database,
        )
        config = PVSystemConfig.get_default_pv_system(
            component_id=component_id,
            location=location,
            maximum_power_in_watt=total_pv_power_in_watt,
            module_name=module_name,
            module_database=module_database,
            inverter_name=inverter_name,
            inverter_database=inverter_database,
        )
        # Stamped after the fact, not passed in above: the power already carries the share.
        # Through ``replace`` rather than by assignment, so that the share meets the range check
        # in ``__post_init__`` on this path too.
        return replace(
            config,
            share_of_maximum_pv_potential=share_of_maximum_pv_potential,
            load_module_data=load_module_data,
        )

    @classmethod
    def size_pv_system(
        cls,
        rooftop_area_in_m2: float,
        share_of_maximum_pv_potential: float,
        module_name: str,
        module_database: PVLibModuleAndInverterEnum,
    ) -> float:
        """Size PV system.

        Size the pv system according to the rooftop area and the share of
        the maximum pv power that should be used.
        """

        # get area and power of module
        if (
            module_name == "Hanwha HSL60P6-PA-4-250T [2013]"
            and module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE
        ):
            module_area_in_m2 = 1.65
            module_power_in_watt = 250.0
            # this is equal to an efficiency of 15,15%

        elif (
            module_name == "Trina Solar TSM-435NE09RC.05"
            and module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE
        ):
            module_area_in_m2 = 1.98
            module_power_in_watt = 435.16
            # this is equal to an efficiency of 21,98%

        # pv module efficiency calculation see:
        # https://www.ess-kempfle.de/ratgeber/ertrag/pv-ertrag/#:~:text=So%20berechnen%20Sie%20den%20Wirkungsgrad,liegt%20bei%201.000%20W%2Fm%C2%B2.
        else:
            raise ValueError(
                f"""Module name or module database {module_name}
                {module_database} not given in this function.
                Please check or add your module information."""
            )

        # scale rooftop area with limiting factor due to shading and
        # obstacles like chimneys etc. see p.18 in following paper:
        # https://www.mdpi.com/1996-1073/15/15/5536 (Stanley's work)
        limiting_factor_for_rooftop = 0.6
        effective_rooftop_area_in_m2 = rooftop_area_in_m2 * limiting_factor_for_rooftop

        total_pv_power_in_watt = (
            effective_rooftop_area_in_m2 / module_area_in_m2 * module_power_in_watt
        ) * share_of_maximum_pv_potential

        return round(total_pv_power_in_watt, 2)


def _pv_sizing_facts(config: PVSystemConfig, ctx: SizingContext) -> Dict[str, Any]:
    """Contributes the array's peak power for the components sized from it.

    A battery's capacity and a charging station's power are read off the generator they
    are installed beside, so the peak power is a fact of the system and not only a field
    of this config. The value is the resolved one — ``compute`` runs after this config
    was sized, so ``power_in_watt`` is a number here whether an author pinned it or a law
    derived it from the roof — and the context argument is unused: the peak power is this
    config's own field.

    Args:
        config: this PV configuration, fully resolved.
        ctx: the sizing context; unused.

    Returns:
        Dict[str, Any]: ``{"pv_peak_power_in_watt": config.power_in_watt}``.
    """
    del ctx
    return {"pv_peak_power_in_watt": concrete(config.power_in_watt)}


# Declared after the class because it refers to it, exactly as ``WeatherConfig`` does. A
# scenario with two arrays has two providers of this fact, and a consumer then names the one
# it means through its ``sizing_sources`` line.
PVSystemConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(facts=("pv_peak_power_in_watt",), compute=_pv_sizing_facts),
)
