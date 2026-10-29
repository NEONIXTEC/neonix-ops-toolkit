import threading

import pytest

from conftest import FakeClient, FakeClock
from support_bot import AlertGate, LLMCaller
from support_bot import errors as E

MODELS = ["model-v2", "model-v1", "model-latest"]


def make(script, *, alerts=None, **kw):
    clock = FakeClock()
    client = FakeClient(script, clock=clock)
    caller = LLMCaller([client], MODELS, alerts=alerts, clock=clock, sleep=clock.sleep, **kw)
    return caller, client, clock


def make_keys(scripts, *, alerts=None, **kw):
    clock = FakeClock()
    clients = [FakeClient(s, clock=clock) for s in scripts]
    caller = LLMCaller(clients, MODELS, alerts=alerts, clock=clock, sleep=clock.sleep, **kw)
    return caller, clients, clock


def test_first_model_answers():
    caller, client, _ = make(["hello"])
    r = caller.generate("sys", "q")
    assert (r.text, r.model, r.key, r.attempts) == ("hello", "model-v2", "key 1", 1)
    assert client.calls[0]["max_tokens"] == 900


def test_single_client_is_accepted_without_a_list():
    clock = FakeClock()
    caller = LLMCaller(FakeClient(["hi"], clock=clock), MODELS, clock=clock, sleep=clock.sleep)
    assert caller.generate("s", "q").text == "hi"


def test_outage_on_key_1_fails_over_to_key_2_same_model():
    caller, (k1, k2), _ = make_keys([[E.RateLimited(429, "m")], ["from key 2"]])
    r = caller.generate("sys", "q")
    assert (r.text, r.key, r.model) == ("from key 2", "key 2", "model-v2")   # same model, next key
    assert len(k1.calls) == 1 and len(k2.calls) == 1


def test_rejected_key_is_removed_alerted_by_label_and_others_keep_answering():
    sent = []
    clock = FakeClock()
    gate = AlertGate(lambda t, b: sent.append((t, b)), clock=clock)
    secret = "FAKE-KEY-VALUE-2"
    k1 = FakeClient(["a1", "a2"], clock=clock)
    k2 = FakeClient([E.AuthRejected(401, "m")], clock=clock)
    caller = LLMCaller([k2, k1], MODELS, alerts=gate, clock=clock, sleep=clock.sleep)   # bad key first
    assert caller.generate("s", "q").key == "key 2"
    assert caller.live_keys() == [1]
    caller.generate("s", "q")
    assert len(k2.calls) == 1                                   # removed key is not used again
    assert len(sent) == 1 and "key 1" in sent[0][1]            # one notice, names the key by label
    assert secret not in sent[0][0] + sent[0][1]
    assert not any("recovered" in t for t, _ in sent)          # a removed key is a notice, not an outage


def test_every_key_rejected_fails_with_key_rejected():
    caller, (k1, k2), _ = make_keys([[E.AuthRejected(403, "m")], [E.AuthRejected(401, "m")]])
    with pytest.raises(E.KeyRejected):
        caller.generate("s", "q")
    with pytest.raises(E.KeyRejected):
        caller.generate("s", "q")
    assert len(k1.calls) == 1 and len(k2.calls) == 1           # nothing called once every key is out


def test_three_keys_all_down_gives_up_after_two_rounds():
    caller, clients, _ = make_keys([[E.Unavailable(503, "m")] * 10] * 3, deadline=1000)
    with pytest.raises(E.AllAttemptsFailed):
        caller.generate("s", "q")
    assert sum(len(c.calls) for c in clients) == 2 * len(MODELS) * 3


def test_retryable_errors_walk_the_models_then_a_second_round():
    script = [E.RateLimited(429, "m"), E.Unavailable(503, "m"), E.CallTimeout(None, "m"), "late answer"]
    caller, client, clock = make(script)
    r = caller.generate("sys", "q")
    assert r.text == "late answer"
    assert [c["model"] for c in client.calls] == MODELS + ["model-v2"]   # round 2 starts at the top again
    assert 2.0 in clock.slept                                             # pause between rounds


def test_gives_up_after_two_rounds_inside_the_given_models():
    script = [E.RateLimited(429, "m")] * 10
    caller, client, _ = make(script)
    with pytest.raises(E.AllAttemptsFailed) as ei:
        caller.generate("sys", "q")
    assert len(client.calls) == 2 * len(MODELS)      # exactly two rounds, nothing else tried
    assert {c["model"] for c in client.calls} == set(MODELS)
    assert "HTTP 429" in ei.value.reason


