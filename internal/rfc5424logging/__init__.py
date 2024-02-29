"""
This library offers an logging in rfc5424 format.

Is based on https://github.com/jobec/rfc5424-logging-handler that is not currently maintained.

A few changes needed to be implemented anyway to tailor the logging to wptagent, hence the hard copy.
"""

from .handler import Rfc5424SysLogHandler
from .rfc5424logging_context import logging_context