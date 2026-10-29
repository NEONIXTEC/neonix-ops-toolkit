"""Demo provider: answers from the knowledge text without calling any API, so the whole stack runs with no key.

It speaks the same adapter interface as the real providers (generate(*, model, system, user, max_tokens,
timeout) -> str), so LLMCaller's failover/retry/deadline rules and SupportBot's guard/log run unchanged.
The "answer" is the knowledge-base line that shares the most words with the question.
"""
import re

_WORD = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOP = {"어디서", "어떻게", "있나요", "되나요", "하나요", "알려", "주세요", "무엇", "얼마", "인가요", "어디"}


def _words(text):
    return {w for w in _WORD.findall(text or "") if w not in _STOP}


class DemoClient:
    def generate(self, *, model, system, user, max_tokens, timeout):
        knowledge = system.split("[제품 지식]", 1)[1] if "[제품 지식]" in system else ""
        q = _words(user)
        best, score = None, 0
        for line in knowledge.splitlines():
            line = line.strip().lstrip("-").strip()
            if not line or line.startswith(("#", ">", "|---")):
                continue
            s = len(q & _words(line))
            if s > score:
                best, score = line, s
        if best is None:
            return "[데모 모드] 지식 문서에서 맞는 내용을 찾지 못했습니다. 운영자가 확인 후 안내드리겠습니다."
        return "[데모 모드] " + best
