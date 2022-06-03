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
            logging.log(7,f"{file}: WAS NOT FOUND BUT IT WAS EXPECTED")
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
        logging.CRITICAL: "\033[1;35m", # purple
        logging.ERROR:    "\033[1;31m", # red
        logging.WARNING:  "\033[1;33m", # yellow
        logging.INFO:     "\033[0;32m", # green
        logging.DEBUG:    "\033[1;37m", # gray
        8:                "\033[1;37m", # white
        7:                "\033[1;31m", # red
        6:                "\033[1;32m", # green     
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
        logfile_log_level = 8, 
        logfile_log_color = False,
        logfile_lambda = lambda a, b : a <= b,
        datefmt = '%Y-%m-%d %H:%M:%S',
        log_format ="%(color_on)s%(asctime)s.%(msecs)03d|%(threadName)10s|%(startTime)5d|%(time)5d|%(levelname)-8s | %(message)-30s|%(filename)s|%(funcName)s|%(lineno)s%(color_off)s"):

    # Custom Logging Level
    logging.addLevelName(8,"PERFDEB")
    logging.addLevelName(7,"PERFERR")
    logging.addLevelName(6,"PERFINFO")
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

### LOCAL TESTING ###

def grab_working_dir():
    try:
        dirpath = "./work/{}".format(os.listdir("work")[0])
    except:
        logging.warning(UserWarning("No WorkPath Was Found")) 
        exit(1)
    
    for dir in os.listdir(dirpath): # Finding the folder with browser data in it
        if ".1.0" in dir:
            dirpath = os.path.join(os.getcwd(),dirpath,dir) # work/workID.1.0/
            break
    return dirpath

def setup_devtools_opt():
    """Finds the name of the work dir and sets everything up for devtools parser"""
    ### SETUP FOR FINDING WORKING PATH ###
    if not os.path.isdir("work"): # Check to See if the Work Dir is present
        logging.warning(UserWarning("No Work Dir Was Found")) 

    devtools_opt = {'devtools': "1_devtools.json.gz",
                    'netlog': "1_netlog_requests.json.gz",
                    'requests': "1_timeline_requests.json.gz",
                    'optimization': "1_optimization.json.gz",
                    'user': "1_user_timing.json.gz",
                    'coverage': None,
                    'cpu': "1_timeline_cpu.json.gz",
                    'v8stats': "1_v8stats.json.gz",
                    'cached': None,
                    'out': None,
                    'noheaders': None,
                    'new_func': None}
                    
    dirpath = grab_working_dir()

    for key,file in devtools_opt.items(): # For each item in devtools_opt we want to check if its a file
        if file == None:
            continue
        devopt = os.path.join(dirpath,file)
        devtools_opt[key] = devopt # work/workID.1.0/1_filename.json.gz
        if not os.path.isfile(devtools_opt[key]): # If not a file set None
            devtools_opt[key] = None

    return devtools_opt, dirpath
    ### END OF SETUP FOR FINDING WORKING PATH ###


def devtools_parser(): 
    """Runs the Devtool processs tools, with new and old functions"""
    from support import devtools_parser as dp
    # Init devtools_opt and dirpath
    devtools_opt, dirpath = setup_devtools_opt() 
    
    # Set up config
    devtools_opt["out"] = f"{dirpath}/oldData.json" # Set the out file
    devtools_opt["cached"] = 0
    devtools_opt["noheaders"] = False 

    logging.log(6,"*** (OLD) Running Devtools ***")
    # Init DevtoolsParser
    devtools = dp.DevToolsParser(devtools_opt)
    devtools.process()

    LogFormatter.set_time_now()
    # Init devtools_opt and dirpath
    devtools_opt, dirpath = setup_devtools_opt() 
    # Set up config to run new functions
    devtools_opt["new_func"] = True
    devtools_opt["cached"] = 0
    devtools_opt["noheaders"] = False 
    devtools_opt["out"] = f"{dirpath}/newData.json" # Set the out file

    logging.log(6,"*** (NEW) Running Devtools ***")

    # Init DevtoolsParser
    devtools = dp.DevToolsParser(devtools_opt)
    devtools.process()
    

def devtools_parser_compare_data():
    oldData = {}
    newData = {}
    dirpath = grab_working_dir()
    
    # Import the nested_diff for comparing profil data
    try: 
        from nested_diff import diff
    except:
        logging.exception("Please install nested_diff: pip3 install nested_diff")
        return
    import ujson as json
    
    # Read Pagedata in from Files
    with open("{}/{}".format(dirpath,"oldData.json")) as f_in:
        oldData = json.load(f_in)
    with open("{}/{}".format(dirpath,"newData.json")) as f_in:
        newData = json.load(f_in)
    # Put Info in about what each letter stands for.    
    diffC = {"info":{
                "A": "stands for 'added', it's value - added item.",
                "C": "is for comments; optional, value - arbitrary string.",
                "D": "means 'different' and contains subdiff.",
                "E": "diffed entity (optional), value - empty instance of entity's class.",
                "I": "index for sequence item, used only when prior item was omitted.",
                "N": "is a new value for changed item.",
                "O": "is a changed item's old value.",
                "R": "key used for removed item.",
                "U": "represent unchanged item.",
            },
            "diff": {}
        }
    diffC['diff'].update(diff(oldData,newData,U=False))
    with open(f"{dirpath}/pageDataComp.json", "w") as f_out:
        json.dump(diffC,f_out)

    print("** Checking For Differences **")
    if any(diffC['diff']): # If not empty {} then print diff and fail
        print("Keys:\n{}\nDifferences:\n{}".format(diffC['info'],diffC['diff']))
        assert False
    else: # Else No Differences
        print("    No Differences Found\n")

def devtools_parser_log_print():
    orginal = ""
    with open("./logging/main.log") as f_in:
        orginal = f_in.read()

    print(orginal)

import argparse
parser = argparse.ArgumentParser(description='WebPageTest Agent.', prog='wpt-agent')

parser.add_argument('-v', '--verbose', action='count',help="Increase verbosity (specify multiple times for more)."
                    " -vvvv for full debug output.")

parser.add_argument('-d', action='store_true', default=False, help="Runs Metrics Devtools on the local store files")

#parser.add_argument('-r', type=int, default=1, help="Prints the avg of running the new and old functions")
parser.add_argument('-c', action='store_true', default=False, help="Prints the Compared Data from new and old functions")
parser.add_argument('-l', action='store_true', default=False, help="Prints the default logs")


if __name__ == "__main__":
    import warnings
    options, _ = parser.parse_known_args()

    FunctionStack = []

    if options.d:
        wptutil_setup_logging(console_lambda=lambda a, b : a <= b, console_log_level=8, logfile=False)
        FunctionStack.append(devtools_parser)
        logging.shutdown()
    if options.c:
        FunctionStack.append(devtools_parser_compare_data)
    if options.l:
        FunctionStack.append(devtools_parser_log_print)
    #for i in range(options.r): # Run count
    if len(FunctionStack) == 0:
        main()
    else:
        for f in FunctionStack: # Call the function Stack
            f()

