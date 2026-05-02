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
echo Testing URL...
set "TEST_FILE=C:\test.mp4"
echo Would open: http://127.0.0.1:8765/watch?path=%TEST_FILE%

echo.
echo Opening URL in default browser...
start "" "http://127.0.0.1:8765/watch?path=%TEST_FILE%"

echo.
echo Done! Browser should have opened (or shown error if Flask isn't running).
pause
