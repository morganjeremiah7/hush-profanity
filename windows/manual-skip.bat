@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
    echo .venv not found. Run windows\install.bat first.
    pause
    exit /b 1
)
echo Starting manual EDL editor at http://127.0.0.1:8765/
echo Press Ctrl+C to stop.
start "" "http://127.0.0.1:8765/"
".venv\Scripts\python.exe" -m hush_profanity.webui.server %*
