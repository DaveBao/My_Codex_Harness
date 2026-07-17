import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "grill-me",
    "grilling",
    "to-spec",
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
    "skills/grilling/upstream/SKILL.md": "5a35925d03a391bcfa46940868b649b72dba89ec9c19525e785bbb6bd3a7f478",
    "skills/to-spec/upstream/SKILL.md": "267638edd513b5918de626ad5605d261952abb7428cb308869c663ca924e93e7",
}
OWNER_ENTRY_SKILLS = {
    "grill-me": ("$grill-me",),
    "grilling": ("$grilling", "$grill-me"),
    "to-spec": ("$to-spec",),
    "init-project": ("$init-project",),
    "to-exec-plan": ("$to-exec-plan",),
    "orchestrator": ("$orchestrator", "/harness run", "/harness resume"),
    "complete-project": ("$complete-project",),
}
DELEGATED_ROLE_SKILLS = ("builder", "reviewer", "librarian")
AGENTS = {
    "harness-builder.toml": {
        "name": "harness-builder",
        "description": "Delegated Builder for one feature in an Owner-activated Harness run.",
        "model_reasoning_effort": "high",
        "role": "Builder",
    },
    "harness-reviewer.toml": {
        "name": "harness-reviewer",
        "description": "Delegated Reviewer for one feature in an Owner-activated Harness run.",
        "model_reasoning_effort": "high",
        "role": "Reviewer",
    },
    "harness-librarian.toml": {
        "name": "harness-librarian",
        "description": "Delegated Librarian for validated work in an Owner-activated Harness run.",
        "model_reasoning_effort": "medium",
        "role": "Librarian",
    },
}
PACKAGE_TEXT_SUFFIXES = frozenset({".json", ".md", ".py", ".toml", ".upstream", ".yaml"})
RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


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


def tracked_package_text_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "skills", "agents", "schemas"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        root / relative
        for relative in result.stdout.split("\0")
        if relative
        and (root / relative).is_file()
        and Path(relative).suffix.lower() in PACKAGE_TEXT_SUFFIXES
    ]


def try_create_symlink(link: Path, target: Path) -> bool:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        return False
    return True


