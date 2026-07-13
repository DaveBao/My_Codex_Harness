---
name: to-exec-plan
description: Use when converting a PRD document into harness-ready execution-plan TODO JSON with Builder-ready feature blocks, DAG-scheduled waves, validation commands, and conflict-risk metadata.
---

# To Exec Plan

Create the active execution plan JSON. Do not create the product spec, implement product code, run Builder work, or maintain `docs/project-map.md`.

## Read Boundary
Read only `AGENTS.md`, `docs/project-map.md`, the supplied PRD (default `docs/product-specs/prd.md`), active `TODO.json` when replanning, unresolved `worklog/handoffs.jsonl` decisions/rework/blockers, and explicit references. Do not preload all docs, scan the repository, or read logs unless the planning decision requires it.

## Outputs
Write or update `docs/exec-plans/active/TODO.json`. Return structured planning questions and follow-ups to the user or Orchestrator for routing. Do not write `worklog/handoffs.jsonl` or `worklog/logs/lifecycle.jsonl`; Orchestrator alone owns both shared event channels. Do not derive telemetry.

Do not write source, tests, migrations, unrelated product docs, the PRD, `worklog/evidence/`, or `docs/project-map.md` unless the user explicitly asks for map maintenance. If navigation is missing, return a structured Librarian follow-up instead of persisting it.

## Planning Workflow
1. State the PRD path and planning assumptions.
2. Do not invent missing requirements. If the PRD is too vague, return structured questions and stop.
3. Split work into narrow, independently verifiable tracer-bullet vertical slices.
4. Build a dependency DAG without false dependencies; record shared-file, shared-state, worktree, and runtime-resource issues as conflict risk.
5. Group parallel-safe slices into waves; later waves start after blockers pass.
6. Present the breakdown and request approval before publishing when requirements, dependencies, or ACs are materially uncertain.
7. Append approved entries to `TODO.json` by default; replace it only on explicit full-rebuild request.

## TODO JSON Format
`docs/exec-plans/active/TODO.json` is the source of truth. Each feature contains `id`, `title`, `status`, `attemptCount`, `blockedBy`, `expectedWave`, `conflictRisk`, `goal`, scoped allowed/not-allowed changes, assumptions, constraints, acceptance criteria, validation, and explicit references. Avoid file-by-file instructions unless a path is a confirmed contract.

The only workflow-mutable top-level feature fields are `status`, `attemptCount`, `handoffReferences`, and `validationHistory`. Every other top-level field is feature-definition input to `featureSpecSha256`; adding or changing one invalidates prior assignments. New mutable state requires an explicit helper-contract version change, not an ad hoc field.

## Replanning Rules
Preserve existing features, `status`, `attemptCount`, `handoffReferences`, and `validationHistory`. Allocate IDs after the active maximum. Block only on unfinished active features, treat completed work as assumptions, validate dependency existence and acyclicity, and record stale navigation as a librarian follow-up rather than expanding this role.

## Finish
Report changed files, DAG validation, unresolved questions, and the next entry point: `$orchestrator` or `/run`.
