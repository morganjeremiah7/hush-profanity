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
echo By default, runs as a DRY RUN — lists what would be deleted but does not
echo touch any files. Add --apply to actually delete.
echo.
echo Defaults: deletes only ".edl" and "-words.srt" files. Pass
echo   --include-cleaned-srt   to also delete cleaned <base>.srt when an official
echo                           subtitle (.en.srt / .eng.srt) sibling exists
echo   --include-all-srt       to delete every <base>.srt (DANGEROUS — could
echo                           remove official subs without language tags)
echo.
echo Examples:
echo   windows\clean.bat                              ^(dry run, .edl + -words.srt^)
echo   windows\clean.bat --apply                      ^(commit the above^)
echo   windows\clean.bat --include-cleaned-srt        ^(dry run, broader^)
echo   windows\clean.bat --include-cleaned-srt --apply
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
