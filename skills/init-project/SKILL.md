---
name: init-project
description: Use when initializing a new or existing repository for progressive-disclosure harness state, project navigation, isolated worktrees, or project-specific agent guidance.
---

# Init Project

Initialize a repository with a progressive-disclosure harness. Agents start at `docs/project-map.md`, then read only task-relevant files.

## Use

Resolve this active skill's installed directory, then run its script with an absolute path from the target repository root:

```bash
python3 <init-project-skill-dir>/scripts/init_project.py --root .
```

Do not assume a repository-local `.agents/skills/` path. If running from another location, pass the absolute target repository path to `--root`.

## Rules

- Non-destructive by default: preserve project-owned files and report `created`, `replaced`, `skipped`, and `conflicts`.
- Initialize git only when `.git` is absent.
- Append `.worktrees/` and `.DS_Store` to `.gitignore` if missing.
- Do not auto-commit, add remotes, push, or rename branches.
- Create `AGENTS.md` with the project-level agent principles.
- Do not create or author reusable Orchestrator, Builder, Reviewer, or Librarian behavior contracts; they are installed separately. Project `.codex/agents/harness-*.toml` files are prefixed runtime adapters only.
- Use `--dry-run` to preview changes.
- Use `--force` only when the user explicitly asks to refresh harness-managed schemas, lifecycle guidance, or project Codex runtime adapters. Even then, preserve project-owned `AGENTS.md`, `CLAUDE.md`, design docs, product docs, the active PRD and `TODO.json`, `project-map.md`, the tech-debt tracker, references index, existing handoff/lifecycle JSONL, checkpoints, and evidence.

## Layout

- `docs/`: project knowledge and navigation.
- `docs/product-specs/prd.md`: product requirements source for execution planning.
- `docs/exec-plans/active/TODO.json`: active feature queue and mutable status.
- `docs/exec-plans/tech-debt-tracker.md`: Librarian-recorded optimization candidates; never active workflow state.
- `worklog/`: visible structured handoffs, lifecycle telemetry, evidence, and checkpoints.
- `worklog/handoffs.jsonl`: structured role handoffs, failures, verdicts, and repair requests.
- `worklog/logs/lifecycle.jsonl`: append-only lifecycle telemetry, never workflow state.
- `worklog/checkpoints/`: durable Orchestrator resume checkpoints.
- `worklog/evidence/`: review and validation evidence.
- `AGENTS.md`: shared engineering principles for every agent.

## After Init

Use `to-exec-plan` to fill `TODO.json` from `docs/product-specs/prd.md` or an explicitly provided PRD document. During `/run`, the orchestrator merges passed work, then invokes Librarian to update `docs/project-map.md` and affected project docs from the merged diff.
