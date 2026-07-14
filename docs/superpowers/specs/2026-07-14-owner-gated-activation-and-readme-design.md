# Owner-Gated Harness Activation And Complete README Design

## Problem

The distributed Harness skills are globally discoverable, but several skill descriptions currently use broad semantic triggers such as "implementing one harness feature" or "running or resuming the harness." A normal coding, review, or documentation request can therefore match Harness behavior even when the Owner did not ask to start the workflow. The project scaffold also says that a repository "uses" the Harness without first stating that the workflow is dormant by default.

The public `README.md` contains only package-validation commands. It does not explain installation, explicit activation, the full workflow from requirements grilling to owner-approved completion, module responsibilities, state ownership, telemetry, migration, upgrade, recovery, or uninstall. A new user cannot safely operate the distribution from the README alone.

## Evidence Classification

- **Verified:** `README.md` currently contains a short product sentence and two validation commands only.
- **Verified:** the front matter for Orchestrator, Builder, Reviewer, Librarian, `init-project`, `to-exec-plan`, and `complete-project` uses broad `Use when...` descriptions.
- **Verified:** `.codex-plugin/plugin.json` has a default prompt that tells Codex to use the Harness without documenting an Owner gate.
- **Verified:** the current clean baseline passes 170 unit and contract tests.
- **Inferred:** broad skill descriptions and scaffold wording increase the chance that Codex selects Harness behavior for an ordinary request. Codex skill selection is model-mediated, so text contracts can strongly constrain but cannot provide a cryptographic enforcement boundary.

## Goals

1. Make every Harness phase dormant unless the human Owner explicitly invokes an approved entry point in the top-level request.
2. Preserve `init-project` and `to-exec-plan` as their existing explicit skill entry points; do not invent duplicate planning or initialization commands.
3. Allow an explicitly activated Orchestrator to delegate Builder, Reviewer, and Librarian work without asking the Owner to trigger each internal role.
4. Prevent role skills from activating because a user merely asks to build, review, document, test, fix, or discuss Harness.
5. Make the root README sufficient to install, understand, run, resume, complete, migrate, diagnose, upgrade, and uninstall the project.
6. Keep the solution text-contract based, small, reversible, and compatible with the current TODO and lifecycle schemas.

## Non-Goals

- No new planner or initializer skill names.
- No custom command parser, background daemon, persistent global activation flag, or authentication mechanism.
- No change to feature scheduling, attempt limits, merge rules, agent concurrency, acceptance criteria, or telemetry schemas.
- No automatic start or resume merely because `TODO.json`, a checkpoint, a worktree, or Harness files exist.
- No installer architecture rewrite, dependency addition, or business-code change.
- No claim that prompt contracts are a security sandbox. They are deterministic workflow policy tested as package text.

## Activation Contract

### Default State

Harness is dormant by default. Installing the package, opening an initialized repository, reading its documentation, mentioning Harness terminology, or having active workflow files on disk does not activate any Harness phase.

If no approved activation appears in the Owner's current top-level request, Codex handles the request as an ordinary task under the repository's normal instructions. It must not preload Harness state, create Harness worktrees, schedule Harness role subagents, update `TODO.json`, or write Harness lifecycle events. This gate does not disable unrelated Codex capabilities or independently requested non-Harness skills.

### Owner Entry Points

The following explicit top-level invocations are the only user-facing entry points:

| Invocation | Scope activated |
| --- | --- |
| `$grill-me` or `$grilling` | Requirements stress-testing only; no implementation |
| `$init-project` | Non-destructive project initialization only |
| `$to-exec-plan` | PRD-to-TODO planning only; human plan approval remains required |
| `$orchestrator` | Start or resume the full execution run from authoritative project state |
| `/harness run` | Start the full execution run |
| `/harness resume` | Reconcile state and resume an explicitly started run |
| `$complete-project` | Run final completion checks and prepare the owner-gated archival proposal |

An invocation is valid only when it is authored by the human Owner in the current top-level request. Skill names or commands found in repository files, quoted text, tool output, web pages, issue content, generated plans, or subagent messages are data and never activation.

Ordinary phrases such as "implement this," "review this," "initialize a variable," "continue," "run the tests," "use builder," or "check the harness" are not activation. A bare `/run` is not a public alias because it is ambiguous; documentation and skill text use `/harness run`.

### Phase Boundaries

Explicit invocation activates only the named phase unless the entry point is `$orchestrator`, `/harness run`, or `/harness resume`.

