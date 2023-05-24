"""A filter for our logging handler"""

import logging

class LoggingFilter(logging.Filter):
    def __init__(self, location, agent_name):
        self.location = location
        self.agent_name = agent_name
        self.test_id = ''
    def filter(self, record):
        record.location = self.location
        record.agent_name = self.agent_name
        record.test_id = self.test_id
        return True
    def set_test_id(self, test_id):
        self.test_id = test_id

