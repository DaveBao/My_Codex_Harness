#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

python3 "$SCRIPT_DIR/doctor.py"
python3 "$SCRIPT_DIR/install.py" --dry-run
python3 "$SCRIPT_DIR/install.py" --yes
