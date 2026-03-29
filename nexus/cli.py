"""
Nexus CLI — command implementations.

Phase 1: hybrid compilation, tighten, watch, history, error whisper.
Phase 3: style learning, predictive whisper, passive type pipeline, forget/reset.
"""
from __future__ import annotations

import json
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

from nexus.audit import log_transform, get_history, format_history
from nexus.compiler import check_rust_toolchain, compile_and_run, compile_rust, watch
from nexus.intent import (
    compile_intents, verify_code, fix_errors,
    tighten_types, apply_tighten, whisper_error,
)
from nexus.project import (
    create_project, load_project, collect_intents, collect_strict_blocks,
    collect_modules, save_generated, Project,
)
from nexus.profile import (
    log_intent, log_error, log_tighten, log_style, classify_error,
    get_error_patterns, format_profile, forget, reset_profile,
)
from nexus.style import analyze_rust, StyleObservation
from nexus.whisper import analyze_pre_compile, format_whispers
from nexus.tighten import TypeTracker, check_locked_violations

log = logging.getLogger("nexus.cli")

# ── Helpers ────────────────────────────────────────────────────────────────

def _print_rust(code: str) -> None:
    """Print Rust code with a header."""
    print("\n\033[36m── generated rust ─────────────────────────────\033[0m")
    print(code)
    print("\033[36m───────────────────────────────────────────────\033[0m\n")


def _print_output(result) -> None:
    """Print compilation/execution results."""
    if result.output:
        print(result.output, end="")
    if result.errors:
        print(f"\033[33m{result.errors}\033[0m", file=sys.stderr, end="")


