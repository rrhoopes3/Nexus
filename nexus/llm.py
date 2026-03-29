"""
Nexus LLM abstraction — multi-provider routing.

Auto-detects provider from env vars or model name prefix.
Supports: xAI (Grok), Anthropic (Claude), OpenAI, LM Studio, Ollama.

Env vars:
  XAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY
  LMSTUDIO_BASE_URL (default http://localhost:1234/v1)
  OLLAMA_BASE_URL   (default http://localhost:11434/v1)
  NEXUS_MODEL       (override default model)
"""
from __future__ import annotations

import os
import logging
from typing import Any

log = logging.getLogger("nexus.llm")

# Read env once at import
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
NEXUS_MODEL = os.environ.get("NEXUS_MODEL", "")


def llm_call(
    messages: list[dict[str, str]],
    system: str = "",
    model: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Multi-provider LLM call. Returns the assistant's response text."""
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

    log.debug("LLM call: provider=%s model=%s tokens=%d", provider, model, max_tokens)

    if provider == "anthropic":
        return _call_anthropic(messages, system, model, temperature, max_tokens)
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
        return _call_openai(messages, system, model, temperature, max_tokens, base_url, api_key)
    else:
        # xAI uses OpenAI-compatible API
        return _call_openai(
            messages, system, model, temperature, max_tokens,
            base_url="https://api.x.ai/v1",
            api_key=XAI_API_KEY,
        )


def _call_anthropic(
    messages: list[dict], system: str, model: str,
    temperature: float, max_tokens: int,
) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if temperature is not None:
        kwargs["temperature"] = min(temperature, 1.0)
    resp = client.messages.create(**kwargs)
    return resp.content[0].text


def _call_openai(
    messages: list[dict], system: str, model: str,
    temperature: float, max_tokens: int,
    base_url: str | None = None, api_key: str = "",
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
    resp = client.chat.completions.create(
        model=model,
        messages=api_messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""
