#!/usr/bin/env python3
"""Install or upgrade My Codex Harness transactionally for one user."""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy_model import (  # noqa: E402
    DESIRED_AGENTS,
    assert_regular_or_missing,
    atomic_write_bytes,
    atomic_write_json,
    canonical_owned_path,
    copy_package,
    hash_package,
    hash_path,
    home_relative,
    load_json_object,
    package_version,
    remove_path,
    source_commit,
    validate_home,
    validate_target,
)
from package_model import merge_agents_table, parse_agents_table  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
AGENT_NAMES = ("builder", "reviewer", "librarian")
SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _paths(home: Path) -> dict[str, Path]:
    return {
        "package": home / ".codex/plugins/my-codex-harness",
        "config": home / ".codex/config.toml",
        "state": home / ".codex/my-codex-harness/install-state.json",
        "journals": home / ".codex/my-codex-harness/journals",
        "backups": home / ".codex/my-codex-harness/backups",
        "agents": home / ".codex/agents",
        "skills": home / ".agents/skills",
    }


def _read_config(path: Path) -> tuple[bytes | None, str]:
    assert_regular_or_missing(path)
    if not path.exists():
        return None, ""
    content = path.read_bytes()
    try:
        return content, content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("config.toml must be UTF-8") from error


def _load_state(path: Path) -> tuple[dict | None, bytes | None]:
    assert_regular_or_missing(path)
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("install state must be a JSON object")
    return value, raw


def _skill_names(root: Path) -> tuple[str, ...]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("package root must be a regular directory")
    skills = root / "skills"
    if skills.is_symlink() or not skills.is_dir():
        raise ValueError("package skills must be a regular directory")
    names = []
    for path in skills.iterdir():
        mode = path.lstat().st_mode
        if not stat.S_ISDIR(mode) or not SKILL_NAME.fullmatch(path.name):
            raise ValueError(f"invalid package skill path: {path.name}")
        names.append(path.name)
    return tuple(sorted(names))


def _expected_owned(paths: dict[str, Path], home: Path, skill_names: tuple[str, ...]) -> set[str]:
    expected = {home_relative(home, paths["package"])}
    expected.update(
        home_relative(home, paths["agents"] / f"harness-{name}.toml") for name in AGENT_NAMES
    )
    expected.update(home_relative(home, paths["skills"] / name) for name in skill_names)
    return expected


def _validate_owned_state(
    home: Path,
    paths: dict[str, Path],
    state: dict,
    config_agents: dict[str, object],
    installed_skill_names: tuple[str, ...],
) -> None:
    owned = state.get("ownedPaths")
    hashes = state.get("hashes")
    added = state.get("addedConfigKeys")
    if not isinstance(owned, list) or not isinstance(hashes, dict) or not isinstance(added, dict):
        raise ValueError("invalid install state ownership data")
    if state.get("mode") not in ("symlink", "copy"):
        raise ValueError("invalid install state mode")
    if any(key not in DESIRED_AGENTS or DESIRED_AGENTS[key] != value for key, value in added.items()):
        raise ValueError("install state contains unexpected config ownership")
    parsed_owned = []
    for relative in owned:
        path = canonical_owned_path(home, relative)
        parsed_owned.append(relative)
        if not path.exists() and not path.is_symlink():
            raise ValueError(f"managed path drift: missing {relative}")
        expected = hashes.get(relative)
        if not isinstance(expected, str) or hash_path(path) != expected:
            raise ValueError(f"managed path drift: {relative}")
    expected_owned = _expected_owned(paths, home, installed_skill_names)
    if len(parsed_owned) != len(set(parsed_owned)) or set(parsed_owned) != expected_owned:
        raise ValueError("install state owns an unexpected path set")
    if set(hashes) != expected_owned:
        raise ValueError("install state hashes do not match owned paths")
    cleanup = state.get("cleanupBackups", [])
    if not isinstance(cleanup, list):
        raise ValueError("invalid cleanup backup state")
    for entry in cleanup:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ValueError("invalid cleanup backup entry")
        cleanup_path = canonical_owned_path(home, entry["path"])
        relative = entry["path"]
        allowed = re.fullmatch(
            r"(?:\.codex/plugins/\.my-codex-harness|\.agents/skills/\.[A-Za-z0-9][A-Za-z0-9._-]*)"
            r"\.rollback-[0-9TZ]+",
            relative,
        )
        if not allowed or not cleanup_path.exists() or hash_path(cleanup_path) != entry["sha256"]:
            raise ValueError("cleanup backup state drift")
    validate_target(home, paths["package"])
    for name in AGENT_NAMES:
        validate_target(home, paths["agents"] / f"harness-{name}.toml")
    for name in installed_skill_names:
        installed_skill = paths["skills"] / name
        validate_target(home, installed_skill, allow_final_symlink=True)
        if state["mode"] == "symlink":
            expected_link = paths["package"] / "skills" / name
            if not installed_skill.is_symlink() or Path(os.readlink(installed_skill)) != expected_link:
                raise ValueError(f"managed skill symlink drift: {name}")
        elif installed_skill.is_symlink():
            raise ValueError(f"managed copied skill became a symlink: {name}")
    for key, expected in added.items():
        if config_agents.get(key) != expected:
            raise ValueError(f"managed config drift: [agents].{key}")


