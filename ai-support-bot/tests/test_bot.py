import json
import os

from conftest import FakeClient, FakeClock
from support_bot import ChatLog, KeywordGuard, KnowledgeBase, LLMCaller, SupportBot, mask
from support_bot import errors as E
from support_bot.bot import BEHAVIOR

KB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge", "faq.md")


def build(tmp_path, script):
    clock = FakeClock()
    client = FakeClient(script, clock=clock)
    caller = LLMCaller(client, ["model-v2", "model-latest"], clock=clock, sleep=clock.sleep)
    kb = KnowledgeBase(KB_PATH, clock=clock)
    log = ChatLog(tmp_path / "chat.jsonl", salt="test-salt")
    return SupportBot(caller, kb, KeywordGuard(), log), client, tmp_path / "chat.jsonl"


def records(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


def test_answer_uses_behavior_plus_knowledge(tmp_path):
    bot, client, _ = build(tmp_path, ["[통계] → [월별] → [엑셀]"])
    ans = bot.answer("u1", "엑셀은 어디서 받아요?")
    assert ans.route == "ai" and ans.model == "model-v2"
    system = client.calls[0]["system"]
    assert system.startswith(BEHAVIOR)
    assert "[제품 지식]" in system and "[엑셀]" in system      # knowledge attached at call time


def test_behavior_holds_no_product_facts():
    # facts live only in the knowledge file - no menu paths, prices or times in the behavior text
    for fact in ("[설정]", "원", "18:00", "엑셀"):
        assert fact not in BEHAVIOR


def test_empty_knowledge_degrades_safely(tmp_path):
    clock = FakeClock()
    client = FakeClient(["ok"], clock=clock)
    caller = LLMCaller(client, ["m"], clock=clock, sleep=clock.sleep)
    bot = SupportBot(caller, KnowledgeBase(tmp_path / "missing.md", clock=clock))
    bot.answer("u", "q")
    assert "기억으로 답하지 말고" in client.calls[0]["system"]


def test_guard_answers_without_calling_the_model(tmp_path):
    bot, client, logp = build(tmp_path, ["should not be used"])
    ans = bot.answer("u1", "환불 받고 싶어요")
    assert ans.route == "guard" and client.calls == []
    assert records(logp)[0]["route"] == "guard"


def test_failure_shows_a_cause_specific_message(tmp_path):
    bot, _, logp = build(tmp_path, [E.AuthRejected(401, "m")])
    ans = bot.answer("u1", "알림 문구는 어디서 바꿔요?")
    assert ans.route == "error:key_rejected"
    assert ans.text == E.KeyRejected.user_message
    assert records(logp)[0]["route"] == "error:key_rejected"


def test_log_masks_personal_data_and_hashes_user(tmp_path):
    bot, _, logp = build(tmp_path, ["확인했습니다"])
    phone = "010-" + "4821-7356"
    bot.answer(phone, "제 번호 %s, 메일 kim.sample@example.com 로 연락 주세요" % phone)
    rec = records(logp)[0]
    raw = json.dumps(rec, ensure_ascii=False)
    assert "4821" not in raw and "kim.sample" not in raw
    assert "[PHONE-7356]" in rec["q"] and "[EMAIL]" in rec["q"]
    assert rec["user"] != phone and len(rec["user"]) == 16


def test_mask_patterns():
    assert mask("주민번호 900101-" + "1234567") == "주민번호 [RRN]"
    assert mask("카드 1234 5678 9012 3456") == "카드 [NUMBER]"
    assert mask("예약 3건, 18:00") == "예약 3건, 18:00"             # ordinary numbers stay
