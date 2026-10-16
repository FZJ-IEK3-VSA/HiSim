"""Names the components of a run that still read January when the run's window starts later in the year.

Since the weather looks the run's start up in its series (hisim-9g2.4, ``hisim/window_offset.py``), a run
from 1 July reads July's weather, and with it the PV system and the building's solar gains. Some
components do not follow the window yet: they index their year-long profile from row 0, which is
1 January, or count a season in timesteps from the run's start as if that were 1 January. A run with
such a component and a window after 1 January finishes and looks normal, but mixes two seasons.

Until the mid-year epic (``roadmap/midyear_start_epic.md``, hisim-9g2) has moved every component onto the
window, :class:`JanuaryReaders` lists those component classes, and the simulator logs a warning at the
start of such a run naming the ones it contains (:meth:`hisim.simulator.Simulator.warn_of_january_readers`).
The warning changes no result. Each pull request of the epic removes the entry of the class it fixes, and
and PR7 (#1033, hisim-9g2.8) removes this module once every component follows the window (hisim-dj4u).

This module imports the standard library only: it names the classes by their dotted path, so the
simulator does not import the component modules to compare against them.
"""

import dataclasses
import datetime
from typing import ClassVar, Iterable, List, Optional, Tuple

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


@dataclasses.dataclass(frozen=True)
class JanuaryReader:
    """One component class that does not follow a mid-year window yet, what it reads instead, and its fix.

    Example: ``JanuaryReader("hisim.components.csvloader.CSVLoader", "its profile from row 0, which is
    1 January in a year-long file", "hisim-9g2.14", 1034)`` says that the CSV loader reads January in a
    July run, and that bead hisim-9g2.14, implemented in pull request #1034, makes it read the window.

    Args:
        class_path: The dotted path of the component class, ``<defining module>.<class name>``.
        what_it_reads: What the class reads in place of the run's window, as the end of a sentence
            starting with the class name and "reads".
        bead: The id of the bead whose pull request makes the class follow the window.
        pull_request: The number of that pull request.
    """

    class_path: str
    what_it_reads: str
    bead: str
    pull_request: int

    def describe(self) -> str:
        """Return one line naming the class, what it reads instead of the window, and the bead and PR that fix it.

        Example: ``"CSVLoader reads its profile from row 0, which is 1 January in a year-long file
        (fixed by hisim-9g2.14, PR #1034)"``.
        """
        class_name = self.class_path.rsplit(".", 1)[-1]
        return f"{class_name} reads {self.what_it_reads} (fixed by {self.bead}, PR #{self.pull_request})"


class JanuaryReaders:
    """The component classes that still read January in a run whose window starts after 1 January.

    A class matches a component when it is the component's class or one of its base classes, so a
    subclass, which inherits the reading, is named too. The warning names each class at most once,
    however many of its instances the run has.
    """

    #: The classes that do not follow the window yet, each with the bead and the pull request that fix it.
    #: Remove an entry in the pull request that fixes its class; the last removal deletes this module.
    CLASSES: ClassVar[Tuple[JanuaryReader, ...]] = (
        JanuaryReader(
            "hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector",
            "its occupancy, electricity, hot-water and internal-gain profiles from 1 January",
            "hisim-9g2.6",
            1031,
        ),
        JanuaryReader(
            "hisim.components.generic_car.Car",
            "its driving profile and its locations from 1 January",
            "hisim-9g2.6",
            1031,
        ),
        JanuaryReader(
            "hisim.components.generic_chp.L1CHPController",
            "its heating season counted from the run's start as if that were 1 January",
            "hisim-9g2.6",
            1031,
        ),
        JanuaryReader(
            "hisim.components.solar_thermal_system.SolarThermalSystem",
            "a sun position of its own, on a clock other than that of the weather's irradiance",
            "hisim-9g2.7",
            1032,
        ),
        JanuaryReader(
            "hisim.components.csvloader.CSVLoader",
            "its profile from row 0, which is 1 January in a year-long file",
            "hisim-9g2.14",
            1034,
        ),
    )

    @staticmethod
    def starts_after_new_year(start_date: datetime.datetime) -> bool:
        """Return whether a run's window starts after 00:00 on 1 January of its year.

        Example: a run from 2021-07-01 00:00 starts after new year, a run from 2021-01-01 00:00 does not.

        Args:
            start_date: The run's start, ``SimulationParameters.start_date``.

        Returns:
            bool: True when the start lies after 1 January 00:00.
        """
        return start_date > datetime.datetime(start_date.year, 1, 1)

    @classmethod
    def present(cls, component_classes: Iterable[type]) -> List[JanuaryReader]:
        """Return the entries of :attr:`CLASSES` that match at least one of the given component classes.

        A class matches an entry when the entry's dotted path is that of the class or of one of its base
        classes. The entries come back in the order of :attr:`CLASSES`, each once.

        Args:
            component_classes: The classes of the run's components.

        Returns:
            List[JanuaryReader]: the matching entries; empty when no component reads January.
        """
        paths_present = {
            f"{base.__module__}.{base.__qualname__}"
            for component_class in component_classes
            for base in component_class.__mro__
        }
        return [reader for reader in cls.CLASSES if reader.class_path in paths_present]

    @classmethod
    def warning_text(cls, start_date: datetime.datetime, component_classes: Iterable[type]) -> Optional[str]:
        """Return the warning for a run that starts after 1 January and has components that still read January.

        Example: a run from 1 July with a CSV loader gets "The run starts on 2021-07-01, after 1 January,
        but some of its components do not follow the run's window yet, so its results mix two seasons:
        CSVLoader reads its profile from row 0, ... (fixed by hisim-9g2.14, PR #1034)."

        Args:
            start_date: The run's start, ``SimulationParameters.start_date``.
            component_classes: The classes of the run's components.

        Returns:
            Optional[str]: the warning, or None for a run from 1 January or one without such components.
        """
        if not cls.starts_after_new_year(start_date):
            return None
        readers = cls.present(component_classes)
        if not readers:
            return None
        descriptions = "; ".join(reader.describe() for reader in readers)
        return (
            f"The run starts on {start_date.date().isoformat()}, after 1 January, but some of its components do not "
            f"follow the run's window yet, so its results mix two seasons: {descriptions}. The weather, the PV "
            "system and the building's solar gains do read the window (roadmap/midyear_start_epic.md)."
        )
