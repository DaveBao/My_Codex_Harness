#!/usr/bin/env python3
"""Safely remove only My Codex Harness-owned installation paths."""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy_model import (  # noqa: E402
    DESIRED_AGENTS,
    atomic_write_bytes,
    atomic_write_json,
    canonical_owned_path,
    hash_path,
    load_json_object,
    remove_path,
    validate_home,
    validate_target,
)
from package_model import (  # noqa: E402
    _has_multiline_close,
    _opening_multiline_delimiter,
    _strip_comment,
    parse_agents_table,
)


HASH = re.compile(r"^[0-9a-f]{64}$")
RECOVERY_TARGET = re.compile(
    r"(?:\.codex/plugins/my-codex-harness"
    r"|\.codex/agents/harness-(?:builder|reviewer|librarian)\.toml"
    r"|\.agents/skills/[A-Za-z0-9][A-Za-z0-9._-]*"
    r"|\.codex/plugins/\.my-codex-harness\.rollback-[0-9TZ]+"
    r"|\.agents/skills/\.[A-Za-z0-9][A-Za-z0-9._-]*\.rollback-[0-9TZ]+)"
)
RECOVERY_BACKUP = re.compile(
    r"(?:\.codex/plugins/\.{1,2}my-codex-harness(?:\.rollback-[0-9TZ]+)?"
    r"|\.codex/agents/\.harness-(?:builder|reviewer|librarian)\.toml"
    r"|\.agents/skills/\.{1,2}[A-Za-z0-9][A-Za-z0-9._-]*(?:\.rollback-[0-9TZ]+)?)"
    r"\.uninstall-[0-9TZ]+"
)


def _timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _remove_config_keys(text: str, keys: dict[str, object]) -> str:
    output = []
    in_agents = False
    multiline = None
    patterns = [re.compile(rf"^\s*{re.escape(key)}\s*=") for key in keys]
    for line in text.splitlines(keepends=True):
        if multiline is not None:
            output.append(line)
            if _has_multiline_close(line, multiline):
                multiline = None
            continue
        stripped = _strip_comment(line).strip()
        if stripped.startswith("["):
            in_agents = stripped == "[agents]"
            output.append(line)
            continue
        if in_agents and any(pattern.match(line) for pattern in patterns):
            continue
        output.append(line)
        if "=" in stripped:
            multiline = _opening_multiline_delimiter(stripped.split("=", 1)[1].strip())
    return "".join(output)


def _relative(home: Path, path: Path) -> str:
    return path.relative_to(home).as_posix()


