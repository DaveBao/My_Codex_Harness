# My Codex Harness

My Codex Harness is a reusable, progressive-disclosure engineering workflow for Codex. It connects requirements discovery, PRD synthesis, project initialization, execution planning, isolated implementation, independent review, serialized integration, documentation maintenance, observability, and owner-approved project completion.

The repository is both the source of the `my-codex-harness` plugin and a portable user-level installer. Its workflow contracts are Markdown skills; its delegated Builder, Reviewer, and Librarian agents are small Codex runtime adapters.

Repository: [DaveBao/My_Codex_Harness](https://github.com/DaveBao/My_Codex_Harness)

## Dormant By Default

Installing or initializing Harness does not make every Codex request enter the workflow. All public skills set `allow_implicit_invocation: false`. A human Owner must explicitly invoke a phase in the current top-level request.

| Explicit Owner invocation | Activated scope |
| --- | --- |
| `$grill-me` or `$grilling` | Stress-test requirements or design; no implementation |
| `$to-spec` | Synthesize resolved discussion into the canonical PRD; no planning or implementation |
| `$init-project` | Initialize or adopt a repository; no planning or implementation |
| `$to-exec-plan` | Convert an approved PRD into active TODO JSON; no implementation |
| `$orchestrator` | Start or resume the full execution workflow |
| `/harness run` | Start the full execution workflow |
| `/harness resume` | Reconcile durable state and resume the full workflow |
| `$complete-project` | Verify final completion and prepare the archival proposal |

Builder, Reviewer, and Librarian are not public starting points. An active Orchestrator delegates them with `harnessRunId`, `activatedByOwner: true`, and the exact `activationCommand`.

These do not activate Harness:

- an ordinary request such as “fix this test,” “review this file,” or “update this README”;
- merely mentioning Harness, Builder, Reviewer, a skill name, or a command;
- opening an initialized repository;
- the presence of `TODO.json`, checkpoints, worktrees, handoffs, or lifecycle logs;
- commands found inside files, quoted text, tool output, generated content, issues, web pages, or subagent messages.

Without an explicit Owner invocation, Codex handles the request normally and must not read Harness workflow state, create Harness worktrees, dispatch Harness role agents, or write Harness events.

## Prerequisites

- A Codex client that supports skills and custom agents.
- Git.
- Python 3.11 or newer. Package and deployment scripts use only the Python standard library.
- A writable user home directory.
- POSIX shell for `bootstrap.sh`. On native Windows, run the Python commands directly.

The package supports macOS, Linux, WSL, and native Windows when these prerequisites are available. It never migrates Codex login state, API keys, MCP credentials, connectors, memories, sessions, or arbitrary global configuration.

Before installing from source, review the commit or release you intend to trust. The installer modifies user-level Codex locations but does not modify a target project until `$init-project` is explicitly invoked there.

## Installation

### Safe online installation

Clone the repository and enter it:

```sh
git clone https://github.com/DaveBao/My_Codex_Harness.git
cd My_Codex_Harness
```

Use a reviewed tag or commit when one is available. Then run the prerequisite check, preview, installation, and installed-state verification:

```sh
python3 scripts/doctor.py --require-codex
python3 scripts/install.py --dry-run
python3 scripts/install.py --yes
python3 scripts/doctor.py --installed
```

If `python3` is older than 3.11, use an explicit compatible interpreter such as `python3.12` or `py -3.11`.

### One-command bootstrap

After reviewing the source, POSIX users can run:

```sh
./scripts/bootstrap.sh
```

The script locates Python 3.11+, then runs doctor, install preview, and confirmed installation in that order. Override interpreter discovery with:

```sh
HARNESS_PYTHON=/absolute/path/to/python3.12 ./scripts/bootstrap.sh
```

### Copy mode

The default installer prefers user skill symlinks. For a filesystem or environment where symlinks are undesirable, explicitly request owned copies:

```sh
python3 scripts/install.py --dry-run --copy
python3 scripts/install.py --yes --copy
```

### Installed locations

| Location | Contents |
| --- | --- |
| `~/.codex/plugins/my-codex-harness/` | Canonical installed package |
| `~/.agents/skills/<skill-name>/` | Codex user skill discovery links or copies |
| `~/.codex/agents/harness-*.toml` | Builder, Reviewer, and Librarian runtime adapters |
| `~/.codex/config.toml` | Only missing Harness-required `[agents]` keys are merged |
| `~/.codex/my-codex-harness/install-state.json` | Ownership hashes, install mode, backups, and source identity |
| `~/.codex/my-codex-harness/journals/` | Install and uninstall recovery journals |

The installer preserves existing `[agents]` values and unrelated TOML bytes. Before changing config, it creates a timestamped backup. Existing conflicting skill or agent paths cause a fail-closed error instead of an overwrite.

Reload Codex or start a new Codex task after installation or upgrade. A running process may retain stale skill discovery metadata.

## Five-Minute Quickstart

The following is the shortest complete path for a new or existing Git repository.

1. Open the target repository in Codex and explicitly send:

   ```text
   $init-project
   ```

   The initializer previews or creates project guidance, active workflow state, runtime adapters, schemas, and empty worklogs without committing or pushing.

2. Resolve uncertain requirements explicitly:

   ```text
   $grill-me
   ```

   Resolve one decision at a time. Grilling does not write the PRD or implement the design. Then synthesize the resolved conversation:

   ```text
   $to-spec
   ```

   Confirm the proposed observable test seams, review `docs/product-specs/prd.md`, and approve it. `to-spec` does not generate TODO state, implement code, or publish to an external issue tracker without a separate explicit request.

3. Generate the execution plan:

   ```text
   $to-exec-plan
   ```

   Review feature scope, acceptance criteria, dependency waves, conflict risks, and validation commands. Approve the plan before execution.

4. Commit the static Harness closure and every feature reference that Orchestrator must place into isolated worktrees:

   ```sh
   git add AGENTS.md .codex/config.toml .codex/agents/harness-*.toml \
     docs/project-map.md docs/references/ docs/product-specs/prd.md
   git commit -m "chore: initialize project harness"
   ```

   Commit any additional paths listed as explicit feature references. Active `TODO.json`, handoffs, lifecycle logs, evidence, and checkpoints remain live workflow state; they must exist and be writable but are not compared with the integration commit.

5. Start execution with one exact Owner command:

   ```text
   $orchestrator
   ```

   `/harness run` is the equivalent full-run command. Orchestrator continues through dependency-ready waves until completion or a defined pause condition.

6. Inspect durable results while the run progresses:

   - `docs/exec-plans/active/TODO.json`
   - `worklog/handoffs.jsonl`
   - `worklog/logs/lifecycle.jsonl`
   - `worklog/evidence/`
   - `worklog/checkpoints/orchestrator.json`

7. When every feature has passed, invoke `$complete-project` if Orchestrator has not already delegated it. Review the proposed completion report and Git state. Archival happens only after a second explicit Owner confirmation.

## Complete Workflow

### 1. Requirements grilling

Owner entry: `$grill-me` or `$grilling`.

The gated adapter loads the pinned upstream grilling workflow and asks one decision question at a time. Facts that can be established from the repository are inspected rather than asked. The result is shared understanding, not implementation permission.

Output: resolved requirements and decisions suitable for a PRD.

### 2. PRD synthesis and approval

Owner entry: `$to-spec`.

`to-spec` synthesizes the current resolved conversation and relevant codebase understanding into `docs/product-specs/prd.md`; it does not restart the grilling interview. It uses the pinned upstream spec shape: problem statement, solution, user stories, implementation decisions, testing decisions, out of scope, and further notes.

Before writing, it presents the highest practical observable test seams for Owner confirmation. It does not initialize Harness state, write `TODO.json`, implement code, commit, push, or publish externally unless the Owner separately requests external issue publication.

Planning and implementation must not invent materially missing requirements. The Owner approves the PRD before execution planning.

### 3. Project initialization or adoption

Owner entry: `$init-project`.

`init-project` uses the packaged [scaffold](scaffold/) to create only missing project files. It initializes Git only when no repository exists, preserves project-owned docs and workflow state, and reports `created`, `replaced`, `skipped`, and `conflicts`. `--force` applies only to explicitly managed schemas and runtime adapters; it does not replace the PRD, TODO state, worklogs, evidence, checkpoints, project map, or project guidance.

Output: a dormant, navigable Harness project.

### 4. Execution-plan generation and approval

Owner entry: `$to-exec-plan`.

`to-exec-plan` reads the approved PRD and limited project navigation, then writes narrow Builder-ready features to `docs/exec-plans/active/TODO.json`. Each feature contains identity, goal, allowed and forbidden scope, assumptions, constraints, acceptance criteria, validation, explicit references, dependencies, expected wave, and conflict risk.

The planner validates that dependencies exist and are acyclic. It preserves mutable history when replanning. Human approval is required before full execution.

### 5. Orchestrator activation and preflight

Owner entry: `$orchestrator`, `/harness run`, or `/harness resume`.

Orchestrator allocates the run identity and validates:

- the static Harness closure exists, is committed, and matches the integration commit;
- the Codex runtime closure uses `.codex/agents/harness-builder.toml`, `harness-reviewer.toml`, and `harness-librarian.toml`;
- the selected feature's `docs/project-map.md` and explicit references are committed and unchanged;
- active TODO and worklog state exists, parses, and is writable;
- the installed Orchestrator, Builder, Reviewer, Librarian, and context helper are readable;
- selected features are dependency-ready and have no declared write or runtime conflict.

Preflight blocks with `harness_not_committed` instead of sending incomplete or dirty context into a worktree.

### 6. Wave selection and isolated worktrees

Orchestrator selects dependency-ready features. Parallel execution is allowed only when file and runtime ownership do not conflict. Every feature receives one branch, one `.worktrees/<feature-id>-<slug>` directory, one base SHA, and one Builder assignment.

Orchestrator owns worktree creation and cleanup. Builder never chooses its worktree. Dirty worktrees are preserved and surfaced to the Owner rather than force-removed.

### 7. Builder implementation and handoff

Delegated role: `builder`.

Builder accepts exactly one activation-envelope assignment and loads only that feature, its exact handoff events, `docs/project-map.md`, and explicit references. For behavior changes and bug fixes it uses red-to-green tests at the acceptance criterion's public seam when practical. It writes scoped implementation and tests, validates them, commits locally, and returns a normalized handoff with:

- feature identity and immutable feature-spec hash;
- branch, worktree, base and commit identities;
- changed files and dependencies;
- checked assumptions;
- validation and applicable TDD evidence;
- concrete review start command, URL, fixture, account, steps, and expected results;
- measured telemetry.

Builder does not update `TODO.json`, shared JSONL, acceptance status, or evidence.

### 8. Builder handoff preflight

Builder preflight is Orchestrator's mechanical trust boundary before independent review. It verifies that the returned feature ID/name/hash and assigned span match, the branch/worktree/commit are the assigned ones, changed files stay in scope, validation succeeded, telemetry is structurally valid, and a `ready-for-review` handoff conforms to [the Builder handoff schema](schemas/builder-handoff.schema.json).

This step answers “is the delivery mechanically reviewable?” It does not answer “does the feature satisfy acceptance criteria?” Reviewer owns that judgment. A malformed handoff is rejected and routed through the visible retry rules rather than passed to Reviewer.

### 9. Independent Reviewer acceptance

Delegated role: `reviewer`.

Reviewer resolves one exact persisted handoff and checks every acceptance criterion with real evidence. Runnable applications are started and exercised. Web/UI paths require browser automation, visible interactions, screenshots, and relevant console/network inspection. Text and contract work uses file inspection, residue scans, command output, and evidence pointers.

Evidence is stored only at `worklog/evidence/<feature-id>/` in the main control tree. Verdicts are:

- `passed`: every acceptance criterion passes with evidence;
- `failed`: implementation misses a repairable criterion;
- `handoff-rejected`: required review context is incomplete;
- `blocked`: the specification or environment prevents a fair review.

Reviewer does not modify product code or implementation tests.

### 10. Merge and global validation

Orchestrator validates the Reviewer verdict, merges passed feature commits serially, then runs project-wide validation. A feature becomes `passed` only after review, merge, and global validation all succeed.

Merge or global-validation failure preserves evidence and counts one failed delivery only after the integration base can be restored safely. If safe restoration cannot be proved, Orchestrator checkpoints and pauses.

### 11. Librarian maintenance

Delegated role: `librarian`.

After all successful integrations in a wave pass global validation, Librarian runs once for that wave. It updates `docs/project-map.md` as a navigation index and edits only durable documentation required by changed behavior, architecture, setup, public API, configuration, or domain language.

Librarian records evidence-backed wave-local duplication in the optimization-debt tracker. Deferred duplication does not block later waves; a demonstrated correctness or security risk pauses for Owner-approved planning.

### 12. Checkpoint and cleanup

Orchestrator stages only the exact Librarian paths, commits them when non-empty, removes only clean feature worktrees, and atomically writes `worklog/checkpoints/orchestrator.json`. The checkpoint records run/wave identity, base SHA, completed and next features, open worktrees, unresolved events, context state, and next action.

The checkpoint is resume data, not activation. A new task still requires `$orchestrator` or `/harness resume` from the Owner.

### 13. Retry, repair, blockers, and resume

- A failed delivery transition back to Builder increments `attemptCount` once.
- Builder missing context receives one automatic targeted repair without consuming an attempt.
- Repeated identical context failure checkpoints and pauses.
- `attemptCount == 3` becomes `blocked(retry_exhausted)` and requires Owner action.
- Interrupted or cancelled role work receives a terminal lifecycle event but consumes no attempt without a valid failed delivery.
- `/harness resume` loads authoritative TODO first, reconciles the latest checkpoint and actual worktrees, then recomputes the next wave.

### 14. Completion and archival

Owner entry: `$complete-project`, or delegation from the active Orchestrator.

Completion checks require quiescence, every feature `passed`, successful validation history, existing evidence, no unresolved formal handoff, a consistent checkpoint, and acceptable Git/worktree state. The skill writes a proposed completion report under `docs/generated/` and presents the exact archival action.

It does not commit, push, delete, move the active TODO, or claim closure. Only after a second explicit Owner confirmation may Orchestrator move the active plan to `docs/exec-plans/completed/` and initialize the next empty queue. Checks run again immediately before archival.

## Modules

| Module | Purpose | Authoritative path |
| --- | --- | --- |
| `grill-me` | Codex-compatible explicit entry for requirements grilling | [skills/grill-me](skills/grill-me/) |
| `grilling` | Owner-gated adapter to pinned upstream questioning behavior | [skills/grilling](skills/grilling/) |
| `to-spec` | Owner-gated synthesis of resolved discussion into the canonical PRD | [skills/to-spec](skills/to-spec/) |
| `init-project` | Non-destructive project initialization and adoption | [skills/init-project](skills/init-project/) |
| `to-exec-plan` | Approved PRD to dependency-aware active TODO JSON | [skills/to-exec-plan](skills/to-exec-plan/) |
| `orchestrator` | Activation, scheduling, assignments, integration, state, telemetry, and checkpoints | [skills/orchestrator](skills/orchestrator/) |
| `builder` | One-feature implementation, tests, validation, commit, and handoff | [skills/builder](skills/builder/) |
| `reviewer` | Independent acceptance judgment and durable evidence | [skills/reviewer](skills/reviewer/) |
| `librarian` | Post-validation project map and durable docs | [skills/librarian](skills/librarian/) |
| `complete-project` | Final consistency report and owner-gated archival | [skills/complete-project](skills/complete-project/) |
| Plugin metadata | Codex plugin identity and dormant default prompt | [.codex-plugin/plugin.json](.codex-plugin/plugin.json) |
| Marketplace metadata | GitHub-backed personal marketplace listing | [.agents/plugins/marketplace.json](.agents/plugins/marketplace.json) |
| `agents/` | Builder, Reviewer, and Librarian runtime identity/model adapters | [agents](agents/) |
| `scaffold/` | Project files installed by `init-project` | [scaffold](scaffold/) |
| `schemas/` | Builder handoff and lifecycle JSON contracts | [schemas](schemas/) |
| `scripts/install.py` | Transactional user install and ownership-checked upgrade | [scripts/install.py](scripts/install.py) |
| `scripts/doctor.py` | Prerequisite, bundle, and installed-state diagnostics | [scripts/doctor.py](scripts/doctor.py) |
| `scripts/uninstall.py` | Ownership-aware, fail-closed uninstall | [scripts/uninstall.py](scripts/uninstall.py) |
| `scripts/build_bundle.py` | Reproducible offline archive, checksum, and manifest | [scripts/build_bundle.py](scripts/build_bundle.py) |
| `scripts/bootstrap.sh` | Doctor, preview, and install convenience entry | [scripts/bootstrap.sh](scripts/bootstrap.sh) |
| `tests/` | Offline package, contract, installer, scaffold, bundle, and vendor tests | [tests](tests/) |

Repository shape:

```text
My_Codex_Harness/
├── .codex-plugin/plugin.json
├── .agents/plugins/marketplace.json
├── agents/
├── skills/
│   ├── grill-me/          ├── grilling/
│   ├── to-spec/           ├── init-project/
│   ├── to-exec-plan/      ├── orchestrator/
│   ├── builder/           ├── reviewer/
│   ├── librarian/
│   └── complete-project/
├── scaffold/
├── schemas/
├── scripts/
├── tests/
└── docs/
```

## State And Ownership

### Durable project state

| Path | Meaning | Writer |
| --- | --- | --- |
| `docs/exec-plans/active/TODO.json` | Only authoritative active workflow state | Orchestrator changes status/attempts; `to-exec-plan` creates approved definitions |
| `worklog/handoffs.jsonl` | Structured role handoffs, verdicts, failures, and repairs | Orchestrator only during a run |
| `worklog/logs/lifecycle.jsonl` | Timing/token observability; never workflow state | Orchestrator only during a run |
| `worklog/evidence/` | Acceptance and validation evidence | Reviewer targets this control-tree path; Orchestrator persists validated results |
| `worklog/checkpoints/` | Durable resume data | Orchestrator only |
| `docs/project-map.md` | Progressive-disclosure navigation index | Librarian after merged global validation |
| `docs/exec-plans/tech-debt-tracker.md` | Evidence-backed optimization candidates | Librarian |
| `docs/generated/` | Proposed completion and generated reports | Completion phase |
| `docs/exec-plans/completed/` | Owner-approved archived plans | Orchestrator only after final confirmation |

Lifecycle telemetry and checkpoints support decisions but never override `TODO.json`. Conversation history is not authoritative resume state.

### Role boundaries

| Role | May change | Must not change or decide |
| --- | --- | --- |
| Owner | Requirements, approvals, activation, gated risk decisions | Nothing is delegated implicitly |
| `to-spec` | Canonical PRD after test-seam confirmation | TODO state, product code, role events, external publication without explicit request |
| `to-exec-plan` | Feature definitions in active TODO | Product code, role events, status execution |
| Orchestrator | Status, attempts, shared events, assignments, merge, checkpoints | Business code, acceptance judgment |
| Builder | One assigned feature's source/tests and local commit | TODO status, shared JSONL, Reviewer evidence |
| Reviewer | Acceptance evidence and verdict | Product code, implementation tests, TODO status |
| Librarian | Project map, necessary durable docs, optimization debt | Product code, tests, TODO, shared JSONL |
| `complete-project` | Proposed completion report | Commit, push, delete, or archive before Owner confirmation |

## Timing And Token Telemetry

Each tracked run, wave, role invocation, merge, validation, retry, context repair, checkpoint, pause, and resume receives a unique `spanId`. Orchestrator writes one `started` and one terminal `finished` lifecycle event. The two persisted timestamps define the interval; the finished event contains measured `durationMs`.

Role results use this telemetry shape before Orchestrator validates and persists it:

```json
{
  "spanId": "span-builder-F001-01",
  "startedAt": "2026-07-14T03:00:00.000Z",
  "finishedAt": "2026-07-14T03:00:02.431Z",
  "durationMs": 2431,
  "tokens": {
    "input": null,
    "cachedInput": null,
    "output": null,
    "total": null
  },
  "outcome": "ready-for-review"
}
```

Time is measured, not guessed. Token fields use runtime-provided usage only. When the runtime does not expose input, cached-input, output, or total token usage, the correct value is `null`; Harness never estimates it from text length or price.

For Codex App, Orchestrator invokes Builder, Reviewer, and Librarian as native child tasks with `spawn_agent`; Harness does not launch Codex CLI processes. These child tasks remain visible in the App. Formal Builder retries start a new child task, while a context repair continues the same Builder task with `followup_task`.

Codex App does not currently expose per-role official Usage to Orchestrator. App-native role spans therefore keep measured duration but store all token fields as `null`, with `metadata.telemetryComplete = false` and `metadata.telemetryReason = "app_usage_unavailable"`; Harness never estimates token usage. Telemetry never changes TODO, acceptance, retry, merge, or validation results.

Use `worklog/logs/lifecycle.jsonl` for per-action duration analysis. Use TODO and handoffs—not telemetry—to determine workflow status.

A counted Builder retry starts a new Codex App child task while preserving the assigned worktree, branch, code, and durable rework handoffs. A valid `needs-rework(context)` repair keeps the same attempt and resumes the same child task with `followup_task`.

## Resume, Recovery, And Troubleshooting

| Symptom | Meaning and action |
| --- | --- |
| `blocked(harness_not_committed)` | Commit the static/runtime closure and selected feature references, then explicitly run `/harness resume` |
| Builder `needs-rework(context)` | Orchestrator resumes the same Builder session for one targeted context repair; repeated identical failure pauses for Owner action |
| Reviewer `handoff-rejected` | Builder handoff lacks a start command, fixture, expected result, account, or exact review context |
| Reviewer `failed` | One or more acceptance criteria failed; Orchestrator visibly routes one retry to Builder |
| `blocked(environment)` | A required observable capability such as browser automation is unavailable; do not substitute code reading |
| `blocked(retry_exhausted)` | Three failed deliveries were counted; Owner must change scope, environment, or plan before resuming |
| Role interrupted/cancelled | Orchestrator closes the lifecycle span and reconciles state; no attempt is consumed without a valid failed delivery |
| Dirty feature worktree | Harness preserves it and pauses; inspect or resolve it manually rather than force-removing it |
| Ordinary task still loads stale Harness behavior | Reload Codex or start a new task after install/upgrade; verify all skill metadata says implicit invocation is false |
| Installer reports a conflict | An existing path is unmanaged or locally modified; inspect it instead of forcing an overwrite |
| Install/uninstall recovery required | Re-run the same command so its journal can recover; do not delete journals or backups manually |
| `doctor.py --installed` fails | Read the exact ownership, hash, config, or cleanup error; resolve drift before upgrade/uninstall |

Resume only with an explicit Owner request:

```text
/harness resume
```

Orchestrator loads TODO first, reconciles the latest checkpoint with actual worktrees and handoffs, and recomputes runnable work. It never blindly replays conversation history or checkpoint instructions.

## Migration, Upgrade, Rollback, And Uninstall

### Build an offline migration bundle

From a clean reviewed source checkout:

```sh
python3 scripts/build_bundle.py --output dist
```

For version `0.1.0`, this produces:

- `dist/my-codex-harness-0.1.0.tar.gz`
- `dist/my-codex-harness-0.1.0.tar.gz.sha256`
- `dist/bundle-manifest.json`

Validate the archive before transfer:

```sh
python3 scripts/doctor.py --bundle dist/my-codex-harness-0.1.0.tar.gz
```

Copy the archive, `.sha256`, and `bundle-manifest.json` to the destination. Do not copy credentials, sessions, arbitrary `~/.codex` state, or project secrets.

After transfer, place the three files together, verify with `doctor.py --bundle`, extract the archive, enter `my-codex-harness-0.1.0/`, then run:

```sh
python3 scripts/doctor.py --require-codex
python3 scripts/install.py --dry-run
python3 scripts/install.py --yes
python3 scripts/doctor.py --installed
```

The bundle contains its own installer and requires no network access after transfer.

### Upgrade

Check out the reviewed newer source or extract its verified bundle, then repeat:

```sh
python3 scripts/doctor.py
python3 scripts/install.py --dry-run
python3 scripts/install.py --yes
python3 scripts/doctor.py --installed
```

Upgrade verifies the current ownership manifest and hashes before replacing managed paths. A locally modified managed file causes a fail-closed conflict. The installer does not reinterpret local drift as permission to overwrite it.

### Rollback and interrupted operations

Config changes receive timestamped backups. Install and uninstall operations write journals before mutation and use atomic replacement where practical. A pre-commit failure restores owned changes; a committed installation with incomplete cleanup remains installed and reports the remaining cleanup state. Re-running the operation resumes journal recovery.

Do not delete `.rollback-*`, `.stage-*`, journals, install state, or backups to silence an error. Run doctor and preserve unknown drift for inspection.

### Uninstall

Preview first:

```sh
python3 scripts/uninstall.py --dry-run
```

Then remove only manifest-owned, hash-matching paths and config keys:

```sh
python3 scripts/uninstall.py --yes
```

Uninstall refuses to delete drifted managed files. Target repositories initialized with `$init-project` are project-owned and are not removed by user-level uninstall.

## Development And Release Validation

Use Python 3.11 or newer:

```sh
python3 scripts/validate_package.py
python3 -m unittest discover -s tests -v
git diff --check
```

The suite validates plugin and marketplace metadata, skill and role contracts, upstream provenance, schemas, context selection, project initialization, installation ownership, rollback, uninstall, reproducible bundles, offline installation, and README links. It does not call a paid model.

Build and validate a release bundle with:

```sh
python3 scripts/build_bundle.py --output dist
python3 scripts/doctor.py --bundle dist/my-codex-harness-0.1.0.tar.gz
```

Project governance and reporting:

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
- [MIT license](LICENSE)
- [Third-party notices](NOTICE)
- [Pinned upstream skill provenance](docs/upstream-grill.md)
- [Owner-gated activation design](docs/superpowers/specs/2026-07-14-owner-gated-activation-and-readme-design.md)

## Known Boundaries

- Native `allow_implicit_invocation: false` plus text and assignment contracts govern activation. They are workflow controls, not a privileged operating-system or cryptographic security boundary.
- Harness does not automatically push, force-push, rewrite history, add dependencies, perform schema migrations, communicate externally, or incur paid-service costs.
- It never force-removes a dirty worktree or silently discards unrelated changes.
- Planning and plan approval require the Owner. Full execution is max-AFK only after explicit activation and within safety/correctness gates.
- Final archival always requires a separate explicit Owner confirmation.
- Runtime token usage may be unavailable. `null` is a valid measurement result; an estimate is not.
- A current Codex process may retain stale skill discovery data after files change. Reload or start a new task before evaluating live activation behavior.

## License And Third-Party Content

My Codex Harness is released under the [MIT License](LICENSE). The vendored `grill-me`, `grilling`, and `to-spec` workflows come from `mattpocock/skills` release `v1.1.0` and retain their MIT license and exact upstream source files. See [NOTICE](NOTICE) and [the upstream audit record](docs/upstream-grill.md).
