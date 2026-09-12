"""Provides thread-safe singleton infrastructure for cross-component data exchange.

This module implements a thread-safe singleton metaclass (`SingletonMeta`),
a singleton simulation repository (`SingletonSimRepository`) for sharing data
across HiSim components during a simulation, and an enum
(`SingletonDictKeyEnum`) defining the well-known keys used in the repository.

**Deprecated: `SingletonSimRepository` and `SingletonDictKeyEnum`.** Use the per-simulation
:class:`hisim.sim_repository.SimRepository` instead, reached from any component as
``self.simulation_repository``. A process-wide singleton holds one entry per key for the whole
program, which is exactly wrong for the simulations HiSim is growing into: in a district run, or a
building with several units, two households write the same well-known key and the second silently
overwrites the first. The per-simulation repository is scoped to the run that owns it, so the same
key means one thing per simulation and the collision cannot occur.

`SingletonMeta` is **not** deprecated — `hisim.result_path_provider.ResultPathProviderSingleton`
uses it legitimately, for a value that really is process-wide.

Migration: new code must not use the singleton repository at all. The retirement is under way and
`SingletonDictKeyEnum` is down to two members, ``RESULT_SCENARIO_NAME`` and ``DESCRIPTION``, written
by the building-sizer setups and ``hisim_main`` and read by postprocessing. They become attributes of
the run rather than repository entries -- they describe the run, so they belong with it. When that
lands, this module keeps only ``SingletonMeta``.

Everything else was deleted or moved on 2026-09-12: the Weather's eight full-year series (outside
temperature, diffuse horizontal, direct normal, direct normal extra and global horizontal
irradiance, azimuth, apparent zenith, wind speed), which the PV system reads at prepare time, travel
through the per-simulation :class:`hisim.sim_repository.SimRepository` under ``Weather.YEARLY_*``;
nothing read the rest.
"""
# clean
import enum
import warnings
from typing import Any, Dict
from threading import Lock
from hisim import loadtypes as lt


# https://refactoring.guru/design-patterns/singleton/python/example#example-1


class SingletonMeta(type):

    """A class for a thread-safe implementation of Singleton."""

    _instances: Dict[Any, Any] = {}

    _lock: Lock = Lock()
    # We now have a lock object that will be used to synchronize threads during first access to the Singleton.

    def __call__(cls, *args, **kwargs):
        """Possible changes to the value of the `__init__` argument do not affect the returned instance."""
        # Fast path: once the singleton has been created, return it without
        # acquiring the lock. ``SingletonSimRepository()`` is called from
        # per-timestep and per-forecast hot loops (e.g. building.py and
        # air_conditioner.py make several calls per step), so paying for an
        # uncontended lock acquire/release on every access is a measurable,
        # if small, overhead during long parametric studies.
        #
        # EAFP single lookup: on the hot path the instance already exists, so a
        # single ``cls._instances[cls]`` access returns it directly. The previous
        # form probed the dict twice (``cls not in cls._instances`` then
        # ``cls._instances[cls]``), recomputing ``hash(cls)`` and re-probing the
        # slot each time; ``try``/``except`` with no exception raised is near-zero
        # cost in CPython, so this halves the per-call dict work. The ``KeyError``
        # branch is taken exactly once per class -- the same as the previous
        # double-checked locking -- because ``super().__call__()`` always returns a
        # real instance (never ``None``).
        try:
            return cls._instances[cls]
        except KeyError:
            # Now, imagine that the program has just been launched. Since there's no
            # Singleton instance yet, multiple threads can simultaneously reach this
            # point almost at the same time. The first of them will acquire the lock
            # and proceed further, while the rest will wait here.
            with cls._lock:
                # The first thread to acquire the lock, reaches this conditional,
                # goes inside and creates the Singleton instance. Once it leaves the
                # lock block, a thread that might have been waiting for the lock
                # release may then enter this section. But since the Singleton field
                # is already initialized, the thread won't create a new object.
                if cls not in cls._instances:
                    instance = super().__call__(*args, **kwargs)
                    cls._instances[cls] = instance
                return cls._instances[cls]


