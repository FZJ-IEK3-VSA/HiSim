""" Logging functionality for all of HiSim. """
# clean
from enum import IntEnum
import os

LOGGING_LEVEL = 3
LOGGING_PATH: str = r"../logs/"
# for storing logs that are written before the filepath exists
PRE = True
PRE_LOGS = ""
PRO_PROFILE = ""


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
def error(message: str, logging_message_path: str = None) -> None:
    """Log an error message."""
    if logging_message_path is None:
        logging_message_path = LOGGING_PATH
    log(LogPrio.ERROR, message, logging_message_path)


def warning(message: str, logging_message_path: str = None) -> None:
    """Log a warning message."""
    if logging_message_path is None:
        logging_message_path = LOGGING_PATH
    log(LogPrio.WARNING, message, logging_message_path)


def information(message: str, logging_message_path: str = None) -> None:
    """Log a information message."""
    if logging_message_path is None:
        logging_message_path = LOGGING_PATH
    log(LogPrio.INFORMATION, message, logging_message_path)


def trace(message: str, logging_message_path: str = None) -> None:
    """Log a trace message."""
    if logging_message_path is None:
        logging_message_path = LOGGING_PATH
    log(LogPrio.TRACE, message, logging_message_path)


def debug(message: str, logging_message_path: str = None) -> None:
    """Log a debug message."""
    if logging_message_path is None:
        logging_message_path = LOGGING_PATH
    log(LogPrio.DEBUG, message, logging_message_path)


def profile(message: str, logging_message_path: str = None) -> None:
    """Log a profile message."""
    log(LogPrio.PROFILE, message, logging_message_path)
    if logging_message_path is None:
        logging_message_path = LOGGING_PATH
    log_profile_file(message, logging_message_path)


def log(prio: int, message: str, logging_message_path: str = None) -> None:
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

    if PRE:
        global PRE_LOGS
        PRE_LOGS += message + "\n"


def log_profile_file(message: str, logging_message_path: str = None) -> None:
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
        global PRE_PROFILE
        PRE_PROFILE += message + "\n"


def initialize_properly(logging_path) -> None:
    """Create actual logging path and file and move pre logs there."""
    global PRE_LOGS, PRE_PROFILE, PRE, LOGGING_PATH
    if not PRE:
        print("WARNING! Logging seems to be already initialized.")
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
