""" Logging functionality for all of HiSim. """
# clean
from enum import IntEnum
import os
from pathlib import Path

LOGGING_LEVEL = 3
LOGGING_PATH: str = r"../logs/"
# for storing logs that are written before the filepath exists
PRE = True
PRE_LOGS = ""
PRE_PROFILE = ""


def set_up():
    """Sets up the logging once a hisim instance is started. The reason for this is twofold:
    a) I want to be minimally invasive, therefore, the current way of having module functions instead
    of a "Logger" class or similar has to be preserved. This necessitates using global variables.
    b) Global variables are set when a module is first imported in a python kernel, regardless of
    where the import takes place. That means starting a new HiSim instance in the same python kernel
    does not reset the global variables above. Therefore, this need to be done manually.
    
    This function needs to be called once in every hisim instance, before anything is logged.
    As soon as the output directory is created, initialize_properly() need to be run."""
    # reset global variables
    global LOGGING_LEVEL, LOGGING_PATH, PRE, PRE_LOGS, PRE_PROFILE  # pylint: disable=global-statement
    LOGGING_LEVEL = 3
    LOGGING_PATH = r"../logs/"
    PRE = True
    PRE_LOGS = ""
    PRE_PROFILE = ""
    # delete old log files in standard path
    logging_default_path = Path(LOGGING_PATH)
    if logging_default_path.exists() and logging_default_path.is_dir():
        for file in logging_default_path.iterdir():
            try:
                file.unlink()
            except Exception:
                information("Logging default file could not be removed. This can occur when more than one simulation run simultaneously.")


def initialize_properly(logging_level, logging_path) -> None:
    """Create actual logging path and file and move pre logs there. Also set logging level.
    This function should be called once during the setup of a HiSim instance, right after the actual
    result/output directory has been created.
    
    Args:
        logging_level: The logging level that is to be used. Get from simulation parameters.
        logging_path: The output directory. Get from simulation parameters."""
    global LOGGING_LEVEL, LOGGING_PATH, PRE, PRE_LOGS, PRE_PROFILE  # pylint: disable=global-statement
    if not PRE:
        print("WARNING! Logging seems to be already initialized.")
    LOGGING_LEVEL = logging_level
    LOGGING_PATH = logging_path  # set actual logging path

    if not os.path.exists(LOGGING_PATH):
        os.makedirs(LOGGING_PATH)  # if folder does not exist, create it

    # write pre_logs to file
    file_name = os.path.join(LOGGING_PATH, "hisim_simulation.log")
    try:
        with open(file_name, "a", encoding="utf-8") as filestream:
            filestream.write(PRE_LOGS)
    except Exception:
        print("hisim_simulation.log could not be appended. "
              "This might happen when too many simultaneous simulations are running.")

    # write pre_profile to file
    file_name = os.path.join(LOGGING_PATH, "profiling_timeuse.log")
    try:
        with open(file_name, "a", encoding="utf-8") as filestream:
            filestream.write(PRE_PROFILE)
    except Exception:
        print("profiling_timeuse.log could not be appended. "
              "This might happen when too many simultaneous simulations are running.")

    # turn off pre_logging and clear pre_logs
    PRE = False
    PRE_LOGS = ""
    PRE_PROFILE = ""




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
        prio_strings = {
            LogPrio.ERROR: "ERR",
            LogPrio.WARNING: "WRN",
            LogPrio.INFORMATION: "IFO",
            LogPrio.DEBUG: "DBG",
            LogPrio.PROFILE: "PRF",
            LogPrio.TRACE: "TRC"
        }
        return prio_strings.get(prio, "???")  # type: ignore


# The logging_message_path can not be set in the function head because then it
# would always use the LOGGING_PATH at definition time, not at runtime.
def error(message: str, logging_message_path: str | None = None) -> None:
    """Log an error message."""
    log(LogPrio.ERROR, message, logging_message_path)


def warning(message: str, logging_message_path: str | None = None) -> None:
    """Log a warning message."""
    log(LogPrio.WARNING, message, logging_message_path)


def information(message: str, logging_message_path: str | None = None) -> None:
    """Log a information message."""
    log(LogPrio.INFORMATION, message, logging_message_path)


def trace(message: str, logging_message_path: str | None = None) -> None:
    """Log a trace message."""
    log(LogPrio.TRACE, message, logging_message_path)


def debug(message: str, logging_message_path: str | None = None) -> None:
    """Log a debug message."""
    log(LogPrio.DEBUG, message, logging_message_path)


def profile(message: str, logging_message_path: str | None = None) -> None:
    """Log a profile message."""
    log(LogPrio.PROFILE, message, logging_message_path)
    log_profile_file(message, logging_message_path)


def log(prio: int, message: str, logging_message_path: str | None = None) -> None:
    """Write and print a log message."""
    if logging_message_path is None:
        logging_message_path = LOGGING_PATH
    if prio <= LOGGING_LEVEL:
        print(str(LogPrio.get_prio_string(prio)) + ":" + message)
    if not os.path.exists(logging_message_path):
        os.makedirs(logging_message_path)

    file_name = os.path.join(logging_message_path, "hisim_simulation.log")
    try:
        with open(file_name, "a", encoding="utf-8") as filestream:
            filestream.write(message + "\n")
    except Exception:
        print("hisim_simulation.log could not be appended. "
              "This might happen when too many simultaneous simulations are running.")

    if PRE: # if no output directory yet: temporarily store log in global variable
        global PRE_LOGS  # pylint: disable=global-statement
        PRE_LOGS += message + "\n"


def log_profile_file(message: str, logging_message_path: str | None = None) -> None:
    """Write log message to logfile."""
    if logging_message_path is None:
        logging_message_path = LOGGING_PATH
    if not os.path.exists(logging_message_path):
        os.makedirs(logging_message_path)

    file_name = os.path.join(logging_message_path, "profiling_timeuse.log")
    try:
        with open(file_name, "a", encoding="utf-8") as filestream:
            filestream.write(message + "\n")
    except Exception:
        print("profiling_timeuse.log could not be appended. "
              "This might happen when too many simultaneous simulations are running.")

    if PRE:
        global PRE_PROFILE  # pylint: disable=global-statement
        PRE_PROFILE += message + "\n"
