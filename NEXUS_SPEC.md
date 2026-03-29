# Nexus Language Specification

**Version:** 0.1-draft
**Status:** Pre-concept / Design Phase
**Authors:** rrhoopes3, Claude

---

## 1. Philosophy

Nexus is an intent-driven programming language with an embedded AI core. The programmer describes *what* they want; Nexus decides *how* to implement it, learning the programmer's style, preferences, and performance priorities over time.

**Core principles:**

- **Intent over syntax** — natural language, voice, and visual input are first-class citizens alongside traditional code
- **Adaptive compilation** — code starts dynamic and progressively tightens as the AI observes patterns
- **Hybrid escape hatch** — strict mode is always one toggle away; the AI never locks you out of manual control
- **Zero ceremony** — no build configs, no boilerplate, no import management

---

## 2. Input Modes

### 2.1 Text Intent

The primary input mode. The programmer writes natural-language statements that describe behavior:

```nexus
loop over users, filter active, sort by last login, sum their karma
```

Nexus compiles this to an optimized internal representation. The programmer never sees the IR unless they ask.

**Ambiguity resolution:** When an intent is ambiguous, Nexus asks rather than guesses:

```
> "sort users"
< Sort by what? You've used `last_login` 4 times and `created_at` once.
  [1] last_login (desc)  [2] created_at  [3] specify
```

### 2.2 Voice Mode

Spoken commands are parsed through the same intent engine:

```
"hey nexus, debug why login crashes"
```

Nexus replays the call stack, identifies the failure point, and responds audibly with a fix suggestion. Voice mode supports conversational follow-ups:

```
"what about the token refresh?"
"try wrapping it in a retry with backoff"
"show me"
```

### 2.3 Visual Mode

Sketch a flowchart, state diagram, or UI wireframe on a tablet or canvas. Nexus interprets the topology and generates corresponding structures:

- Boxes become structs/classes
- Arrows become function calls or state transitions
- Loops drawn as cycles become iteration constructs
- Annotations on edges become conditions/guards

### 2.4 Strict Mode

Traditional code input for when the AI's interpretation isn't what you want:

```nexus
strict {
    fn calculate_karma(user: User) -> u64 {
        user.posts.iter()
            .filter(|p| p.score > 0)
            .map(|p| p.score as u64)
            .sum()
    }
}
```

Strict blocks compile directly — no AI rewriting, no inference. Syntax is Rust-adjacent but simplified (no lifetime annotations; the compiler infers them).

---

## 3. The AI Core

### 3.1 Style Learning

The AI core maintains a per-developer profile that tracks:

- **Naming conventions** — camelCase vs snake_case, abbreviation tolerance, domain vocabulary
- **Structural preferences** — early returns vs match blocks, composition vs inheritance, error handling style
- **Performance posture** — does this developer optimize for speed, readability, or memory? Nexus adjusts its output accordingly
- **Anti-patterns** — repeated mistakes are flagged progressively (gentle hint -> warning -> auto-fix with notification)

The profile is stored locally as a `.nexus/profile.jsonl` — one entry per observed pattern, with confidence scores and timestamps. The developer can inspect, edit, or reset it.

### 3.2 Progressive Type Tightening

Nexus starts fully dynamic. As code accumulates:

| Stage | Trigger | Behavior |
|-------|---------|----------|
| **Dynamic** | First use | All values are `any`. No type errors. |
| **Inferred** | ~10 functions | Nexus infers types from usage patterns and adds soft guards. |
| **Suggested** | Repeated patterns | "Every time you use `user.id`, it's a string that looks like a UUID. Lock it?" |
| **Locked** | Developer confirms | Type becomes enforced. Violations are compile errors. |

The `tighten` command accelerates this:

```
> nexus, tighten this module
< Found 14 inferred types. 11 are high-confidence. Locking those.
  3 ambiguous — showing candidates...
```

### 3.3 Error Whisper

Errors are surfaced before compilation as contextual suggestions:

```
< You've written this O(n^2) pattern 3 times in user_search.
  Each time you refactored it to a hash lookup within a day.
  Want me to skip ahead?
  [1] Yes, rewrite now  [2] No, I want it this way  [3] Show me the diff
```

If the developer selects [2], Nexus remembers and stops suggesting for that pattern.

### 3.4 Forgetting

