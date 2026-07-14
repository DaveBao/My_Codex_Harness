#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

is_qualified_python() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        >/dev/null 2>&1
}

PYTHON=
if [ "${HARNESS_PYTHON+x}" = x ]; then
    if [ -z "$HARNESS_PYTHON" ] || ! is_qualified_python "$HARNESS_PYTHON"; then
        echo "bootstrap failed: HARNESS_PYTHON must name a Python 3.11 or newer interpreter" >&2
        exit 1
    fi
    PYTHON=$HARNESS_PYTHON
else
    for name in python3.13 python3.12 python3.11 python3; do
        candidate=$(command -v "$name" 2>/dev/null || :)
        if [ -n "$candidate" ] && is_qualified_python "$candidate"; then
            PYTHON=$candidate
            break
        fi
    done
    if [ -z "$PYTHON" ] && [ -n "${HOME:-}" ]; then
        for candidate in "$HOME"/.cache/codex-runtimes/*/dependencies/python/bin/python3; do
            if [ -x "$candidate" ] && is_qualified_python "$candidate"; then
                PYTHON=$candidate
                break
            fi
        done
    fi
fi

if [ -z "$PYTHON" ]; then
    echo "bootstrap failed: Python 3.11 or newer was not found; install it or set HARNESS_PYTHON" >&2
    exit 1
fi

"$PYTHON" "$SCRIPT_DIR/doctor.py"
"$PYTHON" "$SCRIPT_DIR/install.py" --dry-run
"$PYTHON" "$SCRIPT_DIR/install.py" --yes
