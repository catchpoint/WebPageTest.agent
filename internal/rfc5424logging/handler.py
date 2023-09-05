# coding=utf-8
import socket
import sys
from codecs import BOM_UTF8
from collections import OrderedDict
from datetime import datetime
from logging import Handler

from pytz import utc

from rfc5424logging import transport

NILVALUE = '-'

SP = b' '
# As defined in RFC5424 Section 7
REGISTERED_SD_IDs = ('timeQuality', 'origin', 'meta')
SYSLOG_VERSION = '1'

EMERGENCY = 70
EMERG = EMERGENCY
ALERT = 60
NOTICE = 25

PY2 = sys.version_info[0] == 2


#  facility codes
LOG_KERN = 0       # kernel messages
LOG_USER = 1       # random user-level messages
LOG_MAIL = 2       # mail system
LOG_DAEMON = 3     # system daemons
LOG_AUTH = 4       # security/authorization messages
LOG_SYSLOG = 5     # messages generated internally by syslogd
LOG_LPR = 6        # line printer subsystem
LOG_NEWS = 7       # network news subsystem
LOG_UUCP = 8       # UUCP subsystem
LOG_CRON = 9       # clock daemon
LOG_AUTHPRIV = 10  # security/authorization messages (private)
LOG_FTP = 11       # FTP daemon
#  other codes through 15 reserved for system use
LOG_LOCAL0 = 16    # reserved for local use
LOG_LOCAL1 = 17    # reserved for local use
LOG_LOCAL2 = 18    # reserved for local use
LOG_LOCAL3 = 19    # reserved for local use
LOG_LOCAL4 = 20    # reserved for local use
LOG_LOCAL5 = 21    # reserved for local use
LOG_LOCAL6 = 22    # reserved for local use
LOG_LOCAL7 = 23    # reserved for local use


