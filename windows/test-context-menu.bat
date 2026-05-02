@echo off
echo Testing context menu setup...
echo.

echo Checking if Flask is running...
netstat -ano 2>nul | findstr ":8765" >nul
if errorlevel 1 (
    echo Flask is NOT running - would auto-start
) else (
    echo Flask IS running on port 8765
)

echo.
echo Testing with a dummy file path...
set TEST_FILE=C:\test.mp4
set URL=http://127.0.0.1:8765/watch?path=!TEST_FILE!
echo Would open URL: !URL!

echo.
echo Opening URL in default browser...
start "" !URL!

pause
