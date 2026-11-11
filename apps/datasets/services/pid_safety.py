import re


_SENSITIVE_KEY = re.compile(
    r"password|passwd|secret|token|authorization|credential|"
    r"api[_-]?key|access[_-]?key|private[_-]?key",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|client[_-]?secret|secret|"
    r"(?:access[_-]?)?token|authorization|(?:access[_-]?)?key|"
    r"api[_-]?key|credential)\b\s*[:=]\s*)[^&\s,;]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+\S+")


def sanitize_pid_text(value):
    text = str(value or "")
    text = _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    return text[:2000]


def sanitize_pid_json(value):
    if isinstance(value, dict):
        return {
            key: sanitize_pid_json(item)
            for key, item in value.items()
            if not _SENSITIVE_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_pid_json(item) for item in value]
    if isinstance(value, str):
        return sanitize_pid_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_pid_text(value)
