"""
Nexus style analyzer — detects coding patterns from generated Rust code.

Feeds observations into the developer profile so future LLM prompts
can match the developer's voice.
"""
from __future__ import annotations

import json
import re
import logging
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Any

log = logging.getLogger("nexus.style")


@dataclass
class StyleObservation:
    """A single observed coding pattern."""
    category: str       # naming, error_handling, structure, performance, etc.
    pattern: str        # specific pattern name
    evidence: str       # short example or description
    confidence: float   # 0.0 - 1.0


@dataclass
class StyleProfile:
    """Aggregated style preferences for a developer."""
    naming: dict[str, float] = field(default_factory=dict)       # pattern -> confidence
    error_handling: dict[str, float] = field(default_factory=dict)
    structure: dict[str, float] = field(default_factory=dict)
    performance: dict[str, float] = field(default_factory=dict)
    preferences: dict[str, float] = field(default_factory=dict)  # misc

    def dominant(self, category: str) -> list[tuple[str, float]]:
        """Get patterns sorted by confidence for a category."""
        data = getattr(self, category, {})
        return sorted(data.items(), key=lambda x: x[1], reverse=True)

    def to_prompt_fragment(self) -> str:
        """Convert profile to a prompt fragment for LLM injection."""
        lines = []
        for cat in ("naming", "error_handling", "structure", "performance", "preferences"):
            top = self.dominant(cat)
            if top:
                strong = [(p, c) for p, c in top if c >= 0.6]
                if strong:
                    items = ", ".join(f"{p} ({c:.0%})" for p, c in strong[:3])
                    lines.append(f"  {cat}: {items}")

        if not lines:
            return ""
        return "Developer style preferences:\n" + "\n".join(lines)


# ── Rust Code Analysis ────────────────────────────────────────────────────

