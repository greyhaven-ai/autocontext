# Librarian & Archivist Agents

Literature-aware advisory and gating roles for the generation loop. Librarians are each bound to a single book and advise the team based on its principles. The archivist arbitrates when librarians disagree or escalate violations, spot-pulling original passages on demand.

## Motivation

Strategies evolve through the generation loop without reference to published research, design literature, or domain-specific best practices. The librarian/archivist system injects that reference layer: proactive advice grounded in specific texts, reactive gating when proposals contradict established principles, and on-demand consultation for any agent that needs guidance from the literature.

## Concepts

- **Book** — a markdown file (optionally with images) containing research, literature, or principles. Stored in a global library, activated per-scenario at run time.
- **Librarian** — an agent bound to one book. Reads the full book at ingestion time, produces a compressed internal reference, and uses that reference to advise and flag during generations.
- **Archivist** — a single conditional agent that arbitrates between librarians. Only runs when a librarian escalates a violation. Spot-pulls original passages from chunked chapter files rather than loading full books.
- **Library** — the global collection of ingested books under `knowledge/_library/`. Books are added once and activated per-scenario via the `--books` flag.

## Architecture

### Global Library Storage

```
knowledge/_library/
  books/
    <book-name>/
      book.md                    # Original full text (preserved)
      chapters/
        ch01-<slug>.md           # Chunked by heading structure
        ch02-<slug>.md
        ...
      images/                    # Only when user supplies --images
        fig1.png
        ...
      meta.json                  # Title, author, tags, token count, chapter count, ingestion date
      reference.md               # Librarian's internal reference (produced at ingestion)
```

### Per-Scenario Library State

```
knowledge/<scenario>/library/
  active_books.json              # Which books are active for this scenario
  librarian_notes/
    <book-name>/
      gen_N.md                   # Advisory + flags from generation N
      cumulative_notes.md        # Rolling summary across generations
  archivist/
    decisions/
      gen_N_decision.md          # Archivist rulings when triggered
    consultation_log.md          # consult_library queries + responses
```

### DAG Position

```
competitor -> translator --> analyst -----------> coach
                         |-> architect
                         |-> librarian_1 -|
                         |-> librarian_2 -|-> archivist (conditional) -> (gate stage)
                         |-> librarian_N -|
```

All librarians run in parallel with analyst and architect (depend on `translator`). The archivist is always present in the DAG (depends on all librarian nodes) but its handler returns a no-op `ArchivistOutput` with empty decisions when no librarian flags severity `"violation"`. Coach depends on analyst and archivist (archivist is always in the DAG, so this is a static edge).

Librarians and archivist execute during Stage 2 (agent generation) as DAG nodes. The archivist's decisions are then evaluated as a gate check at Stage 3b. This is a data-flow concern — the archivist runs once during Stage 2, and its output is consumed at Stage 3b.

## Book Ingestion

### Normalization

The ingestion pipeline parses heading structure and splits into chapter files.

Splitting boundaries (in priority order):
1. `#` (H1) — chapter boundary
2. `##` (H2) — section boundary within chapter
3. Natural break at next paragraph boundary if a section exceeds ~8k tokens

Atomic blocks (never split):
- Tables (header to last row)
- Code blocks (fenced)
- Math blocks (`$$` to `$$`, inline `$` preserved with surrounding paragraph)
- Blockquotes
- Lists (entire list including nested items)
- Image references kept with surrounding paragraph

Files under ~6k tokens skip splitting entirely.

Each chunk gets frontmatter:

```markdown
---
book: clean-architecture
chapter: 8
section: 2
title: "Component Coupling - The Stable Dependencies Principle"
token_count: 3847
---
```

### Image Handling

Images are opt-in. If the user supplies `--images <path>`, image files are copied to `knowledge/_library/books/<name>/images/` and references in chapter files are rewritten. Images are included as multimodal content during the ingestion LLM call. If `--images` is omitted, image markdown syntax stays as-is (alt text visible) but no image data is loaded.

### Ingestion LLM Call

For books that fit in a single context window (~under 150k tokens), a single call reads the full text and produces `reference.md`.

Most books (up to ~300 pages / ~100k tokens of markdown) fit in a single context window. Multi-pass ingestion for larger books is deferred to a follow-up — v1 requires books to fit in one pass. If a book exceeds the context window, `add-book` reports the token count and fails with a clear message.

