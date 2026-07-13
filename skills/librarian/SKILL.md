---
name: librarian
description: Use when a harness feature or wave has been merged and globally validated, and Codex must update docs/project-map.md plus necessary durable docs without touching workflow state.
---

# Librarian

Update project navigation after merged work. Keep `docs/project-map.md` as a navigation index, not a changelog or knowledge base.

## Inputs
- current `docs/project-map.md`
- merged feature IDs and title snapshots
- merged commit or diff summary and changed-file list
- Builder/Reviewer summaries, evidence pointers, merged commit identity, and assigned span identity
- durable docs already pointed to by the map

Run only after Orchestrator merges passed work and global validation succeeds.

## Work
- Update navigation, ownership, and "when to read" guidance from the merged diff and changed files.
- Update durable docs only for changed behavior, architecture, setup, public API, configuration, or domain language.
- Compare only the current wave's merged diffs and changed files for duplicate implementations of the same capability. Do not scan the repository, preload all docs, scan completed archives, build DAGs, or prove assumptions.
- Append every evidence-backed duplicate candidate to `docs/exec-plans/tech-debt-tracker.md` with ID, wave, feature IDs, paths/symbols, duplicated capability, evidence, risk, disposition, and suggested consolidation point. Use `deferred` unless the duplication creates conflicting sources of truth, inconsistent externally observable behavior, security/data-integrity risk, or a known correctness dependency for the next wave; only those cases use `correctness-blocking`.
- Treat completed or archived capabilities as existing context; map their doc or code entry points when needed, but do not add them to active `blockedBy`.
- When duplication evidence is insufficient or navigation is unclear, report it under `unclear`; do not invent content or ask the user during a run.

## Output
Return a docs-only report with merged commit identity to Orchestrator: `created`, `updated`, `skipped`, `unclear`, `duplicateCandidates`, and `validation`, plus telemetry exactly as `{ spanId, startedAt, finishedAt, durationMs, tokens: { input, cachedInput, output, total }, outcome }`. Report file paths as normalized repository-relative POSIX paths; `created` and `updated` are the exact files Orchestrator may stage. Each duplicate candidate repeats its tracker ID, disposition, evidence, and affected feature IDs/paths so Orchestrator can apply the correctness gate without reading unrelated debt. `spanId` must be the assigned value; times and duration are measured; token fields are runtime values or `null`, never estimates. Do not return an `eventId`; Orchestrator validates and persists the result.

## Validation And Limits
- Every local path added to `docs/project-map.md` exists.
- The map contains navigation only, with no copied PRD, handoff, diff, or review evidence.
- Duplicate observations are wave-local, evidence-backed, and recorded even when deferred. They never modify TODO or directly schedule refactoring.
- Run `git diff --check`; run project validation only when setup/configuration docs changed in a runtime-relevant way.
- Do not edit `docs/exec-plans/active/TODO.json`, shared JSONL, lifecycle data, source, tests, business code, status, or attempts.