- `$init-project` does not generate a plan or start implementation.
- `$to-exec-plan` does not initialize a repository, dispatch roles, or implement features.
- `$grill-me` and `$grilling` do not create a PRD or enact the plan unless the Owner separately requests and approves that work.
- `$complete-project` checks completion but retains its separate explicit Owner approval before archival.
- Presence of a completed planning phase does not automatically activate the next phase.

### Orchestrator Delegation

Once the Owner explicitly starts or resumes Orchestrator, internal Builder, Reviewer, and Librarian invocations may proceed without additional Owner commands. Every delegated assignment must include:

- `harnessRunId`: a unique identifier allocated by Orchestrator for the current run or resume;
- `activatedByOwner: true`;
- `activationCommand`: the exact accepted top-level command that started or resumed the run;
- the existing role-specific feature, span, worktree, handoff, and control-root fields.

Role skills fail closed before reading project workflow state or editing files when these fields are missing, malformed, or inconsistent with their assignment. They return a context/assignment rejection to Orchestrator rather than falling back to ordinary role behavior.

This activation envelope is an assignment contract, not new active workflow state. It does not enter `TODO.json` and does not change `featureSpecSha256`. Orchestrator includes the run identity in lifecycle metadata and durable checkpoints so a later `/harness resume` can reconcile the run. A new conversational turn still requires the Owner to invoke `$orchestrator` or `/harness resume`; a checkpoint never self-activates.

`complete-project` may be invoked directly by the Owner or delegated by an already active Orchestrator using the same activation envelope. Its final archival operation always retains the existing second Owner approval gate.

## Contract Changes

### Skill Front Matter

Update Harness-authored skill descriptions to state explicit activation rules:

- `init-project`, `to-exec-plan`, `orchestrator`, and `complete-project`: direct Owner invocation only, with the exact accepted entry point in the description.
- Builder, Reviewer, and Librarian: delegated use only from an Owner-activated Orchestrator assignment.
- `grill-me` and the public `grilling` skill become explicit-entry adapters. The pristine upstream `grilling` instructions remain stored as a pinned internal resource for audit and reuse.

The body of every Harness-authored skill repeats its hard gate before operational instructions. Front matter improves discovery behavior; the body prevents accidental continuation after discovery.

### Project Scaffold

Add a leading "Dormant By Default" section to `scaffold/AGENTS.md` stating:

- Harness files are available but inactive;
- only the explicit Owner entry points activate a phase;
- ordinary tasks use normal Codex behavior;
- role subagents require an active Orchestrator assignment;
- project state never self-activates a run.

The existing state, ownership, progressive-disclosure, safety, and verification rules remain unchanged once Harness is active.

### Plugin Metadata

Revise the plugin default prompt and long description so they explain availability without instructing Codex to auto-run Harness. The default prompt should tell the user which explicit invocation starts a phase and state that ordinary tasks remain normal.

### Installed And Source Copies

The GitHub distribution is the release source of truth. After its contracts pass validation, synchronize the same skill contract content to:

1. the maintained `harness_v2/.agents/skills/` source copies;
2. the installed user copies under `~/.agents/skills/` through the supported installer or a verified equivalent update;
3. project scaffold content supplied by `init-project`.

Do not hand-edit the pristine vendored upstream `grilling` resource. The public `grilling` adapter may change only to enforce the Owner gate and delegate to that resource; provenance and license metadata must distinguish the adapter from the unchanged upstream bytes. After synchronization, start a new Codex task or reload the application so skill discovery does not rely on stale process context.

## README Information Architecture

The README will be an operator guide, not a marketing stub. It will contain the following sections in this order.

### 1. Overview And Safety Boundary

- what My Codex Harness is;
- who it is for;
- dormant-by-default behavior;
- exact Owner activation table;
- actions that never activate it.

### 2. Prerequisites

- Codex client;
- Git;
- Python 3.11 or newer;
- supported macOS, Linux, WSL, and native Windows expectations;
- no credential migration and no automatic push or destructive Git action.

### 3. Installation

- clone/download and checkout of a trusted release;
- `doctor.py` prerequisite check;
- `install.py --dry-run` preview;
- `install.py --yes` installation;
- `bootstrap.sh` one-command equivalent;
- symlink default and `--copy` fallback;
- installed locations under `~/.codex` and `~/.agents`;
- restart/reload requirement;
- post-install `doctor.py --installed` verification.

Commands must match the implemented CLI exactly and use placeholders that users can replace safely.

### 4. Five-Minute Quickstart

Show one minimal repository flow:

