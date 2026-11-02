@echo off
REM Starts the MiniCPM service on port 9130 (syncs .venv with uv on each run). Needs Ollama running with the model:
REM   ollama pull hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M
REM LABEL: MiniCPM Service
REM DESCRIPTION: Shared HTTP service that serves MiniCPM5-2B (via Ollama) for every project.

cd /d "%~dp0"

python -m uv sync
if errorlevel 1 (
    echo uv sync failed. Install uv: python -m pip install uv
    pause
    exit /b 1
)
call .venv\Scripts\activate

python src/app.py
pause
