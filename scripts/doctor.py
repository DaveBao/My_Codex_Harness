#!/usr/bin/env python3
"""Diagnose prerequisites, an installation, or an offline bundle."""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy_model import (  # noqa: E402
    DESIRED_AGENTS,
    canonical_owned_path,
    hash_path,
    load_json_object,
    validate_home,
    validate_target,
)


ROOT = Path(__file__).resolve().parents[1]
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CONFIG_BACKUP = re.compile(
    r"\.codex/my-codex-harness/backups/config\.toml\.backup-[0-9]{8}T[0-9]{12}Z"
)
_CLEANUP_BACKUP = re.compile(
    r"(?:\.codex/plugins/\.my-codex-harness|\.agents/skills/\.[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"\.rollback-[0-9]{8}T[0-9]{12}Z"
)
_INSTALL_JOURNAL = re.compile(
    r"\.codex/my-codex-harness/journals/install-[0-9]{8}T[0-9]{12}Z\.json"
)
_MANIFEST_FIELDS = {"version", "sourceCommit", "desiredAgents", "files"}
_STATE_FIELDS = {
    "version",
    "sourceCommit",
    "mode",
    "ownedPaths",
    "hashes",
    "createdPaths",
    "replacedPaths",
    "backups",
    "addedConfigKeys",
    "preservedConfigKeys",
    "lastJournal",
    "cleanupBackups",
    "cleanupIncomplete",
    "installWarnings",
}


def _canonical_manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("bundle manifest path must be canonical and relative")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value or any(part in ("", ".", "..") for part in pure.parts):
        raise ValueError("bundle manifest path must be canonical and relative")
    return value


def _same_desired_agents(value: object) -> bool:
    return isinstance(value, dict) and set(value) == set(DESIRED_AGENTS) and all(
        type(value[key]) is type(expected) and value[key] == expected
        for key, expected in DESIRED_AGENTS.items()
    )


