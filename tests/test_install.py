import importlib.util
import hashlib
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts/install.py"
UNINSTALL = ROOT / "scripts/uninstall.py"
DOCTOR = ROOT / "scripts/doctor.py"
STATE_RELATIVE = Path(".codex/my-codex-harness/install-state.json")
DESIRED_AGENTS = {
    "max_threads": 6,
    "max_depth": 1,
    "interrupt_message": True,
}


def run_script(script: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=script.parents[1],
        env=env,
        capture_output=True,
        text=True,
    )


def snapshot(root: Path) -> dict[str, bytes | str | None]:
    result: dict[str, bytes | str | None] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = f"link:{os.readlink(path)}"
        elif path.is_dir():
            result[relative] = None
        elif path.is_file():
            result[relative] = path.read_bytes()
        else:
            result[relative] = "special"
    return result


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"harness_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_source(root: Path, version: str) -> Path:
    source = root / f"source-{version}"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", ".worktrees", "__pycache__"))
    manifest_path = source / ".codex-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return source


class InstallTests(unittest.TestCase):
    def test_dry_run_is_deterministic_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            before = snapshot(home)
            first = run_script(INSTALL, home, "--dry-run")
            second = run_script(INSTALL, home, "--dry-run")

            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(before, snapshot(home))
            self.assertIn("would install", first.stdout)

    def test_first_install_creates_canonical_package_agents_skills_config_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            result = run_script(INSTALL, home, "--yes")

            self.assertEqual(0, result.returncode, result.stderr)
            package = home / ".codex/plugins/my-codex-harness"
            self.assertEqual("my-codex-harness", json.loads((package / ".codex-plugin/plugin.json").read_text())["name"])
            for name in ("builder", "reviewer", "librarian"):
                self.assertEqual(
                    (ROOT / f"agents/harness-{name}.toml").read_bytes(),
                    (home / f".codex/agents/harness-{name}.toml").read_bytes(),
                )
            for skill in sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir()):
                self.assertTrue((home / ".agents/skills" / skill).exists(), skill)
            config = (home / ".codex/config.toml").read_text()
            for key, value in DESIRED_AGENTS.items():
                rendered = str(value).lower() if isinstance(value, bool) else str(value)
                self.assertIn(f"{key} = {rendered}", config)

            state = json.loads((home / STATE_RELATIVE).read_text())
            self.assertEqual("0.1.0", state["version"])
            self.assertIn("sourceCommit", state)
            self.assertIn(state["mode"], ("symlink", "copy"))
            self.assertTrue(state["hashes"])
            self.assertTrue(state["ownedPaths"])
            self.assertTrue(set(state["ownedPaths"]).issubset(state["createdPaths"]))
            self.assertIn(".codex/config.toml", state["createdPaths"])
            self.assertEqual([], state["replacedPaths"])
            self.assertEqual(DESIRED_AGENTS, state["addedConfigKeys"])
            self.assertTrue(state["lastJournal"])

    def test_repeat_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            first = run_script(INSTALL, home, "--yes")
            self.assertEqual(0, first.returncode, first.stderr)
            before = snapshot(home)

            second = run_script(INSTALL, home, "--yes")

            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(before, snapshot(home))
            self.assertIn("already installed", second.stdout)

    def test_existing_agents_values_and_unrelated_config_bytes_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".codex/config.toml"
            config.parent.mkdir(parents=True)
            original = (
                b'# project setting\r\nmodel = "gpt-custom"\r\n\r\n'
                b"[agents]\r\nmax_threads = 22\r\n\r\n"
                b"[mcp_servers.keep]\r\ncommand = 'keep'\r\n"
            )
            config.write_bytes(original)

            result = run_script(INSTALL, home, "--yes", "--copy")

            self.assertEqual(0, result.returncode, result.stderr)
            changed = config.read_bytes()
            self.assertIn(b"max_threads = 22\r\n", changed)
            self.assertIn(b'# project setting\r\nmodel = "gpt-custom"\r\n', changed)
            self.assertIn(b"[mcp_servers.keep]\r\ncommand = 'keep'\r\n", changed)
            state = json.loads((home / STATE_RELATIVE).read_text())
            self.assertNotIn("max_threads", state["addedConfigKeys"])
            self.assertEqual(22, state["preservedConfigKeys"]["max_threads"])

    def test_config_change_has_timestamped_backup_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".codex/config.toml"
            config.parent.mkdir(parents=True)
            original = b'model = "gpt-custom"\n'
            config.write_bytes(original)

            result = run_script(INSTALL, home, "--yes")

            self.assertEqual(0, result.returncode, result.stderr)
            state = json.loads((home / STATE_RELATIVE).read_text())
            backups = state["backups"]
            self.assertEqual(1, len(backups))
            backup = home / backups[0]["path"]
            self.assertRegex(backup.name, r"config\.toml\.backup-\d{8}T\d{12}Z")
            self.assertEqual(original, backup.read_bytes())
            self.assertLess(backup.stat().st_mtime_ns, config.stat().st_mtime_ns + 1)

    def test_agent_and_skill_conflicts_fail_before_any_write(self):
        for relative in (
            ".codex/agents/harness-builder.toml",
            ".agents/skills/builder/SKILL.md",
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                conflict = home / relative
                conflict.parent.mkdir(parents=True)
                conflict.write_text("local\n")
                before = snapshot(home)

                result = run_script(INSTALL, home, "--yes")

                self.assertNotEqual(0, result.returncode)
                self.assertEqual(before, snapshot(home))
                self.assertIn("conflict", result.stderr.lower())
                self.assertNotIn("Traceback", result.stderr)

    def test_explicit_copy_mode_and_automatic_symlink_fallback(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "explicit"
            outcome = installer.install_package(ROOT, home, force_copy=True)
            self.assertEqual("copy", outcome["mode"])
            self.assertFalse((home / ".agents/skills/builder").is_symlink())

        def unavailable(*_args, **_kwargs):
            raise OSError("symlink unavailable")

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "fallback"
            outcome = installer.install_package(ROOT, home, symlink_factory=unavailable)
            self.assertEqual("copy", outcome["mode"])
            self.assertFalse((home / ".agents/skills/builder").is_symlink())

    def test_symlink_mode_points_only_into_canonical_package(self):
        installer = load_script("install.py")
        if not installer.symlinks_supported(Path(tempfile.gettempdir())):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            outcome = installer.install_package(ROOT, home)
            self.assertEqual("symlink", outcome["mode"])
            link = home / ".agents/skills/builder"
            self.assertTrue(link.is_symlink())
            self.assertEqual(
                (home / ".codex/plugins/my-codex-harness/skills/builder").resolve(),
                link.resolve(),
            )

    def test_injected_failure_rolls_back_mutations_and_records_journal(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".codex").mkdir()
            config = home / ".codex/config.toml"
            config.write_text('model = "keep"\n')
            baseline = snapshot(home)

            def fail_after_config(action: str, _path: Path) -> None:
                if action == "config-written":
                    raise OSError("injected failure")

            with self.assertRaisesRegex(OSError, "injected failure"):
                installer.install_package(ROOT, home, mutation_hook=fail_after_config)

            current = snapshot(home)
            journal_paths = [path for path in current if ".codex/my-codex-harness/journals/" in path]
            for path, value in baseline.items():
                self.assertEqual(value, current[path], path)
            self.assertFalse((home / ".codex/plugins/my-codex-harness").exists())
            self.assertFalse((home / STATE_RELATIVE).exists())
            backup_dir = home / ".codex/my-codex-harness/backups"
            self.assertEqual([], list(backup_dir.iterdir()) if backup_dir.exists() else [])
            self.assertTrue(journal_paths)
            journal_file = next(home / path for path in journal_paths if path.endswith(".json"))
            self.assertEqual("rolled-back", json.loads(journal_file.read_text())["status"])

    def test_rollback_preserves_and_reports_unknown_drift(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)

            def drift_then_fail(action: str, path: Path) -> None:
                if action == "config-written":
                    path.write_text("external drift\n")
                    raise OSError("injected failure after drift")

            with self.assertRaisesRegex(RuntimeError, "preserved drifted rollback path"):
                installer.install_package(ROOT, home, mutation_hook=drift_then_fail)

            self.assertEqual("external drift\n", (home / ".codex/config.toml").read_text())
            journals = list((home / ".codex/my-codex-harness/journals").glob("install-*.json"))
            self.assertEqual(1, len(journals))
            self.assertEqual("rollback-incomplete", json.loads(journals[0].read_text())["status"])

    def test_failed_package_staging_removes_partial_stage(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "plugins/my-codex-harness"
            original = installer.copy_package

            def partial_then_fail(_source: Path, stage: Path) -> None:
                stage.mkdir(parents=True)
                (stage / "partial").write_text("partial\n")
                raise OSError("copy failed")

            installer.copy_package = partial_then_fail
            try:
                with self.assertRaisesRegex(OSError, "copy failed"):
                    installer._stage_package(ROOT, destination)
            finally:
                installer.copy_package = original
            self.assertEqual([], list(destination.parent.iterdir()))

    def test_replace_failure_restores_destination_backup(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            source.write_text("new\n")
            destination.write_text("old\n")
            original = installer.os.replace
            calls = 0

            def fail_second_replace(old, new):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("replace failed")
                return original(old, new)

            installer.os.replace = fail_second_replace
            try:
                with self.assertRaisesRegex(OSError, "replace failed"):
                    installer._atomic_replace_tree(source, destination)
            finally:
                installer.os.replace = original
            self.assertEqual("old\n", destination.read_text())
            self.assertEqual([], [path for path in root.iterdir() if "rollback" in path.name])

    def test_atomic_file_post_commit_fault_rolls_back_new_file(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            original = installer.atomic_write_bytes

            def write_then_fail(path: Path, content: bytes) -> None:
                original(path, content)
                if path.name == "harness-builder.toml":
                    raise OSError("post-commit file fault")

            installer.atomic_write_bytes = write_then_fail
            try:
                with self.assertRaisesRegex(OSError, "post-commit file fault"):
                    installer.install_package(ROOT, home)
            finally:
                installer.atomic_write_bytes = original
            self.assertFalse((home / ".codex/plugins/my-codex-harness").exists())
            self.assertFalse((home / ".codex/agents/harness-builder.toml").exists())
            self.assertFalse((home / STATE_RELATIVE).exists())

    def test_tree_post_commit_fault_rolls_back_new_tree(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            canonical = home / ".codex/plugins/my-codex-harness"
            original = installer.os.replace

            def replace_then_fail(old, new):
                result = original(old, new)
                if Path(new) == canonical and ".stage-" in Path(old).name:
                    raise OSError("post-commit tree fault")
                return result

            installer.os.replace = replace_then_fail
            try:
                with self.assertRaisesRegex(OSError, "post-commit tree fault"):
                    installer.install_package(ROOT, home)
            finally:
                installer.os.replace = original
            self.assertFalse(canonical.exists())
            self.assertFalse((home / STATE_RELATIVE).exists())

    def test_state_post_commit_fault_preserves_committed_install_with_warning(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            state_path = home / STATE_RELATIVE
            original = installer.atomic_write_json

            def write_then_fail(path: Path, value: object) -> None:
                original(path, value)
                if path == state_path:
                    raise OSError("post-commit state fault")

            installer.atomic_write_json = write_then_fail
            try:
                result = installer.install_package(ROOT, home)
            finally:
                installer.atomic_write_json = original
            self.assertTrue(result["cleanupIncomplete"])
            self.assertTrue((home / ".codex/plugins/my-codex-harness").is_dir())
            self.assertTrue(state_path.is_file())
            journal = home / result["lastJournal"]
            self.assertEqual("cleanup-incomplete", json.loads(journal.read_text())["status"])

    def test_final_journal_fault_does_not_rollback_committed_install(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            original = installer._write_journal

            def fail_completed(path, status, actions, errors=None):
                if status == "completed":
                    raise OSError("journal completion failed")
                return original(path, status, actions, errors)

            installer._write_journal = fail_completed
            try:
                result = installer.install_package(ROOT, home)
            finally:
                installer._write_journal = original
            self.assertTrue(result["cleanupIncomplete"])
            self.assertTrue((home / STATE_RELATIVE).is_file())
            self.assertTrue((home / ".codex/plugins/my-codex-harness").is_dir())
            self.assertIn("journal completion failed", " ".join(result["installWarnings"]))

    def test_cli_returns_warning_status_for_committed_cleanup_failure(self):
        installer = load_script("install.py")
        outcome = {
            "version": "0.2.0",
            "mode": "copy",
            "cleanupIncomplete": True,
            "installWarnings": ["cleanup failed"],
        }
        stderr = io.StringIO()
        stdout = io.StringIO()
        with mock.patch.object(installer, "install_package", return_value=outcome):
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                code = installer.main(["--yes"])
        self.assertEqual(1, code)
        self.assertIn("installed", stdout.getvalue().lower())
        self.assertIn("cleanup failed", stderr.getvalue().lower())

    def test_plan_revalidation_detects_target_drift_before_installer_writes(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            plan = installer.build_plan(ROOT, home)
            conflict = home / ".codex/agents/harness-builder.toml"
            conflict.parent.mkdir(parents=True)
            conflict.write_text("external\n")
            before = snapshot(home)

            with self.assertRaisesRegex(ValueError, "conflict"):
                installer._revalidate_plan(plan)

            self.assertEqual(before, snapshot(home))

    def test_upgrade_rejects_drift_and_replaces_owned_unchanged_content(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            source = base / "source"
            first = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, first.returncode, first.stderr)

            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", ".worktrees", "__pycache__"))
            manifest_path = source / ".codex-plugin/plugin.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["version"] = "0.2.0"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            (source / "CHANGELOG.md").write_text("upgrade\n")

            managed_agent = home / ".codex/agents/harness-builder.toml"
            managed_agent.write_text("local drift\n")
            rejected = run_script(source / "scripts/install.py", home, "--yes", "--copy")
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("drift", rejected.stderr.lower())
            managed_agent.write_bytes((ROOT / "agents/harness-builder.toml").read_bytes())

            upgraded = run_script(source / "scripts/install.py", home, "--yes", "--copy")
            self.assertEqual(0, upgraded.returncode, upgraded.stderr)
            state = json.loads((home / STATE_RELATIVE).read_text())
            self.assertEqual("0.2.0", state["version"])
            self.assertEqual("upgrade\n", (home / ".codex/plugins/my-codex-harness/CHANGELOG.md").read_text())

    def test_upgrade_cleanup_failure_keeps_new_install_and_tracks_remaining_backups(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            first = installer.install_package(ROOT, home, force_copy=True)
            self.assertEqual("0.1.0", first["version"])
            source = copy_source(base, "0.2.0")
            (source / "CHANGELOG.md").write_text("v2\n")
            original = installer.remove_path
            cleanup_calls = 0

            def fail_second_cleanup(path: Path) -> None:
                nonlocal cleanup_calls
                if ".rollback-" in path.name:
                    cleanup_calls += 1
                    if cleanup_calls == 2:
                        raise OSError("cleanup failed")
                original(path)

            installer.remove_path = fail_second_cleanup
            try:
                result = installer.install_package(source, home, force_copy=True)
            finally:
                installer.remove_path = original

            self.assertTrue(result["cleanupIncomplete"])
            self.assertEqual("0.2.0", json.loads((home / STATE_RELATIVE).read_text())["version"])
            self.assertEqual("v2\n", (home / ".codex/plugins/my-codex-harness/CHANGELOG.md").read_text())
            saved = json.loads((home / STATE_RELATIVE).read_text())
            self.assertTrue(saved["cleanupBackups"])
            for entry in saved["cleanupBackups"]:
                self.assertTrue((home / entry["path"]).exists(), entry)
                self.assertEqual(entry["sha256"], installer.hash_path(home / entry["path"]))
            journal = home / saved["lastJournal"]
            self.assertEqual("cleanup-incomplete", json.loads(journal.read_text())["status"])

    def test_upgrade_can_add_then_remove_skills_with_exact_ownership(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            installer.install_package(ROOT, home, force_copy=True)
            source2 = copy_source(base, "0.2.0")
            new_skill = source2 / "skills/extra-skill"
            new_skill.mkdir()
            (new_skill / "SKILL.md").write_text("extra\n")

            second = installer.install_package(source2, home, force_copy=True)
            self.assertTrue((home / ".agents/skills/extra-skill/SKILL.md").is_file())
            self.assertIn(".agents/skills/extra-skill", second["ownedPaths"])

            source3 = copy_source(base, "0.3.0")
            shutil.rmtree(source3 / "skills/grilling")
            third = installer.install_package(source3, home, force_copy=True)
            self.assertFalse((home / ".agents/skills/grilling").exists())
            self.assertFalse((home / ".agents/skills/extra-skill").exists())
            self.assertNotIn(".agents/skills/grilling", third["ownedPaths"])
            self.assertNotIn(".agents/skills/extra-skill", third["ownedPaths"])
            self.assertEqual(set(third["ownedPaths"]), set(third["hashes"]))

    def test_added_skill_conflict_fails_upgrade_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            installed = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, installed.returncode, installed.stderr)
            source = copy_source(base, "0.2.0")
            skill = source / "skills/extra-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("extra\n")
            conflict = home / ".agents/skills/extra-skill"
            conflict.mkdir()
            (conflict / "local").write_text("keep\n")
            before = snapshot(home)

            result = run_script(source / "scripts/install.py", home, "--yes", "--copy")

            self.assertNotEqual(0, result.returncode)
            self.assertEqual(before, snapshot(home))
            self.assertIn("conflict", result.stderr.lower())

    def test_removed_skill_is_restored_when_upgrade_fails_before_state_commit(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            installer.install_package(ROOT, home, force_copy=True)
            source = copy_source(base, "0.2.0")
            shutil.rmtree(source / "skills/grilling")
            state_path = home / STATE_RELATIVE
            original = installer.atomic_write_json

            def fail_before_state(path: Path, value: object) -> None:
                if path == state_path:
                    raise OSError("state unavailable")
                original(path, value)

            installer.atomic_write_json = fail_before_state
            try:
                with self.assertRaisesRegex(OSError, "state unavailable"):
                    installer.install_package(source, home, force_copy=True)
            finally:
                installer.atomic_write_json = original
            self.assertTrue((home / ".agents/skills/grilling/SKILL.md").is_file())
            self.assertEqual("0.1.0", json.loads(state_path.read_text())["version"])

    def test_malicious_owned_path_traversal_is_rejected_without_external_write(self):
        for operation in ("upgrade", "uninstall"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                home = base / "home"
                installed = run_script(INSTALL, home, "--yes", "--copy")
                self.assertEqual(0, installed.returncode, installed.stderr)
                outside = base / "outside"
                outside.write_text("keep\n")
                state_path = home / STATE_RELATIVE
                state = json.loads(state_path.read_text())
                state["ownedPaths"].append("../outside")
                state["hashes"]["../outside"] = hashlib.sha256(b"file\0keep\n").hexdigest()
                state_path.write_text(json.dumps(state, indent=2) + "\n")
                before = snapshot(home)

                script = INSTALL if operation == "upgrade" else UNINSTALL
                result = run_script(script, home, "--yes", "--copy") if operation == "upgrade" else run_script(script, home, "--yes")

                self.assertNotEqual(0, result.returncode)
                self.assertEqual("keep\n", outside.read_text())
                self.assertEqual(before, snapshot(home))
                self.assertRegex(
                    result.stderr.lower(), r"owned path|unexpected path|non-canonical|traversal"
                )

    def test_uninstall_removes_only_hash_matching_owned_paths_and_owned_config_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".codex/config.toml"
            config.parent.mkdir(parents=True)
            config.write_text("[agents]\nmax_threads = 19\n")
            installed = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, installed.returncode, installed.stderr)
            with config.open("a") as stream:
                stream.write("\n[keep]\nvalue = true\n")

            result = run_script(UNINSTALL, home, "--yes")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse((home / ".codex/plugins/my-codex-harness").exists())
            self.assertFalse((home / STATE_RELATIVE).exists())
            text = config.read_text()
            self.assertIn("max_threads = 19", text)
            self.assertNotIn("max_depth", text)
            self.assertNotIn("interrupt_message", text)
            self.assertIn("[keep]\nvalue = true", text)

    def test_uninstall_refuses_drift_without_deleting_any_owned_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, installed.returncode, installed.stderr)
            agent = home / ".codex/agents/harness-reviewer.toml"
            agent.write_text("local drift\n")
            before = snapshot(home)

            result = run_script(UNINSTALL, home, "--yes")

            self.assertNotEqual(0, result.returncode)
            self.assertEqual(before, snapshot(home))
            self.assertIn("drift", result.stderr.lower())

    def test_uninstall_move_post_commit_fault_restores_owned_path(self):
        installer = load_script("install.py")
        uninstaller = load_script("uninstall.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installer.install_package(ROOT, home, force_copy=True)
            package = home / ".codex/plugins/my-codex-harness"
            original = uninstaller.os.replace

            def move_then_fail(old, new):
                result = original(old, new)
                if Path(old) == package and ".uninstall-" in Path(new).name:
                    raise OSError("uninstall move post-commit fault")
                return result

            uninstaller.os.replace = move_then_fail
            try:
                with self.assertRaisesRegex(OSError, "post-commit fault"):
                    uninstaller.uninstall_package(home)
            finally:
                uninstaller.os.replace = original
            self.assertTrue(package.is_dir())
            self.assertTrue((home / STATE_RELATIVE).is_file())

    def test_uninstall_config_post_commit_fault_restores_config(self):
        installer = load_script("install.py")
        uninstaller = load_script("uninstall.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installer.install_package(ROOT, home, force_copy=True)
            config = home / ".codex/config.toml"
            before = config.read_bytes()
            original = uninstaller.atomic_write_bytes
            faulted = False

            def write_then_fail(path: Path, content: bytes) -> None:
                nonlocal faulted
                original(path, content)
                if path == config and not faulted:
                    faulted = True
                    raise OSError("uninstall config post-commit fault")

            uninstaller.atomic_write_bytes = write_then_fail
            try:
                with self.assertRaisesRegex(OSError, "post-commit fault"):
                    uninstaller.uninstall_package(home)
            finally:
                uninstaller.atomic_write_bytes = original
            self.assertEqual(before, config.read_bytes())
            self.assertTrue((home / STATE_RELATIVE).is_file())

    def test_ownership_hashes_do_not_ignore_sensitive_looking_local_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, installed.returncode, installed.stderr)
            local = home / ".agents/skills/builder/token-local-note"
            local.write_text("do not delete\n")
            before = snapshot(home)

            result = run_script(UNINSTALL, home, "--yes")

            self.assertNotEqual(0, result.returncode)
            self.assertEqual(before, snapshot(home))
            self.assertEqual("do not delete\n", local.read_text())
            self.assertIn("drift", result.stderr.lower())

    def test_uninstall_config_edit_ignores_agents_text_inside_multiline_string(self):
        uninstaller = load_script("uninstall.py")
        config = (
            'prompt = """\n[agents]\nmax_depth = 99\n"""\n\n'
            "[agents]\nmax_depth = 1\nmax_threads = 12\n"
        )
        changed = uninstaller._remove_config_keys(config, {"max_depth": 1})
        self.assertIn('[agents]\nmax_depth = 99\n"""', changed)
        self.assertNotIn("[agents]\nmax_depth = 1", changed)
        self.assertIn("max_threads = 12", changed)

    def test_rejects_symlink_and_special_targets_before_writing(self):
        for kind in ("home-symlink", "config-fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                real_home = base / "real"
                real_home.mkdir()
                if kind == "home-symlink":
                    home = base / "home"
                    home.symlink_to(real_home, target_is_directory=True)
                else:
                    home = real_home
                    (home / ".codex").mkdir()
                    os.mkfifo(home / ".codex/config.toml")
                before = snapshot(base)

                result = run_script(INSTALL, home, "--yes")

                self.assertNotEqual(0, result.returncode)
                self.assertEqual(before, snapshot(base))
                self.assertRegex(result.stderr.lower(), r"symlink|special|regular")

    def test_doctor_checks_prerequisites_and_installed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            before = run_script(DOCTOR, home)
            self.assertEqual(0, before.returncode, before.stderr)
            self.assertIn("python", before.stdout.lower())
            self.assertIn("git", before.stdout.lower())
            self.assertIn("symlinks", before.stdout.lower())

            installed = run_script(INSTALL, home, "--yes")
            self.assertEqual(0, installed.returncode, installed.stderr)
            after = run_script(DOCTOR, home, "--installed")
            self.assertEqual(0, after.returncode, after.stderr)
            self.assertIn("installation: healthy", after.stdout.lower())


if __name__ == "__main__":
    unittest.main()