The ingestion prompt asks for: core thesis, key principles (numbered), chapter-by-chapter notes, decision framework, and red lines (what the book considers genuinely harmful). The red lines section directly feeds the librarian's ability to flag violations.

### Validation

Post-ingestion checks:
- `reference.md` exists and is non-empty
- Token count of reference is under 25k
- All source chapters are mentioned in chapter notes
- If `--images` was used, image references in chunks resolve to actual files

If validation fails, `add-book` reports the specific failure, removes any partially-written files from `knowledge/_library/books/<name>/`, and exits with a non-zero status. The user can retry. No partial ingestion state is left behind.

## Agent Roles

### Librarian

One instance per active book. Follows the `CompetitorRunner`/`AnalystRunner` runner pattern.

**Context per generation:**
- Its book's `reference.md` (~10-20k tokens)
- Competitor's proposed strategy (from translator output)
- Current playbook + score trajectory
- Its own `cumulative_notes.md` from prior generations

**Output contract:**

```python
@dataclass(slots=True)
class LibrarianFlag:
    severity: str              # "concern" or "violation"
    description: str
    cited_section: str         # Chapter/section ID from the book
    recommendation: str

@dataclass(slots=True)
class LibrarianOutput:
    raw_markdown: str
    book_name: str
    advisory: str
    flags: list[LibrarianFlag]
    cited_sections: list[str]
    parse_success: bool = True
```

Librarians are instructed to only flag things that are genuinely harmful to the project's goals, not stylistic preferences.

**Output format (parsed by `parse_librarian_output`):**

```markdown
<!-- ADVISORY_START -->
Recommendations grounded in the book's principles...
<!-- ADVISORY_END -->

<!-- FLAGS_START -->
## Flag: [severity: concern|violation]
**Section:** ch03-s02-dependency-inversion
**Issue:** The strategy couples scoring directly to movement logic...
**Recommendation:** Invert the dependency so that...
<!-- FLAGS_END -->
```

**Three operating modes:**
1. Ingestion — reads full book, produces reference (one-time, at `/add-book`)
2. Proactive review — DAG role, reviews strategy each generation
3. Reactive consultation — answers `consult_library` tool calls from other agents

### Archivist

Single instance, runs conditionally (only when any librarian flags severity `"violation"`).

**Context when triggered:**
- All librarian outputs (advisory + flags)
- Competitor's proposed strategy
- Spot-pulled chapter text — original sections cited by flagging librarians, pulled from chunked chapter files

**Output contract:**

```python
@dataclass(slots=True)
class ArchivistDecision:
    flag_source: str           # Which librarian raised the flag
    book_name: str
    verdict: str               # "dismissed", "soft_flag", "hard_gate"
    reasoning: str
    cited_passage: str         # Original text supporting the decision

@dataclass(slots=True)
class ArchivistOutput:
    raw_markdown: str
    decisions: list[ArchivistDecision]
    synthesis: str             # Overall assessment across all librarian input
    parse_success: bool = True
```

**Output format (parsed by `parse_archivist_output`):**

```markdown
<!-- SYNTHESIS_START -->
Overall assessment across librarian inputs...
<!-- SYNTHESIS_END -->

<!-- DECISIONS_START -->
## Decision: [source: librarian_clean_arch] [verdict: soft_flag|hard_gate|dismissed]
**Book:** Clean Architecture
**Reasoning:** The cited principle applies here because...
**Passage:** "The original text from the book..."
<!-- DECISIONS_END -->
```

Verdicts:
- `dismissed` — librarian was too conservative, strategy is fine
- `soft_flag` — legitimate concern, injected as advisory into coach context
- `hard_gate` — genuine violation, triggers retry with archivist reasoning as competitor constraint

## Cross-Agent Consultation

### `consult_library` Tool

Available to all agents (competitor, analyst, coach, architect) via tool permissions.

```python
{
    "name": "consult_library",
    "description": "Ask the library for guidance from the literature.",
    "parameters": {
        "question": str,
        "book_name": str | None,   # Target a specific book, or None for archivist routing
    }
}
```

Routing:
- `book_name` provided -> that librarian answers from its `reference.md`
- `book_name` is None -> archivist identifies relevant book(s), spot-pulls sections, synthesizes

Consultation calls are synchronous LLM calls executed during the calling agent's turn. The `consult_library` handler makes a direct `provider.query()` call with the librarian's reference (or archivist context) and returns the result as a tool response. This is a lightweight single-turn call, not a full agent execution.

All agents see a library availability block in their system prompt listing active book titles with one-line descriptions. Calls are logged to `consultation_log.md`.

