#!/usr/bin/env python3
"""Select exact harness context without exposing sibling workflow state."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


FEATURE_SPEC_VERSION = "harness-featurespec-v1"
FEATURE_SPEC_PREFIX = f"{FEATURE_SPEC_VERSION}\n".encode("utf-8")
TODO_PATH = Path("docs/exec-plans/active/TODO.json")
HANDOFF_PATH = Path("worklog/handoffs.jsonl")
# Unknown top-level fields fail closed as immutable feature-definition input.
MUTABLE_FEATURE_FIELDS = {
    "status",
    "attemptCount",
    "handoffReferences",
    "validationHistory",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")

EXIT_CODES = {
    "INVALID_INVOCATION": 2,
    "NOT_FOUND": 3,
    "DUPLICATE_ID": 4,
    "DEBUG_EVENT": 5,
    "IDENTITY_MISMATCH": 6,
    "FEATURE_HASH_MISMATCH": 7,
    "OUTCOME_MISMATCH": 8,
    "UNSAFE_PATH": 9,
    "MALFORMED_DATA": 10,
}


class HarnessContextError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = EXIT_CODES[code]


class ContextArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise HarnessContextError("INVALID_INVOCATION", "arguments are invalid")


def fail(code: str, message: str) -> NoReturn:
    raise HarnessContextError(code, message)


def run_git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        fail("UNSAFE_PATH", "control root is not a valid Git worktree")
    return result.stdout.strip()


def resolve_control_root(raw_root: str) -> Path:
    requested = Path(raw_root)
    if not requested.is_absolute():
        fail("UNSAFE_PATH", "control root must be absolute")

    try:
        root = requested.resolve(strict=True)
    except OSError:
        fail("UNSAFE_PATH", "control root is unavailable")
    if not root.is_dir():
        fail("UNSAFE_PATH", "control root must be a directory")

    top_level = Path(run_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != root:
        fail("UNSAFE_PATH", "control root must be the repository root")

    git_dir = Path(run_git(root, "rev-parse", "--git-dir"))
    common_dir = Path(run_git(root, "rev-parse", "--git-common-dir"))
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    if git_dir.resolve() != common_dir.resolve():
        fail("UNSAFE_PATH", "control root must be the main worktree")
    return root


def resolve_fixed_file(root: Path, relative_path: Path) -> Path:
    candidate = root / relative_path
    try:
        if candidate.is_symlink():
            fail("UNSAFE_PATH", "fixed control file must not be a symlink")
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        fail("NOT_FOUND", "fixed control file is missing")
    except OSError:
        fail("UNSAFE_PATH", "fixed control file is unavailable")

    try:
        resolved.relative_to(root)
    except ValueError:
        fail("UNSAFE_PATH", "fixed control file escapes control root")
    if not resolved.is_file():
        fail("UNSAFE_PATH", "fixed control path must be a regular file")
    return resolved


def reject_nonstandard_number(value: str) -> NoReturn:
    fail("MALFORMED_DATA", f"non-standard JSON number is forbidden: {value}")


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, parse_constant=reject_nonstandard_number)
    except HarnessContextError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("MALFORMED_DATA", "control JSON is malformed")


def canonical_json(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        fail("MALFORMED_DATA", "feature cannot be canonicalized")
    return rendered.encode("utf-8")


def immutable_feature(feature: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in feature.items()
        if key not in MUTABLE_FEATURE_FIELDS
    }


def feature_hash(feature: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(FEATURE_SPEC_PREFIX)
    digest.update(canonical_json(immutable_feature(feature)))
    return digest.hexdigest()


def require_sha256(value: str) -> None:
    if not SHA256_RE.fullmatch(value):
        fail("INVALID_INVOCATION", "expected SHA-256 must be 64 lowercase hex characters")


def select_feature(root: Path, feature_id: str, expected_sha256: str | None) -> dict[str, Any]:
    if expected_sha256 is not None:
        require_sha256(expected_sha256)
    todo = load_json(resolve_fixed_file(root, TODO_PATH))
    if not isinstance(todo, dict) or not isinstance(todo.get("features"), list):
        fail("MALFORMED_DATA", "TODO must contain a features array")
    if any(
        not isinstance(feature, dict) or not isinstance(feature.get("id"), str)
        for feature in todo["features"]
    ):
        fail("MALFORMED_DATA", "every feature must be an object with a string ID")

    matches = [feature for feature in todo["features"] if feature.get("id") == feature_id]
    if not matches:
        fail("NOT_FOUND", "feature ID was not found")
    if len(matches) != 1:
        fail("DUPLICATE_ID", "feature ID is not unique")

    selected = matches[0]
    digest = feature_hash(selected)
    if expected_sha256 is not None and digest != expected_sha256:
        fail("FEATURE_HASH_MISMATCH", "feature definition hash does not match")
    return {
        "feature": selected,
        "featureSpecSha256": digest,
        "featureSpecVersion": FEATURE_SPEC_VERSION,
    }


def load_handoffs(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line, parse_constant=reject_nonstandard_number)
                if not isinstance(event, dict) or not isinstance(event.get("eventId"), str):
                    fail("MALFORMED_DATA", "every handoff event must be an object with eventId")
                events.append(event)
    except HarnessContextError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("MALFORMED_DATA", "handoff JSONL is malformed")
    return events


def select_handoff(
    root: Path,
    event_id: str,
    feature_id: str,
    expected_feature_sha256: str,
    required_outcome: str | None,
) -> dict[str, Any]:
    require_sha256(expected_feature_sha256)
    handoffs = load_handoffs(resolve_fixed_file(root, HANDOFF_PATH))
    event_ids = [event["eventId"] for event in handoffs]
    if len(event_ids) != len(set(event_ids)):
        fail("DUPLICATE_ID", "handoff event IDs are not unique")
    matches = [event for event in handoffs if event["eventId"] == event_id]
    if not matches:
        fail("NOT_FOUND", "handoff event ID was not found")
    if len(matches) != 1:
        fail("DUPLICATE_ID", "handoff event ID is not unique")

    selected = matches[0]
    metadata = selected.get("metadata", {})
    if not isinstance(metadata, dict):
        fail("MALFORMED_DATA", "handoff metadata must be an object")
    if metadata.get("mode") == "debug":
        fail("DEBUG_EVENT", "debug handoff cannot enter the formal workflow")
    if selected.get("featureId") != feature_id:
        fail("IDENTITY_MISMATCH", "handoff feature identity does not match")
    if selected.get("featureSpecSha256") != expected_feature_sha256:
        fail("FEATURE_HASH_MISMATCH", "handoff feature hash does not match")
    if required_outcome is not None and selected.get("outcome") != required_outcome:
        fail("OUTCOME_MISMATCH", "handoff outcome does not match")
    return selected


def parse_reference(raw_reference: str) -> tuple[str, str]:
    if raw_reference.count("#") != 1:
        fail("INVALID_INVOCATION", "reference must contain one path anchor")
    raw_path, anchor = raw_reference.split("#", 1)
    relative = PurePosixPath(raw_path)
    if (
        not raw_path
        or not anchor
        or relative.is_absolute()
        or "\\" in raw_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        fail("UNSAFE_PATH", "reference path or anchor is unsafe")
    return relative.as_posix(), anchor


def committed_file_bytes(root: Path, base_sha: str, relative: str) -> bytes:
    if COMMIT_SHA_RE.fullmatch(base_sha) is None:
        fail("INVALID_INVOCATION", "base SHA must be lowercase hexadecimal")
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{base_sha}:{relative}"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        fail("NOT_FOUND", "referenced file is absent from the base commit")
    return result.stdout


def normalize_anchor(text: str) -> str:
    value = text.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return value.replace(" ", "-")


def select_markdown_section(
    root: Path,
    raw_reference: str,
    base_sha: str,
    legacy_full_fallback: bool,
) -> dict[str, Any]:
    relative, anchor = parse_reference(raw_reference)
    candidate = resolve_fixed_file(root, Path(relative))
    live = candidate.read_bytes()
    if committed_file_bytes(root, base_sha, relative) != live:
        fail("FEATURE_HASH_MISMATCH", "referenced file bytes differ from the base commit")
    try:
        text = live.decode("utf-8")
    except UnicodeError:
        fail("MALFORMED_DATA", "referenced Markdown must be UTF-8")

    digest = hashlib.sha256(live).hexdigest()
    if legacy_full_fallback:
        return {
            "anchor": anchor,
            "byteCount": len(live),
            "content": text,
            "fileSha256": digest,
            "mode": "full_fallback",
            "path": relative,
            "reference": raw_reference,
        }

    headings: list[tuple[int, int, str]] = []
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})[ \t]+(.+?)[ \t]*(?:#+[ \t]*)?(?:\r?\n)?$", line)
        if match:
            headings.append((index, len(match.group(1)), normalize_anchor(match.group(2))))
    matches = [heading for heading in headings if heading[2] == anchor]
    if not matches:
        fail("NOT_FOUND", "Markdown anchor was not found")
    if len(matches) != 1:
        fail("DUPLICATE_ID", "Markdown anchor is not unique")

    start, level, _ = matches[0]
    end = next(
        (index for index, candidate_level, _ in headings if index > start and candidate_level <= level),
        len(lines),
    )
    content = "".join(lines[start:end])
    return {
        "anchor": anchor,
        "byteCount": len(content.encode("utf-8")),
        "content": content,
        "fileSha256": digest,
        "mode": "section",
        "path": relative,
        "reference": raw_reference,
    }


def build_parser() -> ContextArgumentParser:
    parser = ContextArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=ContextArgumentParser,
    )

    feature = subparsers.add_parser("feature")
    feature.add_argument("--control-root", required=True)
    feature.add_argument("--id", required=True)
    feature.add_argument("--expected-sha256")

    assignment = subparsers.add_parser("assignment")
    assignment.add_argument("--control-root", required=True)
    assignment.add_argument("--id", required=True)
    assignment.add_argument("--expected-sha256")

    handoff = subparsers.add_parser("handoff")
    handoff.add_argument("--control-root", required=True)
    handoff.add_argument("--event-id", required=True)
    handoff.add_argument("--feature-id", required=True)
    handoff.add_argument("--expected-feature-sha256", required=True)
    handoff.add_argument("--require-outcome")

    section = subparsers.add_parser("section")
    section.add_argument("--control-root", required=True)
    section.add_argument("--reference", required=True)
    section.add_argument("--base-sha", required=True)
    section.add_argument("--legacy-full-fallback", action="store_true")
    return parser


def emit(value: dict[str, Any]) -> None:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    sys.stdout.write(f"{rendered}\n")


def main() -> int:
    try:
        args = build_parser().parse_args()
        root = resolve_control_root(args.control_root)
        if args.command in {"feature", "assignment"}:
            if not args.id:
                fail("INVALID_INVOCATION", "feature ID must not be empty")
            selected = select_feature(root, args.id, args.expected_sha256)
            if args.command == "assignment":
                selected["feature"] = immutable_feature(selected["feature"])
            emit(selected)
        elif args.command == "handoff":
            if not args.event_id or not args.feature_id:
                fail("INVALID_INVOCATION", "event and feature IDs must not be empty")
            emit(
                select_handoff(
                    root,
                    args.event_id,
                    args.feature_id,
                    args.expected_feature_sha256,
                    args.require_outcome,
                )
            )
        else:
            emit(
                select_markdown_section(
                    root,
                    args.reference,
                    args.base_sha,
                    args.legacy_full_fallback,
                )
            )
        return 0
    except HarnessContextError as error:
        message = str(error).replace("\n", " ")[:300]
        sys.stderr.write(f"{error.code}: {message}\n")
        return error.exit_code
    except Exception:
        sys.stderr.write("MALFORMED_DATA: context helper failed safely\n")
        return EXIT_CODES["MALFORMED_DATA"]


if __name__ == "__main__":
    raise SystemExit(main())
