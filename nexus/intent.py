"""
Nexus intent engine — translates natural language to Rust code.

Phase 1: hybrid compilation, type tightening, error whisper.
Phase 3: profile-aware prompts — LLM adapts to developer's style.
"""
from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from typing import Any

from nexus.llm import llm_call
from nexus.prompts import (
    INTENT_TO_RUST, VERIFY_CODE, REPL_CONTEXT, FIX_ERRORS,
    HYBRID_COMPILE, TIGHTEN_TYPES, ERROR_WHISPER,
)

log = logging.getLogger("nexus.intent")


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences if the LLM wraps output despite instructions."""
    text = text.strip()
    m = re.match(r"^```(?:rust)?\s*\n(.*?)```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _strip_json_fences(text: str) -> str:
    """Remove markdown JSON fences."""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*\n(.*?)```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


# ── Core Compilation ──────────────────────────────────────────────────────

def _inject_style(system_prompt: str, profile_path: Path | None = None) -> str:
    """Inject developer style preferences into a system prompt.

    If a profile exists and has learned style, append it to the prompt
    so the LLM generates code that matches the developer's voice.
    """
    if not profile_path or not profile_path.exists():
        return system_prompt

    try:
        from nexus.style import StyleProfile, build_style_profile, StyleObservation
        from nexus.profile import get_style_summary

        style_summary = get_style_summary(profile_path)
        if not style_summary:
            return system_prompt

        # Build a prompt fragment from the style summary
        lines = ["\n\nDeveloper style preferences (match these in your output):"]
        for cat, patterns in style_summary.items():
            strong = [(p, c) for p, c in patterns if c >= 0.5]
            if strong:
                items = ", ".join(p for p, c in strong[:3])
                lines.append(f"  - {cat}: {items}")

        if len(lines) <= 1:
            return system_prompt

        return system_prompt + "\n".join(lines)
    except Exception:
        return system_prompt


def compile_intents(
    intents: list[str],
    context: str = "",
    strict_blocks: list[str] | None = None,
    profile_path: Path | None = None,
) -> str:
    """Compile intent strings into Rust source code.

    Args:
        intents: Natural language intent statements.
        context: Optional existing code context (for REPL mode).
        strict_blocks: Optional list of Rust code blocks to preserve.
        profile_path: Optional path to developer profile for style injection.

    Returns:
        Rust source code as a string.
    """
    if not intents and not strict_blocks:
        return ""

    # If we have strict blocks, use hybrid compilation
    if strict_blocks:
        return compile_hybrid(intents, strict_blocks, context, profile_path)

    prompt_parts = []
    if context:
        prompt_parts.append(f"Current program state:\n```rust\n{context}\n```\n")
        prompt_parts.append("Apply these changes:")
    for i, intent in enumerate(intents, 1):
        prompt_parts.append(f"{i}. {intent}")

    user_msg = "\n".join(prompt_parts)
    system = REPL_CONTEXT if context else INTENT_TO_RUST
    system = _inject_style(system, profile_path)

    response = llm_call(
        messages=[{"role": "user", "content": user_msg}],
        system=system,
    )
    return _strip_markdown_fences(response)


def compile_hybrid(
    intents: list[str],
    strict_blocks: list[str],
    context: str = "",
    profile_path: Path | None = None,
) -> str:
    """Compile intents alongside immutable strict blocks.

    The LLM generates code that works with the strict blocks but does not
    modify them. The output is a complete program with strict code + generated code.
    """
    prompt_parts = []

    # Present strict blocks as immutable context
    strict_combined = "\n\n".join(strict_blocks)
    prompt_parts.append("STRICT BLOCKS (do not modify):")
    prompt_parts.append(f"```rust\n{strict_combined}\n```")

    if context:
        prompt_parts.append(f"\nExisting generated code:\n```rust\n{context}\n```")

    if intents:
        prompt_parts.append("\nINTENTS (compile these alongside the strict blocks):")
        for i, intent in enumerate(intents, 1):
            prompt_parts.append(f"{i}. {intent}")
    else:
        prompt_parts.append("\nNo intents — just ensure the strict blocks form a valid program.")

    user_msg = "\n".join(prompt_parts)
    system = _inject_style(HYBRID_COMPILE, profile_path)
    response = llm_call(
        messages=[{"role": "user", "content": user_msg}],
        system=system,
    )
    return _strip_markdown_fences(response)


# ── Verification ──────────────────────────────────────────────────────────

def verify_code(rust_code: str) -> str:
    """Explain what a Rust program does in plain English."""
    response = llm_call(
        messages=[{"role": "user", "content": rust_code}],
        system=VERIFY_CODE,
        temperature=0.2,
    )
    return response.strip()


# ── Error Handling ────────────────────────────────────────────────────────

def fix_errors(rust_code: str, errors: str) -> str:
    """Attempt to fix Rust compiler errors automatically."""
    user_msg = f"Source code:\n```rust\n{rust_code}\n```\n\nCompiler errors:\n```\n{errors}\n```"
    response = llm_call(
        messages=[{"role": "user", "content": user_msg}],
        system=FIX_ERRORS,
    )
    return _strip_markdown_fences(response)


def whisper_error(
    rust_code: str,
    errors: str,
    error_history: list[dict] | None = None,
) -> str:
    """Produce a friendly diagnostic for compiler errors.

    Args:
        rust_code: The broken Rust source.
        errors: Raw compiler error output.
        error_history: Past error patterns from the developer's profile.

    Returns:
        Human-friendly diagnostic string.
    """
    prompt_parts = [
        f"Source code:\n```rust\n{rust_code}\n```",
        f"\nCompiler errors:\n```\n{errors}\n```",
    ]

    if error_history:
        prompt_parts.append("\nDeveloper's error history (recent):")
        for entry in error_history[-10:]:
            prompt_parts.append(
                f"  - {entry.get('error_type', '?')}: "
                f"{entry.get('count', 1)} time(s), "
                f"auto-fixed: {entry.get('auto_fixed', False)}"
            )

    response = llm_call(
        messages=[{"role": "user", "content": "\n".join(prompt_parts)}],
        system=ERROR_WHISPER,
        temperature=0.2,
    )
    return response.strip()


# ── Type Tightening ───────────────────────────────────────────────────────

def tighten_types(rust_code: str) -> list[dict[str, Any]]:
    """Analyze Rust code and suggest type improvements.

    Returns:
        List of suggestion dicts with keys:
        function, location, current, suggested, confidence, reason
    """
    response = llm_call(
        messages=[{"role": "user", "content": rust_code}],
        system=TIGHTEN_TYPES,
        temperature=0.1,
    )

    cleaned = _strip_json_fences(response)

    try:
        suggestions = json.loads(cleaned)
        if not isinstance(suggestions, list):
            log.warning("Tighten returned non-list: %s", type(suggestions))
            return []
        return suggestions
    except json.JSONDecodeError as e:
        log.warning("Failed to parse tighten response: %s", e)
        return []


def apply_tighten(rust_code: str, suggestions: list[dict]) -> str:
    """Apply accepted type suggestions to the code.

    Uses the LLM to apply changes since regex-based type replacement
    in Rust is fragile.
    """
    if not suggestions:
        return rust_code

    changes = "\n".join(
        f"- In {s['function']}: change {s['location']} from `{s['current']}` to `{s['suggested']}`"
        for s in suggestions
    )

    user_msg = (
        f"Apply these type changes to the code:\n{changes}\n\n"
        f"Source code:\n```rust\n{rust_code}\n```"
    )

    response = llm_call(
        messages=[{"role": "user", "content": user_msg}],
        system=(
            "You are Nexus. Apply the requested type changes to the Rust code. "
            "Output ONLY the modified Rust source. No markdown fences, no explanations. "
            "Preserve all logic — only change the types as specified."
        ),
    )
    return _strip_markdown_fences(response)