The AI core has a decay mechanism:

- Patterns not reinforced in 30 days lose confidence
- Explicitly rejected suggestions are tombstoned (never suggested again for that context)
- `nexus forget <pattern>` removes a learned behavior immediately
- `nexus reset profile` starts fresh

---

## 4. Verification Layer

Every intent compilation produces a verifiable artifact before execution.

### 4.1 Dry Run

```
> show me what this actually does
```

Nexus produces:

1. **Pseudocode expansion** — the intent rewritten as explicit steps
2. **Side-effect map** — every I/O operation, mutation, and external call highlighted
3. **Data flow diagram** — auto-generated visual showing what goes in and comes out
4. **Sim trace** — runs against synthetic data and shows the output

### 4.2 Diff Mode

When Nexus rewrites code (performance optimization, type tightening, anti-pattern fix):

```
> nexus, make this fast

< Vectorized inner loop, replaced map+filter chain with single pass.
  Before: 340ms on 1M records
  After:  12ms on 1M records

  [diff]
  - users.filter(active).map(karma).sum()
  + users.par_iter().fold(0, |acc, u| if u.active { acc + u.karma } else { acc })

  [1] Accept  [2] Reject  [3] Benchmark both
```

### 4.3 Audit Trail

All AI-driven transformations are logged to `.nexus/audit.jsonl`:

```jsonl
{"ts":"2026-03-26T14:22:01Z","action":"rewrite","module":"user_search","reason":"O(n^2) detected","accepted":true,"before_hash":"a3f2...","after_hash":"c7b1..."}
```

The developer can replay, revert, or diff any transformation.

---

## 5. Trade-Off Engine

Nexus exposes explicit optimization axes that the developer can dial:

```
> nexus, optimize this module

< Current profile: balanced
  Choose priority:
  [1] Speed     — vectorize, unroll, cache aggressively
  [2] Readable  — split functions, add comments, simplify control flow
  [3] Memory    — pool allocations, compress in-place, stream instead of buffer
  [4] Safe      — add bounds checks, defensive clones, error wrapping
```

These can be set per-module, per-function, or globally:

```
> nexus set priority speed for api/handlers
> nexus set priority readable for lib/utils
```

The trade-off engine also runs passively:

```
< This DB call runs on every request (avg 45ms).
  You have a 5-minute cache on the same data in user_profile.
  Reuse it here?
  [1] Yes, wire up the cache  [2] No, needs fresh data  [3] Add a TTL option
```

---

## 6. Compilation Targets

Nexus compiles to multiple backends. The target can be set per module:

| Target | Use Case | Notes |
|--------|----------|-------|
| **Rust** | Performance-critical, systems-level | Default for modules marked `priority speed` |
| **Go** | Network services, concurrency-heavy | Default for HTTP handlers, queue consumers |
| **WASM** | Browser, edge, portable | Default for anything marked `client` or `edge` |
| **Python** | Scripts, prototyping, ML integration | Default for `priority readable` or data modules |

```
> nexus set target rust for core/engine
> nexus set target wasm for ui/components
```

Mixed targets in a single project are first-class. Nexus generates FFI bindings automatically at module boundaries.

### 6.1 IR (Internal Representation)

All input modes (text, voice, visual, strict) compile to a common IR before target codegen. The IR is:

- **SSA-based** (Static Single Assignment) for optimization passes
- **Serializable** — stored as `.nexus/ir/<module>.jsonl` for inspection and caching
- **Diffable** — intent changes produce minimal IR deltas

---

## 7. Runtime

### 7.1 Execution

```bash
nexus run                  # hot-reload, watches files
nexus run --release        # optimized build, no watcher
nexus run --target rust    # force specific backend
nexus test                 # run all tests (auto-discovered)
nexus bench                # run benchmarks with comparison
```

No build step. No config files. `nexus run` infers everything from the project structure and the developer's profile.

### 7.2 Hot Reload

File changes trigger incremental recompilation. For long-running services, Nexus patches the running process in-place when safe (pure function changes, config updates). Stateful changes trigger a graceful restart.

### 7.3 Dependencies

```
> nexus, I need a JSON parser
< Your project already uses serde (via Rust target).
  For the Go modules, I'll add encoding/json.
  For WASM, I'll use serde-wasm-bindgen.
  All consistent. Proceed?
```