class SingletonSimRepository(metaclass=SingletonMeta):

    """Class for exchanging information across all components.

    .. deprecated::
        Use the per-simulation :class:`hisim.sim_repository.SimRepository`, available on every
        component as ``self.simulation_repository``. This class keeps one entry per key for the
        whole process, so two households in a district or multi-unit simulation collide on every
        well-known key and the later writer silently wins. Nothing warns, and the result is a
        plausible number computed from another building's data.

        The replacement has the same shape — ``set_entry``, ``get_entry``, ``entry_exists``,
        ``delete_entry`` and the dynamic variants — so a migration is usually a change of receiver
        rather than of logic. Keys become plain strings instead of :class:`SingletonDictKeyEnum`
        members, which is the moment to make a key carry the identity that distinguishes one
        household's entry from another's.
    """

    def __init__(self) -> None:
        """Initializes the SimRepository, warning that this class is on its way out.

        The warning fires once per process rather than per call, because the singleton metaclass
        builds the instance only once. It is a ``DeprecationWarning``, which Python hides by
        default and `pytest.ini` filters, so it costs nothing in a normal run and appears for
        anyone who asks for it with ``-W default``.
        """
        warnings.warn(
            "SingletonSimRepository is deprecated: it holds one entry per key for the whole "
            "process, so district and multi-unit simulations collide on every key. Use the "
            "per-simulation SimRepository via self.simulation_repository instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.my_dict: Dict[Any, Any] = {}
        self.my_dynamic_dict: Dict[lt.ComponentType, Dict[int, Any]] = {component_type: {} for component_type in lt.ComponentType}

    def set_entry(self, key: Any, entry: Any) -> None:
        """Sets an entry in the SimRepository."""
        self.my_dict[key] = entry

    def get_entry(self, key: Any) -> Any:
        """Gets an entry from the SimRepository."""
        return self.my_dict[key]

    def entry_exists(self, key: Any) -> bool:
        """Checks if an entry exists."""
        return key in self.my_dict

    def delete_entry(self, key: Any) -> None:
        """Deletes an existing entry."""
        self.my_dict.pop(key)

    def set_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int, entry: Any) -> None:
        """Sets a dynamic entry."""
        self.my_dynamic_dict[component_type][source_weight] = entry

    def get_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int) -> Any:
        """Gets a dynamic entry."""
        component_entries = self.my_dynamic_dict.get(component_type, None)
        if component_entries is None:
            return None
        value = component_entries.get(source_weight, None)
        return value

    def get_dynamic_source_weights(self, component_type: lt.ComponentType) -> list[int]:
        """Lists all source weights that have entries for the given component type.

        Args:
            component_type: The component type to look up.

        Returns:
            A list of source weights with stored entries.
        """
        return list(self.my_dynamic_dict[component_type].keys())

    def delete_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int) -> Any:
        """Deletes a dynamic component entry."""
        self.my_dynamic_dict[component_type].pop(source_weight)

    def clear(self):
        """Clears all dictionaries at the end of the simulation to enable garbage collection and reduce memory consumption."""
        self.my_dict.clear()
        del self.my_dict
        self.my_dynamic_dict.clear()
        del self.my_dynamic_dict

    def reset(self) -> None:
        """Re-initializes both internal dictionaries to empty.

        Unlike :meth:`clear`, this keeps the attributes alive so the singleton
        can be safely reused across successive simulations or test runs
        (KB-5644). Callers that need a clean slate without destroying the
        repository (e.g. test fixtures isolating singleton state) should prefer
        this over ``clear``.
        """
        self.my_dict = {}
        self.my_dynamic_dict = {component_type: {} for component_type in lt.ComponentType}


