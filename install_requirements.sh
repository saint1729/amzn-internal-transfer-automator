#!/usr/bin/env bash
# Create a .venv virtual environment (if missing) and install requirements into it
set -euo pipefail

VENV_DIR=".venv"
PYTHON_CMD="${PYTHON:-python3}"

if [ ! -d "$VENV_DIR" ]; then
		echo "Creating virtual environment in $VENV_DIR..."
		$PYTHON_CMD -m venv "$VENV_DIR"
fi

echo "Upgrading pip inside virtualenv and installing requirements..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

cat <<EOF
Virtual environment created at $VENV_DIR and requirements installed.

To start using it (macOS / Linux):
	source $VENV_DIR/bin/activate
	python send_example.py

To start using it (fish shell):
	source $VENV_DIR/bin/activate.fish

To deactivate:
	deactivate

If you prefer to run a command without activating, prefix with the venv python:
	$VENV_DIR/bin/python send_example.py

EOF
