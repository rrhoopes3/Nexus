"""
Nexus compiler — wraps rustc/cargo for compilation and execution.

Phase 1 additions: watch mode with auto-recompile.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("nexus.compiler")


@dataclass
class CompileResult:
    success: bool
    output: str  # stdout from the program (if run)
    errors: str  # stderr / compiler errors
    binary: Path | None  # path to compiled binary


def check_rust_toolchain() -> bool:
    """Check if rustc is available."""
    return shutil.which("rustc") is not None


def compile_rust(
    source: str,
    output_dir: Path | None = None,
    binary_name: str = "nexus_out",
) -> CompileResult:
    """Compile Rust source code to a binary.

    Args:
        source: Rust source code string.
        output_dir: Directory for the binary. Uses temp dir if None.
        binary_name: Name of the output binary.

    Returns:
        CompileResult with success status, errors, and binary path.
    """
    if not check_rust_toolchain():
        return CompileResult(
            success=False,
            output="",
            errors="Rust toolchain not found. Install from https://rustup.rs",
            binary=None,
        )

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="nexus_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    src_file = output_dir / "main.rs"
    src_file.write_text(source, encoding="utf-8")

    binary = output_dir / binary_name
    if sys.platform == "win32":
        binary = binary.with_suffix(".exe")

    try:
        result = subprocess.run(
            ["rustc", str(src_file), "-o", str(binary)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return CompileResult(
            success=False, output="", errors="Compilation timed out (60s)", binary=None
        )

    if result.returncode != 0:
        return CompileResult(
            success=False, output="", errors=result.stderr, binary=None
        )

    return CompileResult(success=True, output="", errors=result.stderr, binary=binary)


def run_binary(binary: Path, timeout: int = 30) -> CompileResult:
    """Run a compiled binary and capture output."""
    try:
        result = subprocess.run(
            [str(binary)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CompileResult(
            success=False, output="", errors=f"Execution timed out ({timeout}s)", binary=binary
        )

    return CompileResult(
        success=result.returncode == 0,
        output=result.stdout,
        errors=result.stderr,
        binary=binary,
    )


def compile_and_run(
    source: str,
    output_dir: Path | None = None,
    timeout: int = 30,
) -> CompileResult:
    """Compile Rust source and run the resulting binary."""
    comp = compile_rust(source, output_dir)
    if not comp.success:
        return comp

    run_result = run_binary(comp.binary, timeout=timeout)
    # Preserve any compiler warnings
    if comp.errors:
        run_result.errors = comp.errors + "\n" + run_result.errors
    return run_result


# ── Watch Mode ────────────────────────────────────────────────────────────

def _get_mtimes(paths: list[Path]) -> dict[Path, float]:
    """Get modification times for a list of files."""
    mtimes = {}
    for p in paths:
        try:
            mtimes[p] = os.stat(p).st_mtime
        except OSError:
            pass
    return mtimes


def watch(
    watch_dir: Path,
    on_change: Callable[[list[Path]], None],
    pattern: str = "*.nx",
    interval: float = 1.0,
) -> None:
    """Watch .nx files for changes and trigger recompilation.

    Args:
        watch_dir: Directory to watch (recursively).
        on_change: Callback receiving list of changed file paths.
        pattern: Glob pattern to watch.
        interval: Poll interval in seconds.

    Runs forever until KeyboardInterrupt.
    """
    print(f"\033[90mWatching {watch_dir} for changes (Ctrl+C to stop)...\033[0m")

    files = list(watch_dir.rglob(pattern))
    last_mtimes = _get_mtimes(files)

    try:
        while True:
            time.sleep(interval)
            files = list(watch_dir.rglob(pattern))
            current_mtimes = _get_mtimes(files)

            changed = []
            for path, mtime in current_mtimes.items():
                if path not in last_mtimes or last_mtimes[path] != mtime:
                    changed.append(path)

            # Detect new files
            for path in current_mtimes:
                if path not in last_mtimes:
                    changed.append(path)

            if changed:
                unique = list(set(changed))
                names = ", ".join(p.name for p in unique)
                print(f"\n\033[33mChanged: {names}\033[0m")
                try:
                    on_change(unique)
                except Exception as e:
                    print(f"\033[31mError: {e}\033[0m")
                last_mtimes = current_mtimes
    except KeyboardInterrupt:
        print("\n\033[90mWatch stopped.\033[0m")
