"""The harness's one redaction boundary. Every text that leaves the harness for an output sink, whether a child's
stdout or stderr, a result, an exception, a STOP reason, a refusal or harness.log, passes through a Redactor first.

A Redactor knows the exact secret values it must remove: the values of every secret-named variable in the environment
it was built from (the subscription token above all, since the harness holds it in memory), plus any values given to
it, plus anything shaped like an Anthropic key. Each is replaced by <redacted>. Short values (under MIN_LEN characters)
are not treated as secrets, so an empty or placeholder variable cannot blank ordinary text. A text that must be
shortened (a version line, a stderr tail) is redacted whole before it is cut (Redactor.clip).
"""
import os
import re

SECRET_NAME = re.compile(r"TOKEN|API_KEY|SECRET|PASSWORD|AUTH", re.I)
SHAPES = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")
MIN_LEN = 8
MARK = "<redacted>"


class Redactor:
    """scrub(text) removes every known secret value from text. Build one from the environment a child receives."""

    def __init__(self, environ=None, extra=()):
        environ = os.environ if environ is None else environ
        values = {v for k, v in environ.items() if SECRET_NAME.search(k) and isinstance(v, str)}
        values |= {v for v in extra if isinstance(v, str)}
        self.values = sorted((v for v in values if len(v) >= MIN_LEN), key=len, reverse=True)

    def __call__(self, text):
        if text is None:
            return text
        text = str(text)
        for v in self.values:
            text = text.replace(v, MARK)
        return SHAPES.sub(MARK, text)

    def clip(self, text, limit, tail=False):
        """text redacted whole, then cut to its first (or, with tail, its last) limit characters. Never cut first:
        the values are matched exactly, so a secret the cut has shortened would no longer be recognised."""
        text = self(text) or ""
        return text[-limit:] if tail else text[:limit]
