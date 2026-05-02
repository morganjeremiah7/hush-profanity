@echo off
REM Context menu launcher for hush-profanity editor
REM This batch file assumes Flask is already running on http://127.0.0.1:8765
REM If Flask is not running, you'll get a "site can't be reached" error - just start it first

setlocal enabledelayedexpansion

REM Get the file path from the first argument
if "%~1"=="" exit /b 1

set FILE_PATH=%~1

REM Build the URL - spaces in paths are OK, browser will handle them
set URL=http://127.0.0.1:8765/watch?path=!FILE_PATH!

REM Launch browser asynchronously (returns immediately, never blocks Explorer)
REM The key is using 'start ""' which creates a detached process
start "" !URL!

REM Exit immediately - this lets Explorer know the context menu handler is done
exit /b 0
