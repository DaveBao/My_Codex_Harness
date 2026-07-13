# My Codex Harness Distribution Design

## Problem

Codex can run skills and specialized subagents, but a complete engineering workflow still needs durable contracts for discovery, planning, isolated implementation, independent acceptance, integration, observability, documentation, completion, installation, and migration. The existing `harness_v2` repository contains validated contracts and a successful golden run, but it also contains staged development history and project-specific material that should not become a reusable public package.

`My_Codex_Harness` will be a clean, versioned distribution that takes a user from rigorous requirements discovery through a completed and archived project. It must install globally without overwriting unrelated Codex configuration and must move safely to another machine or environment without copying credentials or private runtime state.

## Success Criteria

- A new user can understand the product, trust boundaries, and full workflow from `README.md`.
- One command installs the packaged workflow for the current user after a dry-run and prerequisite check.
- An offline bundle can be exported on one machine, copied to another, verified by checksum, and installed without network access.
- Installation is idempotent, records ownership, backs up modified configuration, and supports safe rollback and uninstall.
- Builder, Reviewer, and Librarian are available as global custom Codex agents.
- The complete skill sequence is available globally: `grill-me`, `grilling`, `init-project`, `to-exec-plan`, `orchestrator`, `builder`, `reviewer`, `librarian`, and `complete-project`.
- A target repository can be initialized with project guidance, product-spec inputs, active execution state, schemas, worklogs, checkpoints, evidence directories, and Codex runtime adapters.
- A project can move from grilled requirements to an approved PRD, DAG plan, isolated build, independent review, merge, global validation, documentation maintenance, durable checkpoint, final report, and approved archival.
- The package validates manifests, skill metadata, agent files, schemas, documentation links, scaffold parity, installer behavior, and a mechanical golden path without invoking a paid model.
- The initial public release is committed and pushed to `https://github.com/DaveBao/My_Codex_Harness.git` without rewriting history.

## Users

- A single developer installing a personal workflow across projects.
- A developer moving their workflow between macOS, Linux, WSL, or native Windows environments with Python 3.
- A team evaluating the repository before adopting the plugin or copying the scaffold into a project.

## Scope

### Distribution

- A Codex plugin manifest and repo marketplace entry.
- User-level skill discovery using official Codex locations.
- Global custom-agent definitions for Builder, Reviewer, and Librarian.
- A non-destructive Python standard-library installer, uninstaller, doctor, verifier, and offline-bundle builder.
- Release metadata, checksums, changelog, contribution, security, and third-party notices.

### Workflow

- Requirements discovery with vendored `grill-me` and `grilling`.
- Project initialization and progressive-disclosure navigation.
- PRD-to-execution-plan conversion.
- Orchestrated dependency waves with one feature per isolated worktree.
- Test-first Builder delivery and schema-valid handoff.
- Independent Reviewer acceptance with evidence stored under the main control root.
- Serialized merge and global validation.
- Wave-scoped Librarian navigation maintenance and optimization-debt reporting.
- Final completion verification, report generation, and human-approved active-plan archival.
- Lifecycle timing and runtime-provided token fields, using `null` when token usage is unavailable.

### Documentation

- Root README with product overview, five-minute quickstart, workflow diagram, installation choices, example, and links.
- Architecture, complete workflow, role boundaries, state model, observability, installation, migration, upgrade, uninstall, troubleshooting, security, completion, and authoring guides.
- A minimal example project and expected golden-run artifacts.

## Non-goals

- Shipping historical archives, legacy Claude-only material, prior review transcripts, or experimental project code from `harness_v2`.
- Replacing Codex's native subagent scheduler, permissions, sandbox, Git, or worktree implementation.
- Copying ChatGPT/Codex authentication, API keys, MCP credentials, secrets, sessions, memories, connector state, or arbitrary `~/.codex/config.toml` content between environments.
- Automatically force-pushing, deleting repositories, rewriting Git history, or publishing releases without explicit owner approval.
- Calling paid models in package tests or continuous integration.
- Supporting recursive subagent fan-out beyond depth one in the initial release.

## Architecture

The repository is both the source package and a personal Codex marketplace. Skills remain small role or phase contracts. Codex-native custom-agent TOML files configure model, reasoning, sandbox, and role identity; they do not duplicate behavior contracts. The project scaffold contains only project-owned navigation, workflow state, schemas, and empty worklog channels.

