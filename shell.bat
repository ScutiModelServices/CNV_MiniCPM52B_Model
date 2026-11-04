@echo off
REM Opens a shell with this project's .venv already activated.
cd /d "%~dp0"
if not exist .venv\Scriptsctivate.bat (
    echo .venv missing. Run: uv sync
    pause
    exit /b 1
)
cmd /k ".venv\Scriptsctivate.bat"
