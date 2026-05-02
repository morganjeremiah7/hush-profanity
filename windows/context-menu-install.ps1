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
$extensions = @(".mp4", ".mkv")  # defaults

if (Test-Path $settingsPath) {
    try {
        $content = Get-Content $settingsPath -Raw
        # Simple regex to extract extensions array: extensions = [".mp4", ".mkv"]
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

# Registry base path for file associations
$regBase = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"

# Create context menu entry for each extension
$menuName = "Edit with hush-profanity"
$progId = "hush-profanity.Edit"

foreach ($ext in $extensions) {
    # Ensure extension starts with a dot
    if (-not $ext.StartsWith(".")) {
        $ext = ".$ext"
    }

    try {
        # Create UserChoice key for this extension (stores recent choice)
        $extPath = Join-Path $regBase $ext
        $shellPath = Join-Path $extPath "UserChoice\shell"

        if (-not (Test-Path $extPath)) {
            New-Item -Path $extPath -Force -ErrorAction SilentlyContinue | Out-Null
        }

        Write-Host "Configuring $ext..." -ForegroundColor White
    } catch {
        # Silently skip; the HKCU way might not work on all systems
    }
}

# Also register globally via HKEY_CLASSES_ROOT (system-wide)
# This is the main method that works reliably across most setups
$regClassesBase = "HKLM:\Software\Classes"

foreach ($ext in $extensions) {
    if (-not $ext.StartsWith(".")) {
        $ext = ".$ext"
    }

    try {
        $classPath = Join-Path $regClassesBase $ext
        $shellPath = Join-Path $classPath "shell\$menuName\command"

        # Ensure the path exists
        if (-not (Test-Path $shellPath)) {
            New-Item -Path $shellPath -Force -ErrorAction Stop | Out-Null
        }

        # Set the command that will be executed
        # PowerShell command: run our helper script with the file path
        $command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$helperScript`" `"%1`""
        Set-ItemProperty -Path $shellPath -Name "(Default)" -Value $command -ErrorAction Stop

        Write-Host "✓ Registered $ext" -ForegroundColor Green
    } catch {
        Write-Host "✗ Failed to register $ext : $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Installation complete!" -ForegroundColor Green
Write-Host "Right-click any video file ($($extensions -join ', ')) to see 'Edit with hush-profanity'" -ForegroundColor Cyan
Write-Host ""
Write-Host "To uninstall, run: powershell -ExecutionPolicy Bypass -File windows\context-menu-uninstall.ps1" -ForegroundColor Gray
