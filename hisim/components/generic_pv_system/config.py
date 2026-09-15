"""The PV configuration: which array, on which roof, with which module and inverter.

Holds :class:`PVSystemConfig`, its ``rooftop`` preset, the law that sizes an array to the roof it
stands on and the sizing fact ``pv_peak_power_in_watt`` that the battery and the charging station
are read off. :class:`PVLibModuleAndInverterEnum`, which names the database a module's and an
inverter's parameters come from, lives in :mod:`hisim.components.generic_pv_system.calculation`
and is imported from there; the package ``__init__`` explains why.
"""

# pylint: disable=cyclic-import
# (the only backward edge is the runtime-local import of PVSystem in get_main_classname;
# module import order is acyclic)

from dataclasses import dataclass
from typing import ClassVar, Dict, Optional, Tuple

from dataclasses_json import dataclass_json

from hisim.components.generic_pv_system.calculation import PVLibModuleAndInverterEnum
from hisim.config import (
    ComponentID,
    ConfigBase,
    ConfigSizingError,
    FactContribution,
    OwnFields,
    Sizable,
    Size,
    SizingContext,
    SizingLaw,
    concrete,
    law,
    preset,
    sized_field,
)


@dataclass(frozen=True)
class PVModule:
    """The two numbers a rooftop array is sized with: how much roof one module takes and what it gives.

    Example: the Trina Solar TSM-435NE09RC.05 is ``PVModule(area_in_m2=1.98,
    power_in_watt=435.16)``, an efficiency of 21.98 % at the standard 1000 W/m². A row of
    :attr:`PVSystemConfig.MODULES` holds one of these, so a module is added to the repository as
    an entry rather than as a branch in the sizing arithmetic.
    """

    area_in_m2: float
    power_in_watt: float


def _rooftop_power_in_watt(ctx: SizingContext, own: OwnFields) -> float:
    """Computes a rooftop array's power from the roof it stands on and the module it is built of.

    The law behind the ``power_in_watt`` field of :class:`PVSystemConfig`: usable roof area times
    the module's power per square metre, times the share of that maximum the author wants
    installed. The arithmetic and the module table stay in
    :meth:`PVSystemConfig.size_pv_system`, which this function only feeds, so a module's area and
    rating are written down in one place.

    The share is applied exactly once, here, so what the field ends up holding is the array that
    is built rather than a maximum, and a record of this configuration replays to the same array.

    Args:
        ctx: The sizing facts of the surrounding system; ``roof_area_in_m2`` is read from it,
            contributed by the building the array stands on.
        own: The sibling fields of the configuration being resolved, for the share and for the
            module the array is built of.

    Returns:
        float: The array's power in watt, rounded to two decimals.

    Raises:
        ConfigSizingError: If the context carries no roof area, so the law has nothing to size
            from.
        ValueError: If the module and database pair is not one :attr:`PVSystemConfig.MODULES`
            holds.
    """
    roof_area_in_m2 = ctx.roof_area_in_m2
    if roof_area_in_m2 is None:
        raise ConfigSizingError(
            "a rooftop array is sized from 'roof_area_in_m2', which this context does not carry. "
            "Resolve the PV configuration against a context a building contributed to, or pin "
            "'power_in_watt' to the array's power."
        )
    return PVSystemConfig.size_pv_system(
        rooftop_area_in_m2=roof_area_in_m2,
        share_of_maximum_pv_potential=own.value_of("share_of_maximum_pv_potential"),
        module_name=own.value_of("module_name"),
        module_database=own.value_of("module_database"),
    )


