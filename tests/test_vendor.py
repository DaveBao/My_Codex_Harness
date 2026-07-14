import hashlib
import re
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://github.com/mattpocock/skills.git"
SOURCE_TAG = "v1.1.0"
SOURCE_COMMIT = "d574778f94cf620fcc8ce741584093bc650a61d3"
SKILLS = ("grill-me", "grilling")
ACTIVE_SKILLS = {
    "grill-me",
    "grilling",
    "init-project",
    "to-exec-plan",
    "orchestrator",
    "builder",
    "reviewer",
    "librarian",
    "complete-project",
}
UPSTREAM_LICENSE_SHA256 = "0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5"
UPSTREAM_SKILL_HASHES = {
    "skills/grill-me/upstream/SKILL.md": "6189dfceb7304a6e5558f75d87e68fa3bc7fcf7ba120e44f21f8a61fe01eba54",
    "skills/grilling/upstream/SKILL.md": "5a35925d03a391bcfa46940868b649b72dba89ec9c19525e785bbb6bd3a7f478",
}
PROVENANCE_REQUIREMENTS = {
    "grill-me": (
        f"- Source: {SOURCE_URL}",
        f"- Tag: `{SOURCE_TAG}`",
        f"- Resolved commit: `{SOURCE_COMMIT}`",
        "- Original path: `skills/productivity/grill-me/SKILL.md`, preserved at `upstream/SKILL.md`.",
        "- License: MIT; see `LICENSE.upstream` in this directory.",
        "- Local status: `SKILL.md` is a Codex-compatible wrapper, while `upstream/SKILL.md` and `LICENSE.upstream` are unmodified upstream files. This provenance file is a local companion.",
    ),
    "grilling": (
        f"- Source: {SOURCE_URL}",
        f"- Tag: `{SOURCE_TAG}`",
        f"- Resolved commit: `{SOURCE_COMMIT}`",
        "- Original path: `skills/productivity/grilling/SKILL.md`",
        "- License: MIT; see `LICENSE.upstream` in this directory.",
        "- Local status: `SKILL.md` is an Owner-gated Codex adapter, while `upstream/SKILL.md` and `LICENSE.upstream` are unmodified upstream files. This provenance file is a local companion.",
    ),
}


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path} has no frontmatter")

    try:
        end = lines.index("---", 1, min(len(lines), 80))
    except ValueError as error:
        raise ValueError(f"{path} has unbounded frontmatter") from error

    values = {}
    for line in lines[1:end]:
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):\s*(.+)", line)
        if match:
            values[match.group(1)] = match.group(2).strip(" '\"")
    return values


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_upstream_licenses(test: unittest.TestCase, root: Path) -> None:
    for skill in SKILLS:
        relative = f"skills/{skill}/LICENSE.upstream"
        test.assertEqual(UPSTREAM_LICENSE_SHA256, sha256(root / relative), relative)


def assert_skill_provenance(test: unittest.TestCase, root: Path) -> None:
    for skill, requirements in PROVENANCE_REQUIREMENTS.items():
        relative = f"skills/{skill}/UPSTREAM.md"
        text = (root / relative).read_text(encoding="utf-8")
        for requirement in requirements:
            test.assertIn(requirement, text, f"{relative} missing exact provenance")


class VendoredGrillTests(unittest.TestCase):
    def test_vendored_skills_have_required_frontmatter(self):
        for skill in SKILLS:
            with self.subTest(skill=skill):
                path = ROOT / "skills" / skill / "SKILL.md"
                self.assertTrue(path.is_file(), f"missing {path.relative_to(ROOT)}")
                metadata = frontmatter(path)
                self.assertEqual(skill, metadata.get("name"))
                self.assertTrue(metadata.get("description"), "description is required")

    def test_codex_wrapper_preserves_exact_upstream_skills(self):
        wrapper = (ROOT / "skills/grill-me/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("disable-model-invocation:", wrapper)
        self.assertIn("$grilling", wrapper)

        for relative, expected in UPSTREAM_SKILL_HASHES.items():
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file(), f"missing {relative}")
                self.assertEqual(expected, sha256(path))

    def test_provenance_and_license_are_pinned(self):
        for relative in ("NOTICE", "docs/upstream-grill.md"):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file(), f"missing {relative}")
                text = path.read_text(encoding="utf-8")
                self.assertIn(SOURCE_URL, text)
                self.assertIn(SOURCE_TAG, text)
                self.assertIn(SOURCE_COMMIT, text)
                self.assertIn("MIT", text)

        assert_upstream_licenses(self, ROOT)
        assert_skill_provenance(self, ROOT)

    def test_altered_license_and_provenance_fail_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "skills", root / "skills")
            license_path = root / "skills/grill-me/LICENSE.upstream"
            license_path.write_bytes(license_path.read_bytes() + b"tampered\n")
            with self.assertRaisesRegex(AssertionError, "skills/grill-me/LICENSE.upstream"):
                assert_upstream_licenses(self, root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "skills", root / "skills")
            provenance_path = root / "skills/grill-me/UPSTREAM.md"
            provenance_path.write_text(
                provenance_path.read_text(encoding="utf-8").replace(
                    SOURCE_URL,
                    "https://example.invalid/skills.git",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(AssertionError, "skills/grill-me/UPSTREAM.md"):
                assert_skill_provenance(self, root)

    def test_update_docs_cover_hash_oracles_and_cross_platform_validation(self):
        document = (ROOT / "docs/upstream-grill.md").read_text(encoding="utf-8")
        self.assertIn("UPSTREAM_LICENSE_SHA256", document)
        self.assertIn("UPSTREAM_SKILL_HASHES", document)
        self.assertIn("### POSIX", document)
        self.assertIn("### PowerShell", document)
        self.assertIn("python3", document)
        self.assertIn("py -3", document)

    def test_documented_hashes_match_every_vendored_file(self):
        path = ROOT / "docs/upstream-grill.md"
        self.assertTrue(path.is_file(), "missing docs/upstream-grill.md")
        document = path.read_text(encoding="utf-8")
        recorded = {
            path: digest
            for path, digest in re.findall(
                r"^\| `((?:skills/(?:grill-me|grilling)/)[^`]+)` \| `([0-9a-f]{64})` \|",
                document,
                re.MULTILINE,
            )
        }
        files = {
            path.relative_to(ROOT).as_posix(): path
            for skill in SKILLS
            for path in (ROOT / "skills" / skill).rglob("*")
            if path.is_file()
        }
        self.assertEqual(set(files), set(recorded))
        for relative, path in files.items():
            with self.subTest(path=relative):
                self.assertEqual(recorded[relative], sha256(path))

    def test_only_expected_top_level_skill_directories_are_present(self):
        skill_root = ROOT / "skills"
        self.assertTrue(skill_root.is_dir(), "missing skills directory")
        actual = {path.name for path in skill_root.iterdir() if path.is_dir()}
        self.assertEqual(ACTIVE_SKILLS, actual)

    def test_vendored_files_are_regular_files(self):
        for skill in SKILLS:
            for path in (ROOT / "skills" / skill).rglob("*"):
                with self.subTest(path=path.relative_to(ROOT)):
                    self.assertFalse(path.is_symlink())


if __name__ == "__main__":
    unittest.main()