Cost control: `AUTOCONTEXT_LIBRARY_MAX_CONSULTS_PER_ROLE` caps queries per agent per generation (default 3).

## Prompt Integration

The existing `PromptBundle` dataclass is frozen with four fields (`competitor`, `analyst`, `coach`, `architect`). Librarian prompts are passed separately via a `LibraryPromptBundle`:

```python
@dataclass(frozen=True)
class LibraryPromptBundle:
    librarian_prompts: dict[str, str]   # book_name -> assembled prompt
    archivist_prompt: str
    library_context_block: str          # Injected into all agent prompts
```

`build_prompt_bundle()` gains an optional `active_books` parameter. When books are active, it:
1. Assembles per-librarian prompts (reference + strategy + playbook + cumulative notes)
2. Assembles the archivist prompt template (populated at execution time with librarian outputs)
3. Generates a `library_context_block` listing active books with descriptions, appended to all agent prompts
4. Returns both `PromptBundle` and `LibraryPromptBundle`

The `library_context_block` includes the `consult_library` tool description so all agents know it is available.

## Pipeline Integration

Library support uses the pipeline engine path (`_run_via_pipeline` in `orchestrator.py`). The legacy `ThreadPoolExecutor` codepath does not gain library support — this is acceptable because the pipeline engine is the forward path and the legacy codepath is retained only for backwards compatibility with non-DAG configurations.

`build_mts_dag()` gains an `active_books: list[str]` parameter. When books are provided, it dynamically adds one `RoleSpec` per book (e.g., `librarian_clean_arch`) depending on `translator`, plus an `archivist` node depending on all librarian nodes. Coach's dependency list is extended to include `archivist`.

The `build_role_handler()` function maps librarian role names to `LibrarianRunner.run()` and `archivist` to `ArchivistRunner.run()`. The archivist handler checks for violations in librarian outputs and returns a no-op if none exist.

For Agent SDK mode (`AUTOCONTEXT_AGENT_PROVIDER=agent_sdk`): `consult_library` is registered as a tool in the `per_role_tools` configuration passed to `claude_agent_sdk.query()`. The tool handler is a synchronous wrapper around the same `provider.query()` call used in non-SDK mode. Agent SDK mode is supported for `consult_library` but the librarian/archivist DAG roles themselves use the standard pipeline engine, not the Agent SDK tool loop.

## Gate Integration

The archivist gate inserts after backpressure and before stagnation check. Current stage numbering in `generation_pipeline.py` is adjusted:

```
Stage 3:  Tournament (Elo scoring)
Stage 3a: Backpressure gate (advance/retry/rollback)
Stage 3b: Archivist gate (NEW — only evaluates archivist output from Stage 2)
Stage 3c: Stagnation check (was 3b)
Stage 3d: Consultation (was 3c)
Stage 4:  Curator gate
```

Rules:
- If backpressure says `rollback`, archivist gate is skipped
- If backpressure says `advance` but archivist says `hard_gate`, archivist wins — retry with reasoning injected as competitor constraint
- `hard_gate` retries count toward `AUTOCONTEXT_MAX_RETRIES` (shared limit)
- `soft_flag` output flows to coach alongside analyst output

## Persistence

### Librarian Notes

Each librarian maintains rolling notes per scenario. After each generation, output is folded into `cumulative_notes.md`. By generation 10, the librarian remembers prior observations and can reinforce patterns ("flagged X in gen 3, team ignored it, score dropped in gen 4").

### Cross-Run Inheritance

When `AUTOCONTEXT_CROSS_RUN_INHERITANCE=true`:
- `librarian_notes/` and `archivist/decisions/` are included in knowledge snapshots
- New runs with the same books pick up cumulative notes
- Removed books have notes archived but not loaded
- New books start fresh

## CLI

```bash
# Ingest a book into the global library
uv run autoctx add-book path/to/book.md --title "Clean Architecture" --tags architecture,principles
uv run autoctx add-book paper.md --title "Attention Is All You Need" --images path/to/figures/

# List the global library
uv run autoctx list-books

# Remove a book
uv run autoctx remove-book clean-architecture

# Run with active books
uv run autoctx run --scenario grid_ctf --gens 5 --books clean-architecture,ddd-reference

# Alternative: activate via env var
AUTOCONTEXT_LIBRARY_BOOKS=clean-architecture,ddd-reference uv run autoctx run --scenario grid_ctf --gens 5
```

