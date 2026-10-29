"""support_bot - structure of a production AI support bot, rebuilt as a small reusable package.

    from support_bot import SupportBot, KnowledgeBase, LLMCaller, AlertGate, ChatLog
    from support_bot.providers.gemini import GeminiClient   # or .claude import ClaudeClient

See ../README.md for why each piece exists.
"""
from .alerts import AlertGate
from .bot import Answer, SupportBot
from .chatlog import ChatLog, mask
from .errors import (AllAttemptsFailed, BotError, Busy, KeyRejected, NoKeyConfigured, ProviderError)
from .guard import KeywordGuard
from .kb import KnowledgeBase
from .llm import LLMCaller, Reply

__all__ = [
    "AlertGate", "Answer", "SupportBot", "ChatLog", "mask", "AllAttemptsFailed", "BotError", "Busy",
    "KeyRejected", "NoKeyConfigured", "ProviderError", "KeywordGuard", "KnowledgeBase", "LLMCaller", "Reply",
]