def build_plan(package_root: Path, home: Path, *, force_copy: bool = False) -> dict:
    package_root = package_root.absolute()
    home = home.absolute()
    validate_home(home)
    if package_root.is_symlink() or not package_root.is_dir():
        raise ValueError("package root must be a regular directory")
    package_version(package_root)
    source_package_hash = hash_package(package_root)
    paths = _paths(home)
    for path in paths.values():
        validate_target(home, path)

    config_raw, config_text = _read_config(paths["config"])
    existing_agents = parse_agents_table(config_text)
    merged_text, _ = merge_agents_table(config_text, DESIRED_AGENTS)
    state, state_raw = _load_state(paths["state"])
    new_skill_names = _skill_names(package_root)
    old_skill_names: tuple[str, ...] = ()

    if state is None:
        if paths["package"].exists() or paths["package"].is_symlink():
            raise ValueError("conflict: canonical package exists without install state")
        for name in AGENT_NAMES:
            destination = paths["agents"] / f"harness-{name}.toml"
            validate_target(home, destination)
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"conflict: {home_relative(home, destination)}")
        for skill in new_skill_names:
            destination = paths["skills"] / skill
            validate_target(home, destination, allow_final_symlink=True)
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"conflict: {home_relative(home, destination)}")
    else:
        old_skill_names = _skill_names(paths["package"])
        _validate_owned_state(home, paths, state, existing_agents, old_skill_names)
        for skill in sorted(set(new_skill_names) - set(old_skill_names)):
            destination = paths["skills"] / skill
            validate_target(home, destination, allow_final_symlink=True)
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"conflict: {home_relative(home, destination)}")

    requested_mode = "copy" if force_copy else None
    current_mode = state.get("mode") if state else None
    current_package_hash = hash_path(paths["package"]) if state else None
    config_unchanged = merged_text == config_text
    idempotent = bool(
        state
        and current_package_hash == source_package_hash
        and config_unchanged
        and (requested_mode is None or current_mode == requested_mode)
    )
    if state and state.get("cleanupBackups") and not idempotent:
        raise ValueError("previous committed cleanup remains pending; resolve it before upgrade")
    added = {key: value for key, value in DESIRED_AGENTS.items() if key not in existing_agents}
    preserved = {key: existing_agents[key] for key in DESIRED_AGENTS if key in existing_agents}
    return {
        "packageRoot": package_root,
        "home": home,
        "paths": paths,
        "version": package_version(package_root),
        "sourceCommit": source_commit(package_root),
        "sourcePackageHash": source_package_hash,
        "oldState": state,
        "oldStateRaw": state_raw,
        "oldConfigRaw": config_raw,
        "mergedConfigRaw": merged_text.encode("utf-8"),
        "addedConfigKeys": added,
        "preservedConfigKeys": preserved,
        "forceCopy": force_copy,
        "idempotent": idempotent,
        "oldSkillNames": old_skill_names,
        "newSkillNames": new_skill_names,
    }


