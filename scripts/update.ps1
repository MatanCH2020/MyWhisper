# MyWhisper in-place updater - launched by the app's "Update now" button.
# Updates whatever folder it lives in (location-independent), then relaunches.
# Kept ASCII-only so Windows PowerShell 5.1 parses it regardless of code page.

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host ""
Write-Host "=== MyWhisper update ===" -ForegroundColor Cyan

# 1. Stop the running instance so files aren't locked and new code takes over.
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -eq "pythonw.exe" -and $_.CommandLine -and
    $_.CommandLine -match "main\.py" -and $_.CommandLine -like "*$root*"
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

# 2. Pull the latest code (force to origin/main if a fast-forward isn't possible).
git -C $root pull --ff-only
if ($LASTEXITCODE -ne 0) {
    Write-Host "Fast-forward failed - forcing to the latest published version..." -ForegroundColor Yellow
    git -C $root fetch origin
    git -C $root reset --hard origin/main
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Update failed. Check your internet connection and try again." -ForegroundColor Red
        Start-Sleep -Seconds 4
        exit 1
    }
}

# 3. Install any new/updated dependencies (fast when nothing changed).
& powershell -ExecutionPolicy Bypass -File (Join-Path $root "scripts\setup.ps1")

# 4. Relaunch (silent, to the tray).
$vbs = Join-Path $root "scripts\run_mywishper.vbs"
Start-Process wscript.exe -ArgumentList ('"' + $vbs + '"') -WorkingDirectory $root

Write-Host ""
Write-Host "=== Update complete - MyWhisper is restarting ===" -ForegroundColor Green
Start-Sleep -Seconds 2
