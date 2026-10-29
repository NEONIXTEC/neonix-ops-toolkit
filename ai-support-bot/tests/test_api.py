import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from conftest import FakeClient, FakeClock  # noqa: E402
from server.app import RateLimiter, build_bot_from_env, create_app  # noqa: E402
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