def _plan_signature(plan: dict) -> tuple:
    return (
        plan["sourcePackageHash"],
        plan["oldStateRaw"],
        plan["oldConfigRaw"],
        plan["mergedConfigRaw"],
        plan["forceCopy"],
        plan["idempotent"],
        plan["oldSkillNames"],
        plan["newSkillNames"],
    )


def _revalidate_plan(plan: dict) -> None:
    current = build_plan(plan["packageRoot"], plan["home"], force_copy=plan["forceCopy"])
    if _plan_signature(current) != _plan_signature(plan):
        raise ValueError("installation inputs drifted after preflight")


def _assert_target_unchanged(plan: dict, path: Path) -> None:
    relative = home_relative(plan["home"], path)
    previous = (plan["oldState"] or {}).get("hashes", {}).get(relative)
    exists = path.exists() or path.is_symlink()
    if previous is None:
        if exists:
            raise ValueError(f"target drifted after preflight: {relative}")
    elif not exists or hash_path(path) != previous:
        raise ValueError(f"target drifted after preflight: {relative}")


def _atomic_replace_tree(
    source: Path, destination: Path, backup: Path | None = None
) -> Path | None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        backup = backup or destination.parent / f".{destination.name}.rollback-{_timestamp()}"
        os.replace(destination, backup)
    try:
        os.replace(source, destination)
    except Exception:
        if backup is not None and not destination.exists():
            os.replace(backup, destination)
        raise
    return backup


