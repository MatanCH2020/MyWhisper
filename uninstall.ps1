# MyWhisper one-line uninstaller.
# From any PowerShell window:
#   irm https://raw.githubusercontent.com/MatanCH2020/MyWhisper/main/uninstall.ps1 | iex
#
# What it does: stops any running instance, removes the Desktop and Startup
# shortcuts, and deletes the %USERPROFILE%\MyWhisper installation directory.

$ErrorActionPreference = "Continue"
$InstallDir = Join-Path $env:USERPROFILE "MyWhisper"

Write-Host ""
Write-Host "=== MyWhisper Uninstaller ===" -ForegroundColor Cyan
Write-Host "Preparing to remove MyWhisper from $InstallDir..." -ForegroundColor DarkGray

# 1. Stop any running instance
Write-Host "Stopping running instances..." -ForegroundColor White
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -eq "pythonw.exe" -and $_.CommandLine -and
    $_.CommandLine -match "main\.py" -and $_.CommandLine -like "*$InstallDir*"
} | ForEach-Object { 
    Write-Host "  Killing process ID $($_.ProcessId)" -ForegroundColor DarkGray
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue 
}
# Also kill the wscript launcher if it's lingering
Get-Process wscript -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*run_mywishper.vbs*" -and $_.CommandLine -like "*$InstallDir*"
} | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

# 2. Remove Shortcuts
Write-Host "Removing shortcuts..." -ForegroundColor White
$desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "MyWhisper.lnk"
if (Test-Path $desktopLnk) {
    Remove-Item $desktopLnk -Force -ErrorAction SilentlyContinue
    Write-Host "  Removed Desktop shortcut." -ForegroundColor DarkGray
}

$startupLnk = Join-Path ([Environment]::GetFolderPath("Startup")) "MyWhisper.lnk"
if (Test-Path $startupLnk) {
    Remove-Item $startupLnk -Force -ErrorAction SilentlyContinue
    Write-Host "  Removed Startup shortcut." -ForegroundColor DarkGray
}

# 3. Delete Installation Directory
if (Test-Path $InstallDir) {
    Write-Host "Deleting installation directory (this may take a moment)..." -ForegroundColor White
    # We use cmd /c rd /s /q because sometimes powershell's Remove-Item struggles with deep paths or long filenames
    cmd.exe /c "rd /s /q ""$InstallDir"""
    Start-Sleep -Seconds 1
    if (Test-Path $InstallDir) {
        Write-Host "  Warning: Some files could not be deleted. They might be locked by another process." -ForegroundColor Yellow
        Write-Host "  Please delete '$InstallDir' manually after restarting your computer." -ForegroundColor Yellow
    } else {
        Write-Host "  Directory deleted successfully." -ForegroundColor DarkGray
    }
} else {
    Write-Host "Directory $InstallDir not found. Already uninstalled?" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Uninstallation complete ===" -ForegroundColor Green
Write-Host "MyWhisper has been removed from your system." -ForegroundColor White
Write-Host ""
