import logging
import time
from datetime import datetime
import os
import json
import atexit
import platform


def util_remove_file(_file):
    try:
        os.remove(_file)
    except Exception:
        pass


def util_makeDirs(_dir):
    if not os.path.isdir(_dir):
        os.makedirs(_dir)


def util_dbg_check_results(dir):
    """Checks to make sure common files are present in the persistent work dir"""
    # CHECKS TO MAKE SURE RESULT OUTPUT HAS EXPECTED FILES
    currentFiles = os.listdir(dir[:-5])
    for file in currentFiles:
        if file.endswith(".1.0"):
            dir = f"{dir[:-5]}/{file}"
            break

    currentFiles = os.listdir(dir)
    expectedFiles = {"1_progress.csv.gz": 0,
                     "1_trace.json.gz": 1000,
                     "1_user_timing.json.gz": 1000,
                     "1_timeline_cpu.json.gz": 1000,
                     "1_script_timing.json.gz": 100,
                     "1_interactive.json.gz": 100,
                     "1_long_tasks.json.gz": 1,
                     "1_feature_usage.json.gz": 1,
                     "1_v8stats.json.gz": 1,
                     "1_screen.jpg": 20,
                     "1_console_log.json.gz": 50,
                     "1_timed_events.json.gz": 1,
                     "1.0.histograms.json.gz": 1,
                     "1_visual_progress.json.gz": 10000,
                     "1_devtools_requests.json.gz": 1000,
                     "1_page_data.json.gz": 300}

    for file, v in expectedFiles.items():
        if file not in currentFiles:
            LogSingleton.write(f"{file}: WAS NOT FOUND BUT IT WAS EXPECTED")
            logging.critical(f"{file}: WAS NOT FOUND BUT IT WAS EXPECTED")


def util_dbg_options(options):
    LogSingleton(log=True, profile=True)
    options.verbose = 1

    if platform.system() == "Linux":
        logging.critical("Setting default arguments")
        options.dockerized = True
        options.xvfb = True
        options.noidle = True
        options.location = 'Test'
        options.testout = 'id'
        options.browser = 'Chrome'

    if options.testurl == None or "light" in options.testurl:  # Add a medium option?
        options.testurl = "https://www.google.com/"
    elif "heavy" in options.testurl:
        options.testurl = "http://sqa.3genlabs.net/hawksyntheticpageserver/Main.ashx?type=html&details=%22image%22:{%22count%22:100,%22height%22:1024,%22width%22:1080,%22delay%22:0,%22redirect%22:5},%22css%22:{%22count%22:100,%22size%22:2700,%22delay%22:0,%22redirect%22:5},%22iframe%22:{%22count%22:50,%22rawtext%22:{%22linebreak%22:100,%22asciistart%22:33,%22asciiend%22:126,%22random%22:true,%22bytecount%22:900000},%22delay%22:0,%22redirect%22:5},%22iframe%22:{%22count%22:10,%22size%22:800000,%22delay%22:0,%22redirect%22:10}"


