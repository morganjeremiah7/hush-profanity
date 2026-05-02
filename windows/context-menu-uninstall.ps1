#Requires -Version 5.0
<#
.SYNOPSIS
Uninstall Windows context menu integration for hush-profanity.

Removes "Edit with hush-profanity" right-click option from all video files.
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

# Find the script directory and settings.toml
$scriptDir = Split-Path $MyInvocation.MyCommand.Path
$projectRoot = Split-Path $scriptDir
$settingsPath = Join-Path $projectRoot "config\settings.toml"
$settingsExamplePath = Join-Path $projectRoot "config\settings.example.toml"

# Parse settings.toml to find configured extensions
$extensions = @(".mp4", ".mkv")

if (Test-Path $settingsPath) {
    try {
        $content = Get-Content $settingsPath -Raw
        if ($content -match 'extensions\s*=\s*\[(.*?)\]') {
            $extStr = $matches[1]
            $extStr = $extStr -replace '"', '' -replace "'", ''
            $extensions = ($extStr -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        }
    } catch {
        Write-Host "Note: Could not parse settings.toml, using defaults (.mp4, .mkv)" -ForegroundColor Gray
    }
} elseif (Test-Path $settingsExamplePath) {
    Write-Host "Note: No settings.toml found, using defaults (.mp4, .mkv)" -ForegroundColor Gray
}

Write-Host "Removing context menu for extensions: $($extensions -join ', ')" -ForegroundColor Cyan

$regBase = "HKLM:\Software\Classes"
$menuName = "Edit with hush-profanity"
$removed = 0
$failed = 0

foreach ($ext in $extensions) {
    if (-not $ext.StartsWith(".")) {
        $ext = ".$ext"
    }

    try {
        $shellPath = "$regBase\$ext\shell\$menuName"

        if (Test-Path $shellPath) {
            Remove-Item -Path $shellPath -Recurse -Force -ErrorAction Stop
            Write-Host "OK: Removed $ext" -ForegroundColor Green
            $removed++
        } else {
            Write-Host "SKIP: $ext not registered" -ForegroundColor Gray
        }
    } catch {
        Write-Host "ERROR: Failed to remove $ext : $_" -ForegroundColor Red
        $failed++
    }
}

Write-Host ""
if ($removed -gt 0) {
    $msg = "Uninstallation complete! Removed context menu from $removed extension(s)."
    Write-Host $msg -ForegroundColor Green
} else {
    Write-Host "No context menu entries found to remove." -ForegroundColor Gray
}

if ($failed -gt 0) {
    $msg = "Warning: Failed to remove $failed extension(s). See above for details."
    Write-Host $msg -ForegroundColor Yellow
}
