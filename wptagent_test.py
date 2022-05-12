import logging
import pytest
import warnings
import os

def test_imports():
    # pylint: disable=W0611
    import logging
    import gzip
    import zipfile
    import re
    import traceback
    import psutil
    import glob

    import hashlib
    import multiprocessing
    import shutil
    import threading

    import gc
    import PIL
    import numpy
    import requests
    import selenium
    
    import wptagent
    from internal.browsers import Browsers
    from internal.webpagetest import WebPageTest
    from internal.traffic_shaping import TrafficShaper
    from internal.adb import Adb
    from internal.ios_device import iOSDevice
    


    try:
        import ujson as json
    except BaseException:
        warnings.warn(UserWarning("Ujson couldn't import, defaulting to json lib"))
        import json