@dataclass_json
@dataclass
class PVSystemConfig(ConfigBase):
    """Configuration of the PVSystem class.

    The named default array is :meth:`preset_rooftop`, which is the field defaults and a name;
    ``power_in_watt`` is sizable, so the preset leaves it ``AUTO`` and ``.resolve(ctx)`` computes
    it from the roof the array stands on. An author who knows the array's power pins the field
    instead.
    """

    MAIN_CLASS = "hisim.components.generic_pv_system.PVSystem"

    #: Area and peak power of the modules this repository carries data for, keyed by the module
    #: name together with the database its electrical parameters are read from — the pair is the
    #: key because the same name in a different database is a different module. A module is added
    #: to the repository by adding a row here.
    MODULES: ClassVar[Dict[Tuple[str, PVLibModuleAndInverterEnum], PVModule]] = {
        ("Hanwha HSL60P6-PA-4-250T [2013]", PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE): PVModule(
            area_in_m2=1.65, power_in_watt=250.0
        ),
        ("Trina Solar TSM-435NE09RC.05", PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE): PVModule(
            area_in_m2=1.98, power_in_watt=435.16
        ),
    }

    #: Fraction of a roof's gross area an array can actually cover, once shading, chimneys and
    #: walkways are deducted. See p. 18 of https://www.mdpi.com/1996-1073/15/15/5536.
    USABLE_ROOF_FRACTION: ClassVar[float] = 0.6

    #: Sizing law of the array's power: the usable part of the roof, filled with the configured
    #: module, times the share of that maximum the author wants installed. Named as a ClassVar so
    #: that the field declaration reads as one line.
    ROOFTOP_POWER_LAW: ClassVar[SizingLaw] = law(
        _rooftop_power_in_watt,
        reads=(Size.ROOF_AREA_IN_M2,),
        fields=("share_of_maximum_pv_potential", "module_name", "module_database"),
    )

    component_id: ComponentID
    #: The year the array is simulated in.
    time: int = 2019
    location: str = "Aachen"
    module_name: str = "Trina Solar TSM-435NE09RC.05"
    integrate_inverter: bool = True
    inverter_name: str = "Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]"
    module_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE
    inverter_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE
    #: Orientation of the array in degrees, 180 being due south.
    azimuth: float = 180
    #: Inclination of the array against the horizontal, in degrees.
    tilt: float = 30
    #: How much of the roof's potential is built, as a fraction in [0, 1].
    share_of_maximum_pv_potential: float = 1.0
    load_module_data: bool = False
    source_weight: int = 0
    #: CO2 footprint of investment in kg. Unset throughout the repository, which is what makes
    #: postprocessing look the array up in the device database instead.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None
    predictive_control: bool = False
    prediction_horizon: Optional[int] = None
    #: The array's power. Sizable: left ``AUTO`` it is computed by :data:`ROOFTOP_POWER_LAW` from
    #: the roof area the building contributes. Marked as the capacity field for the cost engine.
    power_in_watt: Sizable[float] = sized_field(rule=ROOFTOP_POWER_LAW, metadata={"capacity": True})
    #: The weather this system is computed with, as ``WeatherConfig.identity()`` spells it, sized
    #: from the weather by the sizing engine. It is not cache-key material — the series are keyed
    #: by the weather producer's own artifact key — but it is a sized field of the declarative
    #: schema and the record of which weather a scenario was written against.
    weather_identity: Sizable[str] = sized_field(rule=Size.WEATHER_IDENTITY, value_type=str)

    #: The array's resolved peak power, contributed for the components that are sized from the
    #: generator they stand beside (the battery, the charging station). The compute runs after
    #: this config was sized, so ``power_in_watt`` is a number here whether an author pinned it or
    #: the rooftop law derived it. With two arrays in one scenario each is addressable as
    #: "<its name>.pv_peak_power_in_watt" and a consumer names the one it means.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(
            facts=("pv_peak_power_in_watt",),
            compute=lambda config, ctx: {"pv_peak_power_in_watt": concrete(config.power_in_watt)},
        ),
    )

    def __post_init__(self) -> None:
        """Refuses a share of the rooftop maximum that is not a share.

        The field is a fraction in ``[0, 1]``: the rooftop law multiplies the array's maximum by
        it, so a percentage typed as ``50`` sizes a fifty-fold rooftop and a negative share sizes a
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

    @preset
    @classmethod
    def preset_rooftop(cls, name: str) -> "PVSystemConfig":
        """The fleet's rooftop array, scaled to the roof it stands on.

        The field defaults are this array: south-facing at thirty degrees, built of Trina Solar
        TSM-435NE09RC.05 modules from the CEC module database behind an Enphase IQ8P inverter from
        the CEC inverter database, filling the whole usable roof. What it does not fix is the
        array's power: ``power_in_watt`` stays ``AUTO`` so that :data:`ROOFTOP_POWER_LAW` derives
        it from the building's roof area, and an author who knows the power pins the field
        instead.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            PVSystemConfig: The preset configuration, with ``power_in_watt`` unsized.
        """
        return cls(component_id=ComponentID(name=name))

    @classmethod
    def size_pv_system(
        cls,
        rooftop_area_in_m2: float,
        share_of_maximum_pv_potential: float,
        module_name: str,
        module_database: PVLibModuleAndInverterEnum,
    ) -> float:
        """Returns the power an array of the given module fills the given roof with.

        The physics behind the ``power_in_watt`` field: :attr:`USABLE_ROOF_FRACTION` of the roof
        is divided by the module's area and multiplied by the module's rating, and the share the
        author wants installed is applied to the result -- exactly once, so the number returned is
        the array that is built, not a maximum.

        Example: a 120 m² roof covered with Trina Solar TSM-435NE09RC.05 modules at a share of
        1.0 gives ``120 * 0.6 / 1.98 * 435.16``, i.e. 15 823.27 watt.

        Args:
            rooftop_area_in_m2: The building's gross roof area.
            share_of_maximum_pv_potential: The fraction of the usable roof that is covered.
            module_name: The PV module the array is built of.
            module_database: The database that module's parameters come from.

        Returns:
            float: The array's power in watt, rounded to two decimals.

        Raises:
            ValueError: If the module and database pair has no row in :attr:`MODULES`. A
                ``config:`` override of ``module_name`` alone reaches this, which is why the
                message names both halves of the pair and lists the pairs that do have a row.
        """
        module = cls.MODULES.get((module_name, module_database))
        if module is None:
            known = ", ".join(f"({name!r}, {database.name})" for name, database in cls.MODULES)
            raise ValueError(
                f"No area and rating are known for the module ({module_name!r}, "
                f"{module_database.name}), so an array of it cannot be sized. The module and "
                f"database pairs this repository carries are: {known}."
            )
        effective_rooftop_area_in_m2 = rooftop_area_in_m2 * cls.USABLE_ROOF_FRACTION
        total_pv_power_in_watt = (
            effective_rooftop_area_in_m2 / module.area_in_m2 * module.power_in_watt
        ) * share_of_maximum_pv_potential
        return round(total_pv_power_in_watt, 2)