def test_deadline_stops_retries():
    script = [E.CallTimeout(None, "m")] * 10
    clock = FakeClock()
    client = FakeClient(script, clock=clock, call_cost=20.0)   # every call burns 20s
    caller = LLMCaller([client], MODELS, deadline=45.0, clock=clock, sleep=clock.sleep)
    with pytest.raises(E.AllAttemptsFailed):
        caller.generate("sys", "q")
    assert len(client.calls) == 3                              # 20 + 20 + (5s left) then out of time
    assert client.calls[-1]["timeout"] <= 5.0 + 1e-6           # last call only gets what is left


def test_auth_rejected_fails_at_once_and_stays_rejected():
    caller, client, _ = make([E.AuthRejected(401, "m"), "never used"])
    with pytest.raises(E.KeyRejected):
        caller.generate("sys", "q")
    assert len(client.calls) == 1                              # no retry
    with pytest.raises(E.KeyRejected):
        caller.generate("sys", "q")
    assert len(client.calls) == 1                              # not called again until restart


def test_retired_model_is_skipped_without_pause():
    caller, client, clock = make([E.ModelUnavailable(404, "m"), "from v1"])
    r = caller.generate("sys", "q")
    assert r.model == "model-v1"
    assert 2.0 not in clock.slept


def test_bad_request_is_not_retried():
    caller, client, _ = make([E.BadRequest(400, "m")])
    with pytest.raises(E.BadRequestError):
        caller.generate("sys", "q")
    assert len(client.calls) == 1


def test_no_client_means_no_key():
    for empty in (None, []):
        with pytest.raises(E.NoKeyConfigured):
            LLMCaller(empty, MODELS).generate("sys", "q")


def test_calls_are_paced_by_min_gap():
    caller, client, clock = make(["a", "b"], min_gap=0.8)
    caller.generate("sys", "q1")
    caller.generate("sys", "q2")                               # right after the first one ended
    assert 0.8 in clock.slept


def test_calls_are_serialized():
    """Real threads, real time: two callers never have a request in flight at the same time."""
    inflight, peak = [0], [0]
    lock = threading.Lock()

    class Slow:
        def generate(self, **kw):
            with lock:
                inflight[0] += 1
                peak[0] = max(peak[0], inflight[0])
            threading.Event().wait(0.05)
            with lock:
                inflight[0] -= 1
            return "ok"

    caller = LLMCaller([Slow(), Slow()], MODELS, min_gap=0.0)
    ts = [threading.Thread(target=caller.generate, args=("s", "q")) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 1


def test_alerts_once_per_window_then_one_recovery():
    sent = []
    clock = FakeClock()
    gate = AlertGate(lambda title, body: sent.append((title, body)), window_sec=1800, clock=clock)
    key, prompt = "FAKE-KEY-123", "SECRET-PROMPT-TEXT"
    script = [E.RateLimited(429, "m")] * 12 + ["fine"]
    client = FakeClient(script, clock=clock)
    caller = LLMCaller([client], MODELS, alerts=gate, clock=clock, sleep=clock.sleep)

    for _ in range(2):
        with pytest.raises(E.AllAttemptsFailed):
            caller.generate(prompt + " " + key, "question")
    assert len(sent) == 1                                      # second failure inside 30 min: silent
    caller.generate(prompt, "question")
    assert len(sent) == 2 and "recovered" in sent[-1][0]       # one recovery notice
    for title, body in sent:                                   # alerts never carry the key or the prompt
        assert key not in title + body and prompt not in title + body


def test_alert_again_after_window():
    sent = []
    clock = FakeClock()
    gate = AlertGate(lambda t, b: sent.append(t), window_sec=1800, clock=clock)
    gate.failure("all_failed", "HTTP 503 (m)")
    clock.t += 1799
    gate.failure("all_failed", "HTTP 503 (m)")
    clock.t += 2
    gate.failure("all_failed", "HTTP 503 (m)")
    assert len(sent) == 2


def test_broken_alert_channel_does_not_break_answering():
    def boom(t, b):
        raise RuntimeError("webhook down")
    gate = AlertGate(boom)
    caller, _, _ = make([E.AuthRejected(403, "m")], alerts=gate)
    with pytest.raises(E.KeyRejected):
        caller.generate("s", "q")


def test_user_messages_never_say_or():
    for cls in (E.NoKeyConfigured, E.KeyRejected, E.AllAttemptsFailed, E.Busy, E.BadRequestError):
        assert "또는" not in cls.user_message