The main Codex thread acts as Orchestrator. It creates and assigns isolated worktrees, invokes the Builder and Reviewer agents, validates their normalized results, serializes merges and workflow-state updates, invokes Librarian once per successful wave, and writes checkpoints. Builder writes one feature's source and tests. Reviewer writes only durable acceptance evidence. Librarian writes only navigation and necessary durable documentation. `complete-project` verifies quiescence and prepares final artifacts; it requires owner confirmation before archiving active workflow state.

## Repository Layout

```text
My_Codex_Harness/
├── README.md
├── LICENSE
├── NOTICE
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── .codex-plugin/plugin.json
├── .agents/plugins/marketplace.json
├── skills/
│   ├── grill-me/
│   ├── grilling/
│   ├── init-project/
│   ├── to-exec-plan/
│   ├── orchestrator/
│   ├── builder/
│   ├── reviewer/
│   ├── librarian/
│   └── complete-project/
├── agents/
│   ├── harness-builder.toml
│   ├── harness-reviewer.toml
│   └── harness-librarian.toml
├── scaffold/
│   ├── AGENTS.md
│   ├── CLAUDE.md
│   ├── .codex/
│   ├── docs/
│   └── worklog/
├── schemas/
├── scripts/
├── examples/minimal-project/
├── docs/
├── tests/
└── .github/workflows/
```

## End-to-End Workflow

1. `grill-me` invokes the reusable `grilling` loop to resolve the decision tree through one focused question at a time.
2. The approved output becomes `docs/product-specs/prd.md`; shared terminology and durable design decisions are recorded in the project documentation only when needed.
3. `init-project` creates or adopts the progressive-disclosure scaffold non-destructively.
4. `to-exec-plan` converts the approved PRD into narrow vertical features in `docs/exec-plans/active/TODO.json`, validates the DAG, identifies conflicts, and requests human approval.
5. Orchestrator selects dependency-ready waves and binds each feature to one branch, worktree, base SHA, and Builder thread.
6. Builder follows the project Codex policy, writes a failing test when practical, implements the minimum change, validates it, commits it, and returns a schema-valid handoff.
7. Orchestrator performs Builder preflight: feature identity/hash, handoff schema, branch/worktree/commit, allowed file scope, and feature validation.
8. Reviewer resolves the exact persisted handoff, performs independent observable-behavior checks, and stores evidence at `$controlRoot/worklog/evidence/<feature-id>/`.
9. Orchestrator validates the verdict, merges passed work serially, runs global validation, and only then marks the feature passed.
10. Librarian updates project navigation and necessary durable docs, and records wave-local duplicate implementation candidates.
11. Orchestrator removes only clean worktrees, writes the wave checkpoint, and continues until quiescent.
12. `complete-project` verifies that every feature passed, validations and evidence exist, no unresolved event remains, and the repository is in an acceptable state. It generates a completion report and requests owner confirmation before moving the completed active plan into the completed archive and resetting the active queue.

## State And Observability

- `docs/exec-plans/active/TODO.json` is the only active workflow state.
- Only Orchestrator mutates feature status and attempt count.
- `worklog/handoffs.jsonl` contains durable role handoffs and verdicts.
- `worklog/logs/lifecycle.jsonl` contains telemetry, never workflow state.
- Every tracked action has one `started` and one terminal `finished` event with measured `durationMs`.
- Token fields are populated only from runtime-provided counts; unavailable counts remain `null`.
- `worklog/checkpoints/orchestrator.json` is the durable resume point.
- Completion reports live under `docs/generated/`; completed plans live under `docs/exec-plans/completed/` only after owner approval.

## Installation Model

The canonical installed package lives at `~/.codex/plugins/my-codex-harness/`. The installer creates user skill-discovery links under `~/.agents/skills/` pointing to the package skills because that is the documented user skill location and Codex supports symlinked skill directories. On systems where symlinks are unavailable, the installer copies skills and records their hashes in the ownership manifest.

Agent files are copied to `~/.codex/agents/harness-*.toml`. The installer merges only the missing `[agents]` keys `max_threads = 6`, `max_depth = 1`, and `interrupt_message = true` into `~/.codex/config.toml`. Existing values are preserved and reported. Any config mutation is preceded by a timestamped backup.

Installation state is stored at `~/.codex/my-codex-harness/install-state.json`. It contains package version, source commit, install mode, created paths, replaced paths and backups, hashes, and config keys added by the installer. Uninstall removes only paths and keys owned by this manifest.

## One-Command And Portable Deployment

### Online install

