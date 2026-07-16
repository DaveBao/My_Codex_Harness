import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/efficiency"
BASELINE_BYTES = 106_483
MAX_OPTIMIZED_BYTES = 74_538


class HarnessEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        (self.project / "docs/exec-plans/active").mkdir(parents=True)
        (self.project / "worklog").mkdir()
        shutil.copyfile(FIXTURES / "feature.json", self.project / "docs/exec-plans/active/TODO.json")
        shutil.copyfile(FIXTURES / "project-map.md", self.project / "docs/project-map.md")
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.commit("context fixture")

        assignment = self.context("assignment", "--id", "F002")
        self.feature_sha = json.loads(assignment)["featureSpecSha256"]
        handoffs = (FIXTURES / "handoffs.jsonl").read_text(encoding="utf-8")
        (self.project / "worklog/handoffs.jsonl").write_text(
            handoffs.replace("__FEATURE_SHA256__", self.feature_sha),
            encoding="utf-8",
        )
        self.base_sha = self.commit("handoff fixture")

    def tearDown(self):
        self.temporary.cleanup()

    @property
    def helper(self):
        return ROOT / "skills/orchestrator/scripts/harness_context.py"

    def commit(self, message):
        subprocess.run(["git", "-C", str(self.project), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.project), "-c", "user.name=Harness Test",
                "-c", "user.email=harness@example.invalid", "commit", "-qm", message,
            ],
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def context(self, command, *args):
        result = subprocess.run(
            [sys.executable, str(self.helper), command, "--control-root", str(self.project), *args],
            check=True,
            capture_output=True,
        )
        return result.stdout

    def bounded_control_bytes(self):
        event_id = "00000000-0000-4000-8000-000000000000"
        span_id = "11111111-1111-4111-8111-111111111111"
        started = json.dumps(
            {"eventId": event_id, "spanId": span_id, "timestamp": "2026-07-17T00:00:00Z"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode() + b"\n"
        finished = json.dumps(
            {
                "durationMs": 999_999,
                "eventId": event_id,
                "spanId": span_id,
                "status": "succeeded",
                "timestamp": "2026-07-17T00:16:39.999000Z",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode() + b"\n"
        preflight = json.dumps(
            {
                "baseSha": "a" * 40,
                "checkedBytes": 99_999,
                "checkedPaths": ["docs/project-map.md"],
                "installedPathCount": 6,
                "runtime": "codex",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode() + b"\n"
        return 6 * (len(started) + len(finished)) + 3 * len(preflight)

    def exact_handoff(self, event_id):
        return self.context(
            "handoff",
            "--event-id", event_id,
            "--feature-id", "F002",
            "--expected-feature-sha256", self.feature_sha,
        )

    def measure_exact_context(self):
        builder = (ROOT / "skills/builder/SKILL.md").read_bytes()
        reviewer = (ROOT / "skills/reviewer/SKILL.md").read_bytes()
        policy = (ROOT / "scaffold/docs/codex-policy.md").read_bytes()
        assignment = self.context(
            "assignment", "--id", "F002", "--expected-sha256", self.feature_sha,
        )
        section = self.context(
            "section",
            "--reference", "docs/project-map.md#stock-account-events",
            "--base-sha", self.base_sha,
        )
        selected_event_ids = (
            "builder-1",
            "builder-1", "reviewer-1",
            "builder-2",
            "builder-2", "reviewer-2",
            "builder-3",
        )
        selected_handoffs = [self.exact_handoff(event_id) for event_id in selected_event_ids]
        reviewer_events = [json.loads(self.exact_handoff(f"reviewer-{round_number}")) for round_number in (1, 2, 3)]
        optimized = 3 * len(builder) + 3 * len(reviewer) + 3 * len(policy)
        optimized += 6 * (len(assignment) + len(section))
        optimized += sum(map(len, selected_handoffs))
        optimized += self.bounded_control_bytes()
        return {
            "baselineBytes": BASELINE_BYTES,
            "optimizedBytes": optimized,
            "reductionPercent": round((BASELINE_BYTES - optimized) * 100 / BASELINE_BYTES, 2),
            "roleInvocations": 6,
            "reviewOutcomes": [event["outcome"] for event in reviewer_events],
            "metric": "harness_controlled_context_bytes",
            "assignment": json.loads(assignment),
        }

    def test_f002_shaped_context_reduction(self):
        result = self.measure_exact_context()
        self.assertEqual(6, result["roleInvocations"])
        self.assertEqual(["failed", "failed", "passed"], result["reviewOutcomes"])
        self.assertLessEqual(result["optimizedBytes"], MAX_OPTIMIZED_BYTES, result)
        self.assertGreaterEqual(result["reductionPercent"], 30.0, result)
        self.assertEqual("harness_controlled_context_bytes", result["metric"])
        self.assertEqual(self.feature_sha, result["assignment"]["featureSpecSha256"])
        self.assertEqual(5, len(result["assignment"]["feature"]["acceptanceCriteria"]))
        for mutable in ("status", "attemptCount", "handoffReferences", "validationHistory"):
            self.assertNotIn(mutable, result["assignment"]["feature"])


if __name__ == "__main__":
    unittest.main()
