from shutil import rmtree
import os
import sys
import logging
import time

def util_remove_file(_file):
    try:
        os.remove(_file)
    except:
        pass

def util_remove_dir(_dir):
    try:
        rmtree(_dir)
    except:
        pass

def util_makeDirs(_dir):
    if not os.path.isdir(_dir):
        os.makedirs(_dir)


def util_dbg_check_results(dir):
    """Checks to make sure common files are present in the persistent work dir"""
    currentFiles = os.listdir(dir)
    expectedFiles = ["1_progress.csv.gz",
                     "1_trace.json.gz",
                     "1_user_timing.json.gz",
                     "1_timeline_cpu.json.gz",
                     "1_script_timing.json.gz",
                     "1_interactive.json.gz",
                     "1_long_tasks.json.gz",
                     "1_feature_usage.json.gz",
                     "1_v8stats.json.gz",
                     "1_screen.jpg",
                     "1_console_log.json.gz",
                     "1_timed_events.json.gz",
                     "1.0.histograms.json.gz",
                     "1_visual_progress.json.gz",
                     "1_devtools_requests.json.gz",
                     "1_page_data.json.gz"]

    for file in expectedFiles:
        if file not in currentFiles:
            logging.log(8,f"{file}: WAS NOT FOUND BUT IT WAS EXPECTED")
            logging.critical(f"{file}: WAS NOT FOUND BUT IT WAS EXPECTED")

class MyFilter(object):
    def __init__(self, lambda_func, level):
        self.__lambda = lambda_func
        self.__level = level

    def filter(self, logRecord):
        return self.__lambda(logRecord.levelno, self.__level)
class LogFormatter(logging.Formatter):
    __instance = None
    COLOR_CODES = {
        logging.CRITICAL: "\033[1;35m", # bright/bold magenta
        logging.ERROR:    "\033[1;31m", # bright/bold red
        logging.WARNING:  "\033[1;33m", # bright/bold yellow
        logging.INFO:     "\033[0;37m", # white / light gray
        logging.DEBUG:    "\033[1;30m", # bright/bold black / dark gray
        8:                "\033[1;36m", # blue   
    }

    RESET_CODE = "\033[0m"

    def __init__(self, color, *args, **kwargs):
        if LogFormatter.__instance != None:
            return LogFormatter.__instance
        LogFormatter.__instance = self
        super(LogFormatter, self).__init__(*args, **kwargs)
        self.color = color
        self.startTime = time.time()
        self.lastTime = self.startTime

    def format(self, record, *args, **kwargs):
        if (self.color == True and record.levelno in self.COLOR_CODES):
            record.color_on  = self.COLOR_CODES[record.levelno]
            record.color_off = self.RESET_CODE
        else:
            record.color_on  = ""
            record.color_off = ""

        curTime = time.time()
        record.time = (curTime - self.lastTime) * 1000
        record.startTime = (curTime - self.startTime) * 1000 
        self.lastTime = curTime

        return super(LogFormatter, self).format(record, *args, **kwargs)

    @staticmethod 
    def get():
        """ Static access method. """
        if LogFormatter.__instance == None:
            LogFormatter()
        return LogFormatter.__instance

    @staticmethod
    def set_time_now():
        LogFormatter.__instance.startTime = time.time()

# Setup logging
def wptutil_setup_logging(
        console_log = True,
        console_log_output = "stdout", 
        console_log_level = logging.DEBUG, 
        console_log_color = True,
        console_lambda = lambda a, b : a >= b,
        logfile = True,
        logfile_path = "logging",
        logfile_file = "main.log", 
        logfile_log_level = [8], 
        logfile_log_color = False,
        logfile_lambda = lambda a, b : a in b,
        datefmt = '%Y-%m-%d %H:%M:%S',
        log_format ="%(color_on)s%(asctime)s.%(msecs)03d|%(threadName)10s|%(startTime)5d|%(time)5d|%(levelname)-8s | %(message)-30s|%(filename)s|%(funcName)s|%(lineno)s%(color_off)s"):

    # Custom Logging Level
    logging.addLevelName(8,"PERF")
    # Custom Logging Formater
    LogFormatter(fmt=log_format, color=console_log_color, datefmt=datefmt)
    # Global Logger Setup
    logger = logging.getLogger()
    # Global Logger Level
    logger.setLevel(1)

    
    # Create console handler
    if console_log:
        console_log_output = console_log_output.lower()
        if (console_log_output == "stdout"):
            console_log_output = sys.stdout
        elif (console_log_output == "stderr"):
            console_log_output = sys.stderr
        else:
            print("Failed to set console output: invalid output: '%s'" % console_log_output)
            return False
        console_handler = logging.StreamHandler(console_log_output)

        # Create and set formatter, add console handler to logger
        console_formatter = LogFormatter.get()
        console_handler.setFormatter(console_formatter)
        console_handler.addFilter(MyFilter(console_lambda, console_log_level))
        logger.addHandler(console_handler)


    if logfile:
        # Create log file Folder
        util_makeDirs(logfile_path)

        # Create log file handler
        try:
            logfile_handler = logging.FileHandler(os.path.join(logfile_path,logfile_file),"w")
        except Exception as exception:
            print("Failed to set up log file: %s" % str(exception))
            return False

        #logfile_formatter = LogFormatter(fmt=log_format , color=logfile_log_color,datefmt=datefmt)
        logfile_formatter = LogFormatter.get()
        logfile_handler.setFormatter(logfile_formatter)
        logfile_handler.addFilter(MyFilter(logfile_lambda, logfile_log_level))
        logger.addHandler(logfile_handler)

    # Success
    return True

# Main function
def main():

    if (not wptutil_setup_logging()):
        print("Failed to setup logging, aborting.")
        return 1
    from time import sleep
    # Log some messages
    logging.log(8,"here")
    logging.debug("Debug message")
    logging.info("Info message")
    logging.warning("Warning message")
    sleep(1)
    logging.log(7,"here")
    LogFormatter.set_time_now()
    logging.log(1,"here")
    logging.log(1,"test")
    logging.error("Error message")
    logging.log(8,"here")
    
    logging.critical("Critical message")
