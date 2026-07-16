# Harness Efficiency Without Functional Drift Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with RED→GREEN TDD. The Owner explicitly requested inline implementation in the current session, excluded `superpowers:executing-plans`, and did not authorize subagent-driven implementation.

**Goal:** Reduce deterministic Harness context and orchestration overhead without weakening feature hashes, role isolation, review evidence, retries, or global validation.

**Architecture:** Keep semantic workflow decisions in Orchestrator/Builder/Reviewer/Librarian. Add one dependency-free control helper for closure checks and lifecycle appends, and extend the existing context helper with immutable assignments and verbatim Markdown-section resolution. Preserve legacy commands and full-document fallback.

**Tech Stack:** Python 3.11+ standard library, `unittest`, Markdown skill contracts, Git worktrees, existing package/bundle/installer tooling.

---

### Task 1: Immutable assignment and verbatim Markdown sections

**Files:**
- Modify: `tests/test_contracts.py`
- Modify: `skills/orchestrator/scripts/harness_context.py`

- [ ] **Step 1: Add failing assignment test**

Add a feature containing immutable fields plus `status`, `attemptCount`, `handoffReferences`, and `validationHistory`. Invoke:

```python
result = self.run_helper("assignment", "--id", "F1")
self.assertEqual(0, result.returncode, result.stderr)
assignment = json.loads(result.stdout)
self.assertEqual("F1", assignment["feature"]["id"])
self.assertEqual(["works"], assignment["feature"]["acceptanceCriteria"])
for field in ("status", "attemptCount", "handoffReferences", "validationHistory"):
    self.assertNotIn(field, assignment["feature"])
self.assertEqual(
    json.loads(self.run_helper("feature", "--id", "F1").stdout)["featureSpecSha256"],
    assignment["featureSpecSha256"],
)
self.assertNotIn("Sibling secret", result.stdout)
```

- [ ] **Step 2: Run the assignment test and verify RED**

Run:

```bash
python3.12 -m unittest tests.test_contracts.ContextHelperTests.test_assignment_excludes_mutable_history_without_changing_hash -v
```

Expected: failure because `assignment` is not a recognized command.

- [ ] **Step 3: Implement the minimal assignment command**

Add:

```python
def immutable_feature(feature: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in feature.items() if key not in MUTABLE_FEATURE_FIELDS}
```

Reuse it in `feature_hash`, and add an `assignment` parser branch that calls `select_feature` and replaces `feature` with the immutable projection. Keep `feature` output byte-compatible.

- [ ] **Step 4: Verify assignment GREEN and regression suite**

Run the focused test, then:

```bash
python3.12 -m unittest tests.test_contracts.ContextHelperTests -v
```

Expected: all context-helper tests pass.

- [ ] **Step 5: Add failing Markdown-section tests**

Commit a fixture file with `## Entry Points`, `## Transaction Events`, a nested `### Tests`, and `## Next Domain`. Test:

```python
result = self.run_helper(
    "section", "--reference", "docs/project-map.md#transaction-events", "--base-sha", base_sha,
)
section = json.loads(result.stdout)
self.assertEqual("docs/project-map.md#transaction-events", section["reference"])
self.assertEqual(
    "## Transaction Events\nbody\n### Tests\nnested\n",
    section["content"],
)
self.assertEqual(len(section["content"].encode()), section["byteCount"])
self.assertRegex(section["fileSha256"], r"^[0-9a-f]{64}$")
```

Also test missing anchors, duplicate normalized anchors, path escape, base-byte drift, and a `--legacy-full-fallback` call returning the full document.

- [ ] **Step 6: Run Markdown tests and verify RED**

Expected: failure because `section` is not recognized.

- [ ] **Step 7: Implement exact section resolution**

Add safe reference parsing, GitHub-style heading normalization, committed-byte comparison, and slice boundaries. The implementation must:

```python
def normalize_anchor(text: str) -> str:
    value = text.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return value.replace(" ", "-")
```

Parse ATX headings, find exactly one normalized anchor, and return source lines through the next heading with level less than or equal to the selected heading. The full-file fallback is allowed only when the explicit flag is present.

- [ ] **Step 8: Verify Markdown GREEN and commit**

Run all context-helper tests, then commit:

```bash
git add tests/test_contracts.py skills/orchestrator/scripts/harness_context.py
git commit -m "feat: add exact harness context assignments"
```

### Task 2: Deterministic closure preflight and lifecycle spans

