"""Provider adapters. Each one exposes

    generate(*, model, system, user, max_tokens, timeout) -> str

and translates the vendor SDK's errors into support_bot.errors.ProviderError subclasses, so LLMCaller's
failover/retry/deadline rules are written once and work for every vendor. SDKs are imported lazily:
the package and its tests run without any vendor SDK installed.
"""