def is_rfc3339_datetime(value: str) -> bool:
    if RFC3339_DATETIME.fullmatch(value) is None:
        return False
    normalized = value[:10] + "T" + value[11:]
    if normalized[-1:] in ("Z", "z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized).tzinfo is not None
    except ValueError:
        return False


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
    if isinstance(value, str) and len(value) > schema.get("maxLength", len(value)):
        errors.append(f"{path}: maxLength")
    if isinstance(value, str) and "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
        errors.append(f"{path}: pattern")
    if isinstance(value, str) and schema.get("format") == "date-time" and not is_rfc3339_datetime(value):
        errors.append(f"{path}: format")
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
    def test_all_skills_have_valid_metadata(self):
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
                self.assertIs(False, openai.get("allow_implicit_invocation"))

    def test_grill_wrapper_and_upstream_bytes_remain_pinned(self):
        wrapper = (ROOT / "skills/grill-me/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("disable-model-invocation:", wrapper)
        self.assertIn("$grilling", wrapper)
        for relative, expected in UPSTREAM_HASHES.items():
            with self.subTest(path=relative):
                digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                self.assertEqual(expected, digest)

    def test_owner_entry_skills_require_current_top_level_invocation(self):
        for skill, invocations in OWNER_ENTRY_SKILLS.items():
            with self.subTest(skill=skill):
                path = ROOT / "skills" / skill / "SKILL.md"
                text = path.read_text(encoding="utf-8")
                description = frontmatter(path)["description"]
                self.assertRegex(description, r"(?i)owner.*explicit|explicit.*owner")
                self.assertRegex(text, r"(?i)current top-level request")
                self.assertRegex(text, r"(?i)quoted text|files.*not.*activation|not.*activation.*files")
                for invocation in invocations:
                    self.assertIn(invocation, text)

    def test_delegated_roles_require_owner_activation_envelope_before_state(self):
        for role in DELEGATED_ROLE_SKILLS:
            with self.subTest(role=role):
                path = ROOT / "skills" / role / "SKILL.md"
                text = path.read_text(encoding="utf-8")
                description = frontmatter(path)["description"]
                self.assertRegex(description, r"(?i)owner-activated.*orchestrator")
                for field in ("harnessRunId", "activatedByOwner", "activationCommand"):
                    self.assertIn(field, text)
                self.assertLess(text.index("## Activation Gate"), text.index("## Inputs"))
                self.assertRegex(text, r"(?i)before reading.*state|before.*state access")

    def test_orchestrator_allocates_and_propagates_owner_activation(self):
        text = (ROOT / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        for field in ("harnessRunId", "activatedByOwner", "activationCommand"):
            self.assertIn(field, text)
        self.assertIn("/harness run", text)
        self.assertIn("/harness resume", text)
        self.assertRegex(text, r"(?i)checkpoint.*never.*activate|never.*activate.*checkpoint")

    def test_codex_runtime_closure_matches_initialized_scaffold(self):
        text = (ROOT / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        for role in ("builder", "reviewer", "librarian"):
            self.assertIn(f".codex/agents/harness-{role}.toml", text)
            self.assertNotIn(f".codex/agents/{role}.toml", text)
        self.assertIn("`docs/codex-policy.md`", text)

    def test_codex_app_native_roles_do_not_use_cli_runner(self):
        orchestrator = (ROOT / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        builder = (ROOT / "skills/builder/SKILL.md").read_text(encoding="utf-8")
        worklog = (ROOT / "scaffold/docs/references/worklog-events.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertFalse((ROOT / "skills/orchestrator/scripts/codex_runtime.py").exists())
        self.assertIn("spawn_agent", orchestrator)
        self.assertIn("followup_task", orchestrator)
        self.assertNotIn("codex exec", orchestrator)
        self.assertNotIn("codex_runtime.py", orchestrator)
        self.assertRegex(orchestrator, r"(?i)formal retry[^.]*new Builder (?:session|thread)")
        self.assertRegex(orchestrator, r"(?i)context repair[^.]*followup_task")
        self.assertRegex(builder, r"(?i)formal retry[^.]*new session")
        self.assertRegex(builder, r"(?i)context repair[^.]*same Builder session")
        for marker in ("unavailable usage", "null", "never estimate"):
            self.assertIn(marker.lower(), worklog.lower())
        self.assertIn("Codex App", readme)
        self.assertIn("spawn_agent", readme)
        self.assertNotIn("codex exec", readme)
        self.assertNotIn("codex_runtime.py", readme)

    def test_role_skills_resolve_helper_from_active_skill(self):
        for role in ("orchestrator", "builder", "reviewer"):
            text = (ROOT / "skills" / role / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("active orchestrator skill", text)
            self.assertIn("absolute path", text)
            self.assertIn("scripts/harness_context.py", text)
            self.assertNotIn(".agents/skills/orchestrator", text)

    def test_optimized_role_contracts_use_bounded_helpers(self):
        orchestrator = (ROOT / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        builder = (ROOT / "skills/builder/SKILL.md").read_text(encoding="utf-8")
        reviewer = (ROOT / "skills/reviewer/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("scripts/harness_control.py", orchestrator)
        for command in ("preflight", "lifecycle-start", "lifecycle-finish"):
            self.assertIn(f"$harnessControl\" {command}", orchestrator)
        self.assertRegex(orchestrator, r"(?i)same active turn.*next.*safe operation")

        for role, text in (("Builder", builder), ("Reviewer", reviewer)):
            with self.subTest(role=role):
                self.assertIn('$harnessContext" assignment', text)
                self.assertIn('$harnessContext" section', text)
                self.assertIn('$harnessContext" handoff', text)
                self.assertNotIn('$harnessContext" feature', text)

    def test_optimized_contracts_retain_no_drift_gates(self):
        orchestrator = (ROOT / "skills/orchestrator/SKILL.md").read_text(encoding="utf-8")
        builder = (ROOT / "skills/builder/SKILL.md").read_text(encoding="utf-8")
        reviewer = (ROOT / "skills/reviewer/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("spawn_agent", orchestrator)
        self.assertRegex(orchestrator, r"(?i)formal retry[^.]*new Builder (?:session|thread)")
        self.assertRegex(orchestrator, r"(?i)context repair[^.]*followup_task")
        self.assertRegex(orchestrator, r"(?i)Reviewer `passed`.*merge.*global validation")
        self.assertRegex(builder, r"(?i)formal retry[^.]*new session")
        self.assertRegex(builder, r"(?i)context repair[^.]*same Builder session")
        self.assertRegex(reviewer, r"(?i)every AC requires real evidence")
        self.assertRegex(reviewer, r"(?i)must not use generic file reads.*TODO\.json.*handoffs\.jsonl")

    def test_planner_requires_narrow_authoritative_references(self):
        planner = (ROOT / "skills/to-exec-plan/SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(planner, r"(?i)narrowest existing authoritative section")
        self.assertRegex(planner, r"(?i)do not use `docs/project-map\.md#entry-points` as a universal placeholder")
        self.assertRegex(planner, r"(?i)report a navigation gap")

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

    def test_to_spec_synthesizes_prd_without_crossing_phase_boundaries(self):
        text = (ROOT / "skills/to-spec/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("docs/product-specs/prd.md", text)
        self.assertIn("$to-exec-plan", text)
        self.assertRegex(text, r"(?i)do not.*interview|does not restart.*interview")
        self.assertRegex(text, r"(?i)external issue.*explicit")
        self.assertIn("TODO.json", text)
        self.assertRegex(text, r"(?i)do not.*TODO\.json|must not.*TODO\.json")


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

    def test_global_agents_require_owner_activation_envelope(self):
        for filename in AGENTS:
            with self.subTest(agent=filename):
                text = (ROOT / "agents" / filename).read_text(encoding="utf-8")
                for field in ("harnessRunId", "activatedByOwner", "activationCommand"):
                    self.assertIn(field, text)
                self.assertRegex(text, r"(?i)stop before.*state|before.*state.*stop")


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
            {**value, "payload": {**value["payload"], "validation": {"command": "test", "status": "passed", "summary": "x" * 1001}}},
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
            {**value, "timestamp": "not-a-timestamp"},
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

    def commit_fixture(self, message="fixture"):
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.root), "-c", "user.name=Harness Test",
                "-c", "user.email=harness@example.invalid", "commit", "-qm", message,
            ],
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

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

    def test_assignment_excludes_mutable_history_without_changing_hash(self):
        feature = {
            **self.feature,
            "status": "failed",
            "attemptCount": 2,
            "handoffReferences": ["event-1"],
            "validationHistory": [{"result": "failed"}],
        }
        self.write_todo([feature, self.sibling])

        full = self.run_helper("feature", "--id", "F1")
        assignment = self.run_helper("assignment", "--id", "F1")

        self.assertEqual(0, full.returncode, full.stderr)
        self.assertEqual(0, assignment.returncode, assignment.stderr)
        value = json.loads(assignment.stdout)
        self.assertEqual("F1", value["feature"]["id"])
        self.assertEqual(["works"], value["feature"]["acceptanceCriteria"])
        for field in ("status", "attemptCount", "handoffReferences", "validationHistory"):
            self.assertNotIn(field, value["feature"])
        self.assertEqual(
            json.loads(full.stdout)["featureSpecSha256"],
            value["featureSpecSha256"],
        )
        self.assertNotIn("Sibling secret", assignment.stdout)

    def test_markdown_section_is_verbatim_hash_addressed_and_bounded(self):
        project_map = self.root / "docs/project-map.md"
        project_map.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "# Project Map\n\n"
            "## Entry Points\nentry\n\n"
            "## Transaction Events\nbody\n"
            "### Tests\nnested\n\n"
            "## Next Domain\nnext\n"
        )
        project_map.write_text(content, encoding="utf-8")
        base_sha = self.commit_fixture()

        result = self.run_helper(
            "section",
            "--reference", "docs/project-map.md#transaction-events",
            "--base-sha", base_sha,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        section = json.loads(result.stdout)
        expected = "## Transaction Events\nbody\n### Tests\nnested\n\n"
        self.assertEqual("docs/project-map.md#transaction-events", section["reference"])
        self.assertEqual(expected, section["content"])
        self.assertEqual(len(expected.encode("utf-8")), section["byteCount"])
        self.assertEqual(hashlib.sha256(content.encode("utf-8")).hexdigest(), section["fileSha256"])
        self.assertEqual("section", section["mode"])

        fallback = self.run_helper(
            "section",
            "--reference", "docs/project-map.md#entry-points",
            "--base-sha", base_sha,
            "--legacy-full-fallback",
        )
        self.assertEqual(0, fallback.returncode, fallback.stderr)
        fallback_value = json.loads(fallback.stdout)
        self.assertEqual(content, fallback_value["content"])
        self.assertEqual("full_fallback", fallback_value["mode"])

    def test_markdown_section_rejects_missing_duplicate_unsafe_and_drifted_references(self):
        project_map = self.root / "docs/project-map.md"
        project_map.parent.mkdir(parents=True, exist_ok=True)
        project_map.write_text(
            "# Map\n## Cash Events!\none\n## Cash Events\ntwo\n",
            encoding="utf-8",
        )
        base_sha = self.commit_fixture()

        self.assert_failure(
            3, "NOT_FOUND", "section",
            "--reference", "docs/project-map.md#missing",
            "--base-sha", base_sha,
        )
        self.assert_failure(
            4, "DUPLICATE_ID", "section",
            "--reference", "docs/project-map.md#cash-events",
            "--base-sha", base_sha,
        )
        self.assert_failure(
            9, "UNSAFE_PATH", "section",
            "--reference", "../outside.md#anything",
            "--base-sha", base_sha,
        )

        project_map.write_text("# Map\n## Cash Events\ndrifted\n", encoding="utf-8")
        self.assert_failure(
            7, "FEATURE_HASH_MISMATCH", "section",
            "--reference", "docs/project-map.md#cash-events",
            "--base-sha", base_sha,
        )

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

    def test_context_helper_rejects_relative_and_nonroot_roots(self):
        self.assert_failure(9, "UNSAFE_PATH", "feature", "--id", "F1", root=Path("relative"))

        nested = self.root / "nested"
        nested.mkdir()
        self.assert_failure(9, "UNSAFE_PATH", "feature", "--id", "F1", root=nested)

    def test_context_helper_rejects_non_main_worktree(self):
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=Harness Test", "-c", "user.email=harness@example.invalid", "commit", "-qm", "fixture"],
            check=True,
        )
        linked = self.base / "linked"
        subprocess.run(["git", "-C", str(self.root), "worktree", "add", "-qb", "feature/F1", str(linked)], check=True)
        self.assert_failure(9, "UNSAFE_PATH", "feature", "--id", "F1", root=linked)

    def test_context_helper_rejects_symlinked_control_file_when_supported(self):
        external = self.base / "external.json"
        external.write_text(json.dumps({"features": [self.feature]}), encoding="utf-8")
        todo = self.root / "docs/exec-plans/active/TODO.json"
        todo.unlink()
        if not try_create_symlink(todo, external):
            self.skipTest("symlink creation is unavailable")
        self.assert_failure(9, "UNSAFE_PATH", "feature", "--id", "F1")

    def test_symlink_permission_failure_is_a_capability_miss(self):
        with mock.patch.object(Path, "symlink_to", side_effect=PermissionError("Windows privilege")):
            self.assertFalse(try_create_symlink(self.base / "link", self.base / "target"))


class ControlHelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "main"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "docs/references").mkdir(parents=True)
        (self.root / "docs/references/lifecycle-event.schema.json").write_bytes(
            (ROOT / "schemas/lifecycle-event.schema.json").read_bytes()
        )
        (self.root / "worklog/logs").mkdir(parents=True)
        (self.root / "worklog/logs/lifecycle.jsonl").write_text("", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    @property
    def helper(self):
        return ROOT / "skills/orchestrator/scripts/harness_control.py"

    def run_helper(self, *args, root=None):
        return subprocess.run(
            [sys.executable, str(self.helper), args[0], "--control-root", str(root or self.root), *args[1:]],
            capture_output=True,
            text=True,
        )

    def read_events(self):
        return [
            json.loads(line)
            for line in (self.root / "worklog/logs/lifecycle.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]

    def assert_failure_without_lifecycle_write(self, code, name, *args, root=None):
        lifecycle = self.root / "worklog/logs/lifecycle.jsonl"
        before = lifecycle.read_bytes()
        result = self.run_helper(*args, root=root)
        self.assertEqual(code, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertRegex(result.stderr, rf"^{name}:")
        self.assertEqual(before, lifecycle.read_bytes())
        return result

    def commit_fixture(self):
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.root), "-c", "user.name=Harness Test",
                "-c", "user.email=harness@example.invalid", "commit", "-qm", "fixture",
            ],
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_lifecycle_start_and_finish_are_schema_valid_and_measured(self):
        started = self.run_helper(
            "lifecycle-start",
            "--run-id", "run-1",
            "--wave-id", "wave-1",
            "--feature-id", "F1",
            "--feature-name", "Feature one",
            "--parent-span-id", "parent-1",
            "--actor", "builder",
            "--action", "implement_feature",
            "--model", "gpt-5.6",
            "--reasoning-effort", "high",
            "--token-mode", "app-role",
            "--metadata-json", '{"attemptNumber":1}',
        )
        self.assertEqual(0, started.returncode, started.stderr)
        start_result = json.loads(started.stdout)

        finished = self.run_helper(
            "lifecycle-finish",
            "--span-id", start_result["spanId"],
            "--status", "succeeded",
            "--outcome", "ready-for-review",
            "--metadata-json", '{"attemptNumber":1}',
        )
        self.assertEqual(0, finished.returncode, finished.stderr)
        finish_result = json.loads(finished.stdout)

        events = self.read_events()
        self.assertEqual(2, len(events))
        schema = json.loads((ROOT / "schemas/lifecycle-event.schema.json").read_text(encoding="utf-8"))
        for event in events:
            assert_schema_valid(self, schema, event)
        self.assertEqual("started", events[0]["phase"])
        self.assertEqual("finished", events[1]["phase"])
        self.assertEqual(start_result["spanId"], finish_result["spanId"])
        self.assertEqual(events[0]["spanId"], events[1]["spanId"])
        self.assertEqual({"input": None, "cachedInput": None, "output": None, "total": None}, events[1]["tokens"])
        self.assertGreaterEqual(events[1]["durationMs"], 0)
        self.assertEqual(events[1]["durationMs"], finish_result["durationMs"])

    def test_lifecycle_rejects_invalid_missing_and_closed_spans_without_writing(self):
        self.assert_failure_without_lifecycle_write(
            6, "IDENTITY_MISMATCH", "lifecycle-start",
            "--run-id", "run-1", "--feature-id", "F1",
            "--actor", "builder", "--action", "implement_feature",
        )
        self.assert_failure_without_lifecycle_write(
            2, "INVALID_INVOCATION", "lifecycle-start",
            "--run-id", "run-1", "--actor", "invalid", "--action", "run",
        )
        self.assert_failure_without_lifecycle_write(
            3, "NOT_FOUND", "lifecycle-finish",
            "--span-id", "missing", "--status", "succeeded",
        )

        started = self.run_helper(
            "lifecycle-start", "--run-id", "run-1",
            "--actor", "orchestrator", "--action", "run",
        )
        self.assertEqual(0, started.returncode, started.stderr)
        span_id = json.loads(started.stdout)["spanId"]
        finished = self.run_helper(
            "lifecycle-finish", "--span-id", span_id,
            "--status", "succeeded", "--outcome", "completed",
        )
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assert_failure_without_lifecycle_write(
            4, "DUPLICATE_ID", "lifecycle-finish",
            "--span-id", span_id, "--status", "succeeded",
        )

    def test_lifecycle_rejects_invalid_terminal_error_without_writing(self):
        started = self.run_helper(
            "lifecycle-start", "--run-id", "run-1",
            "--actor", "orchestrator", "--action", "run",
        )
        self.assertEqual(0, started.returncode, started.stderr)
        span_id = json.loads(started.stdout)["spanId"]
        self.assert_failure_without_lifecycle_write(
            10, "MALFORMED_DATA", "lifecycle-finish",
            "--span-id", span_id, "--status", "failed", "--error-json", "{}",
        )

    def test_lifecycle_rejects_malformed_duplicate_and_symlinked_logs_without_writing(self):
        lifecycle = self.root / "worklog/logs/lifecycle.jsonl"
        lifecycle.write_text("not-json\n", encoding="utf-8")
        self.assert_failure_without_lifecycle_write(
            10, "MALFORMED_DATA", "lifecycle-start",
            "--run-id", "run-1", "--actor", "orchestrator", "--action", "run",
        )

        lifecycle.write_text("", encoding="utf-8")
        started = self.run_helper(
            "lifecycle-start", "--run-id", "run-1",
            "--actor", "orchestrator", "--action", "run",
        )
        self.assertEqual(0, started.returncode, started.stderr)
        line = lifecycle.read_text(encoding="utf-8")
        lifecycle.write_text(line + line, encoding="utf-8")
        span_id = json.loads(started.stdout)["spanId"]
        self.assert_failure_without_lifecycle_write(
            4, "DUPLICATE_ID", "lifecycle-finish",
            "--span-id", span_id, "--status", "succeeded",
        )

        external = Path(self.temporary.name) / "external.jsonl"
        external.write_text("external\n", encoding="utf-8")
        lifecycle.unlink()
        if not try_create_symlink(lifecycle, external):
            self.skipTest("symlink creation is unavailable")
        result = self.run_helper(
            "lifecycle-start", "--run-id", "run-1",
            "--actor", "orchestrator", "--action", "run",
        )
        self.assertEqual(9, result.returncode, result.stderr)
        self.assertRegex(result.stderr, r"^UNSAFE_PATH:")
        self.assertEqual(b"external\n", external.read_bytes())

    def test_preflight_accepts_committed_closure_and_rejects_drift(self):
        files = {
            "AGENTS.md": "# Agents\n",
            "docs/codex-policy.md": "# Policy\n",
            "docs/references/builder-handoff.schema.json": "{}\n",
            "docs/references/worklog-events.md": "# Events\n",
            ".codex/config.toml": "[agents]\nmax_threads = 4\n",
            ".codex/agents/harness-builder.toml": 'name = "builder"\n',
            ".codex/agents/harness-reviewer.toml": 'name = "reviewer"\n',
            ".codex/agents/harness-librarian.toml": 'name = "librarian"\n',
            "docs/project-map.md": "# Map\n",
        }
        for relative, content in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        base_sha = self.commit_fixture()

        result = self.run_helper(
            "preflight", "--base-sha", base_sha, "--runtime", "codex",
            "--reference", "docs/project-map.md#map",
            "--reference", "docs/project-map.md#another-anchor",
            "--reference", "AGENTS.md#safety",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(base_sha, value["baseSha"])
        self.assertIn("docs/project-map.md", value["checkedPaths"])
        self.assertEqual(len(value["checkedPaths"]), len(set(value["checkedPaths"])))
        self.assertGreater(value["checkedBytes"], 0)

        (self.root / "docs/project-map.md").write_text("# Drifted\n", encoding="utf-8")
        self.assert_failure_without_lifecycle_write(
            11, "HARNESS_NOT_COMMITTED", "preflight",
            "--base-sha", base_sha, "--runtime", "codex",
            "--reference", "docs/project-map.md#map",
        )

    def test_preflight_rejects_missing_unsafe_and_unknown_runtime(self):
        base_sha = self.commit_fixture()
        self.assert_failure_without_lifecycle_write(
            11, "HARNESS_NOT_COMMITTED", "preflight",
            "--base-sha", base_sha, "--runtime", "codex",
            "--reference", "docs/missing.md#missing",
        )
        self.assert_failure_without_lifecycle_write(
            9, "UNSAFE_PATH", "preflight",
            "--base-sha", base_sha, "--runtime", "codex",
            "--reference", "../outside.md#outside",
        )
        self.assert_failure_without_lifecycle_write(
            2, "INVALID_INVOCATION", "preflight",
            "--base-sha", base_sha, "--runtime", "unknown",
        )


class ResidueTests(unittest.TestCase):
    def assert_no_package_residue(self):
        files = tracked_package_text_files(ROOT)
        text = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertNotIn("/Users/zhiqibao/Documents/SynologyDrive/01_Projects/harness_v2", text)
        self.assertNotRegex(text, r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*['\"][^'\"]+")

    def test_new_contracts_have_no_source_repo_or_secret_residue(self):
        self.assert_no_package_residue()

    def test_residue_scan_ignores_ignored_binary_files(self):
        cache = ROOT / "skills/builder/__pycache__"
        probe = cache / "probe.pyc"
        cache.mkdir(exist_ok=True)
        probe.write_bytes(b"\xff\xfeignored binary\x00")
        try:
            ignored = subprocess.run(
                ["git", "-C", str(ROOT), "check-ignore", "--quiet", str(probe.relative_to(ROOT))],
            )
            self.assertEqual(0, ignored.returncode, "probe.pyc must be ignored for this regression")
            self.assert_no_package_residue()
        finally:
            probe.unlink(missing_ok=True)
            try:
                cache.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