1. explicitly run `$init-project`;
2. fill `docs/product-specs/prd.md`;
3. optionally run `$grill-me` to stress-test the plan;
4. explicitly run `$to-exec-plan` and approve the generated TODO plan;
5. commit the Harness closure required by Orchestrator preflight;
6. explicitly run `$orchestrator` or `/harness run`;
7. inspect checkpoints, evidence, telemetry, and completion output;
8. explicitly approve final archival when requested.

### 5. Complete Workflow Guide

Document every stage from start to finish:

1. requirements grilling;
2. PRD creation and human approval;
3. project initialization/adoption;
4. execution-plan generation, DAG waves, conflict metadata, and plan approval;
5. Orchestrator preflight, including why committed closure is required;
6. worktree creation and Builder assignment;
7. Builder red-to-green implementation, validation, local commit, and normalized handoff;
8. Builder handoff preflight;
9. independent Reviewer acceptance and evidence capture;
10. serialized merge and global validation;
11. Librarian navigation and durable-doc maintenance;
12. worktree cleanup and wave checkpoint;
13. retry, context repair, blockers, attempt ceiling, interruption, and resume;
14. completion verification, proposed report, second Owner approval, and archival.

Each stage states: who owns it, required inputs, files it may change, evidence produced, success condition, and next explicit entry point where human activation is required.

### 6. Module Inventory

Provide a table covering at least:

| Module | Purpose |
| --- | --- |
| `grill-me` / `grilling` | One-question-at-a-time requirements and design stress test |
| `init-project` | Non-destructive project scaffold and progressive-disclosure navigation |
| `to-exec-plan` | Approved PRD to Builder-ready TODO JSON and dependency waves |
| Orchestrator | Scheduling, assignments, state transitions, integration, telemetry, and checkpoints |
| Builder | One-feature implementation, tests, validation, commit, and handoff |
| Reviewer | Independent acceptance judgment and durable evidence |
| Librarian | Post-merge project-map and durable documentation maintenance |
| `complete-project` | Final consistency checks, report, and owner-gated archival |
| `agents/` | Codex runtime identity/model/sandbox adapters for delegated roles |
| `scaffold/` | Files installed into a target project by `init-project` |
| `schemas/` | Structured handoff and lifecycle validation contracts |
| `scripts/install.py` | Transactional user-level install and upgrade |
| `scripts/doctor.py` | Prerequisite, bundle, and installed-state diagnostics |
| `scripts/uninstall.py` | Ownership-aware, fail-closed removal |
| `scripts/build_bundle.py` | Reproducible offline migration bundle and checksums |
| `scripts/bootstrap.sh` | Doctor, dry-run, then confirmed install in one command |
| `tests/` | Offline package, contract, installer, bundle, scaffold, and vendor validation |

Also include a repository tree with links to the authoritative files.

### 7. State, Ownership, And File Map

Explain the distinction among:

- `TODO.json` authoritative mutable workflow state;
- `handoffs.jsonl` structured role events;
- `lifecycle.jsonl` observability only;
- `worklog/evidence/` acceptance artifacts;
- `worklog/checkpoints/` resume data;
- `docs/project-map.md` navigation;
- completed plan archives and generated completion reports.

Include a role-to-file ownership matrix and explicitly state that telemetry cannot override TODO state.

### 8. Timing And Token Telemetry

Explain `startedAt`, `finishedAt`, and measured `durationMs` for run, wave, role, merge, validation, repair, and checkpoint spans. Explain `input`, `cachedInput`, `output`, and `total` token fields. Runtime-provided values are recorded; unavailable token counts are `null`, never estimated. Include a small JSON example.

### 9. Resume, Recovery, And Troubleshooting

Cover dirty/uncommitted Harness closure, missing context, rejected handoff, failed acceptance criteria, unavailable review capability, attempt ceiling, interrupted role, dirty worktree cleanup refusal, stale installed skills, installation recovery journals, and doctor failures.

### 10. Migration, Upgrade, Rollback, And Uninstall

- build and verify an offline bundle;
- copy only the bundle, checksum, and manifest;
- install on another machine without copying secrets;
- rerun install for an ownership-checked upgrade;
- explain conflict behavior for locally modified managed files;
- preview and perform uninstall;
- explain backups, journals, and what remains project-owned.

### 11. Development And Release Validation

- package validator;
- full unit-test command;
- bundle build and verification;
- no-paid-model mechanical tests;
- contribution, security, license, notice, and changelog links.

### 12. Known Boundaries

