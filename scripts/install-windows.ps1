#requires -Version 5.1
<#
.SYNOPSIS
    One-time setup for hush-profanity on Windows.

.DESCRIPTION
    Creates a Python venv at .venv\, installs PyTorch with CUDA 12.1,
    installs the rest of the dependencies, verifies ffmpeg is on PATH,
    and writes config\settings.toml from the example if it does not exist.

    Requires:
      - Python 3.10, 3.11, or 3.12 on PATH
      - NVIDIA driver supporting CUDA 12.x (any RTX 20-series or newer)
      - ffmpeg.exe on PATH (winget install Gyan.FFmpeg)

.PARAMETER Cuda
    Torch CUDA build to install: cu126 (default), cu124, or cu121.
    cu126 is required for ctranslate2 4.6+ which uses cuDNN 9 natively.

.PARAMETER Recreate
    Delete and recreate the venv from scratch.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
#>

param(
    [string]$Cuda = "cu126",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Write-Step { param($m) Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Warn { param($m) Write-Host "warn: $m" -ForegroundColor Yellow }

# ---- Python -----------------------------------------------------------------
Write-Step "Locating Python (3.10-3.12)"
$pythonExe = $null
foreach ($candidate in @("py -3.12", "py -3.11", "py -3.10", "python", "python3")) {
    try {
        $parts = $candidate -split " ", 2
        $exe = $parts[0]
        $args = if ($parts.Count -gt 1) { @($parts[1], "-c", "import sys; print(sys.executable)") } else { @("-c", "import sys; print(sys.executable)") }
        $out = & $exe @args 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            $pythonExe = $out.Trim()
            Write-Host "    using $pythonExe"
            break
        }
    } catch { }
}
if (-not $pythonExe) {
    throw "No Python 3.10-3.12 found. Install from https://www.python.org/ and retry."
}

# ---- ffmpeg -----------------------------------------------------------------
Write-Step "Checking ffmpeg.exe"
$ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    Write-Warn "ffmpeg.exe is not on PATH."
    Write-Warn "Install with:  winget install Gyan.FFmpeg"
    Write-Warn "Then open a fresh terminal and re-run this script."
    throw "ffmpeg required."
} else {
    Write-Host "    $($ffmpeg.Source)"
}

# ---- venv -------------------------------------------------------------------
$VenvDir = Join-Path $ProjectRoot ".venv"
if ($Recreate -and (Test-Path $VenvDir)) {
    Write-Step "Removing existing .venv (--Recreate)"
    Remove-Item $VenvDir -Recurse -Force
}
if (-not (Test-Path $VenvDir)) {
    Write-Step "Creating venv at .venv"
    & $pythonExe -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"

Write-Step "Upgrading pip + wheel + setuptools"
# setuptools 81+ removed pkg_resources, which faster-whisper's ctranslate2 dep
# still imports at runtime. Pin to <81 until the upstream fix lands.
& $VenvPython -m pip install --upgrade pip wheel "setuptools<81"

# ---- Torch (must come BEFORE pip install of the package) --------------------
# torch 2.8.x+cu126 is what whisperx>=3.8 expects. cu126 also matches the cuDNN 9
# that ctranslate2 4.6+ uses natively, so we don't need to dual-load cuDNN.
Write-Step "Installing PyTorch ($Cuda)"
& $VenvPip install --index-url "https://download.pytorch.org/whl/$Cuda" "torch==2.8.0" "torchaudio==2.8.0"
if ($LASTEXITCODE -ne 0) { throw "torch install failed" }

# ---- cuBLAS for ctranslate2 -------------------------------------------------
# PyTorch bundles its own cuDNN 9 inside torch\lib; with ctranslate2 4.6+ that's
# now the only cuDNN needed. cuBLAS still has to come from the nvidia-* pip pkg
# so ctranslate2 can find it on the DLL search path. ~550 MB.
Write-Step "Installing cuBLAS for ctranslate2"
& $VenvPip install "nvidia-cublas-cu12"
if ($LASTEXITCODE -ne 0) { throw "cuBLAS install failed" }

# ---- Project deps ------------------------------------------------------------
# whisperx>=3.8 pulls ctranslate2 4.7+ (uses cuDNN 9, no longer needs the
# old cuDNN 8 dance). torch is already installed, but we make the constraints
# explicit to avoid pip resolving down to the CPU build.
Write-Step "Installing project dependencies"
& $VenvPip install -e .
if ($LASTEXITCODE -ne 0) { throw "project install failed" }
& $VenvPip install --upgrade "whisperx>=3.8.5"
if ($LASTEXITCODE -ne 0) { throw "whisperx install failed" }

# ---- Sanity check ------------------------------------------------------------
Write-Step "Verifying CUDA"
$cudaCheck = @"
import torch
print('torch:', torch.__version__)
print('cuda available:', torch.cuda.is_available())
if torch.cuda.is_available():
    print('device:', torch.cuda.get_device_name(0))
"@
& $VenvPython -c $cudaCheck

# ---- Settings ---------------------------------------------------------------
$Example = Join-Path $ProjectRoot "config\settings.example.toml"
$Settings = Join-Path $ProjectRoot "config\settings.toml"
if (-not (Test-Path $Settings)) {
    Write-Step "Creating config\settings.toml from example (edit it before running)"
    Copy-Item $Example $Settings
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  Edit:  config\settings.toml" -ForegroundColor Green
Write-Host "  Run:   windows\scan.bat        (transcribe + write EDL/SRT)" -ForegroundColor Green
Write-Host "  Run:   windows\manual-skip.bat (web UI for marking skip scenes)" -ForegroundColor Green