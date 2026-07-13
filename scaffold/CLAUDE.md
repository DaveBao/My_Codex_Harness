# Claude Runtime Adapter

Follow `AGENTS.md` for permanent repository invariants. Reusable Orchestrator, Builder, Reviewer, and Librarian behavior contracts are installed separately. Project-scoped Codex adapters live under `.codex/agents/` and contain runtime identity only.

Active state is `docs/exec-plans/active/TODO.json`. All runtime artifacts are visible under `worklog/`:

- `worklog/handoffs.jsonl`: Orchestrator is the only shared-JSONL writer during active runs.
- `worklog/logs/lifecycle.jsonl`: Orchestrator-owned lifecycle telemetry, never workflow state.
- `worklog/evidence/`: Reviewer evidence.
- `worklog/checkpoints/`: Orchestrator-owned durable resume checkpoints.
