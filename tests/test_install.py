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


def run_script_with_python(
    python: Path, script: Path, home: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [str(python), str(script), *args],
        cwd=script.parents[1],
        env=env,
        capture_output=True,
        text=True,
    )


def run_python(home: Path, code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
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


def leave_upgrade_cleanup_warning(installer, source: Path, home: Path) -> dict:
    original = installer.remove_path

    def fail_cleanup(path: Path) -> None:
        if ".rollback-" in path.name:
            raise OSError("cleanup warning")
        original(path)

    installer.remove_path = fail_cleanup
    try:
        return installer.install_package(source, home, force_copy=True)
    finally:
        installer.remove_path = original


class InstallTests(unittest.TestCase):
    def test_public_deployment_clis_reject_python_before_3_11_without_writing(self):
        candidates = [Path(path) for path in (shutil.which("python3"), "/usr/bin/python3") if path]
        old_pythons = []
        for python in dict.fromkeys(candidates):
            if not python.exists():
                continue
            version = subprocess.run(
                [str(python), "-c", "import sys; print(*sys.version_info[:2])"],
                capture_output=True,
                text=True,
                check=True,
            )
            if tuple(map(int, version.stdout.split())) < (3, 11):
                old_pythons.append(python)
        if not old_pythons:
            self.skipTest("no Python older than 3.11 is available")

        for python in old_pythons:
            for script, args in (
                (INSTALL, ("--yes", "--copy")),
                (UNINSTALL, ("--dry-run",)),
            ):
                with self.subTest(python=str(python), script=script.name), tempfile.TemporaryDirectory() as directory:
                    home = Path(directory)
                    before = snapshot(home)

                    result = run_script_with_python(python, script, home, *args)

                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual(1, len(result.stderr.splitlines()), result.stderr)
                    self.assertIn("Python 3.11", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(before, snapshot(home))

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

    def test_next_install_recovers_process_exit_after_package_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            interrupted = run_python(
                home,
                """
import importlib.util
import os
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('interrupted_install', root / 'scripts/install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def stop(action, _path):
    if action == 'package-installed':
        os._exit(93)
module.install_package(root, Path(os.environ['HOME']), force_copy=True, mutation_hook=stop)
""",
            )
            self.assertEqual(93, interrupted.returncode)
            self.assertTrue((home / ".codex/plugins/my-codex-harness").is_dir())
            self.assertFalse((home / STATE_RELATIVE).exists())

            recovered = run_script(INSTALL, home, "--yes", "--copy")

            self.assertEqual(0, recovered.returncode, recovered.stderr)
            self.assertTrue((home / STATE_RELATIVE).is_file())
            journals = sorted((home / ".codex/my-codex-harness/journals").glob("install-*.json"))
            self.assertEqual("rolled-back", json.loads(journals[0].read_text())["status"])

    def test_dry_run_reports_pending_recovery_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            interrupted = run_python(
                home,
                """
import importlib.util
import os
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('interrupted_install', root / 'scripts/install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.install_package(root, Path(os.environ['HOME']), force_copy=True,
    mutation_hook=lambda action, _path: os._exit(93) if action == 'package-installed' else None)
""",
            )
            self.assertEqual(93, interrupted.returncode)
            before = snapshot(home)

            preview = run_script(INSTALL, home, "--dry-run", "--copy")

            self.assertNotEqual(0, preview.returncode)
            self.assertIn("recovery required", preview.stderr.lower())
            self.assertEqual(before, snapshot(home))

    def test_install_recovery_preserves_unknown_drift_and_stays_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            interrupted = run_python(
                home,
                """
import importlib.util
import os
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('interrupted_install', root / 'scripts/install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.install_package(root, Path(os.environ['HOME']), force_copy=True,
    mutation_hook=lambda action, _path: os._exit(93) if action == 'package-installed' else None)
""",
            )
            self.assertEqual(93, interrupted.returncode)
            drift = home / ".codex/plugins/my-codex-harness/local-drift"
            drift.write_text("keep\n")

            recovered = run_script(INSTALL, home, "--yes", "--copy")

            self.assertNotEqual(0, recovered.returncode)
            self.assertEqual("keep\n", drift.read_text())
            journal = next((home / ".codex/my-codex-harness/journals").glob("install-*.json"))
            self.assertEqual("rollback-incomplete", json.loads(journal.read_text())["status"])
            self.assertIn("preserved drifted", recovered.stderr.lower())

    def test_install_recovery_never_rolls_back_committed_state_after_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            interrupted = run_python(
                home,
                """
import importlib.util
import os
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('interrupted_install', root / 'scripts/install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.install_package(root, Path(os.environ['HOME']), force_copy=True,
    mutation_hook=lambda action, _path: os._exit(94) if action == 'state-written' else None)
""",
            )
            self.assertEqual(94, interrupted.returncode)
            state_path = home / STATE_RELATIVE
            state_before = json.loads(state_path.read_text())
            package_hash = load_script("install.py").hash_path(
                home / ".codex/plugins/my-codex-harness"
            )

            recovered = run_script(INSTALL, home, "--yes", "--copy")

            self.assertEqual(0, recovered.returncode, recovered.stderr)
            self.assertEqual(package_hash, load_script("install.py").hash_path(
                home / ".codex/plugins/my-codex-harness"
            ))
            self.assertEqual(state_before["hashes"], json.loads(state_path.read_text())["hashes"])
            journal = home / state_before["lastJournal"]
            self.assertEqual("completed", json.loads(journal.read_text())["status"])

    def test_committed_install_recovery_rejects_json_cleanup_path_outside_backup_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            interrupted = run_python(
                home,
                """
import importlib.util
import os
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('interrupted_install', root / 'scripts/install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.install_package(root, Path(os.environ['HOME']), force_copy=True,
    mutation_hook=lambda action, _path: os._exit(99) if action == 'state-written' else None)
""",
            )
            self.assertEqual(99, interrupted.returncode)
            installer = load_script("install.py")
            state_path = home / STATE_RELATIVE
            state = json.loads(state_path.read_text())
            config = home / ".codex/config.toml"
            state["cleanupBackups"] = [
                {"path": ".codex/config.toml", "sha256": installer.hash_path(config)}
            ]
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
            journal_path = home / state["lastJournal"]
            journal = json.loads(journal_path.read_text())
            journal["actions"][-1]["newHash"] = installer.hash_path(state_path)
            journal_path.write_text(json.dumps(journal, indent=2, sort_keys=True) + "\n")
            config_before = config.read_bytes()

            recovered = run_script(INSTALL, home, "--yes", "--copy")

            self.assertNotEqual(0, recovered.returncode)
            self.assertEqual(config_before, config.read_bytes())
            self.assertIn("cleanup backup", recovered.stderr.lower())

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

    def test_next_install_resumes_cleanup_from_warning_updated_committed_state(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            installer.install_package(ROOT, home, force_copy=True)
            source = copy_source(base, "0.2.0")
            warned = leave_upgrade_cleanup_warning(installer, source, home)
            self.assertTrue(warned["cleanupIncomplete"])
            warned_state = json.loads((home / STATE_RELATIVE).read_text())
            self.assertTrue(warned_state["cleanupBackups"])

            recovered = installer.install_package(source, home, force_copy=True)

            self.assertTrue(recovered["idempotent"])
            state = json.loads((home / STATE_RELATIVE).read_text())
            self.assertEqual([], state["cleanupBackups"])
            self.assertFalse(state["cleanupIncomplete"])
            self.assertEqual([], state["installWarnings"])

    def test_warning_state_recovery_rejects_unknown_owned_hash_drift_without_cleanup(self):
        installer = load_script("install.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            installer.install_package(ROOT, home, force_copy=True)
            source = copy_source(base, "0.2.0")
            leave_upgrade_cleanup_warning(installer, source, home)
            state_path = home / STATE_RELATIVE
            state = json.loads(state_path.read_text())
            state["hashes"][".codex/plugins/my-codex-harness"] = "0" * 64
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
            before = snapshot(home)

            with self.assertRaisesRegex(ValueError, "managed path drift"):
                installer.install_package(source, home, force_copy=True)

            self.assertEqual(before, snapshot(home))

    def test_upgrade_cleanup_resumes_after_process_exit_between_remove_and_state_update(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            installed = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, installed.returncode, installed.stderr)
            source = copy_source(base, "0.2.0")
            interrupted = run_python(
                home,
                f"""
import importlib.util
import os
from pathlib import Path
source = Path({str(source)!r})
spec = importlib.util.spec_from_file_location('interrupted_upgrade', source / 'scripts/install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
remove = module.remove_path
def stop(path):
    remove(path)
    if '.rollback-' in path.name:
        os._exit(100)
module.remove_path = stop
module.install_package(source, Path(os.environ['HOME']), force_copy=True)
""",
            )
            self.assertEqual(100, interrupted.returncode)
            self.assertEqual("0.2.0", json.loads((home / STATE_RELATIVE).read_text())["version"])

            recovered = run_script(source / "scripts/install.py", home, "--yes", "--copy")

            self.assertEqual(0, recovered.returncode, recovered.stderr)
            state = json.loads((home / STATE_RELATIVE).read_text())
            self.assertEqual([], state["cleanupBackups"])
            self.assertFalse(state["cleanupIncomplete"])

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

    def test_next_uninstall_finishes_committed_cleanup_after_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, installed.returncode, installed.stderr)
            interrupted = run_python(
                home,
                """
import importlib.util
import os
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('interrupted_uninstall', root / 'scripts/uninstall.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
remove = module.remove_path
def stop(path):
    remove(path)
    if '.uninstall-' in path.name:
        os._exit(95)
module.remove_path = stop
module.uninstall_package(Path(os.environ['HOME']))
""",
            )
            self.assertEqual(95, interrupted.returncode)
            self.assertFalse((home / STATE_RELATIVE).exists())
            self.assertTrue(list((home / STATE_RELATIVE.parent).glob(".install-state.rollback-*.json")))
            self.assertTrue(list(home.rglob("*.uninstall-*")))

            recovered = run_script(UNINSTALL, home, "--yes")

            self.assertEqual(0, recovered.returncode, recovered.stderr)
            self.assertFalse(list((home / STATE_RELATIVE.parent).glob(".install-state.rollback-*.json")))
            self.assertFalse(list(home.rglob("*.uninstall-*")))
            self.assertFalse((home / ".codex/plugins/my-codex-harness").exists())

    def test_next_uninstall_recovers_process_exit_after_partial_move(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, installed.returncode, installed.stderr)
            interrupted = run_python(
                home,
                """
import importlib.util
import os
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('interrupted_uninstall', root / 'scripts/uninstall.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
replace = module.os.replace
def stop(old, new):
    result = replace(old, new)
    if '.uninstall-' in Path(new).name:
        os._exit(96)
    return result
module.os.replace = stop
module.uninstall_package(Path(os.environ['HOME']))
""",
            )
            self.assertEqual(96, interrupted.returncode)
            self.assertTrue((home / STATE_RELATIVE).is_file())

            recovered = run_script(UNINSTALL, home, "--yes")

            self.assertEqual(0, recovered.returncode, recovered.stderr)
            self.assertFalse((home / STATE_RELATIVE).exists())
            self.assertFalse(list(home.rglob("*.uninstall-*")))

    def test_next_uninstall_recovers_process_exit_after_config_backup_write(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, installed.returncode, installed.stderr)
            interrupted = run_python(
                home,
                """
import importlib.util
import os
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('interrupted_uninstall', root / 'scripts/uninstall.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
write = module.atomic_write_bytes
def stop(path, content):
    write(path, content)
    if 'config.toml.uninstall-' in path.name:
        os._exit(101)
module.atomic_write_bytes = stop
module.uninstall_package(Path(os.environ['HOME']))
""",
            )
            self.assertEqual(101, interrupted.returncode)
            self.assertTrue((home / STATE_RELATIVE).is_file())

            recovered = run_script(UNINSTALL, home, "--yes")

            self.assertEqual(0, recovered.returncode, recovered.stderr)
            backups = home / ".codex/my-codex-harness/backups"
            self.assertFalse(list(backups.glob("config.toml.uninstall-*")))

    def test_next_uninstall_treats_moved_state_as_committed_after_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, installed.returncode, installed.stderr)
            interrupted = run_python(
                home,
                """
import importlib.util
import os
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('interrupted_uninstall', root / 'scripts/uninstall.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
replace = module.os.replace
state = Path(os.environ['HOME']) / '.codex/my-codex-harness/install-state.json'
def stop(old, new):
    result = replace(old, new)
    if Path(old) == state and '.install-state.rollback-' in Path(new).name:
        os._exit(97)
    return result
module.os.replace = stop
module.uninstall_package(Path(os.environ['HOME']))
""",
            )
            self.assertEqual(97, interrupted.returncode)
            self.assertFalse((home / STATE_RELATIVE).exists())
            self.assertTrue(list((home / STATE_RELATIVE.parent).glob(".install-state.rollback-*.json")))

            recovered = run_script(UNINSTALL, home, "--yes")

            self.assertEqual(0, recovered.returncode, recovered.stderr)
            self.assertFalse(list((home / STATE_RELATIVE.parent).glob(".install-state.rollback-*.json")))
            self.assertFalse(list(home.rglob("*.uninstall-*")))

    def test_uninstall_recovery_preserves_drifted_cleanup_backup_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = run_script(INSTALL, home, "--yes", "--copy")
            self.assertEqual(0, installed.returncode, installed.stderr)
            interrupted = run_python(
                home,
                """
import importlib.util
import os
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('interrupted_uninstall', root / 'scripts/uninstall.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
remove = module.remove_path
def stop(path):
    remove(path)
    if '.uninstall-' in path.name:
        os._exit(98)
module.remove_path = stop
module.uninstall_package(Path(os.environ['HOME']))
""",
            )
            self.assertEqual(98, interrupted.returncode)
            backup = next(home.rglob("*.uninstall-*"))
            if backup.is_dir():
                (backup / "external-drift").write_text("keep\n")
            else:
                backup.write_text("keep\n")

            recovered = run_script(UNINSTALL, home, "--yes")

            self.assertNotEqual(0, recovered.returncode)
            self.assertTrue(backup.exists())
            self.assertTrue(list((home / STATE_RELATIVE.parent).glob(".install-state.rollback-*.json")))
            self.assertIn("drifted uninstall backup", recovered.stderr.lower())

    def test_uninstall_without_state_is_not_idempotent_when_managed_backup_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            leftover = home / ".codex/agents/.harness-builder.toml.uninstall-20260713T120000000000Z"
            leftover.parent.mkdir(parents=True)
            leftover.write_text("managed backup\n")

            result = run_script(UNINSTALL, home, "--yes")

            self.assertNotEqual(0, result.returncode)
            self.assertEqual("managed backup\n", leftover.read_text())
            self.assertIn("leftover", result.stderr.lower())

    def test_uninstall_recovery_rejects_json_path_as_delete_authority(self):
        installer = load_script("install.py")
        uninstaller = load_script("uninstall.py")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installer.install_package(ROOT, home, force_copy=True)
            original = uninstaller.remove_path

            def fail_cleanup(path: Path) -> None:
                if ".uninstall-" in path.name:
                    raise OSError("leave recovery")
                original(path)

            uninstaller.remove_path = fail_cleanup
            try:
                with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                    uninstaller.uninstall_package(home)
            finally:
                uninstaller.remove_path = original
            journal_path = next(
                (home / ".codex/my-codex-harness/journals").glob("uninstall-*.json")
            )
            journal = json.loads(journal_path.read_text())
            journal["moves"][0]["backup"] = ".codex/config.toml"
            journal_path.write_text(json.dumps(journal, indent=2, sort_keys=True) + "\n")
            config = home / ".codex/config.toml"
            before = config.read_bytes()

            recovered = run_script(UNINSTALL, home, "--yes")

            self.assertNotEqual(0, recovered.returncode)
            self.assertEqual(before, config.read_bytes())
            self.assertIn("invalid uninstall recovery backup", recovered.stderr.lower())

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
