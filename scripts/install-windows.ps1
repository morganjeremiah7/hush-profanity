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
    Torch CUDA build to install: cu121 (default — most compatible with
    whisperx 3.4.x's bundled deps), cu124, or cu126.

.PARAMETER Recreate
    Delete and recreate the venv from scratch.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
#>

param(
    [string]$Cuda = "cu121",
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
& $VenvPython -m pip install --upgrade pip wheel "setuptools<81"

# ---- Torch (must come BEFORE pip install of the package) --------------------
# torch 2.5.1+cu121 is the proven combo for whisperx 3.4.x's wav2vec2 alignment.
# We use openai-whisper for transcription (PyTorch-only — no ctranslate2), so we
# don't need ctranslate2's specific cuDNN/cuBLAS dance. Torch's bundled cuDNN
# is sufficient.
Write-Step "Installing PyTorch ($Cuda)"
& $VenvPip install --index-url "https://download.pytorch.org/whl/$Cuda" "torch==2.5.1" "torchaudio==2.5.1"
if ($LASTEXITCODE -ne 0) { throw "torch install failed" }

# ---- Project deps ------------------------------------------------------------
# openai-whisper (the reference PyTorch impl). We dropped faster-whisper /
# ctranslate2 because the latter has a long-standing CUDA cleanup crash on
# Windows that fired across every version we tried. openai-whisper is ~3-4x
# slower but rock-solid — single CUDA context shared with whisperx alignment.
# whisperx 3.4.x is the last version that doesn't pull a newer ctranslate2
# (which we don't want at all). Pin it explicitly.
Write-Step "Installing project dependencies"
& $VenvPip install -e .
if ($LASTEXITCODE -ne 0) { throw "project install failed" }
& $VenvPip install "openai-whisper>=20250625" "whisperx>=3.4.5,<3.5"
if ($LASTEXITCODE -ne 0) { throw "openai-whisper / whisperx install failed" }

# ---- triton-windows for fast DTW kernels ------------------------------------
# openai-whisper uses Triton to JIT CUDA kernels for word-timestamp DTW. The
# upstream `triton` package is Linux-only; without `triton-windows` Whisper
# falls back to a much slower pure-PyTorch DTW. The version must match torch:
# torch 2.5 -> triton 3.1, torch 2.6 -> triton 3.2, etc.
Write-Step "Installing triton-windows for word-timestamp speedup"
& $VenvPip install "triton-windows==3.1.0.post17"
if ($LASTEXITCODE -ne 0) { Write-Warn "triton-windows install failed; whisper will fall back to slower DTW. Not fatal." }

# ---- Sanity check + VRAM probe ---------------------------------------------
Write-Step "Verifying CUDA + probing GPU VRAM"
# Single-quoted here-string so PowerShell does not interpolate `$` or treat `"` specially.
$probe = @'
import json, torch
out = {'torch': torch.__version__, 'cuda_available': torch.cuda.is_available()}
if torch.cuda.is_available():
    out['device_name'] = torch.cuda.get_device_name(0)
    out['vram_mb'] = int(torch.cuda.get_device_properties(0).total_memory // (1024 * 1024))
else:
    out['device_name'] = None
    out['vram_mb'] = 0
print(json.dumps(out))
'@
$probeJson = & $VenvPython -c $probe
$gpu = $probeJson | ConvertFrom-Json
Write-Host "    torch: $($gpu.torch)"
Write-Host "    cuda available: $($gpu.cuda_available)"
if ($gpu.cuda_available) {
    Write-Host "    device: $($gpu.device_name)"
    Write-Host "    VRAM: $($gpu.vram_mb) MiB"
}

# ---- Choose tier based on VRAM ---------------------------------------------
# Tiers (after openai-whisper engine swap):
#   24+ GB → large-v3 + gpu_workers=2  (3090, 4090, 5090, A6000, etc.)
#   12-23 GB → large-v3 + gpu_workers=1 (3060 12GB, 4070, 4070 Ti Super 16GB, etc.)
#    8-11 GB → medium  + gpu_workers=1 (3060 8GB, 4060, 4060 Ti 8GB)
#       <8 GB → warn, but proceed with medium + 1 worker
$tierModel = "large-v3"
$tierGpuWorkers = 1
$tierLabel = "default"
if ($gpu.cuda_available) {
    if ($gpu.vram_mb -ge 24000) {
        $tierModel = "large-v3"; $tierGpuWorkers = 2; $tierLabel = "high (24GB+: large-v3 + 2 workers)"
    } elseif ($gpu.vram_mb -ge 12000) {
        $tierModel = "large-v3"; $tierGpuWorkers = 1; $tierLabel = "mid (12-23GB: large-v3 + 1 worker)"
    } elseif ($gpu.vram_mb -ge 8000) {
        $tierModel = "medium"; $tierGpuWorkers = 1; $tierLabel = "low (8-11GB: medium model + 1 worker)"
    } else {
        Write-Warn "GPU has only $($gpu.vram_mb) MiB VRAM. Minimum recommended is 8GB."
        Write-Warn "Continuing with conservative defaults; expect slow performance and potential OOMs."
        $tierModel = "medium"; $tierGpuWorkers = 1; $tierLabel = "below-minimum"
    }
}
Write-Host "    -> tier: $tierLabel"

# ---- Settings ---------------------------------------------------------------
$Example = Join-Path $ProjectRoot "config\settings.example.toml"
$Settings = Join-Path $ProjectRoot "config\settings.toml"
if (-not (Test-Path $Settings)) {
    Write-Step "Creating config\settings.toml (auto-tuned for your GPU)"
    $content = Get-Content $Example -Raw
    # Patch the model line. The example currently has model = "large-v3"; replace
    # the first occurrence so we don't touch the comment that lists model options.
    $content = [regex]::Replace($content, '(?m)^model = "large-v3"\s*$', "model = `"$tierModel`"")
    $content = [regex]::Replace($content, '(?m)^gpu_workers = 1\s*$', "gpu_workers = $tierGpuWorkers")
    Set-Content -Path $Settings -Value $content -NoNewline
    Write-Host "    model = $tierModel"
    Write-Host "    gpu_workers = $tierGpuWorkers"
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  Edit:  config\settings.toml" -ForegroundColor Green
Write-Host "  Run:   windows\scan.bat        (transcribe + write EDL/SRT)" -ForegroundColor Green
Write-Host "  Run:   windows\manual-skip.bat (web UI for marking skip scenes)" -ForegroundColor Green