class Rfc5424SysLogHandler(Handler):
    """
    A handler class which sends RFC 5424 formatted logging records to a syslog server.

    Based on the python built-in SyslogHandler class, but simplified in some parts.
    """
    # from <linux/sys/syslog.h>:
    # ======================================================================
    # priorities/facilities are encoded into a single 32-bit quantity, where
    # the bottom 3 bits are the priority (0-7) and the top 28 bits are the
    # facility (0-big number). Both the priorities and the facilities map
    # roughly one-to-one to strings in the syslogd(8) source code.  This
    # mapping is included in this file.
    #
    # priorities (these are ordered)
    LOG_EMERG = 0    # system is unusable
    LOG_ALERT = 1    # action must be taken immediately
    LOG_CRIT = 2     # critical conditions
    LOG_ERR = 3      # error conditions
    LOG_WARNING = 4  # warning conditions
    LOG_NOTICE = 5   # normal but significant condition
    LOG_INFO = 6     # informational
    LOG_DEBUG = 7    # debug-level messages

    priority_map = {
        "DEBUG": LOG_DEBUG,
        "INFO": LOG_INFO,
        "NOTICE": LOG_NOTICE,
        "WARNING": LOG_WARNING,
        "ERROR": LOG_ERR,
        "CRITICAL": LOG_CRIT,
        "ALERT": LOG_ALERT,
        "EMERGENCY": LOG_EMERG,
        "EMERG": LOG_EMERG,
    }

    def __init__(
            self,
            address=('localhost', transport.SYSLOG_PORT),
            facility=LOG_USER,
            socktype=socket.SOCK_DGRAM,
            framing=transport.FRAMING_NON_TRANSPARENT,
            msg_as_utf8=True,
            hostname=None,
            appname=None,
            procid=None,
            structured_data=None,
            enterprise_id=None,
            utc_timestamp=False,
            timeout=5,
            tls_enable=False,
            tls_ca_bundle=None,
            tls_verify=True,
            tls_client_cert=None,
            tls_client_key=None,
            tls_key_password=None,
            stream=None,
    ):
        """
        Returns a new instance of the Rfc5424SysLogHandler class intended to communicate with
        a remote machine whose address is given by address in the form of a (host, port) tuple.
        If address is not specified, ``('localhost', 514)`` is used. The address is used to open a
        socket.

        An alternative to providing a (host, port) tuple is providing an address as a
        string, for example `/dev/log`. In this case, a Unix domain socket is used to send the
        message to the syslog. If facility is not specified, LOG_USER is used. The type of
        socket opened depends on the socktype argument, which defaults to socket.SOCK_DGRAM
        and thus opens a UDP socket. To open a TCP socket (for use with the newer syslog
        daemons such as rsyslog), specify a value of socket.SOCK_STREAM.

        Note that if your server is not listening on UDP port 514, SysLogHandler may appear
        not to work. In that case, check what address you should be using for a domain socket
        - it's system dependent. For example, on Linux it's usually ``/dev/log`` but on OS/X
        it's ``/var/run/syslog``. You'll need to check your platform and use the appropriate
        address (you may need to do this check at runtime if your application needs to run
        on several platforms). On Windows, you pretty much have to use the UDP option.

        As an alternative transport, you can also provide a stream

        Args:
            address (tuple|list):
                address in the form of a (host, port) tuple or list

            facility (int):
                One of the ``rfc5424logging.LOG_*`` values.

            socktype (int):
                One of ``socket.SOCK_STREAM`` (TCP) or ``socket.SOCK_DGRAM`` (UDP).

            framing (int, optional):
                One of the ``rfc5424logging.FRAMING_*`` values according to
                RFC6587 section 3.4. Only applies when sockettype is ``socket.SOCK_STREAM`` (TCP)
                and is used to give the syslog server an indication about the boundaries
                of the message. Defaults to ``FRAMING_NON_TRANSPARENT`` which will escape all
                newline characters in the message and end the message with a newline character.
                When set to ``FRAMING_OCTET_COUNTING``, it will prepend the message length to the
                begin of the message.
            msg_as_utf8 (bool):
                Controls the way the message is sent.
                disabling this parameter sends the message as MSG-ANY (RFC2424 section 6), avoiding
                issues with receivers that don't support the UTF-8 Byte Order Mark (BOM) at
                the beginning of the message.
            hostname (str):
                The hostname of the system where the message originated from.
                Defaults to the values returned by ``socket.gethostname()``
            appname (str):
                The name of the application. Defaults to the name of the logger that sent
                the message.
            procid (any):
                The process ID of the sending application. Defaults to the ``process`` attribute
                of the log record.
            structured_data (dict):
                A dictionary with structured data that is added to every message. Per message your
                can add more structured data by adding it to the ``extra`` argument of the log function.
            enterprise_id (str):
                The Private Enterprise Number. This is used to compose the structured data IDs when
                they do not include an Enterprise ID and are not one of the reserved structured data IDs.
                Can be a single PEN like ``32473`` or optionally contain sub-identifiers like ``32473.2.6``
            utc_timestamp (bool):
                Whether the timestamp should be converted to UTC time or kept in the local timezone
            timeout (int):
                Sets the timeout on the connection to the server.
            tls_enable (bool):
                If set to ``True``, it sets up a TLS/SSL connection to the address specified in ``address``
                over which the syslog messages will be sent.
                Default to ``False``
            tls_ca_bundle (str):
                The path to a bundle of CA certificates used for validating the remote server's identity.
                If set to ``None``, it will try to load the default CA as described in
                https://docs.python.org/3/library/ssl.html#ssl.SSLContext.load_verify_locations
            tls_verify (bool):
                Whether to verify the certificate of the server.
            tls_client_cert (str):
                path to a file containing a client certificate.
            tls_client_key (str):
                Path to a file containing the client private key.
            tls_key_password (str):
                Optionally the password for decrypting the specified private key.
            stream (io.BufferedIOBase, file, io.TextIOBase):
                Optionally a stream object to send the message to. See https://docs.python.org/3/library/io.html
                for details.
        """
        super(Rfc5424SysLogHandler, self).__init__()

        self.address = address
        self.facility = facility
        self.socktype = socktype
        self.socket = None
        self.unixsocket = False
        self.hostname = hostname
        self.appname = appname
        self.procid = procid
        self.structured_data = structured_data
        self.enterprise_id = enterprise_id
        self.framing = framing
        self.msg_as_utf8 = msg_as_utf8
        self.utc_timestamp = utc_timestamp
        self.timeout = timeout
        self.tls_enable = tls_enable
        self.tls_ca_bundle = tls_ca_bundle
        self.tls_verify = tls_verify
        self.tls_client_cert = tls_client_cert
        self.tls_client_key = tls_client_key
        self.tls_key_password = tls_key_password
        self.stream = stream
        self.transport = None

        from tzlocal import get_localzone
        self._local_zone = get_localzone()

        if not (isinstance(self.facility, int) and LOG_KERN <= self.facility <= LOG_LOCAL7):
            raise ValueError("Facility is not valid")

        if self.hostname is None or self.hostname == '':
            self.hostname = socket.gethostname()
        if not isinstance(self.structured_data, dict):
            self.structured_data = OrderedDict()

        self._setup_transport()

    def _setup_transport(self):
        if self.stream is not None:
            self.transport = transport.StreamTransport(self.stream)
        elif isinstance(self.address, str):
            self.transport = transport.UnixSocketTransport(self.address, self.socktype)
        elif isinstance(self.address, (tuple, list)):
            if self.socktype == socket.SOCK_STREAM:
                if self.tls_enable:
                    self.transport = transport.TLSSocketTransport(
                        self.address, self.timeout, self.framing,
                        self.tls_ca_bundle, self.tls_verify,
                        self.tls_client_cert, self.tls_client_key, self.tls_key_password
                    )
                else:
                    self.transport = transport.TCPSocketTransport(self.address, self.timeout, self.framing)
            else:
                self.transport = transport.UDPSocketTransport(self.address, self.timeout)
        else:
            raise ValueError("Unsupported address type")

    def encode_priority(self, facility, priority):
        """
        Encode the facility and priority. You can pass in strings or
        integers - if strings are passed, the facility_names and
        priority_names mapping dictionaries are used to convert them to
        integers.
        """
        return (facility << 3) | self.priority_map.get(priority, self.LOG_WARNING)

    @staticmethod
    def filter_printusascii(str_to_filter):
        return ''.join([x for x in str_to_filter if 33 <= ord(x) <= 126])

    def get_hostname(self, record):
        hostname = getattr(record, 'hostname', None)
        if not hostname:
            hostname = self.hostname
        if hostname is None or hostname == '':
            hostname = NILVALUE
        return self.filter_printusascii(str(hostname))

    def get_appname(self, record):
        appname = getattr(record, 'appname', self.appname)
        if appname is None or appname == '':
            appname = getattr(record, 'name', NILVALUE)
        return self.filter_printusascii(str(appname))

    def get_procid(self, record):
        procid = getattr(record, 'procid', self.procid)
        if procid is None or procid == '':
            procid = getattr(record, 'process', NILVALUE)
        return self.filter_printusascii(str(procid))

    def get_msgid(self, record):
        msgid = getattr(record, 'msgid', NILVALUE)
        if msgid is None or msgid == '':
            msgid = NILVALUE
        return self.filter_printusascii(str(msgid))

    def get_enterprise_id(self, record):
        # We allow None to be returned here.
        # We'll handle it when cleaning the structured data
        enterprise_id = getattr(record, 'enterprise_id', self.enterprise_id)
        if enterprise_id is None:
            return None
        else:
            return self.filter_printusascii(str(enterprise_id))

    def get_structured_data(self, record):
        structured_data = OrderedDict()
        if isinstance(self.structured_data, dict):
            structured_data.update(self.structured_data)
        record_sd = getattr(record, 'structured_data', {})
        if isinstance(record_sd, dict):
            structured_data.update(record_sd)
        return structured_data

    def build_msg(self, record):
        # The syslog message has the following ABNF [RFC5234] definition:
        #
        #     SYSLOG-MSG      = HEADER SP STRUCTURED-DATA [SP MSG]
        #
        #     HEADER          = PRI VERSION SP TIMESTAMP SP HOSTNAME
        #                     SP APP-NAME SP PROCID SP MSGID
        #     PRI             = "<" PRIVAL ">"
        #     PRIVAL          = 1*3DIGIT ; range 0 .. 191
        #     VERSION         = NONZERO-DIGIT 0*2DIGIT
        #     HOSTNAME        = NILVALUE / 1*255PRINTUSASCII
        #
        #     APP-NAME        = NILVALUE / 1*48PRINTUSASCII
        #     PROCID          = NILVALUE / 1*128PRINTUSASCII
        #     MSGID           = NILVALUE / 1*32PRINTUSASCII
        #
        #     TIMESTAMP       = NILVALUE / FULL-DATE "T" FULL-TIME
        #     FULL-DATE       = DATE-FULLYEAR "-" DATE-MONTH "-" DATE-MDAY
        #     DATE-FULLYEAR   = 4DIGIT
        #     DATE-MONTH      = 2DIGIT  ; 01-12
        #     DATE-MDAY       = 2DIGIT  ; 01-28, 01-29, 01-30, 01-31 based on
        #                             ; month/year
        #     FULL-TIME       = PARTIAL-TIME TIME-OFFSET
        #     PARTIAL-TIME    = TIME-HOUR ":" TIME-MINUTE ":" TIME-SECOND
        #                     [TIME-SECFRAC]
        #     TIME-HOUR       = 2DIGIT  ; 00-23
        #     TIME-MINUTE     = 2DIGIT  ; 00-59
        #     TIME-SECOND     = 2DIGIT  ; 00-59
        #     TIME-SECFRAC    = "." 1*6DIGIT
        #     TIME-OFFSET     = "Z" / TIME-NUMOFFSET
        #     TIME-NUMOFFSET  = ("+" / "-") TIME-HOUR ":" TIME-MINUTE
        #
        #
        #     STRUCTURED-DATA = NILVALUE / 1*SD-ELEMENT
        #     SD-ELEMENT      = "[" SD-ID *(SP SD-PARAM) "]"
        #     SD-PARAM        = PARAM-NAME "=" %d34 PARAM-VALUE %d34
        #     SD-ID           = SD-NAME
        #     PARAM-NAME      = SD-NAME
        #     PARAM-VALUE     = UTF-8-STRING ; characters '"', '\' and
        #                                  ; ']' MUST be escaped.
        #     SD-NAME         = 1*32PRINTUSASCII
        #                     ; except '=', SP, ']', %d34 (")
        #
        #     MSG             = MSG-ANY / MSG-UTF8
        #     MSG-ANY         = *OCTET ; not starting with BOM
        #     MSG-UTF8        = BOM UTF-8-STRING
        #     BOM             = %xEF.BB.BF
        #
        #     UTF - 8 - STRING = *OCTET ; UTF - 8 string as specified
        #                               ; in RFC 3629
        #
        #     OCTET = % d00 - 255
        #     SP = % d32
        #     PRINTUSASCII = % d33 - 126
        #     NONZERO - DIGIT = % d49 - 57
        #     DIGIT = % d48 / NONZERO - DIGIT
        #     NILVALUE = "-"

        # HEADER
        pri = '<%d>' % self.encode_priority(self.facility, record.levelname)
        version = SYSLOG_VERSION
        timestamp = datetime.fromtimestamp(record.created, self._local_zone)
        if self.utc_timestamp:
            timestamp = timestamp.astimezone(utc)
        timestamp = timestamp.isoformat()
        hostname = self.get_hostname(record)
        appname = self.get_appname(record)
        procid = self.get_procid(record)
        msgid = self.get_msgid(record)

        pri = pri.encode('ascii')
        version = version.encode('ascii')
        timestamp = timestamp.encode('ascii')
        hostname = hostname.encode('ascii', 'replace')[:255]
        appname = appname.encode('ascii', 'replace')[:48]
        procid = procid.encode('ascii', 'replace')[:128]
        msgid = msgid.encode('ascii', 'replace')[:32]

        header = b''.join((pri, version, SP, timestamp, SP, hostname, SP, appname, SP, procid, SP, msgid))

        # STRUCTURED-DATA
        enterprise_id = self.get_enterprise_id(record)
        structured_data = self.get_structured_data(record)
        cleaned_structured_data = []
        for sd_id, sd_params in list(structured_data.items()):
            # Clean structured data ID
            sd_id = self.filter_printusascii(sd_id)
            sd_id = sd_id.replace('=', '').replace(' ', '').replace(']', '').replace('"', '')
            if '@' not in sd_id and sd_id not in REGISTERED_SD_IDs and enterprise_id is None:
                raise ValueError("Enterprise ID has not been set. Cannot build structured data ID. "
                                 "Please set a enterprise ID when initializing the logging handler "
                                 "or include one in the structured data ID.")
            elif '@' in sd_id:
                sd_id, enterprise_id = sd_id.rsplit('@', 1)

            if len(enterprise_id) > 30:
                raise ValueError("Enterprise ID is too long. Impossible to build structured data ID.")

            sd_id = sd_id.replace('@', '')
            if len(sd_id) + len(enterprise_id) > 32:
                sd_id = sd_id[:31 - len(enterprise_id)]
            if sd_id not in REGISTERED_SD_IDs:
                sd_id = '@'.join((sd_id, enterprise_id))
            sd_id = sd_id.encode('ascii', 'replace')

            cleaned_sd_params = []
            # ignore sd params not int key-value format
            if isinstance(sd_params, dict):
                sd_params = sd_params.items()
            else:
                sd_params = []

            # Clean key-value pairs
            for (param_name, param_value) in sd_params:
                param_name = self.filter_printusascii(str(param_name))
                param_name = param_name.replace('=', '').replace(' ', '').replace(']', '').replace('"', '')
                if param_value is None:
                    param_value = ''

                param_value = str(param_value)

                if PY2:
                    param_value = unicode(param_value, 'utf-8')  # noqa

                param_value = param_value.replace('\\', '\\\\').replace('"', '\\"').replace(']', '\\]')

                param_name = param_name.encode('ascii', 'replace')[:32]
                param_value = param_value.encode('utf-8', 'replace')

                sd_param = b''.join((param_name, b'="', param_value, b'"'))
                cleaned_sd_params.append(sd_param)

            cleaned_sd_params = SP.join(cleaned_sd_params)

            # build structured data element
            spacer = SP if cleaned_sd_params else b''
            sd_element = b''.join((b'[', sd_id, spacer, cleaned_sd_params, b']'))
            cleaned_structured_data.append(sd_element)

        if cleaned_structured_data:
            structured_data = b''.join(cleaned_structured_data)
        else:
            structured_data = NILVALUE.encode('ascii')

        # MSG
        if record.msg is None or record.msg == "":
            pieces = (header, structured_data)
        else:
            msg = self.format(record)
            if self.msg_as_utf8:
                msg = b''.join((BOM_UTF8, msg.encode('utf-8')))
            else:
                msg = msg.encode('utf-8')
            pieces = (header, structured_data, msg)
        syslog_msg = SP.join(pieces)

        return syslog_msg

    def emit(self, record):
        """
        Emit a record.

        The record is formatted, and then sent to the syslog server. If
        exception information is present, it is NOT sent to the server.
        """
        try:
            syslog_msg = self.build_msg(record)
            self.transport.transmit(syslog_msg)
        except Exception:
            self.handleError(record)

    def close(self):
        """
        Closes the socket.
        """
        self.acquire()
        try:
            if self.transport is not None:
                self.transport.close()
            super(Rfc5424SysLogHandler, self).close()
        finally:
            self.release()
