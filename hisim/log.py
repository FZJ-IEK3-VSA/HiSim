# noqa
# flake8: noqa
# noqa: D400, D415, D413, D407
# pylint: skip-file
""" Logging functionality for all of HiSim. """
from __future__ import annotations

from enum import IntEnum
from pathlib import Path

LOGGING_DEFAULT_LEVEL: int = 3


class LogPrio(IntEnum):
    """Define a logging priority."""

    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    DEBUG = 4
    PROFILE = 5
    TRACE = 6

    @staticmethod
    def get_prio_string(prio: int) -> str:
        """Get the string representation of the priority."""

        prio_strings: dict[int, str] = {
            LogPrio.ERROR: "ERR",
            LogPrio.WARNING: "WRN",
            LogPrio.INFORMATION: "IFO",
            LogPrio.DEBUG: "DBG",
            LogPrio.PROFILE: "PRF",
            LogPrio.TRACE: "TRC"
        }
        return prio_strings.get(prio, "???")


class Logger:
    """Class that handles the logging.

    A logger is created the first time this module is imported
    in a kernel. Every time a simulation is started, the logger has to be set up for that
    simulation using the setup() function. Every time a simulation ends, it should be reset with
    the reset() function.

    Until setup() names the run's result directory, messages are printed and buffered, and written
    nowhere: setup() writes the buffer into the result directory. The logger used to write them to
    ``../logs/`` relative to whatever the working directory was, which put a log file beside the
    repository (or anywhere else) on every run -- a stray write the calculation's write guard
    (``hisim.write_guard``) refuses.
    """

    # --------------------------------------------------------------------------------------------
    # ----- member variables ---------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    logging_path: str | None = None
    logging_level: int = LOGGING_DEFAULT_LEVEL
    before_result_dir_created: bool = True
    log_buffer: str = ""
    profile_buffer: str = ""

    # --------------------------------------------------------------------------------------------
    # ----- setup functions ----------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    def setup(self, logging_path: str) -> None:
        """Create actual logging path and files and move the buffered logs there.

        Args:
            logging_path: The output directory. Get from simulation parameters.
        """
        # safety checks
        if not self.before_result_dir_created:
            print("WARNING! Logging seems to be already initialized.")
        # set path and make folder if it does not exist
        self.logging_path = logging_path
        if not Path(logging_path).exists():
            Path(logging_path).mkdir(parents=True, exist_ok=True)
        # write buffered logs to files
        for filename, buffer in [["hisim_simulation", self.log_buffer],
                                 ["profiling_timeuse", self.profile_buffer]]:
            file_path = str(Path(logging_path) / (filename + ".log"))
            try:
                with open(file_path, "a", encoding="utf-8") as filestream:
                    filestream.write(buffer)
            except Exception:
                print(filename + ".log could not be appended. "
                    "This might happen when too many simultaneous simulations are running.")
        # turn off buffering and clear buffers
        self.before_result_dir_created = False
        self.log_buffer = ""
        self.profile_buffer = ""

    def reset(self) -> None:
        """Resets the logger at the end of a simulation to prepare it for the next one.

        This is necessary because the logger gets initialized only once per kernel, when
        log.py is first imported.
        """
        self.logging_path = None
        self.logging_level: int = LOGGING_DEFAULT_LEVEL
        self.before_result_dir_created: bool = True
        self.log_buffer: str = ""
        self.profile_buffer: str = ""

    # --------------------------------------------------------------------------------------------
    # ----- logger class actual logging function -------------------------------------------------
    # --------------------------------------------------------------------------------------------

    def log(self, prio: int, message: str, logging_message_path: str|None = None,
            use_profile_file: bool = False) -> None:
        """Write and print a log message.

        If the parameter logging_message_path is not provided, the instance attribute
        self.logging_path, which is set during the Logger setup, is used.
        """
        if prio > self.logging_level:
            return
        if not use_profile_file:
            print(str(LogPrio.get_prio_string(prio)) + ":" + message)
        if logging_message_path is None:
            logging_message_path = self.logging_path
        if logging_message_path is None:
            # No result directory yet: keep the message for setup() to write there.
            if use_profile_file:
                self.profile_buffer += message + "\n"
            else:
                self.log_buffer += message + "\n"
            return
        # if logging path doesn't exist: create directory
        if not Path(logging_message_path).exists():
            Path(logging_message_path).mkdir(parents=True, exist_ok=True)
        # log to file if possible
        filename = "profiling_timeuse.log" if use_profile_file else "hisim_simulation.log"
        file_path = str(Path(logging_message_path) / filename)
        try:
            with open(file_path, "a", encoding="utf-8") as filestream:
                filestream.write(message + "\n")
        except Exception:
            print(f"{filename} could not be appended. "
                "This might happen when too many simultaneous simulations are running.")


# --------------------------------------------------------------------------------------------
# ----- create the logger object and define the module-level functions -----------------------
# --------------------------------------------------------------------------------------------


# this gets executed once per kernel when the module is first imported
logger: Logger = Logger()


def error(message: str, logging_message_path: str|None = None) -> None:
    """Log an error message."""
    logger.log(LogPrio.ERROR, message, logging_message_path, False)


def warning(message: str, logging_message_path: str|None = None) -> None:
    """Log a warning message."""
    logger.log(LogPrio.WARNING, message, logging_message_path, False)


def information(message: str, logging_message_path: str|None = None) -> None:
    """Log a information message."""
    logger.log(LogPrio.INFORMATION, message, logging_message_path, False)


def trace(message: str, logging_message_path: str|None = None) -> None:
    """Log a trace message."""
    logger.log(LogPrio.TRACE, message, logging_message_path, False)


def debug(message: str, logging_message_path: str|None = None) -> None:
    """Log a debug message."""
    logger.log(LogPrio.DEBUG, message, logging_message_path, False)


def profile(message: str, logging_message_path: str|None = None) -> None:
    """Log a profile message."""
    logger.log(LogPrio.PROFILE, message, logging_message_path, False)
    logger.log(LogPrio.PROFILE, message, logging_message_path, True)


def log(prio: int, message: str, logging_message_path: str|None = None) -> None:
    """Write and print a log message."""
    logger.log(prio, message, logging_message_path)


def log_profile_file(message: str, logging_message_path: str|None = None) -> None:
    """Write log message to logfile."""
    logger.log(LogPrio.PROFILE, message, logging_message_path, True)
