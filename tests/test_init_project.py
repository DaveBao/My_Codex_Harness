import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/init-project/scripts/init_project.py"
SCAFFOLD = ROOT / "scaffold"


def run_init(root: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
    )


def tree_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): None if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


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
                ".codex/config.toml",
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
            text = (SCAFFOLD / f".codex/agents/harness-{name}.toml").read_text()
            self.assertIn(f'name = "harness-{name}"', text)
            self.assertNotIn(str(Path.home()), text)
        self.assertEqual(b"", (SCAFFOLD / "worklog/handoffs.jsonl").read_bytes())
        self.assertEqual(b"", (SCAFFOLD / "worklog/logs/lifecycle.jsonl").read_bytes())


if __name__ == "__main__":
    unittest.main()
