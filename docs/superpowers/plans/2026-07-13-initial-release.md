# My Codex Harness Initial Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a complete, portable Codex engineering harness that installs globally, initializes projects, runs the approved multi-agent workflow, migrates offline, and documents every step from `grill-me` to project completion.

**Architecture:** The repository is a personal Codex plugin and marketplace source. Workflow behavior lives in focused skills; custom-agent TOML files configure Builder, Reviewer, and Librarian; deterministic Python standard-library scripts own validation, installation, migration, rollback, and scaffold initialization. The installed package is canonical under `~/.codex/plugins/my-codex-harness`, with official user skill-discovery links under `~/.agents/skills`.

**Tech Stack:** Markdown, JSON Schema Draft 2020-12, TOML, YAML, Python 3 standard library, Git, GitHub Actions

---

## File Responsibilities

- `.codex-plugin/plugin.json`: plugin identity, version, and skills root.
- `.agents/plugins/marketplace.json`: repository marketplace metadata.
- `skills/*/SKILL.md`: one workflow phase or role contract per directory.
- `skills/*/agents/openai.yaml`: skill UI metadata and invocation policy.
- `agents/harness-*.toml`: global custom-agent runtime configuration.
- `scaffold/`: files installed into a target project by `init-project`.
- `schemas/`: canonical handoff and lifecycle schemas mirrored into the scaffold.
- `scripts/package_model.py`: shared manifest, hash, path, and config-merge primitives.
- `scripts/install.py`: dry-run, install, upgrade, ownership journal, and rollback.
- `scripts/uninstall.py`: ownership-aware safe removal and config restoration.
- `scripts/doctor.py`: prerequisite and installation diagnostics.
- `scripts/build_bundle.py`: reproducible offline archive, manifest, and checksum generation.
- `scripts/validate_package.py`: deterministic package and cross-reference validation.
- `tests/`: unit, contract, scaffold, installer, bundle, documentation, and golden-path tests.
- `docs/`: user and maintainer documentation.
- `examples/minimal-project/`: complete smallest-project walkthrough and expected artifacts.

