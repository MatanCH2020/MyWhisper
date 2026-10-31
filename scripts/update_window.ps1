# Graphical handoff for Windows PowerShell 5.1. Keep UTF-8 BOM for Hebrew.
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^v?\d+\.\d+\.\d+$')][string]$Tag,
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$BackupId
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$root = Split-Path $PSScriptRoot -Parent
$baseLine = Get-Content -LiteralPath (Join-Path $root '.venv\pyvenv.cfg') |
    Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
if (-not $baseLine) { throw 'Cannot locate base Python' }
$basePython = Join-Path (($baseLine -split '=', 2)[1].Trim()) 'python.exe'
$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = $basePython
$info.WorkingDirectory = $root
# Relative paths avoid embedding a user-supplied installation path in the arguments.
$info.Arguments = "-I -u app/updater.py --root . --tag $Tag --parent-pid $ParentPid --prepared-backup $BackupId --progress"
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$info.EnvironmentVariables.Remove('__PYVENV_LAUNCHER__')
$info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'

$form = New-Object Windows.Forms.Form
$form.Text = "MyWhisper · עדכון $Tag"
$form.Font = New-Object Drawing.Font('Segoe UI', 11)
$form.AutoScaleMode = 'Dpi'
$form.FormBorderStyle = 'FixedDialog'
$form.StartPosition = 'CenterScreen'
$form.RightToLeft = 'Yes'
$form.RightToLeftLayout = $true
$form.ControlBox = $false
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.ClientSize = New-Object Drawing.Size(460, 140)
$icon = Join-Path $root 'app\assets\icon.ico'
if (Test-Path -LiteralPath $icon) { $form.Icon = New-Object Drawing.Icon($icon) }

$layout = New-Object Windows.Forms.TableLayoutPanel
$layout.Dock = 'Fill'
$layout.Padding = New-Object Windows.Forms.Padding(18)
$layout.ColumnCount = 1
$layout.RowCount = 3
$form.Controls.Add($layout)
$status = New-Object Windows.Forms.Label
$status.Dock = 'Fill'
$status.AutoSize = $true
$status.Text = 'מסיים את ההכנה להתקנה…'
$layout.Controls.Add($status, 0, 0)
$bar = New-Object Windows.Forms.ProgressBar
$bar.AccessibleName = 'התקדמות העדכון'
$bar.Dock = 'Top'
$bar.Style = 'Marquee'
$layout.Controls.Add($bar, 0, 1)
$close = New-Object Windows.Forms.Button
$close.Text = 'סגור'
$close.AutoSize = $true
$close.Visible = $false
$close.Add_Click({ $form.Close() })
$layout.Controls.Add($close, 0, 2)

$stages = @{
    'stage:install' = 'מתקין את קובצי הגרסה ואת רכיבי התוכנה…'
    'stage:verify' = 'בודק שהגרסה החדשה מוכנה להפעלה…'
    'stage:restore' = 'ההתקנה נכשלה. משחזר את הגרסה הקודמת…'
    'stage:recovered' = 'ההתקנה הזמינה נבדקה וניתנת להפעלה.'
    'stage:restart' = 'פותח את MyWhisper…'
}
$worker = New-Object System.Diagnostics.Process
$worker.StartInfo = $info
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 120
$script:finished = $false
$script:usable = $false
$form.Add_FormClosing({ param($sender, $eventArgs)
    if (-not $script:finished) { $eventArgs.Cancel = $true }
})
$form.Add_Shown({
    try {
        if (-not $worker.Start()) { throw 'Could not start update worker' }
        $script:errors = $worker.StandardError.ReadToEndAsync()
        $script:line = $worker.StandardOutput.ReadLineAsync()
        # Until this acknowledgement the app remains open and owns its mutex.
        [Console]::Out.WriteLine('ready')
        [Console]::Out.Flush()
        $timer.Start()
    } catch {
        $script:finished = $true
        $form.Close()
    }
})
$timer.Add_Tick({
    while ($script:line.IsCompleted) {
        $value = $script:line.Result
        if ($null -eq $value) {
            if (-not $worker.HasExited) { return }
            $timer.Stop()
            $script:finished = $true
            if ($worker.ExitCode -eq 0) {
                $form.Close()
            } else {
                $status.Text = if ($script:usable) { 'העדכון לא הושלם. אפשר להמשיך להשתמש בגרסה הקיימת.' }
                               else { 'העדכון לא הושלם. ניתן להפעיל שוב את המתקין מאתר MyWhisper.' }
                $bar.Visible = $false
                $close.Visible = $true
                $form.AcceptButton = $close
                $form.Activate()
            }
            return
        }
        if ($stages.ContainsKey($value)) { $status.Text = $stages[$value] }
        if ($value -eq 'stage:recovered') { $script:usable = $true }
        $script:line = $worker.StandardOutput.ReadLineAsync()
    }
})
try { [Windows.Forms.Application]::Run($form) }
finally { $timer.Dispose(); $worker.Dispose(); $form.Dispose() }
