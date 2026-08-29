@echo off
setlocal
set "ROOT=%~dp0"
set "PY=py"

rem Prefer a local venv if one exists
if exist "%ROOT%venv\Scripts\python.exe" (
    set "PY=%ROOT%venv\Scripts\python.exe"
) else (
    py -3.12 -c "import sys" >nul 2>&1 || py -3.11 -c "import sys" >nul 2>&1 || (
        echo [eLmi] No local venv and no Python launcher found.
        echo [eLmi] Run:  py -3.12 -m venv venv ^&^& venv\Scripts\pip install -r agent\requirements.txt
        pause
        exit /b 1
    )
)

cd /d "%ROOT%agent"
"%PY%" gui.py --allow-exec
