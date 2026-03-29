# Nexus

An intent-driven programming language with an embedded AI core. Describe *what* you want; Nexus decides *how* to implement it — learning your style, preferences, and performance priorities over time.

```
$ nexus new my-project && cd my-project
$ nexus repl

nexus> create a function that filters active users and sums their karma
── generated rust ─────────────────────────────
fn sum_active_karma(users: &[User]) -> u64 {
    users.iter().filter(|u| u.active).map(|u| u.karma).sum()
}
───────────────────────────────────────────────

nexus> /tighten
  sum_active_karma.return: u64 -> u32 (85%)
  Apply? [y/N]
```

## What's here

- **`NEXUS_SPEC.md`** — full language specification (input modes, AI core, type tightening, trade-off engine, compilation targets)
- **`nexus/`** — prototype CLI in Python
  - Intent-to-Rust compilation via LLM
  - Style learning with JSONL profiles (`.nexus/profile.jsonl`)
  - Progressive type tightening with lock/unlock lifecycle
  - Error whisper — predictive diagnostics before and after compilation
  - Audit trail of all AI transformations
  - Interactive REPL, file watcher, project scaffolding
- **`test-p3/`** — example project

## CLI commands

```
nexus new <name>       Create a project
nexus run              Compile .nx files to Rust and execute
nexus run -w           Watch mode with hot recompile
nexus intent "..."     One-shot intent compilation
nexus repl             Interactive REPL
nexus tighten          Suggest and apply type improvements
nexus verify           Explain what generated code does
nexus show             Print last generated Rust
nexus profile          Show learned developer profile
nexus style            Show coding style preferences
nexus types            Type tracking pipeline status
nexus history          Audit trail
nexus forget <pat>     Tombstone a learned pattern
nexus serve            Live API from intents (Phase 2)
```

## Requirements

- Python 3.10+
- An OpenAI-compatible API key (set `OPENAI_API_KEY` or configure in `nexus/llm.py`)
- Rust toolchain (for compiling generated code) — install from [rustup.rs](https://rustup.rs)

## Status

Early prototype. The spec is ambitious, the scaffolding works, the intent→Rust pipeline runs. Lots of room to grow — see the [roadmap in the spec](NEXUS_SPEC.md#12-implementation-roadmap-conceptual).

## Origin

Designed collaboratively by a human, Claude, and Grok as an experiment in what a common-sense, QOL-first programming language could look like.
