""" Logging functionality for all of HiSim. """
# clean
from enum import IntEnum
import os

LOGGING_DEFAULT_LEVEL = 3
LOGGING_DEFAULT_PATH: str = r"../logs/"

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


class Logger:
    """Class that handles the logging. A logger is created the first time this module is imported
    in a kernel. Every time a simulation is started, the logger has to be set up for that 
    simulation using the setup() function. Every time a simulation ends, it should be reset with
    the reset() function."""

    # --------------------------------------------------------------------------------------------
    # ----- member variables ---------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    logging_path: str = LOGGING_DEFAULT_PATH
    logging_level: int = LOGGING_DEFAULT_LEVEL
    before_result_dir_created: bool = True
    log_buffer: str = ""
    profile_buffer: str = ""


    # --------------------------------------------------------------------------------------------
    # ----- setup functions ----------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    def setup(self, logging_path) -> None:
        """Create actual logging path and files and move the buffered logs there.

        Args:
            logging_path: The output directory. Get from simulation parameters.
        """
        # safety checks
        if not self.before_result_dir_created:
            print("WARNING! Logging seems to be already initialized.")
        # delete parts of file if it becomes too large
        if self.before_result_dir_created:
            for filename in ["hisim_simulation", "profiling_timeuse"]:
                self.file_thanos(filename)
        # set path and make folder if it does not exist
        self.logging_path = logging_path
        if not os.path.exists(logging_path):
            os.makedirs(logging_path)
        # write buffered logs to files
        for filename, buffer in [["hisim_simulation", self.log_buffer], 
                                 ["profiling_timeuse", self.profile_buffer]]:
            file_path = os.path.join(logging_path, filename + ".log")
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
        log.py is first imported."""
        self.logging_path: str = LOGGING_DEFAULT_PATH
        self.logging_level: int = LOGGING_DEFAULT_LEVEL
        self.before_result_dir_created: bool = True
        self.log_buffer: str = ""
        self.profile_buffer: str = ""


    def file_thanos(self, filename):
        """Checks the size of a default logfile and halves it if it is too large."""
        file_path = os.path.join(LOGGING_DEFAULT_PATH, filename + ".log")
        with open(file_path, "rb", encoding="utf-8") as file:
            num_lines = sum(1 for line in file)
        if num_lines > 10000:
            with open(file_path, "w", encoding="utf-8") as file:
                lines = file.readlines()
                file.writelines(lines[-5000:])


    # --------------------------------------------------------------------------------------------
    # ----- logger class actual logging function -------------------------------------------------
    # --------------------------------------------------------------------------------------------

    def log(self, prio: int, message: str, logging_message_path: str = logging_path,
            use_profile_file: bool = False) -> None:
        """Write and print a log message."""
        # if prio high enough: print to stdout
        if not use_profile_file and prio <= self.logging_level:
            print(str(LogPrio.get_prio_string(prio)) + ":" + message)
        # if logging path doesn't exist: create directory
        if not os.path.exists(logging_message_path):
            os.makedirs(logging_message_path)
        # log to file if possible
        filename = "profiling_timeuse.log" if use_profile_file else "hisim_simulation.log"
        file_path = os.path.join(logging_message_path, filename)
        try:
            with open(file_path, "a", encoding="utf-8") as filestream:
                filestream.write(message + "\n")
        except Exception:
            print("{filename} could not be appended. "
                "This might happen when too many simultaneous simulations are running.")
        # if result directory and therefore actual log file not yet created: buffer logs
        if self.before_result_dir_created:
            self.log_buffer += message + "\n"


# --------------------------------------------------------------------------------------------
# ----- create the logger object and define the module-level functions -----------------------
# --------------------------------------------------------------------------------------------


# this gets executed once per kernel when the module is first imported
logger = Logger()


def error(message: str, logging_message_path: str = LOGGING_DEFAULT_PATH) -> None:
    """Log an error message."""
    logger.log(LogPrio.ERROR, message, logging_message_path, False)

def warning(message: str, logging_message_path: str = LOGGING_DEFAULT_PATH) -> None:
    """Log a warning message."""
    logger.log(LogPrio.WARNING, message, logging_message_path, False)

def information(message: str, logging_message_path: str = LOGGING_DEFAULT_PATH) -> None:
    """Log a information message."""
    logger.log(LogPrio.INFORMATION, message, logging_message_path, False)

def trace(message: str, logging_message_path: str = LOGGING_DEFAULT_PATH) -> None:
    """Log a trace message."""
    logger.log(LogPrio.TRACE, message, logging_message_path, False)

def debug(message: str, logging_message_path: str = LOGGING_DEFAULT_PATH) -> None:
    """Log a debug message."""
    logger.log(LogPrio.DEBUG, message, logging_message_path, False)

def profile(message: str, logging_message_path: str = LOGGING_DEFAULT_PATH) -> None:
    """Log a profile message."""
    logger.log(LogPrio.PROFILE, message, logging_message_path, False)
    logger.log(LogPrio.PROFILE, message, logging_message_path, True)

def log(prio: int, message: str, logging_message_path: str = LOGGING_DEFAULT_PATH) -> None:
    """Write and print a log message."""
    logger.log(prio, message, logging_message_path)

def log_profile_file(message: str, logging_message_path: str = LOGGING_DEFAULT_PATH) -> None:
    """Write log message to logfile."""
    logger.log(LogPrio.PROFILE, message, logging_message_path, True)