No package.json, no Cargo.toml, no go.mod. Nexus manages dependencies per-target and resolves version conflicts automatically. Lockfile is `.nexus/deps.lock`.

---

## 8. Editor Integration

### 8.1 Native Editor

Nexus ships with a native editor (optional — LSP support covers VS Code, Neovim, etc.):

- Dark neon theme with syntax highlighting adapted to the developer's profile
- Holographic prompts: AI suggestions float as semi-transparent overlays above the current line
- Predictive completion: not just the next token, but the next *intent* ("you usually add error handling after DB calls — want it?")
- Inline profiler: per-line execution time displayed in the gutter during `nexus run`
- Side-effect gutter: icons showing which lines touch I/O, mutate state, or call external services

### 8.2 LSP Protocol

For existing editors, Nexus exposes a Language Server with extensions:

- Standard: completions, diagnostics, hover, go-to-definition
- Extended: intent-to-code expansion, voice command relay, trade-off suggestions, dry-run inline

---

## 9. Project Structure

```
my-project/
  .nexus/
    profile.jsonl          # developer style profile
    audit.jsonl            # transformation audit trail
    deps.lock              # dependency lockfile
    ir/                    # cached IR per module
  src/
    main.nx                # entry point
    users.nx               # module (intent + strict blocks)
    api/
      handlers.nx
      middleware.nx
  tests/                   # auto-discovered test files
    users_test.nx
```

File extension: `.nx`

---

## 10. Example Session

```
$ nexus new hotel-api
Created hotel-api/ with main.nx

$ cd hotel-api
$ nexus edit

> create a REST API with routes for guests, reservations, and rooms
> use postgres for storage
> add JWT auth on all routes except health check

< Generated 4 modules:
    api/guests.nx      — CRUD for guest records
    api/reservations.nx — booking lifecycle
    api/rooms.nx       — inventory + availability
    auth/jwt.nx        — middleware, token issue/verify
    db/schema.nx       — postgres migrations

  Target: Go (detected: network service pattern)
  Show dry run? [y/n]

> y

< [dry run output with route table, middleware chain, DB schema]

> looks good, but make the reservation module priority speed

< Switching api/reservations.nx target to Rust.
  FFI bridge to Go HTTP layer auto-generated.
  Benchmark estimate: 3x throughput on availability queries.

> nexus run

< Listening on :8080
  Hot reload active. 5 modules compiled (4 Go, 1 Rust).
```

---

## 11. Open Questions

These are unresolved design decisions for future spec revisions:

1. **Profile portability** — should `.nexus/profile.jsonl` be shareable across teams, or strictly per-developer?
2. **AI model** — should the AI core be local-only (privacy), cloud-assisted (capability), or hybrid (local fast path, cloud fallback)?
3. **Determinism** — how do we ensure the same intent produces the same output across machines? Pin the AI model version? Snapshot the IR?
4. **Debugging mixed targets** — when Rust calls Go calls WASM, what does the unified debugger look like?
5. **Licensing** — if Nexus generates code from intent, who owns the output? The developer, always?
6. **Offline mode** — full functionality without network, or degraded (no cloud AI, no dependency resolution)?
7. **Team collaboration** — how do multiple developers' profiles interact on a shared codebase? Merge? Per-file ownership?
8. **Security** — intent injection? Malicious voice commands? Adversarial sketches?

---

## 12. Implementation Roadmap (Conceptual)

| Phase | Milestone | Core Deliverable |
|-------|-----------|------------------|
| **0** | Proof of concept | Text intent -> single-target (Rust) codegen via LLM |
| **1** | Strict mode + hybrid | `.nx` files with mixed intent/strict blocks, basic type inference |
| **2** | Intent-to-API | `nexus serve` — live API from intents, runtime modification via `/_nexus/intent` |
| **3** | AI core v1 | Style learning, progressive type tightening, error whisper |
| **4** | Voice + visual | Speech-to-intent, sketch-to-struct |
| **5** | Editor | Native editor with holographic prompts, inline profiler |
| **6** | Trade-off engine | Optimization axes, bottleneck detection, caching suggestions |
| **7** | Production runtime | Hot reload, dependency management, audit trail |

---

*"Code like you're talking to a friend who knows Rust but hates semicolons."*
