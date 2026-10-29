"""Outage alerts with a per-kind throttle and a single recovery notice.

When every key fails, the customer only sees "please try again" - the operator never hears about it.
So failures alert the operator, but at most once per kind per `window_sec`, and the first success after
an alerted outage sends one recovery notice. A notice that is not an outage (one of several keys was
refused, the others still answer) is sent with `outage=False`: it is throttled the same way but does not
trigger a "recovered" message on the next success.

Alert text is built from structured fields only (kind, HTTP status, model name). It never includes an
exception message, a key or the prompt: those can carry secrets or customer text.
"""
import threading
import time

TITLES = {
    "no_key": "AI support: no API key configured",
    "key_removed": "AI support: one API key rejected",    # body names the key by label ("key 2"), never its value
    "key_rejected": "AI support: every API key rejected",
    "all_failed": "AI support: every attempt failed",
    "busy": "AI support: queue wait exceeded",
}


class AlertGate:
    def __init__(self, send, window_sec=1800.0, clock=time.monotonic):
        """send(title: str, body: str) - e.g. post to a chat webhook. Exceptions from send are swallowed."""
        self._send = send
        self.window_sec = window_sec
        self._clock = clock
        self._lock = threading.Lock()
        self._last = {}          # kind -> time of the last alert
        self._down = False       # an alert went out since the last success

    def failure(self, kind, reason, outage=True):
        now = self._clock()
        with self._lock:
            last = self._last.get(kind)
            if last is not None and now - last < self.window_sec:
                return False
            self._last[kind] = now
            if outage:
                self._down = True
        self._safe_send(TITLES.get(kind, "AI support: " + kind), reason)
        return True

    def success(self):
        with self._lock:
            if not self._down:
                return False
            self._down = False
            for kind in ("no_key", "all_failed", "busy", "bad_request"):
                self._last.pop(kind, None)    # outages may alert again at once; a removed key stays throttled
        self._safe_send("AI support: recovered", "answers are going through again")
        return True

    def _safe_send(self, title, body):
        try:
            self._send(title, body)
        except Exception:  # noqa: BLE001 - an alert channel outage must not break answering
            pass
