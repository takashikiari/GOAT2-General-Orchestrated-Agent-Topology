#!/usr/bin/env bash
set -e

echo "============================================"
echo " Welcome to GOAT - Multi-Agent Supervisor"
echo "============================================"
echo ""

# Detect Python
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python is not installed or not in PATH."
    echo "Please install Python 3.10+ from https://python.org"
    exit 1
fi

echo "Using: $($PYTHON --version)"

# Check dependencies
if [ ! -f "requirements.txt" ]; then
    echo "[WARN] requirements.txt not found. Skipping dependency check."
else
    echo "Checking dependencies..."
    $PYTHON -m pip install -r requirements.txt -q 2>/dev/null && echo "Dependencies OK." || echo "[WARN] Some deps may be missing."
fi

echo ""
echo "Starting GOAT..."
echo ""
$PYTHON main.py
