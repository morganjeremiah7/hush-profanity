@echo off
REM Simple batch wrapper to open a file in hush-profanity's manual editor
REM Usage: open-in-editor.bat "C:\path\to\video.mp4"

setlocal enabledelayedexpansion

if "%~1"=="" (
    echo Usage: open-in-editor.bat "filepath"
    exit /b 1
)

REM Get the full path of this script directory
set SCRIPT_DIR=%~dp0

REM Build the URL with the file path (basic URL encoding for spaces)
set FILE_PATH=%~1
set URL=http://127.0.0.1:8765/watch?path=!FILE_PATH!

REM Try to open the URL in the default browser
start !URL!

exit /b 0
