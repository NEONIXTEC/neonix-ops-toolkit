import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeClock:
    """Manual clock + sleep: sleeping advances the clock, so retry/deadline logic runs instantly."""

    def __init__(self, t=1000.0):
        self.t = t
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


class FakeClient:
    """Provider stand-in. `script` is a list of outcomes, one per call: a string (answer) or an exception."""

    def __init__(self, script, clock=None, call_cost=1.0):
        self.script = list(script)
        self.calls = []
        self.clock = clock
        self.call_cost = call_cost

    def generate(self, *, model, system, user, max_tokens, timeout):
        self.calls.append({"model": model, "system": system, "user": user, "max_tokens": max_tokens,
                           "timeout": timeout})
        if self.clock is not None:
            self.clock.t += self.call_cost
        out = self.script.pop(0) if self.script else "ok"
        if isinstance(out, BaseException):
            raise out
        return out
