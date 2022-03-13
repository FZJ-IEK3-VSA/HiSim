from enum import Enum, IntEnum

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
    #if(prio < LogPrio.Debug):
    print(str(prio) + ":" + message)
    with open('hisim_simulation.log', 'a') as f:
        f.write(message + "\n")

def log_profile_file(prio: int, message: str):
    #if(prio < LogPrio.Debug):
    with open('profiling_timeuse.log', 'a') as f:
        f.write(message + "\n")
