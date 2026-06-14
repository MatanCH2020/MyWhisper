# Adds MyWhisper to Windows startup so it launches (to the tray) on login.
# Run:  powershell -ExecutionPolicy Bypass -File install_autostart.ps1
# Remove: delete the shortcut from the Startup folder it prints below.

$root = $PSScriptRoot
$vbs = Join-Path $root "run_mywishper.vbs"
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "MyWhisper.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "wscript.exe"
$shortcut.Arguments = """$vbs"""
$shortcut.WorkingDirectory = $root
$shortcut.Description = "MyWhisper - Hebrew dictation"
$shortcut.Save()

Write-Host "Autostart shortcut created:" -ForegroundColor Green
Write-Host "  $shortcutPath" -ForegroundColor White
Write-Host "Delete that file to disable autostart." -ForegroundColor DarkGray
