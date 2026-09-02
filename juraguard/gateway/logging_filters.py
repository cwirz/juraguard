import copy
import logging
import re


SETUP_TOKEN = re.compile(r"(/connect/)[^/?\s]+")
QUERY_VALUE = re.compile(r"([?&][^=\s&]+)=([^&#\s]*)")
HEADER_VALUE = re.compile(r"(?im)\b((?:http_)?(?:authorization|cookie))(\s*[:=]\s*)[^\r\n]+")


def _redact(value):
    text = SETUP_TOKEN.sub(r"\1<redacted>", str(value))
    text = QUERY_VALUE.sub(r"\1=<redacted>", text)
    return HEADER_VALUE.sub(r"\1\2<redacted>", text)


class RedactRequestSecrets(logging.Filter):
    def filter(self, record):
        redacted = copy.copy(record)
        redacted.msg = _redact(record.getMessage())
        redacted.args = ()
        for name in ("pathname", "request_path", "path", "path_info", "uri", "url"):
            value = getattr(redacted, name, None)
            if isinstance(value, str):
                setattr(redacted, name, _redact(value))
        request = getattr(record, "request", None)
        if request is not None:
            redacted.request = copy.copy(request)
            redacted.request.path = _redact(request.path)
            redacted.request.path_info = _redact(request.path_info)
            redacted.request.META = copy.copy(request.META)
            redacted.request.META["QUERY_STRING"] = "<redacted>"
            for key in ("HTTP_AUTHORIZATION", "HTTP_COOKIE"):
                if key in redacted.request.META:
                    redacted.request.META[key] = "<redacted>"
        if record.exc_info:
            redacted.exc_text = _redact(logging.Formatter().formatException(record.exc_info))
            redacted.exc_info = None
        if record.stack_info:
            redacted.stack_info = _redact(record.stack_info)
        return redacted
