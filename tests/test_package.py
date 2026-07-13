import json
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.package_model import (
    atomic_write,
    load_json,
    merge_agents_table,
    parse_agents_table,
    safe_relative_path,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]


class PackageContractTests(unittest.TestCase):
    def test_required_public_files_exist_and_are_nonempty(self):
        required = (
            "README.md",
            "LICENSE",
            "NOTICE",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            ".codex-plugin/plugin.json",
            ".agents/plugins/marketplace.json",
        )
        missing = [name for name in required if not (ROOT / name).is_file()]
        self.assertEqual([], missing)
        empty = [name for name in required if not (ROOT / name).read_text().strip()]
        self.assertEqual([], empty)

    def test_plugin_manifest_contract(self):
        path = ROOT / ".codex-plugin/plugin.json"
        self.assertTrue(path.is_file(), f"missing {path.relative_to(ROOT)}")
        manifest = json.loads(path.read_text())

        self.assertEqual("my-codex-harness", manifest["name"])
        self.assertEqual("0.1.0", manifest["version"])
        self.assertEqual("https://github.com/DaveBao/My_Codex_Harness", manifest["repository"])
        self.assertEqual("MIT", manifest["license"])
        self.assertEqual("./skills/", manifest["skills"])
        self.assertTrue(manifest["description"].strip())
        self.assertTrue(manifest["author"]["name"].strip())

        interface = manifest["interface"]
        for key in (
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
            "capabilities",
            "defaultPrompt",
        ):
            self.assertIn(key, interface)
            self.assertTrue(interface[key])
        self.assertEqual("Developer Tools", interface["category"])
        for key in ("apps", "mcpServers", "hooks"):
            self.assertNotIn(key, manifest)
        for key in ("composerIcon", "logo", "logoDark", "screenshots"):
            self.assertNotIn(key, interface)

    def test_marketplace_contract(self):
        path = ROOT / ".agents/plugins/marketplace.json"
        self.assertTrue(path.is_file(), f"missing {path.relative_to(ROOT)}")
        marketplace = json.loads(path.read_text())

        self.assertTrue(marketplace["name"].strip())
        self.assertTrue(marketplace["interface"]["displayName"].strip())
        self.assertEqual(1, len(marketplace["plugins"]))
        plugin = marketplace["plugins"][0]
        self.assertEqual("my-codex-harness", plugin["name"])
        self.assertEqual(
            {
                "source": "url",
                "url": "https://github.com/DaveBao/My_Codex_Harness.git",
                "ref": "main",
            },
            plugin["source"],
        )
        self.assertEqual(
            {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            plugin["policy"],
        )
        self.assertEqual("Developer Tools", plugin["category"])

    def test_validator_accepts_package(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate_package.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("package validation passed\n", result.stdout)

    def test_validator_reports_missing_file_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            scripts = package / "scripts"
            scripts.mkdir()
            shutil.copy2(ROOT / "scripts/package_model.py", scripts)
            shutil.copy2(ROOT / "scripts/validate_package.py", scripts)
            result = subprocess.run(
                [sys.executable, str(scripts / "validate_package.py")],
                cwd=package,
                capture_output=True,
                text=True,
            )

        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual(
            "package validation failed: missing required file: README.md\n",
            result.stderr,
        )


class PackageModelTests(unittest.TestCase):
    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload"
            path.write_bytes(b"harness\n")
            self.assertEqual(hashlib.sha256(b"harness\n").hexdigest(), sha256_file(path))

    def test_safe_relative_path_rejects_root_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            self.assertEqual("inside/file", safe_relative_path(root, root / "inside/file"))
            with self.assertRaises(ValueError):
                safe_relative_path(root, root / "../outside")

    def test_safe_relative_path_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "link").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "escapes root"):
                safe_relative_path(root, root / "link/file")

    def test_load_json_requires_an_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text('{"name": "harness"}')
            self.assertEqual({"name": "harness"}, load_json(path))
            path.write_text("[]")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                load_json(path)

    def test_atomic_write_replaces_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.toml"
            atomic_write(path, "first\n")
            atomic_write(path, "second\n")
            self.assertEqual("second\n", path.read_text())
            self.assertEqual([path], list(path.parent.iterdir()))

    def test_atomic_write_rejects_symlink_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("original\n")
            link = root / "config"
            link.symlink_to(target)

            with self.assertRaisesRegex(ValueError, "symlink"):
                atomic_write(link, "replacement\n")

            self.assertTrue(link.is_symlink())
            self.assertEqual("original\n", target.read_text())

    def test_parse_agents_table(self):
        config = 'model = "gpt-5"\n\n[agents]\nreviewer = "custom.toml"\n'
        self.assertEqual({"reviewer": "custom.toml"}, parse_agents_table(config))

    def test_parse_agents_table_supports_required_scalars(self):
        config = (
            "[agents]\n"
            "max_threads = 0x10\n"
            "max_depth = 3\n"
            "job_max_runtime_seconds = 1_200\n"
            "interrupt_message = true\n"
            "reviewer = 'custom.toml'\n"
        )
        self.assertEqual(
            {
                "max_threads": 16,
                "max_depth": 3,
                "job_max_runtime_seconds": 1200,
                "interrupt_message": True,
                "reviewer": "custom.toml",
            },
            parse_agents_table(config),
        )

    def test_parse_agents_table_rejects_duplicate_table(self):
        config = '[agents]\nreviewer = "one"\n\n[agents]\nbuilder = "two"\n'
        with self.assertRaisesRegex(ValueError, r"duplicate \[agents\] table"):
            parse_agents_table(config)

    def test_parse_agents_table_rejects_duplicate_key(self):
        config = '[agents]\nreviewer = "one"\nreviewer = "two"\n'
        with self.assertRaisesRegex(ValueError, r"duplicate key in \[agents\]: reviewer"):
            parse_agents_table(config)

    def test_parse_agents_table_rejects_unsupported_values(self):
        values = (
            "1.5",
            "[1, 2]",
            '{ path = "builder.toml" }',
            "[\n  1,\n]",
        )
        for value in values:
            with self.subTest(value=value):
                config = f"[agents]\nunsupported = {value}\n"
                with self.assertRaisesRegex(
                    ValueError,
                    r"\[agents\] supports only single-line strings, integers, and booleans",
                ):
                    parse_agents_table(config)

    def test_merge_agents_table_preserves_existing_values_and_text(self):
        config = (
            '# keep this comment\nmodel = "gpt-5"\n\n'
            '[agents]\nreviewer = "custom.toml"\n\n'
            '[other]\nenabled = true\n'
        )
        merged, agents = merge_agents_table(
            config,
            {"reviewer": "default.toml", "builder": "builder.toml"},
        )

        self.assertEqual(
            {"reviewer": "custom.toml", "builder": "builder.toml"},
            agents,
        )
        self.assertEqual(config, merged.replace('builder = "builder.toml"\n', ""))
        self.assertLess(merged.index('builder = "builder.toml"'), merged.index("[other]"))

    def test_merge_agents_table_ignores_unrelated_multiline_values(self):
        config = (
            "[other]\nvalues = [\n  1,\n  2,\n]\n\n"
            '[agents]\nreviewer = "custom.toml"\n'
        )
        merged, agents = merge_agents_table(config, {"builder": "builder.toml"})
        self.assertEqual(
            {"reviewer": "custom.toml", "builder": "builder.toml"},
            agents,
        )
        self.assertEqual(config, merged.replace('builder = "builder.toml"\n', ""))

    def test_merge_agents_table_rejects_root_inline_layout(self):
        config = "agents = { max_threads = 4 }\n"
        with self.assertRaisesRegex(ValueError, "unsupported agents layout"):
            merge_agents_table(config, {"builder": "builder.toml"})

    def test_merge_agents_table_rejects_unsupported_desired_value(self):
        with self.assertRaisesRegex(
            ValueError, "agent values must be strings, integers, or booleans"
        ):
            merge_agents_table("", {"builder": {"path": "builder.toml"}})

    def test_merge_agents_table_formats_boolean(self):
        merged, agents = merge_agents_table("", {"interrupt_message": True})
        self.assertEqual({"interrupt_message": True}, agents)
        self.assertEqual("[agents]\ninterrupt_message = true\n", merged)

    def test_merge_agents_table_adds_missing_table(self):
        merged, agents = merge_agents_table('model = "gpt-5"\n', {"builder": "builder.toml"})
        self.assertEqual({"builder": "builder.toml"}, agents)
        self.assertEqual(
            'model = "gpt-5"\n\n[agents]\nbuilder = "builder.toml"\n',
            merged,
        )


if __name__ == "__main__":
    unittest.main()