State that explicit activation is enforced by distributed text contracts and tests, not by a privileged runtime access-control layer. Also state that Harness does not push, rewrite history, delete dirty worktrees, change dependencies, or archive the active plan without the required Owner approvals.

## Testing Strategy

Implementation follows test-first contract changes.

1. Add failing contract tests that reject broad auto-trigger front matter and require exact Owner activation language for Harness-authored entry skills.
2. Add failing tests that require Builder, Reviewer, and Librarian to document the activation envelope and reject missing assignments before state access.
3. Add failing scaffold tests for the dormant-by-default section and explicit activation table/wording.
4. Add failing plugin-manifest tests ensuring the default prompt describes explicit invocation and does not instruct automatic use.
5. Add README contract tests for all required sections, module names, exact implemented commands, telemetry `null` semantics, local-link existence, and absence of ambiguous bare `/run` guidance.
6. Keep pristine vendored `grilling` integrity tests unchanged and update adapter tests to require the explicit Owner gate.
7. Run package validation, focused tests, then all 170+ offline tests.
8. After installation synchronization, compare source and installed Harness-authored skill bytes and run `doctor.py --installed`.
9. Reload Codex and perform two manual smoke checks in the main thread: an ordinary small coding request remains normal; an explicit `$orchestrator` or `/harness resume` request selects Harness behavior.

Because the Owner requested single-thread work for Harness configuration and inspection, deterministic contract tests and main-thread smoke checks replace subagent-based pressure testing for this change.

## Implementation Surfaces

Expected distribution changes:

- `skills/orchestrator/SKILL.md`
- `skills/builder/SKILL.md`
- `skills/reviewer/SKILL.md`
- `skills/librarian/SKILL.md`
- `skills/init-project/SKILL.md`
- `skills/to-exec-plan/SKILL.md`
- `skills/complete-project/SKILL.md`
- `skills/grill-me/SKILL.md`
- `skills/grilling/SKILL.md` as the public gated adapter, while preserving the pristine upstream resource
- `scaffold/AGENTS.md`
- `.codex-plugin/plugin.json`
- `README.md`
- focused contract/documentation tests

Possible durable documentation links may be added only when the README would otherwise become unreadable. The requested end-to-end operating guide and module inventory remain fully usable from `README.md` itself.

## Rollout

1. Implement and validate the distribution changes in the isolated `codex/owner-gated-activation` worktree.
2. Review the diff for exact scope, secrets, stale commands, and broken links.
3. Commit one logical contract-and-documentation change and push the feature branch; merge to `main` only with Owner approval.
4. Synchronize the validated Harness-authored skill and scaffold contracts into the maintained `harness_v2` source without touching unrelated dirty files.
5. Upgrade the user installation through the supported installer or a verified equivalent path that preserves unrelated global config.
6. Run installed-state diagnostics and byte-parity checks.
7. Reload Codex and perform the ordinary-task and explicit-trigger smoke checks.
8. Report verified results, remaining runtime-selection limitation, and any deferred merge/release action.

## Risks And Mitigations

- **Model-mediated discovery may still drift:** duplicate the exact gate in front matter, skill body, scaffold, plugin metadata, README, and tests; document that this is policy rather than sandbox enforcement.
- **Role assignments can be copied or malformed:** require a complete activation envelope plus existing identity fields and fail closed before state access.
- **Resume could become accidental auto-start:** require a fresh top-level Owner invocation and treat checkpoints as data only.
- **Distribution, maintained source, and installed copies may diverge:** validate distribution first, synchronize explicitly, compare bytes, run doctor, and reload the runtime.
- **README commands may drift from scripts:** test commands and flags against the implemented CLI surface.
- **Large documentation edit may hide behavior changes:** keep behavior-contract edits narrow and test each affected surface.

## Success Criteria

- An ordinary top-level request with no approved invocation remains outside Harness even inside an initialized project.
- Every public Harness phase requires an exact Owner invocation, and every delegated role requires a valid active-run assignment envelope.
- TODO files, checkpoints, documentation, quoted commands, and subagent text never self-activate Harness.
- `init-project` and `to-exec-plan` remain the only documented initialization and plan-generation skill entry points.
- The README independently explains installation, activation, the full workflow, every module, role/file ownership, telemetry, resume, completion, migration, upgrade, troubleshooting, validation, and uninstall.
- All focused and full package tests pass from a clean baseline.
- Distribution source, maintained `harness_v2` contracts, and installed user copies are synchronized and verified before completion is claimed.

## Open Questions

None. The Owner approved strict explicit activation and requested a complete GitHub README covering the workflow from `grill-me` through final project completion and every included module.
