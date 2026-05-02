@echo off
setlocal enabledelayedexpansion

if "%~1"=="" exit /b 1

set FILE_PATH=%~1

REM Check if Flask is running on port 8765
netstat -ano 2>nul | findstr ":8765" >nul
if errorlevel 1 (
    REM Flask is not running, start it
    set SCRIPT_DIR=%~dp0
    start /b "" "!SCRIPT_DIR!manual-skip.bat"
    timeout /t 3 /nobreak >nul
)

REM Open the editor in the default browser
set URL=http://127.0.0.1:8765/watch?path=!FILE_PATH!
start "" !URL!

exit /b 0
