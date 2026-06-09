@echo off
title GOAT - Onboarding
echo ============================================
echo  Welcome to GOAT - Multi-Agent Supervisor
echo ============================================
echo.
echo Checking Python installation...
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

echo Checking dependencies...
if not exist "requirements.txt" (
    echo [WARN] requirements.txt not found. Skipping dependency check.
) else (
    pip install -r requirements.txt >nul 2>nul
    if %ERRORLEVEL% neq 0 (
        echo [WARN] Some dependencies may be missing. Attempting to continue...
    ) else (
        echo Dependencies OK.
    )
)

echo.
echo Starting GOAT...
echo.
python main.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] GOAT exited with error code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)

pause
