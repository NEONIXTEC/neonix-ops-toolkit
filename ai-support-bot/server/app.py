"""FastAPI layer: POST /api/chat -> SupportBot.answer.

    cd ai-support-bot
    uvicorn server.app:app --port 8000          # demo mode: no key, answers from knowledge/faq.md

Real provider (keys come from the environment of this process only - never from a file in the repo):
    SUPPORT_BOT_PROVIDER = demo | gemini | claude     (default demo)
    SUPPORT_BOT_API_KEYS = key1[,key2[,key3]]         (1-3 keys, failover in this order)
    SUPPORT_BOT_MODELS   = model-a[,model-b,...]      (pinned versions first, an alias last)

If web/dist exists (npm run build), the same process also serves the chat UI at /.
"""
import ipaddress
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
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


def client_key(host):
    """IPv6 clients are grouped by /64 - one host usually owns the whole /64, so per-address keys are free to rotate.

    Parsed with ipaddress, not string splitting: "2001:db8::1" and "2001:db8::2" are the same /64 even though
    their text differs. IPv4-mapped IPv6 (::ffff:1.2.3.4) counts as the IPv4 address. Anything unparsable is
    used as-is (never raises).
    """
    if not host:
        return "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class RateLimiter:
    """Per-client sliding window. A public chat endpoint costs money per call, so it needs a cap.

    Memory is bounded and the cap cannot be reset by flooding:
      - clients live in an LRU map of at most `max_clients`. A new client may only push out an entry whose
        last hit is older than the window (it would have been allowed anyway). The front of the LRU map is the
        least recently seen client, so checking it is O(1).
      - if the map is full of clients that are still inside their window, a new client is refused (fail closed)
        instead of evicting someone whose limit is still running.
      - `global_limit` caps all requests per window together, so rotating addresses (each with its own fresh
        per-client budget) still cannot run up the bill.
    Trade-off, decided on purpose: both caps fail closed. Every answer costs an API call, so a flood that
    briefly blocks everyone is preferred to a flood that runs up the bill. Availability against bot floods
    belongs in front of this process - a real deployment sits behind a CDN/WAF with bot protection
    (production runs behind Cloudflare).
    Behind a proxy every request comes from the proxy's address, so per-client keys collapse into one. Start
    uvicorn with --proxy-headers --forwarded-allow-ips=<proxy address>: only a trusted proxy's X-Forwarded-For
    then becomes request.client.host. This code never reads forwarding headers itself (they are forgeable).
    """

    def __init__(self, limit=20, window_sec=60.0, max_clients=10_000, global_limit=600, clock=time.monotonic):
        self.limit, self.window, self.max_clients, self._clock = limit, window_sec, max_clients, clock
        self.global_limit = global_limit
        self._hits = OrderedDict()
        self._all = deque()
        self._lock = threading.Lock()

    def allowed(self, key):
        now = self._clock()
        with self._lock:
            while self._all and now - self._all[0] >= self.window:
                self._all.popleft()
            if len(self._all) >= self.global_limit:
                return False
            q = self._hits.get(key)
            if q is None:
                while len(self._hits) >= self.max_clients:
                    _, oldest = next(iter(self._hits.items()))
                    if oldest and now - oldest[-1] < self.window:
                        return False               # every tracked client is active: refuse the newcomer
                    self._hits.popitem(last=False)
                q = self._hits[key] = deque()
            self._hits.move_to_end(key)
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            self._all.append(now)
            return True

    def __len__(self):
        return len(self._hits)


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
    bot = bot if bot is not None else build_bot_from_env()
    limiter = limiter if limiter is not None else RateLimiter()   # not `or`: an empty limiter has len 0
    app = FastAPI(title="support-bot demo", docs_url=None, redoc_url=None)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # security-ok: anonymous support chat is the product; capped per client below, guard keeps money/account topics away from the model
    @app.post("/api/chat", response_model=ChatOut)
    def chat(body: ChatIn, request: Request):
        client = client_key(request.client.host if request.client else "")
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
