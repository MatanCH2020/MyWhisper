# Stable-release installer. Existing installations use the transactional updater.
$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/MatanCH2020/MyWhisper.git"
$InstallDir = Join-Path $env:USERPROFILE "MyWhisper"
function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/MatanCH2020/MyWhisper/releases/latest" -Headers @{"User-Agent"="MyWhisper"}
$tag = $release.tag_name
if ($release.draft -or $release.prerelease -or $tag -notmatch '^v?\d+\.\d+\.\d+$') {
    throw "No valid stable release was found"
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
    Assert-NativeSuccess "Git installation"
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Reopen PowerShell so Git is available" }
}
if (Test-Path -LiteralPath (Join-Path $InstallDir ".git")) {
    # Exact-release bootstrap gives older installations the safe updater too.
    # No process is killed; the updater refuses to change a running installation.
    $cfg = Get-Content -LiteralPath (Join-Path $InstallDir ".venv\pyvenv.cfg")
    $homeLine = $cfg | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
    if (-not $homeLine) { throw "Cannot find the installed Python environment" }
    $basePy = Join-Path (($homeLine -split '=', 2)[1].Trim()) "python.exe"
    $bootstrap = Join-Path ([IO.Path]::GetTempPath()) ("mywhisper-updater-" + [guid]::NewGuid().ToString('N') + ".py")
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/MatanCH2020/MyWhisper/$tag/app/updater.py" -OutFile $bootstrap
        Remove-Item Env:__PYVENV_LAUNCHER__ -ErrorAction SilentlyContinue
        & $basePy $bootstrap --root $InstallDir --tag $tag
        Assert-NativeSuccess "Update"
    } finally {
        Remove-Item -LiteralPath $bootstrap -ErrorAction SilentlyContinue
    }
    return
}
if (Test-Path -LiteralPath $InstallDir) {
    if (Get-ChildItem -LiteralPath $InstallDir -Force | Select-Object -First 1) {
        throw "The installation folder already contains files and is not a Git checkout"
    }
}
git clone --branch $tag --single-branch $RepoUrl $InstallDir
Assert-NativeSuccess "Repository clone"
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $InstallDir "scripts\setup.ps1")
Assert-NativeSuccess "Setup"
$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = $ws.CreateShortcut((Join-Path $desktop "MyWhisper.lnk"))
$lnk.TargetPath = "wscript.exe"
$lnk.Arguments = '"' + (Join-Path $InstallDir "scripts\run_mywishper.vbs") + '"'
$lnk.WorkingDirectory = $InstallDir
$lnk.Description = "MyWhisper - Hebrew dictation"
$lnk.IconLocation = Join-Path $InstallDir "app\assets\icon.ico"
$lnk.Save()
Start-Process -WindowStyle Hidden -FilePath wscript.exe -ArgumentList $lnk.Arguments -WorkingDirectory $InstallDir
Write-Host "Installation verified. MyWhisper is starting." -ForegroundColor Green