def _validate_bundle_manifest(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != _MANIFEST_FIELDS:
        raise ValueError(f"{label} must contain exactly {sorted(_MANIFEST_FIELDS)}")
    for field in ("version", "sourceCommit"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"{label} {field} must be a non-empty string")
    if not _same_desired_agents(value["desiredAgents"]):
        raise ValueError(f"{label} desiredAgents does not match the supported settings")
    files = value["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError(f"{label} files must be a non-empty object")
    canonical = {_canonical_manifest_path(relative) for relative in files}
    if len(canonical) != len(files):
        raise ValueError(f"{label} contains duplicate canonical file paths")
    for relative, digest in files.items():
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError(f"{label} contains an invalid file hash: {relative}")
    return files


def _validate_toml_parseability(text: str) -> None:
    """Validate TOML, falling back to conservative lexical checks on Python 3.10.

    The fallback is intentionally not a full semantic TOML parser. It accepts
    common Codex configuration forms while failing closed on unterminated
    headers, strings, arrays, and inline tables.
    """
    if tomllib is not None:
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"config.toml is not parseable TOML: {error}") from error
        return
    _validate_toml_lexically(text)


def _validate_toml_lexically(text: str) -> None:
    stack: list[str] = []
    delimiter = None
    comment = False
    header_open = False
    line_has_token = False
    index = 0
    while index < len(text):
        character = text[index]
        if comment:
            if character in "\r\n":
                comment = False
                line_has_token = False
            index += 1
            continue
        if delimiter is not None:
            if len(delimiter) == 3:
                if delimiter == '"""' and character == "\\":
                    index += 2
                elif text.startswith(delimiter, index):
                    delimiter = None
                    index += 3
                else:
                    index += 1
                continue
            if character in "\r\n":
                raise ValueError("config.toml has an unterminated string")
            if delimiter == '"' and character == "\\":
                if index + 1 >= len(text) or text[index + 1] in "\r\n":
                    raise ValueError("config.toml has an unterminated string")
                index += 2
            elif character == delimiter:
                delimiter = None
                index += 1
            else:
                index += 1
            continue
        if character == "#":
            comment = True
            index += 1
            continue
        if character in "\r\n":
            if header_open:
                raise ValueError("config.toml has an unterminated table header")
            line_has_token = False
            index += 1
            continue
        if character.isspace():
            index += 1
            continue
        if character in "'\"":
            triple = character * 3
            delimiter = triple if text.startswith(triple, index) else character
            line_has_token = True
            index += len(delimiter)
            continue
        if character in "[{":
            if character == "[" and not stack and not line_has_token:
                header_open = True
            stack.append(character)
            line_has_token = True
            index += 1
            continue
        if character in "]}":
            expected = "[" if character == "]" else "{"
            if not stack or stack[-1] != expected:
                raise ValueError("config.toml has mismatched brackets or braces")
            stack.pop()
            if header_open and not stack:
                header_open = False
            line_has_token = True
            index += 1
            continue
        line_has_token = True
        index += 1
    if delimiter is not None:
        raise ValueError("config.toml has an unterminated string")
    if header_open:
        raise ValueError("config.toml has an unterminated table header")
    if stack:
        name = "array" if stack[-1] == "[" else "inline table"
        raise ValueError(f"config.toml has an unterminated {name}")


def _check_writable_directory(home: Path, target: Path) -> None:
    validate_target(home, target)
    probe = target
    while not probe.exists():
        probe = probe.parent
    mode = probe.lstat().st_mode
    if not stat.S_ISDIR(mode):
        raise ValueError(f"writable user path must resolve from a directory: {target}")
    if os.name != "nt" and mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0:
        raise ValueError(f"user directory is not writable: {target}")
    descriptor = None
    name = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".harness-doctor-", dir=probe)
        os.write(descriptor, b"write probe\n")
        os.fsync(descriptor)
    except OSError as error:
        raise ValueError(f"user directory is not writable: {target}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if name is not None:
            Path(name).unlink(missing_ok=True)


def _state_paths(home: Path, value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
        raise ValueError(f"install state {label} must be a list of paths")
    if len(value) != len(set(value)):
        raise ValueError(f"install state {label} contains duplicate paths")
    for path in value:
        canonical_owned_path(home, path)
    return value


def _state_hash_entries(
    home: Path,
    value: object,
    label: str,
    allowed_path: re.Pattern,
    *,
    regular_only: bool = False,
) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError(f"install state {label} must be a list")
    paths = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ValueError(f"install state {label} contains an invalid entry")
        relative = entry["path"]
        if not isinstance(relative, str) or allowed_path.fullmatch(relative) is None:
            raise ValueError(f"install state {label} contains an invalid path")
        path = canonical_owned_path(home, relative)
        if not isinstance(entry["sha256"], str) or _SHA256.fullmatch(entry["sha256"]) is None:
            raise ValueError(f"install state {label} contains an invalid hash")
        if not path.exists() and not path.is_symlink():
            raise ValueError(f"install state {label} path is missing")
        if regular_only and (path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode)):
            raise ValueError(f"install state {label} path must be a regular file")
        if hash_path(path) != entry["sha256"]:
            raise ValueError(f"install state {label} hash does not match its path")
        paths.append(relative)
    if len(paths) != len(set(paths)):
        raise ValueError(f"install state {label} contains duplicate paths")
    return value


def _validate_last_journal(home: Path, state: dict, cleanup: list[dict], warnings: list[str]) -> None:
    relative = state["lastJournal"]
    if not isinstance(relative, str) or _INSTALL_JOURNAL.fullmatch(relative) is None:
        raise ValueError("install state lastJournal is not a canonical install journal path")
    path = canonical_owned_path(home, relative)
    if not path.exists() or path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("install state lastJournal must be an existing regular file")
    journal = load_json_object(path)
    expected_status = "cleanup-incomplete" if state["cleanupIncomplete"] else "completed"
    if journal.get("status") != expected_status:
        raise ValueError("install state cleanup fields do not match the journal status")
    if not state["cleanupIncomplete"] and (cleanup or warnings):
        raise ValueError("completed install state contains cleanup data")
    actions = journal.get("actions")
    if not isinstance(actions, list):
        raise ValueError("install journal actions must be a list")
    state_intents = [
        action
        for action in actions
        if isinstance(action, dict) and action.get("action") == "intent-state"
    ]
    if len(state_intents) != 1 or state_intents[0].get("path") != (
        ".codex/my-codex-harness/install-state.json"
    ):
        raise ValueError("install journal is not bound to the install state")


def _validate_install_state(home: Path, state: object) -> None:
    if not isinstance(state, dict) or not _STATE_FIELDS.issubset(state):
        raise ValueError("install state is missing required top-level fields")
    for field in ("version", "sourceCommit"):
        if not isinstance(state[field], str) or not state[field].strip():
            raise ValueError(f"install state {field} must be a non-empty string")
    if state["mode"] not in ("symlink", "copy"):
        raise ValueError("install state mode must be symlink or copy")
    owned = _state_paths(home, state["ownedPaths"], "ownedPaths")
    created = _state_paths(home, state["createdPaths"], "createdPaths")
    replaced = _state_paths(home, state["replacedPaths"], "replacedPaths")
    history = set(created) | set(replaced)
    allowed_history = set(owned) | {".codex/config.toml"}
    if (
        set(created) & set(replaced)
        or not set(owned).issubset(history)
        or not history.issubset(allowed_history)
    ):
        raise ValueError("install state createdPaths and replacedPaths are inconsistent")
    hashes = state["hashes"]
    if not isinstance(hashes, dict) or set(hashes) != set(owned):
        raise ValueError("install state hashes must exactly match ownedPaths")
    for path, digest in hashes.items():
        canonical_owned_path(home, path)
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError(f"install state contains an invalid hash: {path}")
    _state_hash_entries(
        home, state["backups"], "backups", _CONFIG_BACKUP, regular_only=True
    )
    cleanup = _state_hash_entries(
        home, state["cleanupBackups"], "cleanupBackups", _CLEANUP_BACKUP
    )
    for field in ("addedConfigKeys", "preservedConfigKeys"):
        value = state[field]
        if not isinstance(value, dict) or not set(value).issubset(DESIRED_AGENTS):
            raise ValueError(f"install state {field} contains unexpected keys")
    added = state["addedConfigKeys"]
    preserved = state["preservedConfigKeys"]
    if not all(
        type(value) is type(DESIRED_AGENTS[key]) and value == DESIRED_AGENTS[key]
        for key, value in added.items()
    ):
        raise ValueError("install state addedConfigKeys contains invalid values")
    if not all(type(value) is type(DESIRED_AGENTS[key]) for key, value in preserved.items()):
        raise ValueError("install state preservedConfigKeys contains invalid value types")
    if set(added) | set(preserved) != set(DESIRED_AGENTS):
        raise ValueError("install state config keys are incomplete")
    if not isinstance(state["cleanupIncomplete"], bool):
        raise ValueError("install state cleanupIncomplete must be a boolean")
    if cleanup and not state["cleanupIncomplete"]:
        raise ValueError("install state cleanup fields are inconsistent")
    warnings = state["installWarnings"]
    if not isinstance(warnings, list) or not all(isinstance(warning, str) for warning in warnings):
        raise ValueError("install state installWarnings must be a list of strings")
    if state["cleanupIncomplete"] and not cleanup and not warnings:
        raise ValueError("install state cleanup state lacks an explanation")
    _validate_last_journal(home, state, cleanup, warnings)


def _check_prerequisites(home: Path, require_codex: bool) -> list[str]:
    if sys.version_info < (3, 10):
        raise ValueError("Python 3.10 or newer is required")
    validate_home(home)
    _check_writable_directory(home, home / ".codex")
    _check_writable_directory(home, home / ".agents")
    if shutil.which("git") is None:
        raise ValueError("Git executable not found")
    if require_codex and shutil.which("codex") is None:
        raise ValueError("Codex executable not found")
    config = home / ".codex/config.toml"
    if config.exists():
        if config.is_symlink() or not config.is_file():
            raise ValueError("config.toml must be a regular file")
        try:
            _validate_toml_parseability(config.read_text(encoding="utf-8"))
        except UnicodeDecodeError as error:
            raise ValueError("config.toml must be UTF-8") from error
    with tempfile.TemporaryDirectory(dir=home) as directory:
        root = Path(directory)
        target = root / "target"
        link = root / "link"
        target.mkdir()
        try:
            link.symlink_to(target, target_is_directory=True)
            symlinks = link.is_symlink()
        except OSError:
            symlinks = False
    return [
        f"Python: {sys.version_info.major}.{sys.version_info.minor}",
        f"Git: {shutil.which('git')}",
        f"Codex: {shutil.which('codex') or 'not requested/not found'}",
        f"HOME: {home}",
        f"Symlinks: {'available' if symlinks else 'copy fallback required'}",
    ]


def _verify_bundle(archive: Path) -> dict:
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("bundle archive must be a regular file")
    sidecar = archive.with_name(archive.name + ".sha256")
    manifest_path = archive.parent / "bundle-manifest.json"
    if not sidecar.is_file() or sidecar.is_symlink():
        raise ValueError("bundle checksum sidecar is missing")
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("bundle manifest is missing")
    fields = sidecar.read_text(encoding="utf-8").strip().split()
    if len(fields) != 2 or fields[1] != archive.name:
        raise ValueError("invalid bundle checksum sidecar")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if fields[0] != digest:
        raise ValueError("bundle checksum mismatch")
    external = load_json_object(manifest_path)
    _validate_bundle_manifest(external, "external bundle manifest")
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        if not members:
            raise ValueError("bundle archive is empty")
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("bundle contains duplicate member names")
        for member in members:
            pure = PurePosixPath(member.name)
            if not pure.parts or pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"unsafe bundle member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported bundle member type: {member.name}")
        roots = {PurePosixPath(member.name).parts[0] for member in members}
        if len(roots) != 1:
            raise ValueError("bundle must contain one package root")
        root = next(iter(roots))
        inner_stream = stream.extractfile(f"{root}/bundle-manifest.json")
        if inner_stream is None:
            raise ValueError("bundle internal manifest is missing")
        inner = json.loads(inner_stream.read())
        files = _validate_bundle_manifest(inner, "internal bundle manifest")
        if inner != external:
            raise ValueError("bundle manifests differ")
        canonical_files = set(files)
        for member in members:
            pure = PurePosixPath(member.name)
            if pure.as_posix() != member.name:
                raise ValueError(f"unsafe noncanonical bundle member: {member.name}")
        expected_members = {f"{root}/{relative}" for relative in canonical_files}
        expected_members.add(f"{root}/bundle-manifest.json")
        actual_members = {member.name for member in members if member.isfile()}
        if actual_members != expected_members:
            raise ValueError("bundle file set differs from its manifest")
        for relative, expected in files.items():
            member_stream = stream.extractfile(f"{root}/{relative}")
            if member_stream is None:
                raise ValueError(f"bundle file missing: {relative}")
            if hashlib.sha256(member_stream.read()).hexdigest() != expected:
                raise ValueError(f"bundle file hash mismatch: {relative}")
    return external


