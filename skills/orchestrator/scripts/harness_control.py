#!/usr/bin/env python3
"""Perform deterministic Harness control-root mechanics."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from harness_context import HarnessContextError, resolve_control_root, resolve_fixed_file


LIFECYCLE_PATH = Path("worklog/logs/lifecycle.jsonl")
LIFECYCLE_SCHEMA_PATH = Path("docs/references/lifecycle-event.schema.json")
ACTORS = ("orchestrator", "builder", "reviewer", "librarian")
ACTIONS = (
    "run",
    "wave",
    "implement_feature",
    "review_feature",
    "maintain_project_map",
    "merge_feature",
    "validate_feature",
    "validate_global",
    "retry",
    "handoff_rejection",
    "context_repair",
    "checkpoint",
    "pause_context",
    "resume_run",
)
STATUSES = ("succeeded", "failed", "blocked", "interrupted", "cancelled")
EXIT_CODES = {
    "INVALID_INVOCATION": 2,
    "NOT_FOUND": 3,
    "DUPLICATE_ID": 4,
    "IDENTITY_MISMATCH": 6,
    "UNSAFE_PATH": 9,
    "MALFORMED_DATA": 10,
    "HARNESS_NOT_COMMITTED": 11,
}
STATIC_CLOSURE = (
    "AGENTS.md",
    "docs/codex-policy.md",
    "docs/references/builder-handoff.schema.json",
    "docs/references/lifecycle-event.schema.json",
    "docs/references/worklog-events.md",
)
RUNTIME_CLOSURES = {
    "codex": (
        ".codex/config.toml",
        ".codex/agents/harness-builder.toml",
        ".codex/agents/harness-reviewer.toml",
        ".codex/agents/harness-librarian.toml",
    ),
}


class ControlError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = EXIT_CODES[code]


class ControlArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise ControlError("INVALID_INVOCATION", "arguments are invalid")


def fail(code: str, message: str) -> NoReturn:
    raise ControlError(code, message)


def reject_nonstandard_number(value: str) -> NoReturn:
    fail("MALFORMED_DATA", f"non-standard JSON number is forbidden: {value}")


def parse_object(raw: str, field: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, parse_constant=reject_nonstandard_number)
    except ControlError:
        raise
    except json.JSONDecodeError:
        fail("MALFORMED_DATA", f"{field} JSON is malformed")
    if not isinstance(value, dict):
        fail("MALFORMED_DATA", f"{field} must be a JSON object")
    return value


def parse_error(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    value = parse_object(raw, "error")
    if set(value) != {"category", "retryable", "summary"}:
        fail("MALFORMED_DATA", "error fields are invalid")
    if not isinstance(value["category"], str) or not value["category"]:
        fail("MALFORMED_DATA", "error category must be non-empty")
    if not isinstance(value["retryable"], bool):
        fail("MALFORMED_DATA", "error retryable must be boolean")
    if not isinstance(value["summary"], str) or not 0 < len(value["summary"]) <= 500:
        fail("MALFORMED_DATA", "error summary length is invalid")
    return value


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail("MALFORMED_DATA", "persisted lifecycle timestamp is malformed")
    if parsed.tzinfo is None:
        fail("MALFORMED_DATA", "persisted lifecycle timestamp has no timezone")
    return parsed


def tokens(mode: str) -> dict[str, int | None]:
    value: int | None = None if mode == "app-role" else 0
    return {"input": value, "cachedInput": value, "output": value, "total": value}


def lifecycle_file(root: Path) -> Path:
    schema = resolve_fixed_file(root, LIFECYCLE_SCHEMA_PATH)
    try:
        schema_value = json.loads(schema.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("MALFORMED_DATA", "lifecycle schema is malformed")
    if not isinstance(schema_value, dict) or schema_value.get("$id") != "lifecycle-event.schema.json":
        fail("MALFORMED_DATA", "lifecycle schema identity is invalid")
    return resolve_fixed_file(root, LIFECYCLE_PATH)


def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                value = json.loads(line, parse_constant=reject_nonstandard_number)
                if not isinstance(value, dict):
                    fail("MALFORMED_DATA", "lifecycle event must be an object")
                events.append(value)
    except ControlError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("MALFORMED_DATA", "lifecycle JSONL is malformed")
    event_ids = [event.get("eventId") for event in events]
    if any(not isinstance(event_id, str) or not event_id for event_id in event_ids):
        fail("MALFORMED_DATA", "lifecycle eventId is invalid")
    if len(event_ids) != len(set(event_ids)):
        fail("DUPLICATE_ID", "lifecycle eventId is not unique")
    return events


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        fail("UNSAFE_PATH", "lifecycle destination cannot be opened safely")
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            fail("UNSAFE_PATH", "lifecycle destination must be a regular file")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                fail("UNSAFE_PATH", "lifecycle append did not complete")
            offset += written
        os.fsync(descriptor)
    except OSError:
        fail("UNSAFE_PATH", "lifecycle append failed")
    finally:
        os.close(descriptor)


def require_identity_pair(feature_id: str | None, feature_name: str | None) -> None:
    if bool(feature_id) != bool(feature_name):
        fail("IDENTITY_MISMATCH", "feature ID and name must both be set or both be absent")


def normalized_project_path(raw: str) -> str:
    raw_path = raw.split("#", 1)[0]
    path = PurePosixPath(raw_path)
    if (
        not raw_path
        or path.is_absolute()
        or "\\" in raw_path
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        fail("UNSAFE_PATH", "project reference path is unsafe")
    return path.as_posix()


def committed_bytes(root: Path, base_sha: str, relative: str) -> bytes:
    if not base_sha or any(character not in "0123456789abcdef" for character in base_sha) or len(base_sha) not in {40, 64}:
        fail("INVALID_INVOCATION", "base SHA must be 40 or 64 lowercase hexadecimal characters")
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{base_sha}:{relative}"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        fail("HARNESS_NOT_COMMITTED", f"required path is absent from base: {relative}")
    return result.stdout


def preflight(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    paths = sorted(set((*STATIC_CLOSURE, *RUNTIME_CLOSURES[args.runtime], *args.reference)))
    normalized = [normalized_project_path(path) for path in paths]
    checked_bytes = 0
    for relative in normalized:
        try:
            path = resolve_fixed_file(root, Path(relative))
        except HarnessContextError as error:
            if error.code == "NOT_FOUND":
                fail("HARNESS_NOT_COMMITTED", f"required live path is missing: {relative}")
            raise
        live = path.read_bytes()
        if committed_bytes(root, args.base_sha, relative) != live:
            fail("HARNESS_NOT_COMMITTED", f"required path differs from base: {relative}")
        checked_bytes += len(live)

    for raw_path in args.installed_path:
        installed = Path(raw_path)
        if not installed.is_absolute():
            fail("UNSAFE_PATH", "installed closure path must be absolute")
        try:
            resolved = installed.resolve(strict=True)
        except OSError:
            fail("NOT_FOUND", "installed closure path is unavailable")
        if not resolved.is_file() or not os.access(resolved, os.R_OK):
            fail("UNSAFE_PATH", "installed closure path must be a readable file")

    return {
        "baseSha": args.base_sha,
        "checkedBytes": checked_bytes,
        "checkedPaths": normalized,
        "installedPathCount": len(args.installed_path),
        "runtime": args.runtime,
    }


def start_event(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    require_identity_pair(args.feature_id, args.feature_name)
    path = lifecycle_file(root)
    load_events(path)
    event_id = str(uuid.uuid4())
    span_id = str(uuid.uuid4())
    recorded_at = timestamp()
    event = {
        "schemaVersion": 1,
        "eventId": event_id,
        "runId": args.run_id,
        "waveId": args.wave_id,
        "featureId": args.feature_id,
        "featureName": args.feature_name,
        "spanId": span_id,
        "parentSpanId": args.parent_span_id,
        "timestamp": recorded_at,
        "phase": "started",
        "actor": args.actor,
        "action": args.action,
        "model": args.model,
        "reasoningEffort": args.reasoning_effort,
        "durationMs": None,
        "tokens": tokens(args.token_mode),
        "status": None,
        "outcome": None,
        "error": None,
        "metadata": parse_object(args.metadata_json, "metadata"),
    }
    append_jsonl(path, event)
    return {"eventId": event_id, "spanId": span_id, "timestamp": recorded_at}


def finish_event(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    path = lifecycle_file(root)
    events = load_events(path)
    starts = [event for event in events if event.get("spanId") == args.span_id and event.get("phase") == "started"]
    terminals = [event for event in events if event.get("spanId") == args.span_id and event.get("phase") == "finished"]
    if not starts:
        fail("NOT_FOUND", "lifecycle start span was not found")
    if len(starts) != 1 or terminals:
        fail("DUPLICATE_ID", "lifecycle span is not uniquely open")
    started = starts[0]
    recorded_at = timestamp()
    duration_ms = max(
        0,
        round((parse_timestamp(recorded_at) - parse_timestamp(started["timestamp"])).total_seconds() * 1000),
    )
    event_id = str(uuid.uuid4())
    event = {
        **{key: started[key] for key in (
            "schemaVersion", "runId", "waveId", "featureId", "featureName", "spanId",
            "parentSpanId", "actor", "action", "model", "reasoningEffort",
        )},
        "eventId": event_id,
        "timestamp": recorded_at,
        "phase": "finished",
        "durationMs": duration_ms,
        "tokens": started["tokens"],
        "status": args.status,
        "outcome": args.outcome,
        "error": parse_error(args.error_json),
        "metadata": parse_object(args.metadata_json, "metadata"),
    }
    append_jsonl(path, event)
    return {
        "durationMs": duration_ms,
        "eventId": event_id,
        "spanId": args.span_id,
        "status": args.status,
        "timestamp": recorded_at,
    }


def build_parser() -> ControlArgumentParser:
    parser = ControlArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=ControlArgumentParser)
    start = commands.add_parser("lifecycle-start")
    start.add_argument("--control-root", required=True)
    start.add_argument("--run-id", required=True)
    start.add_argument("--wave-id")
    start.add_argument("--feature-id")
    start.add_argument("--feature-name")
    start.add_argument("--parent-span-id")
    start.add_argument("--actor", required=True, choices=ACTORS)
    start.add_argument("--action", required=True, choices=ACTIONS)
    start.add_argument("--model")
    start.add_argument("--reasoning-effort")
    start.add_argument("--token-mode", choices=("local", "app-role"), default="local")
    start.add_argument("--metadata-json", default="{}")

    finish = commands.add_parser("lifecycle-finish")
    finish.add_argument("--control-root", required=True)
    finish.add_argument("--span-id", required=True)
    finish.add_argument("--status", required=True, choices=STATUSES)
    finish.add_argument("--outcome")
    finish.add_argument("--error-json")
    finish.add_argument("--metadata-json", default="{}")

    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("--control-root", required=True)
    preflight_parser.add_argument("--base-sha", required=True)
    preflight_parser.add_argument("--runtime", required=True, choices=tuple(RUNTIME_CLOSURES))
    preflight_parser.add_argument("--reference", action="append", default=[])
    preflight_parser.add_argument("--installed-path", action="append", default=[])
    return parser


def emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def main() -> int:
    try:
        args = build_parser().parse_args()
        root = resolve_control_root(args.control_root)
        if args.command == "lifecycle-start":
            result = start_event(args, root)
        elif args.command == "lifecycle-finish":
            result = finish_event(args, root)
        else:
            result = preflight(args, root)
        emit(result)
        return 0
    except HarnessContextError as error:
        message = str(error).replace("\n", " ")[:300]
        sys.stderr.write(f"{error.code}: {message}\n")
        return error.exit_code
    except ControlError as error:
        message = str(error).replace("\n", " ")[:300]
        sys.stderr.write(f"{error.code}: {message}\n")
        return error.exit_code
    except Exception:
        sys.stderr.write("MALFORMED_DATA: control helper failed safely\n")
        return EXIT_CODES["MALFORMED_DATA"]


if __name__ == "__main__":
    raise SystemExit(main())
