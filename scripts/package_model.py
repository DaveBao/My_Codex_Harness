"""Small, dependency-free helpers shared by package scripts."""

import hashlib as _hashlib
import json as _json
import os as _os
import re as _re
import tempfile as _tempfile
from pathlib import Path


__all__ = [
    "sha256_file",
    "safe_relative_path",
    "load_json",
    "atomic_write",
    "parse_agents_table",
    "merge_agents_table",
]

_BARE_KEY = _re.compile(r"^[A-Za-z0-9_-]+$")
_DECIMAL_INTEGER = _re.compile(r"^[+-]?(?:0|[1-9](?:_?[0-9])*)$")
_HEX_INTEGER = _re.compile(r"^0x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*$")
_AGENTS_HEADER = _re.compile(
    r"^\[{1,2}\s*(?:agents|['\"]agents['\"])(?:\s*[.\]])"
)
_SUPPORTED_VALUES = "[agents] supports only single-line strings, integers, and booleans"


def sha256_file(path: Path) -> str:
    digest = _hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(root: Path, candidate: Path) -> str:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        return resolved_candidate.relative_to(resolved_root).as_posix()
    except ValueError as error:
        raise ValueError(f"path escapes root: {candidate}") from error


def load_json(path: Path) -> dict:
    value = _json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def atomic_write(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with _tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            _os.fsync(stream.fileno())
        _os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_agents_table(config_text: str) -> dict[str, object]:
    agents, _ = _scan_agents(config_text)
    return agents


def merge_agents_table(
    config_text: str, desired: dict[str, object]
) -> tuple[str, dict[str, object]]:
    existing, header_index = _scan_agents(config_text)
    additions = {key: value for key, value in desired.items() if key not in existing}
    entries = "".join(
        f"{_format_key(key)} = {_format_scalar(value)}\n"
        for key, value in sorted(additions.items())
    )
    merged = {**existing, **additions}
    if not entries:
        return config_text, merged

    if header_index is None:
        if not config_text:
            separator = ""
        elif config_text.endswith("\n\n"):
            separator = ""
        elif config_text.endswith("\n"):
            separator = "\n"
        else:
            separator = "\n\n"
        return f"{config_text}{separator}[agents]\n{entries}", merged

    lines = config_text.splitlines(keepends=True)
    insertion_index = next(
        (
            index
            for index in range(header_index + 1, len(lines))
            if _strip_comment(lines[index]).strip().startswith("[")
        ),
        len(lines),
    )
    while insertion_index > header_index + 1 and not lines[insertion_index - 1].strip():
        insertion_index -= 1
    prefix = ""
    if insertion_index and not lines[insertion_index - 1].endswith(("\n", "\r")):
        prefix = "\n"
    lines.insert(insertion_index, prefix + entries)
    return "".join(lines), merged


def _scan_agents(config_text: str) -> tuple[dict[str, object], int | None]:
    agents: dict[str, object] = {}
    header_index = None
    in_agents = False
    at_root = True
    multiline_delimiter = None

    for index, raw_line in enumerate(config_text.splitlines(keepends=True)):
        if multiline_delimiter is not None:
            if _has_multiline_close(raw_line, multiline_delimiter):
                multiline_delimiter = None
            continue
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("["):
            if line == "[agents]":
                if header_index is not None:
                    raise ValueError("duplicate [agents] table")
                header_index = index
                in_agents = True
            else:
                if _AGENTS_HEADER.match(line):
                    raise ValueError("unsupported agents layout: use one direct [agents] table")
                in_agents = False
            at_root = False
            continue

        if in_agents:
            key_text, value_text = _split_assignment(line)
            key = _parse_key(key_text)
            if key in agents:
                raise ValueError(f"duplicate key in [agents]: {key}")
            agents[key] = _parse_scalar(value_text)
        elif at_root:
            try:
                key_text, value_text = _split_assignment(line)
            except ValueError:
                continue
            if _is_agents_key(key_text):
                raise ValueError("unsupported agents layout: use one direct [agents] table")
            multiline_delimiter = _opening_multiline_delimiter(value_text)
        else:
            try:
                _, value_text = _split_assignment(line)
            except ValueError:
                continue
            multiline_delimiter = _opening_multiline_delimiter(value_text)

    if multiline_delimiter is not None:
        raise ValueError("unterminated TOML multiline string")
    return agents, header_index


def _strip_comment(line: str) -> str:
    quote = None
    escaped = False
    for index, character in enumerate(line):
        if quote == '"' and character == "\\" and not escaped:
            escaped = True
            continue
        if character in ("'", '"') and not escaped:
            quote = None if quote == character else (character if quote is None else quote)
        elif character == "#" and quote is None:
            return line[:index]
        escaped = False
    return line


def _split_assignment(line: str) -> tuple[str, str]:
    quote = None
    escaped = False
    for index, character in enumerate(line):
        if quote == '"' and character == "\\" and not escaped:
            escaped = True
            continue
        if character in ("'", '"') and not escaped:
            quote = None if quote == character else (character if quote is None else quote)
        elif character == "=" and quote is None:
            return line[:index].strip(), line[index + 1 :].strip()
        escaped = False
    raise ValueError(f"expected assignment in line: {line}")


def _is_agents_key(key: str) -> bool:
    stripped = key.strip()
    return stripped == "agents" or stripped in ('"agents"', "'agents'") or stripped.startswith("agents.")


def _opening_multiline_delimiter(value: str) -> str | None:
    for delimiter in ('"""', "'''"):
        if value.startswith(delimiter) and not _has_multiline_close(value[len(delimiter) :], delimiter):
            return delimiter
    return None


def _has_multiline_close(text: str, delimiter: str) -> bool:
    start = 0
    while True:
        index = text.find(delimiter, start)
        if index < 0:
            return False
        if delimiter == "'''":
            return True
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and text[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return True
        start = index + 1


def _parse_key(key: str) -> str:
    if _BARE_KEY.fullmatch(key):
        return key
    value = _parse_scalar(key)
    if not isinstance(value, str):
        raise ValueError(f"invalid TOML key: {key}")
    return value


def _parse_scalar(value: str) -> object:
    if value.startswith('"'):
        try:
            parsed = _json.loads(value)
        except (TypeError, ValueError) as error:
            raise ValueError(_SUPPORTED_VALUES) from error
        if isinstance(parsed, str):
            return parsed
    elif value.startswith("'") and value.endswith("'") and "'" not in value[1:-1]:
        return value[1:-1]
    elif value == "true":
        return True
    elif value == "false":
        return False
    elif _HEX_INTEGER.fullmatch(value):
        return int(value.replace("_", ""), 16)
    elif _DECIMAL_INTEGER.fullmatch(value):
        return int(value.replace("_", ""), 10)
    raise ValueError(_SUPPORTED_VALUES)


def _format_key(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError("agent names must be strings")
    return key if _BARE_KEY.fullmatch(key) else _json.dumps(key, ensure_ascii=False)


def _format_scalar(value: object) -> str:
    if isinstance(value, str):
        return _json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    raise ValueError("agent values must be strings, integers, or booleans")
