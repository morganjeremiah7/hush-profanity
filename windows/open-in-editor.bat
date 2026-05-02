@echo off
REM Context menu launcher for hush-profanity editor

if "%~1"=="" exit /b 1

REM Get the file path from argument
set "FILE_PATH=%~1"

REM Check if Flask is running on port 8765
netstat -ano 2>nul | findstr ":8765" >nul
if errorlevel 1 (
    REM Flask is not running, start it
    start /b "" "%~dp0manual-skip.bat"
    timeout /t 3 /nobreak >nul
)

REM Open the editor in the default browser
REM Note: The URL is passed directly to start without variable expansion
start "" "http://127.0.0.1:8765/watch?path=%FILE_PATH%"

exit /b 0
