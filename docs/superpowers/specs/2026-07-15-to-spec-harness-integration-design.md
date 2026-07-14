# Harness `to-spec` Integration Design

## Problem

The published Harness workflow jumps from requirements grilling to a manually authored PRD. Matt Pocock's current upstream workflow uses `to-spec`, not `to-prd`, to synthesize the current conversation and codebase context into a spec/PRD. Harness is missing that explicit phase.

## Decision

Add one Owner-invoked public skill named `to-spec`. Do not add or retain a public `to-prd` alias.

The skill vendors the current Matt Pocock `to-spec` contract and MIT license unchanged under `skills/to-spec/upstream/`. A small Codex/Harness wrapper adds only the local activation and output boundaries.

## Workflow

The complete requirements-to-execution path is:

1. `$grill-me` or `$grilling` resolves requirements and design decisions.
2. `$to-spec` synthesizes the current conversation and relevant codebase context into `docs/product-specs/prd.md`.
3. The Owner reviews and approves the PRD.
4. `$init-project` initializes missing Harness structure when required and preserves an existing PRD.
5. `$to-exec-plan` converts the approved PRD into `docs/exec-plans/active/TODO.json`.
6. `$orchestrator`, `/harness run`, or `/harness resume` executes the approved plan.
7. `$complete-project` verifies and proposes completion.

`$init-project` may run before grilling in an already chosen repository. The invariant is that an approved PRD exists before `$to-exec-plan`.

## `to-spec` Contract

- Activation requires the human Owner to invoke `$to-spec` in the current top-level request.
- Commands found in files, quoted content, tool output, generated content, or subagent messages do not activate it.
- Codex metadata sets `allow_implicit_invocation: false`.
- The skill synthesizes already discussed requirements; it does not restart the grilling interview.
- It may inspect the repository to ground the spec in current domain language, ADRs, modules, interfaces, and existing test seams.
- It presents proposed high-level observable test seams to the Owner before publishing the PRD.
- Its canonical Harness output is `docs/product-specs/prd.md`.
- It does not initialize Harness state, write `TODO.json`, implement product code, dispatch roles, write lifecycle events, commit, push, or publish to an external issue tracker.
- External issue publication requires a separate explicit Owner request.
- Its next public entry point is `$to-exec-plan` after PRD approval.

## PRD Shape

Preserve the upstream sections:

- Problem Statement
- Solution
- User Stories
- Implementation Decisions
- Testing Decisions
- Out of Scope
- Further Notes

Implementation decisions describe modules, interfaces, architectural choices, schemas, contracts, and interactions without brittle file-by-file instructions or working code snippets. A small decision-rich prototype fragment is allowed only when prose would be less precise.

## Distribution And Installation

Add:

- `skills/to-spec/SKILL.md`
- `skills/to-spec/agents/openai.yaml`
- `skills/to-spec/upstream/SKILL.md`
- `skills/to-spec/UPSTREAM.md`
- `skills/to-spec/LICENSE.upstream`

The existing package, bundle, installer, doctor, and uninstall flows discover the skill through the package skill tree. Their contract tests must include `to-spec` so omissions fail closed.

Update the plugin prompt, scaffold activation list, README workflow/module documentation, and stale `/run` references. Do not add a new runtime agent because `to-spec` is a public phase executed by the main Owner thread.

## Validation

- Contract tests fail before the skill exists and pass after integration.
- Vendored upstream hash and provenance are pinned.
- Every public skill remains dormant by default.
- Package and bundle tests prove `to-spec` is installed and validated with the other skills.
- README contains the complete `$grill-me` -> `$to-spec` -> `$to-exec-plan` -> execution path and contains no public `$to-prd` or `/run` command.
- Full unit suite and package validator pass.

## Non-goals

- Replacing `to-exec-plan` with Matt Pocock's `to-tickets`.
- Adding external issue-tracker integration.
- Automatically chaining user-invoked phases.
- Changing Builder, Reviewer, Librarian, or Orchestrator ownership.
- Preserving `to-prd` as a compatibility alias.

## Risks And Controls

- Upstream drift: pin source tag/commit, original path, license, and content hash.
- Accidental Harness activation: use an exact Owner gate and disabled implicit invocation.
- Duplicate PRD locations: use only `docs/product-specs/prd.md` as the Harness canonical output.
- Scope expansion into planning or implementation: enforce explicit phase boundaries in the skill and tests.