**Files:**
- Create: `skills/orchestrator/scripts/harness_control.py`
- Modify: `tests/test_contracts.py`

- [ ] **Step 1: Add failing lifecycle start/finish test**

Create a main Git repository fixture containing the lifecycle schema and an empty `worklog/logs/lifecycle.jsonl`. Invoke `lifecycle-start`, parse its compact response, then invoke `lifecycle-finish` with the returned span. Assert two JSONL events, matching span/run/feature identities, `durationMs >= 0`, and `tokens` equal to the requested zero/null shape.

- [ ] **Step 2: Run the focused test and verify RED**

Expected: failure because `harness_control.py` does not exist.

- [ ] **Step 3: Implement minimal safe lifecycle append**

Implement:

```python
def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_APPEND
    descriptor = os.open(path, flags)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
```

Before opening, reject symlinks and non-regular files. Start allocates UUID/timestamp; finish loads the unique start, rejects an existing terminal event, calculates RFC3339 elapsed milliseconds, and appends one terminal event. Emit only `{eventId, spanId, timestamp, durationMs?, status?}`.

- [ ] **Step 4: Verify lifecycle GREEN**

Run the focused test and all control-helper tests.

- [ ] **Step 5: Add failing lifecycle safety tests**

Cover missing start, duplicate start, duplicate terminal, identity mismatch, malformed JSONL, symlink destination, linked worktree root, and invalid action/actor/status. Capture bytes before each invocation and assert failure leaves them unchanged.

- [ ] **Step 6: Implement bounded validation and symbolic exits**

Use exit codes compatible with the context helper categories where possible. Validate only the fixed lifecycle schema fields the helper creates; do not build a generic JSON Schema engine.

- [ ] **Step 7: Add failing closure preflight tests**

Create committed static/runtime/reference fixtures. Assert `preflight` passes and reports checked paths/byte count. Then modify one path and assert `HARNESS_NOT_COMMITTED` with no workflow-state writes. Test missing path, unsafe path, and unknown runtime.

- [ ] **Step 8: Implement closure preflight**

For each fixed and explicit path, run `git cat-file -e <base>:<path>`, compare committed blob bytes with live regular-file bytes, and return a compact result. Do not stage, commit, or mutate project files.

- [ ] **Step 9: Verify control helper and commit**

Run focused tests and commit:

```bash
git add tests/test_contracts.py skills/orchestrator/scripts/harness_control.py
git commit -m "feat: automate harness control mechanics"
```

### Task 3: Contract wiring and no-drift assertions

**Files:**
- Modify: `skills/orchestrator/SKILL.md`
- Modify: `skills/builder/SKILL.md`
- Modify: `skills/reviewer/SKILL.md`
- Modify: `skills/to-exec-plan/SKILL.md`
- Modify: `tests/test_contracts.py`

- [ ] **Step 1: Add failing skill-contract tests**

Assert that Orchestrator resolves both helpers, uses `harness_control.py` for preflight and lifecycle operations, requires immediate safe progression, and retains `spawn_agent` formal retry plus `followup_task` context repair. Assert Builder/Reviewer use `assignment`, exact `section`, and exact `handoff` commands. Assert `to-exec-plan` rejects universal `docs/project-map.md#entry-points` placeholder guidance and requires narrow authoritative anchors.

- [ ] **Step 2: Run tests and verify RED**

Expected: failures for missing helper and outdated commands.

- [ ] **Step 3: Update role contracts minimally**

Replace mechanical prose with exact helper commands while retaining activation, ownership, accepted outcomes, retry ceilings, Reviewer independence, merge/global-validation gates, and nullable token semantics. Do not change role model settings or schemas.

- [ ] **Step 4: Verify contract GREEN and no-drift assertions**

Run `SkillContractTests`, `ContextHelperTests`, and `SchemaContractTests`.

- [ ] **Step 5: Commit contract wiring**

```bash
git add skills/orchestrator/SKILL.md skills/builder/SKILL.md skills/reviewer/SKILL.md skills/to-exec-plan/SKILL.md tests/test_contracts.py
git commit -m "refactor: reduce harness orchestration context"
```

### Task 4: Deterministic efficiency benchmark

**Files:**
- Create: `tests/test_efficiency.py`
- Create: `tests/fixtures/efficiency/feature.json`
- Create: `tests/fixtures/efficiency/project-map.md`
- Create: `tests/fixtures/efficiency/handoffs.jsonl`

