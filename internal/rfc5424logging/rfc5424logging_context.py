from enum import Enum

class MessageId(Enum):
    """Represent all messages ids, utilized as phases in wptagent, that can be used in the code."""
    INIT="initialization"

class LoggingContext():
    """Collection additional information to be passed as extra argument to each logging call."""

    def __init__(self):
        self.message_id = MessageId.INIT
        self.structured_data = dict()
        pass

    def set_message_id(self, id):
        """Set the message id, effectively changing phase of the agent."""
        self.message_id = id

    def as_extra(self, one_time_data=None):
        """Get dictionary format to pass to logging extra parameter."""
        structured_data = self.structured_data
        if one_time_data != None and isinstance(one_time_data, dict):
            structured_data = dict()
            structured_data.update(self.structured_data)
            structured_data.update(one_time_data)

        # see Rfc5424SysLogHandler.get_structured_data for details
        return {'msgid': self.message_id.value, 'structured_data': structured_data}

context = LoggingContext()

def logging_context():
    """Get the global context."""
    return context
