#Requires -Version 5.0
<#
.SYNOPSIS
Uninstall Windows context menu integration for hush-profanity.

Removes "Edit with hush-profanity" right-click option from all files.
Prompts for admin elevation if not already running with admin privileges.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File windows\context-menu-uninstall.ps1
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

Write-Host "Removing hush-profanity context menu..." -ForegroundColor Cyan

$regPath = "HKEY_LOCAL_MACHINE\SOFTWARE\Classes\*\shell\Edit with hush-profanity"

try {
    & reg delete $regPath /f 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "OK: Removed context menu" -ForegroundColor Green
    } else {
        Write-Host "Context menu not registered (nothing to remove)" -ForegroundColor Gray
    }
} catch {
    Write-Host "ERROR: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "Uninstallation complete!" -ForegroundColor Green
