# Project: Two-Tier Cognitive Deep Learning System in Data Grinder

## Architecture
- **Layer 1: Cognitive Mental Framework (R1, R2)**:
  - Backend extracts semantic knowledge graph (nodes, directed edges) and hierarchical tree structure.
  - Precomputed graph/tree persisted in SQLite 	opic_knowledge_graphs for O(1) instant retrieval.
  - Frontend renders dual-view hybrid: Collapsible DOM Tree Mindmap (subordination) + Canvas 2D Obsidian-style Force Graph (orce-graph CDN, dark neon, edge particles, pinch-to-zoom, cooldownTicks(90)).
- **Layer 2: Deep Socratic Practice & Spaced Repetition (R3, R4, R5)**:
  - Generative NLP pipeline refactored: 0% trivial Yes/No questions, 3 deep archetypes (Situational Vignettes / Decision Trees, Contrast Pairs with Gold Standard criteria, Functional Classifications).
  - Strict Blacklist: prompt constraints + programmatic validator is_blacklisted_card filtering fluff, introductory chapters, obsolete laws, and truisms.
  - Compression calibrated to 70–90 deep cards per 185 pages.
  - Autonomous Interactive Practice Trainer: operates independently of graph view, supporting situational quizzes, contrast-pair matching, and legal fact pattern slot-filling.
- **Backward Compatibility & Stability**:
  - All 21 existing backend unit tests in 	ests/ pass with status OK.
  - System prompt asserts in 	est_13_deepseek_prompt_caching_and_atomic_rules strictly preserved.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Tree Mindmap | Collapsible semantic DOM tree showing hierarchy & subordination | M4 | R1 |
| F2 | Obsidian 2D Graph | Canvas force-graph with neon styling, particles, cooldownTicks(90), touch safety | M4 | R1 |
| F3 | Instant Toggle & Entry | Zero-reload switch between tree & graph; entry in #session-starter & header | M4 | R1 |
| F4 | Knowledge Graph Schema | Extraction schema (5 node categories, 4 directed edge relations) & consolidation | M1 | R2 |
| F5 | Graph DB Persistence | TopicKnowledgeGraph table & API endpoints for instant O(1) retrieval | M1 | R2 |
| F6 | 0% Yes/No & Fluff Purge | Eliminate trivial Yes/No questions & academic water while preserving test_13 strings | M2 | R3 |
| F7 | Deep Card Archetypes | Decision Trees, Contrast Pairs with Gold Standard, Functional Classifications; 70-90 cards/185p | M2 | R3 |
| F8 | Strict Blacklist | Prompt directives + Python validator is_blacklisted_card | M2 | R4 |
| F9 | Practice Trainer Engine | Autonomous situational, contrast-pair, and slot-filling engine with deck fallback | M3 | R5 |
| F10 | Practice Trainer API & UI | Endpoints /api/practice/* and Telegram WebApp interactive practice UI | M3 | R5 |
| F11 | E2E & Unit Test Integrity | 21 unit tests passing + new cognitive architecture test suite + mobile UX | M5 | Acceptance |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Knowledge Graph Backend & DB | TopicKnowledgeGraph model, schema, graph consolidation, /api/knowledge-graph | none | PLANNED |
| M2 | NLP Prompt & Card Generator | Refactor prompts, 3 deep card archetypes, 0% Yes/No, strict blacklist, test_13 safety | M1 | PLANNED |
| M3 | Autonomous Practice Module | PracticeItem model, practice service with dynamic fallback, practice API & UI | M1, M2 | PLANNED |
| M4 | Hybrid Visualizer (Tree + 2D) | Canvas force-graph CDN, tree DOM mindmap, instant toggle, touch-action safety | M1 | PLANNED |
| M5 | E2E Verification & Audit | 21 unit tests pass, new tests pass, challenger verification, forensic audit | M1, M2, M3, M4 | PLANNED |

## Interface Contracts
### Knowledge Graph Schema
- 
odes: list of objects:
  - id: string (unique slug/uuid)
  - 
ame: string (concise entity name)
  - category: string ( authority | instance | condition | exception | legal_status)
  - summary: string (1 complete factual sentence)
  - parent_id: string (optional, for tree hierarchy)
  - level: integer (0 for trunk/branch, 1 for instance, 2 for fork/condition)
- edges: list of objects:
  - source: string (source node id)
  - 	arget: string (target node id)
  - elation: string (appealed_to | excludes_application | demarcated_from | subject_to_jurisdiction)

### Card Archetypes
- ignette:
  - Front: Situational case fact pattern with conflicting conditions + qualification question.
  - Back: Unambiguous verdict with cited statute/protocol + reasoning.
- contrast_pair:
  - Front: Two superficially similar concepts.
  - Back: Decisive Gold Standard Criterion (Водораздельный критерий).
- classification:
  - Front: Functional distinction of categories (e.g. mandatory vs discretionary).
  - Back: Core functional trigger without academic fluff.

### Practice API Contracts
- GET /api/practice/session?subject=<str>&count=10:
  - Returns array of interactive practice items:
    - id: string
    - 	ype: situational | contrast_pair | slot_filling
    - prompt: string (case / pair / cloze text)
    - options: list of 3-4 choices
    - correct_answer: string
    - explanation: string
- POST /api/practice/verify:
  - Body: {item_id: <str>, selected_answer: <str>}
  - Returns: {correct: boolean, explanation: <str>, gold_standard: <str>}

## Code Layout
- app/database/models.py: Database models (Phrase, Card, TopicKnowledgeGraph, PracticeItem).
- app/services/ai_gateway.py: Chunker, DEEPSEEK_CACHED_SYSTEM_PROMPT, LLM calls.
- app/services/generation_worker.py: Job processing, multi-chunk graph merge, blacklist filter.
- app/services/practice_service.py: Autonomous practice generation and verification.
- app/api/endpoints/graph.py: Endpoints for Knowledge Graph retrieval and update.
- app/api/endpoints/practice.py: Endpoints for practice sessions and verification.
- app/static/index.html: Web structure, tree mindmap modal, force-graph canvas container, practice UI.
- app/static/js/app.js: Client runtime, force-graph initialization, tree renderer, practice controller.
- app/static/css/main.css: Neon styling, touch-action: none, overscroll-behavior: none, mindmap hierarchy.
- tests/: Unit tests.

