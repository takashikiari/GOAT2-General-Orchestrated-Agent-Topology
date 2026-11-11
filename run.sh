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

# ── Setup wizard + main dependencies ──────────────────────────────────────────
# Installed BEFORE the checks: ChromaDB is a required check and comes from requirements.txt, so on a fresh
# machine checking first would stop the very first run.
pip install -q -r setup/requirements.txt
pip install -q -r requirements.txt

# ── Setup wizard: FIRST, on the first run or with --reconfigure ──────────────
# It checks every service as a step (with retry), so it runs before the gate below.
if [ "${1:-}" = "--reconfigure" ]; then
    python3 setup/wizard.py --reconfigure || exit 1
elif [ ! -f "goat2.toml" ] || [ ! -f ".env" ]; then
    echo ""
    echo "  First run detected — launching setup wizard..."
    echo ""
    python3 setup/wizard.py || exit 1
fi

# ── Pre-flight checks (Redis and ChromaDB still there?) ───────────────────────
if ! python3 setup/checks.py; then
    echo ""
    echo "  Fix the issues above (or run ./run.sh --reconfigure), then run ./run.sh again."
    echo ""
    exit 1
fi

# ── Update check (non-blocking, skips on failure) ─────────────────────────────
python3 setup/updater.py --check 2>/dev/null || true

# ── Start GOAT ────────────────────────────────────────────────────────────────
echo ""
echo "  Starting GOAT 2.0..."
echo ""
exec python3 -m telegram_interface
