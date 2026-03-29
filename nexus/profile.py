"""
Nexus developer profile — learns your coding style and adapts.

Phase 3: full style learning, confidence decay, tombstoning, anti-pattern detection.

The profile is a JSONL file where each line is an observation, decision, or event.
Aggregation happens at read time with recency weighting and decay.
"""
from __future__ import annotations

import json
import logging
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("nexus.profile")

# Decay: patterns lose confidence after this many days without reinforcement
DECAY_DAYS = 30
DECAY_RATE = 0.5  # halve confidence after DECAY_DAYS

# Tombstone marker — rejected suggestions won't be suggested again for this context
TOMBSTONE = "__tombstoned__"


# ── Core I/O ──────────────────────────────────────────────────────────────

def append_entry(profile_path: Path, entry_type: str, data: dict) -> None:
    """Append a profile entry to the JSONL file."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": entry_type,
        **data,
    }
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    with open(profile_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_profile(profile_path: Path) -> list[dict]:
    """Read all profile entries."""
    if not profile_path.exists():
        return []
    entries = []
    with open(profile_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _parse_ts(ts_str: str) -> datetime:
    """Parse an ISO timestamp, handling various formats."""
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _days_ago(ts_str: str) -> float:
    """How many days ago was this timestamp?"""
    ts = _parse_ts(ts_str)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    return max(delta.total_seconds() / 86400, 0)


def _decay_confidence(confidence: float, days_old: float) -> float:
    """Apply exponential decay to a confidence score."""
    if days_old <= 0:
        return confidence
    half_lives = days_old / DECAY_DAYS
    return confidence * (DECAY_RATE ** half_lives)


# ── Intent Logging ────────────────────────────────────────────────────────

def log_intent(profile_path: Path, intent: str, accepted: bool = True) -> None:
    """Log an intent compilation to the profile."""
    append_entry(profile_path, "intent", {
        "intent": intent,
        "accepted": accepted,
    })


# ── Style Logging ─────────────────────────────────────────────────────────

def log_style(profile_path: Path, observations: list[dict]) -> None:
    """Log style observations from code analysis."""
    for obs in observations:
        append_entry(profile_path, "style", obs)


def get_style_entries(profile_path: Path) -> list[dict]:
    """Get all style observations with decay applied."""
    entries = read_profile(profile_path)
    style_entries = [e for e in entries if e.get("type") == "style"]

    for entry in style_entries:
        days = _days_ago(entry.get("ts", ""))
        original_conf = entry.get("confidence", 0.5)
        entry["decayed_confidence"] = _decay_confidence(original_conf, days)
        entry["days_old"] = round(days, 1)

    return style_entries


def get_style_summary(profile_path: Path) -> dict[str, list[tuple[str, float]]]:
    """Aggregate style patterns by category with decay.

    Returns {category: [(pattern, confidence), ...]} sorted by confidence.
    """
    entries = get_style_entries(profile_path)
    if not entries:
        return {}

    # Group by category + pattern, take max decayed confidence
    aggregated: dict[str, dict[str, float]] = {}
    for e in entries:
        cat = e.get("category", "unknown")
        pat = e.get("pattern", "unknown")
        conf = e.get("decayed_confidence", 0)

        if cat not in aggregated:
            aggregated[cat] = {}
        # Keep highest confidence for each pattern
        if pat not in aggregated[cat] or conf > aggregated[cat][pat]:
            aggregated[cat][pat] = conf

    result = {}
    for cat, patterns in aggregated.items():
        sorted_patterns = sorted(patterns.items(), key=lambda x: x[1], reverse=True)
        # Filter out very decayed patterns
        result[cat] = [(p, round(c, 3)) for p, c in sorted_patterns if c >= 0.1]

    return result


# ── Error Tracking ────────────────────────────────────────────────────────

def log_error(
    profile_path: Path,
    error_type: str,
    module: str = "",
    auto_fixed: bool = False,
    error_text: str = "",
) -> None:
    """Log a compiler error to the profile."""
    append_entry(profile_path, "error", {
        "error_type": error_type,
        "module": module,
        "auto_fixed": auto_fixed,
        "error_snippet": error_text[:200] if error_text else "",
    })


def log_fix(profile_path: Path, error_type: str, auto_fixed: bool) -> None:
    """Log a compiler error fix (backwards compat)."""
    log_error(profile_path, error_type, auto_fixed=auto_fixed)


def classify_error(error_text: str) -> str:
    """Classify a Rust compiler error into a category."""
    text = error_text.lower()
    if "mismatched types" in text or ("expected" in text and "found" in text):
        return "type_mismatch"
    if "borrow" in text or "cannot move" in text or "lifetime" in text:
        return "borrow_error"
    if "not found" in text or "cannot find" in text:
        return "name_not_found"
    if "unused" in text:
        return "unused_warning"
    if "trait" in text and ("not implemented" in text or "bound" in text):
        return "trait_bound"
    if "syntax" in text or "unexpected" in text:
        return "syntax_error"
    if "overflow" in text:
        return "overflow"
    return "other"


def get_error_patterns(profile_path: Path) -> list[dict]:
    """Analyze error history and return patterns with counts."""
    entries = read_profile(profile_path)
    errors = [e for e in entries if e.get("type") == "error"]

    if not errors:
        return []

    by_type: dict[str, list[dict]] = {}
    for e in errors:
        et = e.get("error_type", "unknown")
        by_type.setdefault(et, []).append(e)

    patterns = []
    for error_type, instances in by_type.items():
        auto_fixed = sum(1 for e in instances if e.get("auto_fixed"))
        patterns.append({
            "error_type": error_type,
            "count": len(instances),
            "auto_fixed_count": auto_fixed,
            "last_seen": instances[-1].get("ts", ""),
        })

    patterns.sort(key=lambda p: p["count"], reverse=True)
    return patterns


# ── Tighten Tracking ─────────────────────────────────────────────────────

def log_tighten(
    profile_path: Path,
    suggestion: dict,
    accepted: bool,
) -> None:
    """Log a type tightening decision."""
    append_entry(profile_path, "tighten", {
        "function": suggestion.get("function", ""),
        "location": suggestion.get("location", ""),
        "current": suggestion.get("current", ""),
        "suggested": suggestion.get("suggested", ""),
        "accepted": accepted,
    })


def get_tighten_preferences(profile_path: Path) -> dict[str, bool]:
    """Get patterns of accepted/rejected type suggestions."""
    entries = read_profile(profile_path)
    tightens = [e for e in entries if e.get("type") == "tighten"]

    if not tightens:
        return {}

    transitions: dict[str, list[bool]] = {}
    for t in tightens:
        key = f"{t.get('current', '?')}->{t.get('suggested', '?')}"
        transitions.setdefault(key, []).append(t.get("accepted", False))

    prefs = {}
    for key, decisions in transitions.items():
        accepts = sum(1 for d in decisions if d)
        prefs[key] = accepts > len(decisions) / 2

    return prefs


# ── Tombstoning ───────────────────────────────────────────────────────────

def tombstone(profile_path: Path, pattern: str, context: str = "") -> None:
    """Tombstone a pattern — it won't be suggested again for this context."""
    append_entry(profile_path, "tombstone", {
        "pattern": pattern,
        "context": context,
    })
    log.info("Tombstoned pattern: %s (context: %s)", pattern, context or "global")


