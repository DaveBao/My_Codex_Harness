import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/build_bundle.py"
DOCTOR = ROOT / "scripts/doctor.py"
INSTALL = ROOT / "scripts/install.py"
BOOTSTRAP = ROOT / "scripts/bootstrap.sh"
DESIRED_AGENTS = {
    "max_depth": 1,
    "max_threads": 6,
    "interrupt_message": True,
}


def run_builder(output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BUILDER), "--output", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def load_doctor():
    spec = importlib.util.spec_from_file_location("harness_doctor", DOCTOR)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load doctor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_doctor(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"HOME": str(home), "USERPROFILE": str(home)})
    return subprocess.run(
        [sys.executable, str(DOCTOR), *arguments],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def legacy_python() -> str | None:
    candidates = [shutil.which("python3"), "/usr/bin/python3"]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        result = subprocess.run(
            [
                candidate,
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info < (3, 11) else 1)",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return candidate
    return None


def make_bootstrap_fixture(root: Path) -> Path:
    script = root / "bootstrap.sh"
    script.write_bytes(BOOTSTRAP.read_bytes())
    script.chmod(0o755)
    (root / "doctor.py").write_text("")
    (root / "install.py").write_text("")
    return script


def write_fake_python(path: Path, *, compatible: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        'if [ "${1-}" = "-c" ]; then\n'
        f"  exit {0 if compatible else 1}\n"
        "fi\n"
        'printf "%s|%s\\n" "$0" "$*" >> "$BOOTSTRAP_LOG"\n'
    )
    path.chmod(0o755)


def bundle_manifest(files: dict[str, str]) -> dict:
    return {
        "version": "0.1.0",
        "sourceCommit": "test",
        "desiredAgents": DESIRED_AGENTS,
        "files": files,
    }


def write_custom_bundle(
    directory: Path,
    entries: list[tuple[str, bytes | None | str]],
    manifest: dict,
    *,
    external_manifest: dict | None = None,
) -> Path:
    archive = directory / "custom.tar.gz"
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    with tarfile.open(archive, "w:gz") as stream:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            if content == "directory":
                info.type = tarfile.DIRTYPE
                stream.addfile(info)
            elif content is None:
                info.type = tarfile.FIFOTYPE
                stream.addfile(info)
            else:
                info.size = len(content)
                stream.addfile(info, io.BytesIO(content))
        info = tarfile.TarInfo("root/bundle-manifest.json")
        info.size = len(manifest_bytes)
        stream.addfile(info, io.BytesIO(manifest_bytes))
    external_bytes = (
        json.dumps(external_manifest if external_manifest is not None else manifest, indent=2, sort_keys=True)
        + "\n"
    ).encode()
    (directory / "bundle-manifest.json").write_bytes(external_bytes)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (directory / "custom.tar.gz.sha256").write_text(f"{digest}  custom.tar.gz\n")
    return archive


class BundleTests(unittest.TestCase):
    def test_bundle_builder_rejects_python_older_than_3_11_before_output(self):
        candidates = dict.fromkeys(
            candidate for candidate in (shutil.which("python3"), "/usr/bin/python3") if candidate
        )
        tested = 0
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for index, python in enumerate(candidates):
                if not Path(python).is_file():
                    continue
                capability = subprocess.run(
                    [
                        python,
                        "-c",
                        "import sys; raise SystemExit(0 if sys.version_info < (3, 11) else 1)",
                    ]
                )
                if capability.returncode != 0:
                    continue
                tested += 1
                output = base / f"output-{index}"
                result = subprocess.run(
                    [python, str(BUILDER), "--output", str(output)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                with self.subTest(python=python):
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual("", result.stdout)
                    self.assertEqual(1, len(result.stderr.splitlines()), result.stderr)
                    self.assertIn("python 3.11", result.stderr.lower())
                    self.assertNotIn("traceback", result.stderr.lower())
                    self.assertFalse(output.exists())
        if not tested:
            self.skipTest("Python older than 3.11 is unavailable")

    def test_bundle_is_reproducible_and_has_manifest_and_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = base / "first"
            second = base / "second"
            one = run_builder(first)
            two = run_builder(second)
            self.assertEqual(0, one.returncode, one.stderr)
            self.assertEqual(0, two.returncode, two.stderr)

            archive_name = "my-codex-harness-0.1.0.tar.gz"
            archive_one = first / archive_name
            archive_two = second / archive_name
            self.assertEqual(archive_one.read_bytes(), archive_two.read_bytes())
            digest = hashlib.sha256(archive_one.read_bytes()).hexdigest()
            self.assertEqual(f"{digest}  {archive_name}\n", (first / f"{archive_name}.sha256").read_text())
            manifest = json.loads((first / "bundle-manifest.json").read_text())
            self.assertEqual(
                {"version", "sourceCommit", "desiredAgents", "files"}, set(manifest)
            )
            self.assertEqual("0.1.0", manifest["version"])
            self.assertIn("sourceCommit", manifest)
            self.assertEqual(DESIRED_AGENTS, manifest["desiredAgents"])
            self.assertTrue(manifest["files"])
            for name in ("harness_context.py", "harness_control.py"):
                relative = f"skills/orchestrator/scripts/{name}"
                self.assertEqual(
                    hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
                    manifest["files"][relative],
                )

    def test_doctor_validates_complex_toml_with_stdlib_tomllib(self):
        doctor = load_doctor()
        config = '''
# common Codex configuration forms
model = "gpt-5"
features = ["one", { name = "two", enabled = true }]
prompt = """
# brackets inside multiline strings are data
[not.a.table]
"""
literal = ''' + "'''\nliteral # text\n'''" + '''

[agents]
max_threads = 6
interrupt_message = true

[profiles."team.alpha"]
args = [
  "--flag",
]
environment = { KEY = "value#kept", nested = { enabled = true } }

["dotted"."table"]
"quoted.key".value = "ok"

[[tools]]
name = 'one'
'''
        doctor._validate_toml_parseability(config)

    def test_doctor_rejects_semantically_invalid_toml(self):
        doctor = load_doctor()
        invalid = (
            "x = 1\nx = 2\n",
            "x = [1 2]\n",
            "x =\n",
            "x = TRUE\n",
            "[broken\nvalue = [\n",
            'value = "unterminated\n',
        )
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                doctor._validate_toml_parseability(config)

    def test_doctor_requires_python_3_11_without_traceback(self):
        python = legacy_python()
        if python is None:
            self.skipTest("Python older than 3.11 is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [python, str(DOCTOR)],
                cwd=ROOT,
                env={**os.environ, "HOME": directory, "USERPROFILE": directory},
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual(1, len(result.stderr.splitlines()), result.stderr)
        self.assertIn("python 3.11", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())

    def test_doctor_rejects_system_python_older_than_3_11_without_traceback(self):
        python = Path("/usr/bin/python3")
        if not python.is_file():
            self.skipTest("system Python is unavailable")
        capability = subprocess.run(
            [
                str(python),
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info < (3, 11) else 1)",
            ]
        )
        if capability.returncode != 0:
            self.skipTest("system Python is already 3.11 or newer")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [str(python), str(DOCTOR)],
                cwd=ROOT,
                env={**os.environ, "HOME": directory, "USERPROFILE": directory},
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual(1, len(result.stderr.splitlines()), result.stderr)
        self.assertIn("python 3.11", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())

    def test_doctor_help_documents_python_3_11_requirement(self):
        result = subprocess.run(
            [sys.executable, str(DOCTOR), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("python 3.11", " ".join(result.stdout.lower().split()))

    def test_doctor_cli_rejects_invalid_config_toml(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".codex/config.toml"
            config.parent.mkdir()
            config.write_text("[broken\nvalue = [\n")

            result = run_doctor(home)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("toml", result.stderr.lower())

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not authoritative on Windows")
    def test_doctor_rejects_existing_nonwritable_user_directories_by_mode_bits(self):
        doctor = load_doctor()
        for name in (".codex", ".agents"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                target = home / name
                target.mkdir()
                target.chmod(0o555)
                try:
                    with self.assertRaisesRegex(ValueError, "writable"):
                        doctor._check_prerequisites(home, False)
                finally:
                    target.chmod(0o755)

    def test_doctor_write_probes_leave_missing_user_directories_absent(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            doctor._check_prerequisites(home, False)
            self.assertEqual([], list(home.iterdir()))

    def test_doctor_rejects_symlinked_user_directory(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            outside = base / "outside"
            home.mkdir()
            outside.mkdir()
            try:
                (home / ".codex").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            with self.assertRaisesRegex(ValueError, "symlink"):
                doctor._check_prerequisites(home, False)

    def test_doctor_validates_all_required_install_state_fields_and_consistency(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = subprocess.run(
                [sys.executable, str(INSTALL), "--yes", "--copy"],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, installed.returncode, installed.stderr)
            state_path = home / ".codex/my-codex-harness/install-state.json"
            state = json.loads(state_path.read_text())
            doctor._validate_install_state(home, state)

            invalid_values = {
                "version": "",
                "sourceCommit": "",
                "mode": "attacker",
                "ownedPaths": {},
                "hashes": [],
                "createdPaths": {},
                "replacedPaths": {},
                "backups": {},
                "addedConfigKeys": [],
                "preservedConfigKeys": [],
                "lastJournal": "",
                "cleanupBackups": {},
                "cleanupIncomplete": "false",
                "installWarnings": {},
            }
            for field, invalid in invalid_values.items():
                candidate = json.loads(json.dumps(state))
                candidate[field] = invalid
                with self.subTest(field=field), self.assertRaises(ValueError):
                    doctor._validate_install_state(home, candidate)

            inconsistent = json.loads(json.dumps(state))
            inconsistent["hashes"].pop(next(iter(inconsistent["hashes"])))
            with self.assertRaises(ValueError):
                doctor._validate_install_state(home, inconsistent)

            inconsistent = json.loads(json.dumps(state))
            inconsistent["replacedPaths"] = [inconsistent["createdPaths"][0]]
            with self.assertRaises(ValueError):
                doctor._validate_install_state(home, inconsistent)

    def test_doctor_rejects_created_or_replaced_paths_outside_owned_paths_and_config(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = subprocess.run(
                [sys.executable, str(INSTALL), "--yes", "--copy"],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, installed.returncode, installed.stderr)
            state = json.loads(
                (home / ".codex/my-codex-harness/install-state.json").read_text()
            )

            for field in ("createdPaths", "replacedPaths"):
                candidate = json.loads(json.dumps(state))
                candidate[field].append(".ssh/id_rsa")
                with self.subTest(field=field), self.assertRaisesRegex(
                    ValueError, "createdPaths|replacedPaths"
                ):
                    doctor._validate_install_state(home, candidate)

    def test_doctor_rejects_untrusted_missing_or_drifted_state_backups(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".codex/config.toml"
            config.parent.mkdir()
            config.write_text('model = "custom"\n')
            installed = subprocess.run(
                [sys.executable, str(INSTALL), "--yes", "--copy"],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, installed.returncode, installed.stderr)
            state = json.loads(
                (home / ".codex/my-codex-harness/install-state.json").read_text()
            )
            doctor._validate_install_state(home, state)

            backup = home / state["backups"][0]["path"]
            original = backup.read_bytes()
            backup.unlink()
            with self.assertRaisesRegex(ValueError, "backups"):
                doctor._validate_install_state(home, state)
            backup.write_bytes(original)

            backup.write_text("drifted\n")
            with self.assertRaisesRegex(ValueError, "backups"):
                doctor._validate_install_state(home, state)
            backup.write_bytes(original)

            malicious = home / ".ssh/id_rsa"
            malicious.parent.mkdir()
            malicious.write_bytes(b"private\n")
            candidate = json.loads(json.dumps(state))
            candidate["backups"] = [
                {
                    "path": ".ssh/id_rsa",
                    "sha256": hashlib.sha256(b"file\0private\n").hexdigest(),
                }
            ]
            with self.assertRaisesRegex(ValueError, "backups"):
                doctor._validate_install_state(home, candidate)

    def test_doctor_rejects_untrusted_cleanup_backups(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = subprocess.run(
                [sys.executable, str(INSTALL), "--yes", "--copy"],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, installed.returncode, installed.stderr)
            state = json.loads(
                (home / ".codex/my-codex-harness/install-state.json").read_text()
            )
            malicious = home / ".ssh/id_rsa"
            malicious.parent.mkdir()
            malicious.write_bytes(b"private\n")
            state["cleanupBackups"] = [
                {
                    "path": ".ssh/id_rsa",
                    "sha256": hashlib.sha256(b"file\0private\n").hexdigest(),
                }
            ]
            state["cleanupIncomplete"] = True
            state["installWarnings"] = ["cleanup failed"]

            with self.assertRaisesRegex(ValueError, "cleanupBackups"):
                doctor._validate_install_state(home, state)

            allowed = ".agents/skills/.builder.rollback-20260714T010203000000Z"
            candidate = json.loads(json.dumps(state))
            candidate["cleanupBackups"] = [
                {"path": allowed, "sha256": hashlib.sha256(b"file\0backup\n").hexdigest()}
            ]
            with self.assertRaisesRegex(ValueError, "cleanupBackups"):
                doctor._validate_install_state(home, candidate)

            backup = home / allowed
            backup.write_bytes(b"backup\n")
            journal_path = home / candidate["lastJournal"]
            journal = json.loads(journal_path.read_text())
            journal["status"] = "cleanup-incomplete"
            journal_path.write_text(json.dumps(journal))
            doctor._validate_install_state(home, candidate)

            candidate["cleanupBackups"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "cleanupBackups"):
                doctor._validate_install_state(home, candidate)

    def test_doctor_validates_last_journal_path_status_and_state_binding(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = subprocess.run(
                [sys.executable, str(INSTALL), "--yes", "--copy"],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, installed.returncode, installed.stderr)
            state = json.loads(
                (home / ".codex/my-codex-harness/install-state.json").read_text()
            )
            journal_path = home / state["lastJournal"]
            journal = json.loads(journal_path.read_text())
            doctor._validate_install_state(home, state)

            fake_journal = home / ".ssh/id_rsa"
            fake_journal.parent.mkdir()
            fake_journal.write_text(json.dumps(journal))
            candidate = json.loads(json.dumps(state))
            candidate["lastJournal"] = ".ssh/id_rsa"
            with self.assertRaisesRegex(ValueError, "lastJournal"):
                doctor._validate_install_state(home, candidate)

            for status in ("running", "cleanup-incomplete"):
                changed = json.loads(json.dumps(journal))
                changed["status"] = status
                journal_path.write_text(json.dumps(changed))
                with self.subTest(status=status), self.assertRaisesRegex(
                    ValueError, "journal|cleanup"
                ):
                    doctor._validate_install_state(home, state)

            unbound = json.loads(json.dumps(journal))
            unbound["actions"] = [
                action for action in unbound["actions"] if action.get("action") != "intent-state"
            ]
            journal_path.write_text(json.dumps(unbound))
            with self.assertRaisesRegex(ValueError, "journal"):
                doctor._validate_install_state(home, state)

    def test_doctor_installed_rejects_missing_identity_or_untrusted_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installed = subprocess.run(
                [sys.executable, str(INSTALL), "--yes", "--copy"],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, installed.returncode, installed.stderr)
            state_path = home / ".codex/my-codex-harness/install-state.json"
            original = json.loads(state_path.read_text())
            cases = (("version", None), ("sourceCommit", None), ("mode", "attacker"))
            for field, value in cases:
                candidate = json.loads(json.dumps(original))
                if value is None:
                    candidate.pop(field)
                else:
                    candidate[field] = value
                state_path.write_text(json.dumps(candidate))
                with self.subTest(field=field):
                    result = run_doctor(home, "--installed")
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("install state", result.stderr.lower())
            state_path.write_text(json.dumps(original))

    def test_doctor_installed_accepts_type_correct_preserved_agent_values(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".codex/config.toml"
            config.parent.mkdir()
            config.write_text("[agents]\nmax_threads = 22\n")
            installed = subprocess.run(
                [sys.executable, str(INSTALL), "--yes", "--copy"],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, installed.returncode, installed.stderr)

            result = run_doctor(home, "--installed")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("installation: healthy", result.stdout.lower())

    def test_archive_is_sorted_normalized_and_excludes_runtime_or_sensitive_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run_builder(output)
            self.assertEqual(0, result.returncode, result.stderr)
            archive = output / "my-codex-harness-0.1.0.tar.gz"
            with tarfile.open(archive, "r:gz") as stream:
                members = stream.getmembers()
                names = [member.name for member in members]
                self.assertEqual(sorted(names), names)
                for member in members:
                    self.assertEqual(0, member.mtime)
                    self.assertEqual(0, member.uid)
                    self.assertEqual(0, member.gid)
                    self.assertEqual("", member.uname)
                    self.assertEqual("", member.gname)
                lowered = "\n".join(names).lower()
                for forbidden in (
                    "/.git/",
                    "/.worktrees/",
                    "/dist/",
                    "/__pycache__/",
                    "/credentials",
                    "/tokens",
                    "/sessions/",
                    "/cache/",
                    "/.env",
                ):
                    self.assertNotIn(forbidden, lowered)
                self.assertFalse(any(name.count("/") == 2 and name.endswith("/.codex/config.toml") for name in names))
                self.assertTrue(any(name.endswith("/scaffold/.codex/config.toml") for name in names))
                self.assertTrue(any(name.endswith("/bundle-manifest.json") for name in names))

    def test_manifest_hashes_every_archived_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run_builder(output)
            self.assertEqual(0, result.returncode, result.stderr)
            archive = output / "my-codex-harness-0.1.0.tar.gz"
            with tarfile.open(archive, "r:gz") as stream:
                root_name = stream.getmembers()[0].name.split("/", 1)[0]
                inner = json.loads(stream.extractfile(f"{root_name}/bundle-manifest.json").read())
                for relative, expected in inner["files"].items():
                    data = stream.extractfile(f"{root_name}/{relative}").read()
                    self.assertEqual(expected, hashlib.sha256(data).hexdigest(), relative)

    def test_extracted_bundle_installs_without_repository_or_network_state(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = base / "bundle"
            result = run_builder(output)
            self.assertEqual(0, result.returncode, result.stderr)
            archive = output / "my-codex-harness-0.1.0.tar.gz"
            extracted = base / "extracted"
            with tarfile.open(archive, "r:gz") as stream:
                stream.extractall(extracted)
            package = next(extracted.iterdir())
            self.assertFalse((package / ".git").exists())
            home = base / "home"
            env = os.environ.copy()
            env.update({"HOME": str(home), "USERPROFILE": str(home), "NO_PROXY": "*"})
            installed = subprocess.run(
                [sys.executable, str(package / "scripts/install.py"), "--yes", "--copy"],
                cwd=package,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, installed.returncode, installed.stderr)
            self.assertTrue((home / ".codex/plugins/my-codex-harness/README.md").is_file())

            checked = subprocess.run(
                [sys.executable, str(package / "scripts/doctor.py"), "--bundle", str(archive)],
                cwd=package,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, checked.returncode, checked.stderr)
            self.assertIn("bundle: valid", checked.stdout.lower())

    def test_doctor_rejects_tampered_extracted_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = base / "bundle"
            result = run_builder(output)
            self.assertEqual(0, result.returncode, result.stderr)
            extracted = base / "extracted"
            with tarfile.open(output / "my-codex-harness-0.1.0.tar.gz", "r:gz") as stream:
                stream.extractall(extracted)
            package = next(extracted.iterdir())
            (package / "README.md").write_text("tampered\n")
            home = base / "home"
            home.mkdir()
            env = os.environ.copy()
            env.update({"HOME": str(home), "USERPROFILE": str(home)})

            checked = subprocess.run(
                [sys.executable, str(package / "scripts/doctor.py")],
                cwd=package,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(0, checked.returncode)
            self.assertIn("hash mismatch", checked.stderr.lower())

    def test_doctor_rejects_unmanifested_sensitive_extracted_file(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = base / "bundle"
            result = run_builder(output)
            self.assertEqual(0, result.returncode, result.stderr)
            extracted = base / "extracted"
            with tarfile.open(output / "my-codex-harness-0.1.0.tar.gz", "r:gz") as stream:
                stream.extractall(extracted)
            package = next(extracted.iterdir())
            (package / ".env").write_text("do-not-ignore\n")

            with self.assertRaisesRegex(ValueError, "file set"):
                load_doctor()._verify_extracted_package(package)

    def test_bundle_doctor_rejects_duplicate_member_names(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload = b"payload\n"
            manifest = bundle_manifest({"payload.txt": hashlib.sha256(payload).hexdigest()})
            archive = write_custom_bundle(
                base,
                [("root/payload.txt", payload), ("root/payload.txt", payload)],
                manifest,
            )
            with self.assertRaisesRegex(ValueError, "duplicate"):
                doctor._verify_bundle(archive)

    def test_bundle_doctor_rejects_fifo_member(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload = b"payload\n"
            manifest = bundle_manifest({"payload.txt": hashlib.sha256(payload).hexdigest()})
            archive = write_custom_bundle(
                base,
                [("root/payload.txt", payload), ("root/runtime.pipe", None)],
                manifest,
            )
            with self.assertRaisesRegex(ValueError, "member type"):
                doctor._verify_bundle(archive)

    def test_bundle_doctor_rejects_empty_member_name(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload = b"payload\n"
            manifest = bundle_manifest({"payload.txt": hashlib.sha256(payload).hexdigest()})
            archive = write_custom_bundle(
                base,
                [("", b"empty-name\n"), ("root/payload.txt", payload)],
                manifest,
            )
            with self.assertRaisesRegex(ValueError, "unsafe bundle member"):
                doctor._verify_bundle(archive)

    def test_bundle_doctor_allows_canonical_directory_members(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload = b"payload\n"
            manifest = bundle_manifest({"docs/payload.txt": hashlib.sha256(payload).hexdigest()})
            archive = write_custom_bundle(
                base,
                [("root/docs/", "directory"), ("root/docs/payload.txt", payload)],
                manifest,
            )
            self.assertEqual(manifest, doctor._verify_bundle(archive))

    def test_bundle_doctor_rejects_noncanonical_manifest_path(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload = b"payload\n"
            manifest = bundle_manifest({"docs//payload.txt": hashlib.sha256(payload).hexdigest()})
            archive = write_custom_bundle(
                base,
                [("root/docs//payload.txt", payload)],
                manifest,
            )
            with self.assertRaisesRegex(ValueError, "manifest path"):
                doctor._verify_bundle(archive)

    def test_bundle_doctor_rejects_invalid_external_manifest_schema(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload = b"payload\n"
            manifest = bundle_manifest({"payload.txt": hashlib.sha256(payload).hexdigest()})
            archive = write_custom_bundle(base, [("root/payload.txt", payload)], manifest)
            external = dict(manifest)
            external.pop("desiredAgents")
            (base / "bundle-manifest.json").write_text(json.dumps(external))
            with self.assertRaisesRegex(ValueError, "external bundle manifest"):
                doctor._verify_bundle(archive)

    def test_bundle_doctor_rejects_invalid_internal_manifest_schema(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload = b"payload\n"
            external = bundle_manifest({"payload.txt": hashlib.sha256(payload).hexdigest()})
            internal = dict(external)
            internal["desiredAgents"] = {**DESIRED_AGENTS, "max_threads": 99}
            archive = write_custom_bundle(
                base,
                [("root/payload.txt", payload)],
                internal,
                external_manifest=external,
            )
            with self.assertRaisesRegex(ValueError, "internal bundle manifest"):
                doctor._verify_bundle(archive)

    def test_extracted_package_doctor_rejects_incomplete_manifest_schema(self):
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = base / "bundle"
            result = run_builder(output)
            self.assertEqual(0, result.returncode, result.stderr)
            extracted = base / "extracted"
            with tarfile.open(output / "my-codex-harness-0.1.0.tar.gz", "r:gz") as stream:
                stream.extractall(extracted)
            package = next(extracted.iterdir())
            manifest_path = package / "bundle-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["unexpected"] = True
            manifest_path.write_text(json.dumps(manifest))

            with self.assertRaisesRegex(ValueError, "package manifest"):
                doctor._verify_extracted_package(package)

    def test_bootstrap_stops_on_error_and_orders_doctor_preview_install(self):
        script = (ROOT / "scripts/bootstrap.sh").read_text()
        self.assertRegex(script, r"set -[^\n]*e")
        doctor = script.index("doctor.py")
        preview = script.index('install.py" --dry-run')
        install = script.index('install.py" --yes')
        self.assertLess(doctor, preview)
        self.assertLess(preview, install)

    def test_bootstrap_uses_one_qualified_override_with_space_safe_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "package with spaces"
            base.mkdir()
            script = make_bootstrap_fixture(base)
            python = Path(directory) / "runtime with spaces/python3"
            write_fake_python(python, compatible=True)
            log = Path(directory) / "bootstrap.log"
            result = subprocess.run(
                ["sh", str(script)],
                env={
                    **os.environ,
                    "HOME": str(Path(directory) / "home"),
                    "HARNESS_PYTHON": str(python),
                    "BOOTSTRAP_LOG": str(log),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                [
                    f"{python}|{base / 'doctor.py'}",
                    f"{python}|{base / 'install.py'} --dry-run",
                    f"{python}|{base / 'install.py'} --yes",
                ],
                log.read_text().splitlines(),
            )

    def test_bootstrap_skips_incompatible_candidate_and_reuses_python_3_12(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "package"
            base.mkdir()
            script = make_bootstrap_fixture(base)
            binaries = Path(directory) / "bin"
            write_fake_python(binaries / "python3.13", compatible=False)
            selected = binaries / "python3.12"
            write_fake_python(selected, compatible=True)
            write_fake_python(binaries / "python3.11", compatible=True)
            write_fake_python(binaries / "python3", compatible=True)
            log = Path(directory) / "bootstrap.log"
            result = subprocess.run(
                ["sh", str(script)],
                env={
                    **os.environ,
                    "PATH": f"{binaries}:/usr/bin:/bin",
                    "HOME": str(Path(directory) / "home"),
                    "BOOTSTRAP_LOG": str(log),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(all(line.startswith(f"{selected}|") for line in log.read_text().splitlines()))

    def test_bootstrap_finds_space_safe_codex_runtime_and_reports_no_python(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "package"
            base.mkdir()
            script = make_bootstrap_fixture(base)
            binaries = Path(directory) / "bin"
            for name in ("python3.13", "python3.12", "python3.11", "python3"):
                write_fake_python(binaries / name, compatible=False)
            home = Path(directory) / "home with spaces"
            runtime = home / ".cache/codex-runtimes/runtime with spaces/dependencies/python/bin/python3"
            write_fake_python(runtime, compatible=True)
            log = Path(directory) / "bootstrap.log"
            environment = {
                **os.environ,
                "PATH": f"{binaries}:/usr/bin:/bin",
                "HOME": str(home),
                "BOOTSTRAP_LOG": str(log),
            }
            found = subprocess.run(
                ["sh", str(script)], env=environment, capture_output=True, text=True
            )
            self.assertEqual(0, found.returncode, found.stderr)
            self.assertTrue(all(line.startswith(f"{runtime}|") for line in log.read_text().splitlines()))

            runtime.unlink()
            missing = subprocess.run(
                ["sh", str(script)], env=environment, capture_output=True, text=True
            )
            self.assertNotEqual(0, missing.returncode)
            self.assertIn("python 3.11", missing.stderr.lower())
            self.assertIn("harness_python", missing.stderr.lower())

    def test_bootstrap_rejects_incompatible_harness_python_without_running_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "package"
            base.mkdir()
            script = make_bootstrap_fixture(base)
            python = Path(directory) / "python3.10"
            write_fake_python(python, compatible=False)
            log = Path(directory) / "bootstrap.log"
            result = subprocess.run(
                ["sh", str(script)],
                env={
                    **os.environ,
                    "HARNESS_PYTHON": str(python),
                    "BOOTSTRAP_LOG": str(log),
                    "HOME": str(Path(directory) / "home"),
                },
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(log.exists())
            self.assertIn("harness_python", result.stderr.lower())
            self.assertIn("python 3.11", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
