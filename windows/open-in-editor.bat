@echo off
REM Context menu launcher for hush-profanity editor
REM Auto-starts Flask if it's not already running, then opens the file in the editor

setlocal enabledelayedexpansion

if "%~1"=="" exit /b 1

set FILE_PATH=%~1

REM Check if Flask is running by testing port 8765
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765 "') do (
    set FLASK_PID=%%a
)

REM If Flask is not running, start it
if not defined FLASK_PID (
    echo Starting Flask...
    set SCRIPT_DIR=%~dp0
    set PROJECT_ROOT=!SCRIPT_DIR!..
    start /b "" "!SCRIPT_DIR!manual-skip.bat"
    timeout /t 2 /nobreak
)

REM Build the URL and open it
set URL=http://127.0.0.1:8765/watch?path=!FILE_PATH!
start "" !URL!

exit /b 0