def is_tombstoned(profile_path: Path, pattern: str, context: str = "") -> bool:
    """Check if a pattern has been tombstoned."""
    entries = read_profile(profile_path)
    for e in entries:
        if e.get("type") == "tombstone" and e.get("pattern") == pattern:
            e_ctx = e.get("context", "")
            if not e_ctx or e_ctx == context:
                return True
    return False


def get_tombstones(profile_path: Path) -> list[dict]:
    """Get all tombstoned patterns."""
    entries = read_profile(profile_path)
    return [e for e in entries if e.get("type") == "tombstone"]


def forget(profile_path: Path, pattern: str) -> bool:
    """Remove a learned pattern and tombstone it.

    Returns True if the pattern was found and tombstoned.
    """
    entries = read_profile(profile_path)
    found = False

    # Filter out matching entries
    filtered = []
    for e in entries:
        if e.get("pattern") == pattern or e.get("error_type") == pattern:
            found = True
            continue  # remove it
        filtered.append(e)

    if found:
        # Rewrite profile without the pattern
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = profile_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for e in filtered:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp.replace(profile_path)

        # Tombstone it
        tombstone(profile_path, pattern)

    return found


def reset_profile(profile_path: Path) -> None:
    """Reset the entire profile. Fresh start."""
    if profile_path.exists():
        profile_path.unlink()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("", encoding="utf-8")
    log.info("Profile reset: %s", profile_path)


