#Requires -Version 5.0
<#
.SYNOPSIS
Install Windows context menu integration for hush-profanity.

Adds "Edit with hush-profanity" right-click option to all files.
Prompts for admin elevation if not already running with admin privileges.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File windows\context-menu-install.ps1
#>

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Write-Host "This script requires administrator privileges." -ForegroundColor Yellow
    Write-Host "Attempting to elevate..." -ForegroundColor Yellow
    $scriptPath = $MyInvocation.MyCommand.Path
    Start-Process powershell.exe -ArgumentList "-ExecutionPolicy Bypass -File `"$scriptPath`"" -Verb RunAs
    exit
}

# Find the script directory (where this script is located)
$scriptDir = Split-Path $MyInvocation.MyCommand.Path
$helperScript = Join-Path $scriptDir "edit-with-hush.ps1"

if (-not (Test-Path $helperScript)) {
    Write-Error "Helper script not found: $helperScript"
    exit 1
}

$helperScript = (Resolve-Path $helperScript).Path
$menuName = "Edit with hush-profanity"

Write-Host "Installing hush-profanity context menu..." -ForegroundColor Cyan
Write-Host "Helper script: $helperScript" -ForegroundColor Gray

$regBase = "HKLM:\Software\Classes"
$classPath = "$regBase\*"
$shellPath = "$classPath\shell\$menuName\command"

try {
    if (-not (Test-Path $shellPath)) {
        New-Item -Path $shellPath -Force -ErrorAction Stop | Out-Null
    }

    $psPath = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    $command = '"' + $psPath + '" -NoProfile -ExecutionPolicy Bypass -File "' + $helperScript + '" "%1"'
    Set-ItemProperty -Path $shellPath -Name "(Default)" -Value $command -ErrorAction Stop

    # Set display name
    Set-ItemProperty -Path "$classPath\shell\$menuName" -Name "(Default)" -Value "Edit with &hush-profanity" -ErrorAction Stop

    Write-Host "OK: Registered context menu for all file types" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Failed to register context menu : $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Installation complete!" -ForegroundColor Green
Write-Host "Right-click any file to see 'Edit with hush-profanity'" -ForegroundColor Cyan
Write-Host ""
Write-Host "To uninstall, run: powershell -ExecutionPolicy Bypass -File context-menu-uninstall.ps1" -ForegroundColor Gray