def _file_hash(content: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(b"file\0")
    digest.update(content)
    return digest.hexdigest()


def _recovery_path(home: Path, relative: object, pattern: re.Pattern[str], label: str) -> Path:
    if not isinstance(relative, str) or not pattern.fullmatch(relative):
        raise ValueError(f"invalid uninstall recovery {label}")
    return canonical_owned_path(home, relative)


def _write_recovery_journal(path: Path, record: dict) -> None:
    atomic_write_json(path, record)


def _pending_uninstall_journals(home: Path) -> list[tuple[Path, dict]]:
    journals = home / ".codex/my-codex-harness/journals"
    validate_target(home, journals)
    if not journals.exists():
        return []
    pending = []
    for path in sorted(journals.iterdir()):
        if not re.fullmatch(r"uninstall-[0-9TZ]+\.json", path.name):
            continue
        value = load_json_object(path)
        if value.get("status") in {
            "running", "committed", "cleanup-incomplete", "rollback-incomplete"
        }:
            pending.append((path, value))
    return pending


def _recovery_record(home: Path, journal_path: Path, value: dict) -> dict:
    timestamp = journal_path.name.removeprefix("uninstall-").removesuffix(".json")
    if not HASH.fullmatch(value.get("stateHash", "")):
        raise ValueError("invalid uninstall recovery state hash")
    expected_state_backup = (
        f".codex/my-codex-harness/.install-state.rollback-{timestamp}.json"
    )
    if value.get("stateBackup") != expected_state_backup:
        raise ValueError("invalid uninstall recovery state backup")
    state_backup = canonical_owned_path(home, expected_state_backup)
    moves = value.get("moves")
    if not isinstance(moves, list):
        raise ValueError("invalid uninstall recovery moves")
    parsed_moves = []
    for entry in moves:
        if not isinstance(entry, dict) or set(entry) != {"path", "backup", "sha256"}:
            raise ValueError("invalid uninstall recovery move")
        if not isinstance(entry["sha256"], str) or not HASH.fullmatch(entry["sha256"]):
            raise ValueError("invalid uninstall recovery move hash")
        path = _recovery_path(home, entry["path"], RECOVERY_TARGET, "target")
        backup = _recovery_path(home, entry["backup"], RECOVERY_BACKUP, "backup")
        expected_backup = path.parent / f".{path.name}.uninstall-{timestamp}"
        if backup != expected_backup:
            raise ValueError("uninstall recovery backup does not match target")
        parsed_moves.append({**entry, "targetPath": path, "backupPath": backup})
    config = value.get("config")
    parsed_config = None
    if config is not None:
        if not isinstance(config, dict) or set(config) != {
            "path", "backup", "oldHash", "newHash"
        }:
            raise ValueError("invalid uninstall recovery config")
        if config["path"] != ".codex/config.toml" or not all(
            isinstance(config[key], str) and HASH.fullmatch(config[key])
            for key in ("oldHash", "newHash")
        ):
            raise ValueError("invalid uninstall recovery config data")
        expected_backup = (
            f".codex/my-codex-harness/backups/config.toml.uninstall-{timestamp}"
        )
        if config["backup"] != expected_backup:
            raise ValueError("invalid uninstall recovery config backup")
        parsed_config = {
            **config,
            "configPath": canonical_owned_path(home, config["path"]),
            "backupPath": canonical_owned_path(home, config["backup"]),
        }
    return {
        **value,
        "_saved": value,
        "stateBackupPath": state_backup,
        "moves": parsed_moves,
        "config": parsed_config,
    }


def _rollback_uninstall(home: Path, journal_path: Path, record: dict) -> None:
    errors = []
    config = record["config"]
    if config is not None:
        path = config["configPath"]
        backup = config["backupPath"]
        current = hash_path(path) if path.exists() else None
        if current == config["newHash"] and backup.exists() and hash_path(backup) == config["oldHash"]:
            atomic_write_bytes(path, backup.read_bytes())
            current = config["oldHash"]
        elif current != config["oldHash"]:
            errors.append(f"preserved drifted config: {path}")
        if backup.exists() and current == config["oldHash"]:
            if hash_path(backup) == config["oldHash"]:
                backup.unlink()
            else:
                errors.append(f"preserved drifted uninstall backup: {backup}")
    for entry in reversed(record["moves"]):
        path = entry["targetPath"]
        backup = entry["backupPath"]
        target_hash = hash_path(path) if path.exists() or path.is_symlink() else None
        backup_hash = hash_path(backup) if backup.exists() or backup.is_symlink() else None
        if target_hash == entry["sha256"] and backup_hash is None:
            continue
        if target_hash is None and backup_hash == entry["sha256"]:
            os.replace(backup, path)
        else:
            errors.append(f"preserved drifted uninstall move: {path}")
    status = "rollback-incomplete" if errors else "rolled-back"
    _write_recovery_journal(
        journal_path, {**record["_saved"], "status": status, "rollbackErrors": errors}
    )
    if errors:
        raise RuntimeError("uninstall recovery incomplete: " + "; ".join(errors))


def _finish_uninstall_cleanup(home: Path, journal_path: Path, record: dict) -> None:
    errors = []
    for entry in record["moves"]:
        path = entry["targetPath"]
        backup = entry["backupPath"]
        if path.exists() or path.is_symlink():
            errors.append(f"preserved unexpected uninstall target: {path}")
            break
        if backup.exists() or backup.is_symlink():
            if hash_path(backup) != entry["sha256"]:
                errors.append(f"preserved drifted uninstall backup: {backup}")
                break
            try:
                remove_path(backup)
            except Exception as error:
                errors.append(f"cleanup failed for {backup}: {error}")
                break
    config = record["config"]
    if not errors and config is not None and config["backupPath"].exists():
        backup = config["backupPath"]
        if hash_path(backup) != config["oldHash"]:
            errors.append(f"preserved drifted uninstall backup: {backup}")
        else:
            try:
                backup.unlink()
            except OSError as error:
                errors.append(f"cleanup failed for {backup}: {error}")
    state_backup = record["stateBackupPath"]
    if not errors and state_backup.exists():
        if hash_path(state_backup) != record["stateHash"]:
            errors.append(f"preserved drifted uninstall recovery state: {state_backup}")
        else:
            try:
                state_backup.unlink()
            except OSError as error:
                errors.append(f"cleanup failed for {state_backup}: {error}")
    if errors:
        _write_recovery_journal(
            journal_path,
            {**record["_saved"], "status": "cleanup-incomplete", "rollbackErrors": errors},
        )
        raise RuntimeError("uninstall committed but cleanup failed: " + "; ".join(errors))
    _write_recovery_journal(
        journal_path, {**record["_saved"], "status": "completed", "rollbackErrors": []}
    )


def _recover_uninstall_operations(home: Path, pending: list[tuple[Path, dict]]) -> None:
    state_path = home / ".codex/my-codex-harness/install-state.json"
    for journal_path, saved in pending:
        record = _recovery_record(home, journal_path, saved)
        state_backup = record["stateBackupPath"]
        active = state_path.exists()
        recovery = state_backup.exists()
        if not active and recovery and hash_path(state_backup) == record["stateHash"]:
            _finish_uninstall_cleanup(home, journal_path, record)
        elif (
            record.get("status") in {"running", "rollback-incomplete"}
            and active
            and not recovery
            and hash_path(state_path) == record["stateHash"]
        ):
            _rollback_uninstall(home, journal_path, record)
        elif not active and not recovery and record.get("status") in {
            "committed", "cleanup-incomplete"
        } and all(
            not entry["backupPath"].exists() and not entry["targetPath"].exists()
            for entry in record["moves"]
        ):
            _write_recovery_journal(
                journal_path,
                {**record["_saved"], "status": "completed", "rollbackErrors": []},
            )
        else:
            raise RuntimeError("uninstall recovery state is missing or drifted")


def _orphaned_uninstall_recovery(home: Path) -> list[Path]:
    root = home / ".codex/my-codex-harness"
    if not root.exists():
        return []
    return sorted(root.glob(".install-state.rollback-*.json"))


def _managed_uninstall_leftovers(home: Path) -> list[Path]:
    leftovers = [
        home / ".codex/plugins/my-codex-harness",
        *(home / f".codex/agents/harness-{name}.toml" for name in ("builder", "reviewer", "librarian")),
    ]
    found = [path for path in leftovers if path.exists() or path.is_symlink()]
    for root in (
        home / ".codex/plugins",
        home / ".codex/agents",
        home / ".agents/skills",
    ):
        validate_target(home, root)
        if not root.exists():
            continue
        for path in root.iterdir():
            relative = _relative(home, path)
            if RECOVERY_BACKUP.fullmatch(relative) or (
                path.name.startswith(".")
                and ".rollback-" in path.name
                and RECOVERY_TARGET.fullmatch(relative)
            ):
                found.append(path)
    return sorted(set(found))


def build_plan(home: Path) -> dict | None:
    home = home.absolute()
    validate_home(home)
    state_path = home / ".codex/my-codex-harness/install-state.json"
    validate_target(home, state_path)
    if not state_path.exists():
        return None
    state = load_json_object(state_path)
    state_raw = state_path.read_bytes()
    owned = state.get("ownedPaths")
    hashes = state.get("hashes")
    added = state.get("addedConfigKeys")
    if not isinstance(owned, list) or not isinstance(hashes, dict) or not isinstance(added, dict):
        raise ValueError("invalid install state ownership data")
    if any(key not in DESIRED_AGENTS or DESIRED_AGENTS[key] != value for key, value in added.items()):
        raise ValueError("install state contains unexpected config ownership")
    if not all(isinstance(relative, str) for relative in owned):
        raise ValueError("invalid owned path in install state")
    package = home / ".codex/plugins/my-codex-harness"
    if package.is_symlink() or not package.is_dir():
        raise ValueError("managed package path drift")
    skills_root = package / "skills"
    if skills_root.is_symlink() or not skills_root.is_dir():
        raise ValueError("managed package skills path drift")
    skill_names = []
    for skill in skills_root.iterdir():
        if not stat.S_ISDIR(skill.lstat().st_mode) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", skill.name):
            raise ValueError(f"invalid installed skill path: {skill.name}")
        skill_names.append(skill.name)
    expected_owned = {
        ".codex/plugins/my-codex-harness",
        ".codex/agents/harness-builder.toml",
        ".codex/agents/harness-reviewer.toml",
        ".codex/agents/harness-librarian.toml",
        *(f".agents/skills/{name}" for name in skill_names),
    }
    if len(owned) != len(set(owned)) or set(owned) != expected_owned or set(hashes) != expected_owned:
        raise ValueError("install state owns an unexpected path set")
    owned_paths = []
    removal_hashes = dict(hashes)
    for relative in owned:
        path = canonical_owned_path(home, relative)
        if not path.exists() and not path.is_symlink():
            raise ValueError(f"managed path drift: missing {relative}")
        if hash_path(path) != hashes.get(relative):
            raise ValueError(f"managed path drift: {relative}")
        owned_paths.append(path)
    cleanup = state.get("cleanupBackups", [])
    if not isinstance(cleanup, list):
        raise ValueError("invalid cleanup backup state")
    for entry in cleanup:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ValueError("invalid cleanup backup entry")
        path = canonical_owned_path(home, entry["path"])
        if not re.fullmatch(
            r"(?:\.codex/plugins/\.my-codex-harness|\.agents/skills/\.[A-Za-z0-9][A-Za-z0-9._-]*)"
            r"\.rollback-[0-9TZ]+",
            entry["path"],
        ):
            raise ValueError("invalid cleanup backup path")
        if not path.exists() or hash_path(path) != entry["sha256"]:
            raise ValueError("cleanup backup drift")
        owned_paths.append(path)
        removal_hashes[entry["path"]] = entry["sha256"]

    config = home / ".codex/config.toml"
    validate_target(home, config)
    config_raw = config.read_bytes() if config.exists() else b""
    try:
        config_text = config_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("config.toml must be UTF-8") from error
    agents = parse_agents_table(config_text)
    for key, expected in added.items():
        if agents.get(key) != expected:
            raise ValueError(f"managed config drift: [agents].{key}")
    return {
        "home": home,
        "state": state,
        "stateRaw": state_raw,
        "statePath": state_path,
        "ownedPaths": owned_paths,
        "removalHashes": removal_hashes,
        "configPath": config,
        "configRaw": config_raw,
        "newConfigRaw": _remove_config_keys(config_text, added).encode("utf-8"),
    }


def uninstall_package(home: Path, *, dry_run: bool = False) -> dict:
    home = home.absolute()
    validate_home(home)
    pending = _pending_uninstall_journals(home)
    if dry_run and pending:
        return {"dryRun": True, "recoveryRequired": True}
    if pending:
        _recover_uninstall_operations(home, pending)
    orphaned = _orphaned_uninstall_recovery(home)
    if orphaned:
        raise RuntimeError(f"orphaned uninstall recovery state: {orphaned[0]}")
    plan = build_plan(home)
    if plan is None:
        leftovers = _managed_uninstall_leftovers(home)
        if leftovers:
            raise RuntimeError("managed uninstall leftovers exist without recovery state")
        return {"idempotent": True}
    if dry_run:
        return {"dryRun": True, "ownedPaths": len(plan["ownedPaths"])}

    revalidated = build_plan(home)
    if revalidated is None or (
        revalidated["stateRaw"], revalidated["configRaw"]
    ) != (plan["stateRaw"], plan["configRaw"]):
        raise ValueError("uninstall inputs drifted after preflight")

    timestamp = _timestamp()
    journal = plan["home"] / f".codex/my-codex-harness/journals/uninstall-{timestamp}.json"
    state_backup = plan["statePath"].parent / f".install-state.rollback-{timestamp}.json"
    record = {
        "status": "running",
        "stateBackup": _relative(home, state_backup),
        "stateHash": hash_path(plan["statePath"]),
        "moves": [],
        "config": None,
        "rollbackErrors": [],
    }
    _write_recovery_journal(journal, record)
    try:
        for path in sorted(plan["ownedPaths"], key=lambda item: len(item.parts), reverse=True):
            relative = path.relative_to(plan["home"]).as_posix()
            if hash_path(path) != plan["removalHashes"][relative]:
                raise ValueError(f"managed path drift after preflight: {relative}")
            backup = path.parent / f".{path.name}.uninstall-{timestamp}"
            record["moves"].append(
                {
                    "path": relative,
                    "backup": _relative(home, backup),
                    "sha256": plan["removalHashes"][relative],
                }
            )
            _write_recovery_journal(journal, record)
            os.replace(path, backup)
        if plan["configRaw"] != plan["newConfigRaw"]:
            if plan["configPath"].read_bytes() != plan["configRaw"]:
                raise ValueError("config.toml drifted after preflight")
            durable_backup = plan["statePath"].parent / "backups" / f"config.toml.uninstall-{timestamp}"
            record["config"] = {
                "path": ".codex/config.toml",
                "backup": _relative(home, durable_backup),
                "oldHash": _file_hash(plan["configRaw"]),
                "newHash": _file_hash(plan["newConfigRaw"]),
            }
            _write_recovery_journal(journal, record)
            atomic_write_bytes(durable_backup, plan["configRaw"])
            atomic_write_bytes(plan["configPath"], plan["newConfigRaw"])
        if plan["statePath"].read_bytes() != plan["stateRaw"]:
            raise ValueError("install state drifted after preflight")
        os.replace(plan["statePath"], state_backup)
        record["status"] = "committed"
        _write_recovery_journal(journal, record)
    except Exception as error:
        parsed = _recovery_record(home, journal, record)
        if not plan["statePath"].exists() and state_backup.exists():
            record["status"] = "committed"
            _write_recovery_journal(journal, record)
            raise RuntimeError(f"uninstall committed but final journal update failed: {error}") from error
        _rollback_uninstall(home, journal, parsed)
        raise
    parsed = _recovery_record(home, journal, record)
    _finish_uninstall_cleanup(home, journal, parsed)
    return {"removed": len(record["moves"])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if not args.dry_run and not args.yes:
        parser.error("use --dry-run to preview or --yes to uninstall")
    home = Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or str(Path.home()))
    try:
        result = uninstall_package(home, dry_run=args.dry_run)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"uninstall failed: {error}", file=sys.stderr)
        return 1
    if result.get("idempotent"):
        print("My Codex Harness is not installed")
    elif args.dry_run:
        if result.get("recoveryRequired"):
            print("uninstall recovery required before changes can be previewed", file=sys.stderr)
            return 1
        print(f"would remove {result['ownedPaths']} owned paths")
    else:
        print(f"uninstalled My Codex Harness ({result['removed']} owned paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
