import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "grill-me",
    "grilling",
    "init-project",
    "to-exec-plan",
    "orchestrator",
    "builder",
    "reviewer",
    "librarian",
    "complete-project",
)
UPSTREAM_HASHES = {
    "skills/grill-me/upstream/SKILL.md": "6189dfceb7304a6e5558f75d87e68fa3bc7fcf7ba120e44f21f8a61fe01eba54",
    "skills/grilling/SKILL.md": "5a35925d03a391bcfa46940868b649b72dba89ec9c19525e785bbb6bd3a7f478",
}
AGENTS = {
    "harness-builder.toml": {
        "name": "harness-builder",
        "description": "Build one harness feature and produce a ready-for-review handoff.",
        "model_reasoning_effort": "high",
        "role": "Builder",
    },
    "harness-reviewer.toml": {
        "name": "harness-reviewer",
        "description": "Review one feature against every acceptance criterion using independent runtime evidence.",
        "model_reasoning_effort": "high",
        "role": "Reviewer",
    },
    "harness-librarian.toml": {
        "name": "harness-librarian",
        "description": "Update project navigation after merged work passes global validation.",
        "model_reasoning_effort": "medium",
        "role": "Librarian",
    },
}


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError(f"{path.relative_to(ROOT)} has no frontmatter")
    try:
        end = lines.index("---", 1, min(len(lines), 80))
    except ValueError as error:
        raise AssertionError(f"{path.relative_to(ROOT)} has unbounded frontmatter") from error
    values = {}
    for line in lines[1:end]:
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):\s*(.+)", line)
        if match:
            values[match.group(1)] = match.group(2).strip(" '\"")
    return values


def openai_metadata(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    values: dict[str, object] = {}
    for key in ("display_name", "short_description", "default_prompt"):
        match = re.search(rf'^  {key}: "([^"\n]+)"$', text, re.MULTILINE)
        if match:
            values[key] = match.group(1)
    match = re.search(
        r"^policy:\n  allow_implicit_invocation: (true|false)$",
        text,
        re.MULTILINE,
    )
    if match:
        values["allow_implicit_invocation"] = match.group(1) == "true"
    return values


def agent_toml(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    values = {
        key: value
        for key, value in re.findall(r'^([a-z_]+) = "([^"\n]*)"$', text, re.MULTILINE)
    }
    multiline = re.search(
        r'^developer_instructions = """\n(.*?)\n"""$',
        text,
        re.MULTILINE | re.DOTALL,
    )
    if multiline:
        values["developer_instructions"] = multiline.group(1)
    return values


def assert_schema_valid(test: unittest.TestCase, schema: dict, value: object) -> None:
    errors: list[str] = []
    validate_schema(schema, value, "$", errors)
    test.assertEqual([], errors)


def assert_schema_invalid(test: unittest.TestCase, schema: dict, value: object) -> None:
    errors: list[str] = []
    validate_schema(schema, value, "$", errors)
    test.assertTrue(errors, "representative malformed value unexpectedly passed")


def validate_schema(schema: dict, value: object, path: str, errors: list[str]) -> None:
    if "anyOf" in schema:
        if not any(schema_matches(option, value) for option in schema["anyOf"]):
            errors.append(f"{path}: anyOf")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: const")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: enum")
    if "type" in schema:
        allowed = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(type_matches(kind, value) for kind in allowed):
            errors.append(f"{path}: type")
            return
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: minLength")
    if isinstance(value, str) and "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
        errors.append(f"{path}: pattern")
    if isinstance(value, int) and not isinstance(value, bool) and value < schema.get("minimum", value):
        errors.append(f"{path}: minimum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{path}: extra {key}")
        for key, child in properties.items():
            if key in value:
                validate_schema(child, value[key], f"{path}.{key}", errors)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: minItems")
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, sort_keys=True) for item in value]
            if len(rendered) != len(set(rendered)):
                errors.append(f"{path}: uniqueItems")
        for index, item in enumerate(value):
            validate_schema(schema.get("items", {}), item, f"{path}[{index}]", errors)
    for condition in schema.get("allOf", []):
        branch = condition.get("then") if schema_matches(condition.get("if", {}), value) else condition.get("else")
        if branch:
            validate_schema(branch, value, path, errors)


def schema_matches(schema: dict, value: object) -> bool:
    errors: list[str] = []
    validate_schema(schema, value, "$", errors)
    return not errors


def type_matches(kind: str, value: object) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(kind, False)