class LogSingleton:
    __instance = None

    def __init__(self, workDir: str = "logging", logFileName: str = "log.txt", profileFileName: str = "profile.json",
                 log: bool = False, profile: bool = False,
                 grabFunctionName: bool = True):
        """ Virtually private constructor. """
        if LogSingleton.__instance != None:
            raise Exception("Class is a singleton.")
        else:
            LogSingleton.__instance = self

        self.cwd = os.getcwd()
        self.workDir = workDir
        self.logFileName = logFileName
        self.profileFileName = profileFileName

        self.f_out_log = None  # a Log File type txt for logging out to
        self.f_out_profile = None  # a profile file type json to write to

        self.starttime = time.time()  # Start time
        self.lastcalltime = time.time()  # For logging last call time
        # true false value for heavy stack inspection function
        self.grabFunctionName = grabFunctionName
        self.grabProfile = profile  # true false value for grabing profile data
        self.grabLogs = log  # true false value for grabing logs
        self.logTime = True  # global log timing system
        self.logTotalTime = 0  # Total log time

        if self.grabLogs:
            self.f_out_log = open(
                f"{self.cwd}/{workDir}/{logFileName}", "w", buffering=1)

        if self.grabProfile:
            self.f_out_profile = open(
                f"{self.cwd}/{workDir}/{profileFileName}", "w", buffering=1)
            self.grabFunctionName == True
            self.profileData = {}

        if self.grabFunctionName:
            self.inspect = __import__("inspect")

        util_makeDirs(self.workDir)

        atexit.register(self.done)

    @staticmethod
    def get():
        """ Static access method. """
        if LogSingleton.__instance == None:
            LogSingleton()
        return LogSingleton.__instance

    @staticmethod
    def done():
        if LogSingleton.__instance == None:
            return
        LogSingleton.__instance.__del__()

    @staticmethod
    def write(_out: str = "", grabF=True):
        """Log writing tool that outputs to .txt file. This tool is meant to be used to debug testruns and not meant to be running in a server setting.
        _out is the string going to the .txt file.
        _grabF enables or disables a resource intensive stack calling(avg 5ms per call) for grabing the name of the function that called this write function"""
        if LogSingleton.__instance == None or LogSingleton.__instance.grabLogs == False:
            return

        self, logTime, _fileName, _lineNumber, _cn = LogSingleton.__instance, 0, "", "", ""

        if self.logTime:  # Check if Logging is enabled
            logTime = time.time()
            # Clear the dec with the int then cast back str
            elapsed = str(int((logTime - self.lastcalltime) * 1000))
            self.lastcalltime = logTime  # Set Last call time

        if self.grabFunctionName and grabF == True:  # Grabs Stack Info
            stack = self.inspect.stack()
            _fileName, _lineNumber, _cn = stack[1].filename.replace(
                self.cwd, ""), stack[1].lineno, stack[1][3]

        _timestamp = datetime.now().strftime('%m/%d/%Y %H:%M:%S.%f')[:-3]
        _timeFromStart = str(int((time.time() - self.starttime)*1000))

        self.f_out_log.write(
            f"{_timestamp} | {_timeFromStart.ljust(5)} | {elapsed.ljust(5)} | {_out.ljust(65)} | {_fileName}|{_cn}|{str(_lineNumber)}>\n")

        if self.logTime:  # Finish Time of Logger
            self.logTotalTime += time.time() - logTime

    @staticmethod
    def prof(_cn: str = "", _des: str = "", **data):
        """Profiler for functions, Take _cn (CallerName for specific Function), _des(Description if you want), data which can be compared later or tested
        for similarity, This function should be called once before and once after with same _cn name.\n
        example: \n
        prof("randomFunctionName", randomData=randomData)\n
        function_to_be_profiled(randomData)\n
        prof("randomFunctionName", randomData=randomData)"""
        if LogSingleton.__instance == None:
            return
        self = LogSingleton.__instance  # Set self

        if self.grabProfile == False:  # Check if we are profiling
            return

        secTime = (time.time() - self.starttime) * 1000  # Timeing

        if _cn == "":  # If we should call stack
            _cn = self.inspect.stack()[1][3]

        if self.profileData.setdefault(_cn, {"description": _des, "start_ms": 0.0, "end_ms": 0.0, "dif_ms": 0.0, "Similar": {}, "data": {"before": {}, "after": {}}})["start_ms"] == 0.0:
            self.profileData[_cn]["start_ms"] = round(secTime, 4)
            for key, value in data.items():  # If Data is passes it will be set into the _cn
                self.profileData[_cn]['data']['before'][key] = value
            return
        elif(self.profileData[_cn]["dif_ms"] == 0.0):
            self.profileData[_cn]['end_ms'] = round(secTime, 4)
            self.profileData[_cn]['dif_ms'] = round(
                secTime - self.profileData[_cn]['start_ms'], 4)

            for key, value in data.items():
                self.profileData[_cn]['data']['after'][key] = value
                # If Data was passed twice then data is checked for similarity
                if key in self.profileData[_cn]['data']['before']:
                    self.profileData[_cn]["Similar"][key] = self.profileData[_cn]['data'][
                        'before'][key] == self.profileData[_cn]['data']['after'][key]
            #json.dump(self.profileData, self.f_out_profile)
            #self.profileData = {}
            return
        else:
            print(f"log_profiler: Was called one to many times in {_cn}")

    @staticmethod
    def comp(_cn: str = "", _cn1: str = "", _cn2: str = "", _data: list = []):
        """Compare looks at data you gave to the profiler, _cn is a name for this in the json, _cn1 is the first callername passed to the profil function.\n
        _cn2 is the second callername passed to the profil function\n 
        data is the str = of data fields passed to profiler\n
        logs.comp("CompareOfRandom","ProfiledFunctionName","ProfiledFunctionName2",["randomData",...etc])"""
        if LogSingleton.__instance == None:
            return
        self = LogSingleton.__instance  # Set self

        self.profileData[_cn] = {}
        for key in _data:
            if key not in self.profileData[_cn1]['data']['after'] and key not in self.profileData[_cn2]['data']['after']:
                logging.critical("AFTER COMP WAS NOT FOUND")
                return
            self.profileData[_cn][key] = {
                "Not_Found_Keys_From_C1": [], "Differnce": {}}
            cn1, cn2 = self.profileData[_cn1]['data']['after'][key], self.profileData[_cn2]['data']['after'][key]
            if isinstance(cn1, dict) and isinstance(cn2, dict):
                for cnkey in cn1.keys():
                    if cnkey not in cn2:
                        self.profileData[_cn][key]["Not_Found_Keys_From_C1"].append(
                            cnkey)
                    elif cn1[cnkey] != cn2[cnkey]:
                        self.profileData[_cn][key]["Differnce"][cnkey] = {
                            _cn1: cn1[cnkey], _cn2: cn2[cnkey]}

    def __del__(self):
        try:
            if self.grabProfile:
                json.dump(self.profileData, self.f_out_profile)
                self.f_out_profile.close()

            if self.grabLogs:
                if self.logTime:
                    self.write(
                        _out=f"Total Log Time taken in MS : {int(self.logTotalTime * 1000)}")
                self.f_out_log.close()

            LogSingleton.__instance = None
        except:
            pass


def main():
    LogSingleton(log=True, profile=True)
    LogSingleton.write("testing", False)
    LogSingleton.write("testing", False)
    data = "HERE"
    LogSingleton.prof("WriteFunction", data=data)
    LogSingleton.write(data, False)
    data.lower()
    LogSingleton.prof("WriteFunction", data=data)

    data = "HERE"
    LogSingleton.prof("WriteFunction2", data=data)
    LogSingleton.write(data, False)
    data.upper()
    LogSingleton.prof("WriteFunction2", data=data)
    LogSingleton.comp("WriteFunctionComp", "WriteFunction",
                      "WriteFunction2", ["data"])
