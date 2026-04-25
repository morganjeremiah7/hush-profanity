@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
    echo .venv not found. Run windows\install.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m hush_profanity scan %*
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
    echo Done.
) else (
    echo Scan exited with errors. See logs\ for details.
)
pause
exit /b %RC%