- [ ] **Step 1: Add failing F002-shaped benchmark**

Build six invocation byte totals from role Skill bytes, immutable assignment output, exact map-section bytes, policy bytes for Builder, exact selected handoff/verdict bytes, and bounded control output. Assert:

```python
self.assertEqual(6, result["roleInvocations"])
self.assertEqual(["failed", "failed", "passed"], result["reviewOutcomes"])
self.assertLessEqual(result["optimizedBytes"], 74_538)
self.assertGreaterEqual(result["reductionPercent"], 30.0)
```

- [ ] **Step 2: Run and verify RED**

Expected: benchmark exceeds threshold before context slicing is used.

- [ ] **Step 3: Use exact assignment/section outputs in fixture accounting**

Keep the 106,483-byte observed baseline constant and report both measured optimized bytes and percentage. Do not call bytes “official tokens.”

- [ ] **Step 4: Verify GREEN and commit**

```bash
git add tests/test_efficiency.py tests/fixtures/efficiency
git commit -m "test: benchmark harness context reduction"
```

### Task 5: Packaging, scaffold, documentation, and compatibility

**Files:**
- Modify: `tests/test_package.py`
- Modify: `tests/test_init_project.py`
- Modify: `tests/test_bundle.py`
- Modify: `tests/test_install.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify when required by parity tests: `scaffold/**`

- [ ] **Step 1: Add failing distribution parity tests**

Assert both helpers are tracked, regular, executable Python files under the Orchestrator Skill, included in the bundle manifest, present after temporary installation, and hash-identical in the user skill discovery path. Assert initialized projects keep runtime adapters synchronized without copying user-level helper code into the project.

- [ ] **Step 2: Run and verify RED**

Expected: new helper packaging assertions fail.

- [ ] **Step 3: Update package inclusion and documentation**

Prefer existing recursive package discovery; change package code only if the failing test proves exclusion. Document exact-context fallback, deterministic proxy metrics, lifecycle helper commands, immediate safe progression, and unchanged no-drift gates. Add an Unreleased changelog item.

- [ ] **Step 4: Run focused package/install/bundle tests**

Use the bundled Python 3.12 runtime. Expected: all focused tests pass.

- [ ] **Step 5: Commit distribution changes**

```bash
git add README.md CHANGELOG.md tests/test_package.py tests/test_init_project.py tests/test_bundle.py tests/test_install.py scaffold
git commit -m "docs: ship optimized harness workflow"
```

### Task 6: Completion audit, merge, push, installation, and F003 handoff

**Files:**
- Verify all files changed by Tasks 1-5
- Update user-level installation through existing installer
- Update the personal-finance project through `init-project --force`

- [ ] **Step 1: Run complete repository verification**

```bash
PY=/Users/zhiqibao/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
$PY -m unittest discover -s tests -v
$PY scripts/validate_package.py
$PY scripts/doctor.py
```

Expected: zero failures and successful package/doctor reports.

- [ ] **Step 2: Build and inspect a temporary bundle/install**

Build to a temporary directory, run bundle doctor, install with a temporary `HOME`, initialize a temporary Git project, and hash-compare both helpers and role contracts across source, bundle, installed package, discovery links, and initialized runtime closure.

- [ ] **Step 3: Run the deterministic benchmark and audit fidelity**

Require at most 74,538 optimized bytes, six role calls, outcomes `failed, failed, passed`, unchanged feature hash/ACs, separate Reviewer, new-task formal retries, same-task context repair, and unchanged global validation.

- [ ] **Step 4: Merge feature branch into main and push**

Fast-forward main only after the worktree is clean and all verification is fresh:

```bash
git -C ../.. merge --ff-only feature/harness-efficiency-no-drift
git -C ../.. push origin main
```

- [ ] **Step 5: Install the pushed source to user level**

Use existing doctor preview/install flow with the compatible Python runtime. Verify installed hashes and user discovery links.

- [ ] **Step 6: Update the personal-finance project scaffold**

Run `init-project --force` through the installed Skill against the project, review the exact managed-file changes, and commit only the required closure updates because formal Harness preflight requires committed bytes.

- [ ] **Step 7: Start F003 only through explicit Harness activation**

Run only F003 after the new installed Orchestrator is discoverable. Preserve F001/F002 passed state, create an isolated F003 worktree, execute Builder→Reviewer→merge→global validation→Librarian, and stop after F003. Record duration/proxy metrics and compare them with the documented baseline without fabricating token counts.