No books specified means no librarians, no archivist, DAG unchanged. Fully backwards compatible.

## Configuration

All settings via `AUTOCONTEXT_*` env vars in `config/settings.py`.

```
# Library
AUTOCONTEXT_LIBRARY_BOOKS                  # Comma-separated book names to activate
AUTOCONTEXT_LIBRARY_ROOT                   # Global library path (default: knowledge/_library)

# Librarian
AUTOCONTEXT_LIBRARIAN_ENABLED              # Master toggle (default: true when books specified)
AUTOCONTEXT_MODEL_LIBRARIAN                # Model for librarian role
AUTOCONTEXT_LIBRARIAN_PROVIDER             # Per-role provider override
AUTOCONTEXT_LIBRARY_MAX_CONSULTS_PER_ROLE  # Cap consult_library calls (default: 3)

# Archivist
AUTOCONTEXT_MODEL_ARCHIVIST                # Model for archivist (default: opus — it judges)
AUTOCONTEXT_ARCHIVIST_PROVIDER             # Per-role provider override

# Ingestion
AUTOCONTEXT_INGESTION_MODEL                # Model for initial book reading (default: opus)
```

## Files Changed

| Area | Files | Change |
|------|-------|--------|
| New | `agents/librarian.py`, `agents/archivist.py` | Runner classes |
| New | `knowledge/ingestion.py` | Normalization, chunking, multi-pass reading |
| New | `agents/library_tool.py` | `consult_library` tool implementation |
| Contracts | `agents/contracts.py` | `LibrarianOutput`, `LibrarianFlag`, `ArchivistOutput`, `ArchivistDecision` |
| Aggregation | `agents/types.py` | Librarian/archivist fields on `AgentOutputs` |
| Orchestrator | `agents/orchestrator.py` | Instantiate librarians/archivist, wire into `run_generation()` |
| DAG | `agents/pipeline_adapter.py` | Dynamic librarian nodes + conditional archivist |
| Prompts | `prompts/templates.py` | Librarian/archivist templates, library context for all agents |
| Config | `config/settings.py` | Library/librarian/archivist settings |
| Storage | `storage/artifacts.py` | Library directory management, notes, consultation log |
| Knowledge | `knowledge/export.py` | Include library info in skill packages |
| Pipeline | `loop/generation_pipeline.py` | Archivist gate stage (3b) |
| CLI | `cli.py` | `add-book`, `list-books`, `remove-book`, `--books` on `run` |
| Routing | `agents/role_router.py` | Librarian/archivist in routing table |
| Existing agents | `competitor.py`, `analyst.py`, `coach.py`, `architect.py` | `consult_library` tool permissions |
| Docs | `CLAUDE.md`, `README.md`, `autocontext/README.md`, `CONTRIBUTING.md` | Document new roles, commands, config |

## Documentation Updates

Updates to existing docs use the same writing style and structure already present. Changes:

### CLAUDE.md

- **Repository Layout**: add `knowledge/_library/` entry under `autocontext/`
- **Commands**: add `add-book`, `list-books`, `remove-book`, `--books` flag examples
- **Architecture > Agent Roles**: add Librarian and Archivist descriptions matching existing role format
- **Architecture > Generation Loop**: add Stage 3b (Archivist gate) to the stage list
- **Architecture > Knowledge System**: add library directory structure, cumulative notes, consultation log
- **Configuration**: add Library, Librarian, Archivist, Ingestion groups to the env var table

### README.md

- **Core Capabilities**: add line for literature-aware advisory and gating
- **Common Workflows**: add `add-book` and `run --books` examples

### autocontext/README.md

- **What It Does**: add bullet for literature-grounded advisory via librarian/archivist agents
- **Main CLI Commands**: add `add-book`, `list-books` examples
- **Configuration > Common settings**: add `AUTOCONTEXT_LIBRARY_BOOKS`, `AUTOCONTEXT_MODEL_LIBRARIAN`, `AUTOCONTEXT_MODEL_ARCHIVIST`

### CONTRIBUTING.md

- **Development Notes**: add note about library test fixtures and ingestion model mocking

## What Stays Unchanged

- Scenario system (scenarios do not know about the library)
- Execution and tournament (Elo scoring unaffected)
- Curator (operates on playbook quality, orthogonal to library)
- RLM mode (librarians do not need REPL sessions)
- MCP server (library tools can be added later, not in scope)
- TypeScript port (Python-first, TS follows later)
- TUI (cosmetic — would show librarian status in AgentPanel)
