"""
Nexus project management — scaffold, load, and manage .nx files.

Phase 1 additions: module directives, compilation ordering, multi-file support.
"""
from __future__ import annotations

import re
import logging
from pathlib import Path
from dataclasses import dataclass, field

log = logging.getLogger("nexus.project")

MAIN_NX_TEMPLATE = """\
# {name} — main entry point
# Write intent statements below. Each "intent:" line describes what the program should do.
# Nexus compiles your intents into Rust and runs them.

intent: print hello from {name}
"""


# Module names that compile before main (define types/shared code)
PRIORITY_MODULES = {"types", "lib", "models", "shared"}


@dataclass
class NxFile:
    """Parsed .nx file contents."""
    path: Path
    module_name: str = ""  # from "module:" directive
    intents: list[str] = field(default_factory=list)
    strict_blocks: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)


@dataclass
class Project:
    """A Nexus project."""
    root: Path
    name: str

    @property
    def nexus_dir(self) -> Path:
        return self.root / ".nexus"

    @property
    def src_dir(self) -> Path:
        return self.root / "src"

    @property
    def build_dir(self) -> Path:
        return self.nexus_dir / "build"

    @property
    def generated_rs(self) -> Path:
        return self.build_dir / "main.rs"

    @property
    def profile_path(self) -> Path:
        return self.nexus_dir / "profile.jsonl"

    @property
    def history_path(self) -> Path:
        return self.nexus_dir / "history.jsonl"

    @property
    def audit_path(self) -> Path:
        return self.nexus_dir / "audit.jsonl"


def create_project(name: str, parent: Path | None = None) -> Project:
    """Create a new Nexus project scaffold."""
    parent = parent or Path.cwd()
    root = parent / name
    if root.exists():
        raise FileExistsError(f"Directory already exists: {root}")

    root.mkdir(parents=True)
    (root / ".nexus").mkdir()
    (root / ".nexus" / "build").mkdir()
    (root / "src").mkdir()

    # Write main.nx
    main_nx = root / "src" / "main.nx"
    main_nx.write_text(MAIN_NX_TEMPLATE.format(name=name), encoding="utf-8")

    # Empty profile
    (root / ".nexus" / "profile.jsonl").write_text("", encoding="utf-8")

    log.info("Created project: %s", root)
    return Project(root=root, name=name)


def load_project(path: Path | None = None) -> Project:
    """Load an existing Nexus project from the given or current directory."""
    path = path or Path.cwd()

    # Walk up to find .nexus directory
    check = path
    while check != check.parent:
        if (check / ".nexus").is_dir():
            return Project(root=check, name=check.name)
        check = check.parent

    raise FileNotFoundError(
        f"No Nexus project found at {path} or any parent directory. "
        f"Run 'nexus new <name>' to create one."
    )


def parse_nx_file(path: Path) -> NxFile:
    """Parse a .nx file into intents, strict blocks, and module directives."""
    text = path.read_text(encoding="utf-8")
    nx = NxFile(path=path)

    # Extract strict blocks first (handle nested braces)
    strict_pattern = re.compile(r"strict\s*\{", re.DOTALL)
    pos = 0
    for m in strict_pattern.finditer(text):
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        if depth == 0:
            nx.strict_blocks.append(text[start:i - 1].strip())

    # Remove strict blocks from text for intent/directive parsing
    clean = re.sub(r"strict\s*\{", "\x00STRICT\x00", text)
    # Remove content between markers (simplified)
    simple_pattern = re.compile(r"strict\s*\{.*?\}", re.DOTALL)
    # Re-do with proper nesting removal
    text_no_strict = text
    for block in nx.strict_blocks:
        text_no_strict = text_no_strict.replace(block, "", 1)
    text_no_strict = re.sub(r"strict\s*\{\s*\}", "", text_no_strict)

    for line in text_no_strict.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            nx.comments.append(line[1:].strip())
        elif line.lower().startswith("module:"):
            nx.module_name = line[7:].strip()
        elif line.lower().startswith("intent:"):
            intent = line[7:].strip()
            if intent:
                nx.intents.append(intent)

    # Default module name from filename
    if not nx.module_name:
        nx.module_name = path.stem

    return nx


def _module_sort_key(nx: NxFile) -> tuple[int, str]:
    """Sort key for compilation order: priority modules first, then main last."""
    name = nx.module_name.lower()
    if name in PRIORITY_MODULES:
        return (0, name)
    elif name == "main":
        return (2, name)
    else:
        return (1, name)


def collect_modules(project: Project) -> list[NxFile]:
    """Collect and order all .nx files for compilation.

    Order: priority modules (types, lib, models) -> regular -> main
    """
    modules = []
    for nx_path in project.src_dir.rglob("*.nx"):
        nx = parse_nx_file(nx_path)
        modules.append(nx)

    modules.sort(key=_module_sort_key)
    return modules


def collect_intents(project: Project) -> list[str]:
    """Collect all intents from all .nx files in compilation order."""
    all_intents = []
    for nx in collect_modules(project):
        all_intents.extend(nx.intents)
    return all_intents


def collect_strict_blocks(project: Project) -> list[str]:
    """Collect all strict blocks from all .nx files in compilation order."""
    blocks = []
    for nx in collect_modules(project):
        blocks.extend(nx.strict_blocks)
    return blocks


def save_generated(project: Project, rust_code: str) -> Path:
    """Save generated Rust code to the build directory."""
    project.build_dir.mkdir(parents=True, exist_ok=True)
    out = project.generated_rs
    out.write_text(rust_code, encoding="utf-8")
    log.info("Generated: %s", out)
    return out