def _log_history(project: Project, action: str, data: dict) -> None:
    """Append to project history."""
    record = {"ts": datetime.now(timezone.utc).isoformat(), "action": action, **data}
    with open(project.history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _compile_project(project: Project) -> tuple[str, str]:
    """Compile all .nx files in a project. Returns (rust_code, module_summary).

    Uses hybrid compilation when strict blocks are present.
    """
    modules = collect_modules(project)
    all_intents = []
    all_strict = []

    module_names = []
    for nx in modules:
        all_intents.extend(nx.intents)
        all_strict.extend(nx.strict_blocks)
        module_names.append(f"{nx.module_name} ({len(nx.intents)}i/{len(nx.strict_blocks)}s)")

    summary = ", ".join(module_names)

    if not all_intents and not all_strict:
        return "", summary

    rust_code = compile_intents(
        all_intents,
        strict_blocks=all_strict or None,
        profile_path=project.profile_path,
    )
    return rust_code, summary


# ── Commands ──────────────────────────────────────────────────────────────

def cmd_new(name: str, path: str | None = None) -> None:
    """Create a new Nexus project."""
    parent = Path(path) if path else None
    try:
        project = create_project(name, parent)
        print(f"Created {project.root}/")
        print(f"  src/main.nx    — your entry point")
        print(f"  .nexus/        — project metadata")
        print(f"\nNext: cd {name} && nexus run")
    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_run(auto_fix: bool = True, watch_mode: bool = False) -> None:
    """Compile and run the current project."""
    if not check_rust_toolchain():
        print("Error: Rust toolchain not found. Install from https://rustup.rs", file=sys.stderr)
        sys.exit(1)

    project = load_project()

    if watch_mode:
        def on_change(changed_files):
            _do_run(project, auto_fix)
        # Run once immediately, then watch
        _do_run(project, auto_fix)
        watch(project.src_dir, on_change)
    else:
        _do_run(project, auto_fix)


def _do_run(project: Project, auto_fix: bool = True) -> None:
    """Internal: compile and run once, with full Phase 3 pipeline."""
    rust_code, summary = _compile_project(project)

    if not rust_code:
        print("No intents or strict blocks found in src/*.nx files.", file=sys.stderr)
        return

    print(f"\033[90mCompiling: {summary}\033[0m")

    # Phase 3: Style analysis — learn from generated code
    try:
        observations = analyze_rust(rust_code)
        if observations:
            log_style(project.profile_path, [
                {"category": o.category, "pattern": o.pattern,
                 "evidence": o.evidence, "confidence": o.confidence}
                for o in observations
            ])
    except Exception as e:
        log.debug("Style analysis skipped: %s", e)

    # Phase 3: Pre-compile whisper — catch issues before rustc
    try:
        whispers = analyze_pre_compile(rust_code, project.profile_path)
        if whispers:
            print(format_whispers(whispers))
    except Exception as e:
        log.debug("Pre-compile whisper skipped: %s", e)

    # Phase 3: Check locked type violations
    try:
        types_path = project.nexus_dir / "types.jsonl"
        tracker = TypeTracker(types_path, project.profile_path)
        locked = tracker.get_locked()
        if locked:
            violations = check_locked_violations(rust_code, locked)
            if violations:
                print("\033[31m── locked type violations ─────────────────────\033[0m")
                for v in violations:
                    print(f"  {v}")
                print("\033[31m───────────────────────────────────────────────\033[0m\n")
    except Exception as e:
        log.debug("Type check skipped: %s", e)

    save_generated(project, rust_code)
    log_transform(project.audit_path, "compile", "project", after=rust_code)

    _print_rust(rust_code)

    result = compile_and_run(rust_code, output_dir=project.build_dir)

    if not result.success and result.errors:
        # Error whisper: friendly diagnostic
        error_type = classify_error(result.errors)
        log_error(project.profile_path, error_type, "project", error_text=result.errors)
        error_patterns = get_error_patterns(project.profile_path)

        print("\033[35m── nexus whisper ──────────────────────────────\033[0m")
        diagnosis = whisper_error(rust_code, result.errors, error_patterns)
        print(diagnosis)
        print("\033[35m───────────────────────────────────────────────\033[0m\n")

        if auto_fix:
            print("\033[33mAttempting auto-fix...\033[0m")
            before = rust_code
            fixed = fix_errors(rust_code, result.errors)
            save_generated(project, fixed)
            log_transform(
                project.audit_path, "fix", "project",
                before=before, after=fixed,
                metadata={"error_type": error_type},
            )
            _print_rust(fixed)
            result = compile_and_run(fixed, output_dir=project.build_dir)

            if not result.success:
                print("\033[31mAuto-fix failed.\033[0m", file=sys.stderr)
                _print_output(result)
            else:
                print("\033[32mAuto-fix succeeded.\033[0m")
                log_error(project.profile_path, error_type, "project", auto_fixed=True)
    else:
        _print_output(result)

    _log_history(project, "run", {"success": result.success})


def cmd_intent(text: str) -> None:
    """One-shot: compile a single intent and show the Rust code."""
    rust_code = compile_intents([text])
    _print_rust(rust_code)

    if check_rust_toolchain():
        result = compile_rust(rust_code)
        if result.success:
            print("\033[32mCompiles successfully.\033[0m")
        else:
            print(f"\033[33mCompiler warnings/errors:\033[0m\n{result.errors}")


def cmd_show() -> None:
    """Show the last generated Rust code."""
    project = load_project()
    rs = project.generated_rs
    if not rs.exists():
        print("No generated code yet. Run 'nexus run' first.", file=sys.stderr)
        sys.exit(1)
    _print_rust(rs.read_text(encoding="utf-8"))


def cmd_verify() -> None:
    """Explain what the generated code does."""
    project = load_project()
    rs = project.generated_rs
    if not rs.exists():
        print("No generated code yet. Run 'nexus run' first.", file=sys.stderr)
        sys.exit(1)

    code = rs.read_text(encoding="utf-8")
    print("\033[90mAnalyzing...\033[0m\n")
    explanation = verify_code(code)
    print(explanation)


def cmd_tighten() -> None:
    """Analyze generated code and suggest type improvements."""
    project = load_project()
    rs = project.generated_rs
    if not rs.exists():
        print("No generated code yet. Run 'nexus run' first.", file=sys.stderr)
        sys.exit(1)

    code = rs.read_text(encoding="utf-8")
    print("\033[90mAnalyzing types...\033[0m\n")
    suggestions = tighten_types(code)

    if not suggestions:
        print("\033[32mNo type improvements suggested. Code looks tight.\033[0m")
        return

    print(f"\033[36mFound {len(suggestions)} suggestion(s):\033[0m\n")

    accepted = []
    for i, s in enumerate(suggestions, 1):
        conf = s.get("confidence", 0)
        conf_color = "\033[32m" if conf >= 0.8 else "\033[33m" if conf >= 0.5 else "\033[31m"

        print(f"  {i}. [{s.get('function', '?')}] {s.get('location', '?')}")
        print(f"     {s.get('current', '?')} -> \033[36m{s.get('suggested', '?')}\033[0m")
        print(f"     {conf_color}Confidence: {conf:.0%}\033[0m — {s.get('reason', '')}")

        try:
            choice = input(f"     Accept? [Y/n/q] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            break

        if choice == "q":
            break
        elif choice in ("", "y", "yes"):
            accepted.append(s)
            log_tighten(project.profile_path, s, accepted=True)
            print("     \033[32mAccepted.\033[0m")
        else:
            log_tighten(project.profile_path, s, accepted=False)
            print("     \033[90mSkipped.\033[0m")
        print()

    if accepted:
        print(f"\n\033[90mApplying {len(accepted)} change(s)...\033[0m")
        before = code
        tightened = apply_tighten(code, accepted)
        save_generated(project, tightened)
        log_transform(
            project.audit_path, "tighten", "project",
            before=before, after=tightened,
            metadata={"accepted": len(accepted), "total": len(suggestions)},
        )
        _print_rust(tightened)
        print(f"\033[32mApplied {len(accepted)} type improvement(s).\033[0m")

        # Try to compile to validate
        if check_rust_toolchain():
            result = compile_rust(tightened, output_dir=project.build_dir)
            if result.success:
                print("\033[32mCompiles successfully with tightened types.\033[0m")
            else:
                print(f"\033[33mCompiler issues after tightening:\033[0m\n{result.errors}")
                print("Run 'nexus run' to auto-fix.")
    else:
        print("No changes applied.")


def cmd_history(limit: int = 20) -> None:
    """Show recent audit trail entries."""
    project = load_project()
    entries = get_history(project.audit_path, limit=limit)
    if not entries:
        print("No audit history yet.")
        return

    print("\033[36m── audit trail ────────────────────────────────\033[0m")
    print(format_history(entries))
    print(f"\033[90m({len(entries)} entries shown)\033[0m")


def cmd_profile() -> None:
    """Show developer profile summary with style, anti-patterns, and tombstones."""
    project = load_project()
    print(format_profile(project.profile_path))


def cmd_style() -> None:
    """Show learned coding style preferences."""
    project = load_project()
    rs = project.generated_rs

    if rs.exists():
        # Analyze current code too
        code = rs.read_text(encoding="utf-8")
        observations = analyze_rust(code)
        if observations:
            print("\033[36m── current code analysis ──────────────────────\033[0m")
            for o in observations:
                print(f"  [{o.category}] {o.pattern}: {o.evidence} ({o.confidence:.0%})")
            print()

    from nexus.profile import get_style_summary
    style = get_style_summary(project.profile_path)
    if style:
        print("\033[36m── learned style (with decay) ─────────────────\033[0m")
        for cat, patterns in style.items():
            if patterns:
                print(f"\n  {cat}:")
                for p, c in patterns:
                    filled = int(c * 10)
                    bar = "\033[32m" + "#" * filled + "\033[90m" + "-" * (10 - filled) + "\033[0m"
                    print(f"    {bar} {p} ({c:.0%})")
        print("\033[36m───────────────────────────────────────────────\033[0m")
    else:
        print("No style learned yet. Run 'nexus run' a few times.")


def cmd_types() -> None:
    """Show the type tracking pipeline status."""
    project = load_project()
    types_path = project.nexus_dir / "types.jsonl"
    tracker = TypeTracker(types_path, project.profile_path)
    print(tracker.format_status())


def cmd_forget(pattern: str) -> None:
    """Forget a learned pattern and tombstone it."""
    project = load_project()
    if forget(project.profile_path, pattern):
        print(f"Forgot and tombstoned: {pattern}")
    else:
        print(f"Pattern not found: {pattern}")


def cmd_reset_profile() -> None:
    """Reset the entire developer profile."""
    project = load_project()
    try:
        confirm = input("This will erase your entire profile. Are you sure? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return

    if confirm in ("y", "yes"):
        reset_profile(project.profile_path)
        print("Profile reset. Starting fresh.")
    else:
        print("Aborted.")


def cmd_repl() -> None:
    """Interactive REPL — build a program incrementally."""
    print("\033[36mNexus REPL — type intents, build incrementally.\033[0m")
    print("Commands: /run  /show  /verify  /tighten  /reset  /quit\n")

    context = ""
    history: list[str] = []

    while True:
        try:
            line = input("\033[32mnexus>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not line:
            continue

        # Commands
        if line.startswith("/"):
            cmd = line.lower().split()[0]
            if cmd in ("/quit", "/exit", "/q"):
                print("Bye.")
                break
            elif cmd == "/run":
                if not context:
                    print("Nothing to run yet. Type an intent first.")
                    continue
                if not check_rust_toolchain():
                    print("Rust toolchain not found.")
                    continue
                result = compile_and_run(context)
                _print_output(result)
                if not result.success:
                    print("\033[33mAttempting auto-fix...\033[0m")
                    fixed = fix_errors(context, result.errors)
                    context = fixed
                    _print_rust(context)
                    result = compile_and_run(context)
                    _print_output(result)
            elif cmd == "/show":
                if context:
                    _print_rust(context)
                else:
                    print("No code yet.")
            elif cmd == "/verify":
                if context:
                    print("\033[90mAnalyzing...\033[0m\n")
                    print(verify_code(context))
                else:
                    print("No code to verify.")
            elif cmd == "/tighten":
                if context:
                    print("\033[90mAnalyzing types...\033[0m\n")
                    suggestions = tighten_types(context)
                    if suggestions:
                        for s in suggestions:
                            print(f"  {s.get('function', '?')}.{s.get('location', '?')}: "
                                  f"{s.get('current', '?')} -> {s.get('suggested', '?')} "
                                  f"({s.get('confidence', 0):.0%})")
                        try:
                            choice = input("\nApply all? [y/N] ").strip().lower()
                        except (EOFError, KeyboardInterrupt):
                            print()
                            continue
                        if choice in ("y", "yes"):
                            context = apply_tighten(context, suggestions)
                            _print_rust(context)
                    else:
                        print("No improvements suggested.")
                else:
                    print("No code to tighten.")
            elif cmd == "/reset":
                context = ""
                history.clear()
                print("Reset. Starting fresh.")
            else:
                print(f"Unknown command: {cmd}")
            continue

        # Intent
        history.append(line)
        print("\033[90mCompiling...\033[0m")

        if context:
            context = compile_intents([line], context=context)
        else:
            context = compile_intents([line])

        _print_rust(context)

        # Try to log to profile if in a project
        try:
            project = load_project()
            log_intent(project.profile_path, line)
        except FileNotFoundError:
            pass
