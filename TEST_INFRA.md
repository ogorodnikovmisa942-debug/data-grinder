# E2E Test Infra: Two-Tier Cognitive Deep Learning System in Data Grinder

## Test Philosophy
- Opaque-box, requirement-driven.
- 100% backward compatibility: all 21 existing unit tests in tests/ must pass at every milestone.
- Comprehensive coverage across all 5 user requirements (R1, R2, R3, R4, R5) and acceptance criteria.
- Methodology: Category-Partition + BVA + Pairwise + Workload Testing.

## Feature Inventory
| # | Feature | Source (requirement) | Tier 1 | Tier 2 | Tier 3 |
|---|---------|---------------------|:------:|:------:|:------:|
| 1 | Tree Mindmap & Subordination View | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| 2 | Obsidian 2D Canvas Graph View | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| 3 | Instant View Toggle & TMA Safety | ORIGINAL_REQUEST §R1, Acceptance | 5 | 5 | ✓ |
| 4 | Knowledge Graph Schema & Consolidation | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 5 | Graph Database Persistence & API | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 6 | 0% Trivial Yes/No & Prompt Safety | ORIGINAL_REQUEST §R3, Acceptance | 5 | 5 | ✓ |
| 7 | Deep Card Archetypes & Compression | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 8 | Strict Blacklist Filtering | ORIGINAL_REQUEST §R4 | 5 | 5 | ✓ |
| 9 | Autonomous Practice Engine & Fallback | ORIGINAL_REQUEST §R5 | 5 | 5 | ✓ |
| 10 | Practice API & Interactive UI | ORIGINAL_REQUEST §R5 | 5 | 5 | ✓ |
| 11 | Full Test Suite Pass (21 baseline tests) | Acceptance Criteria | 21 | - | ✓ |

## Test Architecture
- Unit Test Runner: .\venv\Scripts\python.exe -m unittest discover -s tests
- Baseline Test Suite:
  - 	ests/test_v2_features.py (8 tests)
  - 	ests/test_experiment_and_telemetry.py (13 tests)
- New Cognitive Architecture Test Suite:
  - 	ests/test_cognitive_architecture.py (covering Knowledge Graph, Card Blacklist & Archetypes, Practice Engine, and Schema Invariants)

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Judicial System (Судоустройство) 185p book parsing & graph generation | F4, F5, F6, F7, F8 | High |
| 2 | Pre-study mental model orientation in Telegram WebApp (Tree + Graph) | F1, F2, F3 | Medium |
| 3 | Autonomous interactive practice session on judicial instances | F9, F10 | Medium |
| 4 | Deep decision tree card review with zero trivial Yes/No questions | F6, F7 | Medium |
| 5 | Full round-trip: generation -> graph DB store -> mindmap render -> practice verify | All | High |
