import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from conftest import FakeClient, FakeClock  # noqa: E402
from server.app import RateLimiter, build_bot_from_env, client_key, create_app  # noqa: E402
from support_bot import KeywordGuard, KnowledgeBase, LLMCaller, SupportBot  # noqa: E402
from support_bot import errors as E  # noqa: E402


@pytest.fixture
def demo_bot(tmp_path):
    return build_bot_from_env({"SUPPORT_BOT_LOG_DIR": str(tmp_path)})


@pytest.fixture
def demo_client(demo_bot):
    return TestClient(create_app(bot=demo_bot))


def test_health(demo_client):
    assert demo_client.get("/api/health").json() == {"ok": True}


def test_demo_mode_answers_from_knowledge_without_a_key(demo_client, tmp_path):
    r = demo_client.post("/api/chat", json={"message": "알림 문구는 어디서 바꿔요?"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "ai" and body["model"] == "demo"
    assert "[설정] → [알림] → [문구 편집]" in body["text"]
    assert (tmp_path / "chat.jsonl").is_file()                 # the turn went through the masked log


def test_guarded_question_skips_the_model(demo_client):
    r = demo_client.post("/api/chat", json={"message": "환불 받고 싶어요"})
    assert r.json()["route"] == "guard"


def test_empty_and_oversized_messages_are_rejected(demo_client):
    assert demo_client.post("/api/chat", json={"message": ""}).status_code == 422
    assert demo_client.post("/api/chat", json={"message": "x" * 2001}).status_code == 422


def test_rate_limit_per_client(demo_bot):
    clock = FakeClock()
    app = create_app(bot=demo_bot, limiter=RateLimiter(limit=2, window_sec=60, clock=clock))
    c = TestClient(app)
    codes = [c.post("/api/chat", json={"message": "엑셀"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    clock.t += 60
    assert c.post("/api/chat", json={"message": "엑셀"}).status_code == 200


def test_rate_limiter_memory_stays_bounded_under_rotating_clients():
    clock = FakeClock()
    rl = RateLimiter(limit=2, window_sec=60, max_clients=100, global_limit=10**9, clock=clock)
    for i in range(10_000):                       # attacker rotating addresses, never returning
        rl.allowed("10.0.%d.%d" % (i // 256, i % 256))
        clock.t += 0.01
    assert len(rl) <= 100


def test_flooding_new_addresses_cannot_reset_an_active_client():
    clock = FakeClock()
    rl = RateLimiter(limit=2, window_sec=60, max_clients=50, global_limit=10**9, clock=clock)
    assert rl.allowed("victim") and rl.allowed("victim")
    assert rl.allowed("victim") is False
    refused = sum(not rl.allowed("x%d" % i) for i in range(1000))   # all inside the same window
    assert refused == 1000 - 49                   # table full of active clients: newcomers refused
    assert rl.allowed("victim") is False          # victim's window was not evicted
    clock.t += 60
    assert rl.allowed("newcomer")                 # after the window, stale entries make room again


def test_global_cap_limits_rotating_addresses():
    clock = FakeClock()
    rl = RateLimiter(limit=20, window_sec=60, global_limit=30, clock=clock)
    allowed = sum(rl.allowed("c%d" % i) for i in range(100))
    assert allowed == 30
    clock.t += 60
    assert rl.allowed("c999")


def test_ipv6_clients_are_grouped_by_64():
    assert client_key("2001:db8:1:2:aaaa::1") == client_key("2001:db8:1:2:bbbb::9")
    assert client_key("2001:db8:1:2::1") != client_key("2001:db8:1:3::1")
    assert client_key("203.0.113.7") == "203.0.113.7"


def test_ipv6_short_forms_are_normalized():
    # same /64, different text - string splitting would give two keys and let a client dodge the cap
    assert client_key("2001:db8::1") == client_key("2001:db8::2") == "2001:db8::/64"
    assert client_key("2001:db8:0:0::ffff") == client_key("2001:0db8::1")
    assert client_key("::1") == "::/64"


def test_ipv4_mapped_ipv6_counts_as_ipv4_and_garbage_does_not_raise():
    assert client_key("::ffff:1.2.3.4") == "1.2.3.4" == client_key("1.2.3.4")
    assert client_key("testclient") == "testclient"
    assert client_key("") == "unknown"


def test_provider_outage_returns_a_cause_specific_message(tmp_path):
    clock = FakeClock()
    caller = LLMCaller([FakeClient([E.Unavailable(503, "m")] * 10, clock=clock)], ["m"],
                       clock=clock, sleep=clock.sleep)
    bot = SupportBot(caller, KnowledgeBase(tmp_path / "none.md"), KeywordGuard())
    body = TestClient(create_app(bot=bot)).post("/api/chat", json={"message": "q"}).json()
    assert body["route"] == "error:all_failed"
    assert body["text"] == E.AllAttemptsFailed.user_message


def test_no_key_for_a_real_provider_says_so(tmp_path):
    bot = SupportBot(LLMCaller([], ["m"]), KnowledgeBase(tmp_path / "none.md"))
    body = TestClient(create_app(bot=bot)).post("/api/chat", json={"message": "q"}).json()
    assert body["route"] == "error:no_key"
