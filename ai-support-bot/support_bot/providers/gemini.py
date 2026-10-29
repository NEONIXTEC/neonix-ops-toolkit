"""Google Gemini adapter (google-genai SDK).  pip install google-genai

    clients = [GeminiClient(api_key=k) for k in keys]   # 1-3 keys; this module never reads env or files
    caller = LLMCaller(clients, models=[...pinned versions..., "...-latest"])
"""
from .. import errors as E


class GeminiClient:
    def __init__(self, api_key):
        from google import genai   # lazy: tests and other providers do not need the SDK
        self._genai = genai
        self._client = genai.Client(api_key=api_key)

    def generate(self, *, model, system, user, max_tokens, timeout):
        from google.genai import errors as gerr, types
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            http_options=types.HttpOptions(timeout=int(timeout * 1000)),   # milliseconds
        )
        try:
            resp = self._client.models.generate_content(model=model, contents=user, config=config)
        except gerr.APIError as e:
            raise _translate(getattr(e, "code", None), model) from None
        except TimeoutError:
            raise E.CallTimeout(None, model) from None
        except OSError:
            raise E.ConnectionLost(None, model) from None
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            raise E.Unavailable(None, model)    # empty / blocked answer: try the next model
        return text


def _translate(status, model):
    if status in (401, 403):
        return E.AuthRejected(status, model)
    if status == 404:
        return E.ModelUnavailable(status, model)
    if status == 429:
        return E.RateLimited(status, model)
    if status in (408, 504):
        return E.CallTimeout(status, model)
    if status and status >= 500:
        return E.Unavailable(status, model)
    return E.BadRequest(status, model)
