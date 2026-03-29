"""
Nexus predictive whisper — catches patterns before compilation.

Phase 3: pre-compile analysis that surfaces suggestions based on
the developer's history, not just compiler errors.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from pathlib import Path

from nexus.profile import (
    get_error_patterns, get_style_summary, is_tombstoned,
    detect_anti_patterns, AntiPattern,
)

log = logging.getLogger("nexus.whisper")


@dataclass
class Whisper:
    """A pre-compile suggestion."""
    level: str      # hint, warning, nudge
    pattern: str    # what was detected
    message: str    # human-friendly message
    line: int       # approximate line number (0 = whole file)
    suggestion: str  # what to do about it


def analyze_pre_compile(
    rust_code: str,
    profile_path: Path,
) -> list[Whisper]:
    """Analyze code before compilation for predictable issues.

    Uses the developer's profile to surface relevant warnings.
    """
    whispers = []

    # ── Anti-pattern escalation ───────────────────────────────────────
    anti_patterns = detect_anti_patterns(profile_path)
    for ap in anti_patterns:
        if is_tombstoned(profile_path, ap.pattern):
            continue

        if ap.level == "auto_fix":
            whispers.append(Whisper(
                level="warning",
                pattern=ap.pattern,
                message=ap.message,
                line=0,
                suggestion="Will auto-fix if this error occurs again.",
            ))
        elif ap.level == "warning":
            whispers.append(Whisper(
                level="warning",
                pattern=ap.pattern,
                message=ap.message,
                line=0,
                suggestion="Review your approach to avoid this pattern.",
            ))

    # ── Common Rust pitfalls (profile-aware) ──────────────────────────
    error_history = get_error_patterns(profile_path)
    error_types = {ep["error_type"]: ep["count"] for ep in error_history}

    # Unwrap detection — only flag if they've had unwrap panics before
    unwrap_lines = [
        (i + 1, line) for i, line in enumerate(rust_code.splitlines())
        if ".unwrap()" in line and "// safe:" not in line.lower()
    ]
    if unwrap_lines and error_types.get("type_mismatch", 0) > 0:
        for line_no, line in unwrap_lines[:3]:  # cap at 3
            whispers.append(Whisper(
                level="hint",
                pattern="unwrap_risk",
                message=f"Line {line_no}: .unwrap() — you've had type errors before",
                line=line_no,
                suggestion="Consider using ? or .unwrap_or_default()",
            ))

    # O(n^2) detection — nested loops over same collection
    lines = rust_code.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"\s*for\s+\w+\s+in\s+(\w+)", line):
            collection = re.match(r"\s*for\s+\w+\s+in\s+(\w+)", line).group(1)
            # Check next 10 lines for another loop over same collection
            for j in range(i + 1, min(i + 10, len(lines))):
                if re.match(rf"\s*for\s+\w+\s+in\s+{re.escape(collection)}", lines[j]):
                    whispers.append(Whisper(
                        level="warning",
                        pattern="nested_loop_same_collection",
                        message=f"Lines {i+1}-{j+1}: nested loops over `{collection}` — likely O(n^2)",
                        line=i + 1,
                        suggestion="Consider using a HashMap or single-pass approach.",
                    ))
                    break

    # Mutable borrow in loop — common borrow checker issue
    if error_types.get("borrow_error", 0) >= 2:
        for i, line in enumerate(lines):
            if "for" in line and "&mut" in line:
                whispers.append(Whisper(
                    level="hint",
                    pattern="mut_borrow_in_loop",
                    message=f"Line {i+1}: mutable borrow in loop — you've hit borrow errors {error_types['borrow_error']}x",
                    line=i + 1,
                    suggestion="Consider collecting indices first, then mutating.",
                ))

    # ── Style drift detection ─────────────────────────────────────────
    style_summary = get_style_summary(profile_path)

    # Check naming consistency
    naming_prefs = style_summary.get("naming", [])
    if naming_prefs:
        top_naming = naming_prefs[0][0] if naming_prefs else ""

        if top_naming == "snake_case_functions":
            camel_fns = re.findall(r"fn\s+([a-z][a-zA-Z0-9]*[A-Z]\w*)", rust_code)
            if camel_fns:
                whispers.append(Whisper(
                    level="hint",
                    pattern="naming_drift",
                    message=f"Found camelCase functions ({', '.join(camel_fns[:3])}) but you prefer snake_case",
                    line=0,
                    suggestion="Nexus will use snake_case in future generations.",
                ))

    return whispers


def format_whispers(whispers: list[Whisper]) -> str:
    """Format whispers for display."""
    if not whispers:
        return ""

    level_icons = {
        "hint": "\033[90m~\033[0m",
        "warning": "\033[33m!\033[0m",
        "nudge": "\033[35m?\033[0m",
    }

    lines = ["\033[35m── nexus whisper (pre-compile) ─────────────────\033[0m"]
    for w in whispers:
        icon = level_icons.get(w.level, "?")
        lines.append(f"  [{icon}] {w.message}")
        lines.append(f"      {w.suggestion}")
    lines.append("\033[35m───────────────────────────────────────────────\033[0m")

    return "\n".join(lines)
