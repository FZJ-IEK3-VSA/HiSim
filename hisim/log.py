""" Logging functionality for all of HiSim. """
# clean
from enum import IntEnum
import os

LOGGING_LEVEL = 3
LOGGING_PATH: str = r"../logs/"
# for storing logs that are written before the filepath exists
pre = True
pre_logs = ""


class LogPrio(IntEnum):

    """Define a logging priority."""

    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    DEBUG = 4
    PROFILE = 5
    TRACE = 6


def error(message: str, logging_message_path: str = LOGGING_PATH) -> None:
    """Log an error message."""
    log(LogPrio.ERROR, message, logging_message_path)


def warning(message: str, logging_message_path: str = LOGGING_PATH) -> None:
    """Log a warning message."""
    log(LogPrio.WARNING, message, logging_message_path)


def information(message: str, logging_message_path: str = LOGGING_PATH) -> None:
    """Log a information message."""
    log(LogPrio.INFORMATION, message, logging_message_path)


def trace(message: str, logging_message_path: str = LOGGING_PATH) -> None:
    """Log a trace message."""
    log(LogPrio.TRACE, message, logging_message_path)


def debug(message: str, logging_message_path: str = LOGGING_PATH) -> None:
    """Log a debug message."""
    log(LogPrio.DEBUG, message, logging_message_path)


def profile(message: str, logging_message_path: str = LOGGING_PATH) -> None:
    """Log a profile message."""
    log(LogPrio.PROFILE, message, logging_message_path)
    log_profile_file(message, logging_message_path)


def log(prio: int, message: str, logging_message_path: str = LOGGING_PATH) -> None:
    """Write and print a log message."""
    # if(prio < LogPrio.Debug):
    prio_string: str
    if prio == LogPrio.ERROR:
        prio_string = "ERR"
    elif prio == LogPrio.WARNING:
        prio_string = "WRN"
    elif prio == LogPrio.INFORMATION:
        prio_string = "IFO"
    elif prio == LogPrio.DEBUG:
        prio_string = "DBG"
    elif prio == LogPrio.PROFILE:
        prio_string = "PRF"
    elif prio == LogPrio.TRACE:
        prio_string = "TRC"
    else:
        raise ValueError("Unknown log priority: " + str(prio))
    if prio <= LOGGING_LEVEL:
        print(str(prio_string) + ":" + message)

    if not os.path.exists(logging_message_path):
        os.makedirs(logging_message_path)

    file_name = os.path.join(logging_message_path, "hisim_simulation.log")
    try:
        with open(file_name, "a", encoding="utf-8") as filestream:
            filestream.write(message + "\n")
    except Exception:
        print("hisim_simulation.log could not be appended. "
              "This might happen when too many simultaneous simulations are running.")
    
    if pre:
        global pre_logs
        pre_logs += str(prio_string) + ":" + message + "\n"


def log_profile_file(message: str, logging_message_path: str = LOGGING_PATH) -> None:
    """Write log message to logfile."""

    if not os.path.exists(logging_message_path):
        os.makedirs(logging_message_path)

    file_name = os.path.join(logging_message_path, "profiling_timeuse.log")
    try:
        with open(file_name, "a", encoding="utf-8") as filestream:
            filestream.write(message + "\n")
    except Exception:
        print("profiling_timeuse.log could not be appended. "
              "This might happen when too many simultaneous simulations are running.")


def initialize_properly(logging_path) -> None:
    """Move pre logs to the logging path."""
    global pre_logs, pre
    LOGGING_PATH = logging_path # set actual logging path
    if not os.path.exists(LOGGING_PATH):
        os.makedirs(LOGGING_PATH) # if folder does not exist, create it
    file_name = os.path.join(LOGGING_PATH, "hisim_simulation.log")
    # write pre_logs to file
    try: 
        with open(file_name, "a", encoding="utf-8") as filestream:
            filestream.write(pre_logs)
    except Exception:
        print("hisim_simulation.log could not be appended. "
              "This might happen when too many simultaneous simulations are running.")
    # turn off pre_logging and clear pre_logs
    pre = False
    pre_logs = ""