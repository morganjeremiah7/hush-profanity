#Requires -Version 5.0
<#
.SYNOPSIS
Add hush-profanity to the Windows Start Menu.

Creates a shortcut in the current user's Start Menu that launches the editor.
No admin rights required.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File windows\add-to-start-menu.ps1
#>

$scriptDir = Split-Path $MyInvocation.MyCommand.Path
$projectRoot = Split-Path $scriptDir

Write-Host "Adding hush-profanity to Start Menu..." -ForegroundColor Cyan

try {
    # Create WScript.Shell COM object for shortcut creation
    $shell = New-Object -ComObject WScript.Shell
    $startMenuPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
    $shortcutPath = "$startMenuPath\hush-profanity.lnk"

    # Create the shortcut object
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "$scriptDir\manual-skip.bat"
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = "hush-profanity manual scene editor"
    $shortcut.IconLocation = "C:\Windows\System32\cmd.exe,0"

    # Save the shortcut
    $shortcut.Save()

    Write-Host "✓ Shortcut created: $shortcutPath" -ForegroundColor Green
    Write-Host ""
    Write-Host "You can now find 'hush-profanity' in your Start Menu!" -ForegroundColor Green
    Write-Host "Or press the Windows key and type: hush-profanity" -ForegroundColor Cyan

} catch {
    Write-Host "✗ Error creating shortcut: $_" -ForegroundColor Red
    exit 1
}
