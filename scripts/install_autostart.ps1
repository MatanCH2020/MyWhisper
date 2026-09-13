# Adds MyWhisper to Windows startup so it launches (to the tray) on login.
# Run:  powershell -ExecutionPolicy Bypass -File install_autostart.ps1
# Remove: delete the shortcut from the Startup folder it prints below.

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$vbs = Join-Path $root "scripts\run_mywishper.vbs"
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "MyWhisper.lnk"
$startupArguments = '"' + $vbs + '" --startup'
$wscript = Join-Path $env:WINDIR "System32\wscript.exe"
foreach ($required in @($vbs, $wscript, (Join-Path $root "app\main.py"),
                         (Join-Path $root ".venv\Scripts\pythonw.exe"))) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing startup target: $required" }
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $wscript
$shortcut.Arguments = $startupArguments
$shortcut.WorkingDirectory = $root
$shortcut.Description = "MyWhisper - Hebrew dictation"
$icon = Join-Path $root "app\assets\icon.ico"
if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
$shortcut.Save()

# An explicitly requested install also re-enables our entry if Task Manager
# previously disabled it. Do not touch any other startup entry.
$approvedKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
if (-not (Test-Path $approvedKey)) { New-Item -Path $approvedKey -Force | Out-Null }
$enabled = [byte[]](2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
New-ItemProperty -Path $approvedKey -Name "MyWhisper.lnk" -PropertyType Binary -Value $enabled -Force | Out-Null

$check = $shell.CreateShortcut($shortcutPath)
if ($check.TargetPath -ne $wscript -or $check.Arguments -ne $startupArguments -or
    $check.WorkingDirectory -ne $root) { throw "Startup shortcut verification failed" }
$status = (Get-ItemProperty -Path $approvedKey -Name "MyWhisper.lnk").'MyWhisper.lnk'
if ($status[0] -ne 2) { throw "Windows startup entry is not enabled" }

Write-Host "Autostart shortcut created:" -ForegroundColor Green
Write-Host "  $shortcutPath" -ForegroundColor White
Write-Host "Delete that file to disable autostart." -ForegroundColor DarkGray
