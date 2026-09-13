# MyWhisper setup - creates a Python 3.12 venv and installs dependencies.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File setup.ps1

param([switch]$SkipCuda)
$ErrorActionPreference = "Stop"
function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "=== MyWhisper setup ===" -ForegroundColor Cyan

# 1. Ensure Python 3.12 is available (the ML stack lacks 3.14 wheels).
$py312 = $null
try {
    $check = & py -3.12 --version 2>$null
    if ($LASTEXITCODE -eq 0) { $py312 = "py -3.12" }
} catch {}

if (-not $py312) {
    Write-Host "Python 3.12 not found. Installing via winget..." -ForegroundColor Yellow
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    Assert-NativeSuccess "Python installation"
    # Refresh PATH so the just-installed launcher is visible in this session.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    try {
        $check = & py -3.12 --version 2>$null
        if ($LASTEXITCODE -ne 0) { throw }
    } catch {
        Write-Host "Python 3.12 still not visible - close this terminal, open a new one and re-run setup.ps1." -ForegroundColor Red
        exit 1
    }
    $py312 = "py -3.12"
}

# Detect an NVIDIA GPU once — decides CUDA libs install and the config default.
$hasNvidia = $false
try {
    if ((Get-CimInstance Win32_VideoController -ErrorAction Stop).Name -match "NVIDIA") {
        $hasNvidia = $true
    }
} catch {}

# Default config: copy the example on first install (config.json is per-user).
$newConfig = -not (Test-Path "config.json")
if ((Test-Path "app\config.example.json") -and $newConfig) {
    Copy-Item "app\config.example.json" "config.json"
    Write-Host "Created config.json from app\config.example.json" -ForegroundColor Green
}

# No NVIDIA card -> point the config at the CPU so the app doesn't try CUDA.
if ($newConfig -and -not $hasNvidia -and (Test-Path "config.json")) {
    try {
        $cfg = Get-Content "config.json" -Raw | ConvertFrom-Json
        if ($cfg.device -eq "cuda") {
            $cfg.device = "cpu"
            $cfg.compute_type = "int8"
            $json = $cfg | ConvertTo-Json
            [IO.File]::WriteAllText((Join-Path $root "config.json"), $json,
                (New-Object System.Text.UTF8Encoding($false)))
            Write-Host "No NVIDIA GPU detected - config.json set to CPU mode." -ForegroundColor Yellow
        }
    } catch {}
}

# 2. Create the virtual environment.
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv) with Python 3.12..." -ForegroundColor Cyan
    & py -3.12 -m venv .venv
    Assert-NativeSuccess "Virtual environment creation"
} else {
    Write-Host ".venv already exists - reusing it." -ForegroundColor Green
}

$venvPy = Join-Path $root ".venv\Scripts\python.exe"

# 3. Verify the existing interpreter, then install the tested dependency set.
& $venvPy -c "import sys; assert sys.version_info[:2] == (3, 12), 'Python 3.12 is required'"
Assert-NativeSuccess "Python version check"

Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $venvPy -m pip install -r requirements.txt
Assert-NativeSuccess "Dependency installation"

if ($hasNvidia -and -not $SkipCuda) {
    Write-Host "NVIDIA GPU detected - installing CUDA libraries (large download, one time)..." -ForegroundColor Cyan
    & $venvPy -m pip install -r requirements-cuda.txt
    Assert-NativeSuccess "CUDA dependency installation"
} else {
    Write-Host "Skipping CUDA libraries (CPU install or explicit -SkipCuda)." -ForegroundColor Yellow
}

Write-Host ""
& $venvPy -m pip check
Assert-NativeSuccess "Dependency verification"
& $venvPy (Join-Path $root "scripts\verify_install.py")
Assert-NativeSuccess "Installation smoke check"
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host "Test the engine:   .\.venv\Scripts\python app\check_gpu.py" -ForegroundColor White
Write-Host "Run the app:       .\.venv\Scripts\python app\main.py" -ForegroundColor White
Write-Host "(First run downloads the Whisper model, ~1.5-3 GB.)" -ForegroundColor DarkGray
