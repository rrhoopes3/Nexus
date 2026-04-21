"""
Nexus LLM abstraction — multi-provider routing.

Auto-detects provider from env vars or model name prefix.
Supports: xAI (Grok), Anthropic (Claude), OpenAI, LM Studio, Ollama.

Env vars:
  XAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY
  LMSTUDIO_BASE_URL (default http://localhost:1234/v1)
  OLLAMA_BASE_URL   (default http://localhost:11434/v1)
  NEXUS_MODEL       (override default model)
  NEXUS_MAX_RETRIES (default 3)
"""
from __future__ import annotations

import os
import time
import logging
from typing import Any, Callable

log = logging.getLogger("nexus.llm")

# Read env once at import
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
NEXUS_MODEL = os.environ.get("NEXUS_MODEL", "")
MAX_RETRIES = int(os.environ.get("NEXUS_MAX_RETRIES", "3"))
RETRY_BASE_DELAY = 1.0

_RETRYABLE_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
}


def _is_retryable(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in _RETRYABLE_NAMES:
        return True
    if name == "APIStatusError":
        status = getattr(exc, "status_code", None)
        return status is not None and status >= 500
    return isinstance(exc, (ConnectionError, TimeoutError))


def _with_retry(fn: Callable, *args, **kwargs):
    last_exc: BaseException | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if not _is_retryable(e):
                raise
            last_exc = e
            if attempt == MAX_RETRIES - 1:
                break
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            log.warning(
                "LLM call failed (attempt %d/%d): %s: %s — retrying in %.1fs",
                attempt + 1, MAX_RETRIES, type(e).__name__, e, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _log_usage(model: str, usage: Any) -> None:
    if not usage:
        return
    in_tok = getattr(usage, "input_tokens", None)
    if in_tok is None:
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", None)
    if out_tok is None:
        out_tok = getattr(usage, "completion_tokens", 0) or 0
    log.info("LLM tokens: in=%d out=%d model=%s", in_tok, out_tok, model)


def llm_call(
    messages: list[dict[str, str]],
    system: str = "",
    model: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    structured: bool = False,
) -> str:
    """Multi-provider LLM call. Returns the assistant's response text.

    If structured=True, requests a JSON object response. The output is the
    raw JSON text — caller is responsible for parsing.
    """
    if not model:
        model = NEXUS_MODEL

    # Determine provider
    if not model:
        if XAI_API_KEY:
            model = "grok-4-1-fast-reasoning"
            provider = "xai"
        elif ANTHROPIC_API_KEY:
            model = "claude-sonnet-4-20250514"
            provider = "anthropic"
        elif OPENAI_API_KEY:
            model = "gpt-4o-mini"
            provider = "openai"
        else:
            model = "default"
            provider = "lmstudio"
    else:
        if model.startswith("claude-"):
            provider = "anthropic"
        elif model.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
            provider = "openai"
        elif model.startswith("lmstudio:"):
            provider = "lmstudio"
        elif model.startswith("ollama:"):
            provider = "ollama"
        else:
            provider = "xai"

    log.debug(
        "LLM call: provider=%s model=%s tokens=%d structured=%s",
        provider, model, max_tokens, structured,
    )

    if provider == "anthropic":
        return _with_retry(
            _call_anthropic, messages, system, model,
            temperature, max_tokens, structured,
        )
    elif provider in ("openai", "lmstudio", "ollama"):
        base_url = None
        api_key = OPENAI_API_KEY or "local"
        if provider == "lmstudio":
            base_url = LMSTUDIO_BASE_URL
            api_key = "lm-studio"
            model = model.removeprefix("lmstudio:") or "default"
        elif provider == "ollama":
            base_url = OLLAMA_BASE_URL
            api_key = "ollama"
            model = model.removeprefix("ollama:") or "default"
        return _with_retry(
            _call_openai, messages, system, model,
            temperature, max_tokens, base_url, api_key, structured,
        )
    else:
        # xAI uses OpenAI-compatible API
        return _with_retry(
            _call_openai, messages, system, model,
            temperature, max_tokens,
            "https://api.x.ai/v1", XAI_API_KEY, structured,
        )


def _call_anthropic(
    messages: list[dict], system: str, model: str,
    temperature: float, max_tokens: int, structured: bool = False,
) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    api_messages = list(messages)
    if structured:
        # Prefill the assistant turn with `{` so the model continues a JSON object.
        api_messages = api_messages + [{"role": "assistant", "content": "{"}]

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": api_messages,
    }
    if system:
        kwargs["system"] = system
    if temperature is not None:
        kwargs["temperature"] = min(temperature, 1.0)
    resp = client.messages.create(**kwargs)
    _log_usage(model, getattr(resp, "usage", None))
    text = resp.content[0].text
    if structured:
        text = "{" + text
    return text


def _call_openai(
    messages: list[dict], system: str, model: str,
    temperature: float, max_tokens: int,
    base_url: str | None = None, api_key: str = "",
    structured: bool = False,
) -> str:
    from openai import OpenAI
    kwargs: dict[str, Any] = {"api_key": api_key or "none"}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    api_messages = []
    if system:
        api_messages.append({"role": "system", "content": system})
    api_messages.extend(messages)
    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if structured:
        create_kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**create_kwargs)
    _log_usage(model, getattr(resp, "usage", None))
    return resp.choices[0].message.content or ""