# ── Anti-Pattern Detection ────────────────────────────────────────────────

@dataclass
class AntiPattern:
    """A detected anti-pattern with escalation level."""
    pattern: str
    count: int
    level: str  # hint, warning, auto_fix
    message: str


def detect_anti_patterns(profile_path: Path) -> list[AntiPattern]:
    """Detect repeated anti-patterns from profile history.

    Escalation levels:
    - hint: seen 2-3 times
    - warning: seen 4-6 times
    - auto_fix: seen 7+ times
    """
    error_patterns = get_error_patterns(profile_path)
    anti_patterns = []

    for ep in error_patterns:
        count = ep["count"]
        et = ep["error_type"]

        # Skip tombstoned patterns
        if is_tombstoned(profile_path, et):
            continue

        if count >= 7:
            level = "auto_fix"
            msg = f"You've hit '{et}' {count} times. Auto-fixing from now on."
        elif count >= 4:
            level = "warning"
            msg = f"'{et}' keeps coming back ({count} times). Consider addressing the root cause."
        elif count >= 2:
            level = "hint"
            msg = f"You've seen '{et}' {count} times before."
        else:
            continue

        anti_patterns.append(AntiPattern(
            pattern=et,
            count=count,
            level=level,
            message=msg,
        ))

    return anti_patterns


# ── Profile Summary ───────────────────────────────────────────────────────

def get_summary(profile_path: Path) -> dict:
    """Get a comprehensive profile summary."""
    entries = read_profile(profile_path)
    if not entries:
        return {"total_entries": 0}

    type_counts = Counter(e.get("type", "?") for e in entries)
    error_patterns = get_error_patterns(profile_path)
    style_summary = get_style_summary(profile_path)
    tombstones = get_tombstones(profile_path)
    anti_patterns = detect_anti_patterns(profile_path)

    return {
        "total_entries": len(entries),
        "by_type": dict(type_counts),
        "top_errors": error_patterns[:5],
        "style": {cat: patterns[:3] for cat, patterns in style_summary.items()},
        "tombstones": [t.get("pattern", "?") for t in tombstones],
        "anti_patterns": [{"pattern": ap.pattern, "level": ap.level, "count": ap.count} for ap in anti_patterns],
        "first_entry": entries[0].get("ts", ""),
        "last_entry": entries[-1].get("ts", ""),
    }


def format_profile(profile_path: Path) -> str:
    """Format profile for display."""
    summary = get_summary(profile_path)
    if summary.get("total_entries", 0) == 0:
        return "Profile is empty. It builds up as you use Nexus."

    lines = ["\033[36m── developer profile ──────────────────────────\033[0m"]
    lines.append(f"  Entries: {summary['total_entries']}")
    lines.append(f"  Active since: {summary.get('first_entry', '?')[:10]}")

    by_type = summary.get("by_type", {})
    if by_type:
        lines.append(f"  Intents: {by_type.get('intent', 0)} | "
                      f"Errors: {by_type.get('error', 0)} | "
                      f"Style: {by_type.get('style', 0)} | "
                      f"Tighten: {by_type.get('tighten', 0)}")

    style = summary.get("style", {})
    if style:
        lines.append("\n  \033[33mLearned style:\033[0m")
        for cat, patterns in style.items():
            if patterns:
                items = ", ".join(f"{p} ({c:.0%})" for p, c in patterns)
                lines.append(f"    {cat}: {items}")

    anti = summary.get("anti_patterns", [])
    if anti:
        lines.append("\n  \033[31mAnti-patterns:\033[0m")
        for ap in anti:
            icon = {"hint": "~", "warning": "!", "auto_fix": "!!"}[ap["level"]]
            lines.append(f"    [{icon}] {ap['pattern']} ({ap['count']}x)")

    tombs = summary.get("tombstones", [])
    if tombs:
        lines.append(f"\n  \033[90mForgotten: {', '.join(tombs)}\033[0m")

    lines.append("\033[36m───────────────────────────────────────────────\033[0m")
    return "\n".join(lines)