class SkillContractTests(unittest.TestCase):
    def test_all_nine_skills_have_valid_metadata(self):
        for skill in SKILLS:
            with self.subTest(skill=skill):
                directory = ROOT / "skills" / skill
                skill_md = directory / "SKILL.md"
                metadata_file = directory / "agents/openai.yaml"
                self.assertTrue(skill_md.is_file(), f"missing skills/{skill}/SKILL.md")
                metadata = frontmatter(skill_md)
                self.assertEqual(skill, metadata.get("name"))
                self.assertTrue(metadata.get("description", "").strip())
                self.assertTrue(metadata_file.is_file(), f"missing skills/{skill}/agents/openai.yaml")
                openai = openai_metadata(metadata_file)
                self.assertTrue(openai.get("display_name"))
                short = openai.get("short_description", "")
                self.assertGreaterEqual(len(short), 25)
                self.assertLessEqual(len(short), 64)
                self.assertIn(f"${skill}", openai.get("default_prompt", ""))
                self.assertIs(True, openai.get("allow_implicit_invocation"))

    def test_grill_wrapper_and_upstream_bytes_remain_pinned(self):
        wrapper = (ROOT / "skills/grill-me/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("disable-model-invocation:", wrapper)
        self.assertIn("$grilling", wrapper)
        for relative, expected in UPSTREAM_HASHES.items():
            with self.subTest(path=relative):
                digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                self.assertEqual(expected, digest)

    def test_role_skills_resolve_helper_from_active_skill(self):
        for role in ("orchestrator", "builder", "reviewer"):
            text = (ROOT / "skills" / role / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("active orchestrator skill", text)
            self.assertIn("absolute path", text)
            self.assertIn("scripts/harness_context.py", text)
            self.assertNotIn(".agents/skills/orchestrator", text)

    def test_reviewer_evidence_location_is_control_root_only(self):
        text = (ROOT / "skills/reviewer/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("$controlRoot/worklog/evidence/<feature-id>/", text)
        self.assertRegex(text, r"(?i)never.*feature worktree|feature worktree.*never")

    def test_complete_project_requires_safe_owner_gated_archival(self):
        text = (ROOT / "skills/complete-project/SKILL.md").read_text(encoding="utf-8")
        requirements = (
            r"quiescen",
            r"all features.*passed|every feature.*passed",
            r"validation.*evidence",
            r"unresolved.*handoff",
            r"latest.*checkpoint.*consisten",
            r"Git state",
            r"proposed completion report",
            r"explicit.*owner.*confirm",
            r"archive|archival",
            r"must not commit|do not commit",
            r"must not push|do not push",
            r"must not delete|do not delete",
        )
        for requirement in requirements:
            with self.subTest(requirement=requirement):
                self.assertRegex(text, re.compile(requirement, re.IGNORECASE | re.DOTALL))

    def test_role_ownership_is_separated(self):
        orchestrator = (ROOT / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(orchestrator, r"Only Orchestrator updates `TODO\.json` status and attempt count")
        self.assertRegex(orchestrator, r"single writer.*worklog/handoffs\.jsonl.*worklog/logs/lifecycle\.jsonl")
        for role in ("builder", "reviewer", "librarian"):
            text = (ROOT / "skills" / role / "SKILL.md").read_text(encoding="utf-8")
            self.assertRegex(text, r"(?i)do not (?:edit|write).*TODO\.json")
            self.assertRegex(text, r"(?i)do not (?:edit|write).*shared JSONL.*lifecycle")
        builder = (ROOT / "skills/builder/SKILL.md").read_text(encoding="utf-8")
        reviewer = (ROOT / "skills/reviewer/SKILL.md").read_text(encoding="utf-8")
        librarian = (ROOT / "skills/librarian/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("one assigned feature", builder)
        self.assertRegex(reviewer, r"(?i)acceptance.*evidence|evidence.*acceptance")
        self.assertRegex(librarian, re.compile(r"after Orchestrator.*global validation", re.IGNORECASE | re.DOTALL))

    def test_planner_never_writes_orchestrator_owned_event_channels(self):
        planner = (ROOT / "skills/to-exec-plan/SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(planner, r"Do not write `worklog/handoffs\.jsonl` or `worklog/logs/lifecycle\.jsonl`")
        self.assertNotRegex(planner, r"(?i)append.*worklog/handoffs\.jsonl")


class AgentContractTests(unittest.TestCase):
    def test_global_agents_are_identity_only_and_prefixed(self):
        for filename, expected in AGENTS.items():
            with self.subTest(agent=filename):
                path = ROOT / "agents" / filename
                self.assertTrue(path.is_file(), f"missing agents/{filename}")
                data = agent_toml(path)
                self.assertEqual(expected["name"], data.get("name"))
                self.assertEqual(expected["description"], data.get("description"))
                self.assertEqual("gpt-5.6", data.get("model"))
                self.assertEqual(expected["model_reasoning_effort"], data.get("model_reasoning_effort"))
                instructions = data.get("developer_instructions", "")
                self.assertIn(f"assigned {expected['role']} skill", instructions)
                self.assertIn("authoritative behavior contract", instructions)
                self.assertNotIn("sandbox_mode", data)
                self.assertNotIn("permissions", data)
                self.assertNotIn("harness_v2", path.read_text(encoding="utf-8"))


class SchemaContractTests(unittest.TestCase):
    def test_builder_handoff_schema_contract_and_rejections(self):
        schema = json.loads((ROOT / "schemas/builder-handoff.schema.json").read_text(encoding="utf-8"))
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertEqual("builder-handoff.schema.json", schema["$id"])
        self.assertEqual("object", schema["type"])
        self.assertEqual(
            {"schemaVersion", "featureId", "featureName", "featureSpecSha256", "branch", "worktree", "commitSha", "outcome", "payload"},
            set(schema["required"]),
        )
        value = valid_builder_handoff()
        assert_schema_valid(self, schema, value)
        for malformed in (
            {key: item for key, item in value.items() if key != "featureId"},
            {**value, "unexpected": True},
            {**value, "featureSpecSha256": "invalid"},
            {**value, "payload": {**value["payload"], "validation": {"command": "test", "status": "failed", "summary": "failed"}}},
        ):
            with self.subTest(malformed=malformed):
                assert_schema_invalid(self, schema, malformed)

    def test_lifecycle_schema_contract_and_rejections(self):
        schema = json.loads((ROOT / "schemas/lifecycle-event.schema.json").read_text(encoding="utf-8"))
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertEqual("lifecycle-event.schema.json", schema["$id"])
        self.assertEqual("object", schema["type"])
        self.assertIn("eventId", schema["required"])
        self.assertIn("tokens", schema["required"])
        value = valid_lifecycle_event()
        assert_schema_valid(self, schema, value)
        for malformed in (
            {key: item for key, item in value.items() if key != "eventId"},
            {**value, "unexpected": True},
            {**value, "durationMs": 1},
            {**value, "featureId": None, "featureName": "leaked identity"},
        ):
            with self.subTest(malformed=malformed):
                assert_schema_invalid(self, schema, malformed)


def valid_builder_handoff() -> dict:
    return {
        "schemaVersion": 1,
        "featureId": "F1",
        "featureName": "Feature one",
        "featureSpecSha256": "a" * 64,
        "branch": "feature/F1-one",
        "worktree": "/tmp/F1-one",
        "commitSha": "abcdef1",
        "outcome": "ready-for-review",
        "payload": {
            "summary": "Implemented feature",
            "changedFiles": ["src/one.py"],
            "changedDependencies": [],
            "assumptionsChecked": ["No dependency change"],
            "validation": {"command": "python3 -m unittest", "status": "passed", "summary": "passed"},
            "tddEvidence": {"red": "failed before", "green": "passed after"},
            "review": {
                "kind": "runtime",
                "steps": ["Run the command"],
                "expectedResults": ["It passes"],
                "startCommand": "python3 app.py",
                "url": None,
                "fixture": None,
                "account": None,
            },
        },
    }


def valid_lifecycle_event() -> dict:
    return {
        "schemaVersion": 1,
        "eventId": "event-1",
        "runId": "run-1",
        "waveId": None,
        "featureId": None,
        "featureName": None,
        "spanId": "span-1",
        "parentSpanId": None,
        "timestamp": "2026-07-13T00:00:00Z",
        "phase": "started",
        "actor": "orchestrator",
        "action": "run",
        "model": None,
        "reasoningEffort": None,
        "durationMs": None,
        "tokens": {"input": None, "cachedInput": None, "output": None, "total": None},
        "status": None,
        "outcome": None,
        "error": None,
        "metadata": {},
    }


class ContextHelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "main"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "docs/exec-plans/active").mkdir(parents=True)
        (self.root / "worklog").mkdir()
        self.feature = {"id": "F1", "title": "Selected feature", "acceptanceCriteria": ["works"]}
        self.sibling = {"id": "F2", "title": "Sibling secret"}
        self.write_todo([self.feature, self.sibling])
        self.write_handoffs([])

    def tearDown(self):
        self.temporary.cleanup()

    @property
    def helper(self) -> Path:
        return ROOT / "skills/orchestrator/scripts/harness_context.py"

    def write_todo(self, features):
        (self.root / "docs/exec-plans/active/TODO.json").write_text(
            json.dumps({"features": features}) + "\n",
            encoding="utf-8",
        )

    def write_handoffs(self, events):
        (self.root / "worklog/handoffs.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )

    def run_helper(self, *args, root=None):
        return subprocess.run(
            [sys.executable, str(self.helper), args[0], "--control-root", str(root or self.root), *args[1:]],
            capture_output=True,
            text=True,
        )

    def assert_failure(self, code, name, *args, root=None):
        result = self.run_helper(*args, root=root)
        self.assertEqual(code, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertRegex(result.stderr, rf"^{name}:")

    def test_exact_feature_and_handoff_selection_has_no_sibling_leakage(self):
        result = self.run_helper("feature", "--id", "F1")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Sibling secret", result.stdout)
        selected = json.loads(result.stdout)
        self.assertEqual(self.feature, selected["feature"])
        digest = selected["featureSpecSha256"]
        event = {
            "eventId": "event-1",
            "featureId": "F1",
            "featureSpecSha256": digest,
            "outcome": "ready-for-review",
            "metadata": {},
        }
        self.write_handoffs([event])
        handoff = self.run_helper(
            "handoff", "--event-id", "event-1", "--feature-id", "F1",
            "--expected-feature-sha256", digest, "--require-outcome", "ready-for-review",
        )
        self.assertEqual(0, handoff.returncode, handoff.stderr)
        self.assertEqual(event, json.loads(handoff.stdout))

    def test_context_helper_rejects_identity_hash_outcome_debug_duplicates_and_malformed_data(self):
        selected = self.run_helper("feature", "--id", "F1")
        self.assertEqual(0, selected.returncode, selected.stderr)
        digest = json.loads(selected.stdout)["featureSpecSha256"]
        event = {
            "eventId": "event-1", "featureId": "F1", "featureSpecSha256": digest,
            "outcome": "ready-for-review", "metadata": {},
        }
        self.write_handoffs([event])
        self.assert_failure(6, "IDENTITY_MISMATCH", "handoff", "--event-id", "event-1", "--feature-id", "F2", "--expected-feature-sha256", digest)
        self.assert_failure(7, "FEATURE_HASH_MISMATCH", "handoff", "--event-id", "event-1", "--feature-id", "F1", "--expected-feature-sha256", "0" * 64)
        self.assert_failure(8, "OUTCOME_MISMATCH", "handoff", "--event-id", "event-1", "--feature-id", "F1", "--expected-feature-sha256", digest, "--require-outcome", "failed")
        self.write_handoffs([{**event, "metadata": {"mode": "debug"}}])
        self.assert_failure(5, "DEBUG_EVENT", "handoff", "--event-id", "event-1", "--feature-id", "F1", "--expected-feature-sha256", digest)
        self.write_handoffs([event, event])
        self.assert_failure(4, "DUPLICATE_ID", "handoff", "--event-id", "event-1", "--feature-id", "F1", "--expected-feature-sha256", digest)
        self.write_todo([self.feature, self.feature])
        self.assert_failure(4, "DUPLICATE_ID", "feature", "--id", "F1")
        (self.root / "docs/exec-plans/active/TODO.json").write_text("not-json\n", encoding="utf-8")
        self.assert_failure(10, "MALFORMED_DATA", "feature", "--id", "F1")

    def test_context_helper_rejects_relative_non_main_and_unsafe_roots(self):
        self.assert_failure(9, "UNSAFE_PATH", "feature", "--id", "F1", root=Path("relative"))
        external = self.base / "external.json"
        external.write_text(json.dumps({"features": [self.feature]}), encoding="utf-8")
        todo = self.root / "docs/exec-plans/active/TODO.json"
        todo.unlink()
        todo.symlink_to(external)
        self.assert_failure(9, "UNSAFE_PATH", "feature", "--id", "F1")

        todo.unlink()
        self.write_todo([self.feature])
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=Harness Test", "-c", "user.email=harness@example.invalid", "commit", "-qm", "fixture"],
            check=True,
        )
        linked = self.base / "linked"
        subprocess.run(["git", "-C", str(self.root), "worktree", "add", "-qb", "feature/F1", str(linked)], check=True)
        self.assert_failure(9, "UNSAFE_PATH", "feature", "--id", "F1", root=linked)


class ResidueTests(unittest.TestCase):
    def test_new_contracts_have_no_source_repo_or_secret_residue(self):
        paths = [ROOT / "skills" / name for name in SKILLS]
        paths += [ROOT / "agents", ROOT / "schemas"]
        files = [path for base in paths if base.exists() for path in base.rglob("*") if path.is_file()]
        text = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertNotIn("/Users/zhiqibao/Documents/SynologyDrive/01_Projects/harness_v2", text)
        self.assertNotRegex(text, r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*['\"][^'\"]+")


if __name__ == "__main__":
    unittest.main()
