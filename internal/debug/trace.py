import os
import logging
import time
import sys

from internal.os_util import makeDirs

_TRACE = 8

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
    def set_time_now():
        now = time.time()
        for formatter in _Formatter.__instance:
            formatter.start_time = now

class LoggerBase():
    """
    Component LoggerBase system for setting up loggers
    -
    - _level         : sets the level of debuging can be int or [] of levels
    - _level_lambda  : lambda for the filtering of logs default (levelin >= levelset)
    - _color         : enabling color for console
    - _propagate     : enabling the log propagation to parents
    
    """
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


def addLoggingLevel(levelName, levelNum, methodName=None):
    """
    Comprehensively adds a new logging level to the `logging` module and the
    currently configured logging class.

    `levelName` becomes an attribute of the `logging` module with the value
    `levelNum`. `methodName` becomes a convenience method for both `logging`
    itself and the class returned by `logging.getLoggerClass()` (usually just
    `logging.Logger`). If `methodName` is not specified, `levelName.lower()` is
    used.

    To avoid accidental clobberings of existing attributes, this method will
    raise an `AttributeError` if the level name is already an attribute of the
    `logging` module or if the method name is already present 

    Example
    -------
    >>> addLoggingLevel('TRACE', logging.DEBUG - 5)
    >>> logging.getLogger(__name__).setLevel("TRACE")
    >>> logging.getLogger(__name__).trace('that worked')
    >>> logging.trace('so did this')
    >>> logging.TRACE
    5

    """
    if not methodName:
        methodName = levelName.lower()

    if hasattr(logging, levelName):
       raise AttributeError('{} already defined in logging module'.format(levelName))
    if hasattr(logging, methodName):
       raise AttributeError('{} already defined in logging module'.format(methodName))
    if hasattr(logging.getLoggerClass(), methodName):
       raise AttributeError('{} already defined in logger class'.format(methodName))

    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            self._log(levelNum, message, args, **kwargs)
    def logToRoot(message, *args, **kwargs):
        logging.log(levelNum, message, stacklevel=3, *args, **kwargs)

    logging.addLevelName(levelNum, levelName)
    setattr(logging, levelName, levelNum)
    setattr(logging.getLoggerClass(), methodName, logForLevel)
    setattr(logging, methodName, logToRoot)


def setup(_dir = "logging", _stdname = "main.log"):
    handler_cmd = logging.StreamHandler(sys.stdout)
    log_cmd = LoggerBase(logging.DEBUG,True)
    log_cmd.setup(handler_cmd, logging.getLogger())

    makeDirs(_dir)
    handler_file = logging.FileHandler(os.path.join(_dir, _stdname),"w")
    log_trace = LoggerBase(logging.TRACE, False, False, lambda a, b : a == b,)
    log_trace.setup(handler_file, logging.getLogger())

def main():
    setup()
    logging.debug("test")
    logging.trace("tests")


addLoggingLevel("TRACE", _TRACE, "trace")

if __name__ == '__main__':
    # to Run "python3 -m internal.debug.trace"
    main()
