#!/usr/bin/env python3
"""Run Codex JSONL turns and derive exact, exclusive lifecycle usage."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any


NULL_TOKENS = {"input": None, "cachedInput": None, "output": None, "total": None}
MAX_OUTPUT_BYTES = 10 * 1024 * 1024


class RuntimeFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def reduce_codex_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    session_id = next(
        (
            event.get("thread_id")
            for event in events
            if event.get("type") == "thread.started"
        ),
        None,
    )
    final_message = next(
        (
            item.get("text")
            for event in reversed(events)
            if event.get("type") == "item.completed"
            and isinstance((item := event.get("item")), dict)
            and item.get("type") == "agent_message"
        ),
        None,
    )
    usage = next(
        (
            event.get("usage")
            for event in reversed(events)
            if event.get("type") == "turn.completed"
        ),
        None,
    )
    if not isinstance(usage, dict) or not all(
        _is_count(usage.get(field))
        for field in ("input_tokens", "cached_input_tokens", "output_tokens")
    ):
        return {
            "sessionId": session_id,
            "finalMessage": final_message,
            "tokens": dict(NULL_TOKENS),
            "reasoningOutputTokens": None,
            "telemetryComplete": False,
            "telemetryReason": "usage_unavailable",
        }

    input_tokens = usage["input_tokens"]
    cached_input_tokens = usage["cached_input_tokens"]
    output_tokens = usage["output_tokens"]
    reasoning = usage.get("reasoning_output_tokens")
    return {
        "sessionId": session_id,
        "finalMessage": final_message,
        "tokens": {
            "input": input_tokens,
            "cachedInput": cached_input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        },
        "reasoningOutputTokens": reasoning if _is_count(reasoning) else None,
        "telemetryComplete": True,
        "telemetryReason": None,
    }


def build_codex_args(request: dict[str, Any]) -> list[str]:
    mode = request.get("mode")
    schema = (
        ["--output-schema", request["schemaPath"]]
        if isinstance(request.get("schemaPath"), str) and request["schemaPath"]
        else []
    )
    if mode == "resume":
        session_id = request.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("sessionId is required for resume")
        return ["exec", "resume", "--json", *schema, session_id, "-"]
    if mode != "start":
        raise ValueError(f"unsupported mode: {mode}")
    return ["exec", "--json", *schema, "-"]


def resolve_codex_binary(
    env: dict[str, str] | None = None,
    platform_name: str | None = None,
) -> str:
    environment = os.environ if env is None else env
    platform = sys.platform if platform_name is None else platform_name
    candidates = [environment.get("CODEX_BIN")]
    if platform == "darwin":
        candidates.append("/Applications/ChatGPT.app/Contents/Resources/codex")
    candidates.append(shutil.which("codex", path=environment.get("PATH")))

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if os.sep in candidate and not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run(
                [candidate, "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return candidate
    raise RuntimeFailure("CODEX_UNAVAILABLE", "no usable Codex binary")


def parse_jsonl(text: str) -> list[dict[str, Any]]:
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeFailure(
                "INVALID_CODEX_JSONL", "Codex stdout contained non-JSON data"
            ) from error
        if not isinstance(event, dict):
            raise RuntimeFailure(
                "INVALID_CODEX_JSONL", "Codex stdout event must be an object"
            )
        events.append(event)
    return events


def _bounded_process(
    executable: str,
    args: list[str],
    prompt: str,
    cwd: str | None,
    env: dict[str, str],
) -> tuple[int, str]:
    try:
        process = subprocess.Popen(
            [executable, *args],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except OSError as error:
        raise RuntimeFailure("CODEX_TURN_FAILED", "unable to start Codex") from error

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()

    def drain(name: str, stream: Any) -> None:
        while chunk := stream.read(64 * 1024):
            if len(buffers[name]) + len(chunk) > MAX_OUTPUT_BYTES:
                overflow.set()
                process.kill()
                return
            buffers[name].extend(chunk)

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        process.stdin.write(prompt.encode("utf-8"))
        process.stdin.close()
    except BrokenPipeError:
        pass
    exit_code = process.wait()
    for reader in readers:
        reader.join()

    if overflow.is_set():
        raise RuntimeFailure("CODEX_OUTPUT_LIMIT", "Codex output exceeded 10 MiB")
    if exit_code != 0:
        raise RuntimeFailure("CODEX_TURN_FAILED", f"Codex exited with {exit_code}")
    try:
        return exit_code, buffers["stdout"].decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeFailure("INVALID_CODEX_JSONL", "Codex stdout is not UTF-8") from error


def run_codex_turn(
    request: dict[str, Any],
    *,
    codex_bin: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    args = build_codex_args(request)
    prompt = request.get("prompt", "")
    cwd = request.get("cwd")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError("cwd must be a string")
    environment = dict(os.environ if env is None else env)
    executable = codex_bin or resolve_codex_binary(environment)
    exit_code, stdout = _bounded_process(
        executable, args, prompt, cwd, environment
    )
    result = reduce_codex_events(parse_jsonl(stdout))
    if result["sessionId"] is None and request.get("mode") == "resume":
        result["sessionId"] = request.get("sessionId")
    return {**result, "exitCode": exit_code}


def summarize_lifecycle(
    events: list[dict[str, Any]], run_id: str
) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    canonical: dict[str, str] = {}
    for event in events:
        if (
            event.get("runId") != run_id
            or event.get("phase") != "finished"
            or event.get("metadata", {}).get("mode") == "debug"
        ):
            continue
        span_id = event.get("spanId")
        serialized = json.dumps(event, sort_keys=True, separators=(",", ":"))
        if span_id in unique:
            if canonical[span_id] != serialized:
                raise RuntimeFailure(
                    "DUPLICATE_SPAN", f"conflicting finished event for {span_id}"
                )
            continue
        unique[span_id] = event
        canonical[span_id] = serialized

    fields = ("input", "cachedInput", "output", "total")
    total = {field: 0 for field in fields}
    by_role: dict[str, dict[str, int]] = {}
    by_wave: dict[str, dict[str, int]] = {}
    by_feature: dict[str, dict[str, int]] = {}
    by_attempt: dict[str, dict[str, int]] = {}
    complete_span_count = 0

    def add(bucket: dict[str, dict[str, int]], key: object, tokens: dict[str, int]) -> None:
        if key is None:
            return
        name = str(key)
        current = bucket.setdefault(name, {field: 0 for field in fields})
        for field in fields:
            current[field] += tokens[field]

    for event in unique.values():
        tokens = event.get("tokens")
        if not isinstance(tokens, dict) or not all(
            _is_count(tokens.get(field)) for field in fields
        ):
            continue
        complete_span_count += 1
        for field in fields:
            total[field] += tokens[field]
        add(by_role, event.get("actor"), tokens)
        add(by_wave, event.get("waveId"), tokens)
        add(by_feature, event.get("featureId"), tokens)
        attempt = event.get("metadata", {}).get("attemptNumber")
        if event.get("featureId") and _is_count(attempt):
            add(by_attempt, f"{event['featureId']}:{attempt}", tokens)

    span_count = len(unique)
    return {
        "runId": run_id,
        "total": total,
        "spanCount": span_count,
        "completeSpanCount": complete_span_count,
        "usageCompleteness": 1 if span_count == 0 else complete_span_count / span_count,
        "byRole": by_role,
        "byWave": by_wave,
        "byFeature": by_feature,
        "byAttempt": by_attempt,
    }


def _invalid(message: str) -> RuntimeFailure:
    return RuntimeFailure("INVALID_INVOCATION", message)


def _parse_summary_flags(args: list[str]) -> dict[str, str]:
    if len(args) % 2:
        raise _invalid("summary flags require values")
    allowed = {"--run-id", "--lifecycle"}
    flags: dict[str, str] = {}
    for index in range(0, len(args), 2):
        name, value = args[index : index + 2]
        if name not in allowed or name in flags:
            raise _invalid(f"unsupported summary flag: {name}")
        flags[name] = value
    if not flags.get("--run-id") or not flags.get("--lifecycle"):
        raise _invalid("summary requires --run-id and --lifecycle")
    return flags


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if not arguments:
            raise _invalid("expected turn or summary")
        command, *args = arguments
        if command == "turn":
            if args:
                raise _invalid("turn accepts no command-line arguments")
            raw = sys.stdin.buffer.read(MAX_OUTPUT_BYTES + 1)
            if len(raw) > MAX_OUTPUT_BYTES:
                raise _invalid("turn request exceeded 10 MiB")
            try:
                request = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise _invalid("turn requires one JSON request on stdin") from error
            result = run_codex_turn(request)
        elif command == "summary":
            flags = _parse_summary_flags(args)
            lifecycle = Path(flags["--lifecycle"])
            if not lifecycle.is_file():
                raise _invalid("lifecycle path does not exist")
            result = summarize_lifecycle(
                parse_jsonl(lifecycle.read_text(encoding="utf-8")),
                flags["--run-id"],
            )
        else:
            raise _invalid("expected turn or summary")
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (RuntimeFailure, ValueError, OSError) as error:
        code = getattr(error, "code", "RUNTIME_ERROR")
        print(code, file=sys.stderr)
        return 2 if code == "INVALID_INVOCATION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
