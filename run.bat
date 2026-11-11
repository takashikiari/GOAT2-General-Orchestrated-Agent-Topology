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

REM ── Setup wizard + main dependencies ───────────────────────────────────────
REM Installed BEFORE the checks: ChromaDB is a required check and comes from requirements.txt.
pip install -q -r setup\requirements.txt
pip install -q -r requirements.txt

REM ── Setup wizard: FIRST, on the first run or with --reconfigure ───────────
set RUN_WIZARD=0
if not exist "goat2.toml" set RUN_WIZARD=1
if not exist ".env" set RUN_WIZARD=1
if "%~1"=="--reconfigure" (
    python setup\wizard.py --reconfigure
    if errorlevel 1 exit /b 1
) else if !RUN_WIZARD!==1 (
    echo.
    echo   First run detected - launching setup wizard...
    echo.
    python setup\wizard.py
    if errorlevel 1 exit /b 1
)

REM ── Pre-flight checks (Redis and ChromaDB still there?) ────────────────────
python setup\checks.py
if errorlevel 1 (
    echo.
    echo   Fix the issues above (or run run.bat --reconfigure), then run run.bat again.
    echo.
    pause
    exit /b 1
)

REM ── Update check ──────────────────────────────────────────────────────────
python setup\updater.py --check 2>nul

REM ── Start GOAT ────────────────────────────────────────────────────────────
echo.
echo   Starting GOAT 2.0...
echo.
python -m telegram_interface