def _verify_extracted_package(root: Path) -> bool:
    manifest_path = root / "bundle-manifest.json"
    if not manifest_path.exists():
        return False
    manifest = load_json_object(manifest_path)
    files = _validate_bundle_manifest(manifest, "package manifest")
    expected_files = set(files)
    actual_files = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts:
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode) or (stat.S_ISREG(mode) and path.suffix == ".pyc"):
                continue
            raise ValueError(f"unsupported generated package path: {relative.as_posix()}")
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"unsupported extracted package path: {relative.as_posix()}")
        if relative.as_posix() != "bundle-manifest.json":
            actual_files.add(relative.as_posix())
    if actual_files != expected_files:
        raise ValueError("extracted package file set differs from its manifest")
    for relative, expected in files.items():
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"extracted package hash mismatch: {relative}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="validate installed ownership")
    parser.add_argument("--bundle", type=Path, help="validate an offline archive")
    parser.add_argument("--require-codex", action="store_true", help="require the Codex executable")
    args = parser.parse_args(argv)
    home = Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or str(Path.home()))
    try:
        lines = _check_prerequisites(home, args.require_codex)
        if _verify_extracted_package(ROOT):
            lines.append("Package manifest: valid")
        if args.installed:
            from uninstall import build_plan

            state_path = home / ".codex/my-codex-harness/install-state.json"
            validate_target(home, state_path)
            if not state_path.exists():
                raise ValueError("My Codex Harness is not installed")
            _validate_install_state(home, load_json_object(state_path))
            installed_plan = build_plan(home)
            if installed_plan is None:
                raise ValueError("My Codex Harness is not installed")
            if installed_plan["state"].get("cleanupIncomplete") or installed_plan["state"].get(
                "cleanupBackups"
            ):
                raise ValueError("installation is committed but cleanup remains pending")
            lines.append("Installation: healthy")
        if args.bundle is not None:
            manifest = _verify_bundle(args.bundle.absolute())
            lines.append(f"Bundle: valid ({manifest.get('version', 'unknown')})")
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"doctor failed: {error}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
