"""FastAPI layer: POST /api/chat -> SupportBot.answer.

    cd ai-support-bot
    uvicorn server.app:app --port 8000          # demo mode: no key, answers from knowledge/faq.md

Real provider (keys come from the environment of this process only - never from a file in the repo):
    SUPPORT_BOT_PROVIDER = demo | gemini | claude     (default demo)
    SUPPORT_BOT_API_KEYS = key1[,key2[,key3]]         (1-3 keys, failover in this order)
    SUPPORT_BOT_MODELS   = model-a[,model-b,...]      (pinned versions first, an alias last)

If web/dist exists (npm run build), the same process also serves the chat UI at /.
"""
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from support_bot import ChatLog, KeywordGuard, KnowledgeBase, LLMCaller, SupportBot

from .demo_provider import DemoClient

BASE = Path(__file__).resolve().parent.parent
KB_PATH = BASE / "knowledge" / "faq.md"
DIST = BASE / "web" / "dist"
MAX_MESSAGE = 2000


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    session_id: str = Field(default="", max_length=64)


class ChatOut(BaseModel):
    text: str
    route: str        # guard | ai | error:<kind>
    model: str = ""


class RateLimiter:
    """Per-client sliding window. A public chat endpoint costs money per call, so it needs a cap."""

    def __init__(self, limit=20, window_sec=60.0, clock=time.monotonic):
        self.limit, self.window, self._clock = limit, window_sec, clock
        self._hits = {}
        self._lock = threading.Lock()

    def allowed(self, key):
        now = self._clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:                      # bound memory on many distinct clients
                for k in [k for k, v in self._hits.items() if not v][:5_000]:
                    del self._hits[k]
            return True


def build_bot_from_env(env=os.environ):
    provider = (env.get("SUPPORT_BOT_PROVIDER") or "demo").strip().lower()
    keys = [k.strip() for k in (env.get("SUPPORT_BOT_API_KEYS") or "").split(",") if k.strip()][:3]
    models = [m.strip() for m in (env.get("SUPPORT_BOT_MODELS") or "").split(",") if m.strip()]
    if provider == "gemini":
        from support_bot.providers.gemini import GeminiClient
        clients = [GeminiClient(api_key=k) for k in keys]
    elif provider == "claude":
        from support_bot.providers.claude import DEFAULT_MODEL, ClaudeClient
        clients = [ClaudeClient(api_key=k) for k in keys]
        models = models or [DEFAULT_MODEL]
    else:
        clients, models = [DemoClient()], ["demo"]
    caller = LLMCaller(clients, models or ["demo"])
    log_dir = Path(env.get("SUPPORT_BOT_LOG_DIR") or BASE / "logs")
    salt = env.get("SUPPORT_BOT_LOG_SALT") or secrets.token_hex(16)   # per-process salt when none is given
    return SupportBot(caller, KnowledgeBase(KB_PATH), KeywordGuard(), ChatLog(log_dir / "chat.jsonl", salt=salt))


def create_app(bot=None, limiter=None):
    bot = bot or build_bot_from_env()
    limiter = limiter or RateLimiter()
    app = FastAPI(title="support-bot demo", docs_url=None, redoc_url=None)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # security-ok: anonymous support chat is the product; capped per client below, guard keeps money/account topics away from the model
    @app.post("/api/chat", response_model=ChatOut)
    def chat(body: ChatIn, request: Request):
        client = request.client.host if request.client else "unknown"
        if not limiter.allowed(client):    # rate_limit: 20 per minute per client
            raise HTTPException(status_code=429, detail="too many requests")
        ans = bot.answer(body.session_id or client, body.message)
        return ChatOut(text=ans.text, route=ans.route, model=ans.model)

    if DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(DIST / "index.html")

    return app


app = create_app()
