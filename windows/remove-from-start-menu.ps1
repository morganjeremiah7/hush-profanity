#Requires -Version 5.0
<#
.SYNOPSIS
Remove hush-profanity from the Windows Start Menu.

Deletes the shortcut created by add-to-start-menu.ps1.
No admin rights required.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File windows\remove-from-start-menu.ps1
#>

Write-Host "Removing hush-profanity from Start Menu..." -ForegroundColor Cyan

try {
    $shortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\hush-profanity.lnk"

    if (Test-Path $shortcutPath) {
        Remove-Item $shortcutPath -Force
        Write-Host "✓ Shortcut removed" -ForegroundColor Green
    } else {
        Write-Host "Shortcut not found (already removed)" -ForegroundColor Gray
    }

} catch {
    Write-Host "✗ Error removing shortcut: $_" -ForegroundColor Red
    exit 1
}