def analyze_rust(code: str) -> list[StyleObservation]:
    """Analyze Rust source code for style patterns."""
    observations = []

    # ── Naming Conventions ────────────────────────────────────────────
    fn_names = re.findall(r"fn\s+([a-zA-Z_]\w*)", code)
    if fn_names:
        snake = sum(1 for n in fn_names if re.match(r"^[a-z][a-z0-9_]*$", n))
        camel = sum(1 for n in fn_names if re.match(r"^[a-z][a-zA-Z0-9]*$", n) and "_" not in n and any(c.isupper() for c in n))
        total = len(fn_names)
        if snake > camel:
            observations.append(StyleObservation(
                "naming", "snake_case_functions",
                f"{snake}/{total} functions use snake_case",
                min(snake / total, 1.0),
            ))
        elif camel > snake:
            observations.append(StyleObservation(
                "naming", "camel_case_functions",
                f"{camel}/{total} functions use camelCase",
                min(camel / total, 1.0),
            ))

    # Variable name length
    var_names = re.findall(r"let\s+(?:mut\s+)?([a-zA-Z_]\w*)", code)
    if var_names:
        avg_len = sum(len(n) for n in var_names) / len(var_names)
        if avg_len > 8:
            observations.append(StyleObservation(
                "naming", "descriptive_names",
                f"avg variable name length: {avg_len:.1f} chars",
                min(avg_len / 15, 1.0),
            ))
        elif avg_len < 4:
            observations.append(StyleObservation(
                "naming", "short_names",
                f"avg variable name length: {avg_len:.1f} chars",
                min(4 / max(avg_len, 1), 1.0),
            ))

    # ── Error Handling ────────────────────────────────────────────────
    unwraps = len(re.findall(r"\.unwrap\(\)", code))
    expects = len(re.findall(r"\.expect\(", code))
    question_marks = len(re.findall(r"\?;", code)) + len(re.findall(r"\?\s*$", code, re.MULTILINE))
    match_results = len(re.findall(r"match\s+\w+\s*\{[^}]*Ok\(", code, re.DOTALL))

    total_err = unwraps + expects + question_marks + match_results
    if total_err > 0:
        if question_marks > total_err * 0.5:
            observations.append(StyleObservation(
                "error_handling", "question_mark_operator",
                f"{question_marks}/{total_err} error sites use ?",
                min(question_marks / total_err, 1.0),
            ))
        if unwraps > total_err * 0.3:
            observations.append(StyleObservation(
                "error_handling", "unwrap_heavy",
                f"{unwraps}/{total_err} error sites use .unwrap()",
                min(unwraps / total_err, 1.0),
            ))
        if match_results > total_err * 0.3:
            observations.append(StyleObservation(
                "error_handling", "match_on_result",
                f"{match_results}/{total_err} error sites use match",
                min(match_results / total_err, 1.0),
            ))

    # ── Structural Preferences ────────────────────────────────────────
    early_returns = len(re.findall(r"if\s+.*\{\s*return\s+", code))
    if early_returns >= 2:
        observations.append(StyleObservation(
            "structure", "early_return",
            f"{early_returns} early return patterns",
            min(early_returns / 5, 1.0),
        ))

    # Match expressions vs if-else chains
    match_count = len(re.findall(r"\bmatch\b", code))
    if_else_count = len(re.findall(r"\belse\s+if\b", code))
    if match_count > if_else_count and match_count >= 2:
        observations.append(StyleObservation(
            "structure", "prefers_match",
            f"{match_count} match vs {if_else_count} else-if",
            min(match_count / (match_count + if_else_count + 1), 1.0),
        ))
    elif if_else_count > match_count and if_else_count >= 2:
        observations.append(StyleObservation(
            "structure", "prefers_if_else",
            f"{if_else_count} else-if vs {match_count} match",
            min(if_else_count / (match_count + if_else_count + 1), 1.0),
        ))

    # Iterators vs loops
    iter_count = len(re.findall(r"\.(iter|into_iter|map|filter|fold|collect)\(", code))
    for_count = len(re.findall(r"\bfor\s+\w+\s+in\b", code))
    if iter_count > for_count and iter_count >= 2:
        observations.append(StyleObservation(
            "structure", "iterator_chains",
            f"{iter_count} iterator methods vs {for_count} for-loops",
            min(iter_count / (iter_count + for_count + 1), 1.0),
        ))
    elif for_count > iter_count and for_count >= 2:
        observations.append(StyleObservation(
            "structure", "for_loops",
            f"{for_count} for-loops vs {iter_count} iterator methods",
            min(for_count / (iter_count + for_count + 1), 1.0),
        ))

    # ── Performance Patterns ──────────────────────────────────────────
    clones = len(re.findall(r"\.clone\(\)", code))
    borrows = len(re.findall(r"&\w+", code)) + len(re.findall(r"&mut\s+\w+", code))
    if clones > 3:
        observations.append(StyleObservation(
            "performance", "clone_heavy",
            f"{clones} .clone() calls",
            min(clones / 10, 1.0),
        ))
    if borrows > clones * 2 and borrows >= 4:
        observations.append(StyleObservation(
            "performance", "borrow_oriented",
            f"{borrows} borrows vs {clones} clones",
            min(borrows / (borrows + clones + 1), 1.0),
        ))

    # Collect vs pre-allocated
    collects = len(re.findall(r"\.collect\(\)", code))
    with_capacity = len(re.findall(r"with_capacity\(", code))
    if with_capacity >= 1:
        observations.append(StyleObservation(
            "performance", "pre_allocates",
            f"{with_capacity} pre-allocations",
            min(with_capacity / (collects + 1), 1.0),
        ))

    # ── General Preferences ───────────────────────────────────────────
    # Comments density
    comment_lines = len(re.findall(r"^\s*//", code, re.MULTILINE))
    total_lines = code.count("\n") + 1
    if total_lines > 10:
        ratio = comment_lines / total_lines
        if ratio > 0.15:
            observations.append(StyleObservation(
                "preferences", "heavy_comments",
                f"{comment_lines}/{total_lines} lines are comments ({ratio:.0%})",
                min(ratio * 3, 1.0),
            ))
        elif ratio < 0.03 and total_lines > 20:
            observations.append(StyleObservation(
                "preferences", "minimal_comments",
                f"only {comment_lines} comments in {total_lines} lines",
                min(1.0 - ratio * 10, 1.0),
            ))

    # Type annotations on let bindings
    typed_lets = len(re.findall(r"let\s+(?:mut\s+)?\w+\s*:", code))
    untyped_lets = len(re.findall(r"let\s+(?:mut\s+)?\w+\s*=", code)) - typed_lets
    if typed_lets > untyped_lets and typed_lets >= 3:
        observations.append(StyleObservation(
            "preferences", "explicit_types",
            f"{typed_lets} typed vs {untyped_lets} inferred let bindings",
            min(typed_lets / (typed_lets + untyped_lets + 1), 1.0),
        ))

    return observations


def build_style_profile(observations_history: list[list[StyleObservation]]) -> StyleProfile:
    """Build an aggregated style profile from multiple observation sets.

    Uses exponential recency weighting — newer observations count more.
    """
    profile = StyleProfile()
    n = len(observations_history)

    for i, obs_set in enumerate(observations_history):
        # Recency weight: most recent = 1.0, oldest = 0.3
        recency = 0.3 + 0.7 * (i / max(n - 1, 1))

        for obs in obs_set:
            weighted_conf = obs.confidence * recency
            target = getattr(profile, obs.category, None)
            if target is None:
                target = profile.preferences

            # Running average with recency bias
            if obs.pattern in target:
                target[obs.pattern] = (target[obs.pattern] + weighted_conf) / 2
            else:
                target[obs.pattern] = weighted_conf

    return profile
