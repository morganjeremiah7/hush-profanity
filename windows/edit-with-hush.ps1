param([string]$FilePath)

<#
.SYNOPSIS
Helper script launched by Windows context menu to open a video file in hush-profanity's manual editor.

Called by: context-menu-install.ps1 registry entry
Checks if Flask is running; if not, starts the server quietly, then opens the browser.
#>

if (-not $FilePath -or -not (Test-Path $FilePath)) {
    Write-Error "File not found: $FilePath"
    exit 1
}

$file = Get-Item $FilePath
$port = 8765
$baseUrl = "http://127.0.0.1:$port"

# Check if Flask is already running
$flaskRunning = $false
try {
    $null = Invoke-WebRequest -Uri "$baseUrl/" -TimeoutSec 1 -ErrorAction Stop
    $flaskRunning = $true
} catch {
    $flaskRunning = $false
}

# If Flask is not running, start it quietly
if (-not $flaskRunning) {
    $batchPath = Join-Path (Split-Path $PSCommandPath) "manual-skip.bat"
    if (Test-Path $batchPath) {
        # Start batch file in background, detached from current window
        Start-Process -FilePath $batchPath -WindowStyle Hidden -NoNewWindow
        # Wait for Flask to start
        Start-Sleep -Seconds 3
    }
}

# Encode the file path for the URL
$encodedPath = [Uri]::EscapeDataString($file.FullName)
$watchUrl = "$baseUrl/watch?path=$encodedPath"

# Open browser to the watch page
try {
    Start-Process $watchUrl
} catch {
    Write-Error "Failed to open browser: $_"
    Write-Host "You can manually open: $watchUrl" -ForegroundColor Yellow
    exit 1
}
