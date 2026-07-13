import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/build_bundle.py"
DOCTOR = ROOT / "scripts/doctor.py"


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


def write_custom_bundle(
    directory: Path, entries: list[tuple[str, bytes | None | str]], manifest: dict
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
    (directory / "bundle-manifest.json").write_bytes(manifest_bytes)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (directory / "custom.tar.gz.sha256").write_text(f"{digest}  custom.tar.gz\n")
    return archive


class BundleTests(unittest.TestCase):
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
            self.assertEqual("0.1.0", manifest["version"])
            self.assertIn("sourceCommit", manifest)
            self.assertTrue(manifest["files"])

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
            manifest = {
                "version": "0.1.0",
                "sourceCommit": "test",
                "files": {"payload.txt": hashlib.sha256(payload).hexdigest()},
            }
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
            manifest = {
                "version": "0.1.0",
                "sourceCommit": "test",
                "files": {"payload.txt": hashlib.sha256(payload).hexdigest()},
            }
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
            manifest = {
                "version": "0.1.0",
                "sourceCommit": "test",
                "files": {"payload.txt": hashlib.sha256(payload).hexdigest()},
            }
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
            manifest = {
                "version": "0.1.0",
                "sourceCommit": "test",
                "files": {"docs/payload.txt": hashlib.sha256(payload).hexdigest()},
            }
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
            manifest = {
                "version": "0.1.0",
                "sourceCommit": "test",
                "files": {"docs//payload.txt": hashlib.sha256(payload).hexdigest()},
            }
            archive = write_custom_bundle(
                base,
                [("root/docs//payload.txt", payload)],
                manifest,
            )
            with self.assertRaisesRegex(ValueError, "manifest path"):
                doctor._verify_bundle(archive)

    def test_bootstrap_stops_on_error_and_orders_doctor_preview_install(self):
        script = (ROOT / "scripts/bootstrap.sh").read_text()
        self.assertRegex(script, r"set -[^\n]*e")
        doctor = script.index("doctor.py")
        preview = script.index('install.py" --dry-run')
        install = script.index('install.py" --yes')
        self.assertLess(doctor, preview)
        self.assertLess(preview, install)


if __name__ == "__main__":
    unittest.main()
