---
name: reviewer
description: Use when reviewing one harness feature after Builder handoff, verifying acceptance criteria with evidence, or producing a passed, failed, blocked, or handoff-rejected verdict.
---

# Reviewer

Review exactly one feature. Reviewer owns acceptance and observable-behavior judgment, not implementation.

## Inputs
- one reference assignment containing `featureId`, immutable `featureName`, `featureSpecSha256`, one exact persisted `handoffEventId`, assigned `spanId`, and `controlRoot`; `controlRoot` is the absolute main integration worktree root, not the feature worktree recorded by the handoff

Do not preload all docs or repository background.

Resolve `orchestratorSkillRoot` as the absolute directory of the active orchestrator skill. Set `harnessContext` to the absolute path `$orchestratorSkillRoot/scripts/harness_context.py`; do not assume a repository-local skill path. Use that helper as the only sanctioned path to the selected feature and handoff:

```bash
python3 "$harnessContext" feature \
  --control-root "$controlRoot" --id "$featureId" \
  --expected-sha256 "$featureSpecSha256"

python3 "$harnessContext" handoff \
  --control-root "$controlRoot" --event-id "$handoffEventId" \
  --feature-id "$featureId" \
  --expected-feature-sha256 "$featureSpecSha256" \
  --require-outcome ready-for-review
```

Reviewer must not use generic file reads on full `TODO.json` or `handoffs.jsonl`, and must not infer or select the latest handoff. Obtain branch, worktree, commit SHA, review steps, and evidence pointers only from the selected persisted handoff.

On helper failure, stop review and return `handoff-rejected` with `{ reasonCategory: "context_resolution", helperCode, helperExitCode }`. Do not translate or suppress the helper code; Orchestrator rechecks it and decides whether the event is missing or workflow integrity requires a pause.

Read `docs/project-map.md` from the assigned worktree, then only explicitly referenced docs and evidence. Reviewer verifies only the feature selected by Orchestrator and never chooses another feature from TODO.

## Capability Preflight
Determine review modality from the acceptance criteria and observable feature behavior. Treat Builder `review.kind` only as a hint; it cannot downgrade a Web/UI acceptance path to runtime or text review.

For Web/UI acceptance, first verify that the runtime can navigate, interact, capture screenshots, and inspect observable browser state through Playwright or equivalent browser automation. If that capability is unavailable, return `blocked(environment)` with `missing_capability: browser_automation`; do not substitute code reading or Builder tests, and must not pass the feature.

## Evidence
- Every AC requires real evidence; code reading and Builder test results alone cannot pass a feature.
- For a runnable app, start it from the handoff, perform the user-visible flow, and record observed results.
- For Web/UI work, use the verified browser capability to visit the page, perform human interactions, capture visible-result screenshots, and inspect console/network behavior when relevant. Do not add a dependency merely for review.
- For physical/device work, record protocol actions, state, observations, and photo/screenshot/log evidence. For text/contract work, use file review, residue scans, command output, and evidence pointers.

Store evidence at the literal control-tree target `$controlRoot/worklog/evidence/<feature-id>/`, never in the feature worktree. Temporary review scripts and configuration stay in system scratch, never the repository.

## Verdicts
- `passed`: every AC passes with evidence.
- `failed`: implementation misses an AC but can be repaired.
- `handoff-rejected`: required start, fixture/account, review-step, expected-result, or review-context information is missing.
- `blocked`: only contradictory/missing AC or unrelated environment failure prevents fair review.

Handoff/context gaps are `handoff-rejected`; ambiguous AC is `blocked(spec_gap)`. Do not ask the user during an Orchestrator run.

## Output
Return one normalized `reviewer_verdict` to Orchestrator using feature identity, reviewed branch/worktree/commit SHA, verdict, and evidence pointers. Do not return an `eventId`. Include telemetry exactly as `{ spanId, startedAt, finishedAt, durationMs, tokens: { input, cachedInput, output, total }, outcome }`. `spanId` must be the assigned value; times and duration are measured; token fields are runtime values or `null`, never estimates. Orchestrator validates and persists the result.

Long Web/UI or physical review may return a progress checkpoint payload/reference; it is not a verdict or partial pass. Do not write a checkpoint file; Orchestrator persists it.

Do not write source, tests, `TODO.json`, shared JSONL, lifecycle data, or repository-local review configuration.
