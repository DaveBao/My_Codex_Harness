---
name: init-project
description: Use when initializing a new or existing repository for progressive-disclosure harness state, project navigation, isolated worktrees, or project-specific agent guidance.
---

# Init Project

Initialize a repository with a progressive-disclosure harness. Agents start at `docs/project-map.md`, then read only task-relevant files.

## Use

Run the script from the target repository root:

```bash
python3 .agents/skills/init-project/scripts/init_project.py --root .
```

If using this skill from another location, pass the absolute target repo path to `--root`.

## Rules

- Non-destructive by default: skip existing files; report `created`, `skipped`, and `conflicts`.
- Initialize git only when `.git` is absent.
- Append `.worktrees/` and `.DS_Store` to `.gitignore` if missing.
- Do not auto-commit, add remotes, push, or rename branches.
- Create `AGENTS.md` with the project-level agent principles.
- Do not create or author reusable Orchestrator, Builder, Reviewer, Librarian, or runtime-adapter contracts; they are installed separately.
- Use `--dry-run` to preview changes.
- Use `--force` only when the user explicitly asks to refresh harness-managed scaffold files. Even then, preserve project-owned `AGENTS.md`, `CLAUDE.md`, design docs, product docs, the active PRD and `TODO.json`, `project-map.md`, the tech-debt tracker, references index, and existing handoff/lifecycle JSONL. Harness-owned schemas and lifecycle guidance may be refreshed.

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
