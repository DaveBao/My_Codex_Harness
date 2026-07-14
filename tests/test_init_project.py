import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/init-project/scripts/init_project.py"
SCAFFOLD = ROOT / "scaffold"


def run_init(
    root: Path,
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        cwd=cwd or ROOT,
        capture_output=True,
        env=env,
        text=True,
        timeout=timeout,
    )


def tree_snapshot(root: Path) -> dict[str, bytes | str | None]:
    snapshot: dict[str, bytes | str | None] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = f"symlink:{os.readlink(path)}"
        elif path.is_dir():
            snapshot[relative] = None
        elif path.is_file():
            snapshot[relative] = path.read_bytes()
        else:
            snapshot[relative] = "special"
    return snapshot


def load_initializer():
    spec = importlib.util.spec_from_file_location("harness_init_project", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load initializer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InitProjectTests(unittest.TestCase):
    def test_help_documents_supported_flags(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--root", result.stdout)
        self.assertIn("--dry-run", result.stdout)
        self.assertIn("--force", result.stdout)

    def test_dry_run_is_deterministic_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            before = tree_snapshot(target)
            first = run_init(target, "--dry-run")
            second = run_init(target, "--dry-run")

            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(before, tree_snapshot(target))
            self.assertIn("created:", first.stdout)
            self.assertIn(".git/", first.stdout)

    def test_empty_directory_gets_complete_scaffold_and_git_only(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            result = run_init(target)

            self.assertEqual(0, result.returncode, result.stderr)
            for relative in (
                "AGENTS.md",
                "CLAUDE.md",
                ".codex/config.toml",
                ".codex/agents/harness-builder.toml",
                ".codex/agents/harness-reviewer.toml",
                ".codex/agents/harness-librarian.toml",
                "docs/project-map.md",
                "docs/product-specs/prd.md",
                "docs/exec-plans/active/TODO.json",
                "docs/exec-plans/completed/.gitkeep",
                "docs/exec-plans/tech-debt-tracker.md",
                "docs/references/builder-handoff.schema.json",
                "docs/references/lifecycle-event.schema.json",
                "docs/references/worklog-events.md",
                "worklog/handoffs.jsonl",
                "worklog/logs/lifecycle.jsonl",
                "worklog/checkpoints/.gitkeep",
                "worklog/evidence/.gitkeep",
            ):
                self.assertTrue((target / relative).is_file(), relative)
            self.assertTrue((target / ".git").exists())
            self.assertEqual(b"", (target / "worklog/handoffs.jsonl").read_bytes())
            self.assertEqual(b"", (target / "worklog/logs/lifecycle.jsonl").read_bytes())
            remotes = subprocess.run(
                ["git", "remote"], cwd=target, capture_output=True, text=True, check=True
            )
            self.assertEqual("", remotes.stdout)

    def test_existing_git_repository_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=target, check=True)
            marker = target / ".git/harness-test-marker"
            marker.write_text("keep\n")

            result = run_init(target)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("keep\n", marker.read_text())
            self.assertIn(".git/ (unchanged)", result.stdout)

    def test_nested_target_in_parent_git_repo_fails_before_writing(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                subprocess.run(["git", "init", "-q"], cwd=parent, check=True)
                target = parent / "child"
                target.mkdir()
                before = tree_snapshot(parent)

                args = ("--dry-run",) if dry_run else ()
                result = run_init(target, *args)

                self.assertNotEqual(0, result.returncode)
                self.assertEqual(before, tree_snapshot(parent))
                self.assertFalse((target / ".git").exists())
                self.assertFalse((target / "AGENTS.md").exists())
                self.assertIn("existing Git repository", result.stderr)
                self.assertIn("top level", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_git_symlink_and_invalid_git_directory_fail_closed(self):
        for git_kind in ("external_symlink", "invalid_directory"):
            for dry_run in (False, True):
                with (
                    self.subTest(git_kind=git_kind, dry_run=dry_run),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    base = Path(directory)
                    target = base / "target"
                    target.mkdir()
                    if git_kind == "external_symlink":
                        external = base / "external"
                        external.mkdir()
                        subprocess.run(["git", "init", "-q"], cwd=external, check=True)
                        (target / ".git").symlink_to(external / ".git", target_is_directory=True)
                    else:
                        (target / ".git").mkdir()
                        (target / ".git/user-marker").write_text("preserve\n")
                    before = tree_snapshot(base)

                    args = ("--dry-run",) if dry_run else ()
                    result = run_init(target, *args)

                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual(before, tree_snapshot(base))
                    self.assertIn(".git", result.stderr + result.stdout)
                    self.assertNotIn("Traceback", result.stderr)

    def test_non_utf8_gitignore_fails_before_any_write_without_path_leakage(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                (target / ".gitignore").write_bytes(b"\xff\xfe")
                before = tree_snapshot(target)

                args = ("--dry-run",) if dry_run else ()
                result = run_init(target, *args)

                self.assertNotEqual(0, result.returncode)
                self.assertEqual(before, tree_snapshot(target))
                self.assertIn(".gitignore", result.stderr)
                self.assertIn("UTF-8", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn(str(target), result.stderr)
                self.assertEqual("", result.stdout)

    def test_project_owned_files_are_preserved_even_with_force(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            owned = {
                "AGENTS.md": b"project agents\n",
                "CLAUDE.md": b"project claude\n",
                "docs/project-map.md": b"project map\n",
                "docs/product-specs/prd.md": b"project prd\n",
                "docs/exec-plans/active/TODO.json": b'{"features":[{"id":"F1"}]}\n',
                "docs/exec-plans/tech-debt-tracker.md": b"project debt\n",
                "worklog/handoffs.jsonl": b'{"eventId":"keep"}\n',
                "worklog/logs/lifecycle.jsonl": b'{"eventId":"keep"}\n',
                "worklog/checkpoints/orchestrator.json": b'{"keep":true}\n',
                "worklog/evidence/F1/result.txt": b"keep\n",
            }
            for relative, content in owned.items():
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            result = run_init(target, "--force")

            self.assertEqual(0, result.returncode, result.stderr)
            for relative, content in owned.items():
                self.assertEqual(content, (target / relative).read_bytes(), relative)

    def test_managed_schema_conflict_requires_force(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            schema = target / "docs/references/builder-handoff.schema.json"
            schema.parent.mkdir(parents=True)
            schema.write_text('{"local":true}\n')

            result = run_init(target)

            self.assertNotEqual(0, result.returncode)
            self.assertEqual('{"local":true}\n', schema.read_text())
            self.assertIn("conflicts:", result.stdout)
            self.assertIn("docs/references/builder-handoff.schema.json", result.stdout)
            self.assertIn("--force", result.stdout)

    def test_force_replaces_only_managed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            managed = (
                ".codex/agents/harness-builder.toml",
                "docs/references/builder-handoff.schema.json",
                "docs/references/worklog-events.md",
            )
            for relative in managed:
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("local managed change\n")
            project_map = target / "docs/project-map.md"
            project_map.write_text("local project map\n")

            result = run_init(target, "--force")

            self.assertEqual(0, result.returncode, result.stderr)
            for relative in managed:
                self.assertEqual((SCAFFOLD / relative).read_bytes(), (target / relative).read_bytes())
            self.assertEqual("local project map\n", project_map.read_text())
            self.assertIn("replaced:", result.stdout)

    def test_existing_project_codex_config_is_preserved_with_force(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            config = target / ".codex/config.toml"
            config.parent.mkdir(parents=True)
            original = (
                b'features = ["custom"]\n\n[agents]\nmax_threads = 99\n\n'
                b'[unrelated]\nenabled = true\n'
            )
            config.write_bytes(original)

            result = run_init(target, "--force")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(original, config.read_bytes())
            self.assertIn(".codex/config.toml (project-owned; preserved)", result.stdout)

    def test_predictable_conflicts_make_actual_and_dry_run_zero_write(self):
        for conflict_kind in ("managed", "symlink_parent"):
            for dry_run in (False, True):
                with (
                    self.subTest(conflict_kind=conflict_kind, dry_run=dry_run),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    base = Path(directory)
                    target = base / "target"
                    target.mkdir()
                    if conflict_kind == "managed":
                        conflict = target / "docs/references/builder-handoff.schema.json"
                        conflict.parent.mkdir(parents=True)
                        conflict.write_text('{"local":true}\n')
                    else:
                        outside = base / "outside"
                        outside.mkdir()
                        (target / "docs").symlink_to(outside, target_is_directory=True)
                    before = tree_snapshot(base)

                    args = ("--dry-run",) if dry_run else ()
                    result = run_init(target, *args)

                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual(before, tree_snapshot(base))
                    self.assertFalse((target / ".git").exists())
                    self.assertFalse((target / ".gitignore").exists())
                    self.assertIn("conflicts:", result.stdout)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is unavailable on this platform")
    def test_special_file_destinations_fail_preflight_without_hanging_or_writing(self):
        for relative in (".gitignore", "AGENTS.md"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                fifo = target / relative
                fifo.parent.mkdir(parents=True, exist_ok=True)
                os.mkfifo(fifo)
                before = tree_snapshot(target)

                try:
                    result = run_init(target, timeout=1)
                except subprocess.TimeoutExpired as error:
                    self.fail(f"initializer opened and blocked on FIFO {relative}: {error}")

                self.assertNotEqual(0, result.returncode)
                self.assertEqual(before, tree_snapshot(target))
                self.assertIn("not a regular file", result.stderr + result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_git_root_with_trailing_space_is_not_misclassified_as_nested(self):
        if os.name == "nt":
            self.skipTest("Windows paths cannot reliably preserve trailing spaces")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "repo "
            target.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=target, check=True)

            result = run_init(target)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((target / "AGENTS.md").is_file())

    def test_bare_repository_is_rejected_without_writes(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "bare.git"
                subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)
                before = tree_snapshot(target)

                args = ("--dry-run",) if dry_run else ()
                result = run_init(target, *args)

                self.assertNotEqual(0, result.returncode)
                self.assertEqual(before, tree_snapshot(target))
                self.assertIn("bare", result.stderr.lower())

    def test_non_repository_detection_does_not_depend_on_english_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            initializer = load_initializer()
            localized = subprocess.CompletedProcess(
                args=["git"], returncode=128, stdout="", stderr="fatal: kein Repository\n"
            )

            with mock.patch.object(initializer.subprocess, "run", return_value=localized):
                self.assertIsNone(initializer.probe_git_root(target))

    def test_polluted_git_environment_cannot_bypass_conflicts_or_parent_repo(self):
        for scenario in ("managed_conflict", "parent_repo"):
            for dry_run in (False, True):
                with (
                    self.subTest(scenario=scenario, dry_run=dry_run),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    base = Path(directory)
                    external = base / "external"
                    external.mkdir()
                    subprocess.run(["git", "init", "-q"], cwd=external, check=True)
                    if scenario == "parent_repo":
                        parent = base / "parent"
                        parent.mkdir()
                        subprocess.run(["git", "init", "-q"], cwd=parent, check=True)
                        target = parent / "child"
                        target.mkdir()
                    else:
                        target = base / "target"
                        target.mkdir()
                        conflict = target / "docs/references/builder-handoff.schema.json"
                        conflict.parent.mkdir(parents=True)
                        conflict.write_text('{"local":true}\n')
                    polluted = os.environ.copy()
                    polluted.update(
                        {
                            "GIT_DIR": str(external / ".git"),
                            "GIT_COMMON_DIR": str(external / ".git"),
                            "GIT_OBJECT_DIRECTORY": str(external / ".git/objects"),
                            "GIT_CEILING_DIRECTORIES": str(base),
                            "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1",
                        }
                    )
                    before = tree_snapshot(base)

                    args = ("--dry-run",) if dry_run else ()
                    result = run_init(target, *args, env=polluted)

                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual(before, tree_snapshot(base))
                    if scenario == "parent_repo":
                        self.assertIn("existing Git repository", result.stderr)
                    else:
                        self.assertIn(
                            "docs/references/builder-handoff.schema.json", result.stdout
                        )

    def test_fresh_init_with_polluted_git_paths_never_writes_external_repo(self):
        git_paths = {
            "GIT_DIR": ".git",
            "GIT_COMMON_DIR": ".git",
            "GIT_OBJECT_DIRECTORY": ".git/objects",
        }
        for variable, external_relative in git_paths.items():
            for dry_run in (False, True):
                with (
                    self.subTest(variable=variable, dry_run=dry_run),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    base = Path(directory)
                    external = base / "external"
                    external.mkdir()
                    subprocess.run(["git", "init", "-q"], cwd=external, check=True)
                    target = base / "fresh-target"
                    target.mkdir()
                    polluted = os.environ.copy()
                    polluted[variable] = str(external / external_relative)
                    external_before = tree_snapshot(external)
                    target_before = tree_snapshot(target)

                    args = ("--dry-run",) if dry_run else ()
                    result = run_init(target, *args, env=polluted)

                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(external_before, tree_snapshot(external))
                    if dry_run:
                        self.assertEqual(target_before, tree_snapshot(target))
                    else:
                        self.assertTrue((target / ".git").is_dir())
                        self.assertTrue((target / "AGENTS.md").is_file())

    def test_simulated_non_repo_result_rejects_ancestor_git_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=parent, check=True)
            target = parent / "child"
            target.mkdir()
            initializer = load_initializer()
            non_repo = subprocess.CompletedProcess(
                args=["git"], returncode=128, stdout="", stderr="localized failure\n"
            )
            before = tree_snapshot(parent)

            with mock.patch.object(initializer.subprocess, "run", return_value=non_repo):
                with self.assertRaisesRegex(initializer.InitError, "ancestor.*.git"):
                    initializer.probe_git_root(target)

            self.assertEqual(before, tree_snapshot(parent))

    def test_every_git_call_receives_the_same_sanitized_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            existing = base / "existing"
            existing.mkdir()
            (existing / ".git").mkdir()
            fresh = base / "fresh"
            fresh.mkdir()
            initializer = load_initializer()
            polluted_keys = {
                "GIT_DIR": "outside",
                "GIT_COMMON_DIR": "outside",
                "GIT_OBJECT_DIRECTORY": "outside",
                "GIT_CEILING_DIRECTORIES": "outside",
                "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1",
                "GIT_FUTURE_VARIABLE": "outside",
            }
            calls: list[dict] = []

            def git_call(command, **kwargs):
                calls.append(kwargs)
                if "--is-inside-work-tree" in command:
                    value = "true\n" if Path(command[2]) == existing else ""
                    code = 0 if value else 128
                    return subprocess.CompletedProcess(command, code, value, "")
                if "--show-prefix" in command:
                    return subprocess.CompletedProcess(command, 0, "\n", "")
                (Path(kwargs["cwd"]) / ".git").mkdir()
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.dict(os.environ, polluted_keys, clear=False):
                clean = initializer.git_environment()
                with mock.patch.object(initializer.subprocess, "run", side_effect=git_call):
                    initializer.probe_git_root(existing, clean)
                    initializer.execute_plan(fresh, [("git-init", fresh)], clean)

            self.assertIn("PATH", clean)
            self.assertFalse(any(key.startswith("GIT_") for key in clean))
            self.assertEqual(4, len(calls))
            self.assertTrue(all(call.get("env") is clean for call in calls))

    def test_post_plan_destination_drift_fails_without_overwrite_or_escape(self):
        for drift_kind in ("create_file", "managed_replace", "parent_symlink"):
            with self.subTest(drift_kind=drift_kind), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                target = base / "target"
                target.mkdir()
                initializer = load_initializer()

                if drift_kind == "managed_replace":
                    initialized = run_init(target)
                    self.assertEqual(0, initialized.returncode, initialized.stderr)
                    managed = target / "docs/references/builder-handoff.schema.json"
                    managed.write_text('{"planned":true}\n')
                    plan, _report = initializer.build_plan(target, force=True)
                    managed.write_text('{"drifted":true}\n')
                else:
                    plan, _report = initializer.build_plan(target, force=False)
                    if drift_kind == "create_file":
                        (target / "AGENTS.md").write_text("user file after plan\n")
                    else:
                        outside = base / "outside"
                        outside.mkdir()
                        (target / ".codex").symlink_to(outside, target_is_directory=True)
                before = tree_snapshot(base)

                with self.assertRaises(initializer.InitError):
                    initializer.execute_plan(target, plan)

                self.assertEqual(before, tree_snapshot(base))

    def test_post_plan_git_appearance_is_preserved_and_blocks_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            initializer = load_initializer()
            plan, _report = initializer.build_plan(target, force=False)
            (target / ".git").mkdir()
            marker = target / ".git/user-marker"
            marker.write_text("preserve\n")
            before = tree_snapshot(target)

            with self.assertRaises(initializer.InitError):
                initializer.execute_plan(target, plan)

            self.assertEqual(before, tree_snapshot(target))

    def test_post_plan_parent_repo_creation_rolls_back_scaffold(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target = parent / "child"
            target.mkdir()
            initializer = load_initializer()
            plan, _report = initializer.build_plan(target, force=False)
            subprocess.run(["git", "init", "-q"], cwd=parent, check=True)
            before = tree_snapshot(parent)

            with self.assertRaises(initializer.InitError) as caught:
                initializer.execute_plan(target, plan)

            self.assertEqual(before, tree_snapshot(parent))
            self.assertIn("existing Git repository", str(caught.exception))
            self.assertIn("rolled back", str(caught.exception))

    def test_git_rollback_removes_only_undrifted_git_created_by_this_run(self):
        for drifted in (False, True):
            with self.subTest(drifted=drifted), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                initializer = load_initializer()
                source = SCAFFOLD / "AGENTS.md"
                plan = [
                    ("git-init", target),
                    ("copy-create", source, target / "AGENTS.md"),
                ]

                def fail_after_git(_source, _destination):
                    if drifted:
                        (target / ".git/user-marker").write_text("external drift\n")
                    raise OSError("injected failure after git init")

                with mock.patch.object(initializer, "atomic_copy", side_effect=fail_after_git):
                    with self.assertRaises(initializer.InitError) as caught:
                        initializer.execute_plan(target, plan)

                if drifted:
                    self.assertTrue((target / ".git/user-marker").is_file())
                    self.assertIn("rollback was incomplete", str(caught.exception))
                else:
                    self.assertFalse((target / ".git").exists())
                    self.assertIn("rolled back", str(caught.exception))

    def test_partial_git_init_failure_is_preserved_and_reported_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            initializer = load_initializer()

            def git_behavior(command, **_kwargs):
                if "rev-parse" in command:
                    return subprocess.CompletedProcess(command, 128, "", "localized failure\n")
                (target / ".git").mkdir()
                (target / ".git/user-marker").write_text("partial unknown state\n")
                raise subprocess.CalledProcessError(1, command)

            with mock.patch.object(initializer.subprocess, "run", side_effect=git_behavior):
                with self.assertRaises(initializer.InitError) as caught:
                    initializer.execute_plan(target, [("git-init", target)])

            self.assertTrue((target / ".git/user-marker").is_file())
            self.assertIn("rollback was incomplete", str(caught.exception))

    def test_gitignore_preserves_existing_crlf_bytes_when_appending(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            ignore = target / ".gitignore"
            ignore.write_bytes(b"dist/\r\n")

            result = run_init(target)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                b"dist/\r\n.worktrees/\r\n.DS_Store\r\n",
                ignore.read_bytes(),
            )

    def test_write_failure_rolls_back_only_initializer_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            before = tree_snapshot(target)
            initializer = load_initializer()
            plan, _report = initializer.build_plan(target, force=False)
            real_atomic_copy = initializer.atomic_copy
            calls = 0

            def fail_second_copy(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected write failure")
                return real_atomic_copy(source, destination)

            with mock.patch.object(initializer, "atomic_copy", side_effect=fail_second_copy):
                with self.assertRaisesRegex(initializer.InitError, "write failed"):
                    initializer.execute_plan(target, plan)

            self.assertEqual(before, tree_snapshot(target))

    def test_post_replace_failures_roll_back_every_atomic_action_kind(self):
        for action_kind in (
            "copy-create",
            "copy-replace",
            "write-create",
            "write-replace",
        ):
            with self.subTest(action_kind=action_kind), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                initializer = load_initializer()

                if action_kind != "copy-create":
                    initialized = run_init(target)
                    self.assertEqual(0, initialized.returncode, initialized.stderr)
                if action_kind == "copy-replace":
                    managed = target / "docs/references/builder-handoff.schema.json"
                    managed.write_text('{"local":true}\n')
                    plan, _report = initializer.build_plan(target, force=True)
                    atomic_name = "atomic_copy"
                elif action_kind == "write-create":
                    (target / ".gitignore").unlink()
                    plan, _report = initializer.build_plan(target, force=False)
                    atomic_name = "atomic_write"
                elif action_kind == "write-replace":
                    (target / ".gitignore").write_bytes(b"project-only\r\n")
                    plan, _report = initializer.build_plan(target, force=False)
                    atomic_name = "atomic_write"
                else:
                    plan, _report = initializer.build_plan(target, force=False)
                    atomic_name = "atomic_copy"

                before = tree_snapshot(target)
                real_atomic = getattr(initializer, atomic_name)

                def fail_after_replace(*args):
                    real_atomic(*args)
                    raise OSError("injected post-replace failure")

                with mock.patch.object(initializer, atomic_name, side_effect=fail_after_replace):
                    with self.assertRaises(initializer.InitError):
                        initializer.execute_plan(target, plan)

                self.assertEqual(before, tree_snapshot(target))

    def test_second_run_is_idempotent_and_reports_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            first = run_init(target)
            self.assertEqual(0, first.returncode, first.stderr)
            before = tree_snapshot(target)

            second = run_init(target)

            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(before, tree_snapshot(target))
            self.assertIn("created:\n  - none", second.stdout)
            self.assertIn("(unchanged)", second.stdout)

    def test_gitignore_merge_preserves_content_and_adds_only_missing_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            ignore = target / ".gitignore"
            ignore.write_text("dist/\n.DS_Store\nkeep-without-newline")

            first = run_init(target)
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(
                "dist/\n.DS_Store\nkeep-without-newline\n.worktrees/\n",
                ignore.read_text(),
            )
            second = run_init(target)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(1, ignore.read_text().splitlines().count(".worktrees/"))
            self.assertEqual(1, ignore.read_text().splitlines().count(".DS_Store"))

    def test_root_path_with_spaces_and_default_cwd_are_supported(self):
        with tempfile.TemporaryDirectory(prefix="harness root with spaces ") as directory:
            target = Path(directory)
            explicit = run_init(target)
            self.assertEqual(0, explicit.returncode, explicit.stderr)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            default = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=target,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, default.returncode, default.stderr)
            self.assertTrue((target / "docs/project-map.md").is_file())

    def test_missing_root_fails_clearly_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            result = run_init(missing)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("target root does not exist", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(missing.exists())

    def test_scaffold_contract_and_schema_parity(self):
        required = (
            "AGENTS.md",
            "CLAUDE.md",
            ".codex/config.toml",
            ".codex/agents/harness-builder.toml",
            ".codex/agents/harness-reviewer.toml",
            ".codex/agents/harness-librarian.toml",
            "docs/project-map.md",
            "docs/product-specs/prd.md",
            "docs/exec-plans/active/TODO.json",
            "docs/exec-plans/completed/.gitkeep",
            "docs/exec-plans/tech-debt-tracker.md",
            "docs/references/builder-handoff.schema.json",
            "docs/references/lifecycle-event.schema.json",
            "docs/references/worklog-events.md",
            "worklog/handoffs.jsonl",
            "worklog/logs/lifecycle.jsonl",
            "worklog/checkpoints/.gitkeep",
            "worklog/evidence/.gitkeep",
        )
        for relative in required:
            self.assertTrue((SCAFFOLD / relative).is_file(), relative)
        for name in ("builder-handoff.schema.json", "lifecycle-event.schema.json"):
            self.assertEqual((ROOT / "schemas" / name).read_bytes(), (SCAFFOLD / "docs/references" / name).read_bytes())
        for name in ("builder", "reviewer", "librarian"):
            scaffold_agent = SCAFFOLD / f".codex/agents/harness-{name}.toml"
            root_agent = ROOT / f"agents/harness-{name}.toml"
            self.assertEqual(root_agent.read_bytes(), scaffold_agent.read_bytes())
            text = scaffold_agent.read_text()
            self.assertIn(f'name = "harness-{name}"', text)
            self.assertNotIn(str(Path.home()), text)
        self.assertEqual(b"", (SCAFFOLD / "worklog/handoffs.jsonl").read_bytes())
        self.assertEqual(b"", (SCAFFOLD / "worklog/logs/lifecycle.jsonl").read_bytes())

    def test_skill_documents_project_owned_codex_config_boundary(self):
        skill = (ROOT / "skills/init-project/SKILL.md").read_text()
        self.assertIn("`.codex/config.toml` is project-owned once created", skill)

    def test_scaffold_is_dormant_without_owner_invocation(self):
        text = (SCAFFOLD / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## Dormant By Default", text)
        self.assertIn("$orchestrator", text)
        self.assertIn("/harness run", text)
        self.assertIn("/harness resume", text)
        self.assertRegex(text, r"(?i)ordinary tasks.*normal Codex")
        self.assertRegex(text, r"(?i)TODO.*never.*activate|checkpoint.*never.*activate")


if __name__ == "__main__":
    unittest.main()
