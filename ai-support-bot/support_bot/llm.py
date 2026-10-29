"""LLM call wrapper: API keys with failover, retries, a deadline and serialization.

    caller = LLMCaller([client], models=["model-v2", "model-v1", "model-latest"], alerts=gate)
    caller = LLMCaller([client_a, client_b, client_c], models=[...])     # 2-3 API keys: failover
    reply = caller.generate(system_text, question)

One provider client = one API key. One key is the default; register two or three and an outage on one
(429, 5xx, timeout, dropped connection) moves the call to the next key.

Rules (each one from production, see ../README.md):
  - The caller stays inside the keys and models it was given. When every attempt fails it raises and the
    customer sees "please try again in a moment" - it never quietly swaps in some other model to fill the
    gap. A wrong answer is worse than a short wait.
  - `models`: pinned versions first, an alias last. An alias can be repointed by the provider overnight.
  - Attempt order inside a round: for each model, each live key. Retryable failures move on; after a full
    round the caller pauses `round_pause` and tries one more round, all inside `deadline` seconds.
  - 401/403 on a key: that key is taken out until restart and an alert names it ("key 2"), never its
    value. The call goes on with the remaining keys; only when no key is left does it fail.
  - Calls are serialized: one request in flight, at least `min_gap` seconds apart. Failover decides
    *which* key, not *how many at once*; parallel bursts hit the per-minute quota together.
    Callers queue up to `lock_wait` seconds (the chat UI shows a typing indicator meanwhile).
"""
import logging
import threading
import time
from dataclasses import dataclass

from . import errors as E

log = logging.getLogger(__name__)


@dataclass
class Reply:
    text: str
    model: str
    key: str          # "key 1", "key 2", ... - a label, never the key itself
    attempts: int
    elapsed: float


class LLMCaller:
    def __init__(self, clients, models, *, rounds=2, round_pause=2.0, deadline=45.0, per_call_timeout=20.0,
                 min_gap=0.8, lock_wait=45.0, max_tokens=900, alerts=None, clock=time.monotonic,
                 sleep=time.sleep):
        if not models:
            raise ValueError("models must not be empty")
        if clients is None:
            clients = []
        elif not isinstance(clients, (list, tuple)):
            clients = [clients]
        self.clients = list(clients)    # provider adapters, one per API key, in failover order
        self.models = list(models)
        self.rounds = rounds
        self.round_pause = round_pause
        self.deadline = deadline
        self.per_call_timeout = per_call_timeout
        self.min_gap = min_gap
        self.lock_wait = lock_wait
        self.max_tokens = max_tokens    # several questions merged into one turn need room (600 truncated them)
        self.alerts = alerts
        self._clock = clock
        self._sleep = sleep
        self._call_lock = threading.Lock()    # held for the network call - long. Never merge with a short lock.
        self._last_call_end = None
        self._rejected = set()                # indexes of keys refused with 401/403, until restart

    # ------------------------------------------------------------------ public
    def live_keys(self):
        return [i for i in range(len(self.clients)) if i not in self._rejected]

    def generate(self, system, user):
        """# I: system text (behavior + knowledge, built by the caller at call time), user text
           # O: Reply  | raises BotError (NoKeyConfigured / KeyRejected / AllAttemptsFailed / Busy / BadRequestError)"""
        try:
            reply = self._generate(system, user)
        except E.BotError as e:
            if self.alerts is not None:
                self.alerts.failure(e.kind, e.reason)
            raise
        if self.alerts is not None:
            self.alerts.success()
        return reply

    # ------------------------------------------------------------------ internals
    def _generate(self, system, user):
        if not self.clients:
            raise E.NoKeyConfigured("no API key configured")
        if not self.live_keys():
            raise E.KeyRejected("every key was rejected earlier - restart after fixing them")
        start = self._clock()
        if not self._call_lock.acquire(timeout=self.lock_wait):
            raise E.Busy("waited %.0fs for the call lock" % self.lock_wait)
        try:
            return self._rounds(system, user, start)
        finally:
            self._last_call_end = self._clock()
            self._call_lock.release()

    def _rounds(self, system, user, start):
        attempts = 0
        last = None
        for rnd in range(self.rounds):
            if rnd:
                if self._clock() - start + self.round_pause >= self.deadline:
                    break
                self._sleep(self.round_pause)
            for model in self.models:
                for k in self.live_keys():
                    left = self.deadline - (self._clock() - start)
                    if left <= 0:
                        raise E.AllAttemptsFailed(self._reason(last, attempts, "deadline"))
                    self._pace()
                    attempts += 1
                    label = "key %d" % (k + 1)
                    try:
                        text = self.clients[k].generate(model=model, system=system, user=user,
                                                        max_tokens=self.max_tokens,
                                                        timeout=min(self.per_call_timeout, left))
                        return Reply(text=text, model=model, key=label, attempts=attempts,
                                     elapsed=self._clock() - start)
                    except E.AuthRejected as e:
                        self._drop_key(k, label, e, model)
                        if not self.live_keys():
                            raise E.KeyRejected("every key rejected (last: %s HTTP %s)" % (label, e.status)) from None
                    except E.BadRequest as e:
                        raise E.BadRequestError("HTTP %s (%s)" % (e.status, model)) from None
                    except E.ProviderError as e:
                        last = e
                        log.warning("[llm] attempt %d %s %s failed: %s status=%s",
                                    attempts, label, model, type(e).__name__, e.status)
                        if e.next_model:
                            break             # this model is gone for every key - next model
                    finally:
                        self._last_call_end = self._clock()
        raise E.AllAttemptsFailed(self._reason(last, attempts, "rounds"))

    def _drop_key(self, k, label, err, model):
        self._rejected.add(k)
        log.warning("[llm] %s rejected (HTTP %s) - out until restart", label, err.status)
        if self.alerts is not None:
            # a notice, not an outage: the remaining keys may still answer
            self.alerts.failure("key_removed", "%s HTTP %s (%s) - removed until restart" % (label, err.status, model),
                                outage=False)

    def _pace(self):
        if self._last_call_end is None:
            return
        wait = self.min_gap - (self._clock() - self._last_call_end)
        if wait > 0:
            self._sleep(wait)

    @staticmethod
    def _reason(last, attempts, why):
        if last is None:
            return "%s after %d attempt(s)" % (why, attempts)
        status = "HTTP %s" % last.status if last.status else type(last).__name__
        return "%s (%s) after %d attempt(s), %s" % (status, last.model, attempts, why)