### Task 1: Package skeleton and deterministic validator

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `NOTICE`
- Create: `CHANGELOG.md`
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`
- Create: `.gitignore`
- Create: `.codex-plugin/plugin.json`
- Create: `.agents/plugins/marketplace.json`
- Create: `scripts/package_model.py`
- Create: `scripts/validate_package.py`
- Create: `tests/test_package.py`

- [ ] **Step 1: Write failing package-contract tests**

```python
class PackageContractTests(unittest.TestCase):
    def test_plugin_manifest_identifies_skills_root(self):
        manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(manifest["name"], "my-codex-harness")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["skills"], "./skills/")

    def test_marketplace_points_to_repository_plugin(self):
        marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
        self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./")

    def test_required_top_level_documents_exist(self):
        for name in ["README.md", "LICENSE", "NOTICE", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md"]:
            self.assertTrue((ROOT / name).is_file(), name)
```

- [ ] **Step 2: Run the tests and confirm the package is absent**

Run: `python3 -m unittest tests.test_package -v`

Expected: FAIL because the manifest and top-level documents do not exist.

- [ ] **Step 3: Add the minimum valid package metadata**

Use plugin version `0.1.0`, MIT licensing for original Harness content, repository URL `https://github.com/DaveBao/My_Codex_Harness`, marketplace category `Developer Tools`, and plugin skills root `./skills/`.

- [ ] **Step 4: Implement shared deterministic primitives**

`package_model.py` must expose:

```python
def sha256_file(path: Path) -> str: ...
def safe_relative_path(root: Path, candidate: Path) -> str: ...
def load_json(path: Path) -> dict: ...
def atomic_write(path: Path, content: str) -> None: ...
def parse_agents_table(config_text: str) -> dict[str, object]: ...
def merge_agents_table(config_text: str, desired: dict[str, object]) -> tuple[str, dict[str, object]]: ...
```

Use only Python's standard library. Reject paths outside the supplied root.

- [ ] **Step 5: Implement and run package validation**

Run: `python3 scripts/validate_package.py`

Expected: `package validation passed` and exit 0.

- [ ] **Step 6: Run all current tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: all Task 1 tests pass.

- [ ] **Step 7: Review and commit Task 1**

Commit message: `chore: scaffold harness distribution package`

### Task 2: Vendor pinned grill workflow with attribution

**Files:**
- Create: `skills/grill-me/`
- Create: `skills/grilling/`
- Modify: `NOTICE`
- Create: `docs/upstream-grill.md`
- Create: `tests/test_vendor.py`

- [ ] **Step 1: Write failing provenance tests**

```python
PINNED_COMMIT = "d574778f94cf620fcc8ce741584093bc650a61d3"

def test_grill_skills_and_provenance_exist(self):
    self.assertTrue((ROOT / "skills/grill-me/SKILL.md").is_file())
    self.assertTrue((ROOT / "skills/grilling/SKILL.md").is_file())
    notice = (ROOT / "NOTICE").read_text()
    self.assertIn("mattpocock/skills", notice)
    self.assertIn(PINNED_COMMIT, notice)
    self.assertIn("MIT", notice)
```

- [ ] **Step 2: Verify the tests fail before vendoring**

Run: `python3 -m unittest tests.test_vendor -v`

Expected: FAIL because the vendored directories are missing.

- [ ] **Step 3: Fetch only the pinned upstream files**

Fetch tag `v1.1.0` at resolved commit `d574778f94cf620fcc8ce741584093bc650a61d3`. Copy only `grill-me`, `grilling`, their directly required assets/references, and the upstream MIT license. Do not import unrelated upstream skills.

- [ ] **Step 4: Record exact provenance**

`docs/upstream-grill.md` must state source URL, tag, resolved commit, copied paths, local wrapper changes, update procedure, and license. Preserve the upstream author attribution.

- [ ] **Step 5: Validate vendored skills**

Run: `python3 scripts/validate_package.py && python3 -m unittest tests.test_vendor -v`

Expected: all checks pass.

- [ ] **Step 6: Review and commit Task 2**

Commit message: `feat: vendor pinned grill workflow`

### Task 3: Harness role skills, agents, schemas, and context helper

**Files:**
- Create: `skills/init-project/`
- Create: `skills/to-exec-plan/`
- Create: `skills/orchestrator/`
- Create: `skills/builder/`
- Create: `skills/reviewer/`
- Create: `skills/librarian/`
- Create: `skills/complete-project/`
- Create: `agents/harness-builder.toml`
- Create: `agents/harness-reviewer.toml`
- Create: `agents/harness-librarian.toml`
- Create: `schemas/builder-handoff.schema.json`
- Create: `schemas/lifecycle-event.schema.json`
- Create: `tests/test_contracts.py`

- [ ] **Step 1: Write failing role-contract tests**

Tests must assert all nine skills have valid `name` and `description` front matter plus `agents/openai.yaml`; the three agent TOML files contain `name`, `description`, and `developer_instructions`; schemas reject malformed handoffs/events; and the context helper rejects sibling leakage, debug handoffs, duplicate IDs, hash mismatch, and unsafe roots.

- [ ] **Step 2: Run the tests and confirm missing contracts**

Run: `python3 -m unittest tests.test_contracts -v`

Expected: FAIL listing absent role files.

- [ ] **Step 3: Port the validated role contracts**

Port the current validated contracts from `harness_v2`, keeping `TODO.json` as the sole active state and Orchestrator as the only status/attempt writer. Change Reviewer evidence guidance to require `$controlRoot/worklog/evidence/<feature-id>/` explicitly.

- [ ] **Step 4: Add project completion semantics**

`complete-project` must verify quiescence, all-passed status, validation/evidence presence, no unresolved handoff, checkpoint consistency, and acceptable Git state. It writes a proposed completion report and requires explicit owner approval before active-plan archival.

- [ ] **Step 5: Configure global agents**

Use prefixed names `harness-builder`, `harness-reviewer`, and `harness-librarian`; use `gpt-5.6` with high reasoning for Builder/Reviewer and medium for Librarian; omit permissions that should inherit from the parent unless the role needs a narrower sandbox.

- [ ] **Step 6: Validate contracts**

Run: `python3 scripts/validate_package.py && python3 -m unittest tests.test_contracts -v`

Expected: all contract tests pass.

- [ ] **Step 7: Review and commit Task 3**

Commit message: `feat: add complete harness workflow contracts`

### Task 4: Non-destructive project scaffold and initializer

**Files:**
- Create: `scaffold/AGENTS.md`
- Create: `scaffold/CLAUDE.md`
- Create: `scaffold/.codex/config.toml`
- Create: `scaffold/.codex/agents/*.toml`
- Create: `scaffold/docs/`
- Create: `scaffold/worklog/`
- Create: `skills/init-project/scripts/init_project.py`
- Create: `tests/test_init_project.py`

- [ ] **Step 1: Write failing initializer tests**

Cover dry-run, empty directory, existing Git repository, existing project-owned file preservation, managed-schema conflict reporting, `--force` boundaries, worklog preservation, and idempotent second run.

- [ ] **Step 2: Confirm the initializer tests fail**

Run: `python3 -m unittest tests.test_init_project -v`

Expected: FAIL because the initializer and scaffold are absent.

- [ ] **Step 3: Build the complete scaffold**

Include progressive-disclosure project map, PRD template, active empty TODO, completed-plan directory, tech-debt tracker, builder/lifecycle schemas, event guidance, empty handoff/lifecycle JSONL, checkpoint/evidence directories, project Codex config, and role adapters.

- [ ] **Step 4: Implement safe initialization**

The initializer must provide `--root`, `--dry-run`, and explicit `--force`; initialize Git only when absent; append required ignore entries; never commit, push, add remotes, or overwrite project-owned docs/state.

- [ ] **Step 5: Verify scaffold parity and behavior**

Run: `python3 -m unittest tests.test_init_project -v && python3 scripts/validate_package.py`

Expected: all tests pass and root/scaffold schemas are byte-identical.

- [ ] **Step 6: Review and commit Task 4**

Commit message: `feat: add project harness initializer`

### Task 5: Global installer, doctor, rollback, uninstall, and offline migration

**Files:**
- Create: `scripts/install.py`
- Create: `scripts/uninstall.py`
- Create: `scripts/doctor.py`
- Create: `scripts/build_bundle.py`
- Create: `scripts/bootstrap.sh`
- Create: `tests/test_install.py`
- Create: `tests/test_bundle.py`

- [ ] **Step 1: Write failing installation tests using temporary HOME directories**

Test first install, dry-run no-write, repeated install, existing `[agents]` preservation, timestamped backup, conflicting agent file, symlink mode, copy fallback, interrupted-operation rollback, hash-aware uninstall, and config-key ownership.

- [ ] **Step 2: Write failing bundle tests**

Test reproducible sorted archives, manifest version/source commit, SHA-256 sidecar, exclusion of `.git`, credentials, caches, sessions, and unrelated config, plus network-free installation from the extracted archive.

- [ ] **Step 3: Confirm the tests fail**

Run: `python3 -m unittest tests.test_install tests.test_bundle -v`

Expected: FAIL because deployment scripts do not exist.

- [ ] **Step 4: Implement doctor and ownership model**

Doctor checks Python version, Git, Codex executable when requested, writable user directories, TOML parseability, and symlink capability. Install state lives at `~/.codex/my-codex-harness/install-state.json` and records version, source commit, mode, hashes, owned paths, backups, and added config keys.

- [ ] **Step 5: Implement transactional install and uninstall**

Install copies the package to `~/.codex/plugins/my-codex-harness`, links or copies skills into `~/.agents/skills`, installs prefixed agent files, and minimally merges `[agents]`. Every mutation is journaled; failure restores backups. Uninstall refuses to delete hash-diverged managed files.

- [ ] **Step 6: Implement portable bundle creation**

Bundle output must be `my-codex-harness-<version>.tar.gz`, `my-codex-harness-<version>.tar.gz.sha256`, and `bundle-manifest.json`. Use stable path ordering, normalized metadata where practical, and no secrets.

- [ ] **Step 7: Verify deployment behavior**

Run: `python3 -m unittest tests.test_install tests.test_bundle -v`

Expected: all cases pass on the current platform.

- [ ] **Step 8: Review and commit Task 5**

Commit message: `feat: add portable transactional deployment`

### Task 6: Complete documentation and minimal example

**Files:**
- Expand: `README.md`
- Create: `docs/architecture.md`
- Create: `docs/quickstart.md`
- Create: `docs/full-workflow.md`
- Create: `docs/roles.md`
- Create: `docs/state-and-observability.md`
- Create: `docs/project-completion.md`
- Create: `docs/installation.md`
- Create: `docs/migration.md`
- Create: `docs/upgrading.md`
- Create: `docs/troubleshooting.md`
- Create: `examples/minimal-project/`
- Create: `tests/test_docs.py`

- [ ] **Step 1: Write failing documentation tests**

Assert every required document exists, every local Markdown link resolves, all documented commands refer to existing scripts/skills, README contains install/quickstart/workflow/migration/uninstall/security sections, and the example contains a PRD, TODO, handoff, evidence, lifecycle, checkpoint, completion report, and final state.

- [ ] **Step 2: Confirm docs tests fail**

Run: `python3 -m unittest tests.test_docs -v`

Expected: FAIL listing missing guides and example artifacts.

- [ ] **Step 3: Write the README and guides**

Document the safe clone-and-install path, convenience bootstrap, offline migration, manual install, plugin marketplace install, global agents, first project, full role flow, observability semantics, retry/blocker behavior, completion approval, upgrade, rollback, uninstall, and credential exclusions.

- [ ] **Step 4: Build the minimal complete example**

Use one dependency-free `add(a, b)` feature. Include the exact intended artifacts for requirements, plan, Builder handoff, Reviewer evidence, lifecycle events, merge/global validation, Librarian map update, checkpoint, and completion report. Mark it as an illustrative fixture rather than fabricated live token usage.

- [ ] **Step 5: Validate documentation**

Run: `python3 -m unittest tests.test_docs -v && python3 scripts/validate_package.py`

Expected: every link and command resolves.

- [ ] **Step 6: Review and commit Task 6**

Commit message: `docs: add complete workflow and migration guides`

### Task 7: Cross-platform CI and mechanical golden path

**Files:**
- Create: `tests/test_golden_path.py`
- Create: `.github/workflows/ci.yml`
- Modify: `scripts/validate_package.py`

- [ ] **Step 1: Write the failing mechanical golden-path test**

The test must create a temporary Git repository, initialize the scaffold, publish one feature, construct schema-valid Builder and Reviewer events, merge the fixture commit, run global validation, update project navigation, write paired lifecycle spans, checkpoint quiescence, prepare the completion report, and archive only after simulated explicit approval.

- [ ] **Step 2: Verify the golden path fails before the fixture runner exists**

Run: `python3 -m unittest tests.test_golden_path -v`

Expected: FAIL at the first missing deterministic workflow helper or fixture.

- [ ] **Step 3: Implement the minimum deterministic fixture helpers**

Keep helpers under `tests/fixtures/` unless they are also required by production scripts. Never invoke a model or network service.

- [ ] **Step 4: Add cross-platform CI**

Run `python3 scripts/validate_package.py` and `python3 -m unittest discover -s tests -v` on `ubuntu-latest`, `macos-latest`, and `windows-latest` with a supported Python 3 matrix.

- [ ] **Step 5: Run the full local suite**

Run: `python3 scripts/validate_package.py && python3 -m unittest discover -s tests -v`

Expected: all tests pass, no network/model dependency.

- [ ] **Step 6: Review and commit Task 7**

Commit message: `test: validate portable harness golden path`

### Task 8: Install globally, audit, and publish

**Files:**
- Managed external installation: `~/.codex/plugins/my-codex-harness/`
- Managed external agents: `~/.codex/agents/harness-*.toml`
- Managed discovery links/copies: `~/.agents/skills/`
- Managed config: `~/.codex/config.toml`
- Managed state: `~/.codex/my-codex-harness/install-state.json`

- [ ] **Step 1: Run final repository verification**

Run:

```bash
python3 scripts/validate_package.py
python3 -m unittest discover -s tests -v
git diff --check
git status --short
```

Expected: validation/tests pass and the working tree is clean before installation.

- [ ] **Step 2: Build and verify the offline artifact**

Run:

```bash
python3 scripts/build_bundle.py --output dist
python3 scripts/doctor.py --bundle dist/my-codex-harness-0.1.0.tar.gz
```

Expected: archive checksum and manifest validate.

- [ ] **Step 3: Preview and perform the approved global installation**

Run:

```bash
python3 scripts/install.py --dry-run
python3 scripts/install.py --yes
python3 scripts/doctor.py --installed
```

Expected: only declared managed paths/config keys change and doctor reports healthy.

- [ ] **Step 4: Test global discovery in a fresh Codex task or supported CLI inspection path**

Verify the nine skills and three custom agents are discoverable. If the running Codex client caches customization, restart it and verify again.

- [ ] **Step 5: Perform final correctness and simplicity reviews**

Review the complete diff against the design, audit for over-engineering, verify third-party attribution, scan tracked files for likely secret patterns, and resolve every blocking finding.

- [ ] **Step 6: Present the final diff and commit state**

Confirm all logical commits, branch, remote, and intended push set. Do not amend or rewrite history.

- [ ] **Step 7: Push the approved branch normally**

Run: `git push -u origin main`

Expected: GitHub repository receives the full validated history without force push.
