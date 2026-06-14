# MyWhisper setup - creates a Python 3.12 venv and installs dependencies.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
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
    Write-Host "Installed. You may need to re-open the terminal if 'py -3.12' is not found next." -ForegroundColor Yellow
    $py312 = "py -3.12"
}

# 2. Create the virtual environment.
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv) with Python 3.12..." -ForegroundColor Cyan
    Invoke-Expression "$py312 -m venv .venv"
} else {
    Write-Host ".venv already exists - reusing it." -ForegroundColor Green
}

$venvPy = Join-Path $root ".venv\Scripts\python.exe"

# 3. Upgrade pip and install dependencies.
Write-Host "Upgrading pip..." -ForegroundColor Cyan
& $venvPy -m pip install --upgrade pip

Write-Host "Installing dependencies (this includes CUDA libs - large download)..." -ForegroundColor Cyan
& $venvPy -m pip install -r requirements.txt

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host "Test the engine:   .\.venv\Scripts\python app\check_gpu.py" -ForegroundColor White
Write-Host "Run the app:       .\.venv\Scripts\python app\main.py" -ForegroundColor White
Write-Host "(First run downloads the Whisper model, ~1.5-3 GB.)" -ForegroundColor DarkGray
