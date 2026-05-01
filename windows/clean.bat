@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
    echo .venv not found. Run windows\install.bat first.
    pause
    exit /b 1
)
echo.
echo === hush-profanity sidecar cleanup ===
echo.
echo Walks every folder configured in settings.toml [library].roots and:
echo   - deletes every .srt file
echo   - deletes any .edl file that contains no manual skip work
echo   - MOVES .edl files with manual skip work into
echo     logs\preserved-edls\^<timestamp^>\^<root-name^>\^<rel-path^>\
echo     so they're easy to find and won't be loaded by Kodi or merged
echo     into a fresh scan
echo   - writes a log of preserved EDLs to logs\hush-clean-preserved-*.txt
echo     so you can re-integrate them later
echo.
echo Defaults to a DRY RUN ^(lists actions but touches nothing^).
echo Add --apply to actually delete and move.
echo.
echo Examples:
echo   windows\clean.bat                         ^(dry run, configured scope^)
echo   windows\clean.bat --apply                 ^(commit^)
echo   windows\clean.bat --scope "Y:\movies"     ^(dry run on a specific folder^)
echo   windows\clean.bat --scope "Y:\Series" --apply
echo.
".venv\Scripts\python.exe" -m hush_profanity clean %*
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
    echo Done.
) else (
    echo Cleanup exited with errors. See logs\ for details.
)
pause
exit /b %RC%
