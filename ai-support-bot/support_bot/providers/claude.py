"""Anthropic Claude adapter (official SDK).  pip install anthropic

    client = ClaudeClient(api_key=...)          # or ClaudeClient() to use the SDK's own credential lookup
    caller = LLMCaller([client], models=["claude-opus-5-5"])   # one client per API key

The SDK's own retries are turned off (max_retries=0): LLMCaller owns retries, pacing and the deadline.
A refusal (stop_reason "refusal") raises Unavailable instead of switching models behind the caller's back -
the caller stays inside the models it was given, like everything else here.
"""
from .. import errors as E

DEFAULT_MODEL = "claude-opus-5-5"


class ClaudeClient:
    def __init__(self, api_key=None, effort="low"):
        import anthropic   # lazy
        self._anthropic = anthropic
        kwargs = {"max_retries": 0}
        if api_key:
            kwargs["api_key"] = api_key
        self._client = anthropic.Anthropic(**kwargs)
        self.effort = effort            # chat answers rarely need deep reasoning; raise it if quality needs it

    def generate(self, *, model, system, user, max_tokens, timeout):
        a = self._anthropic
        try:
            msg = self._client.with_options(timeout=timeout).messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort},
            )
        except a.AuthenticationError as e:
            raise E.AuthRejected(e.status_code, model) from None
        except a.PermissionDeniedError as e:
            raise E.AuthRejected(e.status_code, model) from None
        except a.NotFoundError as e:
            raise E.ModelUnavailable(e.status_code, model) from None
        except a.RateLimitError as e:
            raise E.RateLimited(e.status_code, model) from None
        except a.InternalServerError as e:          # 5xx incl. 529 overloaded
            raise E.Unavailable(e.status_code, model) from None
        except a.APIStatusError as e:               # any other non-2xx
            raise E.BadRequest(e.status_code, model) from None
        except a.APITimeoutError:
            raise E.CallTimeout(None, model) from None
        except a.APIConnectionError:
            raise E.ConnectionLost(None, model) from None
        if msg.stop_reason == "refusal":
            raise E.Unavailable(None, model)
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if not text:
            raise E.Unavailable(None, model)
        return text
