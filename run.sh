#!/usr/bin/env bash
# GOAT 2.0 — Entry point (Linux / macOS)
# Usage: ./run.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Python check (before venv, before anything else) ──────────────────────────
PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        VER=$("$candidate" -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null)
        if [ "$VER" = "True" ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo "  ERROR: Python 3.11+ not found."
    echo "  Install it from https://python.org and try again."
    echo ""
    exit 1
fi

# ── Virtual environment ───────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# ── Setup wizard dependencies ─────────────────────────────────────────────────
pip install -q -r setup/requirements.txt

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if ! python3 setup/checks.py; then
    echo ""
    echo "  Fix the issues above and run ./run.sh again."
    echo ""
    exit 1
fi

# ── First-run wizard ──────────────────────────────────────────────────────────
if [ ! -f "goat2.toml" ] || [ ! -f ".env" ]; then
    echo ""
    echo "  First run detected — launching setup wizard..."
    echo ""
    python3 setup/wizard.py
fi

# ── Install main dependencies ─────────────────────────────────────────────────
pip install -q -r requirements.txt

# ── Update check (non-blocking, skips on failure) ─────────────────────────────
python3 setup/updater.py --check 2>/dev/null || true

# ── Start GOAT ────────────────────────────────────────────────────────────────
echo ""
echo "  Starting GOAT 2.0..."
echo ""
exec python3 -m telegram_interface
