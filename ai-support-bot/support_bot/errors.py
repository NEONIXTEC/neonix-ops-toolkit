"""Error types.

Two layers:
  ProviderError subclasses  - what one model call reported (raised by a provider adapter, caught by LLMCaller)
  BotError subclasses       - what the whole answer attempt ended with (raised by LLMCaller, shown to the user)

Every BotError carries its own user-facing sentence. One cause, one sentence: a message that says
"rate limit or network problem" hides which of two opposite fixes is needed (add capacity vs raise the
timeout), so no message here uses "or".
"""


class ProviderError(Exception):
    """One call failed. `status` is the HTTP status when there was one (None for timeouts/connection)."""

    retryable = False
    next_model = False      # True: this model cannot serve (retired, unknown) - try the next one, no pause

    def __init__(self, status=None, model=""):
        super().__init__("%s status=%s model=%s" % (type(self).__name__, status, model))
        self.status = status
        self.model = model


class RateLimited(ProviderError):        # 429
    retryable = True


class Unavailable(ProviderError):        # 5xx / overloaded
    retryable = True


class CallTimeout(ProviderError):        # read timeout
    retryable = True


class ConnectionLost(ProviderError):     # dropped before a response
    retryable = True


class AuthRejected(ProviderError):       # 401 / 403 - this key is out; retrying it only burns the deadline
    pass


class ModelUnavailable(ProviderError):   # 404 / model retired - skip to the next model in the list
    next_model = True


class BadRequest(ProviderError):         # other 4xx - our request is wrong, retrying cannot help
    pass


class BotError(Exception):
    kind = "error"
    user_message = "답변을 만들지 못했습니다. 잠시 후 다시 시도해 주세요."

    def __init__(self, reason=""):
        super().__init__(reason or self.kind)
        self.reason = reason            # short, safe for logs and alerts: no key, no prompt text


class NoKeyConfigured(BotError):
    kind = "no_key"
    user_message = "AI 상담이 아직 설정되지 않았습니다. 운영자가 확인 후 답변드리겠습니다."


class KeyRejected(BotError):
    kind = "key_rejected"
    user_message = "AI 상담 연결이 거부되었습니다. 운영자가 확인 후 답변드리겠습니다."


class AllAttemptsFailed(BotError):
    kind = "all_failed"
    user_message = "AI 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요."


class Busy(BotError):
    kind = "busy"
    user_message = "문의가 몰려 순서를 기다리다 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."


class BadRequestError(BotError):
    kind = "bad_request"
    user_message = "질문을 처리하지 못했습니다. 운영자가 확인 후 답변드리겠습니다."
