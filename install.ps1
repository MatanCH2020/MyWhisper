# MyWhisper one-line installer.
# From any PowerShell window:
#   irm https://raw.githubusercontent.com/MatanCH2020/MyWhisper/main/install.ps1 | iex
#
# What it does: installs Git if missing, clones (or updates) the repo into
# %USERPROFILE%\MyWhisper, runs setup.ps1 (Python 3.12 venv + all deps incl.
# CUDA), and puts a MyWhisper shortcut on the Desktop.

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/MatanCH2020/MyWhisper.git"
$InstallDir = Join-Path $env:USERPROFILE "MyWhisper"

Write-Host ""
Write-Host "=== MyWhisper installer ===" -ForegroundColor Cyan
Write-Host "Install dir: $InstallDir" -ForegroundColor DarkGray

# 1. Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Git not found - installing via winget..." -ForegroundColor Yellow
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "Git installed but not visible yet - open a new terminal and re-run the install command." -ForegroundColor Red
        exit 1
    }
}

# 2. Clone or update
if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Host "Existing install found - updating..." -ForegroundColor Cyan
    git -C $InstallDir pull --ff-only
} else {
    Write-Host "Cloning repository..." -ForegroundColor Cyan
    git clone $RepoUrl $InstallDir
}

# 3. Python 3.12 venv + dependencies (setup.ps1 also creates config.json)
& powershell -ExecutionPolicy Bypass -File (Join-Path $InstallDir "setup.ps1")
if ($LASTEXITCODE -ne 0) {
    Write-Host "Setup failed - see messages above." -ForegroundColor Red
    exit 1
}

# 4. Desktop shortcut -> silent launcher (no console window)
$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = $ws.CreateShortcut((Join-Path $desktop "MyWhisper.lnk"))
$lnk.TargetPath = "wscript.exe"
$lnk.Arguments = '"' + (Join-Path $InstallDir "run_mywishper.vbs") + '"'
$lnk.WorkingDirectory = $InstallDir
$lnk.Description = "MyWhisper - Hebrew dictation"
$icon = Join-Path $InstallDir "app\assets\icon.ico"
if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
$lnk.Save()

Write-Host ""
Write-Host "=== Installation complete ===" -ForegroundColor Green
Write-Host "Run:  double-click 'MyWhisper' on the Desktop." -ForegroundColor White
Write-Host "First run downloads the Whisper model (~1.5-3 GB, one time)." -ForegroundColor DarkGray
Write-Host "Start with Windows (optional):" -ForegroundColor White
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$InstallDir\install_autostart.ps1`"" -ForegroundColor DarkGray
