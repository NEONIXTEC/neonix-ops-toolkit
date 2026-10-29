"""Conversation log with personal data masked before it touches disk.

One JSON line per turn. The user id is an HMAC (a plain sha256 of a phone-number-shaped id is reversible -
the id space is small), questions and answers pass through `mask()`. Nothing unmasked is written, so the
log can be read, shared for debugging or kept for KB tuning without a privacy review each time.
"""
import hashlib
import hmac
import json
import os
import re
import threading
import time

_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"(?<!\d)\d{6}-?[1-8]\d{6}(?!\d)"), "[RRN]"),                        # resident registration no.
    (re.compile(r"(?<![\d-])01[016789][-. ]?\d{3,4}[-. ]?(\d{4})(?![\d-])"), r"[PHONE-\1]"),  # keep last 4 only
    (re.compile(r"(?<!\d)(?:\d[ -]?){10,19}(?!\d)"), "[NUMBER]"),                    # card / account numbers
]


def mask(text):
    """# I: free text -> O: the same text with e-mails, resident numbers, phones (last 4 kept) and long numbers masked"""
    out = text or ""
    for rx, repl in _PATTERNS:
        out = rx.sub(repl, out)
    return out


class ChatLog:
    def __init__(self, path, salt, clock=time.time):
        if not salt:
            raise ValueError("salt is required (a server-side secret, never committed)")
        self.path = os.fspath(path)
        self._salt = salt.encode("utf-8") if isinstance(salt, str) else salt
        self._clock = clock
        self._lock = threading.Lock()

    def user_key(self, user_id):
        return hmac.new(self._salt, str(user_id).encode("utf-8"), hashlib.sha256).hexdigest()[:16]

    def write(self, user_id, question, answer, route, model="", elapsed=0.0):
        rec = {
            "ts": round(self._clock(), 3),
            "user": self.user_key(user_id),
            "route": route,                # guard | ai | error:<kind>
            "model": model,
            "ms": int(elapsed * 1000),
            "q": mask(question),
            "a": mask(answer),
        }
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return rec
