"""
Nexus audit trail — logs every AI-driven transformation to JSONL.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("nexus.audit")


def _content_hash(text: str) -> str:
    """Short hash of content for diffing."""
    return hashlib.md5(text.encode()).hexdigest()[:12]


def log_transform(
    audit_path: Path,
    action: str,
    module: str = "",
    before: str = "",
    after: str = "",
    accepted: bool = True,
    metadata: dict | None = None,
) -> None:
    """Log an AI transformation to the audit trail.

    Args:
        audit_path: Path to .nexus/audit.jsonl
        action: Type of transform (compile, fix, tighten, whisper)
        module: Source module name (e.g., main.nx)
        before: Code before transformation (or empty for first compile)
        after: Code after transformation
        accepted: Whether the developer accepted the change
        metadata: Optional extra data (suggestions, error text, etc.)
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "module": module,
        "before_hash": _content_hash(before) if before else "",
        "after_hash": _content_hash(after) if after else "",
        "accepted": accepted,
        "lines_before": before.count("\n") + 1 if before else 0,
        "lines_after": after.count("\n") + 1 if after else 0,
    }
    if metadata:
        record["meta"] = metadata

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    log.debug("Audit: %s on %s (accepted=%s)", action, module, accepted)


def get_history(
    audit_path: Path,
    module: str | None = None,
    action: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Read recent audit entries, newest first.

    Args:
        audit_path: Path to .nexus/audit.jsonl
        module: Filter by module name
        action: Filter by action type
        limit: Max entries to return
    """
    if not audit_path.exists():
        return []

    entries = []
    with open(audit_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if module and entry.get("module") != module:
                continue
            if action and entry.get("action") != action:
                continue
            entries.append(entry)

    # Return newest first, limited
    return entries[-limit:][::-1]


def format_history(entries: list[dict]) -> str:
    """Format audit entries for display."""
    if not entries:
        return "No audit history."

    lines = []
    for e in entries:
        ts = e["ts"][:19].replace("T", " ")
        action = e["action"].upper()
        module = e.get("module", "?")
        accepted = "ok" if e.get("accepted", True) else "REJECTED"
        before = e.get("lines_before", 0)
        after = e.get("lines_after", 0)
        delta = after - before
        delta_str = f"+{delta}" if delta >= 0 else str(delta)
        lines.append(f"  {ts}  {action:<10} {module:<20} {delta_str:>5} lines  [{accepted}]")

    return "\n".join(lines)
