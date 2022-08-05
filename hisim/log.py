from enum import Enum, IntEnum

logging_level = 3
class LogPrio(IntEnum):
    Error = 1
    Warning = 2
    Information = 3
    Debug = 4
    Profile = 5
    Trace = 6


def error(message: str):
    log(LogPrio.Error, message)


def warning(message: str):
    log(LogPrio.Warning, message)


def information(message: str):
    log(LogPrio.Information, message)


def trace(message: str):
    log(LogPrio.Trace, message)


def debug(message: str):
    log(LogPrio.Debug, message)


def profile(message: str):
    log(LogPrio.Profile, message)
    log_profile_file(LogPrio.Profile, message)


def log(prio: int, message: str):
    # if(prio < LogPrio.Debug):
    prio_string: str
    if prio == LogPrio.Error:
        prio_string = "ERR"
    elif prio == LogPrio.Warning:
        prio_string = "WRN"
    elif prio == LogPrio.Information:
        prio_string = "IFO"
    elif prio == LogPrio.Debug:
        prio_string = "DBG"
    elif prio == LogPrio.Profile:
        prio_string = "PRF"
    elif prio == LogPrio.Trace:
        prio_string = "TRC"
    else:
        raise ValueError("Unknown log priority: " + str(prio))
    if prio <= logging_level:
        print(str(prio_string) + ":" + message)
    with open('hisim_simulation.log', 'a') as f:
        f.write(message + "\n")


def log_profile_file(prio: int, message: str):
    # if(prio < LogPrio.Debug):
    with open('profiling_timeuse.log', 'a') as f:
        f.write(message + "\n")
