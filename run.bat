@echo off
REM GOAT 2.0 — Entry point (Windows)
REM Usage: run.bat

setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM ── Python check ──────────────────────────────────────────────────────────
set PYTHON=
for %%P in (python3.12 python3.11 python3 python) do (
    where %%P >nul 2>&1
    if !errorlevel! == 0 (
        for /f %%V in ('%%P -c "import sys; print(sys.version_info >= (3,11))" 2^>nul') do (
            if "%%V"=="True" (
                set PYTHON=%%P
                goto :found_python
            )
        )
    )
)

echo.
echo   ERROR: Python 3.11+ not found.
echo   Install it from https://python.org and try again.
echo.
pause
exit /b 1

:found_python

REM ── Virtual environment ────────────────────────────────────────────────────
if not exist ".venv\" (
    echo Creating virtual environment...
    %PYTHON% -m venv .venv
)

call .venv\Scripts\activate.bat

REM ── Setup wizard dependencies ──────────────────────────────────────────────
pip install -q -r setup\requirements.txt

REM ── Pre-flight checks ──────────────────────────────────────────────────────
python setup\checks.py
if errorlevel 1 (
    echo.
    echo   Fix the issues above and run run.bat again.
    echo.
    pause
    exit /b 1
)

REM ── First-run wizard ───────────────────────────────────────────────────────
if not exist "goat2.toml" (
    echo.
    echo   First run detected - launching setup wizard...
    echo.
    python setup\wizard.py
)
if not exist ".env" (
    echo.
    echo   First run detected - launching setup wizard...
    echo.
    python setup\wizard.py
)

REM ── Install main dependencies ──────────────────────────────────────────────
pip install -q -r requirements.txt

REM ── Update check ──────────────────────────────────────────────────────────
python setup\updater.py --check 2>nul

REM ── Start GOAT ────────────────────────────────────────────────────────────
echo.
echo   Starting GOAT 2.0...
echo.
python -m telegram_interface
