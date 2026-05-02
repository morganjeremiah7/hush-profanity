#Requires -Version 5.0
<#
.SYNOPSIS
Install Windows context menu integration for hush-profanity.

Adds "Edit with hush-profanity" right-click option to all supported video files.
Prompts for admin elevation if not already running with admin privileges.

.DESCRIPTION
Reads the configured video extensions from config/settings.toml (or uses defaults: .mp4, .mkv)
and registers a context menu handler in HKEY_CLASSES_ROOT for each extension.

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

Write-Host "Installing context menu for extensions: $($extensions -join ', ')" -ForegroundColor Cyan

$helperScript = Join-Path $scriptDir "edit-with-hush.ps1"
if (-not (Test-Path $helperScript)) {
    Write-Error "Helper script not found: $helperScript"
    exit 1
}

$helperScript = (Resolve-Path $helperScript).Path
$menuName = "Edit with hush-profanity"

$regBase = "HKLM:\Software\Classes"

foreach ($ext in $extensions) {
    if (-not $ext.StartsWith(".")) {
        $ext = ".$ext"
    }

    try {
        $classPath = Join-Path $regBase $ext
        $shellPath = "$classPath\shell\$menuName\command"

        if (-not (Test-Path $shellPath)) {
            New-Item -Path $shellPath -Force -ErrorAction Stop | Out-Null
        }

        $psPath = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        $command = '"' + $psPath + '" -NoProfile -ExecutionPolicy Bypass -File "' + $helperScript + '" "%1"'
        Set-ItemProperty -Path $shellPath -Name "(Default)" -Value $command -ErrorAction Stop

        Write-Host "OK: Registered $ext" -ForegroundColor Green
    } catch {
        Write-Host "ERROR: Failed to register $ext : $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Installation complete!" -ForegroundColor Green
Write-Host "Right-click any video file to see 'Edit with hush-profanity'" -ForegroundColor Cyan
Write-Host ""
$msg = "To uninstall, run: powershell -ExecutionPolicy Bypass -File context-menu-uninstall.ps1"
Write-Host $msg -ForegroundColor Gray
