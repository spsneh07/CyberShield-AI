"""Logging with redaction of secrets and personal identifiers."""
import logging
import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(api[_-]?key|authorization|password|token|secret)(\"?\s*[:=]\s*\"?)[^\s\",]+"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"), "Bearer [REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{10,}"), "[REDACTED_KEY]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b[\w.\-]+@[a-zA-Z]{2,}\b"), "[UPI]"),
    (re.compile(r"(?<!\d)(?:\+?91[\-\s]?)?[6-9]\d{9}(?!\d)"), "[PHONE]"),
    (re.compile(r"\b\d{11,18}\b"), "[NUMBER]"),
    # Pydantic validation errors echo the offending input, which can be the user's incident text.
    (re.compile(r"input_value=.*?(?=, input_type=|$)", re.M), "input_value=[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if record.exc_info:  # the formatter would append the traceback after this filter, unredacted
            msg += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info = record.exc_text = None
        record.msg = redact(msg)
        record.args = ()
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
