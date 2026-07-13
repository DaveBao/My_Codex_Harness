#!/usr/bin/env python3
"""Safely remove only My Codex Harness-owned installation paths."""

import argparse
import datetime as dt
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
    plan = build_plan(home)
    if plan is None:
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
    actions = []
    moved: list[tuple[Path, Path]] = []
    config_written = False
    committed = False
    durable_backup = None
    state_backup = plan["statePath"].parent / f".install-state.rollback-{timestamp}.json"
    atomic_write_json(journal, {"status": "running", "actions": actions})
    try:
        for path in sorted(plan["ownedPaths"], key=lambda item: len(item.parts), reverse=True):
            relative = path.relative_to(plan["home"]).as_posix()
            if hash_path(path) != plan["removalHashes"][relative]:
                raise ValueError(f"managed path drift after preflight: {relative}")
            backup = path.parent / f".{path.name}.uninstall-{timestamp}"
            moved.append((path, backup))
            actions.append({"action": "intent-remove", "path": str(path)})
            atomic_write_json(journal, {"status": "running", "actions": actions})
            os.replace(path, backup)
        if plan["configRaw"] != plan["newConfigRaw"]:
            if plan["configPath"].read_bytes() != plan["configRaw"]:
                raise ValueError("config.toml drifted after preflight")
            durable_backup = plan["statePath"].parent / "backups" / f"config.toml.uninstall-{timestamp}"
            atomic_write_bytes(durable_backup, plan["configRaw"])
            actions.append({"action": "backup", "path": str(durable_backup)})
            atomic_write_json(journal, {"status": "running", "actions": actions})
            config_written = True
            actions.append({"action": "intent-config", "path": str(plan["configPath"])})
            atomic_write_json(journal, {"status": "running", "actions": actions})
            atomic_write_bytes(plan["configPath"], plan["newConfigRaw"])
        if plan["statePath"].read_bytes() != plan["stateRaw"]:
            raise ValueError("install state drifted after preflight")
        os.replace(plan["statePath"], state_backup)
        actions.append({"action": "remove", "path": str(plan["statePath"])})
        atomic_write_json(journal, {"status": "completed", "actions": actions})
        committed = True
        for _, backup in moved:
            remove_path(backup)
        state_backup.unlink()
        return {"removed": len(moved)}
    except Exception as error:
        if committed:
            atomic_write_json(
                journal,
                {
                    "status": "cleanup-incomplete",
                    "actions": actions,
                    "rollbackErrors": [f"post-commit cleanup failed: {error}"],
                },
            )
            raise RuntimeError(f"uninstall committed but cleanup failed: {error}") from error
        rollback_errors = []
        if state_backup.exists() and not plan["statePath"].exists():
            os.replace(state_backup, plan["statePath"])
        elif state_backup.exists():
            rollback_errors.append(f"preserved rollback state backup: {state_backup}")
        if config_written:
            current_config = plan["configPath"].read_bytes()
            if current_config == plan["newConfigRaw"]:
                atomic_write_bytes(plan["configPath"], plan["configRaw"])
            elif current_config != plan["configRaw"]:
                rollback_errors.append(f"preserved drifted config: {plan['configPath']}")
        for path, backup in reversed(moved):
            if not path.exists() and backup.exists():
                os.replace(backup, path)
            elif backup.exists():
                rollback_errors.append(f"preserved rollback backup for drifted path: {path}")
        if durable_backup is not None and durable_backup.exists():
            if hash_path(durable_backup) == hash_path(plan["configPath"]):
                durable_backup.unlink()
            else:
                rollback_errors.append(f"preserved drifted uninstall backup: {durable_backup}")
        atomic_write_json(
            journal,
            {
                "status": "rollback-incomplete" if rollback_errors else "rolled-back",
                "actions": actions,
                "rollbackErrors": rollback_errors,
            },
        )
        if rollback_errors:
            raise RuntimeError(f"{error}; " + "; ".join(rollback_errors)) from error
        raise


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
        print(f"would remove {result['ownedPaths']} owned paths")
    else:
        print(f"uninstalled My Codex Harness ({result['removed']} owned paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
