"""Pre-LLM keyword guard.

Money, legal and account-security questions never go to the model: a fixed reply hands them to a human
at once (and costs no API call). The list is short on purpose - every word on it is a question the bot
will never answer, so add words only when a wrong AI answer there would cost money or trust.
"""
import re

DEFAULT_WORDS = ("환불", "해지", "위약금", "소송", "비밀번호", "탈퇴")
DEFAULT_REPLY = ("이 문의는 운영자가 직접 확인해야 하는 내용이라 AI 가 답하지 않습니다. "
                 "운영자가 확인 후 이 대화로 답변드리겠습니다.")


class KeywordGuard:
    def __init__(self, words=DEFAULT_WORDS, reply=DEFAULT_REPLY):
        self.words = tuple(words)
        self.reply = reply
        self._rx = re.compile("|".join(re.escape(w) for w in self.words)) if self.words else None

    def check(self, text):
        """# O: the fixed reply when a guarded word appears, else None"""
        if self._rx is not None and self._rx.search(text or ""):
            return self.reply
        return None