def _stage_package(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.stage-", dir=destination.parent))
    stage.rmdir()
    try:
        copy_package(source, stage)
    except Exception:
        remove_path(stage)
        raise
    return stage


def _stage_skill(
    source: Path,
    destination: Path,
    mode: str | None,
    symlink_factory: Callable[..., object],
) -> tuple[Path, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".{destination.name}.stage-{_timestamp()}"
    if mode != "copy":
        try:
            symlink_factory(source, stage, target_is_directory=True)
            return stage, "symlink"
        except OSError:
            stage.unlink(missing_ok=True)
            if mode == "symlink":
                raise
    try:
        shutil.copytree(source, stage)
    except Exception:
        remove_path(stage)
        raise
    return stage, "copy"


def _write_journal(path: Path, status: str, actions: list[dict], errors: list[str] | None = None) -> None:
    atomic_write_json(
        path,
        {
            "status": status,
            "actions": actions,
            "rollbackErrors": errors or [],
        },
    )


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _file_hash(content: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(b"file\0")
    digest.update(content)
    return digest.hexdigest()


def _backup_path(path: Path, timestamp: str) -> Path:
    return path.parent / f".{path.name}.rollback-{timestamp}"


def _register_intent(
    path: Path,
    new_hash: str | None,
    undo: list[dict],
    actions: list[dict],
    *,
    backup: Path | None = None,
    old_bytes: bytes | None = None,
    stage: Path | None = None,
    operation: str = "replace",
) -> dict:
    exists = path.exists() or path.is_symlink()
    intent = {
        "path": path,
        "oldHash": hash_path(path) if exists else None,
        "newHash": new_hash,
        "backup": backup,
        "oldBytes": old_bytes,
        "stage": stage,
        "operation": operation,
    }
    undo.append(intent)
    actions.append({"action": f"intent-{operation}", "path": str(path)})
    return intent


def _rollback(undo: list[dict]) -> list[str]:
    errors = []
    for action in reversed(undo):
        path = action["path"]
        try:
            exists = path.exists() or path.is_symlink()
            current_hash = hash_path(path) if exists else None
            if action["newHash"] is not None and current_hash == action["newHash"]:
                remove_path(path)
                exists = False
                current_hash = None
            elif current_hash == action["oldHash"]:
                pass
            elif current_hash is not None:
                errors.append(f"preserved drifted rollback path: {path}")
                continue
            backup = action.get("backup")
            if backup is not None and (backup.exists() or backup.is_symlink()):
                if not exists:
                    os.replace(backup, path)
                elif action["oldHash"] == current_hash and hash_path(backup) == action["oldHash"]:
                    remove_path(backup)
                else:
                    errors.append(f"preserved rollback backup for drifted path: {path}")
            elif not exists and action.get("oldBytes") is not None:
                atomic_write_bytes(path, action["oldBytes"])
            stage = action.get("stage")
            if stage is not None and (stage.exists() or stage.is_symlink()):
                if action["newHash"] is not None and hash_path(stage) == action["newHash"]:
                    remove_path(stage)
                else:
                    errors.append(f"preserved drifted staging path: {stage}")
        except (OSError, ValueError) as error:
            errors.append(f"rollback failed for {path}: {error}")
    return errors


def _finish_committed_install(
    state: dict,
    state_path: Path,
    journal_path: Path,
    actions: list[dict],
    home: Path,
    cleanup_entries: list[dict],
    initial_error: Exception | None = None,
) -> dict:
    remaining = list(cleanup_entries)
    errors = [str(initial_error)] if initial_error is not None else []
    if not errors:
        for entry in list(cleanup_entries):
            path = canonical_owned_path(home, entry["path"])
            try:
                if not path.exists() and not path.is_symlink():
                    raise OSError(f"cleanup backup disappeared: {path}")
                if hash_path(path) != entry["sha256"]:
                    raise OSError(f"cleanup backup drifted: {path}")
                remove_path(path)
                remaining.remove(entry)
                state = {**state, "cleanupBackups": list(remaining)}
                atomic_write_json(state_path, state)
            except Exception as error:
                errors.append(f"cleanup failed for {path}: {error}")
                break
    if errors:
        remaining = [
            entry
            for entry in remaining
            if (home / entry["path"]).exists() or (home / entry["path"]).is_symlink()
        ]
        state = {
            **state,
            "cleanupBackups": remaining,
            "cleanupIncomplete": True,
            "installWarnings": errors,
        }
        try:
            atomic_write_json(state_path, state)
        except Exception as error:
            errors.append(f"state warning update failed: {error}")
            state["installWarnings"] = errors
        try:
            _write_journal(journal_path, "cleanup-incomplete", actions, errors)
        except Exception as error:
            state["installWarnings"].append(f"journal update failed: {error}")
        return state
    state = {
        **state,
        "cleanupBackups": [],
        "cleanupIncomplete": False,
        "installWarnings": [],
    }
    try:
        _write_journal(journal_path, "completed", actions)
    except Exception as error:
        warning = f"journal completion failed: {error}"
        state = {**state, "cleanupIncomplete": True, "installWarnings": [warning]}
        try:
            atomic_write_json(state_path, state)
        except Exception as state_error:
            state["installWarnings"].append(f"state warning update failed: {state_error}")
        try:
            _write_journal(journal_path, "cleanup-incomplete", actions, state["installWarnings"])
        except Exception as journal_error:
            state["installWarnings"].append(f"journal warning update failed: {journal_error}")
    return state


def install_package(
    package_root: Path,
    home: Path,
    *,
    force_copy: bool = False,
    dry_run: bool = False,
    symlink_factory: Callable[..., object] = os.symlink,
    mutation_hook: Callable[[str, Path], None] | None = None,
) -> dict:
    plan = build_plan(package_root, home, force_copy=force_copy)
    if dry_run:
        return {"dryRun": True, "idempotent": plan["idempotent"]}
    if plan["idempotent"]:
        return {**plan["oldState"], "idempotent": True}

    _revalidate_plan(plan)

    paths = plan["paths"]
    timestamp = _timestamp()
    journal_path = paths["journals"] / f"install-{timestamp}.json"
    actions: list[dict] = []
    undo: list[dict] = []
    cleanup_entries: list[dict] = []
    hook = mutation_hook or (lambda _action, _path: None)
    state: dict | None = None
    state_intent: dict | None = None
    committed = False
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    _write_journal(journal_path, "running", actions)

    try:
        package_stage = _stage_package(plan["packageRoot"], paths["package"])
        if hash_path(package_stage) != plan["sourcePackageHash"]:
            remove_path(package_stage)
            raise ValueError("package source drifted after preflight")
        _assert_target_unchanged(plan, paths["package"])
        package_backup = (
            _backup_path(paths["package"], timestamp)
            if paths["package"].exists() or paths["package"].is_symlink()
            else None
        )
        package_intent = _register_intent(
            paths["package"],
            hash_path(package_stage),
            undo,
            actions,
            backup=package_backup,
            stage=package_stage,
        )
        _write_journal(journal_path, "running", actions)
        _atomic_replace_tree(package_stage, paths["package"], package_backup)
        if package_backup is not None:
            cleanup_entries.append(
                {
                    "path": home_relative(home, package_backup),
                    "sha256": package_intent["oldHash"],
                }
            )
        hook("package-installed", paths["package"])

        for name in AGENT_NAMES:
            source = paths["package"] / f"agents/harness-{name}.toml"
            destination = paths["agents"] / f"harness-{name}.toml"
            _assert_target_unchanged(plan, destination)
            old = destination.read_bytes() if destination.exists() else None
            content = source.read_bytes()
            _register_intent(
                destination,
                _file_hash(content),
                undo,
                actions,
                old_bytes=old,
            )
            _write_journal(journal_path, "running", actions)
            atomic_write_bytes(destination, content)
            hook("agent-installed", destination)

        selected_mode = (
            "copy"
            if plan["forceCopy"]
            else ((plan["oldState"] or {}).get("mode") if plan["oldState"] else None)
        )
        removed_skills = sorted(set(plan["oldSkillNames"]) - set(plan["newSkillNames"]))
        for name in removed_skills:
            destination = paths["skills"] / name
            _assert_target_unchanged(plan, destination)
            backup = _backup_path(destination, timestamp)
            intent = _register_intent(
                destination,
                None,
                undo,
                actions,
                backup=backup,
                operation="remove",
            )
            _write_journal(journal_path, "running", actions)
            os.replace(destination, backup)
            cleanup_entries.append(
                {"path": home_relative(home, backup), "sha256": intent["oldHash"]}
            )
            hook("skill-removed", destination)

        for name in plan["newSkillNames"]:
            source = paths["package"] / "skills" / name
            destination = paths["skills"] / name
            _assert_target_unchanged(plan, destination)
            stage, actual_mode = _stage_skill(source, destination, selected_mode, symlink_factory)
            if selected_mode is None:
                selected_mode = actual_mode
            backup = (
                _backup_path(destination, timestamp)
                if destination.exists() or destination.is_symlink()
                else None
            )
            intent = _register_intent(
                destination,
                hash_path(stage),
                undo,
                actions,
                backup=backup,
                stage=stage,
            )
            _write_journal(journal_path, "running", actions)
            _atomic_replace_tree(stage, destination, backup)
            if backup is not None:
                cleanup_entries.append(
                    {"path": home_relative(home, backup), "sha256": intent["oldHash"]}
                )
            hook("skill-installed", destination)

        backups = list((plan["oldState"] or {}).get("backups", []))
        if plan["oldConfigRaw"] != plan["mergedConfigRaw"]:
            current_config = paths["config"].read_bytes() if paths["config"].exists() else None
            if current_config != plan["oldConfigRaw"]:
                raise ValueError("config.toml drifted after preflight")
            config_backup = None
            if plan["oldConfigRaw"] is not None:
                config_backup = paths["backups"] / f"config.toml.backup-{timestamp}"
                _register_intent(
                    config_backup,
                    _file_hash(plan["oldConfigRaw"]),
                    undo,
                    actions,
                    operation="backup",
                )
                _write_journal(journal_path, "running", actions)
                atomic_write_bytes(config_backup, plan["oldConfigRaw"])
                backups.append(
                    {
                        "path": home_relative(home, config_backup),
                        "sha256": hash_path(config_backup),
                    }
                )
            _register_intent(
                paths["config"],
                _file_hash(plan["mergedConfigRaw"]),
                undo,
                actions,
                old_bytes=plan["oldConfigRaw"],
            )
            _write_journal(journal_path, "running", actions)
            atomic_write_bytes(paths["config"], plan["mergedConfigRaw"])
            hook("config-written", paths["config"])

        owned_paths = sorted(_expected_owned(paths, home, plan["newSkillNames"]))
        hashes = {relative: hash_path(home / relative) for relative in owned_paths}
        prior_added = dict((plan["oldState"] or {}).get("addedConfigKeys", {}))
        prior_added.update(plan["addedConfigKeys"])
        existed_before = plan["oldState"] is not None
        old_owned = set((plan["oldState"] or {}).get("ownedPaths", []))
        created_paths = sorted(set(owned_paths) - old_owned)
        replaced_paths = sorted(set(owned_paths) & old_owned)
        if not existed_before:
            created_paths = list(owned_paths)
            replaced_paths = []
        if plan["oldConfigRaw"] != plan["mergedConfigRaw"]:
            (replaced_paths if plan["oldConfigRaw"] is not None else created_paths).append(
                home_relative(home, paths["config"])
            )
        state = {
            "version": plan["version"],
            "sourceCommit": plan["sourceCommit"],
            "mode": selected_mode or "copy",
            "hashes": hashes,
            "ownedPaths": owned_paths,
            "createdPaths": created_paths,
            "replacedPaths": replaced_paths,
            "backups": backups,
            "addedConfigKeys": prior_added,
            "preservedConfigKeys": plan["preservedConfigKeys"],
            "lastJournal": home_relative(home, journal_path),
            "cleanupBackups": list(cleanup_entries),
            "cleanupIncomplete": False,
            "installWarnings": [],
        }
        old_state_bytes = plan["oldStateRaw"]
        current_state = paths["state"].read_bytes() if paths["state"].exists() else None
        if current_state != old_state_bytes:
            raise ValueError("install state drifted after preflight")
        state_bytes = _json_bytes(state)
        state_intent = _register_intent(
            paths["state"],
            _file_hash(state_bytes),
            undo,
            actions,
            old_bytes=old_state_bytes,
            operation="state",
        )
        _write_journal(journal_path, "running", actions)
        atomic_write_json(paths["state"], state)
        committed = True
        _write_journal(journal_path, "committed", actions)
        hook("state-written", paths["state"])
    except Exception as error:
        if state_intent is not None and paths["state"].exists():
            committed = committed or hash_path(paths["state"]) == state_intent["newHash"]
        if committed and state is not None:
            return _finish_committed_install(
                state,
                paths["state"],
                journal_path,
                actions,
                home,
                cleanup_entries,
                error,
            )
        rollback_errors = _rollback(undo)
        _write_journal(journal_path, "rollback-incomplete" if rollback_errors else "rolled-back", actions, rollback_errors)
        if rollback_errors:
            raise RuntimeError(f"{error}; " + "; ".join(rollback_errors)) from error
        raise
    return _finish_committed_install(
        state,
        paths["state"],
        journal_path,
        actions,
        home,
        cleanup_entries,
    )


def symlinks_supported(directory: Path) -> bool:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        root = Path(temporary)
        target = root / "target"
        link = root / "link"
        target.mkdir()
        try:
            link.symlink_to(target, target_is_directory=True)
            return link.is_symlink()
        except OSError:
            return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="preview without writing")
    parser.add_argument("--yes", action="store_true", help="confirm installation")
    parser.add_argument("--copy", action="store_true", help="copy skills instead of linking")
    args = parser.parse_args(argv)
    if not args.dry_run and not args.yes:
        parser.error("use --dry-run to preview or --yes to install")
    home = Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or str(Path.home()))
    try:
        result = install_package(ROOT, home, force_copy=args.copy, dry_run=args.dry_run)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"install failed: {error}", file=sys.stderr)
        return 1
    if args.dry_run:
        print("already installed; no changes" if result["idempotent"] else "would install My Codex Harness")
    elif result.get("idempotent"):
        print("My Codex Harness already installed; no changes")
    else:
        print(f"installed My Codex Harness {result['version']} ({result['mode']} mode)")
    if result.get("cleanupIncomplete") or result.get("cleanupBackups"):
        warnings = result.get("installWarnings") or ["committed cleanup remains pending"]
        print(
            "install committed with cleanup warning: " + "; ".join(warnings),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
