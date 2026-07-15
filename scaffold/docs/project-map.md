# Project Map

This is a navigation index, not a knowledge base. It points agents to the smallest useful context for the current feature.

## Ownership

- Created by `init-project`.
- Updated by Librarian after merged work passes global validation.
- Planner may read it but does not own it.

## Entry Points

- `AGENTS.md`: permanent repository invariants.
- `docs/codex-policy.md`: project-owned Codex writing policy and prerequisite checks.
- `.codex/config.toml` and `.codex/agents/harness-*.toml`: project runtime settings and prefixed role identities; behavior remains in installed role skills.
- `docs/product-specs/prd.md`: product requirements input.
- `docs/exec-plans/active/TODO.json`: active queue, dependencies, status, and attempts.
- `docs/exec-plans/tech-debt-tracker.md`: read at a wave boundary only for Librarian's current-wave optimization candidates; it is not workflow state.
- `worklog/handoffs.jsonl`: append-only JSONL handoffs, failures, verdicts, and repair requests; filter by `featureId`.
- `docs/references/worklog-events.md`: read only when emitting, validating, or analyzing lifecycle telemetry.
- `docs/references/builder-handoff.schema.json`: read only when emitting or validating a Builder handoff.

## Read Only When Referenced

- `docs/design-docs/`: durable architecture and product decisions.
- `docs/references/`: external or generated reference material.
- `docs/generated/`: generated stable docs.
- `worklog/evidence/`: Reviewer evidence.
- `worklog/logs/`: lifecycle telemetry, never workflow state.
- `worklog/checkpoints/`: durable Orchestrator resume checkpoints.

## Do Not Preload

- Do not read every file under `docs/`.
- Do not read archives, history, logs, or generated references unless the map, active feature, or relevant handoff event points there.
- Do not treat this map as workflow state.

## Validation

- Project validation command: configure for the repository.
