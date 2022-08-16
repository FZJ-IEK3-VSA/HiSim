""" Logging functionality for all of HiSim. """

from enum import IntEnum

LOGGING_LEVEL = 3


class LogPrio(IntEnum):

    """ Define a logging priority. """

    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    DEBUG = 4
    PROFILE = 5
    TRACE = 6


def error(message: str) -> None:
    """ Log an error message. """
    log(LogPrio.ERROR, message)


def warning(message: str) -> None:
    """ Log a warning message. """
    log(LogPrio.WARNING, message)


def information(message: str) -> None:
    """ Log a information message. """
    log(LogPrio.INFORMATION, message)


def trace(message: str) -> None:
    """ Log a trace message. """
    log(LogPrio.TRACE, message)


def debug(message: str) -> None:
    """ Log a debug message. """
    log(LogPrio.DEBUG, message)


def profile(message: str) -> None:
    """ Log a profile message. """
    log(LogPrio.PROFILE, message)
    log_profile_file(message)


def log(prio: int, message: str) -> None:
    """ Write and print a log message. """
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
    with open('hisim_simulation.log', 'a', encoding="utf-8") as filestream:
        filestream.write(message + "\n")


def log_profile_file(message: str) -> None:
    """ Write log message to logfile. """
    with open('profiling_timeuse.log', 'a', encoding="utf-8") as filestream:
        filestream.write(message + "\n")
