"""
Nexus type tightening engine — progressive type pipeline.

Stage 1: Dynamic    — all types inferred, no guards
Stage 2: Inferred   — types detected from usage, soft warnings
Stage 3: Suggested  — repeated patterns surfaced to developer
Stage 4: Locked     — confirmed types enforced as compile errors

The pipeline runs passively after each successful compilation,
tracking type observations in the profile.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from nexus.profile import (
    append_entry, read_profile, is_tombstoned,
    get_tighten_preferences, _days_ago,
)

log = logging.getLogger("nexus.tighten")


@dataclass
class TypeState:
    """Tracked state of a type observation."""
    function: str
    location: str
    current_type: str
    suggested_type: str
    stage: str  # dynamic, inferred, suggested, locked
    observations: int  # how many times we've seen this pattern
    confidence: float
    first_seen: str
    last_seen: str


STAGE_THRESHOLDS = {
    "dynamic": 0,       # initial
    "inferred": 3,      # seen 3+ times
    "suggested": 6,     # seen 6+ times, ready to surface
    "locked": -1,       # only via explicit developer confirmation
}


class TypeTracker:
    """Tracks type observations across compilations."""

    def __init__(self, types_path: Path, profile_path: Path):
        self.types_path = types_path
        self.profile_path = profile_path
        self._states: dict[str, TypeState] = {}
        self._load()

    def _key(self, function: str, location: str) -> str:
        return f"{function}::{location}"

    def _load(self) -> None:
        """Load type states from JSONL."""
        if not self.types_path.exists():
            return
        with open(self.types_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                key = self._key(data["function"], data["location"])
                self._states[key] = TypeState(**data)

    def _save(self) -> None:
        """Save type states to JSONL."""
        self.types_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.types_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for state in self._states.values():
                f.write(json.dumps(asdict(state), ensure_ascii=False) + "\n")
        tmp.replace(self.types_path)

    def observe(self, suggestions: list[dict]) -> list[TypeState]:
        """Process type suggestions from the tighten engine.

        Updates observation counts and stages. Returns states that
        have advanced to a new stage.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        advanced = []

        for s in suggestions:
            function = s.get("function", "?")
            location = s.get("location", "?")
            key = self._key(function, location)

            # Skip tombstoned suggestions
            tombstone_key = f"{s.get('current', '')}->{s.get('suggested', '')}"
            if is_tombstoned(self.profile_path, tombstone_key):
                continue

            if key in self._states:
                state = self._states[key]
                state.observations += 1
                state.last_seen = now
                state.confidence = max(
                    state.confidence,
                    s.get("confidence", 0.5),
                )

                # Check for stage advancement
                old_stage = state.stage
                if state.stage == "dynamic" and state.observations >= STAGE_THRESHOLDS["inferred"]:
                    state.stage = "inferred"
                elif state.stage == "inferred" and state.observations >= STAGE_THRESHOLDS["suggested"]:
                    state.stage = "suggested"
                # locked only via lock() method

                if state.stage != old_stage:
                    advanced.append(state)
                    log.info("Type advanced: %s %s -> %s", key, old_stage, state.stage)
            else:
                state = TypeState(
                    function=function,
                    location=location,
                    current_type=s.get("current", "?"),
                    suggested_type=s.get("suggested", "?"),
                    stage="dynamic",
                    observations=1,
                    confidence=s.get("confidence", 0.5),
                    first_seen=now,
                    last_seen=now,
                )
                self._states[key] = state

        self._save()
        return advanced

    def lock(self, function: str, location: str) -> bool:
        """Lock a type — developer confirmed, becomes enforced."""
        key = self._key(function, location)
        if key not in self._states:
            return False
        self._states[key].stage = "locked"
        self._save()
        log.info("Type locked: %s", key)
        return True

    def get_suggested(self) -> list[TypeState]:
        """Get all types at 'suggested' stage — ready for developer review."""
        return [s for s in self._states.values() if s.stage == "suggested"]

    def get_locked(self) -> list[TypeState]:
        """Get all locked types — these are enforced."""
        return [s for s in self._states.values() if s.stage == "locked"]

    def get_all(self) -> list[TypeState]:
        """Get all tracked types."""
        return list(self._states.values())

    def format_status(self) -> str:
        """Format type tracking status for display."""
        states = self.get_all()
        if not states:
            return "No types tracked yet."

        by_stage = {"dynamic": [], "inferred": [], "suggested": [], "locked": []}
        for s in states:
            by_stage.get(s.stage, by_stage["dynamic"]).append(s)

        lines = ["\033[36m── type pipeline ──────────────────────────────\033[0m"]

        stage_colors = {
            "dynamic": "\033[90m",
            "inferred": "\033[33m",
            "suggested": "\033[35m",
            "locked": "\033[32m",
        }

        for stage in ("locked", "suggested", "inferred", "dynamic"):
            items = by_stage[stage]
            if items:
                color = stage_colors[stage]
                lines.append(f"\n  {color}{stage.upper()} ({len(items)}):\033[0m")
                for s in items:
                    lines.append(
                        f"    {s.function}.{s.location}: "
                        f"{s.current_type} -> {s.suggested_type} "
                        f"({s.observations}x, {s.confidence:.0%})"
                    )

        lines.append("\033[36m───────────────────────────────────────────────\033[0m")
        return "\n".join(lines)


def check_locked_violations(rust_code: str, locked_types: list[TypeState]) -> list[str]:
    """Check if generated code violates any locked type constraints.

    Returns list of violation messages.
    """
    import re
    violations = []

    for lt in locked_types:
        # Look for the function and check if the type is wrong
        # This is a heuristic — Phase 4+ would use proper AST parsing
        fn_pattern = rf"fn\s+{re.escape(lt.function)}\s*\("
        fn_match = re.search(fn_pattern, rust_code)
        if not fn_match:
            continue

        # Check if the old type appears where the new type should be
        if lt.current_type in rust_code and lt.suggested_type not in rust_code:
            violations.append(
                f"LOCKED TYPE VIOLATION: {lt.function}.{lt.location} "
                f"should be `{lt.suggested_type}` but found `{lt.current_type}`"
            )

    return violations
