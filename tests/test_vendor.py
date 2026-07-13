import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://github.com/mattpocock/skills.git"
SOURCE_TAG = "v1.1.0"
SOURCE_COMMIT = "d574778f94cf620fcc8ce741584093bc650a61d3"
SKILLS = ("grill-me", "grilling")
UPSTREAM_SKILL_HASHES = {
    "skills/grill-me/upstream/SKILL.md": "6189dfceb7304a6e5558f75d87e68fa3bc7fcf7ba120e44f21f8a61fe01eba54",
    "skills/grilling/SKILL.md": "5a35925d03a391bcfa46940868b649b72dba89ec9c19525e785bbb6bd3a7f478",
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

        for skill in SKILLS:
            directory = ROOT / "skills" / skill
            licenses = [path for path in directory.glob("*LICENSE*") if path.is_file()]
            self.assertTrue(licenses, f"missing upstream MIT license in skills/{skill}")

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

    def test_no_unrelated_top_level_skill_directory_is_present(self):
        skill_root = ROOT / "skills"
        self.assertTrue(skill_root.is_dir(), "missing skills directory")
        actual = {path.name for path in skill_root.iterdir() if path.is_dir()}
        self.assertEqual(set(SKILLS), actual)

    def test_vendored_files_are_regular_files(self):
        for skill in SKILLS:
            for path in (ROOT / "skills" / skill).rglob("*"):
                with self.subTest(path=path.relative_to(ROOT)):
                    self.assertFalse(path.is_symlink())


if __name__ == "__main__":
    unittest.main()
