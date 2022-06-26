import os
import logging
import time
import sys

from internal import os_util
__all__ = ["LoggerBase", "Logger", "resetTime", "critical", "info", "debug"]

class _MyFilter(object):
    """
    Custom Logging Filter Using Lambda
    -
    Example: 
    - level == logging.DEBUG || [logging.DEBUG, logging.INFO]
    - lambda levelin, levelset : levelin >= levelset ; default for logging
    - lambda levelin, levelset : levelin in levelset ; levelset can be an array of logging levels
    - lambda levelin, levelset : levelin == levelset ; grab one logging level
    """
    __slots__ = "__lambda", "__level"
    def __init__(self, lambda_func, level):
        self.__lambda = lambda_func
        self.__level = level

    def filter(self, logRecord):
        return self.__lambda(logRecord.levelno, self.__level)

class _Formatter(logging.Formatter):
    """ 
    Custom Component formater
    -
    - startTime : Time from Start
    - color: Color for terminal
    - lastTime: Time since last log call
    """
    __slots__ = "color", "start_time", "last_call_time"
    __instance = []
    COLOR_CODES = {
        logging.CRITICAL: "\033[1;35m", # purple
        logging.ERROR:    "\033[1;31m", # red
        logging.WARNING:  "\033[1;33m", # yellow
        logging.INFO:     "\033[0;37m", # white
        logging.DEBUG:    "\033[1;34m", # blue
    }
    RESET_CODE = "\033[0m"

    def __init__(self, color, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _Formatter.__instance.append(self)
        self.color = color
        self.start_time = time.time() 
        self.last_call_time = self.start_time

    def format(self, record, *args, **kwargs):
        if (self.color and record.levelno in self.COLOR_CODES):
            record.color_on, record.color_off = self.COLOR_CODES[record.levelno], self.RESET_CODE
        else:
            record.color_on, record.color_off  = "", ""

        now = time.time()
        record.elapsed_start = (now - self.start_time) * 1000
        record.elapsed_last = (now - self.last_call_time) * 1000
        self.last_call_time = now

        return super().format(record, *args, **kwargs)

    @staticmethod
    def setTimeNow():
        now = time.time()
        for formatter in _Formatter.__instance:
            formatter.startTime = now

class LoggerBase():
    """
    Component LoggerBase system for setting up loggers
    -
    - _level         : sets the level of debuging can be int or [] of levels
    - _level_lambda  : lambda for the filtering of logs default (levelin >= levelset)
    - _color         : enabling color for console
    - _propagate     : enabling the log propagation to parents
    
    """
    __slots__ = "level", "level_lambda", "color", "propagate"
    _date_fmt = "%Y-%m-%d %H:%M:%S"
    _performance_fmt = "%(color_on)s%(asctime)s.%(msecs)03d|%(levelname)-8s|%(message)-30s%(color_off)s"
    _debug_fmt = "%(color_on)s%(asctime)s.%(msecs)03d|%(threadName)10s|%(elapsed_start)5d|%(elapsed_last)5d|%(levelname)-8s | %(message)-30s|%(filename)s|%(funcName)s|%(lineno)s%(color_off)s"

    def __init__(self, _level = logging.DEBUG,
    _color = True, _propagate = False,
    _level_lamda = lambda a, b : a >= b):

        self.level = _level
        self.level_lambda = _level_lamda
        self.color = _color
        self.propagate = _propagate

    def setup(self, handler : logging.StreamHandler , log_instance : logging.Logger):
        """
        Sets up a logging handler with custom formatter and filter
        """
        fmt = self._debug_fmt if self.level <= logging.DEBUG else self._performance_fmt 

        formatter = _Formatter(fmt=fmt, color=self.color, datefmt=self._date_fmt)
        handler.setFormatter(formatter)
        handler.addFilter(_MyFilter(self.level_lambda, self.level))
        log_instance.addHandler(handler)
        log_instance.setLevel(1)
        log_instance.propagate = self.propagate

class Logger():
    """
    Custom Logging Settings Class for Console and Log Files.
    -
    - _log_cmd      : Enable or Disable logging to console
    - _log_file     : Enable or Disable logging to File
    - _logger_name  : Uses logging.getLogger to grab instance
    - _logger_cmd   : Takes Component LoggerBase for console settings
    - _logger_file  : Takes Component LoggerBase for file settings
    - _std          : Takes "stdout" | "stderr"
    - _stdname      : Logging file Name
    - _dir          : Logging folder
    
    Examples
    - Logger(False, False).getInstance()       : Disables logging the global LoggerBase
    - Logger(True, True).getInstance()         : Enables logging for console and files
    - Logger(True, True, "Trace").getInstance(): Creates a new logging instance
    """
    __slots__ = "log_cmd", "log_file", "std", "stdname", \
    "dir", "logger_cmd","logger_file","log_instance"
    
    def __init__(self, 
    _log_cmd = True, 
    _log_file = True,
    _logger_name = "",
    _logger_cmd = LoggerBase(), 
    _logger_file = LoggerBase(_color = False),
    _std = "stdout", _stdname = "main.log", _dir = "logging"):

        self.log_cmd = _log_cmd
        self.log_file = _log_file
        self.std = _std
        self.stdname = _stdname
        self.dir = _dir
        self.logger_cmd = _logger_cmd
        self.logger_file = _logger_file
        self.log_instance = logging.getLogger(_logger_name)

        try:
            self.validate()
            self.setup()
        except Exception as e:
            print("Logger Error: ", e) 

    def validate(self):
        if not isinstance(self.logger_cmd, LoggerBase):
            raise ValueError("logger_cmd is not instance of LoggerBase")
        if not isinstance(self.logger_file, LoggerBase):
            raise ValueError("logger_file is not instance of LoggerBase")

        if self.log_file:
            if self.stdname == "":
                raise ValueError("Cant be empty string")

            os_util.util_makeDirs(self.dir)

        if self.log_cmd:
            if (self.std == "stdout"):
                self.std = sys.stdout
            elif (self.std == "stderr"):
                self.std = sys.stderr
            else:
                raise ValueError("Failed to set console output: invalid output: '%s'" % self.std)

    def setup(self):
        if self.log_cmd == False and self.log_file == False:
            self.log_instance.setLevel(logging.CRITICAL+1)
            return
        if self.log_cmd:
            handler_cmd = logging.StreamHandler(self.std)
            self.logger_cmd.setup(handler_cmd, self.log_instance)
        if self.log_file:
            handler_file = logging.FileHandler(os.path.join(self.dir, self.stdname),"w")
            self.logger_file.setup(handler_file, self.log_instance)

    def setTrace(self):
        """ Override Global Trace With This Instance """
        global _trace
        _trace = self.log_instance

    def getInstance(self):
        """
        Returns Logging Instance
        """
        return self.log_instance

def init():
    """ Inits Base Tracing """
    global _trace
    _trace = Logger(True,True, "Trace").getInstance()

def reset_start_time():
    """ Resets Start Time in the logging system """
    _Formatter.setTimeNow()

def critical(msg, stacklevel = 2, *args, **kwargs):
    _trace.critical(msg, stacklevel=stacklevel, *args, **kwargs)

def info(msg, stacklevel = 2,*args, **kwargs):
    _trace.info(msg, stacklevel=stacklevel, *args, **kwargs)

def debug(msg, stacklevel = 2, *args, **kwargs):
    _trace.debug(msg, stacklevel=stacklevel, *args, **kwargs)

def main():
    from time import sleep
    Logger(True,False)
 
    # Log some messages
    logging.debug("Debug message")
    logging.info("Info message")
    logging.warning("Warning message")

    # Testing not showing
    debug("trace debug Shouldn't show!")
    critical("trace critical Shouldn't show!")
    Logger(True,True,"Trace").setTrace()
    # Should show now
    debug("trace debug")
    critical("trace critical")

    # Testing time
    logging.critical("before sleep")
    sleep(1)
    logging.critical("after sleep")
    reset_start_time()
    logging.debug("after reset time")

if __name__ == '__main__':
    _trace = Logger(False,False,"Trace").getInstance()
    main()
else:
    _trace = Logger(False,False,"Trace").getInstance()


### LOCAL TESTING ###