The documented safe path clones or downloads a tagged release, verifies the included checksum manifest, runs `python3 scripts/doctor.py`, previews with `python3 scripts/install.py --dry-run`, and installs with `python3 scripts/install.py --yes`. A convenience `bootstrap.sh` performs the same steps but never suppresses checksum or prerequisite failures.

### Offline migration

`python3 scripts/build_bundle.py --output <directory>` creates a versioned archive, checksum file, and machine-readable bundle manifest containing only Harness-owned plugin files, agents, scripts, scaffold, schemas, documentation, and the minimal desired `[agents]` settings. The archive contains its own installer and verifier.

On another machine, the user copies the archive and checksum, verifies them, extracts the archive, runs `doctor.py`, previews installation, and installs. The process requires no network access.

### Portability and rollback guarantees

- macOS, Linux, WSL, and native Windows are supported when Python 3 and a compatible Codex client are available.
- Symlink installation is preferred; Windows or restricted filesystems fall back to owned copies.
- Paths use `pathlib` and platform-native user-home resolution.
- Install, upgrade, and uninstall are idempotent.
- Interrupted installation rolls back newly created paths and restores the config backup.
- Upgrade validates ownership hashes before replacing managed files; locally modified managed files cause a fail-closed conflict unless explicitly backed up and accepted.
- No credentials or unrelated global configuration enter a migration bundle.

## Third-Party Content

`grill-me` and `grilling` are vendored from `mattpocock/skills` release `v1.1.0`, resolved commit `d574778f94cf620fcc8ce741584093bc650a61d3`. Their upstream MIT license, source URL, release, commit, and unmodified or modified status are recorded in `NOTICE` and the vendored directories. Harness-specific integration changes are kept as small wrappers or clearly documented patches so upstream updates remain auditable.

## Error Handling

- Existing target paths with different content are conflicts, not silent overwrites.
- Missing prerequisites stop before mutation and list exact remediation.
- Invalid manifests, checksums, schemas, TOML, JSON, skill front matter, or broken documentation links fail validation.
- Partial installation triggers rollback from the operation journal.
- Uninstall refuses to delete paths whose current hash differs from the owned installed hash; it reports them for manual resolution.
- Harness role failures follow the existing retry, context-repair, and terminal-blocker contracts; no step fabricates progress.

## Testing Strategy

- Unit tests for manifest parsing, config merge, install ownership, conflict detection, rollback, uninstall, bundle creation, and checksum verification.
- Contract tests for skill front matter, agent required fields, schemas, mutable TODO fields, helper feature selection, exact handoff selection, and lifecycle pairing.
- Scaffold tests that initialize a temporary Git repository and verify every expected path without overwriting project-owned files.
- Installer tests against temporary HOME directories for first install, repeat install, upgrade, conflict, copy fallback, uninstall, and offline bundle install.
- Documentation tests for local links, command paths, example consistency, and required top-level documents.
- A mechanical golden-path fixture that moves a sample feature through normalized handoff, review, merge, global validation, documentation, checkpoint, and completion states without invoking a model.
- GitHub Actions run the full offline test suite on macOS, Linux, and Windows.

## Release And Git Publication

- Development occurs in the new local clone of `DaveBao/My_Codex_Harness`.
- Commits are small and logically scoped: design, package structure, workflow contracts, installer/migration, docs/examples, and validation.
- Before each commit, the staged path set and diff are reviewed.
- Before the first push, the full validation suite, secret scan, license/notice check, and clean-tree check must pass.
- The initial branch is pushed normally to `origin`; no force push or history rewrite is used.
- Versioned GitHub release bundles and checksums are created only after a later explicit release action.

## Risks And Mitigations

- Global-name collisions: installed agents and support files use the `harness-` prefix; skill conflicts are surfaced before linking.
- Upstream `grill-me` drift: content is pinned and attributed; updates require an explicit audited vendor refresh.
- Context growth: roles remain separate progressive-disclosure skills and assignments contain references rather than full documents.
- Parallel write conflicts: one feature owns one worktree; wave selection excludes shared-file and runtime-resource conflicts.
- Runtime token unavailability: record `null`, never estimates.
- Configuration loss: minimal merge, timestamped backup, operation journal, ownership manifest, and fail-closed conflicts.

## Open Questions

None. The owner approved a plugin-first independent repository, vendored pinned `grill-me`/`grilling`, global custom agents, complete documentation, portable one-command deployment, and publication to the specified empty GitHub repository.
