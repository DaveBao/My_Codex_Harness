import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = ROOT / "skills/orchestrator/scripts/codex_runtime.py"


def load_runtime():
    if not RUNTIME_PATH.is_file():
        raise AssertionError(f"missing runtime: {RUNTIME_PATH.relative_to(ROOT)}")
    spec = importlib.util.spec_from_file_location("codex_runtime", RUNTIME_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CodexRuntimeTests(unittest.TestCase):
    def test_reduces_official_usage_without_double_counting_reasoning(self):
        runtime = load_runtime()
        result = runtime.reduce_codex_events(
            [
                {"type": "thread.started", "thread_id": "thread-1"},
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": '{"outcome":"ok"}'},
                },
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 60,
                        "output_tokens": 25,
                        "reasoning_output_tokens": 10,
                    },
                },
            ]
        )

        self.assertEqual(
            {
                "sessionId": "thread-1",
                "finalMessage": '{"outcome":"ok"}',
                "tokens": {"input": 100, "cachedInput": 60, "output": 25, "total": 125},
                "reasoningOutputTokens": 10,
                "telemetryComplete": True,
                "telemetryReason": None,
            },
            result,
        )

    def test_missing_usage_is_null_and_never_estimated(self):
        runtime = load_runtime()
        result = runtime.reduce_codex_events(
            [{"type": "thread.started", "thread_id": "thread-1"}]
        )

        self.assertEqual(
            {"input": None, "cachedInput": None, "output": None, "total": None},
            result["tokens"],
        )
        self.assertFalse(result["telemetryComplete"])
        self.assertEqual("usage_unavailable", result["telemetryReason"])

    def test_builds_start_and_context_repair_resume_arguments(self):
        runtime = load_runtime()

        self.assertEqual(
            ["exec", "--json", "--output-schema", "/tmp/role.json", "-"],
            runtime.build_codex_args(
                {"mode": "start", "schemaPath": "/tmp/role.json"}
            ),
        )
        self.assertEqual(
            [
                "exec",
                "resume",
                "--json",
                "--output-schema",
                "/tmp/role.json",
                "session-1",
                "-",
            ],
            runtime.build_codex_args(
                {
                    "mode": "resume",
                    "sessionId": "session-1",
                    "schemaPath": "/tmp/role.json",
                }
            ),
        )
        with self.assertRaisesRegex(ValueError, "sessionId"):
            runtime.build_codex_args({"mode": "resume"})

    def test_summarizes_exclusive_finished_spans(self):
        runtime = load_runtime()

        def event(span_id, actor, tokens, **metadata):
            return {
                "runId": "run-1",
                "phase": "finished",
                "spanId": span_id,
                "actor": actor,
                "waveId": "wave-1",
                "featureId": "F001" if actor == "builder" else None,
                "tokens": tokens,
                "metadata": metadata,
            }

        builder = event(
            "builder-2",
            "builder",
            {"input": 100, "cachedInput": 60, "output": 25, "total": 125},
            attemptNumber=2,
        )
        summary = runtime.summarize_lifecycle(
            [
                event(
                    "wave",
                    "orchestrator",
                    {"input": 100, "cachedInput": 60, "output": 20, "total": 120},
                ),
                builder,
                builder,
                event(
                    "reviewer",
                    "reviewer",
                    {"input": 100, "cachedInput": 60, "output": 25, "total": 125},
                ),
                event(
                    "librarian",
                    "librarian",
                    {"input": None, "cachedInput": None, "output": None, "total": None},
                ),
                {**event("debug", "builder", {"input": 999, "cachedInput": 0, "output": 1, "total": 1000}), "metadata": {"mode": "debug"}},
            ],
            "run-1",
        )

        self.assertEqual(
            {"input": 300, "cachedInput": 180, "output": 70, "total": 370},
            summary["total"],
        )
        self.assertEqual(4, summary["spanCount"])
        self.assertEqual(3, summary["completeSpanCount"])
        self.assertEqual(0.75, summary["usageCompleteness"])
        self.assertEqual(125, summary["byRole"]["builder"]["total"])
        self.assertEqual(125, summary["byAttempt"]["F001:2"]["total"])

    def test_turn_and_summary_cli_are_bounded_and_read_only(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_codex = root / "codex"
            fake_codex.write_text(
                """#!/usr/bin/env python3
import json
import sys
if sys.argv[1:] == [\"--version\"]:
    print(\"fake-codex 1.0\")
    raise SystemExit(0)
prompt = sys.stdin.read()
print(json.dumps({\"type\": \"thread.started\", \"thread_id\": \"fake-session\"}))
print(json.dumps({\"type\": \"item.completed\", \"item\": {\"type\": \"agent_message\", \"text\": \"done\"}}))
print(json.dumps({\"type\": \"turn.completed\", \"usage\": {\"input_tokens\": 20, \"cached_input_tokens\": 8, \"output_tokens\": 5, \"reasoning_output_tokens\": 2}}))
""",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            request = {
                "mode": "start",
                "cwd": directory,
                "prompt": "private assignment",
            }
            turn = subprocess.run(
                [sys.executable, str(RUNTIME_PATH), "turn"],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                env={**os.environ, "CODEX_BIN": str(fake_codex)},
                check=False,
            )

            self.assertEqual(0, turn.returncode, turn.stderr)
            self.assertEqual("", turn.stderr)
            result = json.loads(turn.stdout)
            self.assertEqual("fake-session", result["sessionId"])
            self.assertEqual(
                {"input": 20, "cachedInput": 8, "output": 5, "total": 25},
                result["tokens"],
            )
            self.assertNotIn("private assignment", turn.stdout)
            self.assertNotIn("events", result)

            lifecycle = root / "lifecycle.jsonl"
            original = json.dumps(
                {
                    "runId": "run-1",
                    "phase": "finished",
                    "spanId": "builder-1",
                    "actor": "builder",
                    "waveId": "wave-1",
                    "featureId": "F001",
                    "tokens": {"input": 10, "cachedInput": 4, "output": 2, "total": 12},
                    "metadata": {"attemptNumber": 1},
                }
            ) + "\n"
            lifecycle.write_text(original, encoding="utf-8")
            summary = subprocess.run(
                [
                    sys.executable,
                    str(RUNTIME_PATH),
                    "summary",
                    "--run-id",
                    "run-1",
                    "--lifecycle",
                    str(lifecycle),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, summary.returncode, summary.stderr)
            self.assertEqual("", summary.stderr)
            self.assertEqual(12, json.loads(summary.stdout)["total"]["total"])
            self.assertEqual(original, lifecycle.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
