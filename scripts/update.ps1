# Run the transaction with the BASE interpreter: rollback can replace the venv.
param(
    [Parameter(Mandatory=$true)][string]$Tag,
    [Parameter(Mandatory=$true)][int]$ParentPid
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$cfg = Get-Content -LiteralPath (Join-Path $root ".venv\pyvenv.cfg")
$homeLine = $cfg | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
if (-not $homeLine) { throw "Cannot locate base Python interpreter" }
$baseDir = ($homeLine -split '=', 2)[1].Trim()
$basePy = Join-Path $baseDir "python.exe"
if (-not (Test-Path -LiteralPath $basePy)) { throw "Base Python not found" }
Remove-Item Env:__PYVENV_LAUNCHER__ -ErrorAction SilentlyContinue
$env:PYTHONIOENCODING = "utf-8"
& $basePy (Join-Path $root "app\updater.py") --tag $Tag --parent-pid $ParentPid
$updateExit = $LASTEXITCODE
if ($updateExit -ne 0) {
    Write-Host "Update did not complete. See the error and backup location above." -ForegroundColor Red
    Start-Sleep -Seconds 8
}
exit $updateExit
