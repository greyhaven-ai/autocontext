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

All librarians run in parallel with analyst and architect (depend on `translator`). The archivist depends on all librarian nodes and only runs if any librarian flags severity `"violation"`. Coach depends on analyst + archivist (or librarians if archivist was skipped).

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

For larger books, multi-pass ingestion:
- Pass 1: chapters 1-N (filling ~120k tokens) -> partial reference
- Pass 2: chapters N+1-M + partial reference from pass 1 -> extended reference
- Pass 3 (if needed): remaining chapters + extended reference -> complete reference
- Max passes controlled by `AUTOCONTEXT_INGESTION_MAX_PASSES`

The ingestion prompt asks for: core thesis, key principles (numbered), chapter-by-chapter notes, decision framework, and red lines (what the book considers genuinely harmful). The red lines section directly feeds the librarian's ability to flag violations.

### Validation

Post-ingestion checks:
- `reference.md` exists and is non-empty
- Token count of reference is under 25k
- All source chapters are mentioned in chapter notes
- If `--images` was used, image references in chunks resolve to actual files

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

All agents see a library availability block in their system prompt listing active book titles with one-line descriptions. Calls are logged to `consultation_log.md`.

Cost control: `AUTOCONTEXT_LIBRARY_MAX_CONSULTS_PER_ROLE` caps queries per agent per generation (default 3).

## Gate Integration

The archivist gate inserts after backpressure and before stagnation check:

```
Stage 3:  Tournament (Elo scoring)
Stage 3a: Backpressure gate (advance/retry/rollback)
Stage 3b: Archivist gate (NEW)
Stage 3c: Stagnation check
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
AUTOCONTEXT_INGESTION_MAX_PASSES           # Max sequential passes for large books (default: 3)
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
