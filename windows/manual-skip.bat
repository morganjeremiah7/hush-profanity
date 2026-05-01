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
REM Fire-and-forget a 2s delayed browser-open, so Flask has time to bind the port.
REM Without this, the browser races the server and may show "site can't be reached".
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8765/'"
".venv\Scripts\python.exe" -m hush_profanity.webui.server %*
