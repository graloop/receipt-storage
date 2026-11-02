#!/usr/bin/env bash
# Local runner (`npm start`): sets up the Python virtualenv, installs the
# dependencies, makes a .env on first run and starts the app with auto-reload
# on http://127.0.0.1:8000. Only this computer can reach it.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# The newest Python 3.11+ on PATH: macOS's own python3 is 3.9, too old for the
# dependencies, so prefer a versioned one (e.g. Homebrew's python3.14).
PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Python 3.11+ is required but was not found on PATH (python3 is $(python3 --version 2>&1))." >&2
  echo "Install one, e.g.: brew install python@3.14" >&2
  exit 1
fi

if [ ! -d backend/.venv ]; then
  echo "Creating Python virtualenv at backend/.venv with $($PYTHON --version) ..."
  "$PYTHON" -m venv backend/.venv
fi
backend/.venv/bin/pip install -q --disable-pip-version-check -r backend/requirements-dev.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env."
fi

echo "Starting Receipt Vault at http://127.0.0.1:8000"
cd backend
exec .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
