Read this file only when emitting, validating, or analyzing lifecycle events.

# Worklog Lifecycle Events

## Storage

- `worklog/handoffs.jsonl`: structured role handoffs, failures, verdicts, and repair requests.
- `worklog/logs/lifecycle.jsonl`: append-only lifecycle telemetry.
- `worklog/checkpoints/`: durable Orchestrator resume checkpoints.
- `worklog/evidence/`: review and validation evidence.

During an active run, Orchestrator is the only writer to shared JSONL. Lifecycle telemetry is not workflow state.

`metadata.mode` may be `"debug"`; formal workflow selection ignores those events.
Lifecycle analytics exclude `metadata.mode == "debug"` by default and include it only for an explicitly requested debug analysis.

## Event Contract

`lifecycle-event.schema.json` is the canonical Draft 2020-12 schema. Validate every event against it before appending. Each `spanId` has one `started` event and one terminal `finished` event. Every event includes:

`schemaVersion`, `eventId`, `runId`, `waveId`, `featureId`, `featureName`, `spanId`, `parentSpanId`, `timestamp`, `phase`, `actor`, `action`, `model`, `reasoningEffort`, `durationMs`, `tokens`, `status`, `outcome`, `error`, and `metadata`.

- `featureId` is stable and `featureName` is its title snapshot; they are both `null` or both nonblank strings.
- Use runtime-provided token counts only. Unavailable usage is `null`; never estimate it.
- Use exclusive token accounting: a finished span records only its own work and parent/container spans never copy child tokens.
- Finished non-model actions use zero token counts. Unavailable usage uses `null` counts with `metadata.telemetryComplete = false` and a bounded `telemetryReason`; never estimate it.
- Codex App native role tasks use `null` token counts and `telemetryReason = "app_usage_unavailable"` because per-role official Usage is not exposed to Orchestrator.
- Runtime metadata may include bounded `attemptNumber`, `sessionId`, `telemetryComplete`, and `telemetryReason`.
- For `started`, `durationMs`, `status`, `outcome`, and `error` are `null`. For `finished`, `durationMs` is measured from the persisted matching `started` timestamp and `status` is terminal.
- Errors are bounded category/retryability/summary objects. Metadata and errors contain no raw outputs.

## Actions

- `run`, `resume_run`, `pause_context`: run lifecycle and context boundaries.
- `wave`: select or complete one dependency-ready wave.
- `implement_feature`, `review_feature`, `maintain_project_map`: role work.
- `merge_feature`: serialized integration work.
- `validate_feature`: feature-scoped validation and Builder handoff preflight before review.
- `validate_global`: project-wide validation after a reviewed feature is merged.
- `retry`: one counted failed-delivery transition back to Builder; context repair is not a retry.
- `handoff_rejection`: rejected-handoff routing, paired with exactly one counted retry transition.
- `context_repair`, `checkpoint`: targeted continuity work.

Never include prompts, source code, secrets, full command output, or user data in lifecycle events.
