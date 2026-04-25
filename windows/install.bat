@echo off
setlocal
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install-windows.ps1" %*
if errorlevel 1 (
    echo.
    echo Install failed. See the messages above.
    pause
    exit /b 1
)
pause