class SingletonDictKeyEnum(enum.Enum):

    """Class for setting dictionary keys in the singleton sim repository.

    .. deprecated::
        These are the well-known keys of the deprecated :class:`SingletonSimRepository`.
        The per-simulation :class:`hisim.sim_repository.SimRepository` takes plain string
        keys, which is the point at which a key should be given the identity that tells one
        household's entry from another's.
    """

    # 1 to 5 were the sizing keys NUMBEROFAPARTMENTS, WATERMASSFLOWRATEOFHEATGENERATOR,
    # MAXTHERMALBUILDINGDEMAND, SETHEATINGTEMPERATUREFORWATERSTORAGE and
    # SETCOOLINGTEMPERATUREFORWATERSTORAGE. Nothing wrote any of them; the two reads of
    # WATERMASSFLOWRATEOFHEATGENERATOR went with them (R2.4).
    # 6 was LOCATION, written by the Weather at construction time and read back as the
    # report region; postprocessing now reads the Weather component's own config instead.
    RESULT_SCENARIO_NAME = 7
    # 8 to 13 were the six 5R1C thermal-model values the Building wrote at build time --
    # THERMALTRANSMISSIONCOEFFICIENTGLAZING, THERMALTRANSMISSIONSURFACEINDOORAIR,
    # THERMALTRANSMISSIONCOEFFICIENTOPAQUEEM, THERMALTRANSMISSIONCOEFFICIENTOPAQUEMS,
    # THERMALTRANSMISSIONCOEFFICIENTVENTILLATION and THERMALCAPACITYENVELOPE. Their only
    # readers were the PID and MPC controllers, which moved to obsolete/; the Building
    # keeps the values on itself.
    # 14 to 22 were the MPC configuration keys PREDICTIVE, PREDICTIONHORIZON, PVINCLUDED,
    # PVPEAKPOWER, SMARTDEVICESINCLUDED, BATTERYINCLUDED, MPCBATTERYCAPACITY,
    # COEFFICIENT_OF_PERFORMANCE_HEATING and ENERGY_EFFICIENY_RATIO_COOLING. The first
    # seven had no reference outside this file at all; the air conditioner wrote the last
    # two for the PID and MPC controllers now in obsolete/.
    # 24 to 26 were HEATFLUXTHERMALMASSNODEFORECAST, HEATFLUXSURFACENODEFORECAST and
    # HEATFLUXINDOORAIRNODEFORECAST, and 28 was PVFORECASTYEARLY: the disturbance and
    # generation forecasts the Building and the PV system computed under their `predictive`
    # flags for the MPC controller. The flags and the branches went with the controller.
    # 29 to 36 were MAXIMUMBATTERYCAPACITY, MINIMUMBATTERYCAPACITY, MAXIMALCHARGINGPOWER,
    # MAXIMALDISCHARGINGPOWER, BATTERYEFFICIENCY, INVERTEREFFICIENCY,
    # PRICEPURCHASEFORECAST24H and PRICEINJECTIONFORECAST24H -- the battery parameters the
    # MPC controller read and never found a writer for, and the tariff provider's 24 h
    # forecast, whose only reader was that same controller.
    # 37 and 46 were WEATHERALTITUDEYEARLYFORECAST and WEATHERPRESSUREYEARLYFORECAST, two
    # of the Weather's full-year publications that nothing read.
    # 44 was HEATINGBYRESIDENTSYEARLYFORECAST, written by the UTSP connector under its own
    # `predictive` flag and read only by the Building's predictive branch.
    # 23 and 38 to 45 were the eight full-year weather series (outside temperature, diffuse
    # horizontal, direct normal, direct normal extra and global horizontal irradiance, azimuth,
    # apparent zenith, wind speed) the Weather published for the PV system. Since 2026-09-12
    # they travel through the per-simulation SimRepository, keyed by Weather.YEARLY_*.
    DESCRIPTION = 47
